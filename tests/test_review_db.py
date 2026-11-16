import uuid

import pytest
import sqlalchemy as sa

from elusion.collect import db as collect_db
from elusion.collect.db import make_engine
from elusion.config import settings
from elusion.review.db import (
    ensure_schema,
    finish_job,
    get_job,
    insert_scores,
    provider_for,
    review_jobs,
    score_row,
    scores,
    scores_cost_usd,
    scores_for,
    start_job,
    unscored_ids,
    update_job,
    verdicts_for,
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
            "busy_s": 0.0,
            "run_envs": {},
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


def test_provider_for_returns_the_pairs_provider_or_none() -> None:
    engine = make_engine(settings.database_url)
    ensure_schema(engine)
    model = "m-" + uuid.uuid4().hex[:8]
    assert provider_for(engine, model, "v1") is None
    row = {**score_row("p" + uuid.uuid4().hex[:15], "v1", "anthropic", _review()), "model": model}
    try:
        insert_scores(engine, [row])
        assert provider_for(engine, model, "v1") == "anthropic"
        assert provider_for(engine, model, "v2") is None
    finally:
        with engine.begin() as conn:
            conn.execute(sa.delete(scores).where(scores.c.model == model))


def test_verdicts_for_returns_rows_at_or_above_min_score_as_verdicts() -> None:
    engine = make_engine(settings.database_url)
    ensure_schema(engine)
    model = "m-" + uuid.uuid4().hex[:8]
    ids = {s: "v" + uuid.uuid4().hex[:15] for s in (1, 2, 4)}
    rows = []
    for s, i in ids.items():
        r = score_row(i, "v1", "anthropic", _review())
        rows.append({**r, "model": model, "score": s, "rationale": f"r{s}", "quote": f"q{s}"})
    # qualifying scores without a usable rationale are skipped, not graded
    for rationale in (None, ""):
        r = score_row("v" + uuid.uuid4().hex[:15], "v1", "anthropic", _review())
        rows.append({**r, "model": model, "score": 4, "rationale": rationale, "quote": "q"})
    try:
        insert_scores(engine, rows)
        got = verdicts_for(engine, model, "v1", min_score=2)
        assert got == {
            ids[2]: Verdict(score=2, rationale="r2", quote="q2"),
            ids[4]: Verdict(score=4, rationale="r4", quote="q4"),
        }
        assert verdicts_for(engine, model, "v2", min_score=0) == {}
    finally:
        with engine.begin() as conn:
            conn.execute(sa.delete(scores).where(scores.c.model == model))


def test_jobs_summary_busy_time_sums_each_job_not_the_span() -> None:
    """A 10-minute job, a 2-second failed start an hour later: 602 s busy, not ~70 min."""
    from datetime import UTC, datetime, timedelta

    from elusion.review.db import jobs_summary

    engine = make_engine(settings.database_url)
    ensure_schema(engine)
    model = "m-" + uuid.uuid4().hex[:8]
    t0 = datetime(2026, 11, 6, 15, 0, tzinfo=UTC)

    def place(job_id: str, start: datetime, end: datetime | None) -> None:
        with engine.begin() as conn:
            conn.execute(
                sa.update(review_jobs)
                .where(review_jobs.c.job_id == job_id)
                .values(started_at=start, finished_at=end)
            )

    try:
        a = start_job(engine, criterion="c", model=model, prompt_version="c/v2", sample_size=5)
        b = start_job(engine, criterion="c", model=model, prompt_version="c/v2", sample_size=5)
        place(a, t0, t0 + timedelta(minutes=10))
        place(b, t0 + timedelta(hours=1), t0 + timedelta(hours=1, seconds=2))
        assert jobs_summary(engine, model, "c/v2")["busy_s"] == 602.0

        c = start_job(engine, criterion="c", model=model, prompt_version="c/v2", sample_size=5)
        place(c, datetime.now(UTC) - timedelta(seconds=60), None)
        busy = jobs_summary(engine, model, "c/v2")["busy_s"]
        assert 662.0 <= busy < 662.0 + 30  # the running job counts up to now
    finally:
        with engine.begin() as conn:
            conn.execute(sa.delete(review_jobs).where(review_jobs.c.model == model))


# review_jobs as it was before the run_env column: what an existing database holds.
_OLD_REVIEW_JOBS = """
CREATE TABLE "{schema}".review_jobs (
    job_id uuid PRIMARY KEY,
    criterion text NOT NULL,
    model text NOT NULL,
    prompt_version text NOT NULL,
    sample_size integer,
    started_at timestamptz DEFAULT now(),
    finished_at timestamptz,
    status text NOT NULL DEFAULT 'running',
    docs_scored integer NOT NULL DEFAULT 0,
    cost_usd numeric(10, 6) NOT NULL DEFAULT 0
)
"""


def test_ensure_schema_adds_run_env_to_a_table_created_without_it() -> None:
    """In a scratch Postgres schema, so the real review_jobs table is not the fixture."""
    schema = "t_" + uuid.uuid4().hex[:8]
    admin = make_engine(settings.database_url)
    url = settings.database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = sa.create_engine(url, connect_args={"options": f"-csearch_path={schema}"})

    def columns() -> set[str]:
        stmt = sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = :s AND table_name = 'review_jobs'"
        )
        with admin.connect() as conn:
            return {r[0] for r in conn.execute(stmt, {"s": schema})}

    try:
        with admin.begin() as conn:
            conn.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
            conn.execute(sa.text(_OLD_REVIEW_JOBS.format(schema=schema)))
        assert "run_env" not in columns()
        ensure_schema(engine)
        assert "run_env" in columns()
        ensure_schema(engine)  # a second call changes nothing and does not fail
        assert "run_env" in columns()
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(sa.text(f'DROP SCHEMA "{schema}" CASCADE'))


