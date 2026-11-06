"""The `scores` and `review_jobs` tables: what makes a review run idempotent and resumable.

`scores` holds one row per (doc_id, model, prompt_version): the primary key IS the
idempotency rule. Before spending tokens, the review flow asks `unscored_ids` which of a
batch of paragraphs have no row for the current (model, prompt_version) pair, and only
sends those to the LLM. A re-run therefore finds nothing to do, while a new prompt
version (or a new model) correctly counts as a new review of the same paragraphs.
`insert_scores` uses `ON CONFLICT DO NOTHING` so that two overlapping runs cannot
double-insert either.

`review_jobs` is the job table: one row per run, created by `start_job`, bumped as
batches finish (`update_job` adds to the counters rather than overwriting them), and
closed by `finish_job` with status 'finished' or 'failed'. It records what was asked
(criterion, model, prompt version, sample size) and what happened (documents scored,
dollars spent), so a crashed run can be inspected and re-started.

There is deliberately no foreign key from `scores.doc_id` to `documents.doc_id`: it keeps
this module independent of the collect module (own MetaData, own `ensure_schema`).
`job_id` is generated client-side with uuid4, so the id is known without a round trip.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from typing import TypedDict

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID, insert
from sqlalchemy.engine import Engine

from elusion.collect.db import make_engine  # noqa: F401  (re-exported for callers)
from elusion.review.providers import Review, Verdict

metadata = sa.MetaData()

scores = sa.Table(
    "scores",
    metadata,
    sa.Column("doc_id", sa.String(16), primary_key=True),
    sa.Column("model", sa.Text, primary_key=True),
    sa.Column("prompt_version", sa.Text, primary_key=True),
    sa.Column("score", sa.SmallInteger, nullable=False),
    sa.Column("rationale", sa.Text),
    sa.Column("quote", sa.Text),
    sa.Column("input_tokens", sa.Integer),
    sa.Column("output_tokens", sa.Integer),
    sa.Column("cache_creation_input_tokens", sa.Integer, server_default="0"),
    sa.Column("cache_read_input_tokens", sa.Integer, server_default="0"),
    sa.Column("cost_usd", sa.Numeric(10, 6)),
    sa.Column("provider", sa.Text),
    sa.Column("scored_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
)

review_jobs = sa.Table(
    "review_jobs",
    metadata,
    sa.Column("job_id", UUID(as_uuid=False), primary_key=True),
    sa.Column("criterion", sa.Text, nullable=False),
    sa.Column("model", sa.Text, nullable=False),
    sa.Column("prompt_version", sa.Text, nullable=False),
    sa.Column("sample_size", sa.Integer),
    sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    sa.Column("finished_at", sa.DateTime(timezone=True)),
    sa.Column("status", sa.Text, nullable=False, server_default="running"),
    sa.Column("docs_scored", sa.Integer, nullable=False, server_default="0"),
    sa.Column("cost_usd", sa.Numeric(10, 6), nullable=False, server_default="0"),
)


class ScoreRow(TypedDict):
    doc_id: str
    model: str
    prompt_version: str
    score: int
    rationale: str
    quote: str
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    cost_usd: float
    provider: str


def ensure_schema(engine: Engine) -> None:
    metadata.create_all(engine)


def score_row(doc_id: str, prompt_version: str, provider_name: str, review: Review) -> ScoreRow:
    """Build a ScoreRow from a provider Review; `model` is the API-echoed snapshot string."""
    return {
        "doc_id": doc_id,
        "model": review.model,
        "prompt_version": prompt_version,
        "score": review.verdict.score,
        "rationale": review.verdict.rationale,
        "quote": review.verdict.quote,
        "input_tokens": review.input_tokens,
        "output_tokens": review.output_tokens,
        "cache_creation_input_tokens": review.cache_creation_input_tokens,
        "cache_read_input_tokens": review.cache_read_input_tokens,
        "cost_usd": review.cost_usd,
        "provider": provider_name,
    }


def unscored_ids(engine: Engine, ids: Sequence[str], model: str, prompt_version: str) -> list[str]:
    """The subset of `ids` with no scores row for (model, prompt_version), in input order.

    One query: the ids are sent as a single array, `unnest ... WITH ORDINALITY` turns it
    back into rows (remembering each id's position), and NOT EXISTS drops those already
    scored.
    """
    if not ids:
        return []
    cand = (
        sa.func.unnest(sa.bindparam("ids", list(ids), type_=ARRAY(sa.String)))
        .table_valued(sa.column("doc_id", sa.String), with_ordinality="pos")
        .render_derived()
    )
    scored = (
        sa.select(sa.literal(1))
        .where(
            scores.c.doc_id == cand.c.doc_id,
            scores.c.model == model,
            scores.c.prompt_version == prompt_version,
        )
        .exists()
    )
    stmt = sa.select(cand.c.doc_id).where(~scored).order_by(cand.c.pos)
    with engine.connect() as conn:
        return [r[0] for r in conn.execute(stmt)]


def scores_for(engine: Engine, model: str, prompt_version: str) -> dict[str, int]:
    """Every doc_id scored under (model, prompt_version), mapped to its 0-4 score."""
    stmt = sa.select(scores.c.doc_id, scores.c.score).where(
        scores.c.model == model, scores.c.prompt_version == prompt_version
    )
    with engine.connect() as conn:
        return {doc_id: score for doc_id, score in conn.execute(stmt)}


def verdicts_for(
    engine: Engine, model: str, prompt_version: str, min_score: int
) -> dict[str, Verdict]:
    """Every doc under (model, prompt_version) scored >= min_score, as its full Verdict.

    Rows whose rationale is NULL or empty are left out: there is no reason to grade.
    """
    stmt = sa.select(scores.c.doc_id, scores.c.score, scores.c.rationale, scores.c.quote).where(
        scores.c.model == model,
        scores.c.prompt_version == prompt_version,
        scores.c.score >= min_score,
        scores.c.rationale.is_not(None),
        scores.c.rationale != "",
    )
    with engine.connect() as conn:
        return {
            doc_id: Verdict(score=score, rationale=rationale, quote=quote)
            for doc_id, score, rationale, quote in conn.execute(stmt)
        }


def provider_for(engine: Engine, model: str, prompt_version: str) -> str | None:
    """The provider that produced the pair's scores (None if no rows; first by name if mixed)."""
    stmt = (
        sa.select(scores.c.provider)
        .where(scores.c.model == model, scores.c.prompt_version == prompt_version)
        .distinct()
        .order_by(scores.c.provider)
    )
    with engine.connect() as conn:
        found = [p for (p,) in conn.execute(stmt) if p]
    return found[0] if found else None


def insert_scores(engine: Engine, rows: Iterable[ScoreRow]) -> int:
    """Insert score rows, skipping existing keys. Returns rows actually inserted."""
    batch = list(rows)
    if not batch:
        return 0
    stmt = (
        insert(scores)
        .values(batch)
        .on_conflict_do_nothing(index_elements=["doc_id", "model", "prompt_version"])
        .returning(scores.c.doc_id)
    )
    with engine.begin() as conn:
        return len(conn.execute(stmt).all())


def start_job(
    engine: Engine, *, criterion: str, model: str, prompt_version: str, sample_size: int
) -> str:
    job_id = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(
            insert(review_jobs).values(
                job_id=job_id,
                criterion=criterion,
                model=model,
                prompt_version=prompt_version,
                sample_size=sample_size,
                status="running",
            )
        )
    return job_id


def update_job(engine: Engine, job_id: str, *, docs_scored: int, cost_usd: float) -> None:
    """Add to the job's counters (not replace them)."""
    stmt = (
        sa.update(review_jobs)
        .where(review_jobs.c.job_id == job_id)
        .values(
            docs_scored=review_jobs.c.docs_scored + docs_scored,
            cost_usd=review_jobs.c.cost_usd + cost_usd,
        )
    )
    with engine.begin() as conn:
        conn.execute(stmt)


def finish_job(engine: Engine, job_id: str, status: str = "finished") -> None:
    stmt = (
        sa.update(review_jobs)
        .where(review_jobs.c.job_id == job_id)
        .values(status=status, finished_at=sa.func.now())
    )
    with engine.begin() as conn:
        conn.execute(stmt)


def jobs_summary(engine: Engine, model: str, prompt_version: str) -> dict:
    """What every review_jobs row for (model, prompt_version) adds up to.

    Returns jobs (row count), cost_usd (sum), started_at (earliest start), finished_at
    (latest finish, None if none finished), unfinished (some row has no finished_at)
    and unfinished_ids (those rows' job ids, sorted: a running job, or a killed one).
    A crashed and resumed review is several rows, so cost and wall-clock span them all.
    """
    j = review_jobs.c
    stmt = sa.select(
        sa.func.count(),
        sa.func.coalesce(sa.func.sum(j.cost_usd), 0),
        sa.func.min(j.started_at),
        sa.func.max(j.finished_at),
        sa.func.count() - sa.func.count(j.finished_at),
    ).where(j.model == model, j.prompt_version == prompt_version)
    with engine.connect() as conn:
        jobs, cost, started, finished, unfinished = conn.execute(stmt).one()
        open_ids = conn.execute(
            sa.select(j.job_id)
            .where(j.model == model, j.prompt_version == prompt_version, j.finished_at.is_(None))
            .order_by(j.job_id)
        ).scalars()
        open_ids = [str(i) for i in open_ids]
    return {
        "jobs": jobs,
        "cost_usd": float(cost),
        "started_at": started,
        "finished_at": finished,
        "unfinished": unfinished > 0,
        "unfinished_ids": open_ids,
    }


def get_job(engine: Engine, job_id: str) -> dict:
    with engine.connect() as conn:
        row = conn.execute(sa.select(review_jobs).where(review_jobs.c.job_id == job_id)).one()
    return dict(row._mapping)
