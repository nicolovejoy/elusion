"""`ask`: retrieve, one model call, a Python check of every quote, one audit row.

Everything runs on the throwaway database of `index_engine` with `FakeEmbedder` and
`FakeCompleter`: no network, no paid call.
"""

import logging

import pytest

from elusion.ask import answer, index, log
from elusion.ask.answer import Answer, Claim, ask, ask_message
from elusion.ask.embed import Embedded
from elusion.ask.retrieve import Passage
from elusion.board.batch import Paper
from elusion.review.prompt import load_prompt
from elusion.review.providers import ReviewError
from tests.fakes import FakeCompleter, FakeEmbedder

PAIR = {"review_model": "gpt-6-luna", "prompt_version": "auditable-decisions/v1", "threshold": 3}
TEXT_A = "A stopping rule says when a review ends.\nThe team stops at a fixed recall of 0.75."
TEXT_B = "Audit trails record every decision a reviewer makes."
QUESTION = "when does a review stop?  "  # two trailing spaces, kept exactly


def paper(doc_id: str, text: str, title: str) -> Paper:
    return Paper(doc_id, f"W{doc_id}", title, "2024-03-01", text)


@pytest.fixture
def papers(index_engine):
    """Three indexed abstracts. The question matches `a` (review, stop) and `b` (review) by
    words; `c` shares no word with it, so a lexical search never returns it."""
    index.add_abstracts(
        index_engine,
        [
            paper("a", TEXT_A, "Stopping rules"),
            paper("b", TEXT_B + " A review is logged.", "Audit trails"),
            paper("c", "Soil chemistry of wetlands.", "Soil"),
        ],
        **PAIR,
    )
    return index_engine


def good(doc_id="a", quote="stops at a fixed recall of 0.75") -> Answer:
    return Answer(
        answerable=True,
        claims=[Claim(claim="It ends at a recall target.", doc_id=doc_id, quote=quote)],
    )


def answering(result):
    return FakeCompleter(lambda system, message, out: result, model="claude-sonnet-5-5")


def run(engine, completer, question=QUESTION, **kw):
    kw.setdefault("arm", "lexical")
    return ask(engine, question, completer, None, **kw)


def test_the_model_gets_the_prompt_file_and_exactly_the_template_message(papers) -> None:
    completer = answering(good())
    result = run(papers, completer, "audit trails", k=1)
    assert result.retrieved[0].doc_id == "b"
    system, message = completer.calls[0]
    assert system == load_prompt("ask", "v1").text
    assert message == (
        f"QUESTION:\naudit trails\n\nPASSAGES:\n[b] Audit trails\n{TEXT_B} A review is logged."
    )
    assert completer.max_tokens == [answer.ANSWER_MAX_TOKENS] == [16000]


def test_ask_message_joins_passages_in_rank_order() -> None:
    passages = [Passage("x", "T1", "one"), Passage("y", "T2", "two")]
    assert ask_message("q?", passages) == "QUESTION:\nq?\n\nPASSAGES:\n[x] T1\none\n\n[y] T2\ntwo"


def test_a_good_answer_is_answered_and_every_field_is_logged(papers) -> None:
    completer = answering(good())
    result = run(papers, completer)
    assert result.outcome == "answered"
    assert result.cost_usd == 0.0
    assert [(c.index, c.doc_id, c.retrieved, c.verbatim) for c in result.checks] == [
        (0, "a", True, True)
    ]
    row = log.get_ask(papers, result.ask_id)
    assert row.question == QUESTION  # trailing spaces kept
    assert (row.arm, row.k) == ("lexical", 5)
    assert row.embedding_model is None
    assert [(h["doc_id"], h["rank"]) for h in row.retrieved] == [
        (h.doc_id, h.rank) for h in result.retrieved
    ]
    assert {h["doc_id"] for h in row.retrieved} == {"a", "b"}
    assert all(h["score"] > 0 for h in row.retrieved)
    assert (row.answer_model, row.served_model) == ("claude-sonnet-5-5", "claude-sonnet-5-5")
    assert row.prompt_version == "ask/v1"
    assert row.outcome == "answered"
    assert row.answer == good().model_dump()
    assert row.checks == [{"index": 0, "doc_id": "a", "retrieved": True, "verbatim": True}]
    assert (row.input_tokens, row.output_tokens) == (100, 20)
    assert (row.cache_creation_input_tokens, row.cache_read_input_tokens) == (0, 0)
    assert row.cost_usd == 0
    assert row.stop_reason is None
    assert len(log.asks(papers)) == 1


