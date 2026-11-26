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
docs/reports/<criterion>-<model>-<v>-<years>.md. It makes no LLM call. The two piles are
recomputed from the current scores, not taken from the CSV `pile` column, so the report
always describes the scores as they stand.

Both flows work on one *population*: the paragraphs filed in the given `years` and
scored under one (review model, prompt version) pair. `years` has no default. The same
pair can hold several populations (2018 and 2019 scored on the laptop, 2020 on the
cluster), and each keeps its own piles, discard draw, report file and MLflow run.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from prefect import flow, get_run_logger, task
from prefect.cache_policies import NO_CACHE
from sqlalchemy.engine import Engine

from elusion.collect.abstracts import doc_ids_by_work
from elusion.collect.custody import Seed, distinct_works, seed_works, works_per_query
from elusion.collect.db import company_counts, count_documents
from elusion.collect.terms import ANCHORS
from elusion.config import settings
from elusion.corpora import OPENALEX, corpus_for
from elusion.evaluate import metrics
from elusion.evaluate.gold import (
    DEFAULT_SEED,
    HUMAN,
    GoldRow,
    Pile,
    append_judge_run,
    append_refusals,
    append_rows,
    gold_path,
    human_rows,
    judge_cost_usd,
    judge_rows_for,
    judge_runs_path,
    labeled_ids,
    refusals_path,
    refused_ids,
    select_gold,
    split_piles,
    with_seeds,
)
from elusion.evaluate.grades import grade_cost_usd, grade_runs_path, grades_path, rows_for
from elusion.evaluate.report import Identified, Literature, RunReport, render, years_label
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
from elusion.review.db import jobs_summary, provider_for, scores_cost_usd, scores_for
from elusion.review.prompt import PROMPTS_DIR, judge_version, load_prompt
from elusion.review.providers import AnthropicProvider, Judge


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


def draw_gold(
    criterion: str,
    prompt_version: str,
    *,
    years: list[int],
    threshold: int = 3,
    discard_n: int = 200,
    seed: int = DEFAULT_SEED,
    review_model: str | None = None,
    flagged_n: int | None = None,
    force: bool = False,
    engine: Engine | None = None,
) -> list[tuple[str, Pile]]:
    """The gold ids of the review run (`review_model`, `prompt_version`) over `years`.

    Nothing stores the draw: `judge` and `label` each call this with the same sizes and
    seed and get the same ids, because the draw depends only on the scores and the seed.
    That is also why an unfinished review job is refused (ReviewStillRunning) unless
    `force`: a draw from partial scores is not the draw the finished pile will give.
    Raises RuntimeError when the pair has no scores for those years.
    """
    review_model = review_model or settings.review_model
    review_version = f"{criterion}/{prompt_version}"
    engine = engine or db.make_engine(settings.database_url)
    jobs = jobs_summary(engine, review_model, review_version, years)
    if jobs["unfinished"]:
        msg = _unfinished_msg(jobs, review_model, review_version)
        if not force:
            raise ReviewStillRunning(
                f"{msg}; wait for it to finish, or pass --force if it was killed"
            )
        logging.getLogger(__name__).warning("%s; continuing because force=True", msg)
    gold = select_gold(
        engine, review_model, review_version, threshold, discard_n, seed, years, flagged_n=flagged_n
    )
    if not gold:
        raise RuntimeError(f"no scores for ({review_model}, {review_version}) filed in {years}")
    return gold


