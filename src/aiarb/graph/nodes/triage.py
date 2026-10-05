from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from aiarb.domain.ledger import Actor, Status
from aiarb.graph import prompts
from aiarb.graph.nodes._calls import impact_note, severity_levels
from aiarb.ports.calls import Cost

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

    from aiarb.graph.context import RunContext
    from aiarb.graph.state import RunState, StateUpdate


async def triage(state: RunState, runtime: Runtime[RunContext]) -> StateUpdate:
    """Score each new objection's impact. Minor ones skip the debate.

    The rest are debated most severe first: `respond` and `rule` order them so.
    """
    new = [item for item in state["ledger"].values() if len(item.history) == 1]
    if not new:
        return {}
    charter = runtime.context.charter
    thresholds = charter.scoring.thresholds
    levels = severity_levels(thresholds)
    judgements = await asyncio.gather(
        *(
            runtime.context.scorer.score(prompts.scored(item, charter), prompts.IMPACT, levels)
            for item in new
        )
    )
    updated = {}
    for item, judgement in zip(new, judgements, strict=True):
        severity = thresholds.classify(judgement.value)
        scored = item.rescore(
            severity, round=state["round"], note=impact_note("Scored", judgement, severity)
        )
        if severity.skips_debate:
            scored = scored.transition(
                Status.NOTED,
                actor=Actor.SYSTEM,
                round=state["round"],
                note="Minor: noted instead of debated.",
            )
        updated[scored.id] = scored
    return {
        "ledger": updated,
        "costs": {"scorer": sum((judgement.cost for judgement in judgements), Cost())},
    }
