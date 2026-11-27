"""The three arms against a throwaway Postgres that has pgvector (the `index_engine`).

The fusion (`rrf`) is a pure function and is tested with no database at all.
"""

import pytest

from elusion.ask import index, retrieve
from elusion.ask.models import EMBEDDING_DIM
from elusion.board.batch import Paper
from tests.fakes import FakeEmbedder

PAIR = {"review_model": "gpt-6-luna", "prompt_version": "auditable-decisions/v1", "threshold": 3}
MODEL = "fake-embedder"


def paper(doc_id: str, text: str, title: str = "A title") -> Paper:
    return Paper(doc_id, f"W{doc_id}", title, "2024-03-01", text)


def add(engine, *papers: Paper) -> None:
    index.add_abstracts(engine, papers, **PAIR)


def embed_all(engine, model: str = MODEL) -> FakeEmbedder:
    """Give every row a FakeEmbedder vector of its title and text."""
    fake = FakeEmbedder(model=model)
    todo = index.unembedded(engine, model)
    index.add_vectors(engine, model, {doc_id: fake.vector(text) for doc_id, text in todo})
    return fake


def axis(x: float, y: float = 0.0) -> list[float]:
    """A vector in the first two dimensions, so a cosine order can be worked out by hand."""
    return [x, y] + [0.0] * (EMBEDDING_DIM - 2)


def ids(hits) -> list[str]:
    return [h.doc_id for h in hits]


def test_k_is_five() -> None:
    assert retrieve.K == 5
    assert retrieve.ARMS == ("lexical", "vector", "hybrid")
    assert retrieve.DEFAULT_ARM == "hybrid"


# lexical


def test_lexical_finds_a_word(index_engine) -> None:
    add(
        index_engine,
        paper("a", "Notes on audit logs for automated decisions."),
        paper("b", "A study of soil chemistry."),
    )
    hits = retrieve.lexical(index_engine, "audit")
    assert ids(hits) == ["a"]
    assert hits[0].rank == 1
    assert hits[0].score > 0


def test_lexical_stems(index_engine) -> None:
    add(
        index_engine,
        paper("a", "The audit of a model."),
        paper("b", "Soil chemistry."),
    )
    assert ids(retrieve.lexical(index_engine, "auditing")) == ["a"]


def test_lexical_matches_when_one_question_word_is_absent(index_engine) -> None:
    add(index_engine, paper("a", "The audit trail of a model."), paper("b", "Soil chemistry."))
    # "zebra" is in no abstract; with AND this would match nothing.
    assert ids(retrieve.lexical(index_engine, "audit trail zebra")) == ["a"]


def test_lexical_returns_fewer_than_k_when_few_match(index_engine) -> None:
    add(index_engine, *[paper(f"d{i}", "Soil chemistry.") for i in range(8)])
    add(index_engine, paper("hit1", "An audit."), paper("hit2", "Another audit."))
    hits = retrieve.lexical(index_engine, "audit")
    assert len(hits) == 2
    assert [h.rank for h in hits] == [1, 2]
    assert len(retrieve.lexical(index_engine, "soil", k=5)) == 5


def test_lexical_all_stop_words_returns_nothing(index_engine) -> None:
    add(index_engine, paper("a", "The audit of a model."))
    assert retrieve.lexical(index_engine, "what is the of a") == []
    assert retrieve.lexical(index_engine, "") == []
    assert retrieve.lexical(index_engine, "zebra") == []


def test_lexical_odd_punctuation_does_not_error(index_engine) -> None:
    add(index_engine, paper("a", "An audit trail for AT&T."), paper("b", "Soil chemistry."))
    assert ids(retrieve.lexical(index_engine, "What is AT&T's 'audit' (trail)?")) == ["a"]
    finds_a = [
        "audit:*",
        "!audit",
        "x <-> y audit",
        "a | b & c audit",
        "(audit",
        "audit\\",
        "'audit'",
    ]
    for odd in finds_a:
        assert ids(retrieve.lexical(index_engine, odd)) == ["a"], odd
    for odd in ["!!! ???", "a | b & c", "'", "\\", "(", "x <-> y"]:
        assert retrieve.lexical(index_engine, odd) == [], odd


@pytest.mark.parametrize(
    ("question", "text"),
    [
        # The old query stemmed these twice and then failed to match: 'decision' -> 'decis'
        # -> 'deci', 'analyses' -> 'analys' -> 'anali'. (The english stemmer keeps 'analysis'
        # -> 'analysi' apart from 'analyses' -> 'analys'; no query change can join those.)
        ("decisions", "A paper about a decision."),
        ("analyses", "Several analyses of logs."),
        ("decision-making", "Notes on decision making."),
        ("decision-making", "Notes on decision-making."),
        ("state-of-the-art audit", "An audit of the state of the art."),
        ("state-of-the-art audit", "An audit."),
        ("AT&T's audit", "An audit trail."),
        ("AT&T's audit", "AT&T publishes an audit."),
    ],
)
def test_lexical_stems_once_not_twice(index_engine, question, text) -> None:
    add(index_engine, paper("a", text), paper("b", "Soil chemistry."))
    assert ids(retrieve.lexical(index_engine, question)) == ["a"]


