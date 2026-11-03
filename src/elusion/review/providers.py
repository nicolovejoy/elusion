"""The LLM provider interface and its Anthropic implementation.

A "provider" is anything that can read one paragraph under a system prompt and return a
structured Verdict plus the token usage that call consumed. Keeping this behind a small
Protocol (a structural interface, like a Go interface) lets tests use a fake and lets
other vendors be added later without touching the scorer.

Structured output: instead of parsing free text, we hand the API a pydantic model
(`Verdict`) and it constrains the reply to that JSON schema; the SDK's `messages.parse`
returns it already validated.

Prompt caching: the system prompt is identical for every paragraph, so it is marked
`cache_control: ephemeral`. The first call stores that prefix; later calls read it at a
fraction of the input price. Usage therefore has four token buckets (see pricing.py).

Haiku 5.5 decides for itself how much to think (adaptive thinking), so we pass neither
`thinking` nor `temperature`; `effort: low` keeps it quick and cheap for a simple rubric.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import anthropic
from pydantic import BaseModel, Field

from elusion.review.pricing import cost_usd

MAX_TOKENS = 512
REQUEST_TIMEOUT_S = 60.0
MAX_RETRIES = 5  # the SDK retries 429/5xx with exponential backoff


class Verdict(BaseModel):
    score: int = Field(ge=0, le=4, description="Relevance score on the 0-4 scale in the prompt")
    rationale: str = Field(description="One or two sentences explaining the score")
    quote: str = Field(description="Verbatim excerpt supporting the score, or empty string")


class ReviewError(Exception):
    """The API answered but gave no usable Verdict (refusal or truncated at max_tokens).

    Tokens were still billed, so the usage is carried for cost accounting.
    """

    def __init__(
        self,
        stop_reason: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_creation_input_tokens: int = 0,
        cache_read_input_tokens: int = 0,
    ) -> None:
        super().__init__(f"no parsed verdict (stop_reason={stop_reason!r}, model={model!r})")
        self.stop_reason = stop_reason
        self.model = model
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_creation_input_tokens = cache_creation_input_tokens
        self.cache_read_input_tokens = cache_read_input_tokens


@dataclass
class Review:
    verdict: Verdict
    model: str  # as served by the API, not as requested
    input_tokens: int
    output_tokens: int
    cost_usd: float
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


class Provider(Protocol):
    name: str
    model: str

    def review(self, system: str, paragraph: str) -> Review: ...


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str, client: anthropic.Anthropic | None = None) -> None:
        self.model = model
        self.client = client or anthropic.Anthropic(
            max_retries=MAX_RETRIES, timeout=REQUEST_TIMEOUT_S
        )

    def review(self, system: str, paragraph: str) -> Review:
        resp = self.client.messages.parse(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": paragraph}],
            output_format=Verdict,
            output_config={"effort": "low"},
        )
        u = resp.usage
        cache_write = u.cache_creation_input_tokens or 0
        cache_read = u.cache_read_input_tokens or 0
        if resp.parsed_output is None:
            raise ReviewError(
                str(resp.stop_reason),
                resp.model,
                u.input_tokens,
                u.output_tokens,
                cache_write,
                cache_read,
            )
        return Review(
            verdict=resp.parsed_output,
            model=resp.model,
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cost_usd=cost_usd(resp.model, u.input_tokens, u.output_tokens, cache_write, cache_read),
            cache_creation_input_tokens=cache_write,
            cache_read_input_tokens=cache_read,
        )
