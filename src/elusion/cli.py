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
    concurrency: Annotated[int, typer.Option(help="Parallel LLM calls per batch.")] = 8,
    prompt_version: Annotated[str, typer.Option(help="Prompt file version.")] = "v1",
    max_run_usd: Annotated[
        float | None,
        typer.Option(help="Refuse runs projected above this; default ELUSION_MAX_RUN_USD."),
    ] = None,
) -> None:
    """Score up to SAMPLE paragraphs from the topic against a CRITERION."""
    from elusion.flows.review import review as review_flow

    if not criterion.is_file():
        raise typer.BadParameter(f"criterion file not found: {criterion}", param_hint="--criterion")

    summary = review_flow(
        criterion.stem,
        sample,
        batch_size=batch_size,
        concurrency=concurrency,
        prompt_version=prompt_version,
        max_run_usd=max_run_usd,
    )
    typer.echo(summary)


# Mirrors evaluate.gold.DEFAULT_SEED; a literal so `elusion --help` does not import the
# gold module (and through it SQLAlchemy and the Anthropic SDK). A test keeps them equal.
DEFAULT_SEED = 20261104


@app.command()
def judge(
    criterion: Annotated[
        Path, typer.Option(help="Criterion file, e.g. criteria/cloud-concentration.md.")
    ],
    prompt_version: Annotated[
        str, typer.Option(help="Review prompt version whose scores define the two piles.")
    ],
    threshold: Annotated[int, typer.Option(help="Score at or above this is 'flagged'.")] = 2,
    discard_n: Annotated[
        int, typer.Option(help="Paragraphs sampled at random from the discard pile.")
    ] = 200,
    seed: Annotated[int, typer.Option(help="Seed for the discard sample.")] = DEFAULT_SEED,
    max_run_usd: Annotated[
        float | None,
        typer.Option(help="Refuse runs projected above this; default ELUSION_MAX_RUN_USD."),
    ] = None,
) -> None:
    """Have the judge model label the gold set (flagged pile + discard sample) to a CSV."""
    from elusion.flows.evaluate import judge as judge_flow

    if not criterion.is_file():
        raise typer.BadParameter(f"criterion file not found: {criterion}", param_hint="--criterion")

    summary = judge_flow(
        criterion.stem,
        prompt_version=prompt_version,
        threshold=threshold,
        discard_n=discard_n,
        seed=seed,
        max_run_usd=max_run_usd,
    )
    typer.echo(summary)


@app.command()
def label(
    criterion: Annotated[
        Path, typer.Option(help="Criterion file, e.g. criteria/cloud-concentration.md.")
    ],
    prompt_version: Annotated[
        str,
        typer.Option(
            help="Accepted for symmetry with judge/evaluate; unused here, since the judge "
            "rows already carry the judge prompt version."
        ),
    ],
    n: Annotated[int, typer.Option(help="Paragraphs to label: n/2 flagged, n/2 discard.")] = 20,
    seed: Annotated[int, typer.Option(help="Seed for the random pick.")] = DEFAULT_SEED,
) -> None:
    """Label judge-labeled paragraphs yourself, blind, for the human audit."""
    from elusion.evaluate.label import label as label_session

    if not criterion.is_file():
        raise typer.BadParameter(f"criterion file not found: {criterion}", param_hint="--criterion")
    typer.echo(label_session(criterion.stem, n, seed))


@app.command()
def evaluate(
    criterion: Annotated[
        Path, typer.Option(help="Criterion file, e.g. criteria/cloud-concentration.md.")
    ],
    prompt_version: Annotated[
        str, typer.Option(help="Review prompt version whose scores define the two piles.")
    ],
    threshold: Annotated[int, typer.Option(help="Score at or above this is 'flagged'.")] = 2,
    seed: Annotated[
        int, typer.Option(help="Seed the gold set was drawn with; only echoed into the report.")
    ] = DEFAULT_SEED,
) -> None:
    """Compute recall, precision, elusion and richness from the judge labels; write the report."""
    from elusion.flows.evaluate import evaluate as evaluate_flow

    if not criterion.is_file():
        raise typer.BadParameter(f"criterion file not found: {criterion}", param_hint="--criterion")
    path = evaluate_flow(
        criterion.stem, prompt_version=prompt_version, threshold=threshold, seed=seed
    )
    typer.echo(path)


if __name__ == "__main__":
    app()
