import json
from types import SimpleNamespace

import anthropic
import pytest
from pydantic import ValidationError

from elusion.config import Settings
from elusion.review.providers import (
    LABEL_SCHEMA,
    VERDICT_SCHEMA,
    AnthropicProvider,
    Judge,
    Label,
    Provider,
    ReviewError,
    Verdict,
)
from tests.fakes import FakeJudge, FakeProvider


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


def test_judge_model_setting_default(monkeypatch):
    monkeypatch.delenv("ELUSION_JUDGE_MODEL", raising=False)
    assert Settings().judge_model == "claude-opus-5-5"
    monkeypatch.setenv("ELUSION_JUDGE_MODEL", "other")
    assert Settings().judge_model == "other"


USAGE = dict(
    input_tokens=10,
    output_tokens=20,
    cache_creation_input_tokens=None,
    cache_read_input_tokens=30,
)


def stub_create(monkeypatch, text, stop_reason="end_turn", usage=None, seen=None):
    client = anthropic.Anthropic(api_key="test")

    def fake_create(**kwargs):
        if seen is not None:
            seen.update(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)],
            stop_reason=stop_reason,
            model="claude-haiku-5-5",
            usage=SimpleNamespace(**(usage or USAGE)),
        )

    monkeypatch.setattr(client.messages, "create", fake_create)
    return AnthropicProvider("claude-haiku-5-5", client=client)


def test_anthropic_provider_request_shape_and_cost(monkeypatch):
    seen = {}
    verdict = Verdict(score=2, rationale="r", quote="q")
    big = 1_000_000
    usage = dict(
        input_tokens=big,
        output_tokens=big,
        cache_creation_input_tokens=big,
        cache_read_input_tokens=big,
    )
    provider = stub_create(monkeypatch, verdict.model_dump_json(), usage=usage, seen=seen)
    assert provider.name == "anthropic"
    r = provider.review("SYSTEM TEXT", "the paragraph")

    assert seen["model"] == "claude-haiku-5-5"
    assert seen["max_tokens"] == 1024
    fmt = seen["output_config"]["format"]
    assert seen["output_config"]["effort"] == "low"
    assert fmt["type"] == "json_schema"
    assert fmt["schema"] == VERDICT_SCHEMA
    assert fmt["schema"]["additionalProperties"] is False
    assert fmt["schema"]["required"] == ["score", "rationale", "quote"]
    assert "$defs" not in fmt["schema"]
    assert "output_format" not in seen
    assert "thinking" not in seen and "temperature" not in seen
    assert seen["system"] == [
        {"type": "text", "text": "SYSTEM TEXT", "cache_control": {"type": "ephemeral"}}
    ]
    assert seen["messages"] == [{"role": "user", "content": "the paragraph"}]

    assert r.verdict == verdict
    assert r.model == "claude-haiku-5-5"
    assert (r.input_tokens, r.output_tokens) == (big, big)
    assert (r.cache_creation_input_tokens, r.cache_read_input_tokens) == (big, big)
    assert r.cost_usd == pytest.approx(0.735)


def assert_review_error(monkeypatch, text, stop_reason, expected):
    provider = stub_create(monkeypatch, text, stop_reason=stop_reason)
    with pytest.raises(ReviewError) as exc:
        provider.review("s", "p")
    e = exc.value
    assert e.stop_reason == expected
    assert e.model == "claude-haiku-5-5"
    assert (e.input_tokens, e.output_tokens) == (10, 20)
    assert (e.cache_creation_input_tokens, e.cache_read_input_tokens) == (0, 30)
    return e


def test_refusal_raises_review_error_with_usage(monkeypatch):
    assert_review_error(monkeypatch, "", "refusal", "refusal")


def test_max_tokens_raises_review_error_with_usage(monkeypatch):
    assert_review_error(
        monkeypatch, '{"score": 2, "rationale": "r", "quote": "The Cust', "max_tokens", "max_tokens"
    )


def test_truncated_json_with_end_turn_is_invalid_json(monkeypatch):
    e = assert_review_error(
        monkeypatch,
        '{"score": 2, "rationale": "r", "quote": "The Custod',
        "end_turn",
        "invalid_json",
    )
    assert isinstance(e.__cause__, json.JSONDecodeError)


def test_out_of_range_score_is_invalid_json(monkeypatch):
    e = assert_review_error(
        monkeypatch, '{"score": 5, "rationale": "r", "quote": "q"}', "end_turn", "invalid_json"
    )
    assert isinstance(e.__cause__, ValidationError)


def test_fake_judge_satisfies_protocol():
    j: Judge = FakeJudge()
    assert j.label("sys", "para").label.relevant is True


def test_label_rejects_extra_fields():
    with pytest.raises(ValidationError):
        Label.model_validate({"relevant": True, "rationale": "r", "score": 3})


def test_label_request_shape_and_cost(monkeypatch):
    seen = {}
    label = Label(relevant=True, rationale="names AWS as a dependency")
    big = 1_000_000
    usage = dict(
        input_tokens=big,
        output_tokens=big,
        cache_creation_input_tokens=big,
        cache_read_input_tokens=big,
    )
    provider = stub_create(monkeypatch, label.model_dump_json(), usage=usage, seen=seen)
    j = provider.label("JUDGE SYSTEM", "the paragraph")

    assert seen["model"] == "claude-haiku-5-5"
    fmt = seen["output_config"]["format"]
    assert seen["output_config"]["effort"] == "low"
    assert fmt == {"type": "json_schema", "schema": LABEL_SCHEMA}
    assert fmt["schema"]["additionalProperties"] is False
    assert fmt["schema"]["required"] == ["relevant", "rationale"]
    assert "thinking" not in seen and "temperature" not in seen
    assert seen["system"] == [
        {"type": "text", "text": "JUDGE SYSTEM", "cache_control": {"type": "ephemeral"}}
    ]
    # Paragraph text only: nothing else reaches the judge.
    assert seen["messages"] == [{"role": "user", "content": "the paragraph"}]

    assert j.label == label
    assert j.model == "claude-haiku-5-5"
    assert (j.input_tokens, j.output_tokens) == (big, big)
    assert (j.cache_creation_input_tokens, j.cache_read_input_tokens) == (big, big)
    assert j.cost_usd == pytest.approx(0.735)


@pytest.mark.parametrize(
    ("text", "stop_reason", "expected"),
    [
        ("", "refusal", "refusal"),
        ('{"relevant": true, "ratio', "max_tokens", "max_tokens"),
        ('{"relevant": true, "ratio', "end_turn", "invalid_json"),
        ('{"relevant": "maybe", "rationale": "r"}', "end_turn", "invalid_json"),
    ],
)
def test_label_unusable_answers_raise_review_error(monkeypatch, text, stop_reason, expected):
    provider = stub_create(monkeypatch, text, stop_reason=stop_reason)
    with pytest.raises(ReviewError) as exc:
        provider.label("s", "p")
    e = exc.value
    assert e.stop_reason == expected and e.model == "claude-haiku-5-5"
    assert (e.input_tokens, e.output_tokens) == (10, 20)
    assert (e.cache_creation_input_tokens, e.cache_read_input_tokens) == (0, 30)
