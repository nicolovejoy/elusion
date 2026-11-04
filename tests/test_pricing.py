import pytest

from elusion.review.pricing import PRICES, cost_usd


def test_haiku_prices_are_pinned():
    assert PRICES["claude-haiku-5-5"] == {
        "input": 0.10,
        "output": 0.50,
        "cache_write": 0.125,
        "cache_read": 0.01,
    }


def test_cost_sums_all_four_buckets():
    # 1M of each bucket: 0.10 + 0.50 + 0.125 + 0.01
    c = cost_usd("claude-haiku-5-5", 1_000_000, 1_000_000, 1_000_000, 1_000_000)
    assert c == pytest.approx(0.735)


def test_cache_buckets_default_to_zero():
    assert cost_usd("claude-haiku-5-5", 1_000_000, 0) == pytest.approx(0.10)


def test_unknown_model_raises_clear_keyerror():
    with pytest.raises(KeyError, match="no-such-model"):
        cost_usd("no-such-model", 1, 1)


def test_opus_prices_are_pinned():
    assert PRICES["claude-opus-5-5"] == {
        "input": 4.00,
        "output": 20.00,
        "cache_write": 5.00,
        "cache_read": 0.20,
    }


def test_opus_cost_sums_all_four_buckets():
    c = cost_usd("claude-opus-5-5", 1_000_000, 1_000_000, 1_000_000, 1_000_000)
    assert c == pytest.approx(29.20)
