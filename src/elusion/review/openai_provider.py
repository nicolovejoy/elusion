"""The OpenAI implementation of the review/judge Provider protocol.

It uses OpenAI's Responses API (`client.responses.create`), the current way to call their
models. Like the Anthropic provider, it asks for structured output: we send a JSON schema
built from the pydantic model with `strict: True`, and the API constrains the reply to it.
The schema needs `additionalProperties: false` on every object; pydantic emits that for
models declared with `extra="forbid"`, which `Verdict` and `Label` are.

Prompt caching is automatic here: no marker is sent. OpenAI caches the longest repeated
prompt prefix by itself (our system prompt is identical for every paragraph) and reports how
many input tokens were served from cache in `usage.input_tokens_details.cached_tokens`.
Their `input_tokens` INCLUDES those cached tokens, so we subtract them to get the same
non-overlapping buckets as pricing.py expects: `input_tokens` is the uncached part,
`cache_read_input_tokens` the cached part, and cache creation is always 0 (nothing to pay
extra for writing).

An answer is only usable when `status == "completed"`. A cut-off (`incomplete`, with the
reason in `incomplete_details`), a refusal (a content part of type "refusal" inside the
message item) or text that is not valid JSON for the output model becomes a `ReviewError`
carrying the usage, exactly as for Anthropic, so a bad answer is a counted, billed failure.

`temperature` is not sent unless the caller sets it: some models reject it, and the default
is the vendor's. When set, it is recorded in `generation_settings`.
"""

from __future__ import annotations

import json
from typing import TypeVar

import openai
from pydantic import BaseModel, ValidationError

from elusion.review.pricing import cost_usd
from elusion.review.providers import (
    MAX_RETRIES,
    MAX_TOKENS,
    REQUEST_TIMEOUT_S,
    Judgement,
    Label,
    Review,
    ReviewError,
    Verdict,
)

_Out = TypeVar("_Out", bound=BaseModel)


class OpenAIProvider:
    name = "openai"

    def __init__(
        self,
        model: str,
        client: openai.OpenAI | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        temperature: float | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.generation_settings: dict = {} if temperature is None else {"temperature": temperature}
        self.client = client or openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
            max_retries=MAX_RETRIES,
            timeout=REQUEST_TIMEOUT_S,
        )

    def review(self, system: str, paragraph: str) -> Review:
        verdict, model, usage = self._call(system, paragraph, Verdict, "verdict")
        return Review(verdict, model, **usage)

    def label(self, system: str, paragraph: str) -> Judgement:
        label, model, usage = self._call(system, paragraph, Label, "label")
        return Judgement(label, model, **usage)

    def _call(
        self, system: str, paragraph: str, out: type[_Out], name: str
    ) -> tuple[_Out, str, dict]:
        """One structured-output call. Returns (parsed answer, served model, usage fields)."""
        kwargs: dict = {}
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        resp = self.client.responses.create(
            model=self.model,
            instructions=system,
            input=paragraph,
            max_output_tokens=MAX_TOKENS,
            text={
                "format": {
                    "type": "json_schema",
                    "name": name,
                    "schema": out.model_json_schema(),
                    "strict": True,
                }
            },
            **kwargs,
        )
        u = resp.usage
        cached = u.input_tokens_details.cached_tokens or 0
        uncached = u.input_tokens - cached

        def fail(stop_reason: str) -> ReviewError:
            return ReviewError(stop_reason, resp.model, uncached, u.output_tokens, 0, cached)

        if resp.status != "completed":
            details = resp.incomplete_details
            raise fail(str((details.reason if details else None) or resp.status))
        if any(
            c.type == "refusal"
            for item in resp.output
            if item.type == "message"
            for c in item.content
        ):
            raise fail("refusal")
        try:
            parsed = out.model_validate(json.loads(resp.output_text))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise fail("invalid_json") from exc
        usage = {
            "input_tokens": uncached,
            "output_tokens": u.output_tokens,
            "cost_usd": cost_usd(resp.model, uncached, u.output_tokens, 0, cached),
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": cached,
        }
        return parsed, resp.model, usage
