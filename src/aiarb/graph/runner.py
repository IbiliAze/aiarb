"""Starting, resuming and reopening runs.

Every run is checkpointed as it goes, so it can pause for the user's decisions
and carry on later, or be killed mid-round and carry on from its last finished
step. The run ID is the LangGraph thread its checkpoints belong to, and names
the folder the run's files are written to when it finishes.

Once a run has paused or finished, the user can reopen items with new evidence:
those items are debated again, with a full set of rounds, and the run finishes
as before.

Each time a run goes, it is watched: every step and outside call is written to
its log, every call is counted against its budget, and the repo is checked for
changes outside `.aiarb/` when it stops.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Protocol, cast

from langgraph.types import Command, Durability

import aiarb
from aiarb.domain.ledger import Actor, Status, TransitionError
from aiarb.graph.builder import build_graph, run_config
from aiarb.graph.nodes.clarify import ClarifyRequest
from aiarb.graph.nodes.decide import DecisionRequest, decided
from aiarb.graph.routing import round_limit
from aiarb.graph.state import RunState, total_cost
from aiarb.observe.calls import Meter, watch
from aiarb.observe.log import LOG, RunLog, logged_spend
from aiarb.observe.repo import RepoSnapshot
from aiarb.ports.calls import Cost
from aiarb.ports.run_store import RunNotFound

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Mapping, Sequence

    from langchain_core.runnables import RunnableConfig
    from langgraph.graph.state import CompiledStateGraph  # pyright: ignore[reportMissingTypeStubs]
    from langgraph.types import StateSnapshot

    from aiarb.domain.ledger import Objection
    from aiarb.graph.context import RunContext
    from aiarb.graph.state import StateUpdate
    from aiarb.ports.run_store import RunStore

DURABILITY: Durability = "sync"
"""Save each step's checkpoint before the next step starts. Steps take minutes,
so waiting for the write costs nothing, and a kill loses only the step it hit."""

REOPENED = "Reopened by the user with new evidence: {evidence}"
"""The note on an item the user reopens, which the planner and critic read."""


class NotWaiting(ValueError):
    """Decisions were given for a run that is not waiting for any."""


class CannotReopen(ValueError):
    """Items cannot be reopened: they are not in the run, or the run has not
    paused or finished."""


class Progress(Protocol):
    """Told of each step as a run goes, to show the user where it is."""

    def opened(self, run_id: str, state: RunState) -> None:
        """The run is about to start, or carry on from `state`."""

    def started(self, step: str, state: RunState) -> None:
        """A step began from `state`."""

    def finished(self, step: str, state: RunState) -> None:
        """A step finished and was saved. `state` includes what it changed."""

    def repo_changed(self, paths: Sequence[str]) -> None:
        """Files in the repo outside `.aiarb/` changed while the run went."""


class _Unwatched:
    def opened(self, run_id: str, state: RunState) -> None:
        pass

    def started(self, step: str, state: RunState) -> None:
        pass

    def finished(self, step: str, state: RunState) -> None:
        pass

    def repo_changed(self, paths: Sequence[str]) -> None:
        pass


@dataclass(frozen=True, kw_only=True)
class RunResult:
    run_id: str
    state: RunState
    request: DecisionRequest | ClarifyRequest | None = None
    """The questions the run is waiting on, when it paused for decisions or for
    answers to the planner's questions."""
    next: tuple[str, ...] = ()
    """The steps the run takes when it carries on. None once it has finished."""
    spent: Cost = field(default_factory=Cost)
    """What the run has spent, counting the calls in steps that failed, which the
    state's costs leave out."""

    @property
    def paused(self) -> bool:
        return self.request is not None

    @property
    def finished(self) -> bool:
        # A run that asked its questions again can have no next step listed.
        return not self.next and not self.paused

    @property
    def stopped(self) -> bool:
        """Stopped part way by a failure or a kill. Resuming carries on from the
        last finished step."""
        return not self.finished and not self.paused


async def start(
    store: RunStore, context: RunContext, state: RunState, *, progress: Progress | None = None
) -> RunResult:
    """Start a new run from `state`, and run it until it finishes or pauses."""
    name = state["feature"] or ("plan" if state["given_plans"] else None)
    # The run's ID is made from its name, and appears everywhere.
    run_id = store.create(context.redactor.text(name) if name else None)
    async with _open(store, run_id, context) as run:
        details = {"feature": state["feature"], "plans": len(state["given_plans"])}
        return await run.go(state, state, progress, how="start", **details)


async def resume(
    store: RunStore,
    run_id: str,
    context: RunContext,
    answers: Mapping[str, str] | None = None,
    *,
    progress: Progress | None = None,
) -> RunResult:
    """Carry on a run: with the user's decisions if it paused for them, or from
    its last finished step if it was stopped.

    A run that is still waiting is left waiting until `answers` are given, and a
    finished run is returned as it is.
    """
    async with _open(store, run_id, context) as run:
        before = await run.result()
        if answers is not None and not before.paused:
            raise NotWaiting(f"run {run_id} is not waiting for decisions")
        if answers is not None and not answers:
            # LangGraph takes an empty answer for no answer, and would just ask again.
            raise ValueError("give a decision for each question")
        if before.finished or (before.paused and answers is None):
            return before
        command: Any = None if answers is None else Command(resume=dict(answers))
        return await run.go(
            command, before.state, progress, how="resume", decided=sorted(answers or ())
        )


