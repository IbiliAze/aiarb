"""`aiarb plan`: start a run."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from aiarb.cli.common import (
    NoInputOption,
    RepoOption,
    charter_of,
    context_for,
    max_rounds_option,
    read_text,
    setup_of,
)
from aiarb.cli.init import ignore_runs
from aiarb.cli.session import Session
from aiarb.config.charter import CHARTER_PATH
from aiarb.graph import runner
from aiarb.graph.state import initial_state

if TYPE_CHECKING:
    from collections.abc import Sequence

    from aiarb.cli.setup import ExitCode, Setup


def plan(
    ctx: typer.Context,
    feature: Annotated[
        str | None,
        typer.Option(
            "--feature",
            "-f",
            help="What to build, in a sentence or two. Leave it out and the planner "
            "proposes the most valuable next change.",
        ),
    ] = None,
    plans: Annotated[
        list[Path] | None,
        typer.Option(
            "--plan",
            "-p",
            help="A plan you already have, to critique instead of drafting one. Repeat it "
            "for a plan in several files.",
            exists=True,
            dir_okay=False,
        ),
    ] = None,
    max_rounds: Annotated[
        int | None, max_rounds_option("Rounds of debate. Default: the charter's.")
    ] = None,
    repo: RepoOption = Path("."),
    no_input: NoInputOption = False,
) -> None:
    """Start a run: plan a feature, or critique a plan you have, against this repo."""
    setup = setup_of(ctx)
    can_ask = not no_input and setup.can_ask()
    code = start_run(
        setup, repo, feature=feature, plans=plans or [], max_rounds=max_rounds, can_ask=can_ask
    )
    raise typer.Exit(code)


def start_run(
    setup: Setup,
    repo: Path,
    *,
    feature: str | None,
    plans: Sequence[Path],
    max_rounds: int | None,
    can_ask: bool,
) -> ExitCode:
    console = setup.console
    charter = charter_of(setup, repo)
    given_plans = [read_text(setup, path, "the plan") for path in plans]
    context = context_for(setup, charter, repo, can_ask=can_ask)
    feature = (feature or "").strip() or None
    if not (repo / CHARTER_PATH).exists():
        console.print(f"[dim]No {CHARTER_PATH}: using the defaults. `aiarb init` writes one.[/]")
    if feature is None and not given_plans:
        console.print(
            "[dim]No feature given: the planner will propose the most valuable next change, "
            "and state that as its first assumption.[/]"
        )
    ignore_runs(repo)
    state = initial_state(feature=feature, given_plans=given_plans, max_rounds=max_rounds)
    store = setup.stores(repo)
    session = Session(setup, store, charter, can_ask)
    return session.run(
        context, lambda progress: runner.start(store, context, state, progress=progress)
    )
