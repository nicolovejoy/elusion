"""The per-arm figures of `ask-eval`, computed from plain rows: no database, no model."""

import pytest

from elusion.ask.metrics import ClaimResult, EvalRow, arm_report
from elusion.evaluate.metrics import proportion

OK = ClaimResult(checked=True, supported=True)
NO = ClaimResult(checked=True, supported=False)
UNCHECKED = ClaimResult(checked=False, supported=None)
UNUSABLE = ClaimResult(checked=True, supported=None)


def row(question_id, kind="answerable", hit=True, outcome="answered", claims=()):
    return EvalRow(question_id, kind, hit if kind == "answerable" else None, outcome, tuple(claims))


def report(rows, cost=1.5):
    return arm_report("hybrid", 5, rows, cost)


def test_recall_is_hits_over_answerable_with_an_interval() -> None:
    rows = [
        row("q1", hit=True),
        row("q2", hit=True, outcome="refused"),  # a refusal still counts: retrieval found it
        row("q3", hit=False, outcome="error"),
        row("q4", hit=False),
        row("u1", kind="unanswerable", outcome="refused"),
    ]
    r = report(rows)
    assert (r.n_answerable, r.n_unanswerable) == (4, 1)
    assert r.recall_at_k == proportion(2, 4)
    assert (r.arm, r.k, r.cost_usd) == ("hybrid", 5, 1.5)


def test_a_claim_with_a_failed_check_is_not_supported() -> None:
    r = report([row("q1", outcome="failed", claims=[OK, UNCHECKED])])
    assert (r.claims_total, r.claims_supported) == (2, 1)
    assert r.groundedness_claims == proportion(1, 2)
    assert r.judge_unusable == 0


def test_a_claim_the_judge_could_not_answer_is_not_supported_and_is_counted() -> None:
    """Not counted as supported, not counted as unsupported: it is out of the denominator
    and reported on its own, with the question it sits in."""
    r = report(
        [
            row("q1", claims=[OK, UNUSABLE]),
            row("q2", claims=[OK]),
            row("q3", claims=[OK, NO, UNUSABLE]),
        ]
    )
    assert r.judge_unusable == 2
    assert (r.claims_total, r.claims_supported) == (6, 3)
    assert r.groundedness_claims == proportion(3, 4)
    # q1 cannot be decided; q3 has an unsupported claim whatever the judge said of the other.
    assert r.questions_not_judged == 1
    assert (r.answered_questions, r.fully_grounded) == (3, 1)
    assert r.groundedness_questions == proportion(1, 2)


def test_groundedness_by_question_needs_every_claim_supported() -> None:
    r = report(
        [
            row("q1", claims=[OK, OK, OK]),
            row("q2", claims=[OK, NO]),
            row("q3", outcome="failed", claims=[OK, UNCHECKED]),
            row("q4", claims=[OK]),
        ]
    )
    assert (r.answered_questions, r.fully_grounded) == (4, 2)
    assert r.groundedness_questions == proportion(2, 4)
    assert r.groundedness_claims == proportion(6, 8)


def test_refused_and_error_rows_are_not_in_the_by_question_denominator() -> None:
    r = report(
        [
            row("q1", claims=[OK]),
            row("q2", outcome="refused"),
            row("q3", outcome="error"),
            row("u1", kind="unanswerable", outcome="refused"),
            row("u2", kind="unanswerable", outcome="answered", claims=[NO]),
        ]
    )
    assert r.answered_questions == 2  # q1 and u2: the rows with claims
    assert r.groundedness_questions == proportion(1, 2)
    assert r.errors == 1


def test_correct_refusals_are_counted_over_the_unanswerable() -> None:
    r = report(
        [
            row("u1", kind="unanswerable", outcome="refused"),
            row("u2", kind="unanswerable", outcome="refused"),
            row("u3", kind="unanswerable", outcome="answered", claims=[OK]),
            row("u4", kind="unanswerable", outcome="error"),
            row("q1"),
        ]
    )
    assert (r.refusals_correct, r.n_unanswerable) == (2, 4)


def test_wrong_refusals_are_counted_over_the_answerable() -> None:
    r = report(
        [
            row("q1", outcome="refused"),
            row("q2", outcome="refused"),
            row("q3", claims=[OK]),
            row("u1", kind="unanswerable", outcome="refused"),
        ]
    )
    assert r.refusals_wrong == 2
    assert r.refusals_correct == 1


def test_no_claims_gives_none_not_a_division_by_zero() -> None:
    r = report([row("q1", outcome="refused"), row("u1", kind="unanswerable", outcome="refused")])
    assert r.groundedness_claims is None
    assert r.groundedness_questions is None
    assert (r.claims_total, r.answered_questions, r.fully_grounded) == (0, 0, 0)
    # Every claim undecided is the same: nothing to divide by.
    undecided = report([row("q1", claims=[UNUSABLE])])
    assert undecided.groundedness_claims is None
    assert undecided.groundedness_questions is None
    assert undecided.judge_unusable == 1 and undecided.questions_not_judged == 1


def test_a_report_with_no_answerable_question_is_refused() -> None:
    with pytest.raises(ValueError, match="answerable"):
        report([row("u1", kind="unanswerable", outcome="refused")])
