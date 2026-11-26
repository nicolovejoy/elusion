"""Command-line entry point. `uv run elusion --help`.

Typer builds the CLI from type-annotated functions: each decorated function is a
subcommand, its parameters become options/arguments, and the docstring is the help text.
"""

from pathlib import Path
from typing import Annotated

import typer

from elusion import (
    __version__,
    config,  # noqa: F401  (loads .env and sets PREFECT_API_URL before prefect imports)
)

app = typer.Typer(no_args_is_help=True)


# With only one subcommand Typer would flatten the app into that command. A callback
# keeps `elusion` a command group so later phases add `collect`, `review` and
# `evaluate` beside `version`.
@app.callback()
def main() -> None:
    """Document review over SEC 10-K risk factors."""


@app.command()
def version() -> None:
    """Print the installed elusion version."""
    typer.echo(__version__)


@app.command()
def collect(
    year: Annotated[list[int], typer.Option(help="Filing year; repeat for several.")],
    limit: Annotated[
        int | None, typer.Option(help="Stop after this many filings per year.")
    ] = None,
) -> None:
    """Download Item 1A for each YEAR, split into paragraphs, store and publish."""
    from elusion.flows.collect import collect as collect_flow

    for y in year:
        summary = collect_flow(y, limit)
        typer.echo(f"{y}: {summary}")


@app.command()
def harvest(
    max_pages: Annotated[
        int | None,
        typer.Option(help="Stop after this many pages of 100 works per search term."),
    ] = None,
    seeds_only: Annotated[
        bool,
        typer.Option(
            "--seeds-only",
            help="Skip the searches; draw only the citation samples not yet drawn.",
        ),
    ] = False,
) -> None:
    """Fetch the abstracts the search terms return from OpenAlex, and a sample of the
    works citing each anchor paper; store and publish them."""
    from elusion.flows.harvest import harvest as harvest_flow

    typer.echo(harvest_flow(max_pages=max_pages, seeds_only=seeds_only))


@app.command()
def review(
    criterion: Annotated[
        Path, typer.Option(help="Criterion file, e.g. criteria/cloud-concentration.md.")
    ],
    sample: Annotated[
        int,
        typer.Option(
            help="Pull the next SAMPLE ids for this criterion/model/prompt-version consumer "
            "group; a re-run continues from the committed offset, it does not re-score."
        ),
    ],
    batch_size: Annotated[int, typer.Option(help="Ids per batch (and per offset commit).")] = 50,
    concurrency: Annotated[
        int,
        typer.Option(help="Parallel LLM calls per batch; 2 is sensible for a local ollama model."),
    ] = 8,
    prompt_version: Annotated[
        str | None, typer.Option(help="Prompt file version; default v1.")
    ] = None,
    max_run_usd: Annotated[
        float | None,
        typer.Option(help="Refuse runs projected above this; default ELUSION_MAX_RUN_USD."),
    ] = None,
    provider: Annotated[
        str | None, typer.Option(help="Provider: anthropic (default), openai or ollama.")
    ] = None,
    model: Annotated[
        str | None,
        typer.Option(help="Pinned model id; default ELUSION_REVIEW_MODEL / ELUSION_OPENAI_MODEL."),
    ] = None,
    reviewer: Annotated[
        str | None,
        typer.Option(
            help="Registered reviewer NAME@ALIAS, e.g. elusion-reviewer@champion; it sets "
            "provider, model and prompt version, so those flags must not be given."
        ),
    ] = None,
) -> None:
    """Score up to SAMPLE paragraphs from the topic against a CRITERION."""
    from elusion.flows.review import review as review_flow

    if not criterion.is_file():
        raise typer.BadParameter(f"criterion file not found: {criterion}", param_hint="--criterion")

    try:
        summary = review_flow(
            criterion.stem,
            sample,
            batch_size=batch_size,
            concurrency=concurrency,
            prompt_version=prompt_version,
            max_run_usd=max_run_usd,
            provider_name=provider,
            model=model,
            reviewer=reviewer,
        )
    except ValueError as exc:
        # Only the owner's input problems get a one-line error: a bad NAME@ALIAS, missing alias,
        # wrong criterion, flag clash (ReviewerError), a bad provider name or missing key
        # (ProviderConfigError). Anything else, even another ValueError or a KeyError from the
        # corpus, is a bug and keeps its traceback. Imported here, on the error path, so
        # `elusion --help` stays fast (the registry imports mlflow).
        from elusion.review.providers import ProviderConfigError
        from elusion.review.registry import ReviewerError

        if not isinstance(exc, ReviewerError | ProviderConfigError):
            raise
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(summary)