def test_a_quote_not_in_the_abstract_fails_and_the_model_is_called_once(papers) -> None:
    completer = answering(good(quote="stops at a recall of 0.80"))
    result = run(papers, completer)
    assert result.outcome == "failed"
    assert [(c.retrieved, c.verbatim) for c in result.checks] == [(True, False)]
    assert len(completer.calls) == 1
    row = log.get_ask(papers, result.ask_id)
    assert row.outcome == "failed"
    assert row.answer == good(quote="stops at a recall of 0.80").model_dump()  # stored whole


def test_a_quote_differing_only_in_whitespace_passes(papers) -> None:
    result = run(papers, answering(good(quote="ends.  The team\nstops   at a fixed recall")))
    assert result.outcome == "answered"


def test_a_quote_of_the_title_fails(papers) -> None:
    result = run(papers, answering(good(quote="Stopping rules")))
    assert result.outcome == "failed"
    assert [(c.retrieved, c.verbatim) for c in result.checks] == [(True, False)]


def test_an_empty_quote_fails(papers) -> None:
    assert run(papers, answering(good(quote="  "))).outcome == "failed"


def test_a_cited_id_in_the_index_but_not_retrieved_fails(papers) -> None:
    # `c` is indexed and its text holds the quote, but the lexical search never returned it.
    result = run(papers, answering(good(doc_id="c", quote="Soil chemistry")))
    assert result.outcome == "failed"
    assert [(c.doc_id, c.retrieved, c.verbatim) for c in result.checks] == [("c", False, False)]


def test_a_cited_id_not_in_the_index_fails(papers) -> None:
    result = run(papers, answering(good(doc_id="zzzz")))
    assert result.outcome == "failed"
    assert [(c.retrieved, c.verbatim) for c in result.checks] == [(False, False)]


def test_a_refusal_is_stored_with_no_claims(papers) -> None:
    refusal = Answer(answerable=False, claims=[])
    result = run(papers, answering(refusal))
    assert result.outcome == "refused"
    assert result.checks == []
    row = log.get_ask(papers, result.ask_id)
    assert row.outcome == "refused"
    assert row.answer == {"answerable": False, "claims": []}
    assert row.checks == []
    assert (row.input_tokens, row.output_tokens) == (100, 20)
    assert answer.REFUSAL == "The passages do not answer the question."


def test_answerable_true_with_no_claims_fails(papers) -> None:
    result = run(papers, answering(Answer(answerable=True, claims=[])))
    assert result.outcome == "failed"
    assert log.get_ask(papers, result.ask_id).detail["inconsistent_answer"] is True


def test_answerable_false_with_claims_fails(papers) -> None:
    result = run(papers, answering(Answer(answerable=False, claims=good().claims)))
    assert result.outcome == "failed"
    row = log.get_ask(papers, result.ask_id)
    assert row.detail["inconsistent_answer"] is True
    assert row.answer["claims"] != []  # kept whole, not dropped


