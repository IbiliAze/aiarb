"""The state the debate carries from node to node.

Nodes return partial updates. Most keys are plain values that only one node may
write at a time; LangGraph refuses two concurrent writes to them. The ledger,
costs and sessions have reducers, so nodes running in parallel can each update
them and the updates combine.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal, TypedDict

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from aiarb.domain import ledger
from aiarb.domain.ledger import Objection
from aiarb.domain.outcome import RunStatus
from aiarb.domain.scoring import Severity
from aiarb.ports.calls import Cost
from aiarb.schemas.outputs import ExplainedItem, Explanation

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

Role = Literal["planner", "critic", "verifier", "judge", "explainer", "scorer"]
"""Every role that makes outside calls, and so has sessions and costs."""


class LedgerConflict(ValueError):
    """Two updates to one objection that do not build on each other."""


def merge_ledger(
    current: Mapping[str, Objection], update: Mapping[str, Objection]
) -> dict[str, Objection]:
    """Add new objections, and replace ones whose history the update extends.

    An update that drops or rewrites history is refused. That is also what
    catches two parallel nodes changing the same objection: the second one's
    history no longer extends what is in the ledger.
    """
    merged = dict(current)
    for key, objection in update.items():
        if key != objection.id:
            raise LedgerConflict(f"objection {objection.id} was filed under {key!r}")
        existing = merged.get(key)
        if existing is not None:
            known = existing.history
            if objection.history[: len(known)] != known:
                raise LedgerConflict(
                    f"{key}: the update does not extend the ledger's history; "
                    "it is stale, or another node changed the objection at the same time"
                )
        merged[key] = objection
    return merged


def add_costs(current: Mapping[Role, Cost], update: Mapping[Role, Cost]) -> dict[Role, Cost]:
    """Add each role's new spend to its total."""
    totals = dict(current)
    for role, cost in update.items():
        totals[role] = totals.get(role, Cost()) + cost
    return totals


def merge_sessions(current: Mapping[Role, str], update: Mapping[Role, str]) -> dict[Role, str]:
    """Keep the latest session for each role."""
    return {**current, **update}


class RunState(TypedDict):
    feature: str | None
    """What the user wants to build, if they said."""
    given_plans: list[str]
    """Plans the user brought from earlier sessions. With any, the run starts at critique."""
    facts: str
    """The repo fact sheet from the scan, as the agents are shown it."""
    plan: str
    """The current plan, in Markdown. Empty until drafted."""
    round: int
    """0 for the opening; the planner's response starts each later round."""
    ledger: Annotated[dict[str, Objection], merge_ledger]
    costs: Annotated[dict[Role, Cost], add_costs]
    sessions: Annotated[dict[Role, str], merge_sessions]
    explanation: Explanation | None
    """The ledger in plain English, from the explainer once the debate is over."""
    status: RunStatus | None
    """How the run ended. None until it finishes."""


class StateUpdate(TypedDict, total=False):
    """What a node returns: any subset of the state's keys."""

    facts: str
    plan: str
    round: int
    ledger: dict[str, Objection]
    costs: dict[Role, Cost]
    sessions: dict[Role, str]
    explanation: Explanation
    status: RunStatus


def initial_state(*, feature: str | None = None, given_plans: Sequence[str] = ()) -> RunState:
    """The state a new run starts from."""
    return RunState(
        feature=feature,
        given_plans=list(given_plans),
        facts="",
        plan="",
        round=0,
        ledger={},
        costs={},
        sessions={},
        explanation=None,
        status=None,
    )


def total_cost(state: RunState) -> Cost:
    return sum(state["costs"].values(), Cost())


CHECKPOINT_TYPES: tuple[tuple[str, str], ...] = tuple(
    (cls.__module__, cls.__qualname__)
    for cls in (
        Objection,
        ledger.Event,
        ledger.CodeRef,
        ledger.PlanRef,
        ledger.Resolution,
        ledger.ResolutionKind,
        ledger.ObjectionType,
        ledger.Status,
        ledger.Actor,
        Severity,
        RunStatus,
        Cost,
        Explanation,
        ExplainedItem,
    )
)
"""Every class that can appear in the state, for the checkpoint allow-list."""


def checkpoint_serializer() -> JsonPlusSerializer:
    """A serializer that restores the state's own types and refuses any others."""
    return JsonPlusSerializer(allowed_msgpack_modules=CHECKPOINT_TYPES)
