"""Price table for LLM calls, in one place so cost numbers are auditable.

LLM APIs bill per token, with different rates for tokens read in (input), tokens generated
(output), and tokens of a prompt prefix that is stored in or served from the provider's
prompt cache. A cached prefix is written once (slightly dearer than normal input) and then
read cheaply, which is why 20k calls sharing one system prompt cost little. The API reports
these as four additive counts: `input_tokens` excludes cached tokens, so the buckets never
overlap and cost is simply the sum of count x rate.

Prices are USD per million tokens, keyed by model id. Update here when prices change.
"""

from __future__ import annotations

PRICES: dict[str, dict[str, float]] = {
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_write": 0.125, "cache_read": 0.01},
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_write": 5.00, "cache_read": 0.20},
    # OpenAI caches prompt prefixes automatically and has no cache-write charge: cache_write is 0.
    "gpt-6-luna": {"input": 0.10, "output": 0.50, "cache_write": 0.0, "cache_read": 0.01},
}


def cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
) -> float:
    try:
        p = PRICES[model]
    except KeyError:
        raise KeyError(f"no price for model {model!r}; add it to review/pricing.py") from None
    return (
        input_tokens * p["input"]
        + output_tokens * p["output"]
        + cache_creation_input_tokens * p["cache_write"]
        + cache_read_input_tokens * p["cache_read"]
    ) / 1_000_000


def register_free(model: str) -> None:
    """Add an all-zero price row (a local model): cost_usd returns 0.0 instead of raising."""
    PRICES.setdefault(model, {"input": 0.0, "output": 0.0, "cache_write": 0.0, "cache_read": 0.0})
