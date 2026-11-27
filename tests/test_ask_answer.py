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
        ask(papers, "bad\x00 question", answering(good()), embedder, arm="vector")
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
        ask(papers, QUESTION, answering(good()), embedder, arm="vector", prompt_version="v99")
    assert embedder.calls == [] and log.asks(papers) == []
