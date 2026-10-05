from __future__ import annotations

from typing import TYPE_CHECKING

from aiarb.domain.ledger import Actor, Status
from aiarb.graph import prompts
from aiarb.graph.nodes._calls import ask, by_id, in_status
from aiarb.schemas.outputs import Responses

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

    from aiarb.graph.context import RunContext
    from aiarb.graph.state import RunState, StateUpdate


async def respond(state: RunState, runtime: Runtime[RunContext]) -> StateUpdate:
    """Start a round: the planner fixes or rebuts every open objection."""
    round = state["round"] + 1
    open_items = in_status(state, Status.OPEN)
    if not open_items:
        return {}
    notes = in_status(state, Status.NOTED)
    result = await ask(
        runtime,
        "planner",
        prompts.respond(state, open_items, notes),
        Responses,
        session_id=state["sessions"].get("planner"),
    )
    answers = by_id("planner", result.output.answers, open_items, result.cost)
    answered = {
        item.id: item.transition(
            Status.ANSWERED,
            actor=Actor.PLANNER,
            round=round,
            note=f"{answers[item.id].response.capitalize()}: {answers[item.id].note}",
            refs=answers[item.id].cited,
        )
        for item in open_items
    }
    return {
        "round": round,
        "plan": result.output.plan,
        "ledger": answered,
        "costs": {"planner": result.cost},
        "sessions": {"planner": result.session_id},
    }
