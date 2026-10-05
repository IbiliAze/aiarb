"""The objection ledger: every objection the critic raised, and everything that
happened to it afterwards.

An objection moves through a fixed set of statuses. Each move is checked against
`TRANSITIONS`, which says who may make it and for which types of objection, and
against the closing rule: an objection is resolved only by a plan change or by
cited evidence, never because one side agreed with the other.

Objections are immutable. Every move returns a new objection with one more event
in its history, so the history is the full record of the debate.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import TypeAlias

from aiarb.domain.scoring import Severity


class TransitionError(ValueError):
    """A move the ledger's rules do not allow."""


class Actor(StrEnum):
    PLANNER = "planner"
    CRITIC = "critic"
    VERIFIER = "verifier"
    JUDGE = "judge"
    SCORER = "scorer"
    HUMAN = "human"
    SYSTEM = "system"
    """Deterministic graph logic, such as triage and round-limit routing."""


class ObjectionType(StrEnum):
    FACTUAL = "factual"
    """A claim about what the code does or how the system behaves."""
    DESIGN = "design"
    """A flaw in the plan that has a right answer."""
    TRADE_OFF = "trade_off"
    """A choice between legitimate priorities, which only the user can make."""


class Status(StrEnum):
    OPEN = "open"
    """Raised or reopened, waiting for the planner."""
    ANSWERED = "answered"
    """The planner has fixed or rebutted it, waiting for the critic's ruling."""
    DISPUTED = "disputed"
    """A factual disagreement, waiting for the verifier to check the repo."""
    NOTED = "noted"
    """Minor; passed to the planner as a note instead of being debated."""
    RESOLVED = "resolved"
    """Closed by a plan change or cited evidence."""
    RULED = "ruled"
    """Deadlocked at the round limit and decided by the judge: ruled, not agreed."""
    AWAITING_DECISION = "awaiting_decision"
    """A trade-off left at the round limit, waiting for the user."""
    DECIDED = "decided"
    """A trade-off the user has ruled on."""
    DEFERRED = "deferred"
    """A fact that reading the code cannot settle; it becomes a task in the plan."""

    @property
    def is_open(self) -> bool:
        """Whether the item still counts against the plan as an open item."""
        return self in _OPEN_STATUSES


_OPEN_STATUSES = frozenset(
    {Status.OPEN, Status.ANSWERED, Status.DISPUTED, Status.AWAITING_DECISION, Status.DEFERRED}
)


@dataclass(frozen=True)
class CodeRef:
    """A file in the target repo, optionally narrowed to a line range."""

    path: str
    start_line: int | None = None
    end_line: int | None = None

    def __post_init__(self) -> None:
        parts = PurePosixPath(self.path).parts
        if not self.path.strip() or PurePosixPath(self.path).is_absolute() or ".." in parts:
            raise ValueError(f"a code reference must be a path inside the repo, got {self.path!r}")
        if self.start_line is None:
            if self.end_line is not None:
                raise ValueError("a code reference with an end line needs a start line")
        elif self.start_line < 1:
            raise ValueError(f"line numbers start at 1, got {self.start_line}")
        elif self.end_line is not None and self.end_line < self.start_line:
            raise ValueError(f"end line {self.end_line} is before start line {self.start_line}")


@dataclass(frozen=True)
class PlanRef:
    """A section of the plan, optionally with the line quoted.

    A quote survives later revisions of the plan in a way a line number does not.
    """

    section: str
    quote: str | None = None

    def __post_init__(self) -> None:
        if not self.section.strip():
            raise ValueError("a plan reference must name a section")


Ref: TypeAlias = CodeRef | PlanRef


def _freeze(instance: object, *fields: str) -> None:
    """Store sequence fields as tuples, whatever they were given as.

    Checkpoints restore tuples as lists. Without this, a restored objection would
    not equal the one that was saved.
    """
    for name in fields:
        object.__setattr__(instance, name, tuple(getattr(instance, name)))


class ResolutionKind(StrEnum):
    PLAN_CHANGE = "plan_change"
    """The plan was changed to address the objection."""
    EVIDENCE = "evidence"
    """Cited evidence shows the objection does not hold, or is already handled."""


