"""Test-only providers that return canned answers without any network call."""

from __future__ import annotations

import math
import zlib
from collections.abc import Sequence

from elusion.ask.embed import Embedded
from elusion.ask.models import EMBEDDING_DIM
from elusion.review.pricing import register_free
from elusion.review.providers import (
    FirstRead,
    Grade,
    Grading,
    Judgement,
    Label,
    Reading,
    Review,
    Verdict,
)


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


class FakeGrader:
    """Test-only grader: answers `sound` from a predicate on the paragraph text.

    `calls` records (system, paragraph, verdict) exactly as sent.
    """

    name = "fake"

    def __init__(self, model: str = "fake-grader", sound=lambda text: True) -> None:
        self.model = model
        self.sound = sound
        self.calls: list[tuple[str, str, Verdict]] = []

    def grade(self, system: str, paragraph: str, verdict: Verdict) -> Grading:
        self.calls.append((system, paragraph, verdict))
        return Grading(
            grade=Grade(sound=self.sound(paragraph), rationale="canned"),
            model=self.model,
            input_tokens=100,
            output_tokens=20,
            cost_usd=0.0,
        )


class FakeReader:
    """Test-only first reader: answers from a function of the message it was sent.

    The function returns a `Reading`, or an exception, which is raised. `calls` records
    (system, message) exactly as sent.
    """

    name = "fake"

    def __init__(self, answer, model: str = "claude-haiku-5-5") -> None:
        self.model = model
        self.answer = answer
        self.calls: list[tuple[str, str]] = []

    def read(self, system: str, message: str) -> FirstRead:
        self.calls.append((system, message))
        result: Reading | Exception = self.answer(message)
        if isinstance(result, Exception):
            raise result
        return FirstRead(
            reading=result, model=self.model, input_tokens=100, output_tokens=20, cost_usd=0.0
        )


class FakeEmbedder:
    """Test-only embedder: a text becomes a vector of word counts, scaled to length 1.

    The word is hashed with `zlib.crc32` (Python's `hash` changes between runs), so the same
    text always gives the same vector, and two texts that share words get nearby vectors.
    `calls` records every list of texts it was sent; `fail_on_call=2` raises `RuntimeError`
    on its second call, to test a crash between two batches. Its model is free.
    """

    name = "fake"

    def __init__(
        self,
        model: str = "fake-embedder",
        dim: int = EMBEDDING_DIM,
        fail_on_call: int | None = None,
    ) -> None:
        register_free(model)
        self.model = model
        self.dim = dim
        self.fail_on_call = fail_on_call
        self.calls: list[list[str]] = []

    def vector(self, text: str) -> list[float]:
        counts = [0.0] * self.dim
        for word in text.split():
            counts[zlib.crc32(word.lower().encode()) % self.dim] += 1.0
        norm = math.sqrt(sum(c * c for c in counts)) or 1.0
        return [c / norm for c in counts]

    def embed(self, texts: Sequence[str]) -> Embedded:
        self.calls.append(list(texts))
        if self.fail_on_call is not None and len(self.calls) == self.fail_on_call:
            raise RuntimeError(f"fake embedder failed on call {len(self.calls)}")
        return Embedded(
            vectors=[self.vector(t) for t in texts],
            model=self.model,
            input_tokens=sum(len(t.split()) for t in texts),
            cost_usd=0.0,
        )