def test_start_job_stores_run_env_and_defaults_to_local() -> None:
    engine = make_engine(settings.database_url)
    ensure_schema(engine)
    model = "m-" + uuid.uuid4().hex[:8]
    try:
        a = start_job(engine, criterion="c", model=model, prompt_version="v1", sample_size=1)
        b = start_job(
            engine,
            criterion="c",
            model=model,
            prompt_version="v1",
            sample_size=1,
            run_env="aks-elusion",
        )
        assert get_job(engine, a)["run_env"] == "local"
        assert get_job(engine, b)["run_env"] == "aks-elusion"
    finally:
        with engine.begin() as conn:
            conn.execute(sa.delete(review_jobs).where(review_jobs.c.model == model))


def test_jobs_summary_counts_jobs_per_run_env_and_reads_null_as_local() -> None:
    from elusion.review.db import jobs_summary

    engine = make_engine(settings.database_url)
    ensure_schema(engine)
    model = "m-" + uuid.uuid4().hex[:8]
    job = dict(criterion="c", model=model, prompt_version="c/v2", sample_size=1)
    try:
        start_job(engine, **job)
        start_job(engine, **job, run_env="aks-elusion")
        old = start_job(engine, **job)
        with engine.begin() as conn:  # a row from before the column existed
            conn.execute(
                sa.update(review_jobs).where(review_jobs.c.job_id == old).values(run_env=None)
            )
        assert jobs_summary(engine, model, "c/v2")["run_envs"] == {"local": 2, "aks-elusion": 1}
    finally:
        with engine.begin() as conn:
            conn.execute(sa.delete(review_jobs).where(review_jobs.c.model == model))


def _file_documents(engine, year_of: dict[str, int]) -> None:
    """Give each doc id a `documents` row with that filing year (the year lives there only)."""
    collect_db.ensure_schema(engine)
    rows = [
        {"doc_id": d, "cik": "0", "year": y, "paragraph_index": 0, "word_count": 1}
        for d, y in year_of.items()
    ]
    with engine.begin() as conn:
        conn.execute(sa.insert(collect_db.documents), rows)


def _unfile_documents(engine, doc_ids) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.delete(collect_db.documents).where(collect_db.documents.c.doc_id.in_(list(doc_ids)))
        )


def _scored_in_three_years(engine, model: str) -> dict[int, str]:
    """One document per filing year 2018, 2019, 2020, each scored under (model, 'v1')."""
    ids = {y: "y" + uuid.uuid4().hex[:15] for y in (2018, 2019, 2020)}
    _file_documents(engine, {i: y for y, i in ids.items()})
    rows = []
    for y, i in ids.items():
        r = score_row(i, "v1", "anthropic", _review())
        rows.append({**r, "model": model, "score": y - 2016, "cost_usd": (y - 2017) / 100})
    insert_scores(engine, rows)
    return ids


def test_scores_for_keeps_only_paragraphs_filed_in_the_given_years() -> None:
    engine = make_engine(settings.database_url)
    ensure_schema(engine)
    model = "m-" + uuid.uuid4().hex[:8]
    ids = _scored_in_three_years(engine, model)
    try:
        assert scores_for(engine, model, "v1", years=[2020]) == {ids[2020]: 4}
        assert scores_for(engine, model, "v1", years=[2019, 2018]) == {ids[2018]: 2, ids[2019]: 3}
        assert scores_for(engine, model, "v1", years=[2017]) == {}
        assert len(scores_for(engine, model, "v1")) == 3  # no years: the whole pair
    finally:
        _unfile_documents(engine, ids.values())
        with engine.begin() as conn:
            conn.execute(sa.delete(scores).where(scores.c.model == model))


def test_verdicts_for_keeps_only_paragraphs_filed_in_the_given_years() -> None:
    engine = make_engine(settings.database_url)
    ensure_schema(engine)
    model = "m-" + uuid.uuid4().hex[:8]
    ids = _scored_in_three_years(engine, model)
    try:
        got = verdicts_for(engine, model, "v1", min_score=0, years=[2020])
        assert got == {ids[2020]: Verdict(score=4, rationale="r", quote="q")}
        assert set(verdicts_for(engine, model, "v1", min_score=3, years=[2018, 2019])) == {
            ids[2019]
        }
    finally:
        _unfile_documents(engine, ids.values())
        with engine.begin() as conn:
            conn.execute(sa.delete(scores).where(scores.c.model == model))


