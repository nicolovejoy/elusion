"""The `concept_log` table: an append-only record of how a concept was defined and read.

An *append-only log* is a table that only ever takes new rows. Nothing is edited and
nothing is deleted: a changed mind is a new row that comes later, and the history of how
the work went stays whole. That is what lets the owner say afterwards which wording a
reading was made under, and what was known when a decision was taken.

Every row has a `kind`:

- `concept`: a wording of a concept (a slug such as `stopping`), with a version number.
- `reading`: what the model made of one abstract under one concept version.
- `audit`: the owner's own verdict on one abstract, given blind.
- `decision`: a settled disagreement or a changed wording, with the reason.

The rule is enforced by the database and not by this module. A *trigger* is a function
Postgres runs by itself when a row is about to change. Two triggers here refuse every
UPDATE, DELETE and TRUNCATE with the error `concept_log is append-only`, so a stray
script, a psql session or a later bug in this code cannot rewrite history either. The
code never tries; the triggers are the guarantee.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, fields
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.engine import Engine

from elusion.board.quotes import find_quote

KINDS = ("concept", "reading", "audit", "decision")
VERDICTS = ("holds", "does_not_hold", "skip", "failed")

metadata = sa.MetaData()

concept_log = sa.Table(
    "concept_log",
    metadata,
    sa.Column("entry_id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("concept", sa.Text, nullable=False),  # a slug, such as `stopping`
    sa.Column("concept_version", sa.Integer, nullable=False),
    sa.Column("doc_id", sa.String(16)),
    sa.Column("verdict", sa.Text),
    sa.Column("quote", sa.Text),
    sa.Column("body", sa.Text),  # concept: the wording; reading: the rationale; decision: why
    sa.Column("actor", sa.Text, nullable=False),  # owner | claude | owner+claude | model
    sa.Column("model", sa.Text),
    sa.Column("prompt_version", sa.Text),
    sa.Column("detail", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column(
        "written_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.CheckConstraint(
        "kind IN ('concept', 'reading', 'audit', 'decision')", name="concept_log_kind"
    ),
    sa.CheckConstraint(
        "verdict IS NULL OR verdict IN ('holds', 'does_not_hold', 'skip', 'failed')",
        name="concept_log_verdict",
    ),
)

_concept_key = sa.Index(
    "uq_concept_log_concept",
    concept_log.c.concept,
    concept_log.c.concept_version,
    unique=True,
    postgresql_where=concept_log.c.kind == "concept",
)
_READING_KEY = ["concept", "concept_version", "doc_id", "model", "prompt_version"]
_reading_key = sa.Index(
    "uq_concept_log_reading",
    *(concept_log.c[name] for name in _READING_KEY),
    unique=True,
    postgresql_where=concept_log.c.kind == "reading",
)

_REFUSE_FUNCTION = """
CREATE OR REPLACE FUNCTION concept_log_refuse_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'concept_log is append-only';
END;
$$ LANGUAGE plpgsql
"""
_TRIGGERS = (
    "DROP TRIGGER IF EXISTS concept_log_no_change ON concept_log",
    "CREATE TRIGGER concept_log_no_change BEFORE UPDATE OR DELETE ON concept_log "
    "FOR EACH ROW EXECUTE FUNCTION concept_log_refuse_change()",
    "DROP TRIGGER IF EXISTS concept_log_no_truncate ON concept_log",
    "CREATE TRIGGER concept_log_no_truncate BEFORE TRUNCATE ON concept_log "
    "FOR EACH STATEMENT EXECUTE FUNCTION concept_log_refuse_change()",
)


class QuoteNotVerbatim(ValueError):
    """A reading's quote is empty or does not appear in the abstract."""


class AlreadyAudited(ValueError):
    """The paper already has the owner's holds / does_not_hold verdict for this version."""


class UnknownConcept(LookupError):
    """No concept entry exists under that slug."""


@dataclass(frozen=True)
class Entry:
    entry_id: int
    kind: str
    concept: str
    concept_version: int
    doc_id: str | None
    verdict: str | None
    quote: str | None
    body: str | None
    actor: str
    model: str | None
    prompt_version: str | None
    detail: dict[str, Any]
    written_at: datetime


def ensure_schema(engine: Engine) -> None:
    """Create the table, its indexes and the triggers that refuse changes. Safe to repeat."""
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(sa.text(_REFUSE_FUNCTION))
        for statement in _TRIGGERS:
            conn.execute(sa.text(statement))


def _append(engine: Engine, **values: Any) -> int:
    values.setdefault("detail", {})
    stmt = insert(concept_log).values(**values).returning(concept_log.c.entry_id)
    with engine.begin() as conn:
        return conn.execute(stmt).scalar_one()


def add_concept(
    engine: Engine, concept: str, wording: str, actor: str, detail: dict | None = None
) -> int:
    """Write a new wording of `concept`. Returns its version: 1 for a new concept,
    otherwise the highest existing version plus one."""
    c = concept_log.c
    next_version = (
        sa.select(sa.func.coalesce(sa.func.max(c.concept_version), 0) + 1)
        .where(c.kind == "concept", c.concept == concept)
        .scalar_subquery()
    )
    stmt = (
        insert(concept_log)
        .values(
            kind="concept",
            concept=concept,
            concept_version=next_version,
            body=wording,
            actor=actor,
            detail=detail or {},
        )
        .returning(c.concept_version)
    )
    with engine.begin() as conn:
        return conn.execute(stmt).scalar_one()


