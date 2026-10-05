from __future__ import annotations

from typing import TYPE_CHECKING

from aiarb.graph import prompts
from aiarb.graph.nodes._calls import ask, by_id
from aiarb.schemas.outputs import Explanation

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

    from aiarb.graph.context import RunContext
    from aiarb.graph.state import RunState, StateUpdate


async def explain(state: RunState, runtime: Runtime[RunContext]) -> StateUpdate:
    """The explainer puts every objection, and how it ended, in plain English.

    A run with no objections has nothing to explain, and makes no call.
    """
    items = list(state["ledger"].values())
    if not items:
        return {}
    result = await ask(runtime, "explainer", prompts.explain(state), Explanation)
    by_id("explainer", result.output.items, items, result.cost)
    return {"explanation": result.output, "costs": {"explainer": result.cost}}
