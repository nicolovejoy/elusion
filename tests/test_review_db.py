import uuid

import sqlalchemy as sa

from elusion.collect.db import make_engine
from elusion.config import settings
from elusion.review.db import (
    ensure_schema,
    finish_job,
    get_job,
    insert_scores,
    review_jobs,
    score_row,
    scores,
    start_job,
    unscored_ids,
    update_job,
)
from elusion.review.providers import Review, Verdict
from tests.conftest import requires_service

pytestmark = requires_service("localhost", 5433, "postgres")


def _review() -> Review:
    return Review(
        verdict=Verdict(score=3, rationale="r", quote="q"),
        model="claude-haiku-5-5-20260101",
        input_tokens=100,
        output_tokens=20,
        cost_usd=0.000123,
        cache_creation_input_tokens=5,
        cache_read_input_tokens=7,
    )


def test_insert_scores_and_unscored_ids() -> None:
    engine = make_engine(settings.database_url)
    ensure_schema(engine)
    model = "m-" + uuid.uuid4().hex[:8]
    ids = [uuid.uuid4().hex[:16] for _ in range(3)]
    rows = [score_row(i, "v1", "anthropic", _review()) for i in ids]
    try:
        assert unscored_ids(engine, [], model, "v1") == []
        rows = [{**r, "model": model} for r in rows]
        assert unscored_ids(engine, ids, model, "v1") == ids
        assert insert_scores(engine, rows[:2]) == 2
        assert unscored_ids(engine, ids, model, "v1") == [ids[2]]
        assert unscored_ids(engine, list(reversed(ids)), model, "v1") == [ids[2]]
        assert insert_scores(engine, rows) == 1
        assert insert_scores(engine, rows) == 0
        assert unscored_ids(engine, ids, model, "v1") == []
        # a different prompt version is a different review
        assert unscored_ids(engine, ids, model, "v2") == ids
    finally:
        with engine.begin() as conn:
            conn.execute(sa.delete(scores).where(scores.c.model == model))


def test_unscored_ids_preserves_input_order() -> None:
    engine = make_engine(settings.database_url)
    ensure_schema(engine)
    ids = ["z" + uuid.uuid4().hex[:15], "a" + uuid.uuid4().hex[:15], "m" + uuid.uuid4().hex[:15]]
    assert unscored_ids(engine, ids, "nomodel-" + uuid.uuid4().hex[:6], "v1") == ids


def test_job_lifecycle() -> None:
    engine = make_engine(settings.database_url)
    ensure_schema(engine)
    job_id = start_job(engine, criterion="c", model="m", prompt_version="v1", sample_size=10)
    try:
        j = get_job(engine, job_id)
        assert j["status"] == "running" and j["docs_scored"] == 0 and j["finished_at"] is None
        update_job(engine, job_id, docs_scored=4, cost_usd=0.5)
        update_job(engine, job_id, docs_scored=6, cost_usd=0.25)
        j = get_job(engine, job_id)
        assert j["docs_scored"] == 10
        assert float(j["cost_usd"]) == 0.75
        finish_job(engine, job_id)
        j = get_job(engine, job_id)
        assert j["status"] == "finished" and j["finished_at"] is not None
        finish_job(engine, job_id, "failed")
        assert get_job(engine, job_id)["status"] == "failed"
    finally:
        with engine.begin() as conn:
            conn.execute(sa.delete(review_jobs).where(review_jobs.c.job_id == job_id))


def test_jobs_summary_sums_cost_and_spans_all_jobs() -> None:
    from elusion.review.db import jobs_summary

    engine = make_engine(settings.database_url)
    ensure_schema(engine)
    model = "m-" + uuid.uuid4().hex[:8]
    try:
        assert jobs_summary(engine, model, "c/v2") == {
            "jobs": 0,
            "cost_usd": 0.0,
            "started_at": None,
            "finished_at": None,
            "unfinished": False,
            "unfinished_ids": [],
        }
        a = start_job(engine, criterion="c", model=model, prompt_version="c/v2", sample_size=5)
        b = start_job(engine, criterion="c", model=model, prompt_version="c/v2", sample_size=5)
        other = start_job(engine, criterion="c", model=model, prompt_version="c/v1", sample_size=5)
        update_job(engine, a, docs_scored=5, cost_usd=0.25)
        update_job(engine, b, docs_scored=5, cost_usd=0.5)
        update_job(engine, other, docs_scored=5, cost_usd=9.0)
        finish_job(engine, a)
        s = jobs_summary(engine, model, "c/v2")
        assert (s["jobs"], s["cost_usd"], s["unfinished"]) == (2, 0.75, True)
        assert s["unfinished_ids"] == [b]
        assert s["started_at"] is not None and s["finished_at"] is not None
        finish_job(engine, b, "failed")
        s = jobs_summary(engine, model, "c/v2")
        assert s["unfinished"] is False and s["unfinished_ids"] == []
        assert s["finished_at"] == get_job(engine, b)["finished_at"]
        assert s["started_at"] == get_job(engine, a)["started_at"]
    finally:
        with engine.begin() as conn:
            conn.execute(sa.delete(review_jobs).where(review_jobs.c.model == model))
