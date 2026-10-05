"""Helpers the nodes share for calling the ports."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypeVar

from aiarb.domain.scoring import RUBRIC, Severity, SeverityThresholds
from aiarb.ports.calls import RetryableCallError
from aiarb.ports.code_agent import AgentRequest, AgentResult, OutputT
from aiarb.ports.scorer import Level

if TYPE_CHECKING:
    from collections.abc import Sequence

    from langgraph.runtime import Runtime

    from aiarb.domain.ledger import Objection, Status
    from aiarb.graph.context import RunContext
    from aiarb.graph.state import Role, RunState
    from aiarb.ports.calls import Cost


async def ask(
    runtime: Runtime[RunContext],
    role: Role,
    prompt: str,
    schema: type[OutputT],
    *,
    session_id: str | None = None,
) -> AgentResult[OutputT]:
    """One agent turn, on the model the charter gives this role."""
    context = runtime.context
    request = AgentRequest(
        prompt=prompt,
        repo=context.repo,
        model=getattr(context.charter.models, role),
        schema=schema,
        session_id=session_id,
    )
    return await context.agent.run(request)


def severity_levels(thresholds: SeverityThresholds) -> list[Level]:
    """The severity rubric as scoring levels: each severity starts at its threshold."""
    starts = {
        Severity.MINOR: 0.0,
        Severity.MODERATE: thresholds.moderate,
        Severity.MAJOR: thresholds.major,
        Severity.BLOCKER: thresholds.blocker,
    }
    return [Level(starts[severity], RUBRIC[severity]) for severity in Severity]


class _Identified(Protocol):
    @property
    def id(self) -> str: ...


IdentifiedT = TypeVar("IdentifiedT", bound=_Identified)


def by_id(
    role: Role, replies: Sequence[IdentifiedT], expected: Sequence[Objection], cost: Cost
) -> dict[str, IdentifiedT]:
    """Match replies to objections, refusing a reply set that skips or invents any.

    A bad set is retryable: asking again usually gets a complete answer.
    """
    got = [reply.id for reply in replies]
    wanted = [item.id for item in expected]
    if sorted(got) != sorted(wanted):
        problems = [
            f"{label}: {', '.join(ids)}"
            for label, ids in (
                ("missing", sorted(set(wanted) - set(got))),
                ("unknown", sorted(set(got) - set(wanted))),
                ("repeated", sorted({id for id in got if got.count(id) > 1})),
            )
            if ids
        ]
        raise RetryableCallError(
            f"the {role} must reply to each of {', '.join(wanted)} exactly once "
            f"({'; '.join(problems)})",
            cost=cost,
        )
    return {reply.id: reply for reply in replies}


def in_status(state: RunState, *statuses: Status) -> list[Objection]:
    """The objections in any of these statuses, in the order they were raised."""
    return [item for item in state["ledger"].values() if item.status in statuses]
