from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from aiarb.graph import prompts
from aiarb.graph.nodes._calls import severity_levels
from aiarb.ports.calls import Cost

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

    from aiarb.domain.ledger import Objection
    from aiarb.graph.context import RunContext
    from aiarb.graph.state import RunState, StateUpdate


async def rescore(state: RunState, runtime: Runtime[RunContext]) -> StateUpdate:
    """Score the items still open after the debate again, recording any change."""
    still_open = [item for item in state["ledger"].values() if item.is_open]
    if not still_open:
        return {}
    thresholds = runtime.context.charter.scoring.thresholds
    levels = severity_levels(thresholds)
    judgements = await asyncio.gather(
        *(runtime.context.scorer.score(prompts.score(item), levels) for item in still_open)
    )
    changed: dict[str, Objection] = {}
    for item, judgement in zip(still_open, judgements, strict=True):
        severity = thresholds.classify(judgement.value)
        if severity is not item.severity:
            changed[item.id] = item.rescore(
                severity,
                round=state["round"],
                note=f"Rescored after the debate: {judgement.value:.2f}, "
                f"with confidence {judgement.confidence:.2f}.",
            )
    update: StateUpdate = {"costs": {"scorer": sum((j.cost for j in judgements), Cost())}}
    if changed:
        update["ledger"] = changed
    return update
