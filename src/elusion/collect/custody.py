"""The `harvest_custody` table: where every abstract came from.

*Chain of custody* is the e-discovery term for the record that shows where a document
was collected, when, and that it has not changed since. Here one row is one *hit*: a
work that one query returned in one harvest. A work found by three search terms has
three rows a harvest, all pointing at the same document.

Each row says what was asked (the query, its form, the filter, OpenAlex's catalogue),
when (one UTC timestamp for the whole harvest), what came back (the work's id) and what
its text was (the SHA-256 of title and abstract, and the document id derived from it).
A second harvest adds its own rows for the same works. If a work's hash differs between
two harvests, OpenAlex changed the abstract in between, and the two rows name two
different documents.

The counts a systematic review reports in its PRISMA flow diagram are queries over this
table: works identified per term, and how many were left once duplicates were removed.

`route` is `search` for a search term. The citation route of phase 7's task 3 writes
`citation` rows, with the anchor paper's id as the query and the seed of its sample.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TypedDict

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine

from elusion.collect.db import metadata

harvest_custody = sa.Table(
    "harvest_custody",
    metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column("harvest_id", sa.String(32), nullable=False, index=True),  # one per run
    sa.Column("harvested_at", sa.DateTime(timezone=True), nullable=False),  # the run's start
    sa.Column("route", sa.Text, nullable=False),  # search | citation
    sa.Column("query", sa.Text, nullable=False),  # the search string, or the anchor's work id
    sa.Column("search_form", sa.Text),  # stemmed | exact; null on the citation route
    sa.Column("sample_seed", sa.Integer),  # the citation route's seed; null on a search
    sa.Column("filter", sa.Text, nullable=False),  # the filter sent, window included
    sa.Column("openalex_corpus", sa.Text, nullable=False),  # OpenAlex's catalogue: core
    sa.Column("work_id", sa.Text, nullable=False, index=True),
    sa.Column("doc_id", sa.String(16), nullable=False, index=True),
    sa.Column("content_hash", sa.String(64), nullable=False),
    sa.UniqueConstraint("harvest_id", "route", "query", "work_id", name="uq_custody_hit"),
)


class Hit(TypedDict):
    harvest_id: str
    harvested_at: object  # an aware datetime, UTC
    route: str
    query: str
    search_form: str | None
    sample_seed: int | None
    filter: str
    openalex_corpus: str
    work_id: str
    doc_id: str
    content_hash: str


def ensure_schema(engine: Engine) -> None:
    harvest_custody.create(engine, checkfirst=True)


def record(engine: Engine, hits: Iterable[Hit], batch_size: int = 2000) -> int:
    """Insert one row per hit. Returns rows inserted.

    A cursor can serve the same work twice when the catalogue changes mid-search; the
    unique constraint and `ON CONFLICT DO NOTHING` keep that to one row.
    """
    rows = list(hits)
    inserted = 0
    for start in range(0, len(rows), batch_size):
        stmt = (
            insert(harvest_custody)
            .values(rows[start : start + batch_size])
            .on_conflict_do_nothing(constraint="uq_custody_hit")
            .returning(harvest_custody.c.id)
        )
        with engine.begin() as conn:
            inserted += len(conn.execute(stmt).all())
    return inserted


def drawn_anchors(engine: Engine) -> set[str]:
    """The anchors whose citation sample has been drawn: those with a `citation` row."""
    stmt = sa.select(harvest_custody.c.query).where(harvest_custody.c.route == "citation")
    with engine.connect() as conn:
        return set(conn.execute(stmt.distinct()).scalars())


@dataclass(frozen=True)
class QueryCount:
    route: str
    query: str
    search_form: str | None
    works: int  # distinct works the query has returned, over every harvest


def works_per_query(engine: Engine) -> list[QueryCount]:
    """Distinct works identified by each query: searches first, each route in the order
    its queries were first run. The "identified" boxes of a PRISMA flow diagram.

    Distinct, because a second harvest writes a second row for a work it finds again.
    """
    c = harvest_custody.c
    stmt = (
        sa.select(c.route, c.query, c.search_form, sa.func.count(sa.distinct(c.work_id)))
        .group_by(c.route, c.query, c.search_form)
        .order_by(sa.case((c.route == "search", 0), else_=1), sa.func.min(c.id))
    )
    with engine.connect() as conn:
        return [QueryCount(*row) for row in conn.execute(stmt)]


@dataclass(frozen=True)
class Seed:
    anchors: tuple[str, ...]  # the anchors whose sample drew this work, sorted
    by_search: bool  # a search term returned it too


def seed_works(engine: Engine) -> dict[str, Seed]:
    """Every work drawn on the citation route, by work id, and whether a search term
    also returned it. Harvest recall is `by_search` over the seeds the judge calls
    relevant. It is keyed by work and not by document, so a seed whose abstract changed
    between the two routes still counts as found.
    """
    c = harvest_custody.c
    stmt = sa.select(
        c.work_id,
        sa.func.array_agg(sa.distinct(c.query)).filter(c.route == "citation"),
        sa.func.bool_or(c.route == "search"),
    ).group_by(c.work_id)
    stmt = stmt.having(sa.func.bool_or(c.route == "citation"))
    with engine.connect() as conn:
        return {
            work_id: Seed(anchors=tuple(sorted(anchors)), by_search=by_search)
            for work_id, anchors, by_search in conn.execute(stmt)
        }
