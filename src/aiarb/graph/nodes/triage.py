from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from aiarb.domain.ledger import Actor, Status
from aiarb.graph import prompts
from aiarb.graph.nodes._calls import severity_levels
from aiarb.ports.calls import Cost

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

    from aiarb.graph.context import RunContext
    from aiarb.graph.state import RunState, StateUpdate


async def triage(state: RunState, runtime: Runtime[RunContext]) -> StateUpdate:
    """Score each new objection's impact. Minor ones skip the debate."""
    new = [item for item in state["ledger"].values() if len(item.history) == 1]
    if not new:
        return {}
    thresholds = runtime.context.charter.scoring.thresholds
    levels = severity_levels(thresholds)
    judgements = await asyncio.gather(
        *(runtime.context.scorer.score(prompts.score(item), levels) for item in new)
    )
    updated = {}
    for item, judgement in zip(new, judgements, strict=True):
        severity = thresholds.classify(judgement.value)
        scored = item.rescore(
            severity,
            round=state["round"],
            note=f"Scored {judgement.value:.2f} for impact, "
            f"with confidence {judgement.confidence:.2f}.",
        )
        if severity.skips_debate:
            scored = scored.transition(
                Status.NOTED,
                actor=Actor.SYSTEM,
                round=state["round"],
                note="Minor: passed to the planner as a note instead of being debated.",
            )
        updated[scored.id] = scored
    return {
        "ledger": updated,
        "costs": {"scorer": sum((judgement.cost for judgement in judgements), Cost())},
    }
