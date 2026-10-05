"""`aiarb` with no arguments: a guided session.

It offers to write a charter if the repo has none, to carry on the latest run if
that one is waiting or stopped, and otherwise asks what to build and starts a
run.
"""

from __future__ import annotations

import asyncio
import shlex
from pathlib import Path
from typing import TYPE_CHECKING

from rich.markup import escape
from rich.prompt import Confirm, IntPrompt, Prompt

from aiarb.cli.common import charter_of
from aiarb.cli.init import init_repo
from aiarb.cli.plan import start_run
from aiarb.cli.resume import resume_run
from aiarb.cli.setup import ExitCode
from aiarb.config.charter import CHARTER_PATH
from aiarb.graph import runner
from aiarb.output.report import plural

if TYPE_CHECKING:
    from aiarb.cli.setup import Setup


def guide(setup: Setup, repo: Path) -> ExitCode:
    console = setup.console
    console.print("[bold]aiarb[/]: an AI architecture review board for this repo.")
    if not (repo / CHARTER_PATH).exists() and Confirm.ask(
        f"This repo has no charter. Write a starter one to {CHARTER_PATH}?",
        console=console,
        default=True,
    ):
        init_repo(setup, repo, jev=None, force=False)
    waiting = _offer_latest(setup, repo)
    if waiting is not None:
        return waiting
    charter = charter_of(setup, repo)
    feature = Prompt.ask(
        "What do you want to build? Leave it blank and the planner proposes something",
        console=console,
        default="",
        show_default=False,
    )
    plans = _ask_plans(setup, repo)
    rounds = IntPrompt.ask("Rounds of debate", console=console, default=charter.debate.max_rounds)
    while rounds < 1:
        rounds = IntPrompt.ask("At least one round", console=console, default=1)
    models = charter.models
    console.print(
        f"The planner is {escape(models.planner)} and the critic {escape(models.critic)}. "
        "A run can take an hour or more."
    )
    if not Confirm.ask("Start the review?", console=console, default=True):
        return ExitCode.OK
    return start_run(
        setup,
        repo,
        feature=feature,
        plans=plans,
        max_rounds=None if rounds == charter.debate.max_rounds else rounds,
        can_ask=True,
    )


def _offer_latest(setup: Setup, repo: Path) -> ExitCode | None:
    """Offer to carry on the latest run if it is waiting or stopped. The exit code
    if it was carried on."""
    runs = setup.stores(repo).runs()
    if not runs:
        return None
    try:
        latest = asyncio.run(runner.load(setup.stores(repo), runs[0]))
    except Exception:
        return None
    if latest.request is not None:
        count = plural(len(latest.request["questions"]), "trade-off")
        question = f"Run {latest.run_id} is waiting for your decisions on {count}. Answer them now?"
    elif latest.stopped:
        question = f"Run {latest.run_id} stopped part way. Carry it on?"
    else:
        return None
    if not Confirm.ask(question, console=setup.console, default=True):
        return None
    return resume_run(
        setup, repo, latest.run_id, decisions={}, evidence={}, max_rounds=None, can_ask=True
    )


def _ask_plans(setup: Setup, repo: Path) -> list[Path]:
    while True:
        answer = Prompt.ask(
            "Or a plan you have, to critique: its files, separated by spaces. Blank for none",
            console=setup.console,
            default="",
            show_default=False,
        )
        try:
            paths = [(repo / Path(part).expanduser()) for part in shlex.split(answer)]
        except ValueError as error:
            setup.console.print(f"[red]{escape(str(error))}[/]")
            continue
        missing = [str(path) for path in paths if not path.is_file()]
        if not missing:
            return paths
        setup.console.print(f"[red]No such file: {escape(', '.join(missing))}[/]")
