"""The audit's logic: the order papers are served in, the owner's verdicts, the
disagreements with the reader, and the summary figures. All of it is computed from the log,
so each test writes entries through `log.py` and asks `audit.py` what they add up to.

Every test runs against the throwaway database of the `board_engine` fixture, under a
concept slug of its own.
"""

import uuid

import pytest

from elusion.board import audit, log

READER = audit.ReaderKey("m-1", "v1")


def doc(i: int) -> str:
    return f"{i:016x}"


def text(i: int) -> str:
    return f"Paper {i} says a review stops at a fixed recall."


@pytest.fixture
def concept(board_engine) -> str:
    slug = f"c{uuid.uuid4().hex[:10]}"
    log.add_concept(board_engine, slug, "when does a review stop", "owner")
    return slug


def read(engine, concept, i, verdict, *, model="m-1", prompt_version="v1") -> int:
    entry_id = log.add_reading(
        engine,
        concept=concept,
        version=1,
        doc_id=doc(i),
        verdict=verdict,
        quote="a fixed recall" if verdict == "holds" else None,
        rationale=f"reason {i}",
        model=model,
        prompt_version=prompt_version,
        text=text(i),
    )
    assert entry_id is not None
    return entry_id


def blind(engine, concept, i, verdict) -> int:
    return log.add_audit(engine, concept, 1, doc(i), verdict)


# -- the order and the next paper --------------------------------------------------------


def test_audit_order_is_the_same_on_every_call_and_not_the_sorted_order() -> None:
    ids = [doc(i) for i in range(30)]
    first = audit.audit_order(ids, "stopping", 1)
    assert first == audit.audit_order(list(reversed(ids)), "stopping", 1)
    assert sorted(first) == ids
    assert first != ids


def test_audit_order_differs_between_concepts_and_versions() -> None:
    ids = [doc(i) for i in range(30)]
    assert audit.audit_order(ids, "stopping", 1) != audit.audit_order(ids, "stopping", 2)
    assert audit.audit_order(ids, "stopping", 1) != audit.audit_order(ids, "other", 1)


def test_next_blind_serves_unseen_papers_in_audit_order_then_the_oldest_skip(
    board_engine, concept
) -> None:
    ids = [doc(i) for i in range(4)]
    order = audit.audit_order(ids, concept, 1)
    by_id = {d: i for i, d in enumerate(ids)}
    assert audit.next_blind(board_engine, concept, 1, ids) == order[0]

    blind(board_engine, concept, by_id[order[0]], "skip")
    assert audit.next_blind(board_engine, concept, 1, ids) == order[1]
    blind(board_engine, concept, by_id[order[1]], "holds")
    blind(board_engine, concept, by_id[order[2]], "skip")
    assert audit.next_blind(board_engine, concept, 1, ids) == order[3]

    blind(board_engine, concept, by_id[order[3]], "skip")
    # every paper has an entry: the skipped one whose latest skip is oldest comes back
    assert audit.next_blind(board_engine, concept, 1, ids) == order[0]
    blind(board_engine, concept, by_id[order[0]], "skip")  # now its latest skip is the newest
    assert audit.next_blind(board_engine, concept, 1, ids) == order[2]


def test_next_blind_is_none_when_every_paper_has_a_verdict(board_engine, concept) -> None:
    ids = [doc(i) for i in range(3)]
    blind(board_engine, concept, 0, "skip")
    for i in range(3):
        blind(board_engine, concept, i, "does_not_hold")
    assert audit.next_blind(board_engine, concept, 1, ids) is None


def test_blind_verdicts_holds_one_verdict_entry_per_paper(board_engine, concept) -> None:
    blind(board_engine, concept, 0, "skip")
    blind(board_engine, concept, 0, "holds")
    blind(board_engine, concept, 1, "skip")
    found = audit.blind_verdicts(board_engine, concept, 1)
    assert {d: e.verdict for d, e in found.items()} == {doc(0): "holds"}


# -- the reader ---------------------------------------------------------------------------


def test_reader_of_names_the_one_model_and_prompt_version(board_engine, concept) -> None:
    read(board_engine, concept, 0, "holds")
    read(board_engine, concept, 1, "does_not_hold")
    assert audit.reader_of(board_engine, concept, 1) == READER


def test_reader_of_refuses_no_readings_and_more_than_one_pair(board_engine, concept) -> None:
    with pytest.raises(ValueError, match="no reading"):
        audit.reader_of(board_engine, concept, 1)
    read(board_engine, concept, 0, "holds")
    read(board_engine, concept, 0, "holds", prompt_version="v2")
    with pytest.raises(ValueError, match="more than one"):
        audit.reader_of(board_engine, concept, 1)


# -- disagreements and final verdicts ------------------------------------------------------


