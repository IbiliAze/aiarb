from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from aiarb.domain.ledger import Actor, ObjectionType, Status
from aiarb.graph import prompts
from aiarb.graph.nodes._calls import ask, in_status
from aiarb.ports.calls import Cost
from aiarb.schemas.outputs import JudgeRuling, refs

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

    from aiarb.domain.ledger import Objection
    from aiarb.graph.context import RunContext
    from aiarb.graph.state import RunState, StateUpdate


async def judge(state: RunState, runtime: Runtime[RunContext]) -> StateUpdate:
    """At the round limit, hand each item still open on by type.

    Facts become tasks to settle before building, trade-offs go to the user, and
    design deadlocks go to the judge, one call each.
    """
    leftovers = in_status(state, Status.OPEN)
    round = state["round"]
    handled: dict[str, Objection] = {}
    for item in leftovers:
        if item.type is ObjectionType.FACTUAL:
            handled[item.id] = item.transition(
                Status.DEFERRED,
                actor=Actor.SYSTEM,
                round=round,
                note="Unsettled at the round limit: becomes a task to run before building.",
            )
        elif item.type is ObjectionType.TRADE_OFF:
            handled[item.id] = item.transition(
                Status.AWAITING_DECISION,
                actor=Actor.SYSTEM,
                round=round,
                note="Unsettled at the round limit: a trade-off for the user to decide.",
            )
    deadlocked = [item for item in leftovers if item.type is ObjectionType.DESIGN]
    results = await asyncio.gather(
        *(ask(runtime, "judge", prompts.judge(state, item), JudgeRuling) for item in deadlocked)
    )
    for item, result in zip(deadlocked, results, strict=True):
        ruling = result.output
        handled[item.id] = item.transition(
            Status.RULED,
            actor=Actor.JUDGE,
            round=round,
            note=f"Ruled for the {ruling.favours}: {ruling.ruling} Why: {ruling.reason}",
            refs=refs(ruling.evidence),
        )
    if not handled:
        return {}
    update: StateUpdate = {"ledger": handled}
    if results:
        update["costs"] = {"judge": sum((result.cost for result in results), Cost())}
    return update
