import uuid

import pytest
import sqlalchemy as sa

from elusion.collect.db import make_engine
from elusion.config import settings
from elusion.review.db import ensure_schema, get_job, review_jobs, scores
from elusion.review.prompt import Prompt
from elusion.review.providers import Review, ReviewError
from tests.conftest import requires_service
from tests.fakes import FakeProvider, ListIdSource

# The flow writes to Postgres and reports its run to the compose Prefect server.
pytestmark = [
    requires_service("localhost", 5433, "postgres"),
    requires_service("localhost", 4200, "prefect server"),
]

TEXTS = {f"t{uuid.uuid4().hex[:15]}": f"paragraph {i} about cloud vendors" for i in range(5)}
IDS = list(TEXTS)


@pytest.fixture
def env(monkeypatch):
    """Patch the corpus and prompt loaders; yield a unique prompt version; clean up rows."""
    import elusion.flows.review as mod

    version = f"test/{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(mod, "load_texts", lambda corpus_dir: dict(TEXTS))
    monkeypatch.setattr(mod, "load_prompt", lambda c, v="v1": Prompt("system text", version))
    engine = make_engine(settings.database_url)
    ensure_schema(engine)
    yield engine, version
    with engine.begin() as conn:
        conn.execute(sa.delete(scores).where(scores.c.prompt_version == version))
        conn.execute(sa.delete(review_jobs).where(review_jobs.c.prompt_version == version))


def test_rerun_scores_nothing_twice(env) -> None:
    from elusion.flows.review import review

    engine, version = env
    provider = FakeProvider()
    ids = ListIdSource(IDS)
    s1 = review("cloud-concentration", sample=5, batch_size=2, provider=provider, ids=ids)
    assert (s1.docs_pulled, s1.docs_scored, s1.docs_skipped, s1.docs_failed) == (5, 5, 0, 0)
    assert len(provider.calls) == 5
    assert ids.committed == 5 and ids.closed
    job = get_job(engine, s1.job_id)
    assert job["status"] == "finished" and job["docs_scored"] == 5
    assert job["prompt_version"] == version and job["model"] == "fake-model"

    # Same ids again (e.g. a new consumer group): nothing is re-spent.
    s2 = review(
        "cloud-concentration", sample=5, batch_size=2, provider=provider, ids=ListIdSource(IDS)
    )
    assert (s2.docs_pulled, s2.docs_scored, s2.docs_skipped) == (5, 0, 5)
    assert len(provider.calls) == 5


def test_sample_limits_pull_and_dry_source_stops(env) -> None:
    from elusion.flows.review import review

    s = review(
        "cloud-concentration",
        sample=3,
        batch_size=2,
        provider=FakeProvider(),
        ids=ListIdSource(IDS),
    )
    assert s.docs_pulled == 3 and s.docs_scored == 3
    s = review(
        "cloud-concentration",
        sample=50,
        batch_size=2,
        provider=FakeProvider(),
        ids=ListIdSource(IDS),
    )
    assert s.docs_pulled == 5 and s.docs_scored == 2 and s.docs_skipped == 3


def test_duplicate_ids_in_a_batch_are_scored_once(env) -> None:
    from elusion.flows.review import review

    provider = FakeProvider()
    s = review(
        "cloud-concentration",
        sample=4,
        batch_size=4,
        provider=provider,
        ids=ListIdSource([IDS[0], IDS[0], IDS[1], IDS[1]]),
    )
    assert s.docs_pulled == 4 and s.docs_scored == 2 and len(provider.calls) == 2


class FlakyProvider(FakeProvider):
    """Raises ReviewError (billed, no verdict) for one paragraph."""

    def __init__(self, bad_text: str) -> None:
        super().__init__(model="claude-haiku-5-5")
        self.bad_text = bad_text

    def review(self, system: str, paragraph: str) -> Review:
        if paragraph == self.bad_text:
            self.calls.append((system, paragraph))
            raise ReviewError("refusal", self.model, 1_000_000, 0)
        return super().review(system, paragraph)


