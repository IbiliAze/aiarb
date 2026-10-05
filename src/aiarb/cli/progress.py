"""Live progress on standard error: the step under way, the round, what is open,
and what the run has spent.

At a terminal, the step under way shows on a line of its own with a timer, and
each finished step is printed above it. Elsewhere, such as in CI, a line is
printed as each step starts and finishes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from aiarb.graph.state import total_cost

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from rich.console import Console

    from aiarb.graph.state import RunState

DOING: Mapping[str, str] = {
    "scan": "Scanning the repo",
    "draft_plan": "Planner drafting the plan",
    "critique": "Critic raising objections",
    "triage": "Scoring the objections",
    "respond": "Planner answering the objections",
    "rule": "Critic ruling on the answers",
    "verify": "Verifier checking disputed facts",
    "judge": "Settling what is left at the round limit",
    "decide": "Waiting for your decisions",
    "rescore": "Scoring what is left open",
    "explain": "Explaining the ledger",
    "finalise": "Writing the run's files",
}

DONE: Mapping[str, str] = {
    "scan": "Scanned the repo",
    "draft_plan": "Planner drafted the plan",
    "critique": "Critic raised its objections",
    "triage": "Scored the objections",
    "respond": "Planner answered the objections",
    "rule": "Critic ruled on the answers",
    "verify": "Verifier checked disputed facts",
    "judge": "Settled what was left at the round limit",
    "decide": "Took your decisions",
    "rescore": "Scored what is left open",
    "explain": "Explained the ledger",
    "finalise": "Wrote the run's files",
}

_IN_ROUND = frozenset({"respond", "rule", "verify", "judge"})


class LiveProgress:
    """Shows a run's steps as they go. Implements the runner's `Progress`."""

    def __init__(self, console: Console, clock: Callable[[], float]) -> None:
        self.console = console
        self.clock = clock
        self.run_id: str | None = None
        self.state: RunState | None = None
        """The run as last seen."""
        self._live = console.is_terminal
        self._bar: Progress | None = None
        self._step: tuple[str, float, float] | None = None
        """The step under way: its name, when it started, and the run's spend then."""

    def opened(self, run_id: str, state: RunState) -> None:
        if self.run_id != run_id:
            self.console.print(f"[bold]Run {run_id}[/]")
        self.run_id = run_id
        self.state = state

    def started(self, step: str, state: RunState) -> None:
        self.state = state
        self._step = (step, self.clock(), total_cost(state).usd)
        doing = f"{_label(DOING, step, state)}{_where(step, state, starting=True)}"
        if not self._live:
            self.console.print(f"  … {doing}")
            return
        if self._bar is None:
            self._bar = Progress(
                SpinnerColumn(),
                TextColumn("{task.description}"),
                TimeElapsedColumn(),
                console=self.console,
                transient=True,
            )
            self._bar.start()
        for task in list(self._bar.task_ids):
            self._bar.remove_task(task)
        self._bar.add_task(doing, total=None)

    def finished(self, step: str, state: RunState) -> None:
        self.state = state
        took, spent = "", ""
        if self._step is not None and self._step[0] == step:
            took = f"  {duration(self.clock() - self._step[1])}"
            cost = total_cost(state).usd - self._step[2]
            spent = f"  ${cost:.2f}" if cost >= 0.005 else ""
        self._step = None
        self.idle()
        done = _label(DONE, step, state)
        where = _where(step, state, starting=False)
        self.console.print(f"  [green]✓[/] {done}{where}[dim]{took}{spent}[/]")

    def idle(self) -> None:
        """Clear the live line, as when the run has stopped or paused."""
        if self._bar is not None:
            self._bar.stop()
            self._bar = None


def _label(labels: Mapping[str, str], step: str, state: RunState) -> str:
    if step == "draft_plan" and state["given_plans"]:
        return "Took in your plan" if labels is DONE else "Taking in your plan"
    return labels.get(step, step)


def _where(step: str, state: RunState, *, starting: bool) -> str:
    """Where the run is: the round, what is open, and, as a step starts, what the
    run has spent. A finished step shows what it spent itself."""
    parts = []
    if step in _IN_ROUND:
        # The planner's answer starts the next round.
        round = state["round"] + 1 if starting and step == "respond" else state["round"]
        parts.append(f"round {round}")
    if state["ledger"]:
        open_items = sum(item.is_open for item in state["ledger"].values())
        parts.append(f"{open_items} open")
    spent = total_cost(state).usd
    if starting and spent:
        parts.append(f"${spent:.2f} so far")
    return f" [dim]· {' · '.join(parts)}[/]" if parts else ""


def duration(seconds: float) -> str:
    """4s, 2m 05s, 1h 12m."""
    seconds = round(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"
