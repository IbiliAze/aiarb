"""Running a run from the command line: progress while it goes, the user's
answers and decisions when it pauses, and what it came to, as a summary on standard error
and JSON on standard output. What the agents wrote is redacted before it is
shown.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar, cast

import typer
from rich.markup import escape
from rich.prompt import Prompt

from aiarb import output
from aiarb.cli.progress import LiveProgress, duration
from aiarb.cli.setup import ExitCode, Setup
from aiarb.config.charter import CHARTER_PATH, Charter
from aiarb.domain.ledger import Status
from aiarb.graph import runner
from aiarb.observe.calls import OverBudget
from aiarb.output.report import RUN_STATUS_LABELS, Report, plural
from aiarb.ports.calls import CallError
from aiarb.ports.run_store import RunStore

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from rich.console import Console

    from aiarb.graph.context import RunContext
    from aiarb.graph.nodes.clarify import ClarifyRequest
    from aiarb.graph.nodes.decide import DecisionRequest, Question
    from aiarb.graph.runner import Progress, RunResult
    from aiarb.graph.state import PlannerQuestion
    from aiarb.observe.redact import Redactor

RequestT = TypeVar("RequestT", "DecisionRequest", "ClarifyRequest")

WAITING = "waiting_for_decisions"
WAITING_FOR_ANSWERS = "waiting_for_answers"
STOPPED = "stopped"
OVER_BUDGET = "over_budget"

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
        except OverBudget as error:
            progress.idle()
            if progress.run_id is None:
                raise
            return self._report_stopped(progress.run_id, str(error), began, over_budget=error)
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
            questions = clarifying(result.request)
            answers = (
                ask_decisions(self.setup, deciding(result.request))
                if questions is None
                else ask_clarifications(self.setup, questions)
            )
            if answers is None:
                break
            result = await runner.resume(
                self.store, result.run_id, context, answers, progress=progress
            )
            progress.idle()
        return result

    def _report_stopped(
        self,
        run_id: str,
        error: str,
        began: float,
        *,
        interrupted: bool = False,
        over_budget: OverBudget | None = None,
    ) -> ExitCode:
        try:
            result = asyncio.run(runner.load(self.store, run_id))
        except Exception:
            result = None
        return self.report(
            result,
            began=began,
            run_id=run_id,
            error=error,
            interrupted=interrupted,
            over_budget=over_budget,
        )

    def report(
        self,
        result: RunResult | None,
        *,
        began: float | None = None,
        run_id: str | None = None,
        error: str | None = None,
        interrupted: bool = False,
        over_budget: OverBudget | None = None,
    ) -> ExitCode:
        """Tell the user what the run came to, and give the exit code for it.

        `result` is None when the run stopped and could not be read back.
        """
        console = self.setup.console
        redactor = self.setup.redactor
        run_id = result.run_id if result is not None else run_id
        assert run_id is not None, "a report needs a run"
        folder = self.store.folder(run_id)
        error = redactor.text(error) if error is not None else None
        if result is not None and result.finished:
            report = Report.of(result.state, self.charter, run_id)
            code = ExitCode(report.outcome.exit_code)
            data = _finished(report, folder)
            _print_finished(console, report, folder, redactor)
        elif result is not None and result.request is not None:
            code = ExitCode.WAITING
            questions = clarifying(result.request)
            if questions is None:
                request = _redacted(redactor, deciding(result.request))
                data = _waiting(request)
                _print_waiting(console, run_id, request)
            else:
                questions = _redacted(redactor, questions)
                data = _waiting(questions, status=WAITING_FOR_ANSWERS)
                _print_waiting_for_answers(console, run_id, questions)
        elif over_budget is not None:
            code = ExitCode.OVER_BUDGET
            data = {"status": OVER_BUDGET, "error": error, "budget_usd": over_budget.budget}
            _print_over_budget(console, run_id, over_budget)
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
            spent = result.spent.usd
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


def clarifying(request: DecisionRequest | ClarifyRequest | None) -> ClarifyRequest | None:
    """The planner's questions, if that is what the run paused for."""
    if request is None or request.get("kind") != "clarify":
        return None
    return cast("ClarifyRequest", request)


def deciding(request: DecisionRequest | ClarifyRequest) -> DecisionRequest:
    """The trade-offs the run paused for. Runs paused before planners could ask
    questions have no `kind`."""
    assert request.get("kind", "decide") == "decide", "the run is waiting for answers"
    return cast("DecisionRequest", request)


def _waiting(request: DecisionRequest | ClarifyRequest, status: str = WAITING) -> dict[str, Any]:
    data: dict[str, Any] = {"status": status, "questions": request["questions"]}
    if "problem" in request:
        data["problem"] = request["problem"]
    return data


_STYLES = {"settled": "green", "settled_with_open_items": "yellow", "not_ready": "red"}


def _redacted(redactor: Redactor, request: RequestT) -> RequestT:
    return cast("RequestT", redactor.data(request))