@dataclass(frozen=True)
class Resolution:
    kind: ResolutionKind
    refs: tuple[Ref, ...]

    def __post_init__(self) -> None:
        _freeze(self, "refs")
        if not self.refs:
            raise ValueError("a resolution must cite what settled it")
        if self.kind is ResolutionKind.PLAN_CHANGE and not any(
            isinstance(ref, PlanRef) for ref in self.refs
        ):
            raise ValueError("a plan-change resolution must cite the plan section that changed")


@dataclass(frozen=True, kw_only=True)
class Event:
    """One step in an objection's history, recording its state afterwards."""

    round: int
    actor: Actor
    status: Status
    severity: Severity
    note: str
    refs: tuple[Ref, ...] = ()
    resolution: Resolution | None = None

    def __post_init__(self) -> None:
        _freeze(self, "refs")
        if self.round < 0:
            raise ValueError(f"rounds start at 0, got {self.round}")
        if not self.note.strip():
            raise ValueError("every event needs a note saying what happened")


@dataclass(frozen=True)
class Rule:
    """Who may make a move, and for which types of objection (None: any type)."""

    actors: frozenset[Actor]
    types: frozenset[ObjectionType] | None = None


def _rule(*actors: Actor, types: Iterable[ObjectionType] | None = None) -> Rule:
    return Rule(frozenset(actors), None if types is None else frozenset(types))


# Moves into RULED, AWAITING_DECISION and DEFERRED from OPEN happen only at the
# round limit, where leftover items are handled by type. The moves back to OPEN
# from parked or closed statuses are the user reopening items on resume.
TRANSITIONS: Mapping[tuple[Status, Status], Rule] = MappingProxyType(
    {
        (Status.OPEN, Status.ANSWERED): _rule(Actor.PLANNER),
        (Status.OPEN, Status.NOTED): _rule(Actor.SYSTEM),
        (Status.ANSWERED, Status.RESOLVED): _rule(Actor.CRITIC),
        (Status.ANSWERED, Status.OPEN): _rule(Actor.CRITIC),
        (Status.ANSWERED, Status.DISPUTED): _rule(Actor.CRITIC),
        (Status.DISPUTED, Status.RESOLVED): _rule(Actor.VERIFIER),
        (Status.DISPUTED, Status.OPEN): _rule(Actor.VERIFIER),
        (Status.DISPUTED, Status.DEFERRED): _rule(Actor.VERIFIER),
        (Status.OPEN, Status.DEFERRED): _rule(Actor.SYSTEM, types=[ObjectionType.FACTUAL]),
        (Status.OPEN, Status.AWAITING_DECISION): _rule(
            Actor.SYSTEM, types=[ObjectionType.TRADE_OFF]
        ),
        (Status.OPEN, Status.RULED): _rule(Actor.JUDGE, types=[ObjectionType.DESIGN]),
        (Status.AWAITING_DECISION, Status.DECIDED): _rule(Actor.HUMAN),
        (Status.AWAITING_DECISION, Status.OPEN): _rule(Actor.HUMAN),
        (Status.DEFERRED, Status.OPEN): _rule(Actor.HUMAN),
        (Status.RULED, Status.OPEN): _rule(Actor.HUMAN),
        (Status.DECIDED, Status.OPEN): _rule(Actor.HUMAN),
        (Status.RESOLVED, Status.OPEN): _rule(Actor.HUMAN),
        (Status.NOTED, Status.OPEN): _rule(Actor.HUMAN),
    }
)


