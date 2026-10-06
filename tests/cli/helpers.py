"""Running the CLI in tests, on scripted agents and scorers."""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from rich.console import Console
from typer.testing import CliRunner, Result

from aiarb.adapters.checkpointer import RUNS_PATH, SqliteRunStore
from aiarb.cli import setup as cli_setup
from aiarb.cli.app import app
from aiarb.cli.setup import Ports, Setup
from aiarb.config.charter import Charter
from aiarb.observe.redact import Redactor
from tests.fakes import Scenario, ScriptedCodeAgent, ScriptedScorer

SCENARIOS = Path(__file__).parent.parent / "fixtures" / "scenarios"
FEATURE = "Add a virtual waiting room."
RUN_ID = "2026-10-05-add-a-virtual-waiting-room"


def scenario(name: str, *, asking: bool = False) -> Scenario:
    """A scripted scenario. With `asking`, for a run someone can answer: the
    planner is offered questions, and drafts straight away instead."""
    if not asking:
        return Scenario.from_yaml(SCENARIOS / f"{name}.yaml")
    data = yaml.safe_load((SCENARIOS / f"{name}.yaml").read_text(encoding="utf-8"))
    draft = next(step for step in data["agent"] if step["schema"] == "PlanDraft")
    draft["schema"] = "Opening"
    draft["output"] = {"answer": {"kind": "plan", **draft["output"]}}
    return Scenario.from_data(data)


def nothing() -> Scenario:
    """Ports that fail the test if anything calls them."""
    return Scenario(ScriptedCodeAgent([]), ScriptedScorer([]))


@dataclass
class Outcome:
    """What one command did."""

    result: Result
    built_ports: list[Charter] = field(default_factory=list)

    @property
    def code(self) -> int:
        return self.result.exit_code

    @property
    def stderr(self) -> str:
        return self.result.stderr

    @property
    def json(self) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(self.result.stdout)
        return data


def store(repo: Path) -> SqliteRunStore:
    return SqliteRunStore(repo / RUNS_PATH, today=lambda: date(2026, 10, 5))


def aiarb(
    repo: Path,
    *args: str,
    ports: Scenario | None = None,
    can_ask: bool = False,
    input: str | None = None,
    real_ports: bool = False,
    redactor: Redactor | None = None,
) -> Outcome:
    """Run `aiarb` on `repo`, with `ports` scripted, or the real ones if
    `real_ports`. The clock moves a minute each time it is read. Only secrets
    known by their shape are redacted, unless `redactor` knows more."""
    ports = ports or nothing()
    ticks = itertools.count(0, 60)
    built: list[Charter] = []

    def make_ports(charter: Charter, repo: Path) -> Ports:
        built.append(charter)
        return Ports(agent=ports.agent, scorer=ports.scorer, confirmer=ports.scorer)

    setup = Setup(
        ports=cli_setup.real_ports if real_ports else make_ports,
        stores=store,
        can_ask=lambda: can_ask,
        console=Console(stderr=True, width=200, color_system=None, highlight=False),
        clock=lambda: float(next(ticks)),
        redactor=redactor or Redactor(),
    )
    command = [*args]
    if args and args[0] in {"init", "plan", "resume"}:
        command += ["--repo", str(repo)]
    result = CliRunner().invoke(app, command, obj=setup, input=input)
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise result.exception
    return Outcome(result, built)
