"""The `aiarb` command-line entry point."""

from typing import Annotated

import typer

from aiarb import __version__

app = typer.Typer(
    name="aiarb",
    help="An AI architecture review board for your codebase.",
    no_args_is_help=True,
)


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"aiarb {__version__}")
        raise typer.Exit


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_print_version,
            is_eager=True,
            help="Show the version and exit.",
        ),
    ] = False,
) -> None:
    """An AI architecture review board for your codebase."""