@flow(name="judge")
def judge(
    criterion: str,
    prompt_version: str = "v2",
    *,
    years: list[int],
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
    flagged_n: int | None = None,
    only_human: bool = False,
    seeds: bool = False,
) -> JudgeSummary:
    """Label the gold set of the review run (`review_model`, `prompt_version`) for the
    paragraphs filed in `years`.

    `years` has no default. A pair's scores can cover several populations (2018-2019
    scored on the laptop, 2020 on the cluster), and the seeded discard draw depends on
    the whole pile it is drawn from: mixing years would pick a new sample and pay the
    judge to label it. `prompt_version` names the review prompt whose scores define the
    piles; the judge's own prompt is the criterion's (`judge_version`), and
    labels written under another judge prompt are not counted as done. `gold` and `gold_csv`
    default to `select_gold` over Postgres and data/gold/<criterion>.csv; tests pass
    their own.

    `flagged_n` limits the flagged pile to a seeded random sample of that size (see
    `split_piles`); without it every flagged paragraph is labeled. `only_human` narrows
    the gold to the ids the owner has already labeled by hand, so the judge can be
    measured against the owner on a few dozen before it is paid for the rest; a later
    run without it skips those ids and labels the others. `seeds` adds, after the draw,
    every flagged citation seed the draw left out (pile "seed", see `with_seeds`); it
    applies only to a criterion over the abstracts corpus, the one with citation seeds.

    While a review job that scored the population is unfinished the scores may be
    partial: the discard sample would be drawn from them, and once the review finishes,
    new flagged docs would lack labels and a re-draw would pay again. So the flow raises
    ReviewStillRunning
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
    prompt = load_prompt(criterion, judge_version(criterion))
    provider = provider or AnthropicProvider(settings.judge_model)
    path = gold_csv or gold_path(criterion, settings.data_dir)
    if gold is None:
        corpus = corpus_for(criterion)
        if seeds and corpus is not OPENALEX:
            raise RuntimeError(f"{criterion} is reviewed over '{corpus.name}': no citation seeds")
        engine = db.make_engine(settings.database_url)
        gold = draw_gold(
            criterion,
            prompt_version,
            years=years,
            threshold=threshold,
            discard_n=discard_n,
            seed=seed,
            review_model=review_model,
            flagged_n=flagged_n,
            force=force,
            engine=engine,
        )
        if seeds:
            by_work = doc_ids_by_work(corpus.text_dir(settings.data_dir))
            seed_ids = {d for w in seed_works(engine) for d in by_work.get(w, ())}
            scores = scores_for(engine, review_model, f"{criterion}/{prompt_version}", years)
            gold = with_seeds(gold, scores, threshold, seed_ids)
    if only_human:
        by_owner = labeled_ids(path, HUMAN)
        outside = by_owner - {d for d, _ in gold}
        if outside:
            # Not an error: a gold file can hold the owner's labels from another draw.
            msg = (
                f"{len(outside)} of the {len(by_owner)} human labels in {path} are not "
                "among the drawn gold ids and are left out; if that is unexpected, check "
                "that --flagged-n, --discard-n and the seed are the ones `elusion label` "
                "was given"
            )
            print(f"WARNING: {msg}", file=sys.stderr)
            log.warning(msg)
        gold = [(d, p) for d, p in gold if d in by_owner]
        if not gold:
            raise RuntimeError(
                f"no human labels in {path} among the drawn gold ids; run `elusion label` first"
            )
    pile = dict(gold)
    done = labeled_ids(path, provider.model, prompt.version)
    todo = [d for d in pile if d not in done]
    summary = JudgeSummary(skipped=len(pile) - len(todo))
    log.info(
        "judge=%s prompt=%s: %s gold ids, %s to label, %s already labeled; seed=%s -> %s",
        provider.model, prompt.version, len(pile), len(todo), summary.skipped, seed, path,
    )  # fmt: skip
    if not todo:
        return summary

    texts = load_texts(corpus_for(criterion).text_dir(settings.data_dir))
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
        # A refusal writes no label, so the id is tried again on the next run; the record
        # lets `evaluate` tell an abstract the judge would not read from one never sent.
        append_refusals(refusals_path(path), res.refused, provider.model, prompt.version)
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
            # The review pair this run was for. Older records lack these two fields.
            "review_model": review_model,
            "review_prompt_version": f"{criterion}/{prompt_version}",
            "years": sorted(years),  # informational: the judge cost line is criterion-wide
            "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        try:
            append_judge_run(judge_runs_path(path), record)
        except Exception:
            log.exception("could not record the judge run %s", record)
    log.info("judge finished: %s", summary)
    return summary


class MissingLabels(RuntimeError):
    """Some flagged paragraph (of the flagged draw, when there is one) has no judge label,
    so precision cannot be computed."""


class MissingDiscardLabels(MissingLabels):
    """Some doc in the pair's seeded discard draw has no judge label, so elusion is biased."""


@dataclass
class EvalResult:
    """What `evaluate` returns: the report file and the MLflow run id (None if not logged)."""

    report_path: Path
    run_id: str | None


def report_path(
    reports_dir: Path, criterion: str, model: str, prompt_version: str, years: Sequence[int]
) -> Path:
    """docs/reports/<criterion>-<model>-<version>-<years>.md, e.g. `...-v2-2018-2019.md`."""
    # Windows rejects < > : " / \ | ? * in a filename; an Ollama tag like qwen2.5:14b has a colon.
    name = re.sub(r'[<>:"/\\|?*]', "-", f"{criterion}-{model}-{prompt_version}")
    return reports_dir / f"{name}-{years_label(years)}.md"


def report_date(now: datetime) -> str:
    """The calendar day printed on a report: the owner's day in California, not UTC's.

    Timestamps are stored in UTC; a day shown to a person is America/Los_Angeles, or an
    evening run would be dated tomorrow.
    """
    return now.astimezone(ZoneInfo("America/Los_Angeles")).date().isoformat()


