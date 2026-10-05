"""`aiarb resume`: carry on a run, with the user's decisions or new evidence."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from aiarb.cli.common import (
    NoInputOption,
    RepoOption,
    by_id,
    charter_of,
    context_for,
    fail,
    max_rounds_option,
    setup_of,
)
from aiarb.cli.session import Session, fill_in
from aiarb.graph import runner
from aiarb.graph.runner import CannotReopen, NotWaiting
from aiarb.ports.run_store import RunNotFound

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from aiarb.cli.setup import ExitCode, Setup
    from aiarb.graph.context import RunContext
    from aiarb.graph.runner import Progress, RunResult
    from aiarb.ports.run_store import RunStore

    Begin = Callable[[RunStore, RunContext, Progress], Awaitable[RunResult]]


def resume(
    ctx: typer.Context,
    run_id: Annotated[
        str | None,
        typer.Argument(help="The run to carry on. Default: the most recently active one."),
    ] = None,
    decide: Annotated[
        list[str] | None,
        typer.Option(
            "--decide",
            "-d",
            metavar="ID=DECISION",
            help="Your decision on a trade-off the run is waiting on. Repeat it for each. "
            "ID=@file reads the decision from a file.",
        ),
    ] = None,
    evidence: Annotated[
        list[str] | None,
        typer.Option(
            "--evidence",
            "-e",
            metavar="ID=EVIDENCE",
            help="New evidence that reopens an item for another debate. Repeat it for each "
            "item. ID=@file reads the evidence from a file.",
        ),
    ] = None,
    max_rounds: Annotated[
        int | None,
        max_rounds_option("Rounds for the reopened debate. Default: the run's."),
    ] = None,
    repo: RepoOption = Path("."),
    no_input: NoInputOption = False,
) -> None:
    """Carry on a run with your decisions or new evidence, or from where it stopped."""
    setup = setup_of(ctx)
    code = resume_run(
        setup,
        repo,
        run_id,
        decisions=by_id(setup, decide, "--decide"),
        evidence=by_id(setup, evidence, "--evidence"),
        max_rounds=max_rounds,
        can_ask=not no_input and setup.can_ask(),
    )
    raise typer.Exit(code)


def resume_run(
    setup: Setup,
    repo: Path,
    run_id: str | None,
    *,
    decisions: Mapping[str, str],
    evidence: Mapping[str, str],
    max_rounds: int | None,
    can_ask: bool,
) -> ExitCode:
    store = setup.stores(repo)
    charter = charter_of(setup, repo)
    if run_id is None:
        runs = store.runs()
        if not runs:
            fail(setup, "this repo has no runs yet: start one with `aiarb plan`")
        run_id = runs[0]
    try:
        before = asyncio.run(runner.load(store, run_id))
    except (RunNotFound, ValueError) as error:
        fail(setup, str(error))
    if max_rounds is not None and not evidence:
        fail(setup, "--max-rounds sets the rounds of a reopened debate: give --evidence too")
    session = Session(setup, store, charter, can_ask)
    # Set up the agent and scorers before asking for decisions, so a setup
    # problem does not throw the answers away.
    will_run = evidence or before.stopped or (before.paused and (decisions or can_ask))
    context = context_for(setup, charter, repo) if will_run else None
    begin = _plan(setup, before, decisions, evidence, max_rounds, can_ask)
    if begin is None or context is None:
        return session.report(before)
    try:
        return session.run(context, lambda progress: begin(store, context, progress))
    except (CannotReopen, NotWaiting, ValueError) as error:
        fail(setup, str(error))


def _plan(
    setup: Setup,
    before: RunResult,
    decisions: Mapping[str, str],
    evidence: Mapping[str, str],
    max_rounds: int | None,
    can_ask: bool,
) -> Begin | None:
    """How to carry the run on, or None to report it as it is."""
    run_id = before.run_id
    if evidence:
        answers = dict(decisions) or None
        return lambda store, context, progress: runner.reopen(
            store,
            run_id,
            context,
            evidence,
            answers=answers,
            max_rounds=max_rounds,
            progress=progress,
        )
    if before.request is not None:
        asked = [question["id"] for question in before.request["questions"]]
        unasked = sorted(set(decisions) - set(asked))
        if unasked:
            fail(
                setup,
                f"run {run_id} is not waiting for a decision on {', '.join(unasked)}; "
                f"it asks about {', '.join(asked)}",
            )
        answers = fill_in(setup, before.request, decisions, can_ask=can_ask)
        if answers is None:
            missing = [id for id in asked if id not in decisions]
            if decisions:
                fail(setup, f"give a decision on each question; missing: {', '.join(missing)}")
            return None
        return lambda store, context, progress: runner.resume(
            store, run_id, context, answers, progress=progress
        )
    if decisions:
        fail(setup, f"run {run_id} is not waiting for decisions")
    if before.stopped:
        return lambda store, context, progress: runner.resume(
            store, run_id, context, progress=progress
        )
    setup.console.print(
        f"Run {run_id} has finished. To reopen items with new evidence, give "
        "--evidence ID=EVIDENCE.",
        soft_wrap=True,
    )
    return None
