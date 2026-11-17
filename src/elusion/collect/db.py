"""The `documents` table: one row of metadata per paragraph, text stays in Parquet.

SQLAlchemy Core (not the ORM) describes the table and builds the INSERT; psycopg
executes it. `ON CONFLICT DO NOTHING` on the primary key is what makes a re-run of
collect safe: rows already present are skipped, and the function reports how many
were new.

`corpus` says which corpus a row belongs to (`elusion.corpora`): `edgar` for a 10-K
paragraph, `openalex` for a paper abstract. It was added after 1.6 million 10-K rows
existed. `create_all` never alters a table, so `ensure_schema` adds the column itself,
with `edgar` as its default: every older row is a 10-K paragraph. Postgres stores a
constant default in the catalogue instead of rewriting the rows, so the change is
instant at any table size. `cik`, the SEC's id for a filer, stopped being required at
the same time, because an abstract has no filer.
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
    sa.Column("cik", sa.String(10), index=True),  # null outside the edgar corpus
    sa.Column("company", sa.Text),
    sa.Column("year", sa.SmallInteger, nullable=False, index=True),
    sa.Column("paragraph_index", sa.Integer, nullable=False),
    sa.Column("word_count", sa.Integer, nullable=False),
    sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    sa.Column("corpus", sa.Text, nullable=False, server_default="edgar"),
)


def make_engine(database_url: str) -> Engine:
    # SQLAlchemy's default Postgres driver is psycopg2; name psycopg (v3) explicitly.
    url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return sa.create_engine(url)


def ensure_schema(engine: Engine) -> None:
    metadata.create_all(engine)
    with engine.begin() as conn:  # for a documents table created before the second corpus
        conn.execute(
            sa.text(
                "ALTER TABLE documents ADD COLUMN IF NOT EXISTS corpus text NOT NULL "
                "DEFAULT 'edgar'"
            )
        )
        conn.execute(sa.text("ALTER TABLE documents ALTER COLUMN cik DROP NOT NULL"))


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


def count_documents(engine: Engine, year: int | None = None, corpus: str | None = None) -> int:
    """Rows of `documents`, narrowed to a year, to a corpus (its name), or to both."""
    stmt = sa.select(sa.func.count()).select_from(documents)
    if year is not None:
        stmt = stmt.where(documents.c.year == year)
    if corpus is not None:
        stmt = stmt.where(documents.c.corpus == corpus)
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
