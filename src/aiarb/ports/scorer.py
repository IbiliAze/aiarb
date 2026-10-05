"""The port for rating how much something matters.

A scorer measures; it never decides who is right in the debate. Every answer
comes with a confidence, so a low-confidence answer can be passed to a language
model to confirm. Jev implements this port when configured, a language model
otherwise, and a scripted fake in tests.

Each call is about a subject, such as one objection, and asks one question of
it. The two are kept apart because Jev reads them differently: the subject is
the content it evaluates, and the question is what it is asked about it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Generic, Protocol, TypeVar, runtime_checkable

from aiarb.ports.calls import Cost

if TYPE_CHECKING:
    from collections.abc import Sequence

ValueT = TypeVar("ValueT", float, str, bool)


@dataclass(frozen=True)
class Level:
    """One anchor on a scoring rubric: what a given score means."""

    score: float
    meaning: str

    def __post_init__(self) -> None:
        if not 0 <= self.score <= 1:
            raise ValueError(f"a level's score must be between 0 and 1, got {self.score}")
        if not self.meaning.strip():
            raise ValueError("a level needs a meaning")


@dataclass(frozen=True, kw_only=True)
class Judgement(Generic[ValueT]):
    value: ValueT
    confidence: float
    """From 0 (a guess) to 1 (certain)."""
    cost: Cost
    escalated_from: Judgement[ValueT] | None = None
    """The first scorer's answer, when its confidence was too low and this one
    replaced it. The cost includes both."""

    def __post_init__(self) -> None:
        if not 0 <= self.confidence <= 1:
            raise ValueError(f"confidence must be between 0 and 1, got {self.confidence}")

    def replacing(self, first: Judgement[ValueT]) -> Judgement[ValueT]:
        """This answer in place of `first`, which was not confident enough."""
        return replace(self, cost=first.cost + self.cost, escalated_from=first)


def check_levels(levels: Sequence[Level]) -> None:
    """A rubric needs at least two levels, in rising order of score."""
    if len(levels) < 2:
        raise ValueError("a rubric needs at least two levels")
    scores = [level.score for level in levels]
    if scores != sorted(set(scores)):
        raise ValueError(f"rubric levels must rise strictly, got scores {scores}")


def check_options(options: Sequence[str]) -> None:
    """A choice needs at least two distinct, non-blank options."""
    if len(options) < 2:
        raise ValueError("a choice needs at least two options")
    if len(set(options)) != len(options):
        raise ValueError(f"choice options must be distinct, got {list(options)}")
    if any(not option.strip() for option in options):
        raise ValueError("choice options cannot be blank")


@runtime_checkable
class Scorer(Protocol):
    """Each call raises RetryableCallError or FatalCallError when it has no answer."""

    async def score(self, subject: str, question: str, levels: Sequence[Level]) -> Judgement[float]:
        """Rate the subject on a rubric. The value is between 0 and 1, not limited to
        the levels' scores."""
        ...

    async def choose(self, subject: str, question: str, options: Sequence[str]) -> Judgement[str]:
        """Pick one of the options for the subject. The value is always one of them."""
        ...

    async def yes_no(self, subject: str, question: str) -> Judgement[bool]:
        """Answer a yes/no question about the subject."""
        ...