def test_review_error_counts_failed_and_its_cost(env) -> None:
    from elusion.flows.review import review

    engine, _ = env
    provider = FlakyProvider(TEXTS[IDS[2]])
    s = review(
        "cloud-concentration", sample=5, batch_size=5, provider=provider, ids=ListIdSource(IDS)
    )
    assert (s.docs_scored, s.docs_failed) == (4, 1)
    # 1M input tokens at $0.10/M; FakeProvider reviews cost 0.
    assert s.cost_usd == pytest.approx(0.10)
    job = get_job(engine, s.job_id)
    assert job["status"] == "finished" and float(job["cost_usd"]) == pytest.approx(0.10)
    with engine.connect() as conn:
        n = conn.execute(sa.select(sa.func.count()).where(scores.c.doc_id == IDS[2])).scalar()
    assert n == 0  # left unscored; retried only when its id is read again (new group)


class CostlyProvider(FakeProvider):
    """Each review costs $0.01, so the job's cost shows which reviews were accounted."""

    def review(self, system: str, paragraph: str) -> Review:
        r = super().review(system, paragraph)
        r.cost_usd = 0.01
        return r


class EchoMismatch(CostlyProvider):
    def review(self, system: str, paragraph: str) -> Review:
        r = super().review(system, paragraph)
        r.model = "fake-model-20990101"
        return r


def test_model_echo_mismatch_fails_job(env) -> None:
    from elusion.flows.review import review

    engine, version = env
    ids = ListIdSource(IDS)
    with pytest.raises(RuntimeError, match="fake-model-20990101"):
        review("cloud-concentration", sample=5, batch_size=5, provider=EchoMismatch(), ids=ids)
    assert ids.committed == 0 and ids.closed
    with engine.connect() as conn:
        job = conn.execute(
            sa.select(review_jobs).where(review_jobs.c.prompt_version == version)
        ).one()
        n = conn.execute(
            sa.select(sa.func.count()).where(scores.c.prompt_version == version)
        ).scalar()
    assert job.status == "failed" and n == 0
    assert float(job.cost_usd) == pytest.approx(0.01)  # the probe was billed, so recorded


class RefuseFirstThenMismatch(EchoMismatch):
    """First document: ReviewError on the pinned (priced) model; then mismatched echoes."""

    def __init__(self) -> None:
        super().__init__(model="claude-haiku-5-5")

    def review(self, system: str, paragraph: str) -> Review:
        if paragraph == TEXTS[IDS[0]]:
            raise ReviewError("refusal", self.model, 1_000_000, 0)
        return super().review(system, paragraph)


def test_model_echo_checked_even_when_first_document_fails(env) -> None:
    from elusion.flows.review import review

    engine, version = env
    ids = ListIdSource(IDS)
    with pytest.raises(RuntimeError, match="fake-model-20990101"):
        review(
            "cloud-concentration",
            sample=5,
            batch_size=5,
            provider=RefuseFirstThenMismatch(),
            ids=ids,
        )
    assert ids.committed == 0 and ids.closed
    with engine.connect() as conn:
        job = conn.execute(
            sa.select(review_jobs).where(review_jobs.c.prompt_version == version)
        ).one()
        n = conn.execute(
            sa.select(sa.func.count()).where(scores.c.prompt_version == version)
        ).scalar()
    assert job.status == "failed" and n == 0
    assert float(job.cost_usd) == pytest.approx(0.10 + 0.01)  # refusal + the next probe


class BrokenOnOne(CostlyProvider):
    """Raises a non-ReviewError (like a 400 for one bad paragraph) for IDS[2]."""

    def review(self, system: str, paragraph: str) -> Review:
        if paragraph == TEXTS[IDS[2]]:
            raise ValueError("bad request for this paragraph")
        return super().review(system, paragraph)


def test_unexpected_error_keeps_paid_work_and_fails(env) -> None:
    from elusion.flows.review import review

    engine, version = env
    ids = ListIdSource(IDS)
    provider = BrokenOnOne()
    with pytest.raises(ValueError, match="bad request"):
        review("cloud-concentration", sample=5, batch_size=5, provider=provider, ids=ids)
    assert len(provider.calls) == 4  # every other document was sent (and billed)
    assert ids.committed == 0 and ids.closed
    with engine.connect() as conn:
        job = conn.execute(
            sa.select(review_jobs).where(review_jobs.c.prompt_version == version)
        ).one()
        stored = {
            r[0]
            for r in conn.execute(
                sa.select(scores.c.doc_id).where(scores.c.prompt_version == version)
            )
        }
    assert stored == set(IDS) - {IDS[2]}
    assert job.status == "failed" and job.docs_scored == 4
    assert float(job.cost_usd) == pytest.approx(0.04)