def seed_six(engine, concept) -> list[str]:
    """Readings d0..d5: holds, holds, no, no, failed, holds. Blind verdicts: holds, no, no,
    holds, holds, holds. So d1 and d3 disagree; d4's reading failed; d0, d2, d5 agree."""
    readings = ["holds", "holds", "does_not_hold", "does_not_hold", "failed", "holds"]
    blinds = ["holds", "does_not_hold", "does_not_hold", "holds", "holds", "holds"]
    for i, (r, b) in enumerate(zip(readings, blinds, strict=True)):
        read(engine, concept, i, r)
        blind(engine, concept, i, b)
    return [doc(i) for i in range(6)]


def test_disagreements_are_the_differing_verdicts_and_never_a_failed_reading(
    board_engine, concept
) -> None:
    ids = seed_six(board_engine, concept)
    found = audit.disagreements(board_engine, concept, 1, READER, ids)
    assert sorted(d.doc_id for d in found) == [doc(1), doc(3)]
    for d in found:
        assert d.blind.verdict != d.reading.verdict
        assert d.blind.kind == "audit" and d.reading.kind == "reading"
        assert d.decision is None


def test_a_disagreement_carries_its_latest_decision(board_engine, concept) -> None:
    ids = seed_six(board_engine, concept)
    log.add_decision(
        board_engine, concept, 1, reason="first thought", actor="owner", doc_id=doc(1),
        verdict="holds",
    )  # fmt: skip
    last = log.add_decision(
        board_engine, concept, 1, reason="second thought", actor="owner", doc_id=doc(1),
        verdict="does_not_hold",
    )  # fmt: skip
    found = {d.doc_id: d for d in audit.disagreements(board_engine, concept, 1, READER, ids)}
    assert found[doc(1)].decision.entry_id == last
    assert found[doc(3)].decision is None


def test_disagreements_ignore_another_readers_readings(board_engine, concept) -> None:
    ids = seed_six(board_engine, concept)
    read(board_engine, concept, 0, "does_not_hold", model="m-2")
    found = audit.disagreements(board_engine, concept, 1, READER, ids)
    assert doc(0) not in {d.doc_id for d in found}


def test_final_verdicts_take_the_decision_over_the_blind_verdict(board_engine, concept) -> None:
    ids = seed_six(board_engine, concept)
    log.add_decision(
        board_engine, concept, 1, reason="the reader was right", actor="owner", doc_id=doc(1),
        verdict="holds",
    )  # fmt: skip
    final = audit.final_verdicts(board_engine, concept, 1, ids)
    assert final[doc(1)] == "holds"  # the owner said no blind; the decision says yes
    assert final[doc(3)] == "holds"  # unsettled: the blind verdict stands
    assert final[doc(2)] == "does_not_hold"
    assert set(final) == set(ids)


# -- the summary --------------------------------------------------------------------------


def test_summary_counts_agreement_failures_and_unsettled(board_engine, concept) -> None:
    ids = seed_six(board_engine, concept)
    s = audit.summary(board_engine, concept, 1, READER, ids, held_back_n=2)
    assert (s.audited, s.compared, s.agreed) == (6, 5, 3)
    assert (s.agreement.k, s.agreement.n) == (3, 5)
    assert s.agreement.point == pytest.approx(0.6)
    assert (s.failed_readings, s.unsettled, s.held_back) == (1, 2, 2)
    # blind holds: d0, d3, d4, d5
    assert (s.holds_final, s.precision.k, s.precision.n) == (4, 4, 6)
    assert s.precision.low < s.precision.point < s.precision.high


def test_a_decision_changes_holds_final_and_unsettled(board_engine, concept) -> None:
    ids = seed_six(board_engine, concept)
    log.add_decision(
        board_engine, concept, 1, reason="reads as stopping", actor="owner", doc_id=doc(1),
        verdict="holds",
    )  # fmt: skip
    s = audit.summary(board_engine, concept, 1, READER, ids, held_back_n=0)
    assert (s.holds_final, s.unsettled) == (5, 1)
    log.add_decision(
        board_engine, concept, 1, reason="no, it does not", actor="owner", doc_id=doc(3),
        verdict="does_not_hold",
    )  # fmt: skip
    s = audit.summary(board_engine, concept, 1, READER, ids, held_back_n=0)
    assert (s.holds_final, s.unsettled) == (4, 0)
    assert s.agreed == 3  # agreement is the blind figure: a decision does not change it


def test_summary_has_no_agreement_when_nothing_can_be_compared(board_engine, concept) -> None:
    read(board_engine, concept, 0, "failed")
    blind(board_engine, concept, 0, "holds")
    s = audit.summary(board_engine, concept, 1, READER, [doc(0)], held_back_n=0)
    assert (s.compared, s.agreement, s.failed_readings) == (0, None, 1)
    assert s.precision.k == 1
