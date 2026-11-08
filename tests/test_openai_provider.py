"""OpenAIProvider against a fake Responses client: no network, kwargs are recorded."""

import json
from types import SimpleNamespace as NS

import pytest

from elusion.review.openai_provider import OpenAIProvider
from elusion.review.pricing import cost_usd
from elusion.review.providers import (
    AnthropicProvider,
    Judgement,
    Review,
    ReviewError,
    make_provider,
)

MODEL = "gpt-6-luna"
GOOD = json.dumps({"score": 3, "rationale": "names one cloud vendor", "quote": "AWS"})


def response(
    text: str = GOOD,
    status: str = "completed",
    reason: str | None = None,
    refusal: str | None = None,
    prompt: int = 1000,
    cached: int = 0,
    out: int = 50,
    model: str = MODEL,
):
    """Shaped like the SDK's Response: a refusal is a content part inside a message item."""
    part = NS(type="refusal", refusal=refusal) if refusal else NS(type="output_text", text=text)
    return NS(
        status=status,
        incomplete_details=NS(reason=reason) if reason else None,
        model=model,
        output=[NS(type="message", content=[part])],
        output_text="" if refusal else text,
        usage=NS(
            input_tokens=prompt,
            output_tokens=out,
            input_tokens_details=NS(cached_tokens=cached),
        ),
    )


class FakeResponses:
    def __init__(self, resp) -> None:
        self.resp = resp
        self.kw: dict = {}

    def create(self, **kw):
        self.kw = kw
        return self.resp


def provider(resp, **kw) -> tuple[OpenAIProvider, FakeResponses]:
    fake = FakeResponses(resp)
    return OpenAIProvider(MODEL, client=NS(responses=fake), **kw), fake


