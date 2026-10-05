from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from aiarb.domain.scoring import Severity
from aiarb.graph import prompts
from aiarb.graph.nodes._calls import impact_note, severity_levels
from aiarb.ports.calls import Cost

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

    from aiarb.domain.ledger import Objection
    from aiarb.graph.context import RunContext
    from aiarb.graph.state import RunState, StateUpdate
    from aiarb.ports.scorer import Judgement


async def rescore(state: RunState, runtime: Runtime[RunContext]) -> StateUpdate:
    """Score the items still open after the debate again, and confirm every blocker.

    A blocker decides that the plan is not ready, so a language model is asked
    whether each one really is. The call stands unless it confidently says no,
    in which case the item is rated major. Every change is recorded, and an item
    rated lower than before though nobody settled it is flagged.
    """
    still_open = [item for item in state["ledger"].values() if item.is_open]
    if not still_open:
        return {}
    context = runtime.context
    charter = context.charter
    thresholds = charter.scoring.thresholds
    levels = severity_levels(thresholds)
    subjects = {item.id: prompts.scored(item, charter) for item in still_open}
    judgements = await asyncio.gather(
        *(context.scorer.score(subjects[item.id], prompts.IMPACT, levels) for item in still_open)
    )
    severities = {
        item.id: thresholds.classify(judgement.value)
        for item, judgement in zip(still_open, judgements, strict=True)
    }
    blockers = [item for item in still_open if severities[item.id] is Severity.BLOCKER]
    checks = await asyncio.gather(
        *(context.confirmer.yes_no(subjects[item.id], prompts.IS_BLOCKER) for item in blockers)
    )
    confirmations = {item.id: check for item, check in zip(blockers, checks, strict=True)}

    round = state["round"]
    changed: dict[str, Objection] = {}
    for item, judgement in zip(still_open, judgements, strict=True):
        severity = severities[item.id]
        updated = item
        if severity is not item.severity:
            note = impact_note("Rescored after the debate:", judgement, severity)
            updated = updated.rescore(severity, round=round, note=note + _flag(updated, severity))
        check = confirmations.get(item.id)
        if check is not None:
            refuted = not check.value and check.confidence >= charter.scoring.confidence_threshold
            severity = Severity.MAJOR if refuted else Severity.BLOCKER
            note = _confirmation(check, refuted)
            updated = updated.rescore(severity, round=round, note=note + _flag(updated, severity))
        if updated is not item:
            changed[item.id] = updated
    spent = sum((j.cost for j in judgements), Cost()) + sum((c.cost for c in checks), Cost())
    update: StateUpdate = {"costs": {"scorer": spent}}
    if changed:
        update["ledger"] = changed
    return update


def _confirmation(check: Judgement[bool], refuted: bool) -> str:
    said = f"{'yes' if check.value else 'no'}, with confidence {check.confidence:.2f}"
    if refuted:
        return f"A language model did not confirm it as a blocker ({said}), so it is rated major."
    if check.value:
        return f"A language model confirmed it as a blocker ({said})."
    return f"A language model doubted it is a blocker but was unsure ({said}), so the call stands."


def _flag(item: Objection, severity: Severity) -> str:
    """A warning when `severity` is lower than the scorer rated this open item before."""
    peak = item.peak_severity
    if peak is None or peak.rank <= severity.rank:
        return ""
    return f" Flagged: it was rated {peak} before and is still open, though nobody settled it."
