"""The `concept_log` table: it takes inserts only, and its writers keep their promises.

Every test runs against the throwaway database of the `board_engine` fixture, under a
concept slug of its own.
"""

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError

from elusion.board import log

ABSTRACT = "Stopping rules decide when a review ends.\nThe team stops at a fixed recall."


@pytest.fixture
def concept(board_engine) -> str:
    return f"c{uuid.uuid4().hex[:10]}"


def reading(engine, concept: str, **overrides) -> int | None:
    args = dict(
        concept=concept,
        version=1,
        doc_id="d" * 16,
        verdict="holds",
        quote="a fixed recall",
        rationale="it names a recall target",
        model="m-1",
        prompt_version="v1",
        text=ABSTRACT,
    )
    return log.add_reading(engine, **{**args, **overrides})


def test_an_update_is_refused_by_the_database(board_engine, concept) -> None:
    log.add_concept(board_engine, concept, "when a review stops", "owner")
    with pytest.raises(DBAPIError, match="append-only"), board_engine.begin() as conn:
        conn.execute(
            sa.update(log.concept_log).where(log.concept_log.c.concept == concept).values(body="x")
        )


def test_a_delete_is_refused_by_the_database(board_engine, concept) -> None:
    log.add_concept(board_engine, concept, "when a review stops", "owner")
    with pytest.raises(DBAPIError, match="append-only"), board_engine.begin() as conn:
        conn.execute(sa.delete(log.concept_log).where(log.concept_log.c.concept == concept))
    assert len(log.entries(board_engine, concept)) == 1


def test_a_truncate_is_refused_by_the_database(board_engine, concept) -> None:
    log.add_concept(board_engine, concept, "when a review stops", "owner")
    with pytest.raises(DBAPIError, match="append-only"), board_engine.begin() as conn:
        conn.execute(sa.text("TRUNCATE concept_log"))
    assert len(log.entries(board_engine, concept)) == 1


def test_ensure_schema_can_run_twice(board_engine) -> None:
    log.ensure_schema(board_engine)
    log.ensure_schema(board_engine)


def test_concept_versions_count_up_from_one(board_engine, concept) -> None:
    assert log.add_concept(board_engine, concept, "first wording", "owner") == 1
    assert log.add_concept(board_engine, concept, "second wording", "owner+claude") == 2
    current = log.current_concept(board_engine, concept)
    assert (current.concept_version, current.body, current.actor) == (
        2,
        "second wording",
        "owner+claude",
    )


def test_an_unknown_concept_raises(board_engine, concept) -> None:
    with pytest.raises(log.UnknownConcept):
        log.current_concept(board_engine, concept)


def test_a_holds_reading_with_a_quote_not_in_the_text_raises_and_inserts_nothing(
    board_engine, concept
) -> None:
    with pytest.raises(log.QuoteNotVerbatim):
        reading(board_engine, concept, quote="a variable recall")
    assert log.entries(board_engine, concept) == []


def test_a_holds_reading_with_an_empty_quote_raises(board_engine, concept) -> None:
    with pytest.raises(log.QuoteNotVerbatim):
        reading(board_engine, concept, quote="  ")
    assert log.entries(board_engine, concept) == []


def test_a_does_not_hold_reading_checks_a_quote_but_stores_an_empty_one_as_null(
    board_engine, concept
) -> None:
    with pytest.raises(log.QuoteNotVerbatim):
        reading(board_engine, concept, verdict="does_not_hold", quote="not in the text")
    reading(board_engine, concept, verdict="does_not_hold", quote="")
    [entry] = log.entries(board_engine, concept)
    assert (entry.verdict, entry.quote, entry.actor) == ("does_not_hold", None, "model")


def test_a_second_reading_under_the_same_key_inserts_nothing(board_engine, concept) -> None:
    assert reading(board_engine, concept) is not None
    assert reading(board_engine, concept, rationale="another go") is None
    assert len(log.entries(board_engine, concept, kind="reading")) == 1


def test_a_reading_under_another_model_is_a_new_row(board_engine, concept) -> None:
    reading(board_engine, concept)
    assert reading(board_engine, concept, model="m-2") is not None


def test_a_failed_reading_stores_a_null_quote(board_engine, concept) -> None:
    reading(board_engine, concept, verdict="failed", quote="whatever it said", rationale="")
    [entry] = log.entries(board_engine, concept)
    assert (entry.verdict, entry.quote) == ("failed", None)


