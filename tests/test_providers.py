from types import SimpleNamespace

import anthropic
import pytest
from pydantic import ValidationError

from elusion.config import Settings
from elusion.review.providers import AnthropicProvider, Provider, ReviewError, Verdict
from tests.fakes import FakeProvider


def test_verdict_rejects_out_of_range_score():
    with pytest.raises(ValidationError):
        Verdict(score=5, rationale="r", quote="q")
    with pytest.raises(ValidationError):
        Verdict(score=-1, rationale="r", quote="q")


def test_fake_provider_satisfies_protocol():
    p: Provider = FakeProvider()
    assert p.review("sys", "para").verdict.score == 3


def test_review_model_setting_default(monkeypatch):
    monkeypatch.delenv("ELUSION_REVIEW_MODEL", raising=False)
    assert Settings().review_model == "claude-haiku-5-5"
    monkeypatch.setenv("ELUSION_REVIEW_MODEL", "other")
    assert Settings().review_model == "other"


def test_anthropic_provider_request_shape_and_cost(monkeypatch):
    client = anthropic.Anthropic(api_key="test")
    seen = {}
    verdict = Verdict(score=2, rationale="r", quote="q")

    def fake_parse(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(
            parsed_output=verdict,
            model="claude-haiku-5-5",
            usage=SimpleNamespace(
                input_tokens=1_000_000,
                output_tokens=1_000_000,
                cache_creation_input_tokens=1_000_000,
                cache_read_input_tokens=1_000_000,
            ),
        )

    monkeypatch.setattr(client.messages, "parse", fake_parse)
    provider = AnthropicProvider("claude-haiku-5-5", client=client)
    assert provider.name == "anthropic"
    r = provider.review("SYSTEM TEXT", "the paragraph")

    assert seen["model"] == "claude-haiku-5-5"
    assert seen["max_tokens"] == 512
    assert seen["output_config"] == {"effort": "low"}
    assert seen["output_format"] is Verdict
    assert "thinking" not in seen and "temperature" not in seen
    assert seen["system"] == [
        {"type": "text", "text": "SYSTEM TEXT", "cache_control": {"type": "ephemeral"}}
    ]
    assert seen["messages"] == [{"role": "user", "content": "the paragraph"}]

    assert r.verdict == verdict
    assert r.model == "claude-haiku-5-5"
    assert (r.input_tokens, r.output_tokens) == (1_000_000, 1_000_000)
    assert (r.cache_creation_input_tokens, r.cache_read_input_tokens) == (1_000_000, 1_000_000)
    assert r.cost_usd == pytest.approx(0.735)


def test_missing_parsed_output_raises_review_error_with_usage(monkeypatch):
    client = anthropic.Anthropic(api_key="test")

    def fake_parse(**kwargs):
        return SimpleNamespace(
            parsed_output=None,
            stop_reason="refusal",
            model="claude-haiku-5-5",
            usage=SimpleNamespace(
                input_tokens=10,
                output_tokens=20,
                cache_creation_input_tokens=None,
                cache_read_input_tokens=30,
            ),
        )

    monkeypatch.setattr(client.messages, "parse", fake_parse)
    with pytest.raises(ReviewError) as exc:
        AnthropicProvider("claude-haiku-5-5", client=client).review("s", "p")
    e = exc.value
    assert e.stop_reason == "refusal"
    assert e.model == "claude-haiku-5-5"
    assert (e.input_tokens, e.output_tokens) == (10, 20)
    assert (e.cache_creation_input_tokens, e.cache_read_input_tokens) == (0, 30)
