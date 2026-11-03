"""Test-only provider that returns a canned Verdict without any network call."""

from __future__ import annotations

from elusion.review.providers import Review, Verdict


class FakeProvider:
    name = "fake"

    def __init__(self, model: str = "fake-model", verdict: Verdict | None = None) -> None:
        self.model = model
        self.verdict = verdict or Verdict(score=3, rationale="canned", quote="canned quote")
        self.calls: list[tuple[str, str]] = []

    def review(self, system: str, paragraph: str) -> Review:
        self.calls.append((system, paragraph))
        return Review(
            verdict=self.verdict,
            model="fake-model",
            input_tokens=100,
            output_tokens=20,
            cost_usd=0.0,
        )
