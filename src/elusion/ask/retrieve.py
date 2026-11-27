"""The three arms: three ways to find the abstracts that answer a question.

An *arm* is one way of searching the index. `lexical` is Postgres full-text search: it
finds the question's words (stemmed, so "auditing" finds "audit") and misses paraphrase.
`vector` compares the question's embedding with every stored one by cosine similarity (1
for the same direction, 0 for unrelated): it finds meaning and can miss an exact term.
`hybrid` runs both and merges the two ranked lists by *reciprocal rank fusion*: a
document's score is the sum, over the lists, of `1 / (60 + its rank in that list)`, so no
score from one list is ever compared with a score from the other.

Nothing here makes a model call: the caller embeds the question and passes the vector in.
Nothing here filters on anything but the embedding model: the search sees every indexed
abstract, and which abstracts are indexed was decided when the index was built.

`K = 5` is how many abstracts a search returns and how many `ask` reads. It was fixed
before any gold question existed as a file, and `ask-eval` has no flag to change it. Five
whole abstracts are about 2,000 to 2,500 tokens: few enough that the owner can read every
retrieved abstract by hand when an answer looks wrong, cheap enough for 90 eval asks, and a
strict test, since a right abstract in the top 5 of about 10,000 is real evidence that the
arm found it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from elusion.ask.index import abstract_index
from elusion.ask.models import EMBEDDING_DIM

K = 5
RRF_C = 60
# How deep each arm is read before fusing: deep enough that a document ranked 20th by one
# arm and 3rd by the other still fuses, shallow enough to stay cheap.
FUSION_DEPTH = 50
ARMS = ("lexical", "vector", "hybrid")
DEFAULT_ARM = "hybrid"


@dataclass(frozen=True)
class Hit:
    doc_id: str
    rank: int  # 1 is best
    score: float


@dataclass(frozen=True)
class Passage:
    doc_id: str
    title: str
    text: str


# The question's words are joined with OR, not AND: a natural-language question has words
# the right abstract lacks, and `plainto_tsquery` (AND) would then match nothing. It also
# strips operators and odd punctuation, so any string is safe; `replace` turns its `&`
# into `|` in the query's text form.
_LEXICAL_SQL = sa.text(
    "WITH q AS (SELECT to_tsquery('english', replace(plainto_tsquery('english', :question)::text,"
    " '&', '|')) AS query) "
    "SELECT doc_id, ts_rank_cd(tsv, q.query) AS score FROM abstract_index, q "
    "WHERE tsv @@ q.query ORDER BY score DESC, doc_id LIMIT :k"
)

# `<=>` is pgvector's cosine distance; 1 minus it is the cosine similarity.
_VECTOR_SQL = sa.text(
    "SELECT doc_id, 1 - (embedding <=> CAST(:q AS vector)) AS score FROM abstract_vectors "
    "WHERE embedding_model = :m ORDER BY embedding <=> CAST(:q AS vector), doc_id LIMIT :k"
)


def _hits(rows) -> list[Hit]:
    return [Hit(doc_id, rank, float(score)) for rank, (doc_id, score) in enumerate(rows, start=1)]


def lexical(engine: Engine, question: str, k: int = K) -> list[Hit]:
    """The `k` best full-text matches, best first. Fewer than `k` when fewer match; none when
    no word of the question matches or the question is all stop words."""
    with engine.connect() as conn:
        rows = conn.execute(_LEXICAL_SQL, {"question": question, "k": k}).all()
    return _hits(rows)


def vector(
    engine: Engine, query_vector: Sequence[float], embedding_model: str, k: int = K
) -> list[Hit]:
    """The `k` nearest abstracts by cosine similarity among the vectors of `embedding_model`
    (those of another model are invisible), best first. A vector of the wrong length raises
    `ValueError` before any query."""
    if len(query_vector) != EMBEDDING_DIM:
        raise ValueError(
            f"query vector has {len(query_vector)} numbers; the index holds {EMBEDDING_DIM}"
        )
    literal = "[" + ",".join(repr(float(x)) for x in query_vector) + "]"
    with engine.connect() as conn:
        rows = conn.execute(_VECTOR_SQL, {"q": literal, "m": embedding_model, "k": k}).all()
    return _hits(rows)


def rrf(rankings: Sequence[Sequence[str]], k: int = K, c: int = RRF_C) -> list[Hit]:
    """Fuse ranked id lists (best first) into one. Ranks count from 1 within each list, a
    document absent from a list adds nothing for it, and its score is the sum of
    `1 / (c + rank)`. Ties go by `doc_id`. A pure function: no database."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (c + rank)
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:k]
    return _hits(ordered)


def hybrid(
    engine: Engine,
    question: str,
    query_vector: Sequence[float],
    embedding_model: str,
    k: int = K,
    depth: int = FUSION_DEPTH,
) -> list[Hit]:
    """Reciprocal rank fusion of the top `depth` of `lexical` and of `vector`."""
    words = [h.doc_id for h in lexical(engine, question, depth)]
    meaning = [h.doc_id for h in vector(engine, query_vector, embedding_model, depth)]
    return rrf([words, meaning], k=k)


def retrieve(
    engine: Engine,
    arm: str,
    question: str,
    query_vector: Sequence[float] | None,
    embedding_model: str | None,
    k: int = K,
) -> list[Hit]:
    """Search with the named arm. `vector` and `hybrid` need the question's vector and the
    model that made it; without them, or with an arm not in `ARMS`, `ValueError`."""
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}; the arms are {', '.join(ARMS)}")
    if arm == "lexical":
        return lexical(engine, question, k)
    if query_vector is None or embedding_model is None:
        raise ValueError(f"the {arm} arm needs the question's vector and its embedding model")
    if arm == "vector":
        return vector(engine, query_vector, embedding_model, k)
    return hybrid(engine, question, query_vector, embedding_model, k)


def passages(engine: Engine, doc_ids: Sequence[str]) -> dict[str, Passage]:
    """Title and text of the indexed abstracts among `doc_ids`, keyed by id. An id that is
    not indexed is absent from the result."""
    if not doc_ids:
        return {}
    c = abstract_index.c
    stmt = sa.select(c.doc_id, c.title, c.text).where(c.doc_id.in_(list(doc_ids)))
    with engine.connect() as conn:
        return {row.doc_id: Passage(row.doc_id, row.title, row.text) for row in conn.execute(stmt)}