def test_scores_cost_usd_sums_the_cost_of_the_given_years_scores() -> None:
    engine = make_engine(settings.database_url)
    ensure_schema(engine)
    model = "m-" + uuid.uuid4().hex[:8]
    assert scores_cost_usd(engine, model, "v1", years=[2018]) == 0.0
    ids = _scored_in_three_years(engine, model)  # costs 0.01, 0.02, 0.03
    try:
        assert scores_cost_usd(engine, model, "v1", years=[2018, 2019]) == pytest.approx(0.03)
        assert scores_cost_usd(engine, model, "v1", years=[2020]) == pytest.approx(0.03)
        assert scores_cost_usd(engine, model, "v2", years=[2020]) == 0.0
    finally:
        _unfile_documents(engine, ids.values())
        with engine.begin() as conn:
            conn.execute(sa.delete(scores).where(scores.c.model == model))


def test_jobs_summary_with_years_keeps_the_jobs_that_scored_that_population() -> None:
    """Jobs carry no year: one belongs if a score of those years falls inside its window."""
    from datetime import UTC, datetime, timedelta

    from elusion.review.db import jobs_summary

    engine = make_engine(settings.database_url)
    ensure_schema(engine)
    model = "m-" + uuid.uuid4().hex[:8]
    ids = _scored_in_three_years(engine, model)
    t0 = datetime(2026, 11, 6, 15, 0, tzinfo=UTC)
    hour = timedelta(hours=1)

    def job(start: datetime, seconds: int, cost: float, run_env: str) -> None:
        j = start_job(
            engine, criterion="c", model=model, prompt_version="v1", sample_size=1, run_env=run_env
        )
        end = start + timedelta(seconds=seconds)
        with engine.begin() as conn:
            conn.execute(
                sa.update(review_jobs)
                .where(review_jobs.c.job_id == j)
                .values(started_at=start, finished_at=end, cost_usd=cost)
            )

    def scored_at(year: int, when: datetime) -> None:
        with engine.begin() as conn:
            conn.execute(
                sa.update(scores)
                .where(scores.c.model == model, scores.c.doc_id == ids[year])
                .values(scored_at=when)
            )

    try:
        job(t0, 600, 0.50, "local")  # scored the 2018 and 2019 paragraphs
        job(t0 + hour, 2, 0.25, "aks-elusion")  # a failed start: no score at all
        job(t0 + 2 * hour, 300, 1.00, "aks-elusion")  # scored the 2020 paragraph
        scored_at(2018, t0 + timedelta(seconds=5))
        scored_at(2019, t0 + timedelta(seconds=600))  # on the job's last instant: inside
        scored_at(2020, t0 + 2 * hour + timedelta(seconds=10))

        new = jobs_summary(engine, model, "v1", years=[2020])
        assert (new["jobs"], new["busy_s"], new["cost_usd"]) == (1, 300.0, 1.0)
        assert new["run_envs"] == {"aks-elusion": 1}
        assert new["started_at"] == t0 + 2 * hour

        old = jobs_summary(engine, model, "v1", years=[2018, 2019])
        assert (old["jobs"], old["busy_s"], old["run_envs"]) == (1, 600.0, {"local": 1})

        assert jobs_summary(engine, model, "v1", years=[2017])["jobs"] == 0
        assert jobs_summary(engine, model, "v1")["jobs"] == 3  # no years: every job of the pair
    finally:
        _unfile_documents(engine, ids.values())
        with engine.begin() as conn:
            conn.execute(sa.delete(scores).where(scores.c.model == model))
            conn.execute(sa.delete(review_jobs).where(review_jobs.c.model == model))


def test_jobs_summary_with_years_reports_an_unfinished_job_that_scored_them() -> None:
    from datetime import UTC, datetime, timedelta

    from elusion.review.db import jobs_summary

    engine = make_engine(settings.database_url)
    ensure_schema(engine)
    model = "m-" + uuid.uuid4().hex[:8]
    ids = _scored_in_three_years(engine, model)  # scored_at = now
    try:
        j = start_job(engine, criterion="c", model=model, prompt_version="v1", sample_size=1)
        with engine.begin() as conn:
            conn.execute(
                sa.update(review_jobs)
                .where(review_jobs.c.job_id == j)
                .values(started_at=datetime.now(UTC) - timedelta(minutes=5))
            )
        got = jobs_summary(engine, model, "v1", years=[2020])
        assert got["unfinished"] is True
        assert got["unfinished_ids"] == [j]
    finally:
        _unfile_documents(engine, ids.values())
        with engine.begin() as conn:
            conn.execute(sa.delete(scores).where(scores.c.model == model))
            conn.execute(sa.delete(review_jobs).where(review_jobs.c.model == model))
