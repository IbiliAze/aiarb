"""What the commands share: their common options, and turning the repo's setup
into a run's context, failing with a clear message when it cannot be done."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, NoReturn

import typer
from rich.markup import escape

from aiarb.adapters.scoring import MissingKeyError
from aiarb.cli.setup import ExitCode, Setup
from aiarb.config.charter import CharterError, load_charter
from aiarb.graph.context import RunContext

if TYPE_CHECKING:
    from aiarb.config.charter import Charter

RepoOption = Annotated[
    Path,
    typer.Option(
        "--repo",
        help="The repo to review. Default: the current directory.",
        exists=True,
        file_okay=False,
        resolve_path=True,
        show_default=False,
    ),
]
NoInputOption = Annotated[
    bool,
    typer.Option(
        "--no-input",
        help="Never ask anything. A run that needs your decisions stops and says how to give them.",
    ),
]


def max_rounds_option(help: str) -> Any:
    return typer.Option("--max-rounds", min=1, metavar="N", help=help)


def setup_of(ctx: typer.Context) -> Setup:
    """The setup the commands run with: the one given to the app, as tests do, or
    the real one."""
    return ctx.ensure_object(Setup)


def fail(setup: Setup, message: str) -> NoReturn:
    setup.console.print(f"[bold red]Error:[/] {escape(message)}")
    raise typer.Exit(ExitCode.ERROR)


def charter_of(setup: Setup, repo: Path) -> Charter:
    try:
        return load_charter(repo)
    except CharterError as error:
        fail(setup, str(error))


def context_for(setup: Setup, charter: Charter, repo: Path) -> RunContext:
    try:
        ports = setup.ports(charter, repo)
    except MissingKeyError as error:
        fail(setup, str(error))
    return RunContext(
        charter=charter,
        repo=repo,
        agent=ports.agent,
        scorer=ports.scorer,
        confirmer=ports.confirmer,
    )


def read_text(setup: Setup, path: Path, what: str) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        fail(setup, f"{what} {path} is not UTF-8 text")
    except OSError as error:
        fail(setup, f"cannot read {what} {path}: {error.strerror or error}")
    if not text.strip():
        fail(setup, f"{what} {path} is empty")
    return text


def by_id(setup: Setup, values: list[str] | None, option: str) -> dict[str, str]:
    """`ID=TEXT` options as text by id. `ID=@path` reads the text from a file."""
    given: dict[str, str] = {}
    for value in values or ():
        id, equals, text = value.partition("=")
        id = id.strip().upper()
        if not equals or not id:
            fail(setup, f'{option} takes ID=TEXT, such as {option} OBJ-1="...", not {value!r}')
        if id in given:
            fail(setup, f"{option} {id} is given twice")
        if text.startswith("@"):
            text = read_text(setup, Path(text[1:]).expanduser(), "the file")
        if not text.strip():
            fail(setup, f"{option} {id} is blank")
        given[id] = text.strip()
    return given
