from __future__ import annotations

from typing import TYPE_CHECKING

from aiarb.scan.facts import fact_sheet

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

    from aiarb.graph.context import RunContext
    from aiarb.graph.state import RunState, StateUpdate


def scan(state: RunState, runtime: Runtime[RunContext]) -> StateUpdate:
    """Build the repo fact sheet. No model is involved."""
    return {"facts": fact_sheet(runtime.context.repo)}
