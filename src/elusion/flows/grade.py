"""Prefect flow `grade`: have the grader model judge each flagged rationale.

A review run answers each paragraph with a score, a rationale and a verbatim quote. The
score is checked by `evaluate` against the judge's labels; this flow checks the other
half, whether the *reason given* is a faithful reading of the paragraph under the
criterion. The grader (settings.judge_model, prompt `grade-v1`) is sent the paragraph and
that one reviewer answer, never a gold label, and its verdicts go to data/grades/ (see
evaluate/grades.py). `evaluate` joins them with the labels afterwards, in memory.

Only flagged verdicts (score >= threshold) are graded: those are the ones whose reasons a
reader would act on. The mechanics are the judge flow's (flows/evaluate.py): the first
PROBE_DOCS calls run one at a time and their mean cost projects the run against
ELUSION_MAX_RUN_USD; every echoed model must equal the pinned one; the rest runs in a
thread pool, one batch at a time, each batch appended to the CSV before the next starts.
Idempotency is by (doc_id, review model, review prompt version, grader): a failed call
writes no row, so a plain re-run retries exactly those ids.

The probe/settle loop below is a copy of `judge`'s, not a shared helper: the two differ in
what a call takes (a verdict as well as a paragraph), what a row holds and what the ledger
records, and a helper parameterized over all three read worse than the duplication.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from prefect import flow, get_run_logger, task
from prefect.cache_policies import NO_CACHE

from elusion.config import settings
from elusion.evaluate.grades import (
    GradeRow,
    append_grade_run,
    append_rows,
    grade_runs_path,
    graded_ids,
    grades_path,
    quote_is_verbatim,
)
from elusion.flows.review import (
    PROBE_DOCS,
    BatchResult,
    BudgetExceeded,
    check_model_echo,
    project_cost,
    run_batch,
)
from elusion.review import db
from elusion.review.corpus import load_texts
from elusion.review.db import verdicts_for
from elusion.review.prompt import load_prompt
from elusion.review.providers import AnthropicProvider, Grader, Verdict

GRADE_PROMPT_VERSION = "grade-v1"  # prompts/<criterion>/grade-v1.md


@dataclass
class GradeSummary:
    """Counts for one grade run. `skipped` = flagged ids this grader already graded."""

    graded: int = 0
    skipped: int = 0
    failed: int = 0  # ReviewErrors: billed, no row written, retried on the next run
    cost_usd: float = 0.0
    cost_projected_usd: float | None = None  # None if fewer than PROBE_DOCS to grade


# NO_CACHE: Prefect 3 would otherwise hash the task inputs (grader, texts) for caching.
@task(cache_policy=NO_CACHE)
def grade_batch(
    system: str,
    ids: list[str],
    paragraphs: dict[str, str],
    verdicts: dict[str, Verdict],
    grader: Grader,
    concurrency: int,
) -> BatchResult:
    """Grade each doc id concurrently; same contract as review.score_batch.

    run_batch hands `call` one value per key, so the keys are the doc ids themselves and
    the closure looks the paragraph and the verdict up by id.
    """
    return run_batch(
        lambda d: grader.grade(system, paragraphs[d], verdicts[d]), {d: d for d in ids}, concurrency
    )


@flow(name="grade")
def grade(
    criterion: str,
    prompt_version: str = "v2",
    review_model: str | None = None,
    threshold: int = 3,
    max_run_usd: float | None = None,
    batch_size: int = 50,
    concurrency: int = 8,
    grader: Grader | None = None,
    verdicts: dict[str, Verdict] | None = None,
    grades_csv: Path | None = None,
) -> GradeSummary:
    """Grade the flagged rationales of the review run (`review_model`, `prompt_version`).

    `verdicts` defaults to every verdict of the pair scoring >= `threshold`, read from
    Postgres; `grades_csv` to data/grades/<criterion>.csv. Tests pass their own. The
    spend guard is the judge flow's: above `max_run_usd` (default ELUSION_MAX_RUN_USD)
    the flow raises BudgetExceeded before any concurrent spend, and a probe without a
    single usable grade raises RuntimeError.
    """
    log = get_run_logger()
    review_model = review_model or settings.review_model
    if max_run_usd is None:
        max_run_usd = settings.max_run_usd
    prompt = load_prompt(criterion, GRADE_PROMPT_VERSION)
    grader = grader or AnthropicProvider(settings.judge_model)
    path = grades_csv or grades_path(criterion, settings.data_dir)
    review_version = f"{criterion}/{prompt_version}"
    if verdicts is None:
        engine = db.make_engine(settings.database_url)
        verdicts = verdicts_for(engine, review_model, review_version, threshold)
        if not verdicts:
            raise RuntimeError(f"no flagged scores for ({review_model}, {review_version})")
    done = graded_ids(path, review_model, review_version, grader.model)
    todo = sorted(d for d in verdicts if d not in done)
    summary = GradeSummary(skipped=len(verdicts) - len(todo))
    log.info(
        "grader=%s prompt=%s: %s flagged, %s to grade, %s already graded -> %s",
        grader.model, prompt.version, len(verdicts), len(todo), summary.skipped, path,
    )  # fmt: skip
    if not todo:
        return summary

    texts = load_texts(settings.corpus_dir)
    missing = [d for d in todo if d not in texts]
    if missing:
        raise KeyError(f"{len(missing)} ids not in the Parquet corpus, e.g. {missing[0]}")

    def settle(res: BatchResult) -> None:
        """Record what `res` cost and append its rows; raise if anything went wrong.

        Every echoed model is checked before any row is written: rows are keyed by the
        pinned id, so a mismatch would make a re-run pay for every document again.
        """
        summary.cost_usd += res.cost_usd
        summary.failed += res.failed
        for _, g in res.answers:
            check_model_echo(grader, g, "ELUSION_JUDGE_MODEL")
        now = datetime.now(UTC).isoformat(timespec="seconds")
        rows = [
            GradeRow(
                doc_id=d,
                review_model=review_model,
                prompt_version=review_version,
                score=verdicts[d].score,
                grader=grader.model,
                grader_prompt_version=prompt.version,
                sound=int(g.grade.sound),
                quote_verbatim=int(quote_is_verbatim(verdicts[d].quote, texts[d])),
                rationale=g.grade.rationale,
                graded_at=now,
            )
            for d, g in res.answers
        ]
        summary.graded += append_rows(path, rows)
        if res.errors:
            raise res.errors[0][1]

    def run_grades(ids: list[str], n: int) -> BatchResult:
        return grade_batch(prompt.text, ids, texts, verdicts, grader, n)

    # From here on every path has made at least one call, so the run's spend is recorded
    # in the grade-runs ledger whether it finishes or raises (evaluate reads it later).
    try:
        probe_costs: list[float] = []  # billed cost of every probe attempt, failures included
        probe_ok = 0
        project = len(todo) >= PROBE_DOCS  # a smaller run only needs one good probe (echo check)
        while todo and (len(probe_costs) < PROBE_DOCS if project else probe_ok < 1):
            first = run_grades(todo[:1], 1)
            settle(first)
            probe_costs.append(first.cost_usd)
            probe_ok += len(first.answers)
            todo = todo[1:]
        if project:
            # The limit is on the whole run: probes already billed plus the rest.
            mean, rest = project_cost(probe_costs, len(todo))
            spent = sum(probe_costs)
            projected = spent + rest
            summary.cost_projected_usd = projected
            msg = (
                f"projected run cost ${projected:.2f} = ${spent:.2f} probes + ${rest:.2f} "
                f"for {len(todo)} more grades (mean ${mean:.5f}/doc over {len(probe_costs)} "
                f"probe attempts, {probe_ok} with a grade); limit ${max_run_usd:.2f}"
            )
            log.info(msg)
            if projected > max_run_usd:
                raise BudgetExceeded(f"{msg}; refusing to continue")
            if probe_ok == 0:
                raise RuntimeError(f"no usable grade in {PROBE_DOCS} attempts; check the prompt")

        for k in range(0, len(todo), batch_size):
            settle(run_grades(todo[k : k + batch_size], concurrency))
            log.info(
                "graded %s, failed %s, $%.4f so far",
                summary.graded,
                summary.failed,
                summary.cost_usd,
            )
    finally:
        record = {
            "graded": summary.graded,
            "failed": summary.failed,
            "cost_usd": summary.cost_usd,
            "model": grader.model,
            "prompt_version": prompt.version,
            "review_model": review_model,
            "review_prompt_version": review_version,
            "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        try:
            append_grade_run(grade_runs_path(path), record)
        except Exception:
            log.exception("could not record the grade run %s", record)
    log.info("grade finished: %s", summary)
    return summary