def test_read_doc_ids_lists_the_papers_a_model_and_prompt_have_read(board_engine, concept) -> None:
    reading(board_engine, concept, doc_id="a" * 16)
    reading(board_engine, concept, doc_id="b" * 16, verdict="failed", quote=None)
    reading(board_engine, concept, doc_id="c" * 16, prompt_version="v2")
    assert log.read_doc_ids(board_engine, concept, 1, "m-1", "v1") == {"a" * 16, "b" * 16}


def test_an_audit_allows_skips_then_one_verdict(board_engine, concept) -> None:
    doc = "e" * 16
    log.add_audit(board_engine, concept, 1, doc, "skip")
    log.add_audit(board_engine, concept, 1, doc, "skip")
    log.add_audit(board_engine, concept, 1, doc, "holds")
    with pytest.raises(log.AlreadyAudited):
        log.add_audit(board_engine, concept, 1, doc, "does_not_hold")
    assert [e.verdict for e in log.entries(board_engine, concept, kind="audit")] == [
        "skip",
        "skip",
        "holds",
    ]


def test_the_database_refuses_a_second_verdict_that_bypasses_add_audit(
    board_engine, concept
) -> None:
    doc = "f" * 16
    log.add_audit(board_engine, concept, 1, doc, "holds")
    row = dict(kind="audit", concept=concept, concept_version=1, doc_id=doc, actor="owner")
    with pytest.raises(IntegrityError), board_engine.begin() as conn:
        conn.execute(sa.insert(log.concept_log).values(verdict="does_not_hold", **row))
    with board_engine.begin() as conn:  # skips are not verdicts: still allowed
        conn.execute(sa.insert(log.concept_log).values(verdict="skip", **row))


def test_a_decision_needs_a_reason(board_engine, concept) -> None:
    with pytest.raises(ValueError):
        log.add_decision(board_engine, concept, 1, reason="  ", actor="owner")
    assert log.entries(board_engine, concept) == []


def test_a_decision_stores_the_entries_it_rests_on(board_engine, concept) -> None:
    log.add_decision(
        board_engine,
        concept,
        1,
        reason="the quote is a target, not a rule",
        actor="owner+claude",
        doc_id="f" * 16,
        verdict="does_not_hold",
        rests_on=(3, 5),
    )
    [entry] = log.entries(board_engine, concept, version=1, kind="decision")
    assert entry.detail == {"rests_on": [3, 5]}
    assert (entry.body, entry.verdict, entry.doc_id) == (
        "the quote is a target, not a rule",
        "does_not_hold",
        "f" * 16,
    )


def test_written_at_is_timezone_aware(board_engine, concept) -> None:
    log.add_concept(board_engine, concept, "when a review stops", "owner")
    [entry] = log.entries(board_engine, concept)
    assert entry.written_at.utcoffset() is not None


def test_many_readings_on_one_connection_all_store_and_a_repeat_is_skipped(
    board_engine, concept
) -> None:
    # psycopg prepares a statement server-side after 5 runs, and Postgres may plan it
    # generically after 5 more. A bind parameter in the ON CONFLICT predicate then no longer
    # proves it implies the partial index, and the 11th insert raised. One connection makes
    # every run of the statement share that fate.
    one_connection = sa.create_engine(board_engine.url, poolclass=sa.pool.StaticPool)
    try:
        ids = [reading(one_connection, concept, doc_id=f"{i:016d}") for i in range(12)]
        assert all(isinstance(i, int) for i in ids)
        assert reading(one_connection, concept, doc_id=f"{3:016d}") is None
    finally:
        one_connection.dispose()
    assert len(log.entries(board_engine, concept, kind="reading")) == 12


def test_the_on_conflict_predicate_has_no_bind_parameter(board_engine, concept) -> None:
    sql = []
    one_connection = sa.create_engine(board_engine.url, poolclass=sa.pool.StaticPool)
    sa.event.listen(
        one_connection, "before_cursor_execute", lambda c, cur, stmt, *a: sql.append(stmt)
    )
    try:
        reading(one_connection, concept)
    finally:
        one_connection.dispose()
    [insert] = [s for s in sql if "ON CONFLICT" in s]
    assert "WHERE kind = 'reading'" in insert
