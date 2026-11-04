"""The `documents` table: one row of metadata per paragraph, text stays in Parquet.

SQLAlchemy Core (not the ORM) describes the table and builds the INSERT; psycopg
executes it. `ON CONFLICT DO NOTHING` on the primary key is what makes a re-run of
collect safe: rows already present are skipped, and the function reports how many
were new.
"""

from __future__ import annotations

from collections.abc import Iterable

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine

from elusion.collect.parquet import Document

metadata = sa.MetaData()

documents = sa.Table(
    "documents",
    metadata,
    sa.Column("doc_id", sa.String(16), primary_key=True),
    sa.Column("cik", sa.String(10), nullable=False, index=True),
    sa.Column("company", sa.Text),
    sa.Column("year", sa.SmallInteger, nullable=False, index=True),
    sa.Column("paragraph_index", sa.Integer, nullable=False),
    sa.Column("word_count", sa.Integer, nullable=False),
    sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
)


def make_engine(database_url: str) -> Engine:
    # SQLAlchemy's default Postgres driver is psycopg2; name psycopg (v3) explicitly.
    url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return sa.create_engine(url)


def ensure_schema(engine: Engine) -> None:
    metadata.create_all(engine)


def upsert_documents(engine: Engine, docs: Iterable[Document], batch_size: int = 5000) -> int:
    """Insert rows for `docs`, skipping doc_ids already present. Returns rows inserted."""
    inserted = 0
    batch: list[dict] = []

    def flush() -> None:
        nonlocal inserted
        if not batch:
            return
        # RETURNING yields only the rows actually inserted, so its length is the
        # new-row count; rowcount is unreliable for multi-row inserts through psycopg.
        stmt = (
            insert(documents)
            .values(batch)
            .on_conflict_do_nothing(index_elements=["doc_id"])
            .returning(documents.c.doc_id)
        )
        with engine.begin() as conn:
            inserted += len(conn.execute(stmt).all())
        batch.clear()

    for d in docs:
        batch.append(
            {
                "doc_id": d["doc_id"],
                "cik": d["cik"],
                "company": d["company"],
                "year": d["year"],
                "paragraph_index": d["paragraph_index"],
                "word_count": len(d["text"].split()),
            }
        )
        if len(batch) >= batch_size:
            flush()
    flush()
    return inserted


def count_documents(engine: Engine, year: int | None = None) -> int:
    stmt = sa.select(sa.func.count()).select_from(documents)
    if year is not None:
        stmt = stmt.where(documents.c.year == year)
    with engine.connect() as conn:
        return conn.execute(stmt).scalar_one()


NO_COMPANY = "(no company name)"  # documents.company is null when EDGAR had no name


def company_counts(engine: Engine, doc_ids: Iterable[str]) -> list[tuple[str, int]]:
    """Distinct companies among `doc_ids` with paragraph counts, most paragraphs first,
    ties by name. A null company is reported as NO_COMPANY."""
    ids = list(doc_ids)
    if not ids:
        return []
    stmt = (
        sa.select(documents.c.company, sa.func.count())
        .where(documents.c.doc_id.in_(ids))
        .group_by(documents.c.company)
    )
    with engine.connect() as conn:
        counts = [(name or NO_COMPANY, n) for name, n in conn.execute(stmt)]
    return sorted(counts, key=lambda c: (-c[1], c[0]))