def test_a_review_error_stores_an_error_row_with_its_cost(papers) -> None:
    error = ReviewError("max_tokens", "claude-sonnet-5-5", 1000, 16000, 200, 300)
    completer = answering(error)
    result = run(papers, completer)
    assert result.outcome == "error"
    assert result.answer is None and result.checks == []
    assert len(completer.calls) == 1  # not retried
    row = log.get_ask(papers, result.ask_id)
    assert (row.outcome, row.stop_reason, row.answer) == ("error", "max_tokens", None)
    assert row.checks == []
    assert (row.input_tokens, row.output_tokens) == (1000, 16000)
    assert (row.cache_creation_input_tokens, row.cache_read_input_tokens) == (200, 300)
    # claude-sonnet-5-5 at 2.00 in, 10.00 out, 2.50 cache write, 0.10 cache read per million
    expected = (1000 * 2.0 + 16000 * 10.0 + 200 * 2.5 + 300 * 0.1) / 1_000_000
    assert row.cost_usd == pytest.approx(expected)
    assert result.cost_usd == pytest.approx(expected)


def test_no_hits_makes_no_model_call_and_is_a_refusal(papers) -> None:
    completer = answering(good())
    result = run(papers, completer, "zebra giraffe")
    assert completer.calls == []
    assert result.outcome == "refused"
    assert result.answer is None and result.retrieved == [] and result.cost_usd == 0
    row = log.get_ask(papers, result.ask_id)
    assert (row.outcome, row.answer, row.served_model) == ("refused", None, None)
    assert row.answer_model == "claude-sonnet-5-5" and row.prompt_version == "ask/v1"
    assert row.retrieved == [] and row.checks == []
    assert row.detail["no_passages"] is True
    assert row.cost_usd == 0


class PricedEmbedder(FakeEmbedder):
    """A FakeEmbedder that bills, to prove the query embedding's cost is added."""

    def embed(self, texts):
        done = super().embed(texts)
        return Embedded(done.vectors, done.model, input_tokens=7, cost_usd=0.25)


def test_the_vector_arm_embeds_the_question_once_and_adds_its_cost(papers) -> None:
    embedder = PricedEmbedder(model="fake-embedder")
    todo = index.unembedded(papers, embedder.model)
    index.add_vectors(papers, embedder.model, {d: embedder.vector(t) for d, t in todo})
    embedder.calls.clear()
    result = ask(
        papers,
        QUESTION,
        answering(good()),
        embedder,
        arm="vector",
        embedding_model=embedder.model,
        k=2,
    )
    assert embedder.calls == [[QUESTION]]
    assert result.outcome == "answered"
    assert result.cost_usd == pytest.approx(0.25)
    row = log.get_ask(papers, result.ask_id)
    assert (row.arm, row.k, row.embedding_model) == ("vector", 2, "fake-embedder")
    assert len(row.retrieved) == 2
    assert row.cost_usd == pytest.approx(0.25)
    assert row.detail["embedding"]["input_tokens"] == 7
    assert row.detail["embedding"]["cost_usd"] == pytest.approx(0.25)
    assert row.detail["embedding"]["model"] == "fake-embedder"


def test_the_hybrid_arm_embeds_once_too(papers) -> None:
    embedder = FakeEmbedder()
    todo = index.unembedded(papers, embedder.model)
    index.add_vectors(papers, embedder.model, {d: embedder.vector(t) for d, t in todo})
    embedder.calls.clear()
    result = ask(papers, QUESTION, answering(good()), embedder, embedding_model=embedder.model)
    assert len(embedder.calls) == 1
    assert log.get_ask(papers, result.ask_id).arm == "hybrid"


def test_no_hits_on_the_vector_arm_still_costs_the_embedding(index_engine) -> None:
    embedder = PricedEmbedder()
    completer = answering(good())
    result = ask(
        index_engine, QUESTION, completer, embedder, arm="vector", embedding_model=embedder.model
    )
    assert completer.calls == []
    assert result.outcome == "refused"
    row = log.get_ask(index_engine, result.ask_id)
    assert row.cost_usd == pytest.approx(0.25)
    assert row.detail["no_passages"] is True


