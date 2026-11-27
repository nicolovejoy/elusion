"""The index: the screened-in abstracts, searchable by words and by meaning.

*Screened-in* means flagged by the review of one (model, prompt version) pair at or above a
threshold: the abstracts a model scored 3 or 4 against the criterion. `ask` searches only
those, so a question is answered from the pile that already passed the review.

Two tables, kept in their own MetaData and made by `ensure_schema`:

`abstract_index` holds one row per abstract: its text and a `tsvector`, which is Postgres's
form of the text reduced to normalised words (stemmed, stop words dropped). A full-text
query matches exact words and misses paraphrase. The column is computed by Postgres from
title and text, so the two can never disagree, and a GIN index on it answers a query
exactly (an index that approximates, which this phase builds none of, can miss).

`abstract_vectors` holds one embedding per (abstract, embedding model), in a `vector`
column from the pgvector extension. There is no index on it: a search compares the
question's vector with every stored one (an exact scan), about ten thousand comparisons,
which is fast and never misses. A vector is kept per model, so a new embedding model adds
rows beside the old ones.

The rule that defines the set lives in `screened_in_ids` alone: scores and a threshold. The
gold set is taken out by the flow (`held_back_ids`), and no label is ever read here.

pgvector's own Python package is not used: a `vector` is sent and read as text such as
`[0.1,0.2]`, and the small type below does that.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR, insert
from sqlalchemy.engine import Engine

from elusion.ask.models import EMBEDDING_DIM
from elusion.board.batch import Paper
from elusion.review.db import scores_for

# The longest text that is embedded, in characters: at about three characters a token it
# stays under the model's input limit. The `tsvector` uses the whole text.
EMBED_MAX_CHARS = 20_000
_ROWS_PER_INSERT = 500  # a statement may carry at most 65,535 parameters


class IndexPairMismatch(ValueError):
    """The index was built from another (review model, prompt version, threshold)."""


def vector_literal(values: Sequence[float]) -> str:
    """pgvector's text form of a vector, `[0.1,0.2]`; the one place it is written."""
    return "[" + ",".join(repr(float(x)) for x in values) + "]"


class Vector(sa.types.UserDefinedType):
    """pgvector's `vector(dim)`, sent and read as text: a list of floats <-> `[0.1,0.2]`."""

    cache_ok = True

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def get_col_spec(self, **kw) -> str:
        return f"vector({self.dim})"

    def bind_processor(self, dialect):
        def process(value):
            if value is None:
                return None
            return vector_literal(value)

        return process

    def bind_expression(self, bindvalue):
        return sa.cast(bindvalue, self)

    def result_processor(self, dialect, coltype):
        def process(value):
            if value is None:
                return None
            return [float(x) for x in value.strip("[]").split(",") if x]

        return process


metadata = sa.MetaData()