def _entry(row: sa.Row) -> Entry:
    return Entry(**{f.name: row._mapping[f.name] for f in fields(Entry)})


def current_concept(engine: Engine, concept: str) -> Entry:
    """The concept entry with the highest version. Raises `UnknownConcept`."""
    c = concept_log.c
    stmt = (
        sa.select(concept_log)
        .where(c.kind == "concept", c.concept == concept)
        .order_by(c.concept_version.desc())
        .limit(1)
    )
    with engine.connect() as conn:
        row = conn.execute(stmt).first()
    if row is None:
        raise UnknownConcept(concept)
    return _entry(row)


def add_reading(
    engine: Engine,
    *,
    concept: str,
    version: int,
    doc_id: str,
    verdict: str,
    quote: str | None,
    rationale: str,
    model: str,
    prompt_version: str,
    text: str,
    detail: dict | None = None,
) -> int | None:
    """Record what `model` made of one abstract. Returns the entry id, or None when this
    (concept, version, doc, model, prompt version) already has a reading: the first one
    stands, whatever it was, and nothing is inserted.

    `text` is the stored abstract. A `holds` quote must appear in it (`find_quote`), and so
    must a `does_not_hold` quote unless it is empty, which is stored as null. A `failed`
    reading has no quote.
    """
    if verdict not in ("holds", "does_not_hold", "failed"):
        raise ValueError(f"a reading's verdict is holds, does_not_hold or failed, not {verdict!r}")
    stored = quote.strip() if quote and quote.strip() else None
    if verdict == "failed":
        stored = None
    elif stored is not None or verdict == "holds":
        if stored is None or find_quote(text, stored) is None:
            raise QuoteNotVerbatim(f"not found in the abstract: {quote!r}")
    stmt = (
        insert(concept_log)
        .values(
            kind="reading",
            concept=concept,
            concept_version=version,
            doc_id=doc_id,
            verdict=verdict,
            quote=stored,
            body=rationale,
            actor="model",
            model=model,
            prompt_version=prompt_version,
            detail=detail or {},
        )
        .on_conflict_do_nothing(
            index_elements=_READING_KEY, index_where=concept_log.c.kind == "reading"
        )
        .returning(concept_log.c.entry_id)
    )
    with engine.begin() as conn:
        return conn.execute(stmt).scalar_one_or_none()


def read_doc_ids(
    engine: Engine, concept: str, version: int, model: str, prompt_version: str
) -> set[str]:
    """The papers this model and prompt version have already read under this concept version."""
    c = concept_log.c
    stmt = sa.select(c.doc_id).where(
        c.kind == "reading",
        c.concept == concept,
        c.concept_version == version,
        c.model == model,
        c.prompt_version == prompt_version,
    )
    with engine.connect() as conn:
        return set(conn.execute(stmt).scalars())


def add_audit(engine: Engine, concept: str, version: int, doc_id: str, verdict: str) -> int:
    """Record the owner's blind verdict on a paper: holds, does_not_hold or skip. A verdict
    is given once, so a second holds / does_not_hold raises `AlreadyAudited`; any number of
    skips may come before it."""
    if verdict not in ("holds", "does_not_hold", "skip"):
        raise ValueError(f"an audit's verdict is holds, does_not_hold or skip, not {verdict!r}")
    c = concept_log.c
    with engine.begin() as conn:
        if verdict != "skip":
            given = sa.select(c.entry_id).where(
                c.kind == "audit",
                c.concept == concept,
                c.concept_version == version,
                c.doc_id == doc_id,
                c.verdict.in_(("holds", "does_not_hold")),
            )
            if conn.execute(given.limit(1)).first() is not None:
                raise AlreadyAudited(f"{doc_id} already has a verdict under {concept} v{version}")
        stmt = (
            insert(concept_log)
            .values(
                kind="audit",
                concept=concept,
                concept_version=version,
                doc_id=doc_id,
                verdict=verdict,
                actor="owner",
                detail={},
            )
            .returning(c.entry_id)
        )
        return conn.execute(stmt).scalar_one()


def add_decision(
    engine: Engine,
    concept: str,
    version: int,
    *,
    reason: str,
    actor: str,
    doc_id: str | None = None,
    verdict: str | None = None,
    rests_on: Sequence[int] = (),
) -> int:
    """Record a settled question and why. `rests_on` lists the entry ids it was decided on."""
    if not reason.strip():
        raise ValueError("a decision needs a reason")
    return _append(
        engine,
        kind="decision",
        concept=concept,
        concept_version=version,
        doc_id=doc_id,
        verdict=verdict,
        body=reason,
        actor=actor,
        detail={"rests_on": list(rests_on)},
    )


def entries(
    engine: Engine, concept: str, version: int | None = None, kind: str | None = None
) -> list[Entry]:
    """Entries of a concept, in the order they were written; narrowed to a version or a kind."""
    c = concept_log.c
    stmt = sa.select(concept_log).where(c.concept == concept).order_by(c.entry_id)
    if version is not None:
        stmt = stmt.where(c.concept_version == version)
    if kind is not None:
        stmt = stmt.where(c.kind == kind)
    with engine.connect() as conn:
        return [_entry(row) for row in conn.execute(stmt)]
