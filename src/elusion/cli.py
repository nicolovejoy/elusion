"""Command-line entry point. `uv run elusion --help`.

Typer builds the CLI from type-annotated functions: each decorated function is a
subcommand, its parameters become options/arguments, and the docstring is the help text.
"""

import typer

from elusion import __version__

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
