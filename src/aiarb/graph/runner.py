"""Starting and resuming runs.

Every run is checkpointed as it goes, so it can pause for the user's decisions
and carry on later, or be killed mid-round and carry on from its last finished
step. The run ID is the LangGraph thread its checkpoints belong to.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from langgraph.types import Command, Durability

from aiarb.graph.builder import build_graph, run_config
from aiarb.graph.nodes.decide import DecisionRequest
from aiarb.graph.state import RunState
from aiarb.ports.run_store import RunNotFound

if TYPE_CHECKING:
    from collections.abc import Mapping

    from langgraph.types import StateSnapshot

    from aiarb.graph.context import RunContext
    from aiarb.ports.run_store import RunStore

DURABILITY: Durability = "sync"
"""Save each step's checkpoint before the next step starts. Steps take minutes,
so waiting for the write costs nothing, and a kill loses only the step it hit."""


class NotWaiting(ValueError):
    """Decisions were given for a run that is not waiting for any."""


@dataclass(frozen=True, kw_only=True)
class RunResult:
    run_id: str
    state: RunState
    request: DecisionRequest | None = None
    """The questions the run is waiting on, when it paused for decisions."""

    @property
    def paused(self) -> bool:
        return self.request is not None


async def start(store: RunStore, context: RunContext, state: RunState) -> RunResult:
    """Start a new run from `state`, and run it until it finishes or pauses."""
    name = state["feature"] or ("plan" if state["given_plans"] else None)
    run_id = store.create(name)
    async with store.checkpointer(run_id) as saver:
        graph = build_graph(checkpointer=saver)
        config = run_config(context.charter, run_id)
        await graph.ainvoke(state, config, context=context, durability=DURABILITY)
        return _result(run_id, await graph.aget_state(config))


async def resume(
    store: RunStore,
    run_id: str,
    context: RunContext,
    answers: Mapping[str, str] | None = None,
) -> RunResult:
    """Carry on a run: with the user's decisions if it paused for them, or from
    its last finished step if it was stopped.

    A run that is still waiting is left waiting until `answers` are given, and a
    finished run is returned as it is.
    """
    if not store.exists(run_id):
        raise RunNotFound(f"no run {run_id!r}")
    async with store.checkpointer(run_id) as saver:
        graph = build_graph(checkpointer=saver)
        config = run_config(context.charter, run_id)
        snapshot = await graph.aget_state(config)
        before = _result(run_id, snapshot)
        if answers is not None and not before.paused:
            raise NotWaiting(f"run {run_id} is not waiting for decisions")
        if answers is not None and not answers:
            # LangGraph takes an empty answer for no answer, and would just ask again.
            raise ValueError("give a decision for each question")
        finished = not snapshot.next and not before.paused
        if finished or (before.paused and answers is None):
            return before
        command: Any = None if answers is None else Command(resume=dict(answers))
        await graph.ainvoke(command, config, context=context, durability=DURABILITY)
        return _result(run_id, await graph.aget_state(config))


def _result(run_id: str, snapshot: StateSnapshot) -> RunResult:
    request = snapshot.interrupts[0].value if snapshot.interrupts else None
    return RunResult(run_id=run_id, state=cast("RunState", snapshot.values), request=request)
