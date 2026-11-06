"""Prefect flows `judge` (label the gold set) and `evaluate` (turn the labels into a report).

`judge`: gold ids -> skip already labeled -> judge model -> gold CSV.

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

`evaluate`: scores + judge labels (+ rationale grades, if any) -> metrics ->
docs/reports/<criterion>-<model>-<v>.md. It makes no LLM call. The two piles are
recomputed from the current scores, not taken from the CSV `pile` column, so the report
always describes the scores as they stand.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from prefect import flow, get_run_logger, task
from prefect.cache_policies import NO_CACHE
from sqlalchemy.engine import Engine

from elusion.collect.db import company_counts
from elusion.config import settings
from elusion.evaluate import metrics
from elusion.evaluate.gold import (
    DEFAULT_SEED,
    GoldRow,
    Pile,
    append_judge_run,
    append_rows,
    gold_path,
    human_rows,
    judge_cost_usd,
    judge_rows,
    judge_runs_path,
    labeled_ids,
    select_gold,
    split_piles,
)
from elusion.evaluate.grades import grade_cost_usd, grade_runs_path, grades_path, rows_for
from elusion.evaluate.report import RunReport, render
from elusion.evaluate.tracking import log_run
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
from elusion.review.db import jobs_summary, provider_for, scores_for
from elusion.review.prompt import PROMPTS_DIR, load_prompt
from elusion.review.providers import AnthropicProvider, Judge

JUDGE_PROMPT_VERSION = "judge-v1"  # prompts/<criterion>/judge-v1.md


@dataclass
class JudgeSummary:
    """Counts for one judge run. `skipped` = gold ids this judge model already labeled."""

    labeled: int = 0
    skipped: int = 0
    failed: int = 0  # ReviewErrors: billed, no row written, retried on the next run
    cost_usd: float = 0.0
    cost_projected_usd: float | None = None  # None if fewer than PROBE_DOCS to label


# NO_CACHE: Prefect 3 would otherwise hash the task inputs (provider, texts) for caching.
@task(cache_policy=NO_CACHE)
def label_batch(
    system: str, texts: dict[str, str], provider: Judge, concurrency: int
) -> BatchResult:
    """Label each (doc_id, text) concurrently; same contract as review.score_batch."""
    return run_batch(lambda text: provider.label(system, text), texts, concurrency)


class ReviewStillRunning(RuntimeError):
    """A review job for the pair has no finished_at, so its score set may be partial."""


def _unfinished_msg(jobs: dict, model: str, version: str) -> str:
    ids = ", ".join(jobs["unfinished_ids"])
    return f"review job(s) {ids} for ({model}, {version}) still running (no finished_at)"


@flow(name="judge")
def judge(
    criterion: str,
    prompt_version: str = "v2",
    threshold: int = 3,
    discard_n: int = 200,
    seed: int = DEFAULT_SEED,
    review_model: str | None = None,
    max_run_usd: float | None = None,
    batch_size: int = 50,
    concurrency: int = 8,
    provider: Judge | None = None,
    gold: list[tuple[str, Pile]] | None = None,
    gold_csv: Path | None = None,
    force: bool = False,
) -> JudgeSummary:
    """Label the gold set of the review run (`review_model`, `prompt_version`).

    `prompt_version` names the review prompt whose scores define the piles; the judge's
    own prompt is always JUDGE_PROMPT_VERSION. `gold` and `gold_csv` default to
    `select_gold` over Postgres and data/gold/<criterion>.csv; tests pass their own.

    While a review job for the pair is unfinished the scores may be partial: the discard
    sample would be drawn from them, and once the review finishes, new flagged docs would
    lack labels and a re-draw would pay again. So the flow raises ReviewStillRunning
    unless `force` (for a row left unfinished by a killed process).

    Spend guard as in the review flow: PROBE_DOCS attempts one at a time (their rows kept),
    then mean cost per attempt times the ids not yet attempted; above `max_run_usd`
    (default ELUSION_MAX_RUN_USD) the flow raises BudgetExceeded before any concurrent
    spend, and a probe without a single usable label raises RuntimeError.
    """
    log = get_run_logger()
    review_model = review_model or settings.review_model
    if max_run_usd is None:
        max_run_usd = settings.max_run_usd
    prompt = load_prompt(criterion, JUDGE_PROMPT_VERSION)
    provider = provider or AnthropicProvider(settings.judge_model)
    path = gold_csv or gold_path(criterion, settings.data_dir)
    if gold is None:
        review_version = f"{criterion}/{prompt_version}"
        engine = db.make_engine(settings.database_url)
        jobs = jobs_summary(engine, review_model, review_version)
        if jobs["unfinished"]:
            msg = _unfinished_msg(jobs, review_model, review_version)
            if not force:
                raise ReviewStillRunning(
                    f"{msg}; wait for it to finish, or pass --force if it was killed"
                )
            log.warning("%s; continuing because force=True", msg)
        gold = select_gold(engine, review_model, review_version, threshold, discard_n, seed)
        if not gold:
            raise RuntimeError(f"no scores for ({review_model}, {review_version})")
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

    def settle(res: BatchResult) -> None:
        """Record what `res` cost and append its rows; raise if anything went wrong.

        Every echoed model is checked before any row is written: rows are keyed by the
        pinned id, so a mismatch would make a re-run pay for every document again.
        """
        summary.cost_usd += res.cost_usd
        summary.failed += res.failed
        for _, j in res.answers:
            check_model_echo(provider, j, "ELUSION_JUDGE_MODEL")
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
            for d, j in res.answers
        ]
        summary.labeled += append_rows(path, rows)
        if res.errors:
            raise res.errors[0][1]

    # From here on every path has made at least one call, so the run's spend is recorded
    # in the judge-runs ledger whether it finishes or raises (evaluate reads it later).
    try:
        probe_costs: list[float] = []  # billed cost of every probe attempt, failures included
        probe_ok = 0
        project = len(todo) >= PROBE_DOCS  # a smaller run only needs one good probe (echo check)
        while todo and (len(probe_costs) < PROBE_DOCS if project else probe_ok < 1):
            first = label_batch(prompt.text, {todo[0]: texts[todo[0]]}, provider, 1)
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
                f"for {len(todo)} more labels (mean ${mean:.5f}/doc over {len(probe_costs)} "
                f"probe attempts, {probe_ok} with a label); limit ${max_run_usd:.2f}"
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
                "labeled %s, failed %s, $%.4f so far",
                summary.labeled,
                summary.failed,
                summary.cost_usd,
            )
    finally:
        record = {
            "labeled": summary.labeled,
            "failed": summary.failed,
            "cost_usd": summary.cost_usd,
            "model": provider.model,
            "prompt_version": prompt.version,
            "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        try:
            append_judge_run(judge_runs_path(path), record)
        except Exception:
            log.exception("could not record the judge run %s", record)
    log.info("judge finished: %s", summary)
    return summary


class MissingLabels(RuntimeError):
    """Some flagged paragraph has no judge label, so precision cannot be computed."""


class MissingDiscardLabels(MissingLabels):
    """Some doc in the pair's seeded discard draw has no judge label, so elusion is biased."""