async def reopen(
    store: RunStore,
    run_id: str,
    context: RunContext,
    evidence: Mapping[str, str],
    *,
    answers: Mapping[str, str] | None = None,
    max_rounds: int | None = None,
    progress: Progress | None = None,
) -> RunResult:
    """Reopen items of a paused or finished run with the user's new evidence, by
    objection id, and debate them again with a full set of rounds.

    For a paused run, `answers` decides trade-offs that are not reopened. Any it
    leaves undecided are asked again once the new debate ends. `max_rounds`
    replaces the run's round limit.
    """
    if not evidence:
        raise ValueError("give new evidence for at least one item to reopen")
    if max_rounds is not None and max_rounds < 1:
        raise ValueError(f"a run needs at least one round, got {max_rounds}")
    async with _open(store, run_id, context) as run:
        before = await run.result()
        if before.stopped:
            raise CannotReopen(f"run {run_id} stopped part way: resume it before reopening items")
        if answers and not before.paused:
            raise NotWaiting(f"run {run_id} is not waiting for decisions")
        state = before.state
        round = state["round"]
        ledger = {
            **{id: _reopened(state, id, text) for id, text in evidence.items()},
            **{id: _decided(state, id, text, evidence) for id, text in (answers or {}).items()},
        }
        update: StateUpdate = {"ledger": ledger, "debate_start": round, "status": None}
        if max_rounds is not None:
            update["max_rounds"] = max_rounds
        # As if triage had just sent the reopened items to debate.
        await run.graph.aupdate_state(_thread(run_id), update, as_node="triage")
        reopened = await run.result()
        return await run.go(
            None,
            reopened.state,
            progress,
            how="reopen",
            reopened=sorted(evidence),
            decided=sorted(answers or ()),
        )


async def load(store: RunStore, run_id: str) -> RunResult:
    """Where a run is, without carrying it on."""
    async with store.checkpointer(run_id) as saver:
        graph = build_graph(checkpointer=saver)
        snapshot = await graph.aget_state(_thread(run_id))
        return _result(run_id, snapshot, logged_spend(store.folder(run_id) / LOG))


def _reopened(state: RunState, id: str, evidence: str) -> Objection:
    item = _item(state, id)
    if not evidence.strip():
        raise CannotReopen(f"{id}: give the new evidence that reopens it")
    try:
        return item.transition(
            Status.OPEN,
            actor=Actor.HUMAN,
            round=state["round"],
            note=REOPENED.format(evidence=evidence.strip()),
        )
    except TransitionError as error:
        raise CannotReopen(f"{id} is {item.status} and cannot be reopened") from error


def _decided(state: RunState, id: str, answer: str, evidence: Mapping[str, str]) -> Objection:
    item = _item(state, id)
    if id in evidence:
        raise CannotReopen(f"{id}: either reopen it or decide it, not both")
    if item.status is not Status.AWAITING_DECISION:
        raise NotWaiting(f"{id} is not waiting for a decision")
    if not answer.strip():
        raise ValueError(f"{id}: the decision is blank")
    return decided(item, answer, state["round"])


def _item(state: RunState, id: str) -> Objection:
    item = state["ledger"].get(id)
    if item is None:
        known = ", ".join(state["ledger"]) or "none"
        raise CannotReopen(f"the run has no objection {id} (it has: {known})")
    return item


