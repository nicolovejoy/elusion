"""The LLM provider interface and its Anthropic implementation.

A "provider" is anything that can read one paragraph under a system prompt and return a
structured Verdict plus the token usage that call consumed. Keeping this behind a small
Protocol (a structural interface, like a Go interface) lets tests use a fake and lets
other vendors be added later without touching the scorer.

Structured output: instead of parsing free text, we hand the API a JSON schema
(`output_config.format`, built from the pydantic model `Verdict`) and it constrains the
reply to that schema. `messages.create()` with `output_config.format` is the same API
feature `messages.parse()` wraps. We call `create()` directly because `parse()` raises a
pydantic ValidationError from inside the SDK when a reply is cut off mid-JSON (max_tokens)
and hides the stop_reason; here we check `stop_reason` ourselves and turn every unusable
answer (refusal, truncation, invalid JSON) into a `ReviewError` that carries the usage,
so one bad answer is a counted, billed failure rather than an unexpected crash.
The schema goes through the SDK's `transform_schema`, the same step `parse()` applies: it
adds `additionalProperties: false` and moves unsupported keywords (minimum/maximum) into
the field description; the 0-4 range is still enforced Python-side by `Verdict`.

Prompt caching: the system prompt is identical for every paragraph, so it is marked
`cache_control: ephemeral`. The first call stores that prefix; later calls read it at a
fraction of the input price. Usage therefore has four token buckets (see pricing.py).

Haiku 5.5 decides for itself how much to think (adaptive thinking), so we pass neither
`thinking` nor `temperature`; `effort: low` keeps it quick and cheap for a simple rubric.

The same provider also serves the judge (phase 3): `label()` sends one paragraph under the
judge prompt and returns a yes/no `Label` instead of a 0-4 `Verdict`. It is the same call
with a different output schema, so both go through one private `_call`: same cached system
prompt, same `effort: low`, same `ReviewError` for unusable answers, same cost accounting.
The judge is a separate `Judge` Protocol so the judge flow can take a fake in tests.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol, TypeVar, runtime_checkable

import anthropic
from anthropic import transform_schema
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from elusion.review.pricing import cost_usd

# 1024, not 512: the reply carries a verbatim quote of the paragraph plus a rationale, and a
# long paragraph overflowed 512 mid-JSON in the live run.
MAX_TOKENS = 1024
EFFORT = "low"  # output_config effort for both review and judge calls
# Generation settings per provider name (the `scores.provider` value). MLflow logs this dict:
# evaluate only sees the provider name in the scores table, not a provider instance.
GENERATION_SETTINGS: dict[str, dict] = {
    "anthropic": {"effort": EFFORT},
    "openai": {},
    "ollama": {"temperature": 0.0},
}
REQUEST_TIMEOUT_S = 60.0
MAX_RETRIES = 5  # the SDK retries 429/5xx with exponential backoff


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=0, le=4, description="Relevance score on the 0-4 scale in the prompt")
    rationale: str = Field(description="One or two sentences explaining the score")
    quote: str = Field(description="Verbatim excerpt supporting the score, or empty string")


VERDICT_SCHEMA = transform_schema(Verdict.model_json_schema())


class Label(BaseModel):
    """The judge's answer: does the paragraph meet the criterion, and why."""

    model_config = ConfigDict(extra="forbid")

    relevant: bool = Field(description="True if the paragraph meets the criterion")
    rationale: str = Field(description="One sentence saying why")


LABEL_SCHEMA = transform_schema(Label.model_json_schema())


class Grade(BaseModel):
    """The grader's answer: is the reviewer's rationale a sound reading, and why."""

    model_config = ConfigDict(extra="forbid")

    sound: bool = Field(description="True if the reviewer's rationale is a faithful reading")
    rationale: str = Field(description="One sentence saying why")


GRADE_SCHEMA = transform_schema(Grade.model_json_schema())


def grader_message(paragraph: str, verdict: Verdict) -> str:
    """The user turn for a grading call: the paragraph and one reviewer's answer, no label."""
    return (
        f"PARAGRAPH:\n{paragraph}\n\n"
        f"REVIEWER SCORE: {verdict.score}\n"
        f"REVIEWER RATIONALE: {verdict.rationale}\n"
        f"REVIEWER QUOTE: {verdict.quote}"
    )


