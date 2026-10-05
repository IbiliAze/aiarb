from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from aiarb.domain.ledger import Actor, Resolution, ResolutionKind, Status
from aiarb.graph import prompts
from aiarb.graph.nodes._calls import ask, in_status
from aiarb.ports.calls import Cost
from aiarb.schemas.outputs import PlannerRight, Unverifiable, Verdict, refs

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

    from aiarb.graph.context import RunContext
    from aiarb.graph.state import RunState, StateUpdate

_FINDINGS = {
    "planner_right": Status.RESOLVED,
    "critic_right": Status.OPEN,
    "unverifiable": Status.DEFERRED,
}


async def verify(state: RunState, runtime: Runtime[RunContext]) -> StateUpdate:
    """The verifier checks each disputed fact against the repo, one call per dispute."""
    disputed = in_status(state, Status.DISPUTED)
    if not disputed:
        return {}
    results = await asyncio.gather(
        *(ask(runtime, "verifier", prompts.verify(state, item), Verdict) for item in disputed)
    )
    settled = {}
    for item, result in zip(disputed, results, strict=True):
        finding = result.output.result
        cited = refs(finding.evidence)
        planner_right = isinstance(finding, PlannerRight)
        settled[item.id] = item.transition(
            _FINDINGS[finding.finding],
            actor=Actor.VERIFIER,
            round=state["round"],
            note=(
                f"{finding.note} To settle before building: {finding.task}"
                if isinstance(finding, Unverifiable)
                else finding.note
            ),
            refs=() if planner_right else cited,
            resolution=Resolution(ResolutionKind.EVIDENCE, cited) if planner_right else None,
        )
    return {
        "ledger": settled,
        "costs": {"verifier": sum((result.cost for result in results), Cost())},
    }