class _Run:
    """One run's graph, open on its checkpoints, with its log."""

    def __init__(
        self,
        run_id: str,
        graph: CompiledStateGraph[RunState, RunContext, RunState, RunState],
        context: RunContext,
        log: RunLog,
    ) -> None:
        self.run_id = run_id
        self.graph = graph
        self.context = context
        self.log = log

    async def result(self) -> RunResult:
        snapshot = await self.graph.aget_state(_thread(self.run_id))
        return _result(self.run_id, snapshot, self.log.spent())

    async def go(
        self, input: Any, state: RunState, progress: Progress | None, *, how: str, **details: Any
    ) -> RunResult:
        """Run the graph until it finishes, pauses or fails, telling `progress` and
        the log of each step."""
        progress = progress or _Unwatched()
        progress.opened(self.run_id, state)
        charter = self.context.charter
        meter = Meter(charter.budget.per_run, _spent(state, self.log.spent()))
        context = watch(self.context, self.log, meter)
        limit = round_limit(state, charter)
        self.log.write(
            "opened",
            how=how,
            **details,
            aiarb_version=aiarb.__version__,
            round=state["round"],
            round_limit=limit,
            budget_usd=meter.budget,
            spent_usd=round(meter.spent.usd, 6),
            models=charter.models.model_dump(),
        )
        steps = _Steps(progress, self.log)
        before = RepoSnapshot.take(context.repo)
        stream = self.graph.astream(
            input,
            run_config(self.run_id, limit),
            context=context,
            durability=DURABILITY,
            stream_mode=["tasks", "values"],
        )
        try:
            # With several stream modes, each chunk comes as (mode, data).
            # A step's own result comes before the state it leads to, and a step
            # that changes nothing leads to no new state, so each step is
            # reported finished at whichever comes next: the new state, another
            # step, or the end.
            async for mode, chunk in cast("AsyncIterator[tuple[str, dict[str, Any]]]", stream):
                if mode == "values":
                    state = cast(
                        "RunState", {k: v for k, v in chunk.items() if k != "__interrupt__"}
                    )
                    steps.report(state)
                elif "input" in chunk:
                    steps.report(state)
                    steps.started(chunk["name"], state)
                elif chunk["error"] is None and not chunk["interrupts"]:
                    steps.finishing.append(chunk["name"])
            steps.report(state)
        except BaseException as error:
            interrupted = isinstance(error, KeyboardInterrupt | asyncio.CancelledError)
            self.log.write(
                "stopped",
                step=steps.under_way,
                interrupted=interrupted,
                error=None if interrupted else str(error) or type(error).__name__,
                error_type=type(error).__name__,
                spent_usd=round(meter.spent.usd, 6),
            )
            raise
        finally:
            changed = before.changes(RepoSnapshot.take(context.repo))
            if changed:
                self.log.write("repo_changed", paths=changed[:_LOGGED_PATHS], count=len(changed))
                progress.repo_changed(changed)
        result = await self.result()
        self._log_end(result)
        return result

    def _log_end(self, result: RunResult) -> None:
        state = result.state
        spent = round(result.spent.usd, 6)
        if result.request is not None:
            questions = [question["id"] for question in result.request["questions"]]
            self.log.write(
                "paused",
                kind=result.request.get("kind", "decide"),
                questions=questions,
                round=state["round"],
                spent_usd=spent,
            )
        elif result.finished:
            status = state["status"].value if state["status"] is not None else None
            self.log.write("finished", status=status, rounds=state["round"], spent_usd=spent)
        else:
            self.log.write("stopped", interrupted=False, error=None, spent_usd=spent)


_LOGGED_PATHS = 50
"""The most changed paths the log lists."""


class _Steps:
    """Tells the progress and the log of each step as it starts and finishes."""

    def __init__(
        self, progress: Progress, log: RunLog, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.progress = progress
        self.log = log
        self.clock = clock
        self.finishing: list[str] = []
        """Steps that have finished, to report with the state they lead to."""
        self._began: dict[str, tuple[float, Cost]] = {}

    def started(self, step: str, state: RunState) -> None:
        self._began[step] = (self.clock(), total_cost(state))
        self.progress.started(step, state)
        self.log.write("step_started", step=step, **_where(state))

    @property
    def under_way(self) -> str | None:
        """The step that started and has not finished, if any."""
        return next(iter(self._began), None)

    def report(self, state: RunState) -> None:
        for step in self.finishing:
            self.progress.finished(step, state)
            began, cost = self._began.pop(step, (self.clock(), total_cost(state)))
            self.log.write(
                "step_finished",
                step=step,
                seconds=round(self.clock() - began, 3),
                cost_usd=round(max(total_cost(state).usd - cost.usd, 0.0), 6),
                **_where(state),
            )
        self.finishing.clear()


def _where(state: RunState) -> dict[str, Any]:
    return {
        "round": state["round"],
        "open": sum(item.is_open for item in state["ledger"].values()),
        "spent_usd": round(total_cost(state).usd, 6),
    }


def _spent(state: RunState, logged: Cost) -> Cost:
    """What the run has spent: what its log counts, or what its state does for a
    run whose log is missing or older than the run."""
    recorded = total_cost(state)
    return logged if logged.usd >= recorded.usd else recorded


def _thread(run_id: str) -> RunnableConfig:
    """Enough config to read and update a run's checkpoints."""
    return {"configurable": {"thread_id": run_id}}


@asynccontextmanager
async def _open(store: RunStore, run_id: str, context: RunContext) -> AsyncIterator[_Run]:
    if not store.exists(run_id):
        raise RunNotFound(f"no run {run_id!r}")
    folder = store.folder(run_id)
    context = replace(context, run_dir=folder)
    log = RunLog(folder / LOG, context.redactor)
    async with store.checkpointer(run_id) as saver:
        yield _Run(run_id, build_graph(checkpointer=saver), context, log)


def _result(run_id: str, snapshot: StateSnapshot, logged: Cost) -> RunResult:
    if not snapshot.values:
        raise RunNotFound(f"run {run_id} stopped before saving anything: start it again")
    state = cast("RunState", snapshot.values)
    request = snapshot.interrupts[0].value if snapshot.interrupts else None
    return RunResult(
        run_id=run_id,
        state=state,
        request=request,
        next=tuple(snapshot.next),
        spent=_spent(state, logged),
    )