@app.command()
def promote(
    run: Annotated[str, typer.Option(help="MLflow run id whose reviewer to register.")],
    name: Annotated[str, typer.Option(help="Registered model name.")] = "elusion-reviewer",
    alias: Annotated[str, typer.Option(help="Alias to point at the new version.")] = "champion",
) -> None:
    """Register a run's reviewer as a new model version and alias it (default: champion)."""
    from elusion.config import settings
    from elusion.review.registry import ReviewerError, registry_url
    from elusion.review.registry import promote as promote_run

    try:
        version = promote_run(run, name, alias, settings.mlflow_tracking_uri)
    except ReviewerError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"{name} version {version} aliased {alias}")
    typer.echo(f"{registry_url(settings.mlflow_tracking_uri)}/{name}")


# Mirrors evaluate.gold.DEFAULT_SEED; a literal so `elusion --help` does not import the
# gold module (and through it SQLAlchemy and the Anthropic SDK). A test keeps them equal.
DEFAULT_SEED = 20261104


# A pair's scores can cover several populations (2018 and 2019 scored on the laptop, 2020
# on the cluster). judge, grade and evaluate each work on one, so the option is required:
# no default can mix years by accident.
Years = Annotated[
    list[int],
    typer.Option(
        "--year",
        help="Filing year of the paragraphs to evaluate; repeat it for several "
        "(--year 2018 --year 2019).",
    ),
]


@app.command()
def judge(
    criterion: Annotated[
        Path, typer.Option(help="Criterion file, e.g. criteria/cloud-concentration.md.")
    ],
    prompt_version: Annotated[
        str, typer.Option(help="Review prompt version whose scores define the two piles.")
    ],
    year: Years,
    threshold: Annotated[int, typer.Option(help="Score at or above this is 'flagged'.")] = 3,
    discard_n: Annotated[
        int, typer.Option(help="Paragraphs sampled at random from the discard pile.")
    ] = 200,
    seed: Annotated[int, typer.Option(help="Seed for the discard sample.")] = DEFAULT_SEED,
    review_model: Annotated[
        str | None,
        typer.Option(help="Pinned review model of the pair; default ELUSION_REVIEW_MODEL."),
    ] = None,
    max_run_usd: Annotated[
        float | None,
        typer.Option(help="Refuse runs projected above this; default ELUSION_MAX_RUN_USD."),
    ] = None,
    force: Annotated[
        bool,
        typer.Option(
            help="Label even though a review job for the pair has no finished_at "
            "(only for a job whose process was killed)."
        ),
    ] = False,
    flagged_n: Annotated[
        int | None,
        typer.Option(
            help="Label a seeded random sample of this many flagged paragraphs; "
            "default: the whole flagged pile."
        ),
    ] = None,
    only_labeled: Annotated[
        bool,
        typer.Option(
            "--only-labeled",
            help="Label only the drawn paragraphs the owner has already labeled with "
            "`elusion label`.",
        ),
    ] = False,
    seeds: Annotated[
        bool,
        typer.Option(
            "--seeds",
            help="Also label the flagged citation seeds the flagged sample left out "
            "(abstracts only; they feed harvest recall, never precision).",
        ),
    ] = False,
) -> None:
    """Have the judge model label the gold set (flagged pile + discard sample) to a CSV."""
    from elusion.flows.evaluate import ReviewStillRunning
    from elusion.flows.evaluate import judge as judge_flow

    if not criterion.is_file():
        raise typer.BadParameter(f"criterion file not found: {criterion}", param_hint="--criterion")

    if seeds and only_labeled:
        raise typer.BadParameter(
            "--seeds cannot be combined with --only-labeled: the seeds are not among "
            "the paragraphs the owner labels",
            param_hint="--seeds",
        )

    try:
        summary = judge_flow(
            criterion.stem,
            prompt_version=prompt_version,
            years=sorted(set(year)),
            threshold=threshold,
            discard_n=discard_n,
            seed=seed,
            review_model=review_model,
            max_run_usd=max_run_usd,
            force=force,
            flagged_n=flagged_n,
            only_human=only_labeled,
            seeds=seeds,
        )
    except ReviewStillRunning as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None
    typer.echo(summary)


