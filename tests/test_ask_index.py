"""The index tables against a throwaway Postgres that has pgvector (the `index_engine`)."""

import pytest
import sqlalchemy as sa

from elusion.ask import index
from elusion.ask.models import EMBEDDING_DIM
from elusion.board.batch import Paper
from elusion.review import db as review_db

PAIR = {"review_model": "gpt-6-luna", "prompt_version": "auditable-decisions/v1", "threshold": 3}


def paper(doc_id: str, text: str = "An abstract.", title: str = "A title") -> Paper:
    return Paper(doc_id, f"W{doc_id}", title, "2024-03-01", text)


def add(engine, *papers: Paper, **kw) -> int:
    return index.add_abstracts(engine, papers, **{**PAIR, **kw})


def vec(first: float) -> list[float]:
    return [first] + [0.0] * (EMBEDDING_DIM - 1)


def test_ensure_schema_creates_the_extension(index_engine) -> None:
    with index_engine.connect() as conn:
        name = conn.execute(sa.text("SELECT extname FROM pg_extension WHERE extname='vector'"))
        assert name.scalar_one() == "vector"


def test_ensure_schema_twice(index_engine) -> None:
    index.ensure_schema(index_engine)
    index.ensure_schema(index_engine)


def test_vector_round_trip(index_engine) -> None:
    add(index_engine, paper("a"))
    sent = [0.1, -0.25, 1e-07, 3.0] + [0.0] * (EMBEDDING_DIM - 4)
    assert index.add_vectors(index_engine, "m", {"a": sent}) == 1
    with index_engine.connect() as conn:
        got = conn.execute(sa.select(index.abstract_vectors.c.embedding)).scalar_one()
    assert isinstance(got, list)
    # pgvector stores float32: the numbers come back to about seven digits.
    assert got == pytest.approx(sent, rel=1e-6, abs=1e-9)


def test_add_vectors_refuses_a_wrong_length_and_inserts_nothing(index_engine) -> None:
    add(index_engine, paper("a"), paper("b"))
    with pytest.raises(ValueError, match=rf"b.*5.*{EMBEDDING_DIM}"):
        index.add_vectors(index_engine, "m", {"a": vec(1.0), "b": [0.0] * 5})
    assert index.count_vectors(index_engine, "m") == 0


def test_add_abstracts_is_idempotent(index_engine) -> None:
    assert add(index_engine, paper("a"), paper("b")) == 2
    assert add(index_engine, paper("a"), paper("b"), paper("c")) == 1
    assert index.count_indexed(index_engine) == 3
    assert add(index_engine) == 0


def test_tsv_is_built_from_title_and_text(index_engine) -> None:
    add(index_engine, paper("a", text="Reviewers read documents.", title="Zebrafish"))
    query = "SELECT doc_id FROM abstract_index WHERE tsv @@ plainto_tsquery('english', :q)"
    with index_engine.connect() as conn:
        assert conn.execute(sa.text(query), {"q": "zebrafish"}).scalars().all() == ["a"]
        assert conn.execute(sa.text(query), {"q": "document"}).scalars().all() == ["a"]
        assert conn.execute(sa.text(query), {"q": "absent"}).scalars().all() == []
        names = conn.execute(
            sa.text("SELECT indexname FROM pg_indexes WHERE tablename='abstract_index'")
        ).scalars()
        assert "ix_abstract_index_tsv" in set(names)


def test_no_index_on_the_embedding_column(index_engine) -> None:
    with index_engine.connect() as conn:
        rows = conn.execute(
            sa.text("SELECT indexname FROM pg_indexes WHERE tablename='abstract_vectors'")
        ).scalars()
        assert list(rows) == ["abstract_vectors_pkey"]


def test_unembedded_lists_rows_without_a_vector_for_that_model(index_engine) -> None:
    add(index_engine, paper("b", "Text b.", "Title b"), paper("a", "Text a.", "Title a"))
    assert index.unembedded(index_engine, "m") == [
        ("a", "Title a\n\nText a."),
        ("b", "Title b\n\nText b."),
    ]
    index.add_vectors(index_engine, "m", {"a": vec(1.0)})
    assert [d for d, _ in index.unembedded(index_engine, "m")] == ["b"]
    assert [d for d, _ in index.unembedded(index_engine, "other")] == ["a", "b"]