@dataclass
class EvalResult:
    """What `evaluate` returns: the report file and the MLflow run id (None if not logged)."""

    report_path: Path
    run_id: str | None


def report_path(reports_dir: Path, criterion: str, model: str, prompt_version: str) -> Path:
    return reports_dir / f"{criterion}-{model}-{prompt_version.replace('/', '-')}.md"


@flow(name="evaluate")
def evaluate(
    criterion: str,
    prompt_version: str = "v2",
    threshold: int = 3,
    seed: int = DEFAULT_SEED,
    engine: Engine | None = None,
    data_dir: Path | None = None,
    reports_dir: Path = Path("docs/reports"),
    review_model: str | None = None,
    discard_n: int = 200,
    judge_model: str | None = None,
    today: str | None = None,
    log_mlflow: bool = True,
) -> EvalResult:
    """Compute the metrics of the review run (review_model, `prompt_version`) from the
    judge's labels, write the Markdown report and log one MLflow run for it.

    Flagged = every doc scoring >= `threshold` under the pair; each must carry a label by
    `judge_model`, otherwise MissingLabels (re-running `elusion judge` fills them). The
    discard sample = the seeded draw of `discard_n` from this pair's own discard pile
    (`split_piles`, the same draw `elusion judge` made); each drawn doc must carry a label
    by `judge_model`, otherwise MissingDiscardLabels. Labels for docs outside the draw
    (e.g. flagged by another review run) are never counted, so elusion stays a uniform
    sample. Tests pass a fake `engine` and their own paths.

    An unfinished review job for the pair only warns (on stderr): the report is still
    written, and its wall-clock line says a job has not finished.

    The report is written before MLflow is touched. If logging fails (server down) the
    report stays on disk and the RuntimeError names its path. `log_mlflow=False` skips it.
    """
    log = get_run_logger()
    review_model = review_model or settings.review_model
    judge_model = judge_model or settings.judge_model
    engine = engine or db.make_engine(settings.database_url)
    csv = gold_path(criterion, data_dir or settings.data_dir)
    version = f"{criterion}/{prompt_version}"

    jobs = jobs_summary(engine, review_model, version)
    if jobs["unfinished"]:
        msg = _unfinished_msg(jobs, review_model, version)
        msg += "; the scores may be partial, so this report may be too"
        print(f"WARNING: {msg}", file=sys.stderr)
        log.warning(msg)

    scores = scores_for(engine, review_model, version)
    if not scores:
        raise RuntimeError(f"no scores for ({review_model}, {version})")
    flagged = {d for d, s in scores.items() if s >= threshold}
    judged = {r.doc_id: r for r in judge_rows(csv) if r.labeler == judge_model}
    missing = flagged - judged.keys()
    if missing:
        raise MissingLabels(
            f"{len(missing)} flagged paragraphs have no {judge_model} label in {csv}, "
            f"e.g. {min(missing)}; re-run `elusion judge` to fill them"
        )
    flagged_rows = [judged[d] for d in sorted(flagged)]
    draw = {d for d, pile in split_piles(scores, threshold, discard_n, seed) if pile == "discard"}
    missing_d = draw - judged.keys()
    if missing_d:
        raise MissingDiscardLabels(
            f"{len(missing_d)} sampled discard paragraphs have no {judge_model} label in {csv}; "
            "re-run `elusion judge` with the same seed"
        )
    discard_rows = [judged[d] for d in sorted(draw)]
    n_flagged, n_discard = len(flagged), len(scores) - len(flagged)

    precision = metrics.precision(flagged_rows)
    elusion = metrics.elusion(discard_rows)
    humans = human_rows(csv)
    shared = [h for h in humans if h.doc_id in judged]
    agreement = metrics.agreement(shared, list(judged.values())) if shared else None

    wall_clock = None
    if jobs["jobs"]:
        end = datetime.now(UTC) if jobs["unfinished"] else jobs["finished_at"]
        wall_clock = end - jobs["started_at"]
    relevant = [r.doc_id for r in flagged_rows if r.label == 1]

    # Rationale grades for the pair's flagged docs by the same model that judged. They
    # join the judge's labels here, in memory; neither file feeds the other.
    grades_csv = grades_path(criterion, data_dir or settings.data_dir)
    grades = {g.doc_id: g for g in rows_for(grades_csv, review_model, version, judge_model)}
    grades = {d: g for d, g in grades.items() if d in flagged}
    graded: dict = {}
    if grades:
        sound_by_label = {(s, lab): 0 for s in (0, 1) for lab in (0, 1)}
        for d, g in grades.items():
            sound_by_label[(g.sound, judged[d].label)] += 1
        graded = dict(
            rationale_sound=metrics.proportion(sum(g.sound for g in grades.values()), len(grades)),
            quote_verbatim=metrics.proportion(
                sum(g.quote_verbatim for g in grades.values()), len(grades)
            ),
            sound_by_label=sound_by_label,
            grader_model=judge_model,
            grader_prompt_version=", ".join(
                sorted({g.grader_prompt_version for g in grades.values()})
            ),
            grade_cost_usd=grade_cost_usd(grade_runs_path(grades_csv)),
        )

    report = RunReport(
        criterion=criterion,
        review_model=review_model,
        prompt_version=version,
        judge_model=judge_model,
        judge_prompt_version=", ".join(sorted({r.prompt_version for r in judged.values()})),
        date=today or datetime.now(UTC).date().isoformat(),
        seed=seed,
        discard_n=discard_n,
        threshold=threshold,
        population=len(scores),
        n_flagged=n_flagged,
        n_discard=n_discard,
        precision=precision,
        elusion=elusion,
        richness=metrics.richness(precision, elusion, n_flagged, n_discard),
        recall=metrics.recall(precision, elusion, n_flagged, n_discard),
        agreement=agreement,
        review_cost_usd=jobs["cost_usd"],
        judge_cost_usd=judge_cost_usd(judge_runs_path(csv)),
        review_jobs=jobs["jobs"],
        review_wall_clock=wall_clock,
        review_unfinished=jobs["unfinished"],
        companies=company_counts(engine, relevant),
        **graded,
    )
    path = report_path(reports_dir, criterion, review_model, prompt_version)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(report), encoding="utf-8")
    log.info("report written to %s", path)
    if not log_mlflow:
        return EvalResult(path, None)
    provider = provider_for(engine, review_model, version) or "unknown"
    artifacts = [
        path,
        PROMPTS_DIR / criterion / f"{prompt_version}.md",
        Path("criteria") / f"{criterion}.md",
    ]
    try:
        run_id = log_run(
            report,
            provider,
            [a for a in artifacts if a.is_file()],
            settings.mlflow_tracking_uri,
            path,
        )
    except Exception as exc:
        raise RuntimeError(f"report written to {path}, but MLflow logging failed: {exc}") from exc
    log.info("MLflow run %s", run_id)
    return EvalResult(path, run_id)