def test_lexical_url_with_an_ampersand_still_matches_on_its_other_words(index_engine) -> None:
    add(index_engine, paper("a", "An audit trail."), paper("b", "Soil chemistry."))
    question = "See https://example.org/page?x=1&y=2 for the audit"
    assert ids(retrieve.lexical(index_engine, question)) == ["a"]


def test_lexical_question_with_a_nul_character_raises_value_error(index_engine) -> None:
    with pytest.raises(ValueError, match="NUL"):
        retrieve.lexical(index_engine, "audit\x00trail")


def test_lexical_order_is_by_score_then_doc_id(index_engine) -> None:
    add(
        index_engine,
        paper("c", "Audit audit audit of decisions.", title="Audit"),
        paper("b", "A note on audit."),
        paper("a", "A note on audit."),
    )
    hits = retrieve.lexical(index_engine, "audit")
    # "c" mentions the word most, so it scores highest; "a" and "b" tie and go by id.
    assert ids(hits) == ["c", "a", "b"]
    assert hits[1].score == hits[2].score
    assert hits[0].score > hits[1].score
    assert [h.rank for h in hits] == [1, 2, 3]


# vector


def test_vector_orders_by_cosine(index_engine) -> None:
    add(index_engine, paper("a", "x"), paper("b", "x"), paper("c", "x"), paper("d", "x"))
    index.add_vectors(
        index_engine,
        MODEL,
        {"a": axis(1, 0), "b": axis(1, 1), "c": axis(0, 1), "d": axis(-1, 0)},
    )
    hits = retrieve.vector(index_engine, axis(1, 0.2), MODEL)
    assert ids(hits) == ["a", "b", "c", "d"]
    assert [h.rank for h in hits] == [1, 2, 3, 4]


def test_vector_scores_are_cosine_similarity(index_engine) -> None:
    add(index_engine, paper("a", "x"), paper("b", "x"), paper("c", "x"))
    index.add_vectors(index_engine, MODEL, {"a": axis(2, 0), "b": axis(1, 1), "c": axis(0, 3)})
    hits = {h.doc_id: h.score for h in retrieve.vector(index_engine, axis(5, 0), MODEL)}
    assert hits["a"] == pytest.approx(1.0)
    assert hits["b"] == pytest.approx(0.5**0.5)
    assert hits["c"] == pytest.approx(0.0, abs=1e-9)


def test_vector_respects_k(index_engine) -> None:
    add(index_engine, *[paper(f"d{i}", "x") for i in range(7)])
    index.add_vectors(index_engine, MODEL, {f"d{i}": axis(1, i) for i in range(7)})
    assert len(retrieve.vector(index_engine, axis(1, 0), MODEL)) == 5
    assert len(retrieve.vector(index_engine, axis(1, 0), MODEL, k=2)) == 2


def test_vector_ignores_other_models_vectors(index_engine) -> None:
    add(index_engine, paper("a", "x"), paper("b", "x"))
    index.add_vectors(index_engine, MODEL, {"a": axis(0, 1)})
    index.add_vectors(index_engine, "other-model", {"b": axis(1, 0)})
    # "b" is the exact match, but its vector belongs to another model.
    assert ids(retrieve.vector(index_engine, axis(1, 0), MODEL)) == ["a"]
    assert ids(retrieve.vector(index_engine, axis(1, 0), "other-model")) == ["b"]
    assert retrieve.vector(index_engine, axis(1, 0), "no-such-model") == []


def test_vector_wrong_dimension_raises(index_engine) -> None:
    with pytest.raises(ValueError, match=rf"3.*{EMBEDDING_DIM}"):
        retrieve.vector(index_engine, [1.0, 0.0, 0.0], MODEL)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_vector_non_finite_number_raises_before_any_query(bad) -> None:
    # `engine=None` proves it: a query would fail with an AttributeError, not a ValueError.
    query = axis(1, 0)
    query[7] = bad
    with pytest.raises(ValueError, match="finite"):
        retrieve.vector(None, query, MODEL)


# rrf


def test_rrf_worked_example() -> None:
    hits = retrieve.rrf([["a", "b", "c"], ["b", "a", "d"]])
    assert ids(hits) == ["a", "b", "c", "d"]
    assert [h.rank for h in hits] == [1, 2, 3, 4]
    assert hits[0].score == pytest.approx(1 / 61 + 1 / 62)
    assert hits[1].score == pytest.approx(1 / 61 + 1 / 62)
    assert hits[2].score == pytest.approx(1 / 63)
    assert hits[3].score == pytest.approx(1 / 63)


def test_rrf_a_document_in_both_lists_beats_one_at_rank_one_in_one() -> None:
    hits = retrieve.rrf([["x", "y"], ["z", "y"]])
    assert ids(hits)[0] == "y"
    assert hits[0].score == pytest.approx(2 / 62)
    assert ids(hits)[1:] == ["x", "z"]