class ReviewError(Exception):
    """The API answered but gave no usable Verdict (or, for the judge, Label).

    stop_reason is the API's (refusal, max_tokens, ...) when it did not end its turn, or
    "invalid_json" when it did but the text was not valid JSON for the output model.
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


@dataclass
class Judgement:
    label: Label
    model: str  # as served by the API, not as requested
    input_tokens: int
    output_tokens: int
    cost_usd: float
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass
class Grading:
    grade: Grade
    model: str  # as served by the API, not as requested
    input_tokens: int
    output_tokens: int
    cost_usd: float
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


# runtime_checkable: Prefect builds a pydantic schema from the flow signature, which
# isinstance-checks parameters; a plain Protocol cannot be used with isinstance.
@runtime_checkable
class Provider(Protocol):
    name: str
    model: str

    def review(self, system: str, paragraph: str) -> Review: ...


@runtime_checkable
class Judge(Protocol):
    name: str
    model: str

    def label(self, system: str, paragraph: str) -> Judgement: ...


@runtime_checkable
class Grader(Protocol):
    name: str
    model: str

    def grade(self, system: str, paragraph: str, verdict: Verdict) -> Grading: ...


_Out = TypeVar("_Out", bound=BaseModel)


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str, client: anthropic.Anthropic | None = None) -> None:
        self.model = model
        self.client = client or anthropic.Anthropic(
            max_retries=MAX_RETRIES, timeout=REQUEST_TIMEOUT_S
        )

    def review(self, system: str, paragraph: str) -> Review:
        verdict, model, usage = self._call(system, paragraph, Verdict, VERDICT_SCHEMA)
        return Review(verdict, model, **usage)

    def label(self, system: str, paragraph: str) -> Judgement:
        label, model, usage = self._call(system, paragraph, Label, LABEL_SCHEMA)
        return Judgement(label, model, **usage)

    def grade(self, system: str, paragraph: str, verdict: Verdict) -> Grading:
        grade, model, usage = self._call(
            system, grader_message(paragraph, verdict), Grade, GRADE_SCHEMA
        )
        return Grading(grade, model, **usage)

    def _call(
        self, system: str, paragraph: str, out: type[_Out], schema: dict
    ) -> tuple[_Out, str, dict]:
        """One structured-output call. Returns (parsed answer, served model, usage fields).

        Usage fields are the token counts and their cost, keyed like the Review/Judgement
        dataclass fields. Raises ReviewError (with usage) for any unusable answer.
        """
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": paragraph}],
            output_config={
                "effort": EFFORT,
                "format": {"type": "json_schema", "schema": schema},
            },
        )
        u = resp.usage
        cache_write = u.cache_creation_input_tokens or 0
        cache_read = u.cache_read_input_tokens or 0

        def fail(stop_reason: str) -> ReviewError:
            return ReviewError(
                stop_reason, resp.model, u.input_tokens, u.output_tokens, cache_write, cache_read
            )

        if resp.stop_reason != "end_turn":
            raise fail(str(resp.stop_reason))
        try:
            text = next(b.text for b in resp.content if b.type == "text")
            parsed = out.model_validate(json.loads(text))
        except (StopIteration, json.JSONDecodeError, ValidationError) as exc:
            raise fail("invalid_json") from exc
        usage = {
            "input_tokens": u.input_tokens,
            "output_tokens": u.output_tokens,
            "cost_usd": cost_usd(
                resp.model, u.input_tokens, u.output_tokens, cache_write, cache_read
            ),
            "cache_creation_input_tokens": cache_write,
            "cache_read_input_tokens": cache_read,
        }
        return parsed, resp.model, usage


def make_provider(provider: str, model: str | None = None) -> Provider:
    """Build the named provider; `model` None means that provider's configured default."""
    from elusion.config import settings

    if provider == "anthropic":
        return AnthropicProvider(model or settings.review_model)
    if provider == "openai":
        from elusion.review.openai_provider import OpenAIProvider

        return OpenAIProvider(model or settings.openai_model)
    if provider == "ollama":
        from elusion.review.openai_provider import OllamaProvider

        return OllamaProvider(model or settings.ollama_model)
    raise ValueError(f"unknown provider {provider!r}; expected anthropic, openai or ollama")
