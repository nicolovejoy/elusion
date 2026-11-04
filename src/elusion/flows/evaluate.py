"""Prefect flow `judge`: gold ids -> skip already labeled -> judge model -> gold CSV.

A *judge* is a second, stronger model (Opus 5.5, pinned in ELUSION_JUDGE_MODEL) asked the
same question as the review in yes/no form, under its own versioned prompt. Its labels
stand in for human ground truth on the gold set: every paragraph the review flagged plus
a seeded random sample of the discard pile (see evaluate/gold.py). The owner then audits
a handful of the judge's labels by hand.

The judge reads the paragraph text and nothing else: no review score, rationale or
quote, no company, no earlier label. Otherwise it could anchor on the answer it is meant
to check.

The mechanics copy the review flow (flows/review.py): the first PROBE_DOCS calls are
made one at a time and their mean cost projects the run against ELUSION_MAX_RUN_USD;
every answer's echoed model must equal the pinned one; the rest is labeled by a thread
pool, one batch at a time, and each batch's rows are appended to the CSV before the next
starts. Idempotency is by (doc_id, labeler): ids the judge model already labeled are
skipped, and a failed call (refusal, truncation, invalid JSON) writes no row, so a plain
re-run retries exactly those ids. There is no Kafka here: the gold ids are a fixed list.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from prefect import flow, get_run_logger, task
from prefect.cache_policies import NO_CACHE

from elusion.config import settings
from elusion.evaluate.gold import (
    DEFAULT_SEED,
    GoldRow,
    Pile,
    append_rows,
    gold_path,
    labeled_ids,
    select_gold,
)
from elusion.flows.review import PROBE_DOCS, BudgetExceeded, project_cost
from elusion.review import db
from elusion.review.corpus import load_texts
from elusion.review.pricing import cost_usd
from elusion.review.prompt import load_prompt
from elusion.review.providers import AnthropicProvider, Judge, Judgement, ReviewError

JUDGE_PROMPT_VERSION = "judge-v1"  # prompts/<criterion>/judge-v1.md


@dataclass
class JudgeSummary:
    """Counts for one judge run. `skipped` = gold ids this judge model already labeled."""

    labeled: int = 0
    skipped: int = 0
    failed: int = 0  # ReviewErrors: billed, no row written, retried on the next run
    cost_usd: float = 0.0
    cost_projected_usd: float | None = None  # None if fewer than PROBE_DOCS to label


@dataclass
class LabelBatch:
    judgements: list[tuple[str, Judgement]] = field(default_factory=list)
    failed: int = 0  # ReviewErrors: billed, no label
    cost_usd: float = 0.0  # everything billed: labels and ReviewErrors
    errors: list[tuple[str, Exception]] = field(default_factory=list)  # anything else


# NO_CACHE: Prefect 3 would otherwise hash the task inputs (provider, texts) for caching.
@task(cache_policy=NO_CACHE)
def label_batch(
    system: str, texts: dict[str, str], provider: Judge, concurrency: int
) -> LabelBatch:
    """Label each (doc_id, text) concurrently; never raises for a single document.

    Same contract as flows.review.score_batch: every document is sent, every outcome is
    returned, and the flow stores what was paid for before re-raising any error.
    """
    log = get_run_logger()
    result = LabelBatch()

    def one(doc_id: str) -> tuple[str, Judgement | Exception]:
        try:
            return doc_id, provider.label(system, texts[doc_id])
        except Exception as e:  # ReviewError included; sorted out below
            return doc_id, e

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for doc_id, out in pool.map(one, texts):
            if isinstance(out, Judgement):
                result.judgements.append((doc_id, out))
                result.cost_usd += out.cost_usd
            elif isinstance(out, ReviewError):
                log.warning("no label for %s (stop_reason=%s)", doc_id, out.stop_reason)
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
                log.error("judging %s raised %r", doc_id, out)
                result.errors.append((doc_id, out))
    return result


@flow(name="judge")
def judge(
    criterion: str,
    prompt_version: str = "v2",
    threshold: int = 2,
    discard_n: int = 200,
    seed: int = DEFAULT_SEED,
    max_run_usd: float | None = None,
    batch_size: int = 50,
    concurrency: int = 8,
    provider: Judge | None = None,
    gold: list[tuple[str, Pile]] | None = None,
    gold_csv: Path | None = None,
) -> JudgeSummary:
    """Label the gold set of the review run (settings.review_model, `prompt_version`).

    `prompt_version` names the review prompt whose scores define the piles; the judge's
    own prompt is always JUDGE_PROMPT_VERSION. `gold` and `gold_csv` default to
    `select_gold` over Postgres and data/gold/<criterion>.csv; tests pass their own.

    Spend guard as in the review flow: PROBE_DOCS attempts one at a time (their rows kept),
    then mean cost per attempt times the ids not yet attempted; above `max_run_usd`
    (default ELUSION_MAX_RUN_USD) the flow raises BudgetExceeded before any concurrent
    spend, and a probe without a single usable label raises RuntimeError.
    """
    log = get_run_logger()
    if max_run_usd is None:
        max_run_usd = settings.max_run_usd
    prompt = load_prompt(criterion, JUDGE_PROMPT_VERSION)
    provider = provider or AnthropicProvider(settings.judge_model)
    path = gold_csv or gold_path(criterion, settings.data_dir)
    if gold is None:
        review_version = f"{criterion}/{prompt_version}"
        engine = db.make_engine(settings.database_url)
        gold = select_gold(
            engine, settings.review_model, review_version, threshold, discard_n, seed
        )
        if not gold:
            raise RuntimeError(f"no scores for ({settings.review_model}, {review_version})")
    pile = dict(gold)
    done = labeled_ids(path, provider.model)
    todo = [d for d in pile if d not in done]
    summary = JudgeSummary(skipped=len(pile) - len(todo))
    log.info(
        "judge=%s prompt=%s: %s gold ids, %s to label, %s already labeled; seed=%s -> %s",
        provider.model, prompt.version, len(pile), len(todo), summary.skipped, seed, path,
    )  # fmt: skip
    if not todo:
        return summary

    texts = load_texts(settings.corpus_dir)
    missing = [d for d in todo if d not in texts]
    if missing:
        raise KeyError(f"{len(missing)} ids not in the Parquet corpus, e.g. {missing[0]}")

    def settle(res: LabelBatch) -> None:
        """Record what `res` cost and append its rows; raise if anything went wrong.

        Every echoed model is checked before any row is written: rows are keyed by the
        pinned id, so a mismatch would make a re-run pay for every document again.
        """
        summary.cost_usd += res.cost_usd
        summary.failed += res.failed
        for _, j in res.judgements:
            if j.model != provider.model:
                raise RuntimeError(
                    f"API served model {j.model!r} but the pinned judge model is "
                    f"{provider.model!r}; pin the served snapshot id (ELUSION_JUDGE_MODEL)"
                )
        now = datetime.now(UTC).isoformat(timespec="seconds")
        rows = [
            GoldRow(
                doc_id=d,
                label=int(j.label.relevant),
                pile=pile[d],
                labeler=provider.model,
                prompt_version=prompt.version,
                rationale=j.label.rationale,
                labeled_at=now,
            )
            for d, j in res.judgements
        ]
        summary.labeled += append_rows(path, rows)
        if res.errors:
            raise res.errors[0][1]

    probe_costs: list[float] = []  # billed cost of every probe attempt, failures included
    probe_ok = 0
    project = len(todo) >= PROBE_DOCS  # a smaller run only needs one good probe (echo check)
    while todo and (len(probe_costs) < PROBE_DOCS if project else probe_ok < 1):
        first = label_batch(prompt.text, {todo[0]: texts[todo[0]]}, provider, 1)
        settle(first)
        probe_costs.append(first.cost_usd)
        probe_ok += len(first.judgements)
        todo = todo[1:]
    if project:
        mean, projected = project_cost(probe_costs, len(todo))
        summary.cost_projected_usd = projected
        msg = (
            f"projected cost ${projected:.2f} for {len(todo)} more labels "
            f"(mean ${mean:.5f}/doc over {len(probe_costs)} probe attempts, "
            f"{probe_ok} with a label); limit ${max_run_usd:.2f}"
        )
        log.info(msg)
        if projected > max_run_usd:
            raise BudgetExceeded(f"{msg}; refusing to continue")
        if probe_ok == 0:
            raise RuntimeError(f"no usable label in {PROBE_DOCS} attempts; check the prompt")

    for k in range(0, len(todo), batch_size):
        batch = {d: texts[d] for d in todo[k : k + batch_size]}
        settle(label_batch(prompt.text, batch, provider, concurrency))
        log.info(
            "labeled %s, failed %s, $%.4f so far", summary.labeled, summary.failed, summary.cost_usd
        )
    log.info("judge finished: %s", summary)
    return summary