def _literature(
    engine: Engine,
    abstracts_dir: Path,
    scores: dict[str, int],
    threshold: int,
    judged: dict[str, GoldRow],
    relevant: list[str],
) -> Literature:
    """What the report of a criterion over the abstracts corpus adds (report.Literature).

    The record counts are queries over the custody table. Harvest recall is counted by
    *work*: a seed is flagged, judged or relevant if any document stored for it is. The
    judge's labels on seeds outside the flagged draw are read here and nowhere else in
    the report, so precision stays a random sample. `relevant` are the documents listed
    as kept: those of the flagged sample the judge labeled relevant.
    """
    by_work = doc_ids_by_work(abstracts_dir)
    work_of = {d: w for w, docs in by_work.items() for d in docs}
    anchors = {a.work_id: a.label for a in ANCHORS}
    identified = [
        Identified(
            q.route,
            f"{anchors[q.query]} ({q.query})" if q.query in anchors else q.query,
            q.search_form,
            q.works,
        )
        for q in works_per_query(engine)
    ]
    seeds: dict[str, Seed] = seed_works(engine)
    flagged_seeds = judged_seeds = 0
    found: list[bool] = []  # one per relevant seed: did a search term return it too
    for work, seed in seeds.items():
        docs = [d for d in by_work.get(work, ()) if scores.get(d, -1) >= threshold]
        if not docs:
            continue
        flagged_seeds += 1
        labels = [judged[d].label for d in docs if d in judged]
        if not labels:
            continue
        judged_seeds += 1
        if any(labels):
            found.append(seed.by_search)
    titles = load_texts(abstracts_dir)
    return Literature(
        identified=identified,
        distinct_works=distinct_works(engine),
        seeds=len(seeds),
        seeds_flagged=flagged_seeds,
        seeds_judged=judged_seeds,
        seeds_relevant=len(found),
        harvest_recall=metrics.proportion(sum(found), len(found)) if found else None,
        # The stored text is the title, a blank line and the abstract.
        works=[(work_of.get(d, d), titles.get(d, "").split("\n", 1)[0].strip()) for d in relevant],
    )


