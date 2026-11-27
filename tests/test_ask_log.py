"""The `ask_log` table: it takes inserts only, and refuses what the database should refuse.

Every test runs against the throwaway database of the `index_engine` fixture.
"""

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError

from elusion.ask import log


def row(engine, **overrides) -> int:
    columns = dict(
        question="what is a stopping rule? ",
        arm="hybrid",
        k=5,
        retrieved=[{"doc_id": "d1", "rank": 1, "score": 0.5}],
        embedding_model="fake-embedder",
        answer_model="claude-sonnet-5-5",
        served_model="claude-sonnet-5-5",
        prompt_version="ask/v1",
        outcome="refused",
    )
    return log.add_ask(engine, **{**columns, **overrides})


def test_update_is_refused(index_engine) -> None:
    ask_id = row(index_engine)
    with pytest.raises(DBAPIError, match="ask_log is append-only"), index_engine.begin() as conn:
        conn.execute(sa.update(log.ask_log).where(log.ask_log.c.ask_id == ask_id).values(k=3))
    assert log.get_ask(index_engine, ask_id).k == 5


def test_delete_is_refused(index_engine) -> None:
    ask_id = row(index_engine)
    with pytest.raises(DBAPIError, match="ask_log is append-only"), index_engine.begin() as conn:
        conn.execute(sa.delete(log.ask_log).where(log.ask_log.c.ask_id == ask_id))
    assert len(log.asks(index_engine)) == 1


def test_truncate_is_refused(index_engine) -> None:
    row(index_engine)
    with pytest.raises(DBAPIError, match="ask_log is append-only"), index_engine.begin() as conn:
        conn.execute(sa.text("TRUNCATE ask_log"))
    assert len(log.asks(index_engine)) == 1


def test_ensure_schema_twice(index_engine) -> None:
    ask_id = row(index_engine)
    log.ensure_schema(index_engine)
    log.ensure_schema(index_engine)
    assert [r.ask_id for r in log.asks(index_engine)] == [ask_id]
    with pytest.raises(DBAPIError, match="append-only"), index_engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM ask_log"))


def test_an_unknown_outcome_is_refused_by_the_database(index_engine) -> None:
    with pytest.raises(IntegrityError, match="ask_log_outcome"):
        row(index_engine, outcome="maybe")
    with pytest.raises(IntegrityError, match="ask_log_arm"):
        row(index_engine, arm="fuzzy")
    assert log.asks(index_engine) == []


def test_written_at_is_timezone_aware(index_engine) -> None:
    got = log.get_ask(index_engine, row(index_engine))
    assert got.asked_at.tzinfo is not None
    assert got.asked_at.utcoffset().total_seconds() == 0


def test_a_row_round_trips_with_its_defaults(index_engine) -> None:
    got = log.get_ask(index_engine, row(index_engine))
    assert got.question == "what is a stopping rule? "  # exactly as typed
    assert got.retrieved == [{"doc_id": "d1", "rank": 1, "score": 0.5}]
    assert got.answer is None and got.stop_reason is None
    assert got.checks == [] and got.detail == {}
    assert (got.input_tokens, got.output_tokens) == (0, 0)
    assert (got.cache_creation_input_tokens, got.cache_read_input_tokens) == (0, 0)
    assert got.cost_usd == 0


def test_asks_come_in_id_order_and_can_be_narrowed_to_a_question(index_engine) -> None:
    a = row(index_engine, question="one")
    b = row(index_engine, question="two")
    c = row(index_engine, question="one")
    assert [r.ask_id for r in log.asks(index_engine)] == [a, b, c]
    assert [r.ask_id for r in log.asks(index_engine, "one")] == [a, c]
    assert log.asks(index_engine, "one ") == []  # the wording is exact