def test_the_lexical_arm_makes_no_embedding_call(papers) -> None:
    embedder = FakeEmbedder()
    result = ask(papers, QUESTION, answering(good()), embedder, arm="lexical")
    assert embedder.calls == []
    row = log.get_ask(papers, result.ask_id)
    assert row.embedding_model is None
    assert "embedding" not in row.detail


@pytest.mark.parametrize("arm", ["vector", "hybrid"])
def test_a_vector_arm_without_an_embedder_raises(papers, arm) -> None:
    completer = answering(good())
    with pytest.raises(ValueError, match="embedder"):
        ask(papers, QUESTION, completer, None, arm=arm)
    assert completer.calls == [] and log.asks(papers) == []


def test_an_unknown_arm_raises(papers) -> None:
    completer = answering(good())
    with pytest.raises(ValueError, match="arm"):
        ask(papers, QUESTION, completer, FakeEmbedder(), arm="fuzzy")
    assert completer.calls == [] and log.asks(papers) == []


def test_a_question_with_a_nul_raises_before_anything_is_spent(papers) -> None:
    embedder = FakeEmbedder()
    with pytest.raises(ValueError, match="NUL"):
        ask(
            papers,
            "bad\x00 question",
            answering(good()),
            embedder,
            arm="vector",
            embedding_model=embedder.model,
        )
    assert embedder.calls == [] and log.asks(papers) == []


def test_pinned_and_served_model_are_both_stored(papers, caplog) -> None:
    completer = answering(good())
    completer.model = "claude-sonnet-5-5"
    served = FakeCompleter(lambda s, m, o: good(), model="claude-sonnet-5-5")

    original = served.complete

    def complete(*args, **kwargs):
        done = original(*args, **kwargs)
        done.model = "claude-sonnet-5-5-20261201"
        return done

    served.complete = complete
    with caplog.at_level(logging.WARNING, logger="elusion.ask.answer"):
        result = run(papers, served)
    row = log.get_ask(papers, result.ask_id)
    assert row.answer_model == "claude-sonnet-5-5"
    assert row.served_model == "claude-sonnet-5-5-20261201"
    assert "claude-sonnet-5-5-20261201" in caplog.text
    # no warning when they agree
    caplog.clear()
    run(papers, completer)
    assert caplog.text == ""


def test_an_unknown_prompt_version_raises_before_anything_is_spent(papers) -> None:
    embedder = FakeEmbedder()
    with pytest.raises(FileNotFoundError):
        ask(
            papers,
            QUESTION,
            answering(good()),
            embedder,
            arm="vector",
            embedding_model=embedder.model,
            prompt_version="v99",
        )
    assert embedder.calls == [] and log.asks(papers) == []


# fix round 1: validate first, and never lose a paid call

USAGE = {
    "input_tokens": 111,
    "output_tokens": 222,
    "cache_creation_input_tokens": 333,
    "cache_read_input_tokens": 444,
    "cost_usd": 0.37,
}


def vectors(engine, embedder) -> None:
    todo = index.unembedded(engine, embedder.model)
    index.add_vectors(engine, embedder.model, {d: embedder.vector(t) for d, t in todo})


def test_each_usage_figure_lands_in_its_own_column(papers) -> None:
    completer = FakeCompleter(lambda s, m, o: good(), model="claude-sonnet-5-5", usage=USAGE)
    result = run(papers, completer)
    row = log.get_ask(papers, result.ask_id)
    assert (row.input_tokens, row.output_tokens) == (111, 222)
    assert (row.cache_creation_input_tokens, row.cache_read_input_tokens) == (333, 444)
    assert row.cost_usd == pytest.approx(0.37)
    assert result.cost_usd == pytest.approx(0.37)


def test_the_error_row_keeps_each_figure_in_its_own_column(papers) -> None:
    error = ReviewError("refusal", "claude-sonnet-5-5", 111, 222, 333, 444)
    result = run(papers, answering(error))
    row = log.get_ask(papers, result.ask_id)
    assert (row.input_tokens, row.output_tokens) == (111, 222)
    assert (row.cache_creation_input_tokens, row.cache_read_input_tokens) == (333, 444)
    assert (row.stop_reason, row.served_model) == ("refusal", "claude-sonnet-5-5")


