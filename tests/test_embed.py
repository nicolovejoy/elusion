from types import SimpleNamespace

import pytest

from elusion.ask.embed import Embedded, Embedder, OpenAIEmbedder
from elusion.review.pricing import PRICES, cost_usd, register_free
from elusion.review.providers import ProviderConfigError
from tests.fakes import FakeEmbedder

register_free("m")  # the stub model of the tests below costs nothing


class StubClient:
    """Answers like `client.embeddings.create`, items out of order, and records the calls."""

    def __init__(self, tokens: int = 7) -> None:
        self.calls: list[dict] = []
        self.tokens = tokens
        self.embeddings = SimpleNamespace(create=self.create)

    def create(self, **kw):
        self.calls.append(kw)
        items = [
            SimpleNamespace(index=i, embedding=[float(i), 0.5]) for i in range(len(kw["input"]))
        ]
        return SimpleNamespace(
            data=list(reversed(items)),
            model="served-model",
            usage=SimpleNamespace(prompt_tokens=self.tokens, total_tokens=self.tokens),
        )


def test_vectors_come_back_in_input_order() -> None:
    client = StubClient()
    result = OpenAIEmbedder("m", client=client).embed(["x", "y", "z"])
    assert result.vectors == [[0.0, 0.5], [1.0, 0.5], [2.0, 0.5]]
    assert result.model == "served-model"
    assert len(client.calls) == 1
    assert client.calls[0] == {"model": "m", "input": ["x", "y", "z"]}  # no `dimensions`


def test_tokens_and_cost(monkeypatch) -> None:
    # A price this test registers itself: the real model's row is not this test's business.
    monkeypatch.setitem(
        PRICES, "test-embed", {"input": 0.5, "output": 0.0, "cache_write": 0.0, "cache_read": 0.0}
    )
    result = OpenAIEmbedder("test-embed", client=StubClient(tokens=2_000_000)).embed(["a"])
    assert result.input_tokens == 2_000_000
    assert result.cost_usd == cost_usd("test-embed", 2_000_000, 0) == pytest.approx(1.0)


def test_empty_input_makes_no_call() -> None:
    client = StubClient()
    result = OpenAIEmbedder("m", client=client).embed([])
    assert result == Embedded(vectors=[], model="m", input_tokens=0, cost_usd=0.0)
    assert client.calls == []


def test_missing_key_is_a_config_error(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ProviderConfigError, match="OPENAI_API_KEY is not set"):
        OpenAIEmbedder("m")


def test_the_fake_and_the_real_embedder_satisfy_the_protocol() -> None:
    assert isinstance(FakeEmbedder(), Embedder)
    assert isinstance(OpenAIEmbedder("m", client=StubClient()), Embedder)
