"""What the output files are rendered from: the finished run, with the wording
they share."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aiarb.config.charter import Charter
from aiarb.domain.ledger import Actor, CodeRef, Objection, Status
from aiarb.domain.outcome import Outcome, RunStatus, outcome
from aiarb.graph.state import Role
from aiarb.ports.calls import Cost
from aiarb.schemas.outputs import Explanation

if TYPE_CHECKING:
    from collections.abc import Iterable

    from aiarb.domain.ledger import Event, Ref
    from aiarb.graph.state import RunState
    from aiarb.schemas.outputs import ExplainedItem


@dataclass(frozen=True, kw_only=True)
class Report:
    run_id: str | None
    feature: str | None
    plan: str
    rounds: int
    ledger: Mapping[str, Objection]
    explanation: Explanation | None
    outcome: Outcome
    costs: Mapping[Role, Cost]
    charter: Charter

    @classmethod
    def of(cls, state: RunState, charter: Charter, run_id: str | None) -> Report:
        return cls(
            run_id=run_id,
            feature=state["feature"],
            plan=state["plan"],
            rounds=state["round"],
            ledger=state["ledger"],
            explanation=state["explanation"],
            outcome=outcome(state["ledger"]),
            costs=state["costs"],
            charter=charter,
        )

    def explained(self, item: Objection) -> ExplainedItem | None:
        """The explainer's account of the item, if it gave one."""
        if self.explanation is None:
            return None
        return next((e for e in self.explanation.items if e.id == item.id), None)

    def title(self, item: Objection) -> str:
        explained = self.explained(item)
        return explained.title if explained is not None else _first_sentence(item.claim)

    def what_now(self, item: Objection) -> str:
        """How the item ended, or for an open one what to do about it: in the
        explainer's words if it gave them, otherwise the ledger's."""
        explained = self.explained(item)
        return explained.outcome if explained is not None else ending(item).note


STATUS_LABELS: Mapping[Status, str] = {
    Status.OPEN: "open",
    Status.ANSWERED: "open, answered by the planner",
    Status.DISPUTED: "open, a disputed fact",
    Status.NOTED: "noted: minor, passed to the planner without debate",
    Status.RESOLVED: "resolved",
    Status.RULED: "ruled by the judge, not agreed",
    Status.AWAITING_DECISION: "waiting for your decision",
    Status.DECIDED: "decided by you",
    Status.DEFERRED: "deferred: settle before building",
}

SHORT_STATUS_LABELS: Mapping[Status, str] = {
    Status.OPEN: "open",
    Status.ANSWERED: "answered",
    Status.DISPUTED: "disputed",
    Status.AWAITING_DECISION: "waiting for you",
    Status.DEFERRED: "deferred",
    Status.RULED: "ruled",
    Status.DECIDED: "decided",
    Status.RESOLVED: "resolved",
    Status.NOTED: "noted",
}
"""For counts, in this order: what needs attention first."""

RUN_STATUS_LABELS: Mapping[RunStatus, str] = {
    RunStatus.SETTLED: "Settled",
    RunStatus.SETTLED_WITH_OPEN_ITEMS: "Settled with open items",
    RunStatus.NOT_READY: "Not ready",
}


def cite(refs: Iterable[Ref]) -> str:
    """References as Markdown: code paths in backticks, plan sections by name."""
    return "; ".join(_cite_one(ref) for ref in refs)


def _cite_one(ref: Ref) -> str:
    if isinstance(ref, CodeRef):
        if ref.start_line is None:
            return f"`{ref.path}`"
        end = f"-{ref.end_line}" if ref.end_line not in (None, ref.start_line) else ""
        return f"`{ref.path}:{ref.start_line}{end}`"
    quote = f': "{ref.quote}"' if ref.quote else ""
    return f"plan § {ref.section}{quote}"


def last_by(item: Objection, *actors: Actor) -> Event | None:
    """The item's latest event by any of these actors."""
    return next((event for event in reversed(item.history) if event.actor in actors), None)


def ending(item: Objection) -> Event:
    """The event that put the item in its current status: the latest one that was
    not a rescore."""
    return (
        last_by(item, *(actor for actor in Actor if actor is not Actor.SCORER)) or (item.history[0])
    )


def decision(item: Objection) -> str:
    """What the user decided, for a decided trade-off."""
    return ending(item).note.removeprefix("Decided: ")


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _first_sentence(text: str, limit: int = 80) -> str:
    sentence = text.strip().split(". ")[0].rstrip(".")
    return sentence if len(sentence) <= limit else sentence[: limit - 1].rstrip() + "…"
