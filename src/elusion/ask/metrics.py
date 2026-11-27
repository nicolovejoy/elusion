"""The per-arm figures of `ask-eval`, computed from plain rows.

Nothing here touches the database or a model: the flow turns what it stored into `EvalRow`s
and this module counts. Every proportion carries a Clopper-Pearson 95% interval and the
sample size behind it (`evaluate.metrics.proportion`).

How a claim is counted. Each claim of an answer has a *check* (Python found its quote
verbatim in an abstract the model was shown) and, when the check passed, a *judgement* (a
second model said whether the quote supports the claim). So a claim is one of:

- supported: checked, and the judge said yes.
- unsupported: the check failed (the judge is never asked), or the judge said no.
- not judged: checked, but the judge call ended in a refusal or a cut-off, or has not been
  made. It is neither supported nor unsupported, so it stays out of the denominators and is
  reported on its own.

*Groundedness by claim* is supported over (supported plus unsupported). It treats the claims
of one answer as independent, which they are not. *Groundedness by question* asks that every
claim of an answer be supported, and is the figure to trust. A question with an unsupported
claim is not grounded whatever the rest were; a question whose other claims are all supported
but one is not judged cannot be decided, and is left out of that denominator too.

*Refusals.* An ask that retrieved no passage is stored as refused without any model call
(only the lexical arm can do this, when no word of the question is in the index). Nobody
decided that refusal, so it is not a correct (or wrong) refusal: the model's refusals are
counted on their own, and the no-passage ones are counted beside them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from elusion.evaluate.metrics import Estimate, proportion


@dataclass(frozen=True)
class ClaimResult:
    checked: bool  # retrieved and verbatim
    supported: bool | None  # the judge's answer; None when not judged or unusable


@dataclass(frozen=True)
class EvalRow:
    question_id: str
    kind: str  # "answerable" or "unanswerable"
    source_hit: bool | None  # None for an unanswerable question
    outcome: str  # answered, refused, failed, error
    claims: tuple[ClaimResult, ...]  # empty when there are no claims
    # True for a `refused` row where nothing was retrieved, so no model was asked: nobody
    # decided that refusal.
    no_passages: bool = False


@dataclass(frozen=True)
class ArmReport:
    arm: str
    k: int
    n_answerable: int
    n_unanswerable: int
    recall_at_k: Estimate
    claims_total: int  # every claim of every answer, judged or not
    claims_supported: int
    groundedness_claims: Estimate | None  # over the claims that are supported or unsupported
    answered_questions: int  # rows with at least one claim
    fully_grounded: int
    groundedness_questions: Estimate | None  # over the answered rows that can be decided
    refusals_correct: int  # unanswerable rows the model refused, of n_unanswerable
    refusals_wrong: int  # answerable rows the model refused
    # Refused with no passage retrieved, so no model was asked; in neither count above.
    no_passage_unanswerable: int
    no_passage_answerable: int
    errors: int
    judge_unusable: int  # checked claims with no usable judgement
    questions_not_judged: int  # answered rows left out of the by-question denominator
    cost_usd: float


def _supported(claim: ClaimResult) -> bool:
    return claim.checked and claim.supported is True


def _not_judged(claim: ClaimResult) -> bool:
    return claim.checked and claim.supported is None


def _no_passages(row: EvalRow) -> bool:
    return row.outcome == "refused" and row.no_passages


def _model_refused(row: EvalRow) -> bool:
    return row.outcome == "refused" and not row.no_passages


def arm_report(arm: str, k: int, rows: Sequence[EvalRow], cost_usd: float) -> ArmReport:
    """The figures of one arm. Raises `ValueError` when no row is answerable: recall has no
    denominator then."""
    answerable = [r for r in rows if r.kind == "answerable"]
    unanswerable = [r for r in rows if r.kind == "unanswerable"]
    if not answerable:
        raise ValueError("no answerable question: recall@k has nothing to divide by")
    recall = proportion(sum(1 for r in answerable if r.source_hit), len(answerable))

    claims = [c for r in rows for c in r.claims]
    supported = sum(1 for c in claims if _supported(c))
    unusable = sum(1 for c in claims if _not_judged(c))
    decided = len(claims) - unusable

    answered = [r for r in rows if r.claims]
    fully = sum(1 for r in answered if all(_supported(c) for c in r.claims))
    # Undecided: nothing unsupported in it, and at least one claim not judged.
    undecided = sum(
        1
        for r in answered
        if any(_not_judged(c) for c in r.claims)
        and not any(c.checked is False or c.supported is False for c in r.claims)
    )
    return ArmReport(
        arm=arm,
        k=k,
        n_answerable=len(answerable),
        n_unanswerable=len(unanswerable),
        recall_at_k=recall,
        claims_total=len(claims),
        claims_supported=supported,
        groundedness_claims=proportion(supported, decided) if decided else None,
        answered_questions=len(answered),
        fully_grounded=fully,
        groundedness_questions=(
            proportion(fully, len(answered) - undecided) if len(answered) > undecided else None
        ),
        refusals_correct=sum(1 for r in unanswerable if _model_refused(r)),
        refusals_wrong=sum(1 for r in answerable if _model_refused(r)),
        no_passage_unanswerable=sum(1 for r in unanswerable if _no_passages(r)),
        no_passage_answerable=sum(1 for r in answerable if _no_passages(r)),
        errors=sum(1 for r in rows if r.outcome == "error"),
        judge_unusable=unusable,
        questions_not_judged=undecided,
        cost_usd=cost_usd,
    )
