"""Objections in each way a run can leave them, and a report to render."""

from __future__ import annotations

from typing import Any

from aiarb.config.charter import Charter
from aiarb.domain.ledger import (
    Actor,
    CodeRef,
    Objection,
    ObjectionType,
    PlanRef,
    Resolution,
    ResolutionKind,
    Status,
)
from aiarb.domain.outcome import outcome
from aiarb.domain.scoring import Severity
from aiarb.output import Report
from aiarb.ports.calls import Cost
from aiarb.schemas.outputs import ExplainedItem, Explanation

PLAN = """\
# Waiting room

## Summary

Hold buyers in a queue and admit them in batches.

## Queue storage

Keep queue places in Redis, keyed by session.

## Rollout

Ship behind a flag.
"""


def raised(
    id: str,
    claim: str,
    *evidence: CodeRef | PlanRef,
    type: ObjectionType = ObjectionType.DESIGN,
    severity: Severity = Severity.MAJOR,
) -> Objection:
    item = Objection.raised(
        id=id, claim=claim, evidence=evidence, type=type, severity=severity, round=0
    )
    return item.rescore(severity, round=0, note=f"Scored for impact ({severity}).")


def answered(item: Objection, note: str = "Rebuttal: It is fine.") -> Objection:
    return item.transition(Status.ANSWERED, actor=Actor.PLANNER, round=1, note=note)


def rejected(item: Objection, note: str = "Not persuaded.") -> Objection:
    return answered(item).transition(Status.OPEN, actor=Actor.CRITIC, round=1, note=note)


DEFERRED = (
    answered(
        raised(
            "OBJ-1",
            "Redis may not take one write per buyer at the on-sale peak.",
            PlanRef("Queue storage", "Keep queue places in Redis"),
            type=ObjectionType.FACTUAL,
            severity=Severity.BLOCKER,
        )
    )
    .transition(Status.DISPUTED, actor=Actor.CRITIC, round=1, note="Can Redis take it?")
    .transition(
        Status.DEFERRED,
        actor=Actor.VERIFIER,
        round=1,
        note="The repo has no load figures. To settle before building: load test Redis.",
    )
)
RESOLVED = answered(
    raised("OBJ-2", "Places are lost on restart.", CodeRef("app/queue.py", 10, 24)),
    "Fix: Places persist in Redis.",
).transition(
    Status.RESOLVED,
    actor=Actor.CRITIC,
    round=1,
    note="Redis keeps them.",
    resolution=Resolution(ResolutionKind.PLAN_CHANGE, (PlanRef("Queue storage"),)),
)
RULED = rejected(
    raised("OBJ-3", "The batch size is hard-coded.", PlanRef("2. Summary")),
).transition(
    Status.RULED,
    actor=Actor.JUDGE,
    round=1,
    note="Ruled for the critic: Read it from config. Why: It changes mid-sale.",
)
DECIDED = (
    rejected(
        raised(
            "OBJ-4",
            "Small batches are fairer but slower.",
            PlanRef("Rollout"),
            type=ObjectionType.TRADE_OFF,
            severity=Severity.MODERATE,
        )
    )
    .transition(
        Status.AWAITING_DECISION,
        actor=Actor.SYSTEM,
        round=1,
        note="Unsettled at the round limit: a trade-off for the user to decide.",
    )
    .transition(Status.DECIDED, actor=Actor.HUMAN, round=1, note="Decided: Fairness first.")
)
NOTED = raised(
    "OBJ-5", "The flag has no name.", PlanRef("Nowhere"), severity=Severity.MINOR
).transition(Status.NOTED, actor=Actor.SYSTEM, round=0, note="Minor.")

EVERY_ENDING = (DEFERRED, RESOLVED, RULED, DECIDED, NOTED)


def explanation(*items: Objection) -> Explanation:
    return Explanation(
        summary="The plan holds buyers in Redis. One question is open.",
        items=[
            ExplainedItem(
                id=item.id,
                title=f"Title of {item.id}",
                problem=f"Problem of {item.id}.",
                consequence=f"Consequence of {item.id}.",
                outcome=f"Outcome of {item.id}.",
            )
            for item in items
        ],
    )


def report(*items: Objection, plan: str = PLAN, explained: bool = True, **fields: Any) -> Report:
    ledger = {item.id: item for item in items}
    values: dict[str, Any] = {
        "run_id": "2026-10-05-waiting-room",
        "feature": "Add a virtual waiting room.",
        "plan": plan,
        "rounds": 1,
        "ledger": ledger,
        "explanation": explanation(*items) if explained and items else None,
        "outcome": outcome(ledger),
        "costs": {"planner": Cost(usd=1.25, input_tokens=1000), "scorer": Cost(usd=0.01)},
        "charter": Charter(),
    }
    values.update(fields)
    return Report(**values)
