"""The `ask-eval` report: one plain-text page that compares the three arms.

Plain lists, no tables. It states what was run (the index, the models, the prompt versions,
the fusion constants), then for each arm its figures with Clopper-Pearson 95% intervals and
the counts behind them, then one line per question by `question_id` and nothing else: the
question texts, the source ids and the abstracts stay in the owner's files and the database,
never in the report, which is committed. It ends with the five caveats the figures need.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from elusion.ask.metrics import ArmReport, EvalRow
from elusion.evaluate.metrics import Estimate

CAVEATS = (
    "recall@5 is a lower bound: another abstract may also answer the question.",
    "The questions were drafted from abstracts, so they share vocabulary with their source "
    "even after rewording; this flatters lexical search.",
    "Groundedness by claim treats the claims of one answer as independent, which they are "
    "not; the by-question figure is the one to trust.",
    "The answer model and the judge are different models, so no model grades itself.",
    "Only abstracts the screen flagged are searched; one the screen discarded cannot be found.",
)


@dataclass(frozen=True)
class Setup:
    """What was run, as the header prints it."""

    criterion: str
    date: str  # from `flows.evaluate.report_date`
    index_size: int
    review_model: str  # the pair and threshold that screened the index
    review_prompt_version: str
    threshold: int
    k: int
    embedding_model: str
    answer_model: str
    prompt_version: str
    judge_model: str
    judge_prompt_version: str
    rrf_c: int
    fusion_depth: int


def report_path(reports_dir: Path, criterion: str, answer_model: str, prompt_version: str) -> Path:
    """docs/reports/ask-eval-<criterion>-<answer model>-<prompt version, / as ->.md."""
    # The same character cleaning as `flows.evaluate.report_path`.
    name = re.sub(r'[<>:"/\\|?*]', "-", f"{criterion}-{answer_model}-{prompt_version}")
    return reports_dir / f"ask-eval-{name}.md"


def _interval(e: Estimate) -> str:
    return f"{e.point:.3f} (95% interval {e.low:.3f} to {e.high:.3f})"


def _ratio(label: str, k: int, n: int, e: Estimate | None) -> str:
    if e is None:
        return f"- {label}: none to measure ({k} of {n})"
    return f"- {label}: {k} of {n}, {_interval(e)}"


def _arm_lines(r: ArmReport) -> list[str]:
    claims = r.groundedness_claims
    questions = r.groundedness_questions
    lines = [
        f"## {r.arm}",
        "",
        _ratio(f"recall@{r.k}", r.recall_at_k.k, r.recall_at_k.n, r.recall_at_k),
        _ratio(
            "groundedness by claim",
            r.claims_supported,
            claims.n if claims else 0,
            claims,
        ),
        _ratio(
            "groundedness by question (every claim supported)",
            r.fully_grounded,
            questions.n if questions else 0,
            questions,
        ),
        f"- claims in all answers: {r.claims_total}; answered questions with a claim: "
        f"{r.answered_questions}",
        f"- correct refusals: {r.refusals_correct} of {r.n_unanswerable}",
        f"- wrong refusals (an answerable question refused): {r.refusals_wrong} of "
        f"{r.n_answerable}",
        f"- errors (no usable answer from the model): {r.errors}",
        f"- not judged (the judge gave no usable answer): {r.judge_unusable} claims, "
        f"{r.questions_not_judged} questions; they are in no groundedness figure",
        f"- cost: ${r.cost_usd:.4f}",
        "",
    ]
    return lines


def _question_lines(rows_by_arm: Mapping[str, Sequence[EvalRow]]) -> list[str]:
    arms = list(rows_by_arm)
    by_arm = {arm: {r.question_id: r for r in rows} for arm, rows in rows_by_arm.items()}
    ids = sorted({qid for rows in by_arm.values() for qid in rows})
    lines = ["## Per question", ""]
    for qid in ids:
        parts = []
        for arm in arms:
            r = by_arm[arm].get(qid)
            if r is None:
                parts.append(f"{arm} not run")
            elif r.source_hit is None:
                parts.append(f"{arm} {r.outcome}")
            else:
                parts.append(f"{arm} {'hit' if r.source_hit else 'miss'}, {r.outcome}")
        lines.append(f"- {qid}: " + "; ".join(parts))
    lines.append("")
    return lines


def render(
    reports: Sequence[ArmReport], rows_by_arm: Mapping[str, Sequence[EvalRow]], setup: Setup
) -> str:
    """The whole report as Markdown text."""
    s = setup
    lines = [
        f"# ask-eval: {s.criterion}, {s.answer_model}, {s.prompt_version}",
        "",
        f"- date: {s.date}",
        f"- index: {s.index_size} abstracts, screened in by {s.review_model} under "
        f"{s.review_prompt_version} at threshold {s.threshold}",
        f"- k: {s.k} abstracts read per question",
        f"- embedding model: {s.embedding_model}",
        f"- answer model: {s.answer_model}, prompt version {s.prompt_version}",
        f"- judge model: {s.judge_model}, prompt version {s.judge_prompt_version}",
        f"- reciprocal rank fusion: constant {s.rrf_c}, each arm read {s.fusion_depth} deep "
        "before fusing",
        "",
    ]
    for r in reports:
        lines += _arm_lines(r)
    lines += _question_lines(rows_by_arm)
    lines += ["## Read this with", ""]
    lines += [f"- {c}" for c in CAVEATS]
    return "\n".join(lines) + "\n"