def test_review_request_shape_and_cost() -> None:
    p, fake = provider(response(prompt=1000, cached=400, out=50))
    r = p.review("system text", "the paragraph")
    kw = fake.kw
    assert kw["model"] == MODEL
    assert kw["instructions"] == "system text"
    assert kw["input"] == "the paragraph"
    assert kw["max_output_tokens"] == 1024
    assert "temperature" not in kw
    fmt = kw["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["strict"] is True and fmt["name"] == "verdict"
    assert fmt["schema"]["additionalProperties"] is False
    assert isinstance(r, Review) and r.verdict.score == 3 and r.model == MODEL
    assert r.cost_usd == cost_usd(MODEL, 600, 50, 0, 400)


def test_cached_tokens_map_to_cache_read_bucket() -> None:
    p, _ = provider(response(prompt=1000, cached=400))
    r = p.review("s", "t")
    assert r.cache_read_input_tokens == 400
    assert r.input_tokens == 600
    assert r.cache_creation_input_tokens == 0


def test_incomplete_max_output_tokens_raises_review_error_with_usage() -> None:
    p, _ = provider(
        response(text='{"score": 3, "rat', status="incomplete", reason="max_output_tokens")
    )
    with pytest.raises(ReviewError) as e:
        p.review("s", "t")
    assert e.value.stop_reason == "max_output_tokens"
    assert (e.value.input_tokens, e.value.output_tokens) == (1000, 50)


def test_failed_response_without_usage_is_a_review_error() -> None:
    r = response(status="failed")
    r.usage = None
    p, _ = provider(r)
    with pytest.raises(ReviewError) as e:
        p.review("s", "t")
    assert e.value.stop_reason == "failed"
    assert (e.value.input_tokens, e.value.output_tokens) == (0, 0)


def test_cost_uses_pinned_model_so_served_drift_reaches_the_echo_check() -> None:
    p, _ = provider(response(model="gpt-6-luna-2026-12-01"))
    r = p.review("s", "t")
    assert r.model == "gpt-6-luna-2026-12-01"  # as served; the flow compares it to p.model
    assert r.cost_usd == cost_usd(MODEL, 1000, 50, 0, 0)


def test_refusal_raises_review_error() -> None:
    p, _ = provider(response(refusal="I cannot help with that"))
    with pytest.raises(ReviewError) as e:
        p.review("s", "t")
    assert e.value.stop_reason == "refusal"
    assert e.value.output_tokens == 50


def test_prose_answer_is_invalid_json() -> None:
    p, _ = provider(response(text="I think 3"))
    with pytest.raises(ReviewError) as e:
        p.review("s", "t")
    assert e.value.stop_reason == "invalid_json"


def test_label_returns_judgement() -> None:
    p, fake = provider(response(text=json.dumps({"relevant": True, "rationale": "yes"})))
    j = p.label("s", "t")
    assert isinstance(j, Judgement) and j.label.relevant is True
    assert fake.kw["text"]["format"]["name"] == "label"
    assert "relevant" in fake.kw["text"]["format"]["schema"]["properties"]


def test_temperature_is_sent_only_when_set() -> None:
    p, fake = provider(response(), temperature=0.0)
    p.review("s", "t")
    assert fake.kw["temperature"] == 0.0
    assert p.generation_settings == {"temperature": 0.0}
    p2, _ = provider(response())
    assert p2.generation_settings == {}


def test_make_provider_dispatches_and_rejects_unknown(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    a = make_provider("anthropic", "claude-haiku-5-5")
    assert isinstance(a, AnthropicProvider)
    o = make_provider("openai", MODEL)
    assert isinstance(o, OpenAIProvider) and o.model == MODEL and o.name == "openai"
    with pytest.raises(ValueError, match="nope"):
        make_provider("nope", "m")


# --- Ollama: chat completions against a fake client -------------------------------------

OLLAMA_MODEL = "m:8b"


def chat(
    text=GOOD, finish="stop", refusal=None, prompt=1000, cached=None, out=50, model=OLLAMA_MODEL
):
    """Shaped like the SDK's ChatCompletion; cached_tokens may be None."""
    details = None if cached is None else NS(cached_tokens=cached)
    return NS(
        model=model,
        choices=[
            NS(finish_reason=finish, message=NS(content=text, refusal=refusal)),
        ],
        usage=NS(prompt_tokens=prompt, completion_tokens=out, prompt_tokens_details=details),
    )


class FakeChat:
    def __init__(self, resp) -> None:
        self.resp = resp
        self.kw: dict = {}
        self.completions = self

    def create(self, **kw):
        self.kw = kw
        return self.resp


def ollama(resp, **kw):
    from elusion.review.openai_provider import OllamaProvider

    fake = FakeChat(resp)
    return OllamaProvider(OLLAMA_MODEL, client=NS(chat=fake), **kw), fake


def test_ollama_uses_compat_endpoint_and_zero_cost(monkeypatch):
    import openai

    from elusion.review import openai_provider
    from elusion.review.openai_provider import OllamaProvider

    seen: dict = {}

    class Spy:
        def __init__(self, **kw) -> None:
            seen.update(kw)

    monkeypatch.setattr(openai_provider.openai, "OpenAI", Spy)
    assert openai.OpenAI is Spy
    p = OllamaProvider(OLLAMA_MODEL, host="http://h:1")
    assert seen["base_url"] == "http://h:1/v1" and seen["api_key"] == "ollama"
    assert cost_usd(OLLAMA_MODEL, 10, 10) == 0.0
    p2, fake = ollama(chat())
    r = p2.review("sys", "para")
    assert r.cost_usd == 0.0 and r.verdict.score == 3
    assert fake.kw["response_format"]["json_schema"]["strict"] is True
    assert fake.kw["messages"][0] == {"role": "system", "content": "sys"}
    assert p.name == "ollama"


def test_ollama_sends_temperature_zero_by_default():
    p, fake = ollama(chat())
    p.review("s", "p")
    assert fake.kw["temperature"] == 0.0
    assert p.generation_settings == {"temperature": 0.0}


def test_ollama_prose_answer_is_counted_invalid_json():
    p, _ = ollama(chat(text="Score: 3 because it names AWS.", prompt=200, out=9))
    with pytest.raises(ReviewError) as e:
        p.review("s", "p")
    assert e.value.stop_reason == "invalid_json"
    assert (e.value.input_tokens, e.value.output_tokens) == (200, 9)


def test_ollama_length_is_counted_failure():
    p, _ = ollama(chat(finish="length", text='{"score":'))
    with pytest.raises(ReviewError) as e:
        p.review("s", "p")
    assert e.value.stop_reason == "length"


def test_ollama_echo_name_tag():
    p, _ = ollama(chat(model="m:latest"))
    assert p.review("s", "p").model == "m:latest"  # mismatch is the flow's check_model_echo
    p, _ = ollama(chat())
    assert p.review("s", "p").model == p.model


def test_make_provider_ollama_defaults_to_settings_model(monkeypatch):
    from elusion.config import settings

    p = make_provider("ollama")
    assert p.name == "ollama" and p.model == settings.ollama_model
    assert make_provider("ollama", "x:1b").model == "x:1b"


def test_make_provider_openai_without_a_key_is_a_clean_value_error(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENAI_API_KEY is not set; run with op run"):
        make_provider("openai", MODEL)


@pytest.mark.parametrize(
    "host, base_url",
    [
        ("127.0.0.1:11434", "http://127.0.0.1:11434/v1"),
        ("0.0.0.0:11434", "http://0.0.0.0:11434/v1"),
        ("http://h:1/", "http://h:1/v1"),
        ("https://h:1", "https://h:1/v1"),
    ],
)
def test_ollama_host_is_normalized(monkeypatch, host, base_url):
    """Ollama's own server accepts a scheme-less OLLAMA_HOST; httpx does not."""
    from elusion.review import openai_provider
    from elusion.review.openai_provider import OllamaProvider

    seen: dict = {}

    class Spy:
        def __init__(self, **kw) -> None:
            seen.update(kw)

    monkeypatch.setattr(openai_provider.openai, "OpenAI", Spy)
    OllamaProvider(OLLAMA_MODEL, host=host)
    assert seen["base_url"] == base_url
