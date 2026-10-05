from __future__ import annotations

from typing import TYPE_CHECKING

from aiarb.domain.ledger import Actor, Status
from aiarb.graph import prompts
from aiarb.graph.nodes._calls import ask, by_id, in_status
from aiarb.schemas.outputs import Accept, Rulings

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

    from aiarb.graph.context import RunContext
    from aiarb.graph.state import RunState, StateUpdate

_VERDICTS = {"accept": Status.RESOLVED, "reject": Status.OPEN, "dispute": Status.DISPUTED}


async def rule(state: RunState, runtime: Runtime[RunContext]) -> StateUpdate:
    """The critic accepts, rejects or disputes each of the planner's answers."""
    answered = in_status(state, Status.ANSWERED)
    if not answered:
        return {}
    result = await ask(
        runtime,
        "critic",
        prompts.rule(state, answered),
        Rulings,
        session_id=state["sessions"].get("critic"),
    )
    rulings = by_id("critic", result.output.rulings, answered, result.cost)
    ruled = {}
    for item in answered:
        ruling = rulings[item.id]
        ruled[item.id] = item.transition(
            _VERDICTS[ruling.verdict],
            actor=Actor.CRITIC,
            round=state["round"],
            note=ruling.note,
            resolution=ruling.resolution.to_resolution() if isinstance(ruling, Accept) else None,
        )
    return {
        "ledger": ruled,
        "costs": {"critic": result.cost},
        "sessions": {"critic": result.session_id},
    }