@app.command()
def grade(
    criterion: Annotated[
        Path, typer.Option(help="Criterion file, e.g. criteria/cloud-concentration.md.")
    ],
    prompt_version: Annotated[
        str, typer.Option(help="Review prompt version whose flagged rationales are graded.")
    ],
    year: Years,
    review_model: Annotated[
        str | None,
        typer.Option(help="Pinned review model of the pair; default ELUSION_REVIEW_MODEL."),
    ] = None,
    threshold: Annotated[int, typer.Option(help="Score at or above this is 'flagged'.")] = 3,
    max_run_usd: Annotated[
        float | None,
        typer.Option(help="Refuse runs projected above this; default ELUSION_MAX_RUN_USD."),
    ] = None,
    flagged_n: Annotated[
        int | None,
        typer.Option(
            help="Grade only the seeded flagged sample of this size, as given to `judge`; "
            "default: the whole flagged pile."
        ),
    ] = None,
    seed: Annotated[
        int, typer.Option(help="Seed the flagged sample was drawn with.")
    ] = DEFAULT_SEED,
    force: Annotated[
        bool,
        typer.Option(
            help="With --flagged-n: grade even though a review job for the pair has no "
            "finished_at (only for a job whose process was killed)."
        ),
    ] = False,
) -> None:
    """Have the grader model judge whether each flagged rationale is a sound reading."""
    from elusion.flows.evaluate import ReviewStillRunning
    from elusion.flows.grade import grade as grade_flow

    if not criterion.is_file():
        raise typer.BadParameter(f"criterion file not found: {criterion}", param_hint="--criterion")
    try:
        summary = grade_flow(
            criterion.stem,
            prompt_version=prompt_version,
            years=sorted(set(year)),
            review_model=review_model,
            threshold=threshold,
            max_run_usd=max_run_usd,
            flagged_n=flagged_n,
            seed=seed,
            force=force,
        )
    except ReviewStillRunning as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None
    typer.echo(summary)


@app.command()
def label(
    criterion: Annotated[
        Path, typer.Option(help="Criterion file, e.g. criteria/cloud-concentration.md.")
    ],
    prompt_version: Annotated[
        str,
        typer.Option(
            help="Review prompt version whose scores define the two piles; read only "
            "when --year is given."
        ),
    ],
    n: Annotated[int, typer.Option(help="Paragraphs to label: n/2 flagged, n/2 discard.")] = 20,
    seed: Annotated[
        int,
        typer.Option(help="Seed for the random pick and, with --year, for the draw."),
    ] = DEFAULT_SEED,
    year: Annotated[
        list[int] | None,
        typer.Option(
            "--year",
            help="Pick from the draw of the population filed in these years, before the "
            "judge has labeled it; give the same --flagged-n, --discard-n and --seed to "
            "`judge` later. Without --year, pick among the judge's labels.",
        ),
    ] = None,
    review_model: Annotated[
        str | None,
        typer.Option(help="Pinned review model of the pair; default ELUSION_REVIEW_MODEL."),
    ] = None,
    threshold: Annotated[int, typer.Option(help="Score at or above this is 'flagged'.")] = 3,
    flagged_n: Annotated[
        int | None,
        typer.Option(help="Size of the flagged sample; default: the whole flagged pile."),
    ] = None,
    discard_n: Annotated[int, typer.Option(help="Size of the discard sample.")] = 200,
    exclude: Annotated[
        list[str] | None,
        typer.Option(help="Document id never to show (already seen); repeat for several."),
    ] = None,
    force: Annotated[
        bool,
        typer.Option(
            help="With --year: draw even though a review job for the pair has no "
            "finished_at (only for a job whose process was killed)."
        ),
    ] = False,
) -> None:
    """Label paragraphs yourself, blind: before the judge (--year) or as its audit."""
    from elusion.evaluate.label import criterion_summary
    from elusion.evaluate.label import label as label_session

    if not criterion.is_file():
        raise typer.BadParameter(f"criterion file not found: {criterion}", param_hint="--criterion")
    candidates = None
    if year:
        from elusion.flows.evaluate import ReviewStillRunning, draw_gold

        try:
            candidates = draw_gold(
                criterion.stem,
                prompt_version,
                years=sorted(set(year)),
                threshold=threshold,
                discard_n=discard_n,
                seed=seed,
                review_model=review_model,
                flagged_n=flagged_n,
                force=force,
            )
        except ReviewStillRunning as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(1) from None
    typer.echo(
        label_session(
            criterion.stem,
            n,
            seed,
            candidates=candidates,
            exclude=set(exclude or ()),
            topic=criterion_summary(criterion),
        )
    )