@dataclass(frozen=True, kw_only=True)
class Objection:
    id: str
    claim: str
    evidence: tuple[Ref, ...]
    """The code or plan lines the objection concerns."""
    type: ObjectionType
    history: tuple[Event, ...]

    def __post_init__(self) -> None:
        _freeze(self, "evidence", "history")
        if not self.id.strip():
            raise ValueError("an objection needs an id")
        if not self.claim.strip():
            raise ValueError(f"{self.id}: an objection needs a claim")
        if not self.evidence:
            raise ValueError(f"{self.id}: an objection must cite the code or plan line it concerns")
        if not self.history or self.history[0].status is not Status.OPEN:
            raise ValueError(f"{self.id}: history must start with the objection being raised")
        rounds = [event.round for event in self.history]
        if rounds != sorted(rounds):
            raise ValueError(f"{self.id}: history rounds must not go backwards")

    @classmethod
    def raised(
        cls,
        *,
        id: str,
        claim: str,
        evidence: Iterable[Ref],
        type: ObjectionType,
        severity: Severity,
        round: int,
    ) -> Objection:
        """A new objection, as the critic raises it."""
        cited = tuple(evidence)
        raised = Event(
            round=round,
            actor=Actor.CRITIC,
            status=Status.OPEN,
            severity=severity,
            note="Raised.",
            refs=cited,
        )
        return cls(id=id, claim=claim, evidence=cited, type=type, history=(raised,))

    @property
    def status(self) -> Status:
        return self.history[-1].status

    @property
    def severity(self) -> Severity:
        return self.history[-1].severity

    @property
    def round(self) -> int:
        """The round of the most recent event."""
        return self.history[-1].round

    @property
    def is_open(self) -> bool:
        return self.status.is_open

    @property
    def peak_severity(self) -> Severity | None:
        """The highest severity the scorer has given this objection, if it has scored it."""
        scored = [event.severity for event in self.history if event.actor is Actor.SCORER]
        return max(scored, key=lambda severity: severity.rank, default=None)

    @property
    def dropped_from(self) -> Severity | None:
        """The scorer's highest severity for this objection, when it is still open
        and now rated lower.

        Nobody settled it, so the drop deserves a second look. Only the scorer's
        ratings count: the critic's, given when raising it, is one side's view.
        """
        peak = self.peak_severity
        if self.is_open and peak is not None and peak.rank > self.severity.rank:
            return peak
        return None

    @property
    def resolution(self) -> Resolution | None:
        return self.history[-1].resolution if self.status is Status.RESOLVED else None

    def transition(
        self,
        to: Status,
        *,
        actor: Actor,
        round: int,
        note: str,
        refs: Iterable[Ref] = (),
        resolution: Resolution | None = None,
    ) -> Objection:
        """Move to another status, if the rules allow it."""
        rule = TRANSITIONS.get((self.status, to))
        if rule is None:
            raise TransitionError(f"{self.id}: cannot move from {self.status} to {to}")
        if actor not in rule.actors:
            raise TransitionError(f"{self.id}: {actor} cannot move from {self.status} to {to}")
        if rule.types is not None and self.type not in rule.types:
            raise TransitionError(
                f"{self.id}: a {self.type} objection cannot move from {self.status} to {to}"
            )
        if to is Status.RESOLVED and resolution is None:
            raise TransitionError(
                f"{self.id}: an objection is resolved only by a plan change or cited evidence"
            )
        if to is not Status.RESOLVED and resolution is not None:
            raise TransitionError(f"{self.id}: only a move to {Status.RESOLVED} has a resolution")
        if to is Status.NOTED and not self.severity.skips_debate:
            raise TransitionError(
                f"{self.id}: a {self.severity} objection must be debated, not noted"
            )
        return self._record(
            round=round,
            actor=actor,
            status=to,
            severity=self.severity,
            note=note,
            refs=tuple(refs),
            resolution=resolution,
        )

    def rescore(self, severity: Severity, *, round: int, note: str) -> Objection:
        """Record a new severity from the scorer, keeping the status."""
        if not self.is_open:
            raise TransitionError(f"{self.id}: only open objections are rescored")
        return self._record(
            round=round, actor=Actor.SCORER, status=self.status, severity=severity, note=note
        )

    def _record(
        self,
        *,
        round: int,
        actor: Actor,
        status: Status,
        severity: Severity,
        note: str,
        refs: tuple[Ref, ...] = (),
        resolution: Resolution | None = None,
    ) -> Objection:
        if round < self.round:
            raise TransitionError(f"{self.id}: round {round} is before round {self.round}")
        if not note.strip():
            raise TransitionError(f"{self.id}: every move needs a note saying what happened")
        event = Event(
            round=round,
            actor=actor,
            status=status,
            severity=severity,
            note=note,
            refs=refs,
            resolution=resolution,
        )
        return replace(self, history=(*self.history, event))


Ledger: TypeAlias = Mapping[str, Objection]
"""Objections keyed by id."""