def test_rrf_ties_break_by_doc_id() -> None:
    assert ids(retrieve.rrf([["m", "z"], ["a", "q"]])) == ["a", "m", "q", "z"]


def test_rrf_takes_the_top_k_and_handles_empty_lists() -> None:
    assert len(retrieve.rrf([["a", "b", "c", "d", "e", "f", "g"]])) == 5
    assert ids(retrieve.rrf([["a", "b", "c"]], k=2)) == ["a", "b"]
    assert retrieve.rrf([[], []]) == []
    assert ids(retrieve.rrf([[], ["a"]])) == ["a"]


# hybrid


def test_hybrid_returns_a_doc_only_lexical_finds_and_one_only_vector_finds(index_engine) -> None:
    add(
        index_engine,
        paper("lex", "Zebra stripes audit."),
        paper("vec", "Soil chemistry."),
        paper("none", "Soil chemistry again."),
    )
    # Only "vec" is near the question's vector; only "lex" holds the question's word.
    index.add_vectors(
        index_engine,
        MODEL,
        {"lex": axis(0, 1), "vec": axis(1, 0), "none": axis(-1, 0)},
    )
    # depth=1 reads one hit from each arm: the vector arm alone would give only "vec" and
    # the lexical arm alone only "lex", so both are here only if both arms ran. Each is
    # rank 1 in its own list, so they tie at 1/61 and the id order decides.
    hits = retrieve.hybrid(index_engine, "zebra", axis(1, 0), MODEL, depth=1)
    assert ids(hits) == ["lex", "vec"]
    assert [h.rank for h in hits] == [1, 2]
    assert [h.score for h in hits] == [pytest.approx(1 / 61), pytest.approx(1 / 61)]
    assert retrieve.vector(index_engine, axis(1, 0), MODEL, k=1)[0].doc_id == "vec"


def test_hybrid_is_deterministic_and_ties_go_by_doc_id(index_engine) -> None:
    # Twelve distinct documents with identical text and identical vectors: every score in
    # both arms is exactly equal, so only the id order can decide.
    add(index_engine, *[paper(f"d{i:02}", "Audit of decisions in a system.") for i in range(12)])
    index.add_vectors(index_engine, MODEL, {f"d{i:02}": axis(1, 0) for i in range(12)})
    lex = retrieve.lexical(index_engine, "audit of decisions", 12)
    assert len({h.score for h in lex}) == 1
    assert ids(lex) == [f"d{i:02}" for i in range(12)]
    first = retrieve.hybrid(index_engine, "audit of decisions", axis(1, 0), MODEL)
    second = retrieve.hybrid(index_engine, "audit of decisions", axis(1, 0), MODEL)
    assert first == second
    assert ids(first) == ["d00", "d01", "d02", "d03", "d04"]


def test_hybrid_on_an_empty_index_returns_nothing(index_engine) -> None:
    assert retrieve.hybrid(index_engine, "audit", axis(1, 0), MODEL) == []


# retrieve


def test_retrieve_dispatches(index_engine) -> None:
    fake = FakeEmbedder(model=MODEL)
    add(
        index_engine,
        paper("a", "Audit logs for automated decisions."),
        paper("b", "Soil chemistry."),
    )
    embed_all(index_engine)
    q = "audit logs"
    qv = fake.vector(q)
    assert retrieve.retrieve(index_engine, "lexical", q, None, None) == retrieve.lexical(
        index_engine, q
    )
    assert retrieve.retrieve(index_engine, "vector", q, qv, MODEL) == retrieve.vector(
        index_engine, qv, MODEL
    )
    assert retrieve.retrieve(index_engine, "hybrid", q, qv, MODEL) == retrieve.hybrid(
        index_engine, q, qv, MODEL
    )
    assert len(retrieve.retrieve(index_engine, "vector", q, qv, MODEL, k=1)) == 1


@pytest.mark.parametrize("arm", ["vector", "hybrid"])
def test_retrieve_vector_without_a_vector_raises(index_engine, arm) -> None:
    with pytest.raises(ValueError, match="vector"):
        retrieve.retrieve(index_engine, arm, "q", None, MODEL)
    with pytest.raises(ValueError, match="embedding model"):
        retrieve.retrieve(index_engine, arm, "q", axis(1), None)


def test_retrieve_unknown_arm_raises(index_engine) -> None:
    with pytest.raises(ValueError, match="lexical.*vector.*hybrid"):
        retrieve.retrieve(index_engine, "semantic", "q", None, None)


# passages


def test_passages_returns_title_and_text(index_engine) -> None:
    add(
        index_engine,
        paper("a", "Text of a.", title="Title of a"),
        paper("b", "Text of b.", title="Title of b"),
        paper("c", "Text of c.", title="Title of c"),
    )
    got = retrieve.passages(index_engine, ["c", "a", "missing"])
    assert got == {
        "a": retrieve.Passage("a", "Title of a", "Text of a."),
        "c": retrieve.Passage("c", "Title of c", "Text of c."),
    }
    assert retrieve.passages(index_engine, []) == {}
