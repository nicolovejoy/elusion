"""Command-line entry point. `uv run elusion --help`.

Typer builds the CLI from type-annotated functions: each decorated function is a
subcommand, its parameters become options/arguments, and the docstring is the help text.
"""

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


if __name__ == "__main__":
    app()


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