def _outcome_cases():
    refusal = Answer(answerable=False, claims=[])
    return {
        "answered": (good(), "QUESTION"),
        "refused": (refusal, "QUESTION"),
        "failed": (good(quote="not in the abstract"), "QUESTION"),
        "error": (ReviewError("max_tokens", "claude-sonnet-5-5", 1, 2), "QUESTION"),
        "no_passages": (good(), "zebra giraffe"),
    }


@pytest.mark.parametrize("case", ["answered", "refused", "failed", "error", "no_passages"])
def test_exactly_one_row_is_written_for_each_outcome(papers, case) -> None:
    result_value, question = _outcome_cases()[case]
    question = QUESTION if question == "QUESTION" else question
    result = run(papers, answering(result_value), question)
    rows = log.asks(papers)
    assert [r.ask_id for r in rows] == [result.ask_id]
    assert rows[0].outcome == ("refused" if case == "no_passages" else case)


def test_hits_with_none_in_the_index_are_a_refusal_with_no_call(papers, monkeypatch) -> None:
    monkeypatch.setattr(answer, "passages", lambda engine, ids: {})
    completer = answering(good())
    result = run(papers, completer)
    assert completer.calls == []
    assert result.outcome == "refused"
    assert {h.doc_id for h in result.retrieved} == {"a", "b"}
    row = log.get_ask(papers, result.ask_id)
    assert row.detail["no_passages"] is True
    assert {h["doc_id"] for h in row.retrieved} == {"a", "b"}
    assert len(log.asks(papers)) == 1


def test_an_embedder_of_another_model_raises_before_any_spend(papers) -> None:
    embedder = PricedEmbedder(model="some-other-embedder")
    completer = answering(good())
    with pytest.raises(ValueError, match="some-other-embedder"):
        ask(papers, QUESTION, completer, embedder, arm="vector")  # default model differs
    assert embedder.calls == [] and completer.calls == [] and log.asks(papers) == []


@pytest.mark.parametrize("k", [0, -1, 1001])
def test_k_outside_one_to_a_thousand_raises(papers, k) -> None:
    completer = answering(good())
    with pytest.raises(ValueError, match="k must be between 1 and 1000"):
        run(papers, completer, k=k)
    assert completer.calls == [] and log.asks(papers) == []


def test_the_edges_of_k_are_allowed(papers) -> None:
    assert run(papers, answering(good()), k=1).outcome == "answered"
    assert run(papers, answering(good()), k=1000).outcome == "answered"


def test_a_model_with_no_price_raises_before_any_spend(papers) -> None:
    class Unpriced:
        name = "stub"
        model = "no-such-embedding-model"

        def embed(self, texts):
            raise AssertionError("no call may be made")

    unpriced_answerer = FakeCompleter(lambda s, m, o: good(), model="claude-sonnet-5-5")
    unpriced_answerer.model = "no-such-answer-model"  # bypass register_free
    with pytest.raises(ValueError, match="no-such-answer-model"):
        run(papers, unpriced_answerer)
    assert unpriced_answerer.calls == []
    completer = answering(good())
    with pytest.raises(ValueError, match="no-such-embedding-model"):
        ask(
            papers,
            QUESTION,
            completer,
            Unpriced(),
            arm="vector",
            embedding_model="no-such-embedding-model",
        )
    assert completer.calls == [] and log.asks(papers) == []


def test_the_lexical_arm_needs_no_embedding_price(papers) -> None:
    assert run(papers, answering(good()), embedding_model="no-such-embedding-model").ask_id


def test_a_missing_index_says_to_run_elusion_index_first(board_engine) -> None:
    completer = answering(good())
    with pytest.raises(ValueError, match="run elusion index first"):
        run(board_engine, completer)
    assert completer.calls == []