def _print_finished(console: Console, report: Report, folder: Path, redactor: Redactor) -> None:
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
        title = redactor.text(report.title(item))
        console.print(f"  {item.id}  [bold]{item.severity}[/]  {escape(title)}")
    to_apply = [
        f"{whose} {_on(noun, ids)}"
        for whose, noun, status in (
            ("the judge's", "ruling", Status.RULED),
            ("your", "decision", Status.DECIDED),
        )
        if (ids := [item.id for item in report.ledger.values() if item.status is status])
    ]
    if to_apply:
        console.print(
            f"Apply {' and '.join(to_apply)} as you build: they are listed at the top of plan.md."
        )
    if result.downgraded:
        lower = ", ".join(item.id for item in result.downgraded)
        console.print(
            f"[yellow]Rated lower after the debate, though nobody settled them: {lower}.[/]"
        )
    console.print(f"Files in {escape(str(_shown(folder)))}/", soft_wrap=True)
    for name in output.FILES:
        console.print(f"  {name:<13} [dim]{FILE_NOTES.get(name, '')}[/]")


def _on(noun: str, ids: list[str]) -> str:
    """ "rulings on OBJ-1, OBJ-3", or "ruling on OBJ-1"."""
    return f"{noun}{'s' if len(ids) > 1 else ''} on {', '.join(ids)}"


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


def _print_waiting_for_answers(console: Console, run_id: str, request: ClarifyRequest) -> None:
    questions = request["questions"]
    console.print()
    console.print(
        f"[bold]Waiting for your answers to {plural(len(questions), 'question')} "
        "from the planner.[/]"
    )
    if "problem" in request:
        console.print(f"[red]Your last answers were refused: {escape(request['problem'])}.[/]")
    for question in questions:
        _print_planner_question(console, question)
    answer = " ".join(f'--decide {q["id"]}="..."' for q in questions)
    console.print("Answer here with:")
    console.print(f"  aiarb resume {run_id}", soft_wrap=True)
    console.print("Or give your answers, leaving out any you want the planner to decide:")
    console.print(f"  aiarb resume {run_id} --no-input {answer}", soft_wrap=True)


def _print_planner_question(console: Console, question: PlannerQuestion) -> None:
    console.print()
    console.print(f"[bold]{question['id']}[/]  {escape(question['question'])}")
    console.print(f"[dim]{escape(question['why'])}[/]")
    for number, option in enumerate(question["options"], start=1):
        recommended = " [green](recommended)[/]" if option["recommended"] else ""
        console.print(
            f"  {number}. [bold]{escape(option['label'])}[/]{recommended}  "
            f"[dim]{escape(option['description'])}[/]"
        )


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


def _print_over_budget(console: Console, run_id: str, over: OverBudget) -> None:
    console.print()
    console.print(
        f"[bold yellow]Stopped at the budget:[/] the run has spent ${over.spent.usd:.2f} "
        f"of its ${over.budget:.2f}."
    )
    console.print(
        f"Its progress is saved. Raise budget.per_run in {CHARTER_PATH}, then carry on "
        f"with: aiarb resume {run_id}",
        soft_wrap=True,
    )


def ask_clarifications(setup: Setup, request: ClarifyRequest) -> dict[str, str]:
    """Ask the user the planner's questions. An answer is an option's number, the
    user's own words, or blank to leave the question to the planner."""
    console = setup.console
    request = _redacted(setup.redactor, request)
    questions = request["questions"]
    console.print()
    console.print(
        f"[bold]The planner has {plural(len(questions), 'question')} before it drafts the "
        "plan.[/] Pick a number, type your own answer, or leave it blank to let the "
        "planner decide."
    )
    if "problem" in request:
        console.print(f"[red]Your last answers were refused: {escape(request['problem'])}.[/]")
    answers: dict[str, str] = {}
    for question in questions:
        _print_planner_question(console, question)
        answer = Prompt.ask("Your answer", console=console, default="", show_default=False)
        answers[question["id"]] = _chosen(question, answer.strip())
    return answers


def _chosen(question: PlannerQuestion, answer: str) -> str:
    """The option an answer picks by number, or the answer as the user wrote it."""
    options = question["options"]
    if answer.isdigit() and 1 <= int(answer) <= len(options):
        return options[int(answer) - 1]["label"]
    return answer


def ask_decisions(setup: Setup, request: DecisionRequest) -> dict[str, str] | None:
    """Ask the user to decide each question. None if they leave one blank, to
    decide later."""
    console = setup.console
    request = _redacted(setup.redactor, request)
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
    setup: Setup,
    request: DecisionRequest | ClarifyRequest,
    given: Mapping[str, str],
    *,
    can_ask: bool,
) -> dict[str, str] | None:
    """The answers to the questions: those given, and the rest asked for if
    someone can answer. None if some trade-offs are left undecided. The planner's
    questions nobody can answer are left to the planner."""
    questions = clarifying(request)
    if questions is not None:
        unasked = [question for question in questions["questions"] if question["id"] not in given]
        if not unasked:
            return dict(given)
        if not can_ask:
            return {**given, **{question["id"]: "" for question in unasked}}
        answers = ask_clarifications(setup, {**questions, "questions": unasked})
        return {**given, **answers}
    decisions = deciding(request)
    asked = [question for question in decisions["questions"] if question["id"] not in given]
    if not asked:
        return dict(given)
    if not can_ask:
        return None
    rest = ask_decisions(setup, {**decisions, "questions": asked})
    return None if rest is None else {**given, **rest}