BIG = {f"b{uuid.uuid4().hex[:15]}": f"big paragraph {i}" for i in range(30)}
BIG_IDS = list(BIG)


def test_budget_exceeded_stops_after_probe(env, monkeypatch) -> None:
    import elusion.flows.review as mod
    from elusion.flows.review import BudgetExceeded, review

    engine, version = env
    monkeypatch.setattr(mod, "load_texts", lambda corpus_dir: dict(BIG))
    ids = ListIdSource(BIG_IDS)
    provider = CostlyProvider()
    # mean $0.01 x 10 remaining = $0.10 projected > $0.05
    with pytest.raises(BudgetExceeded, match="refusing to continue"):
        review(
            "cloud-concentration",
            sample=30,
            batch_size=30,
            provider=provider,
            ids=ids,
            max_run_usd=0.05,
        )
    assert len(provider.calls) == 20
    assert ids.committed == 0 and ids.closed
    with engine.connect() as conn:
        job = conn.execute(
            sa.select(review_jobs).where(review_jobs.c.prompt_version == version)
        ).one()
        n = conn.execute(
            sa.select(sa.func.count()).where(scores.c.prompt_version == version)
        ).scalar()
    assert n == 20
    assert job.status == "failed" and job.docs_scored == 20
    assert float(job.cost_usd) == pytest.approx(0.20)


def test_budget_ok_reports_projection(env, monkeypatch) -> None:
    import elusion.flows.review as mod
    from elusion.flows.review import review

    engine, _ = env
    monkeypatch.setattr(mod, "load_texts", lambda corpus_dir: dict(BIG))
    s = review(
        "cloud-concentration",
        sample=30,
        batch_size=7,
        provider=CostlyProvider(),
        ids=ListIdSource(BIG_IDS),
        max_run_usd=100.0,
    )
    assert s.docs_scored == 30
    assert s.cost_projected_usd == pytest.approx(0.01 * (30 - 20), abs=1e-9)
    assert get_job(engine, s.job_id)["status"] == "finished"


def test_small_sample_has_no_projection(env) -> None:
    from elusion.flows.review import review

    s = review(
        "cloud-concentration",
        sample=5,
        batch_size=5,
        provider=FakeProvider(),
        ids=ListIdSource(IDS),
        max_run_usd=0.0,
    )
    assert s.docs_scored == 5 and s.cost_projected_usd is None


BIG40 = {f"c{uuid.uuid4().hex[:15]}": f"large paragraph {i}" for i in range(40)}
BIG40_IDS = list(BIG40)


class AlwaysRefuses(FakeProvider):
    """Every call raises ReviewError (billed $0.10, no verdict)."""

    def __init__(self) -> None:
        super().__init__(model="claude-haiku-5-5")

    def review(self, system: str, paragraph: str) -> Review:
        self.calls.append((system, paragraph))
        raise ReviewError("max_tokens", self.model, 1_000_000, 0)


def test_all_review_errors_still_trip_the_spend_guard(env, monkeypatch) -> None:
    import elusion.flows.review as mod
    from elusion.flows.review import BudgetExceeded, review

    engine, version = env
    monkeypatch.setattr(mod, "load_texts", lambda corpus_dir: dict(BIG40))
    ids = ListIdSource(BIG40_IDS)
    provider = AlwaysRefuses()
    with pytest.raises(BudgetExceeded, match="refusing to continue"):
        review(
            "cloud-concentration",
            sample=40,
            batch_size=40,
            provider=provider,
            ids=ids,
            max_run_usd=0.05,
        )
    assert len(provider.calls) == 20
    assert ids.committed == 0 and ids.closed
    with engine.connect() as conn:
        job = conn.execute(
            sa.select(review_jobs).where(review_jobs.c.prompt_version == version)
        ).one()
        n = conn.execute(
            sa.select(sa.func.count()).where(scores.c.prompt_version == version)
        ).scalar()
    assert n == 0 and job.status == "failed"
    assert float(job.cost_usd) == pytest.approx(20 * 0.10)


