"""The embedder: turn texts into vectors with one pinned model.

An *embedding* is a list of numbers that a model produces for a text so that texts with
similar meaning get nearby lists. `ask` embeds every screened-in abstract once (that is
the index) and embeds each question once (that is the search). The `Embedder` protocol is
the one seam: `OpenAIEmbedder` is the real one, and the tests use `FakeEmbedder`, so no
test makes a network call.

One `embed` call is one request: it sends the texts it is given, in one list. Splitting a
long list into requests of `BATCH` texts is the caller's job, because the caller stores
each batch before it sends the next.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import openai

from elusion.ask.models import EMBEDDING_MODEL
from elusion.review.pricing import cost_usd
from elusion.review.providers import MAX_RETRIES, REQUEST_TIMEOUT_S, ProviderConfigError

BATCH = 100  # texts per request


@dataclass(frozen=True)
class Embedded:
    vectors: list[list[float]]  # in the order of the input texts
    model: str  # as served by the API
    input_tokens: int
    cost_usd: float


@runtime_checkable
class Embedder(Protocol):
    name: str
    model: str

    def embed(self, texts: Sequence[str]) -> Embedded: ...


class OpenAIEmbedder:
    name = "openai"

    def __init__(self, model: str = EMBEDDING_MODEL, client: openai.OpenAI | None = None) -> None:
        if client is None and not os.environ.get("OPENAI_API_KEY"):
            raise ProviderConfigError(
                "OPENAI_API_KEY is not set; run with op run --env-file=.env.tpl --"
            )
        self.model = model
        self.client = client or openai.OpenAI(max_retries=MAX_RETRIES, timeout=REQUEST_TIMEOUT_S)

    def embed(self, texts: Sequence[str]) -> Embedded:
        """One request for all `texts`. The API may answer in any order; each item carries
        the position of its text, and the vectors are put back in that order. `dimensions`
        is never passed: the length is the model's own, `EMBEDDING_DIM`."""
        if not texts:
            return Embedded(vectors=[], model=self.model, input_tokens=0, cost_usd=0.0)
        resp = self.client.embeddings.create(model=self.model, input=list(texts))
        ordered = sorted(resp.data, key=lambda item: item.index)
        tokens = resp.usage.prompt_tokens
        return Embedded(
            vectors=[list(item.embedding) for item in ordered],
            model=resp.model,
            input_tokens=tokens,
            cost_usd=cost_usd(self.model, tokens, 0),
        )
