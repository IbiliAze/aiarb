from __future__ import annotations

from typing import TYPE_CHECKING

from aiarb.graph import prompts
from aiarb.graph.nodes._calls import ask
from aiarb.schemas.outputs import PlanDraft

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

    from aiarb.graph.context import RunContext
    from aiarb.graph.state import RunState, StateUpdate


async def draft_plan(state: RunState, runtime: Runtime[RunContext]) -> StateUpdate:
    """The planner drafts the plan, unless the user brought one to critique."""
    if state["given_plans"]:
        return {"plan": "\n\n---\n\n".join(state["given_plans"])}
    result = await ask(runtime, "planner", prompts.draft(state), PlanDraft)
    return {
        "plan": result.output.plan,
        "costs": {"planner": result.cost},
        "sessions": {"planner": result.session_id},
    }
