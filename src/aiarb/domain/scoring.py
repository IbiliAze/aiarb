"""How much an objection matters: the severity levels, what each one means, and
how an impact score maps onto them.

Severity measures impact only. It never says whether an objection is right;
the debate settles that.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class Severity(StrEnum):
    MINOR = "minor"
    MODERATE = "moderate"
    MAJOR = "major"
    BLOCKER = "blocker"

    @property
    def rank(self) -> int:
        """Position from least to most severe, for ordering."""
        return _RANK[self]

    @property
    def skips_debate(self) -> bool:
        """Minor objections go to the planner as notes instead of being debated."""
        return self is Severity.MINOR


_RANK = {severity: rank for rank, severity in enumerate(Severity)}

# Worded as consequences in production, since that is how the ledger explains
# each objection. Scorers are given these as the levels to choose between.
RUBRIC: Mapping[Severity, str] = MappingProxyType(
    {
        Severity.MINOR: (
            "Polish, clarity or a small inefficiency. Building the plan as written "
            "works; fixing this later costs little."
        ),
        Severity.MODERATE: (
            "A real defect with a contained effect: a degraded path, extra running "
            "cost, or rework later. A workaround exists and nobody is harmed meanwhile."
        ),
        Severity.MAJOR: (
            "Users or operators would notice: an outage under foreseeable load, data "
            "loss in an edge case, a security gap, or a breach of a charter priority "
            "or constraint. It must be fixed before release."
        ),
        Severity.BLOCKER: (
            "The plan cannot work as written, or building it would cause serious "
            "harm: routine data loss or corruption, a broken core flow, an exploitable "
            "hole, or a change that cannot be safely deployed or rolled back."
        ),
    }
)


@dataclass(frozen=True)
class SeverityThresholds:
    """The lowest impact score, from 0 to 1, at which each severity begins.

    Scores below `moderate` are minor.
    """

    moderate: float = 0.25
    major: float = 0.5
    blocker: float = 0.8

    def __post_init__(self) -> None:
        if not 0 < self.moderate < self.major < self.blocker <= 1:
            raise ValueError(
                "severity thresholds must rise strictly within (0, 1]; got "
                f"moderate={self.moderate}, major={self.major}, blocker={self.blocker}"
            )

    def classify(self, score: float) -> Severity:
        if not 0 <= score <= 1:
            raise ValueError(f"impact score must be between 0 and 1, got {score}")
        if score >= self.blocker:
            return Severity.BLOCKER
        if score >= self.major:
            return Severity.MAJOR
        if score >= self.moderate:
            return Severity.MODERATE
        return Severity.MINOR