@flow(name="evaluate")
def evaluate(
    criterion: str,
    prompt_version: str = "v2",
    *,
    years: list[int],
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
    flagged_n: int | None = None,
) -> EvalResult:
    """Compute the metrics of the review run (review_model, `prompt_version`) over the
    paragraphs filed in `years`, from the judge's labels; write the Markdown report and
    log one MLflow run for it.

    Everything is read for that population only: the scores, their cost (each score's
    own `cost_usd`, summed), the review jobs that scored them and the grading spend.

    Flagged = every doc of the population scoring >= `threshold` under the pair; each
    must carry a label by `judge_model`, otherwise MissingLabels (re-running `elusion
    judge` fills them). The discard sample = the seeded draw of `discard_n` from the
    population's own discard pile
    (`split_piles`, the same draw `elusion judge` made); each drawn doc must carry a label
    by `judge_model`, otherwise MissingDiscardLabels. Labels for docs outside the draw
    (e.g. flagged by another review run) are never counted, so elusion stays a uniform
    sample. Tests pass a fake `engine` and their own paths.

    With `flagged_n` (the value `elusion judge` was given), precision is measured on the
    seeded sample of the flagged pile instead of the whole pile: only the drawn ids need
    a label, and only they are counted, in precision, in the rationale grades and in the
    list of what was found. A label outside the draw (a citation seed, say) is ignored
    here. Recall and richness still weight the two rates by the sizes of the whole piles,
    because the sample estimates the rate of the pile it was drawn from. The report also
    splits precision by score, as a description of the same sample.

    An unfinished review job that scored the population only warns (on stderr): the report is still
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
    years = sorted(set(years))

    jobs = jobs_summary(engine, review_model, version, years)
    if jobs["unfinished"]:
        msg = _unfinished_msg(jobs, review_model, version)
        msg += "; the scores may be partial, so this report may be too"
        print(f"WARNING: {msg}", file=sys.stderr)
        log.warning(msg)

    scores = scores_for(engine, review_model, version, years)
    if not scores:
        raise RuntimeError(f"no scores for ({review_model}, {version}) filed in {years}")
    n_flagged = sum(1 for s in scores.values() if s >= threshold)
    piles = split_piles(scores, threshold, discard_n, seed, flagged_n)  # both draws, one call
    flagged = {d for d, pile in piles if pile == "flagged"}  # the whole pile without a limit
    judge_prompt = f"{criterion}/{judge_version(criterion)}"
    judged = {r.doc_id: r for r in judge_rows_for(csv, judge_model, judge_prompt)}
    # Ids the judge refused to answer about and has not labeled since. They are left out
    # of both samples and counted in the report; the pile sizes do not change.
    refused = refused_ids(refusals_path(csv), judge_model, judge_prompt) - judged.keys()
    refused_flagged = flagged & refused
    flagged -= refused
    missing = flagged - judged.keys()
    if missing and flagged_n is not None:
        raise MissingLabels(
            f"{len(missing)} flagged paragraphs of the draw have no {judge_model} label in "
            f"{csv}, e.g. {min(missing)}; re-run `elusion judge` with the same --flagged-n "
            "and seed"
        )
    if missing:
        raise MissingLabels(
            f"{len(missing)} flagged paragraphs have no {judge_model} label in {csv}, "
            f"e.g. {min(missing)}; re-run `elusion judge` to fill them, or, if the judge "
            "was given --flagged-n, pass the same value here"
        )
    flagged_rows = [judged[d] for d in sorted(flagged)]
    draw = {d for d, pile in piles if pile == "discard"}
    refused_discard = draw & refused
    draw -= refused
    missing_d = draw - judged.keys()
    if missing_d:
        raise MissingDiscardLabels(
            f"{len(missing_d)} sampled discard paragraphs have no {judge_model} label in {csv}; "
            "re-run `elusion judge` with the same seed"
        )
    discard_rows = [judged[d] for d in sorted(draw)]
    n_discard = len(scores) - n_flagged

    precision = metrics.precision(flagged_rows)
    precision_by_score = None
    if flagged_n is not None:
        precision_by_score = {}
        for s in range(threshold, 5):
            labels = [r.label for r in flagged_rows if scores[r.doc_id] == s]
            if labels:  # a score nothing was drawn at has no rate to report
                precision_by_score[s] = metrics.proportion(sum(labels), len(labels))
    elusion = metrics.elusion(discard_rows)
    humans = human_rows(csv)
    shared = [h for h in humans if h.doc_id in judged]
    agreement = metrics.agreement(shared, list(judged.values())) if shared else None

    wall_clock = timedelta(seconds=jobs["busy_s"]) if jobs["jobs"] else None
    relevant = [r.doc_id for r in flagged_rows if r.label == 1]
    corpus = corpus_for(criterion)
    literature = None
    if corpus is OPENALEX:
        abstracts_dir = corpus.text_dir(data_dir or settings.data_dir)
        literature = _literature(engine, abstracts_dir, scores, threshold, judged, relevant)

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
            grade_cost_usd=grade_cost_usd(
                grade_runs_path(grades_csv), review_model, version, years
            ),
        )

    report = RunReport(
        criterion=criterion,
        review_model=review_model,
        prompt_version=version,
        judge_model=judge_model,
        judge_prompt_version=", ".join(sorted({r.prompt_version for r in judged.values()})),
        date=today or report_date(datetime.now(UTC)),
        seed=seed,
        discard_n=discard_n,
        flagged_n=flagged_n,
        precision_by_score=precision_by_score,
        threshold=threshold,
        years=tuple(years),
        filed=sum(count_documents(engine, y, corpus.name) for y in years),
        population=len(scores),
        n_flagged=n_flagged,
        n_discard=n_discard,
        precision=precision,
        elusion=elusion,
        richness=metrics.richness(precision, elusion, n_flagged, n_discard),
        recall=metrics.recall(precision, elusion, n_flagged, n_discard),
        agreement=agreement,
        review_cost_usd=scores_cost_usd(engine, review_model, version, years),
        judge_cost_criterion_usd=judge_cost_usd(judge_runs_path(csv)),
        review_jobs=jobs["jobs"],
        review_wall_clock=wall_clock,
        review_unfinished=jobs["unfinished"],
        review_envs=jobs["run_envs"],
        companies=[] if literature else company_counts(engine, relevant),
        literature=literature,
        refused_flagged=len(refused_flagged),
        refused_discard=len(refused_discard),
        **graded,
    )
    path = report_path(reports_dir, criterion, review_model, prompt_version, years)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(report), encoding="utf-8")
    log.info("report written to %s", path)
    if not log_mlflow:
        return EvalResult(path, None)
    provider = provider_for(engine, review_model, version) or "unknown"
    prompt_path = PROMPTS_DIR / criterion / f"{prompt_version}.md"
    artifacts = [
        path,
        prompt_path,
        Path("criteria") / f"{criterion}.md",
    ]
    try:
        run_id = log_run(
            report,
            provider,
            [a for a in artifacts if a.is_file()],
            settings.mlflow_tracking_uri,
            path,
            prompt_path=prompt_path if prompt_path.is_file() else None,
        )
    except Exception as exc:
        raise RuntimeError(f"report written to {path}, but MLflow logging failed: {exc}") from exc
    log.info("MLflow run %s", run_id)
    return EvalResult(path, run_id)
