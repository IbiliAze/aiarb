"""`aiarb init`: a starter charter, and keeping runs out of git."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from aiarb.cli.common import RepoOption, fail, setup_of
from aiarb.cli.setup import ExitCode
from aiarb.config.charter import CHARTER_PATH, Charter
from aiarb.config.settings import Settings

if TYPE_CHECKING:
    from aiarb.cli.setup import Setup

IGNORE_PATH = CHARTER_PATH.parent / ".gitignore"

IGNORED = """\
# aiarb's runs and scan cache stay on this machine. The charter is committed.
runs/
cache/
"""

_ROLES = {
    "planner": "your strongest model",
    "critic": "must differ from the planner",
    "judge": "breaks deadlocks; takes no part in the debate",
    "verifier": "settles disputed facts; a smaller model is fine",
    "explainer": "writes the ledger in plain English",
    "scorer": "scores when Jev is not used, and confirms blockers",
}


def starter_charter(*, jev: bool = False) -> str:
    """A charter with every default written out and explained. With `jev`, it
    scores with Jev."""
    defaults = Charter()
    models = defaults.models.model_dump()
    lines = [
        "# How the aiarb review board behaves in this repo. Commit this file.",
        "# Every setting is optional: delete one to use its default.",
        "",
        "models: # any model ID Claude Code accepts",
        *(_commented(f"  {role}: {models[role]}", note) for role, note in _ROLES.items()),
        "",
        "debate:",
        f"  max_rounds: {defaults.debate.max_rounds}",
        "",
        "scoring:",
        _commented(
            "  provider: jev" if jev else "  provider: llm",
            "or llm: a language model scores" if jev else "or jev, with TYPESAFE_API_KEY set",
        ),
        _commented(
            f"  confidence_threshold: {defaults.scoring.confidence_threshold}",
            "below this, a language model scores again",
        ),
        "",
        "# What matters in this codebase, from 1 (a little) to 5 (most). For example:",
        "priorities:",
        "  # data_integrity: 5",
        "  # availability_at_peak: 5",
        "  # delivery_time: 3",
        "  # running_cost: 2",
        "",
        "# Rules every plan must keep to. For example:",
        "constraints:",
        "  # - no new managed services",
        "  # - zero-downtime deploys only",
        "",
        "budget: # in US dollars",
        _commented(
            f"  per_call: {defaults.budget.per_call:g}", "the most one agent call may spend"
        ),
        _commented(f"  per_run: {defaults.budget.per_run:g}", "the most one run may spend"),
        "",
    ]
    return "\n".join(lines)


def _commented(setting: str, note: str) -> str:
    return f"{setting:<34}# {note}"


class CharterExists(FileExistsError):
    """The repo has a charter already."""


def write_charter(repo: Path, *, jev: bool = False, force: bool = False) -> Path:
    """Write the starter charter to the repo, unless it has one and not `force`."""
    path = repo / CHARTER_PATH
    if path.exists() and not force:
        raise CharterExists(f"{CHARTER_PATH} exists already: pass --force to replace it")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(starter_charter(jev=jev), encoding="utf-8")
    return path


def ignore_runs(repo: Path) -> None:
    """Keep runs and the scan cache out of git, with a `.gitignore` in `.aiarb/`.

    One that is there already is left as it is.
    """
    path = repo / IGNORE_PATH
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(IGNORED, encoding="utf-8")


def init(
    ctx: typer.Context,
    jev: Annotated[
        bool | None,
        typer.Option(
            "--jev/--llm",
            help="Score objections with Jev, or with a language model. Default: Jev when "
            "TYPESAFE_API_KEY is set.",
            show_default=False,
        ),
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Replace the charter if there is one.")
    ] = False,
    repo: RepoOption = Path("."),
) -> None:
    """Write a starter charter to .aiarb/charter.yaml."""
    setup = setup_of(ctx)
    raise typer.Exit(init_repo(setup, repo, jev=jev, force=force))


def init_repo(setup: Setup, repo: Path, *, jev: bool | None, force: bool) -> ExitCode:
    if jev is None:
        jev = Settings().typesafe_api_key is not None
    try:
        path = write_charter(repo, jev=jev, force=force)
    except CharterExists as error:
        fail(setup, str(error))
    ignore_runs(repo)
    scoring = "Jev" if jev else "a language model"
    setup.console.print(f"Wrote {path.relative_to(repo)}, scoring with {scoring}.")
    setup.console.print(
        "Set the priorities and constraints that matter in this repo, commit it, then "
        "start a run with `aiarb plan`."
    )
    return ExitCode.OK