def test_a_search_failure_after_the_embedding_was_paid_writes_an_error_row(
    papers, monkeypatch
) -> None:
    def broken(*args, **kwargs):
        raise RuntimeError("the database went away")

    monkeypatch.setattr(answer, "retrieve", broken)
    embedder = PricedEmbedder()
    completer = answering(good())
    with pytest.raises(RuntimeError, match="the database went away"):
        ask(papers, QUESTION, completer, embedder, embedding_model=embedder.model)
    assert completer.calls == []
    [row] = log.asks(papers)
    assert row.outcome == "error"
    assert row.cost_usd == pytest.approx(0.25)
    assert row.stop_reason == "RuntimeError"
    assert "the database went away" in row.detail["error"]
    assert row.detail["stage"] == "retrieval"
    assert row.detail["embedding"]["cost_usd"] == pytest.approx(0.25)


def test_a_passages_failure_after_the_embedding_was_paid_writes_an_error_row(
    papers, monkeypatch
) -> None:
    def broken(engine, ids):
        raise RuntimeError("no passages for you")

    monkeypatch.setattr(answer, "passages", broken)
    embedder = PricedEmbedder()
    with pytest.raises(RuntimeError):
        ask(papers, QUESTION, answering(good()), embedder, embedding_model=embedder.model)
    [row] = log.asks(papers)
    assert (row.outcome, row.cost_usd) == ("error", pytest.approx(0.25))
    assert len(row.retrieved) > 0  # the hits were found before it failed


def test_an_api_exception_from_the_answer_call_keeps_the_embedding_cost(papers) -> None:
    embedder = PricedEmbedder()
    completer = answering(ConnectionError("the network dropped"))
    with pytest.raises(ConnectionError):
        ask(papers, QUESTION, completer, embedder, embedding_model=embedder.model)
    [row] = log.asks(papers)
    assert row.outcome == "error"
    assert row.cost_usd == pytest.approx(0.25)
    assert row.detail["stage"] == "answer"
    assert "the network dropped" in row.detail["error"]
    assert len(row.retrieved) > 0


def test_a_nul_in_the_models_answer_is_removed_and_said_so(papers) -> None:
    bad = Answer(
        answerable=True,
        claims=[Claim(claim="It ends\x00 at a target.", doc_id="a", quote="fixed\x00 recall")],
    )
    result = run(papers, answering(bad))
    assert result.outcome == "failed"  # the quote with its NUL is not in the abstract
    row = log.get_ask(papers, result.ask_id)
    assert row.answer["claims"][0]["claim"] == "It ends at a target."
    assert row.answer["claims"][0]["quote"] == "fixed recall"
    assert row.detail["nul_removed"] is True
    assert result.answer.claims[0].quote == "fixed\x00 recall"  # the caller gets it unchanged


def test_no_nul_means_no_nul_removed_flag(papers) -> None:
    row = log.get_ask(papers, run(papers, answering(good())).ask_id)
    assert "nul_removed" not in row.detail


def test_a_failed_insert_is_logged_with_the_figures_and_raised(papers, monkeypatch, caplog) -> None:
    def broken(engine, **columns):
        raise RuntimeError("insert failed")

    monkeypatch.setattr(log, "add_ask", broken)
    completer = FakeCompleter(lambda s, m, o: good(), model="claude-sonnet-5-5", usage=USAGE)
    with (
        caplog.at_level(logging.ERROR, logger="elusion.ask.answer"),
        pytest.raises(RuntimeError, match="insert failed"),
    ):
        run(papers, completer)
    text = caplog.text
    assert repr(QUESTION) in text
    assert "outcome=answered" in text
    for figure in ("input_tokens=111", "output_tokens=222", "0.37", "claude-sonnet-5-5"):
        assert figure in text
    assert "cache_creation_input_tokens=333" in text
    assert "cache_read_input_tokens=444" in text
