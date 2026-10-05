"""Running a run from the command line: progress while it goes, the user's
decisions when it pauses, and what it came to, as a summary on standard error
and JSON on standard output.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer
from rich.markup import escape
from rich.prompt import Prompt

from aiarb import output
from aiarb.cli.progress import LiveProgress, duration
from aiarb.cli.setup import ExitCode, Setup
from aiarb.config.charter import Charter
from aiarb.graph import runner
from aiarb.graph.state import total_cost
from aiarb.output.report import RUN_STATUS_LABELS, Report, plural
from aiarb.ports.calls import CallError
from aiarb.ports.run_store import RunStore

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from rich.console import Console

    from aiarb.graph.context import RunContext
    from aiarb.graph.nodes.decide import DecisionRequest, Question
    from aiarb.graph.runner import Progress, RunResult

WAITING = "waiting_for_decisions"
STOPPED = "stopped"

FILE_NOTES = {
    "plan.md": "the plan, and what to settle before building",
    "ledger.md": "every objection, in plain English",
    "decisions.md": "the trade-offs you decided",
    "run.json": "the full record",
}


@dataclass(frozen=True)
class Session:
    """One command's work on a run: what it reaches, and whether it may ask."""

    setup: Setup
    store: RunStore
    charter: Charter
    can_ask: bool

    def run(
        self, context: RunContext, begin: Callable[[Progress], Awaitable[RunResult]]
    ) -> ExitCode:
        """Run `begin` to its end or a pause, asking for decisions when it pauses
        if someone can answer, then report what the run came to.

        Errors before the run opens, such as items that cannot be reopened, are
        raised. Once it opens, an error stops it, and is reported with it.
        """
        progress = LiveProgress(self.setup.console, self.setup.clock)
        began = self.setup.clock()
        try:
            result = asyncio.run(self._go(context, begin, progress))
        except KeyboardInterrupt:
            progress.idle()
            if progress.run_id is None:
                raise
            return self._report_stopped(progress.run_id, "interrupted", began, interrupted=True)
        except Exception as error:
            progress.idle()
            if progress.run_id is None:
                raise
            if not isinstance(error, CallError):
                self.setup.console.print_exception()
            return self._report_stopped(progress.run_id, _describe(error), began)
        return self.report(result, began=began)

    async def _go(
        self,
        context: RunContext,
        begin: Callable[[Progress], Awaitable[RunResult]],
        progress: LiveProgress,
    ) -> RunResult:
        result = await begin(progress)
        progress.idle()
        while result.request is not None and self.can_ask:
            answers = ask_decisions(self.setup, result.request)
            if answers is None:
                break
            result = await runner.resume(
                self.store, result.run_id, context, answers, progress=progress
            )
            progress.idle()
        return result

    def _report_stopped(
        self, run_id: str, error: str, began: float, *, interrupted: bool = False
    ) -> ExitCode:
        try:
            result = asyncio.run(runner.load(self.store, run_id))
        except Exception:
            result = None
        return self.report(result, began=began, run_id=run_id, error=error, interrupted=interrupted)

    def report(
        self,
        result: RunResult | None,
        *,
        began: float | None = None,
        run_id: str | None = None,
        error: str | None = None,
        interrupted: bool = False,
    ) -> ExitCode:
        """Tell the user what the run came to, and give the exit code for it.

        `result` is None when the run stopped and could not be read back.
        """
        console = self.setup.console
        run_id = result.run_id if result is not None else run_id
        assert run_id is not None, "a report needs a run"
        folder = self.store.folder(run_id)
        if result is not None and result.finished:
            report = Report.of(result.state, self.charter, run_id)
            code = ExitCode(report.outcome.exit_code)
            data = _finished(report, folder)
            _print_finished(console, report, folder)
        elif result is not None and result.request is not None:
            code = ExitCode.WAITING
            data = _waiting(result.request)
            _print_waiting(console, run_id, result.request)
        else:
            code = ExitCode.ERROR
            data = {"status": STOPPED, "error": error or "the run stopped part way"}
            _print_stopped(console, run_id, error, interrupted=interrupted)
        if interrupted:
            code = ExitCode.INTERRUPTED
        record: dict[str, Any] = {
            "run_id": run_id,
            "status": data.pop("status"),
            "exit_code": int(code),
            "folder": str(folder),
            **data,
        }
        if result is not None:
            state = result.state
            spent = total_cost(state).usd
            took = "" if began is None else f" · {duration(self.setup.clock() - began)}"
            console.print(f"[dim]{plural(state['round'], 'round')} · ${spent:.2f}{took}[/]")
            record |= {"rounds": state["round"], "cost_usd": round(spent, 6)}
        typer.echo(json.dumps(record, indent=2, ensure_ascii=False))
        return code


def _describe(error: Exception) -> str:
    return str(error) or type(error).__name__