class Alternating(FakeProvider):
    """Odd calls raise ReviewError ($0.10); even calls succeed at $0.01."""

    def __init__(self) -> None:
        super().__init__(model="claude-haiku-5-5")

    def review(self, system: str, paragraph: str) -> Review:
        n = len(self.calls)
        if n % 2 == 0:
            self.calls.append((system, paragraph))
            raise ReviewError("max_tokens", self.model, 1_000_000, 0)
        r = super().review(system, paragraph)
        r.cost_usd = 0.01
        return r


def test_projection_averages_all_probe_attempts(env, monkeypatch) -> None:
    import elusion.flows.review as mod
    from elusion.flows.review import review

    monkeypatch.setattr(mod, "load_texts", lambda corpus_dir: dict(BIG40))
    s = review(
        "cloud-concentration",
        sample=40,
        batch_size=40,
        provider=Alternating(),
        ids=ListIdSource(BIG40_IDS),
        max_run_usd=100.0,
    )
    # 20 attempts: 10 errors at $0.10 and 10 reviews at $0.01; 10 documents scored then.
    mean = (10 * 0.10 + 10 * 0.01) / 20
    assert s.cost_projected_usd == pytest.approx(mean * (40 - 10), abs=1e-9)
    assert s.docs_scored == 10 + 10 and s.docs_failed == 10 + 10


def test_no_usable_verdict_in_probe_fails_job(env, monkeypatch) -> None:
    import elusion.flows.review as mod
    from elusion.flows.review import review

    engine, version = env
    monkeypatch.setattr(mod, "load_texts", lambda corpus_dir: dict(BIG40))
    provider = AlwaysRefuses()
    ids = ListIdSource(BIG40_IDS)
    with pytest.raises(RuntimeError, match="no usable verdict in 20 attempts"):
        review(
            "cloud-concentration",
            sample=40,
            batch_size=40,
            provider=provider,
            ids=ids,
            max_run_usd=100.0,
        )
    assert len(provider.calls) == 20 and ids.committed == 0 and ids.closed
    with engine.connect() as conn:
        job = conn.execute(
            sa.select(review_jobs).where(review_jobs.c.prompt_version == version)
        ).one()
    assert job.status == "failed"


def test_alias_model_echo_mismatch_stops_before_storing(env) -> None:
    """An alias pin is served under a dated id; the echo check stops before any score row."""
    from elusion.flows.review import review

    engine, version = env
    provider = FakeProvider(model="gpt-x-mini")

    def served_dated(system, paragraph):
        r = FakeProvider.review(provider, system, paragraph)
        r.model = "gpt-x-mini-2026-01-01"
        return r

    provider.review = served_dated
    with pytest.raises(RuntimeError, match="pin the served snapshot id"):
        review(
            "cloud-concentration", sample=5, batch_size=5, provider=provider, ids=ListIdSource(IDS)
        )
    with engine.connect() as conn:
        n = conn.execute(sa.select(sa.func.count()).where(scores.c.prompt_version == version))
        assert n.scalar_one() == 0


def test_real_openai_provider_served_drift_stops_before_storing(env) -> None:
    from types import SimpleNamespace as NS

    from elusion.flows.review import review
    from elusion.review.openai_provider import OpenAIProvider

    engine, version = env
    good = '{"score": 3, "rationale": "r", "quote": "q"}'
    resp = NS(
        status="completed",
        incomplete_details=None,
        model="gpt-6-luna-2026-12-01",
        output=[NS(type="message", content=[NS(type="output_text", text=good)])],
        output_text=good,
        usage=NS(input_tokens=100, output_tokens=10, input_tokens_details=NS(cached_tokens=0)),
    )
    client = NS(responses=NS(create=lambda **kw: resp))
    provider = OpenAIProvider("gpt-6-luna", client=client)
    with pytest.raises(RuntimeError, match=r"pin the served snapshot id \(--model or"):
        review(
            "cloud-concentration", sample=5, batch_size=5, provider=provider, ids=ListIdSource(IDS)
        )
    with engine.connect() as conn:
        n = conn.execute(sa.select(sa.func.count()).where(scores.c.prompt_version == version))
        assert n.scalar_one() == 0
