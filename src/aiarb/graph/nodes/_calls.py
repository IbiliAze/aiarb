"""Helpers the nodes share for calling the ports."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypeVar

from aiarb.domain.scoring import RUBRIC, Severity, SeverityThresholds
from aiarb.ports.calls import RetryableCallError
from aiarb.ports.code_agent import AgentRequest, AgentResult, OutputT
from aiarb.ports.scorer import Level

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from langgraph.runtime import Runtime

    from aiarb.domain.ledger import Objection, Status
    from aiarb.graph.context import RunContext
    from aiarb.graph.state import Role, RunState
    from aiarb.ports.calls import Cost
    from aiarb.ports.scorer import Judgement


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


def impact_note(lead: str, judgement: Judgement[float], severity: Severity) -> str:
    """What the ledger records of an impact score."""
    note = (
        f"{lead} {judgement.value:.2f} for impact ({severity}), "
        f"with confidence {judgement.confidence:.2f}."
    )
    first = judgement.escalated_from
    if first is not None:
        note += (
            f" The fallback scorer gave this after the first scorer's {first.value:.2f}, "
            f"with confidence {first.confidence:.2f}, fell below the confidence threshold."
        )
    return note


def by_severity(items: Iterable[Objection]) -> list[Objection]:
    """Most severe first, and in the order raised within a severity."""
    return sorted(items, key=lambda item: -item.severity.rank)


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
