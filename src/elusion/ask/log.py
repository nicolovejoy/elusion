"""The `ask_log` table: one row for every question asked, and never a changed one.

An *audit trail* answers four questions about an answer after the fact: who asked, and in
exactly which words; what was retrieved and returned; which claims were made and which quote
each rests on; and which model and prompt version produced it. Each `ask` writes one row that
holds all four, whether the ask was answered, refused, failed its quote check, or errored.

Like the board's `concept_log`, the table is *append-only*, and the database enforces it, not
this module: two triggers (a function Postgres runs by itself when a row is about to change)
refuse every UPDATE, DELETE and TRUNCATE with `ask_log is append-only`. A later judgement of
an answer's claims is a row in another table that points at `ask_id`; this one is never
edited.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.engine import Engine

from elusion.ask.retrieve import ARMS

OUTCOMES = ("answered", "refused", "failed", "error")

metadata = sa.MetaData()

ask_log = sa.Table(
    "ask_log",
    metadata,
    sa.Column("ask_id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("asked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.Column("question", sa.Text, nullable=False),  # exactly as typed
    sa.Column("arm", sa.Text, nullable=False),
    sa.Column("k", sa.SmallInteger, nullable=False),
    # [{"doc_id", "rank", "score"}, ...] in rank order
    sa.Column("retrieved", JSONB, nullable=False),
    sa.Column("embedding_model", sa.Text),  # null for the lexical arm
    sa.Column("answer_model", sa.Text, nullable=False),  # the pinned id
    sa.Column("served_model", sa.Text),  # the id the API answered with
    sa.Column("prompt_version", sa.Text, nullable=False),
    sa.Column("outcome", sa.Text, nullable=False),
    sa.Column("answer", JSONB),  # the Answer as parsed, whole
    # [{"index", "doc_id", "retrieved", "verbatim"}, ...]
    sa.Column("checks", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    sa.Column("stop_reason", sa.Text),
    sa.Column("input_tokens", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("output_tokens", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column(
        "cache_creation_input_tokens", sa.Integer, nullable=False, server_default=sa.text("0")
    ),
    sa.Column("cache_read_input_tokens", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column(
        "cost_usd", sa.Numeric(10, 6, asdecimal=False), nullable=False, server_default=sa.text("0")
    ),
    sa.Column("detail", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.CheckConstraint("arm IN (" + ", ".join(f"'{a}'" for a in ARMS) + ")", name="ask_log_arm"),
    sa.CheckConstraint(
        "outcome IN (" + ", ".join(f"'{o}'" for o in OUTCOMES) + ")", name="ask_log_outcome"
    ),
)

_REFUSE_FUNCTION = """
CREATE OR REPLACE FUNCTION ask_log_refuse_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'ask_log is append-only';
END;
$$ LANGUAGE plpgsql
"""
_TRIGGERS = (
    "DROP TRIGGER IF EXISTS ask_log_no_change ON ask_log",
    "CREATE TRIGGER ask_log_no_change BEFORE UPDATE OR DELETE ON ask_log "
    "FOR EACH ROW EXECUTE FUNCTION ask_log_refuse_change()",
    "DROP TRIGGER IF EXISTS ask_log_no_truncate ON ask_log",
    "CREATE TRIGGER ask_log_no_truncate BEFORE TRUNCATE ON ask_log "
    "FOR EACH STATEMENT EXECUTE FUNCTION ask_log_refuse_change()",
)


@dataclass(frozen=True)
class AskRow:
    ask_id: int
    asked_at: datetime
    question: str
    arm: str
    k: int
    retrieved: list[dict[str, Any]]
    embedding_model: str | None
    answer_model: str
    served_model: str | None
    prompt_version: str
    outcome: str
    answer: dict[str, Any] | None
    checks: list[dict[str, Any]]
    stop_reason: str | None
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    cost_usd: float
    detail: dict[str, Any]


def ensure_schema(engine: Engine) -> None:
    """Create the table and the triggers that refuse changes. Safe to repeat."""
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(sa.text(_REFUSE_FUNCTION))
        for statement in _TRIGGERS:
            conn.execute(sa.text(statement))


def add_ask(engine: Engine, **columns: Any) -> int:
    """Append one row and return its `ask_id`. `columns` are named as the table's columns;
    the ones with a default (`asked_at`, `checks`, the token counts, `cost_usd`, `detail`)
    may be left out."""
    stmt = insert(ask_log).values(**columns).returning(ask_log.c.ask_id)
    with engine.begin() as conn:
        return conn.execute(stmt).scalar_one()


def _row(row: sa.Row) -> AskRow:
    return AskRow(**{f.name: row._mapping[f.name] for f in fields(AskRow)})


def get_ask(engine: Engine, ask_id: int) -> AskRow:
    """One row by id. Raises `LookupError` when there is none."""
    with engine.connect() as conn:
        found = conn.execute(sa.select(ask_log).where(ask_log.c.ask_id == ask_id)).first()
    if found is None:
        raise LookupError(f"no ask {ask_id}")
    return _row(found)


def asks(engine: Engine, question: str | None = None) -> list[AskRow]:
    """Every row in `ask_id` order, or those whose question is exactly `question`."""
    stmt = sa.select(ask_log).order_by(ask_log.c.ask_id)
    if question is not None:
        stmt = stmt.where(ask_log.c.question == question)
    with engine.connect() as conn:
        return [_row(r) for r in conn.execute(stmt)]
