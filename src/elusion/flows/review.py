"""Prefect flow `review`: Kafka ids -> skip already scored -> LLM -> Postgres -> commit.

Over a plain loop, Prefect gives us a recorded run per invocation (parameters, state,
logs, duration) and one task run per batch in the UI, so a long run can be watched and a
failed one diagnosed after the fact. The code still runs in this process.

Ids come from the `documents.ingested` topic through a Kafka *consumer group* named
after (criterion, model, prompt version): the broker remembers how far that group has
read, so a re-run resumes and a new prompt version starts from the beginning. Ids are
pulled one batch at a time and the group's offsets are committed only after that batch's
scores are in Postgres. A kill between the insert and the commit means the batch is
read again, and the `unscored_ids` check then skips everything already paid for: Kafka
gives at-least-once delivery, Postgres makes it effectively once.

Each batch is scored by a thread pool (the work is network-bound, so threads are enough).
429s and 5xx are retried with backoff by the Anthropic SDK itself, so the task has no
Prefect retries. A refusal or truncated answer (`ReviewError`) is billed but yields no
score: it is logged, counted as failed, its cost is added to the job, and no row is
written, so the document stays unscored and is retried the next time its id is read.
Note that the batch's offsets are still committed, so the same consumer group has moved
past it: the retry happens under a new group (new prompt version, or a fresh read of the
topic), not on a plain re-run. Any other provider exception fails the run.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from prefect import flow, get_run_logger, task
from prefect.cache_policies import NO_CACHE

from elusion.config import settings
from elusion.review import db
from elusion.review.corpus import load_texts
from elusion.review.pricing import cost_usd
from elusion.review.prompt import load_prompt
from elusion.review.providers import AnthropicProvider, Provider, Review, ReviewError
from elusion.review.sampler import IdSource, KafkaIdSource, group_id


@dataclass
class ReviewSummary:
    """Counts for one run. `docs_skipped` = already scored for (model, prompt version);
    ids repeated inside one batch are pulled but scored once and not counted again."""

    job_id: str
    docs_pulled: int = 0
    docs_scored: int = 0
    docs_skipped: int = 0
    docs_failed: int = 0
    cost_usd: float = 0.0
    cost_projected_usd: float | None = None  # None if the run ended before PROBE_DOCS probes


class BudgetExceeded(RuntimeError):
    """The projected cost of the run is above the limit (ELUSION_MAX_RUN_USD)."""


# How many single-document reviews are scored (and kept) before the cost is projected.
PROBE_DOCS = 20


def project_cost(probe_costs: list[float], remaining: int) -> tuple[float, float]:
    """Return (mean cost per document, projected cost of `remaining` more documents).

    An upper bound: some of the `remaining` ids (sample minus documents scored so far)
    will turn out to be already scored for this (model, prompt version) and cost nothing.
    """
    mean = sum(probe_costs) / len(probe_costs)
    return mean, mean * remaining


@dataclass
class BatchResult:
    reviews: list[tuple[str, Review]] = field(default_factory=list)
    failed: int = 0  # ReviewErrors: billed, no verdict
    cost_usd: float = 0.0  # everything billed: reviews and ReviewErrors
    errors: list[tuple[str, Exception]] = field(default_factory=list)  # anything else


# NO_CACHE: Prefect 3 would otherwise hash the task inputs (provider, texts) for caching.
@task(cache_policy=NO_CACHE)
def score_batch(
    system: str, texts: dict[str, str], provider: Provider, concurrency: int
) -> BatchResult:
    """Review each (doc_id, text) concurrently; never raises for a single document.

    Every document is submitted up front, so one failure cannot stop the others from
    being sent and billed. Each outcome is therefore returned, not raised: the flow
    stores what was paid for and only then re-raises the first unexpected error.
    Passing a live provider object into a task works because Prefect 3 runs tasks
    in-process (threads), so nothing is pickled or sent to the server.
    """
    log = get_run_logger()
    result = BatchResult()

    def one(doc_id: str) -> tuple[str, Review | Exception]:
        try:
            return doc_id, provider.review(system, texts[doc_id])
        except Exception as e:  # ReviewError included; sorted out below
            return doc_id, e

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for doc_id, out in pool.map(one, texts):
            if isinstance(out, Review):
                result.reviews.append((doc_id, out))
                result.cost_usd += out.cost_usd
            elif isinstance(out, ReviewError):
                log.warning("no verdict for %s (stop_reason=%s)", doc_id, out.stop_reason)
                result.failed += 1
                try:
                    result.cost_usd += cost_usd(
                        out.model,
                        out.input_tokens,
                        out.output_tokens,
                        out.cache_creation_input_tokens,
                        out.cache_read_input_tokens,
                    )
                except KeyError as e:  # unpriced (e.g. unexpected echoed) model
                    result.errors.append((doc_id, e))
            else:
                log.error("review of %s raised %r", doc_id, out)
                result.errors.append((doc_id, out))
    return result


def check_model_echo(provider: Provider, review: Review) -> None:
    """Checked on every review before it is inserted (a string compare).

    Scores are stored under the model string the API echoes, but `unscored_ids` filters
    on the pinned id we requested. If they differ, every re-run would re-pay for every
    document, so stop before inserting anything.
    """
    if review.model != provider.model:
        raise RuntimeError(
            f"API served model {review.model!r} but the pinned model is {provider.model!r}; "
            "pin the served snapshot id (ELUSION_REVIEW_MODEL) so idempotency holds"
        )


@flow(name="review")
def review(
    criterion: str,
    sample: int,
    batch_size: int = 50,
    concurrency: int = 8,
    prompt_version: str = "v1",
    provider: Provider | None = None,
    ids: IdSource | None = None,
    max_run_usd: float | None = None,
) -> ReviewSummary:
    """Score up to `sample` ids from the topic against `criterion` (a criterion name).

    Spend guard: the first PROBE_DOCS documents are reviewed one at a time and kept.
    Their mean cost times the documents still to score is the projected cost of the
    run; above `max_run_usd` (default ELUSION_MAX_RUN_USD) the flow raises
    BudgetExceeded before any concurrent spend. The probe batch's offsets stay
    uncommitted, and its scores are already in Postgres, so a re-run skips them.
    """
    log = get_run_logger()
    if max_run_usd is None:
        max_run_usd = settings.max_run_usd
    prompt = load_prompt(criterion, prompt_version)
    provider = provider or AnthropicProvider(settings.review_model)
    engine = db.make_engine(settings.database_url)
    db.ensure_schema(engine)
    job_id = db.start_job(
        engine,
        criterion=criterion,
        model=provider.model,
        prompt_version=prompt.version,
        sample_size=sample,
    )
    summary = ReviewSummary(job_id=job_id)
    log.info("job %s: model=%s prompt=%s sample=%s", job_id, provider.model, prompt.version, sample)

    def settle(res: BatchResult) -> None:
        """Record what `res` cost and stored; raise if anything in it went wrong.

        Order matters: the spend reaches the job row before any raise, mismatched rows
        are never inserted, and good rows are inserted even if a sibling failed.
        """
        summary.cost_usd += res.cost_usd
        summary.docs_failed += res.failed
        try:
            for _, r in res.reviews:
                check_model_echo(provider, r)
        except RuntimeError:
            db.update_job(engine, job_id, docs_scored=0, cost_usd=res.cost_usd)
            raise
        rows = [db.score_row(doc_id, prompt.version, provider.name, r) for doc_id, r in res.reviews]
        inserted = db.insert_scores(engine, rows)
        db.update_job(engine, job_id, docs_scored=inserted, cost_usd=res.cost_usd)
        summary.docs_scored += inserted
        if res.errors:
            raise res.errors[0][1]

    try:
        if ids is None:
            group = group_id(criterion, provider.model, prompt.version)
            log.info("kafka consumer group %s", group)
            ids = KafkaIdSource(settings.kafka_bootstrap, group)
        t0 = time.monotonic()
        texts = load_texts(settings.corpus_dir)
        log.info("loaded %s paragraph texts in %.1fs", len(texts), time.monotonic() - t0)

        probe_costs: list[float] = []  # cost of each successful probe review
        # A run smaller than PROBE_DOCS has nothing worth projecting: one probe (the echo
        # check) and the rest concurrently.
        probe_target = PROBE_DOCS if sample >= PROBE_DOCS else 1
        k = 0
        while summary.docs_pulled < sample:
            pulled = ids.take(min(batch_size, sample - summary.docs_pulled))
            if not pulled:
                log.info("topic has no more ids for this group")
                break
            k += 1
            summary.docs_pulled += len(pulled)
            unique = list(dict.fromkeys(pulled))
            todo = db.unscored_ids(engine, unique, provider.model, prompt.version)
            skipped = len(unique) - len(todo)
            summary.docs_skipped += skipped
            log.info("batch %s: %s to score, %s skipped", k, len(todo), skipped)
            missing = [i for i in todo if i not in texts]
            if missing:
                raise KeyError(f"{len(missing)} ids not in the Parquet corpus, e.g. {missing[0]}")

            while len(probe_costs) < probe_target and todo:
                # Probe: score documents one at a time until PROBE_DOCS have a verdict,
                # so a model mismatch and a runaway cost are caught before any
                # concurrent spend.
                first = score_batch(prompt.text, {todo[0]: texts[todo[0]]}, provider, 1)
                settle(first)
                probe_costs.extend(r.cost_usd for _, r in first.reviews)
                todo = todo[1:]
                if len(probe_costs) == PROBE_DOCS and summary.cost_projected_usd is None:
                    mean, projected = project_cost(probe_costs, sample - summary.docs_scored)
                    summary.cost_projected_usd = projected
                    msg = (
                        f"projected cost ${projected:.2f} for {sample} docs "
                        f"(mean ${mean:.5f}/doc over {len(probe_costs)} probed); "
                        f"limit ${max_run_usd:.2f}"
                    )
                    log.info(msg)
                    if projected > max_run_usd:
                        raise BudgetExceeded(f"{msg}; refusing to continue")
            if todo:
                batch = {i: texts[i] for i in todo}
                settle(score_batch(prompt.text, batch, provider, concurrency))
            ids.commit()  # only now: the whole batch's scores are durable in Postgres
    except BaseException:
        try:
            db.finish_job(engine, job_id, "failed")
        except Exception:
            log.exception("could not mark job %s failed", job_id)
        raise
    finally:
        if ids is not None:
            try:
                ids.close()
            except Exception:
                log.exception("could not close the id source")
    db.finish_job(engine, job_id, "finished")
    log.info("job %s finished: %s", job_id, summary)
    return summary
