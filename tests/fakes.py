"""Test-only providers that return canned answers without any network call."""

from __future__ import annotations

from elusion.review.providers import Judgement, Label, Review, Verdict


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
            model=self.model,
            input_tokens=100,
            output_tokens=20,
            cost_usd=0.0,
        )


class ListIdSource:
    """In-memory stand-in for KafkaIdSource: hands out ids in order, records commits.

    `committed` is how many ids have been committed, like a consumer group's offset.
    """

    def __init__(self, ids: list[str]) -> None:
        self.ids = list(ids)
        self.position = 0
        self.committed = 0
        self.closed = False

    def take(self, n: int, idle_timeout_s: float = 5.0) -> list[str]:
        got = self.ids[self.position : self.position + n]
        self.position += len(got)
        return got

    def commit(self) -> None:
        self.committed = self.position

    def close(self) -> None:
        self.closed = True


class FakeJudge:
    """Test-only judge: answers `relevant` from a predicate on the paragraph text.

    `calls` records exactly what the judge was sent, so tests can check it saw the
    paragraph text and nothing else.
    """

    name = "fake"

    def __init__(self, model: str = "fake-judge", relevant=lambda text: True) -> None:
        self.model = model
        self.relevant = relevant
        self.calls: list[tuple[str, str]] = []

    def label(self, system: str, paragraph: str) -> Judgement:
        self.calls.append((system, paragraph))
        return Judgement(
            label=Label(relevant=self.relevant(paragraph), rationale="canned"),
            model=self.model,
            input_tokens=100,
            output_tokens=20,
            cost_usd=0.0,
        )
