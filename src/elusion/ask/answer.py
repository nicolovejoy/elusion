"""`ask`: retrieve the abstracts for one question, make one model call, check every quote.

The idea in plain words. A question is searched once (`retrieve`), the best `k` abstracts
are shown to a model together with the question, and the model answers in claims: each
claim is one sentence, the id of the abstract it rests on, and one quote from that
abstract. The model is then not trusted: Python checks each quote against the stored
abstract text (`find_quote`, which tolerates only whitespace), and each cited id against
the ids the model was actually shown. A claim whose quote is not in the abstract, or whose
id was never given, makes the whole ask `failed`. There is no retry, no dropped claim and no
second call to repair the quote: a failed ask is stored whole and reported as failed.

The four outcomes:

- `answered`: the model said the passages answer the question, and every claim checked out.
- `refused`: the passages do not answer it (the model said so, or nothing was retrieved and
  no model was called). The refusal is a good answer to a question the index cannot answer.
- `failed`: the model answered, but a claim did not check out or the answer contradicts
  itself (`answerable` true with no claims, or false with claims).
- `error`: the API gave no usable answer (a refusal by the provider, a cut-off, bad JSON).
  The call was billed, so the row holds the cost.

Every ask writes exactly one row to the append-only `ask_log`.

No agent: one search, one answer. The question is never rewritten and nothing loops.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.engine import Engine

from elusion.ask import log
from elusion.ask.embed import Embedder
from elusion.ask.models import EMBEDDING_MODEL
from elusion.ask.retrieve import ARMS, DEFAULT_ARM, Hit, K, Passage, passages, retrieve
from elusion.board.quotes import find_quote
from elusion.review.pricing import cost_usd
from elusion.review.prompt import load_prompt
from elusion.review.providers import Completer, ReviewError

logger = logging.getLogger(__name__)

# 16,000, not 1,024: on claude-sonnet-5-5 and claude-opus-5-5 thinking cannot be turned off
# and its tokens count against `max_tokens` (Anthropic docs, read 2026-11-26), so a small
# limit can be spent on thinking and cut the answer off, which is a wasted ask.
ANSWER_MAX_TOKENS = 16000
PROMPT = "ask"
REFUSAL = "The passages do not answer the question."


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str = Field(description="One sentence, in your own words, that the quote supports")
    doc_id: str = Field(
        description="The document id of the passage the quote comes from, exactly as given"
    )
    quote: str = Field(
        description="The one passage of that abstract that supports the claim, copied character"
        " for character from the abstract text, under 40 words, never from the title"
    )


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answerable: bool = Field(description="True if the passages answer the question")
    claims: list[Claim] = Field(
        description="One to four claims that together answer the question; empty if not answerable"
    )


@dataclass(frozen=True)
class ClaimCheck:
    index: int
    doc_id: str
    retrieved: bool  # the cited id is among the passages the model was given
    verbatim: bool  # find_quote finds the quote in that abstract's text (not its title)


@dataclass(frozen=True)
class AskResult:
    ask_id: int
    outcome: str
    answer: Answer | None
    checks: list[ClaimCheck]
    retrieved: list[Hit]
    cost_usd: float
    stop_reason: str | None = None  # the API's, for an `error` ask


def ask_message(question: str, passages: Sequence[Passage]) -> str:
    """The user turn: the question, then each passage as `[doc_id] title`, a line break and
    its text, in rank order. Nothing else reaches the model."""
    blocks = "\n\n".join(f"[{p.doc_id}] {p.title}\n{p.text}" for p in passages)
    return f"QUESTION:\n{question}\n\nPASSAGES:\n{blocks}"


def check_answer(answer: Answer, texts: Mapping[str, str]) -> list[ClaimCheck]:
    """Check each claim. `texts` maps the ids the model was shown to their abstract text.
    A claim passes when its id is among them and its quote is found in that text."""
    checks = []
    for index, claim in enumerate(answer.claims):
        retrieved = claim.doc_id in texts
        verbatim = retrieved and find_quote(texts[claim.doc_id], claim.quote) is not None
        checks.append(ClaimCheck(index, claim.doc_id, retrieved, verbatim))
    return checks


def ask(
    engine: Engine,
    question: str,
    answerer: Completer,
    embedder: Embedder | None,
    *,
    arm: str = DEFAULT_ARM,
    k: int = K,
    embedding_model: str = EMBEDDING_MODEL,
    prompt_version: str = "v1",
) -> AskResult:
    """Answer one question from the index, log it, and return what happened.

    Raises `ValueError` for an arm that does not exist, for a vector arm with no embedder,
    and for a question with a NUL character, and `FileNotFoundError` for an unknown prompt
    version: all before anything is spent or written.
    """
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}; the arms are {', '.join(ARMS)}")
    needs_vector = arm != "lexical"
    if needs_vector and embedder is None:
        raise ValueError(f"the {arm} arm needs an embedder to embed the question")
    if "\x00" in question:
        raise ValueError("the question contains a NUL character, which Postgres cannot store")
    prompt = load_prompt(PROMPT, prompt_version)

    query_vector = None
    embedding_cost = 0.0
    detail: dict = {}
    if needs_vector:
        embedded = embedder.embed([question])
        query_vector = embedded.vectors[0]
        embedding_cost = embedded.cost_usd
        detail["embedding"] = {
            "model": embedding_model,
            "served_model": embedded.model,
            "input_tokens": embedded.input_tokens,
            "cost_usd": embedded.cost_usd,
        }

    hits = retrieve(engine, arm, question, query_vector, embedding_model, k)
    found = passages(engine, [h.doc_id for h in hits])
    shown = [found[h.doc_id] for h in hits if h.doc_id in found]

    row = {
        "question": question,
        "arm": arm,
        "k": k,
        "retrieved": [asdict(h) for h in hits],
        "embedding_model": embedding_model if needs_vector else None,
        "answer_model": answerer.model,
        "prompt_version": prompt.version,
    }

    def write(outcome: str, **more) -> int:
        spent = more.pop("cost_usd", 0.0) + embedding_cost
        return log.add_ask(engine, **row, outcome=outcome, cost_usd=spent, detail=detail, **more)

    if not shown:
        # A call on empty input is paid for nothing: refuse without one.
        detail["no_passages"] = True
        ask_id = write("refused")
        return AskResult(ask_id, "refused", None, [], hits, embedding_cost)

    try:
        done = answerer.complete(
            prompt.text,
            ask_message(question, shown),
            Answer,
            max_tokens=ANSWER_MAX_TOKENS,
        )
    except ReviewError as exc:
        spent = cost_usd(
            answerer.model,
            exc.input_tokens,
            exc.output_tokens,
            exc.cache_creation_input_tokens,
            exc.cache_read_input_tokens,
        )
        ask_id = write(
            "error",
            served_model=exc.model,
            stop_reason=exc.stop_reason,
            input_tokens=exc.input_tokens,
            output_tokens=exc.output_tokens,
            cache_creation_input_tokens=exc.cache_creation_input_tokens,
            cache_read_input_tokens=exc.cache_read_input_tokens,
            cost_usd=spent,
        )
        return AskResult(
            ask_id, "error", None, [], hits, spent + embedding_cost, stop_reason=exc.stop_reason
        )

    if done.model != answerer.model:
        logger.warning(
            "the pinned model is %s but the API served %s; the row stores both",
            answerer.model,
            done.model,
        )
    answer = done.parsed
    checks = check_answer(answer, {p.doc_id: p.text for p in shown})
    if not answer.answerable and not answer.claims:
        outcome = "refused"
    elif answer.answerable != bool(answer.claims):
        detail["inconsistent_answer"] = True
        outcome = "failed"
    elif all(c.retrieved and c.verbatim for c in checks):
        outcome = "answered"
    else:
        outcome = "failed"
    ask_id = write(
        outcome,
        served_model=done.model,
        answer=answer.model_dump(),
        checks=[asdict(c) for c in checks],
        input_tokens=done.input_tokens,
        output_tokens=done.output_tokens,
        cache_creation_input_tokens=done.cache_creation_input_tokens,
        cache_read_input_tokens=done.cache_read_input_tokens,
        cost_usd=done.cost_usd,
    )
    return AskResult(ask_id, outcome, answer, checks, hits, done.cost_usd + embedding_cost)