def _finished(report: Report, folder: Path) -> dict[str, Any]:
    result = report.outcome
    return {
        "status": result.status.value,
        "files": {name: str(folder / name) for name in output.FILES},
        "open_items": [item.id for item in result.open_items],
        "blockers": [item.id for item in result.blockers],
        "downgraded": [item.id for item in result.downgraded],
    }


def _waiting(request: DecisionRequest) -> dict[str, Any]:
    data: dict[str, Any] = {"status": WAITING, "questions": request["questions"]}
    if "problem" in request:
        data["problem"] = request["problem"]
    return data


_STYLES = {"settled": "green", "settled_with_open_items": "yellow", "not_ready": "red"}


def _print_finished(console: Console, report: Report, folder: Path) -> None:
    result = report.outcome
    label = RUN_STATUS_LABELS[result.status]
    style = _STYLES[result.status.value]
    if result.blockers:
        headline = f"{label}: {plural(len(result.blockers), 'open blocker')}."
        advice = "Do not build from this plan yet: see Before you build in plan.md."
    elif result.open_items:
        headline = f"{label}: {plural(len(result.open_items), 'open item')}."
        advice = "The plan is usable; the open items are known risks."
    else:
        headline, advice = f"{label}.", "No objections are open."
    console.print()
    console.print(f"[bold {style}]{headline}[/] {advice}")
    for item in sorted(result.open_items, key=lambda item: -item.severity.rank):
        console.print(f"  {item.id}  [bold]{item.severity}[/]  {escape(report.title(item))}")
    if result.downgraded:
        ids = ", ".join(item.id for item in result.downgraded)
        console.print(
            f"[yellow]Rated lower after the debate, though nobody settled them: {ids}.[/]"
        )
    console.print(f"Files in {escape(str(_shown(folder)))}/", soft_wrap=True)
    for name in output.FILES:
        console.print(f"  {name:<13} [dim]{FILE_NOTES.get(name, '')}[/]")


def _shown(path: Path) -> Path:
    """The path from the current directory, when it is below it."""
    try:
        return path.relative_to(Path.cwd())
    except ValueError:
        return path


def _print_waiting(console: Console, run_id: str, request: DecisionRequest) -> None:
    questions = request["questions"]
    console.print()
    console.print(f"[bold]Waiting for your decisions on {plural(len(questions), 'trade-off')}.[/]")
    if "problem" in request:
        console.print(f"[red]Your last answers were refused: {escape(request['problem'])}.[/]")
    for question in questions:
        _print_question(console, question)
    decide = " ".join(f'--decide {q["id"]}="..."' for q in questions)
    console.print("Decide with:")
    console.print(f"  aiarb resume {run_id} {decide}", soft_wrap=True)
    console.print("Or reopen an item with new evidence instead:")
    console.print(f'  aiarb resume {run_id} --evidence ID="..."', soft_wrap=True)


def _print_question(console: Console, question: Question) -> None:
    console.print()
    console.print(f"[bold]{question['id']}[/]  {escape(question['claim'])}")
    for line in question["detail"].splitlines()[1:]:
        console.print(f"[dim]{escape(line)}[/]")


def _print_stopped(console: Console, run_id: str, error: str | None, *, interrupted: bool) -> None:
    console.print()
    if interrupted:
        console.print("[bold yellow]Stopped.[/]")
    else:
        console.print(f"[bold red]The run stopped part way:[/] {escape(error or 'unknown error')}")
    console.print(f"Its progress is saved. Carry on with: aiarb resume {run_id}", soft_wrap=True)


def ask_decisions(setup: Setup, request: DecisionRequest) -> dict[str, str] | None:
    """Ask the user to decide each question. None if they leave one blank, to
    decide later."""
    console = setup.console
    questions = request["questions"]
    console.print()
    console.print(
        f"[bold]The run needs your decision on {plural(len(questions), 'trade-off')}.[/] "
        "Leave one blank to decide later."
    )
    if "problem" in request:
        console.print(f"[red]Your last answers were refused: {escape(request['problem'])}.[/]")
    answers: dict[str, str] = {}
    for question in questions:
        _print_question(console, question)
        answer = Prompt.ask("Your decision", console=console, default="", show_default=False)
        if not answer.strip():
            return None
        answers[question["id"]] = answer.strip()
    return answers


def fill_in(
    setup: Setup, request: DecisionRequest, given: Mapping[str, str], *, can_ask: bool
) -> dict[str, str] | None:
    """The answers to the questions: those given, and the rest asked for if
    someone can answer. None if some are left unanswered."""
    asked = [question for question in request["questions"] if question["id"] not in given]
    if not asked:
        return dict(given)
    if not can_ask:
        return None
    rest = ask_decisions(setup, {**request, "questions": asked})
    return None if rest is None else {**given, **rest}