def test_vectors_are_kept_per_model(index_engine) -> None:
    add(index_engine, paper("a"))
    assert index.add_vectors(index_engine, "m1", {"a": vec(1.0)}) == 1
    assert index.add_vectors(index_engine, "m2", {"a": vec(0.5)}) == 1
    assert index.add_vectors(index_engine, "m1", {"a": vec(0.9)}) == 0  # key present: kept as is
    assert index.count_vectors(index_engine, "m1") == 1
    assert index.count_vectors(index_engine, "m2") == 1
    with index_engine.connect() as conn:
        first = conn.execute(
            sa.select(index.abstract_vectors.c.embedding).where(
                index.abstract_vectors.c.embedding_model == "m1"
            )
        ).scalar_one()
    assert first[0] == pytest.approx(1.0)


def test_drop_abstracts_removes_their_vectors(index_engine) -> None:
    add(index_engine, paper("a"), paper("b"))
    index.add_vectors(index_engine, "m", {"a": vec(1.0), "b": vec(1.0)})
    assert index.drop_abstracts(index_engine, ["a", "zz"]) == 1
    assert index.count_indexed(index_engine) == 1
    assert index.count_vectors(index_engine, "m") == 1
    assert index.drop_abstracts(index_engine, []) == 0


def test_document_text_is_capped() -> None:
    assert index.document_text("T", "body") == "T\n\nbody"
    long = index.document_text("T", "x" * 50_000)
    assert len(long) == index.EMBED_MAX_CHARS == 20_000
    assert long.startswith("T\n\nxx")


def test_indexed_pair(index_engine) -> None:
    assert index.indexed_pair(index_engine) is None
    add(index_engine, paper("a"), paper("b"))
    assert index.indexed_pair(index_engine) == ("gpt-6-luna", "auditable-decisions/v1", 3)
    add(index_engine, paper("c"), review_model="other")
    with pytest.raises(index.IndexPairMismatch):
        index.indexed_pair(index_engine)


def test_screened_in_ids_reads_scores_of_the_pair_only(index_engine) -> None:
    def row(doc_id, model, version, score):
        return {
            "doc_id": doc_id,
            "model": model,
            "prompt_version": version,
            "score": score,
            "rationale": "r",
            "quote": "q",
            "input_tokens": 1,
            "output_tokens": 1,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "cost_usd": 0.0,
            "provider": "fake",
        }

    review_db.insert_scores(
        index_engine,
        [
            row("c", "gpt-6-luna", "auditable-decisions/v1", 4),
            row("a", "gpt-6-luna", "auditable-decisions/v1", 3),
            row("b", "gpt-6-luna", "auditable-decisions/v1", 2),
            row("d", "other-model", "auditable-decisions/v1", 4),
            row("e", "gpt-6-luna", "auditable-decisions/v2", 4),
        ],
    )
    assert index.screened_in_ids(index_engine, "gpt-6-luna", "auditable-decisions/v1") == ["a", "c"]
    assert index.screened_in_ids(index_engine, "gpt-6-luna", "auditable-decisions/v1", 4) == ["c"]


def test_the_served_model_is_stored_beside_the_pinned_one(index_engine) -> None:
    add(index_engine, paper("a"), paper("b"))
    index.add_vectors(index_engine, "m", {"a": vec(1.0)}, served_model="m-2026-11-01")
    index.add_vectors(index_engine, "m", {"b": vec(1.0)})  # no served model given: the pinned id
    with index_engine.connect() as conn:
        rows = conn.execute(
            sa.select(
                index.abstract_vectors.c.doc_id, index.abstract_vectors.c.served_model
            ).order_by("doc_id")
        ).all()
    assert rows == [("a", "m-2026-11-01"), ("b", "m")]


def test_inserts_cross_the_chunk_size(index_engine, monkeypatch) -> None:
    monkeypatch.setattr(index, "_ROWS_PER_INSERT", 2)
    papers = [paper(d) for d in "abcde"]
    assert add(index_engine, *papers) == 5
    assert add(index_engine, *papers, paper("f")) == 1
    assert index.count_indexed(index_engine) == 6
    assert index.add_vectors(index_engine, "m", {d: vec(1.0) for d in "abcde"}) == 5
    assert index.add_vectors(index_engine, "m", {d: vec(1.0) for d in "abcdef"}) == 1
    assert index.count_vectors(index_engine, "m") == 6
    assert index.indexed_ids(index_engine) == set("abcdef")
