"""The `aiarb` command-line entry point.

Exit codes 0, 1 and 2 say how a finished run came out, so nothing else may use
them: bad usage and unexpected errors exit with 4, as a run that stops part way
does, and Ctrl-C with 130.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer
from typer.core import TyperGroup

from aiarb import __version__
from aiarb.cli import init, plan, resume
from aiarb.cli.common import setup_of
from aiarb.cli.interactive import guide
from aiarb.cli.setup import ExitCode


class _Group(TyperGroup):
    def make_context(
        self, info_name: str | None, args: list[str], parent: Any = None, **extra: Any
    ) -> Any:
        try:
            return super().make_context(info_name, args, parent, **extra)
        except typer.TyperException as error:
            error.exit_code = ExitCode.ERROR
            raise

    def invoke(self, ctx: Any) -> Any:
        try:
            return super().invoke(ctx)
        except typer.Exit:
            raise
        except typer.Abort as error:
            raise typer.Exit(ExitCode.INTERRUPTED) from error
        except typer.TyperException as error:
            # Bad usage, such as a plan file that does not exist.
            error.exit_code = ExitCode.ERROR
            raise
        except KeyboardInterrupt as error:
            raise typer.Exit(ExitCode.INTERRUPTED) from error
        except Exception as error:
            setup_of(ctx).console.print_exception()
            raise typer.Exit(ExitCode.ERROR) from error


app = typer.Typer(
    name="aiarb",
    cls=_Group,
    invoke_without_command=True,
)

app.command(name="init")(init.init)
app.command(name="plan")(plan.plan)
app.command(name="resume")(resume.resume)


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"aiarb {__version__}")
        raise typer.Exit


@app.callback()
def main(
    ctx: typer.Context,
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
    """An AI architecture review board for your codebase.

    Run it with no command for a guided session.
    """
    if ctx.invoked_subcommand is not None:
        return
    setup = setup_of(ctx)
    if not setup.can_ask():
        typer.echo(ctx.get_help())
        raise typer.Exit
    raise typer.Exit(guide(setup, Path.cwd()))