abstract_index = sa.Table(
    "abstract_index",
    metadata,
    sa.Column("doc_id", sa.String(16), primary_key=True),
    sa.Column("work_id", sa.Text, nullable=False),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("publication_date", sa.Text, nullable=False),
    sa.Column("text", sa.Text, nullable=False),
    sa.Column(
        "tsv",
        TSVECTOR,
        sa.Computed("to_tsvector('english', title || ' ' || text)", persisted=True),
    ),
    # The pair and threshold that screened it in.
    sa.Column("review_model", sa.Text, nullable=False),
    sa.Column("review_prompt_version", sa.Text, nullable=False),
    sa.Column("threshold", sa.SmallInteger, nullable=False),
    sa.Column(
        "indexed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Index("ix_abstract_index_tsv", "tsv", postgresql_using="gin"),
)

abstract_vectors = sa.Table(
    "abstract_vectors",
    metadata,
    sa.Column(
        "doc_id",
        sa.String(16),
        sa.ForeignKey("abstract_index.doc_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("embedding_model", sa.Text, primary_key=True),
    # The model id the API answered with for the batch, stored beside the pinned one.
    sa.Column("served_model", sa.Text, nullable=False),
    sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
    sa.Column(
        "embedded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
)


def ensure_schema(engine: Engine) -> None:
    """Make the pgvector extension and the two tables. Safe to repeat."""
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
    metadata.create_all(engine)


def document_text(title: str, text: str) -> str:
    """The text that is embedded: the title, a blank line, the abstract, cut to
    `EMBED_MAX_CHARS`. The title comes first because it carries the topic in few words."""
    return f"{title}\n\n{text}"[:EMBED_MAX_CHARS]


def screened_in_ids(
    engine: Engine, review_model: str, prompt_version: str, threshold: int = 3
) -> list[str]:
    """The ids the review of the pair scored at or above `threshold`, sorted.

    `prompt_version` is the full key of the scores table, e.g. `auditable-decisions/v1`.
    This is the only place the set is defined. It reads no judge label and no gold file.
    """
    found = scores_for(engine, review_model, prompt_version)
    return sorted(doc_id for doc_id, score in found.items() if score >= threshold)


def indexed_pair(engine: Engine) -> tuple[str, str, int] | None:
    """The one (review model, prompt version, threshold) the index was built from; None
    when it is empty. Raises `IndexPairMismatch` when rows come from different ones."""
    c = abstract_index.c
    stmt = sa.select(c.review_model, c.review_prompt_version, c.threshold).distinct()
    with engine.connect() as conn:
        pairs = [tuple(row) for row in conn.execute(stmt)]
    if not pairs:
        return None
    if len(pairs) > 1:
        raise IndexPairMismatch(f"the index holds rows from {len(pairs)} pairs: {sorted(pairs)}")
    return pairs[0]


def add_abstracts(
    engine: Engine,
    papers: Iterable[Paper],
    *,
    review_model: str,
    prompt_version: str,
    threshold: int,
) -> int:
    """Index the papers not indexed yet (`ON CONFLICT DO NOTHING` on `doc_id`). Returns the
    number of rows inserted."""
    rows = [
        {
            "doc_id": p.doc_id,
            "work_id": p.work_id,
            "title": p.title or "",
            "publication_date": p.publication_date,
            "text": p.text,
            "review_model": review_model,
            "review_prompt_version": prompt_version,
            "threshold": threshold,
        }
        for p in papers
    ]
    added = 0
    with engine.begin() as conn:
        for start in range(0, len(rows), _ROWS_PER_INSERT):
            stmt = (
                insert(abstract_index)
                .values(rows[start : start + _ROWS_PER_INSERT])
                .on_conflict_do_nothing(index_elements=["doc_id"])
                .returning(abstract_index.c.doc_id)
            )
            added += len(conn.execute(stmt).all())
    return added


def drop_abstracts(engine: Engine, doc_ids: Iterable[str]) -> int:
    """Remove the rows (and, by cascade, their vectors). Returns the rows removed."""
    ids = list(doc_ids)
    if not ids:
        return 0
    stmt = (
        sa.delete(abstract_index)
        .where(abstract_index.c.doc_id == sa.any_(sa.bindparam("ids", ids, type_=ARRAY(sa.String))))
        .returning(abstract_index.c.doc_id)
    )
    with engine.begin() as conn:
        return len(conn.execute(stmt).all())


def unembedded(engine: Engine, embedding_model: str) -> list[tuple[str, str]]:
    """(doc_id, text to embed) of every row with no vector for `embedding_model`, by doc_id."""
    has_vector = (
        sa.select(sa.literal(1))
        .where(
            abstract_vectors.c.doc_id == abstract_index.c.doc_id,
            abstract_vectors.c.embedding_model == embedding_model,
        )
        .exists()
    )
    stmt = (
        sa.select(abstract_index.c.doc_id, abstract_index.c.title, abstract_index.c.text)
        .where(~has_vector)
        .order_by(abstract_index.c.doc_id)
    )
    with engine.connect() as conn:
        return [(doc_id, document_text(title, text)) for doc_id, title, text in conn.execute(stmt)]


def indexed_ids(engine: Engine) -> set[str]:
    """Every `doc_id` in the index."""
    with engine.connect() as conn:
        return set(conn.execute(sa.select(abstract_index.c.doc_id)).scalars())


def add_vectors(
    engine: Engine,
    embedding_model: str,
    vectors: Mapping[str, Sequence[float]],
    *,
    served_model: str | None = None,
) -> int:
    """Store the vectors (`ON CONFLICT DO NOTHING`: a key that has one keeps it). Returns the
    rows inserted. `served_model` is the id the API answered with; it defaults to the pinned
    `embedding_model`. A vector of the wrong length raises `ValueError` before anything is
    inserted."""
    for doc_id, vector in vectors.items():
        if len(vector) != EMBEDDING_DIM:
            raise ValueError(
                f"vector for {doc_id} has {len(vector)} numbers; the column holds {EMBEDDING_DIM}"
            )
    rows = [
        {
            "doc_id": doc_id,
            "embedding_model": embedding_model,
            "served_model": served_model or embedding_model,
            "embedding": list(vector),
        }
        for doc_id, vector in vectors.items()
    ]
    added = 0
    with engine.begin() as conn:
        for start in range(0, len(rows), _ROWS_PER_INSERT):
            stmt = (
                insert(abstract_vectors)
                .values(rows[start : start + _ROWS_PER_INSERT])
                .on_conflict_do_nothing(index_elements=["doc_id", "embedding_model"])
                .returning(abstract_vectors.c.doc_id)
            )
            added += len(conn.execute(stmt).all())
    return added


def count_indexed(engine: Engine) -> int:
    with engine.connect() as conn:
        return conn.execute(sa.select(sa.func.count()).select_from(abstract_index)).scalar_one()


def count_vectors(engine: Engine, embedding_model: str) -> int:
    stmt = (
        sa.select(sa.func.count())
        .select_from(abstract_vectors)
        .where(abstract_vectors.c.embedding_model == embedding_model)
    )
    with engine.connect() as conn:
        return conn.execute(stmt).scalar_one()