@app.command()
def agreement(
    criterion: Annotated[
        Path, typer.Option(help="Criterion file, e.g. criteria/auditable-decisions.md.")
    ],
    judge_model: Annotated[
        str | None,
        typer.Option(help="Pinned judge model to compare with; default ELUSION_JUDGE_MODEL."),
    ] = None,
    judge_prompt: Annotated[
        str | None,
        typer.Option(
            help="Judge prompt whose labels to compare, e.g. judge-v1; default: the "
            "one the criterion uses."
        ),
    ] = None,
) -> None:
    """Count how often the judge matched your blind labels, and print each disagreement.

    Reads the gold CSV only: no model call, nothing written. Run it after `judge
    --only-labeled`, before the judge labels the rest.
    """
    import shutil

    from elusion.config import settings
    from elusion.corpora import corpus_for
    from elusion.evaluate.agreement import compare, render
    from elusion.evaluate.gold import gold_path
    from elusion.review.corpus import load_texts
    from elusion.review.prompt import judge_version

    if not criterion.is_file():
        raise typer.BadParameter(f"criterion file not found: {criterion}", param_hint="--criterion")
    judge_model = judge_model or settings.judge_model
    version = f"{criterion.stem}/{judge_prompt or judge_version(criterion.stem)}"
    try:
        comparison = compare(gold_path(criterion.stem, settings.data_dir), judge_model, version)
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None
    texts = {}
    if comparison.disagreements:
        texts = load_texts(corpus_for(criterion.stem).text_dir(settings.data_dir))
    width = shutil.get_terminal_size((80, 24)).columns
    typer.echo(render(comparison, judge_model, texts, width, version), nl=False)


@app.command()
def evaluate(
    criterion: Annotated[
        Path, typer.Option(help="Criterion file, e.g. criteria/cloud-concentration.md.")
    ],
    prompt_version: Annotated[
        str, typer.Option(help="Review prompt version whose scores define the two piles.")
    ],
    year: Years,
    threshold: Annotated[int, typer.Option(help="Score at or above this is 'flagged'.")] = 3,
    seed: Annotated[
        int, typer.Option(help="Seed the discard sample was drawn with.")
    ] = DEFAULT_SEED,
    discard_n: Annotated[
        int, typer.Option(help="Size of the discard sample, as given to `judge`.")
    ] = 200,
    review_model: Annotated[
        str | None,
        typer.Option(help="Pinned review model of the pair; default ELUSION_REVIEW_MODEL."),
    ] = None,
    flagged_n: Annotated[
        int | None,
        typer.Option(
            help="Size of the flagged sample, as given to `judge`; default: the whole "
            "flagged pile was labeled."
        ),
    ] = None,
) -> None:
    """Compute recall, precision, elusion and richness from the judge labels; write the report."""
    from elusion.flows.evaluate import MissingLabels  # MissingDiscardLabels subclasses it
    from elusion.flows.evaluate import evaluate as evaluate_flow

    if not criterion.is_file():
        raise typer.BadParameter(f"criterion file not found: {criterion}", param_hint="--criterion")
    try:
        result = evaluate_flow(
            criterion.stem,
            prompt_version=prompt_version,
            years=sorted(set(year)),
            threshold=threshold,
            seed=seed,
            discard_n=discard_n,
            review_model=review_model,
            flagged_n=flagged_n,
        )
    except (MissingLabels, ValueError) as exc:
        # metrics raises ValueError("no labeled rows in the <pile> pile") for an empty
        # sample; any other ValueError is a bug and keeps its traceback.
        if isinstance(exc, ValueError) and not str(exc).startswith("no labeled rows"):
            raise
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None
    typer.echo(result.report_path)
    if result.run_id:
        from elusion.config import settings

        typer.echo(f"mlflow run {result.run_id}")
        typer.echo(f"{settings.mlflow_tracking_uri}/#/experiments")


if __name__ == "__main__":
    app()
