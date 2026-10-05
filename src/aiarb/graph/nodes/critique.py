from __future__ import annotations

from typing import TYPE_CHECKING

from aiarb.domain.ledger import Objection
from aiarb.graph import prompts
from aiarb.graph.nodes._calls import ask
from aiarb.schemas.outputs import Critique, refs

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

    from aiarb.graph.context import RunContext
    from aiarb.graph.state import RunState, StateUpdate


async def critique(state: RunState, runtime: Runtime[RunContext]) -> StateUpdate:
    """The critic raises objections to the plan, numbered after any already raised."""
    result = await ask(
        runtime,
        "critic",
        prompts.critique(state),
        Critique,
        session_id=state["sessions"].get("critic"),
    )
    first = len(state["ledger"]) + 1
    raised = [
        Objection.raised(
            id=f"OBJ-{number}",
            claim=objection.claim,
            evidence=refs(objection.evidence),
            type=objection.type,
            severity=objection.severity,
            round=state["round"],
        )
        for number, objection in enumerate(result.output.objections, start=first)
    ]
    return {
        "ledger": {item.id: item for item in raised},
        "costs": {"critic": result.cost},
        "sessions": {"critic": result.session_id},
    }
