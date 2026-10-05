"""The shape of each role's answer.

These are the schemas agents must answer in. Each one checks its content against
the domain rules as it validates, so an answer that would break them (a path
outside the repo, an acceptance that cites nothing) is a bad answer, which can be
retried, rather than an error inside a node.

Minimal for now: enough for the graph to run on fakes. The prompts and schemas
are completed in section 9.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from aiarb.domain.ledger import CodeRef, ObjectionType, PlanRef, Ref, Resolution, ResolutionKind
from aiarb.domain.scoring import Severity

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class _Output(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CodeCitation(_Output):
    kind: Literal["code"]
    path: str
    start_line: int | None = None
    end_line: int | None = None

    @model_validator(mode="after")
    def _valid_ref(self) -> Self:
        self.to_ref()
        return self

    def to_ref(self) -> CodeRef:
        return CodeRef(self.path, self.start_line, self.end_line)


class PlanCitation(_Output):
    kind: Literal["plan"]
    section: str
    quote: str | None = None

    @model_validator(mode="after")
    def _valid_ref(self) -> Self:
        self.to_ref()
        return self

    def to_ref(self) -> PlanRef:
        return PlanRef(self.section, self.quote)


Citation = Annotated[CodeCitation | PlanCitation, Field(discriminator="kind")]


def refs(citations: list[Citation]) -> tuple[Ref, ...]:
    return tuple(citation.to_ref() for citation in citations)


class PlanDraft(_Output):
    """The planner's first plan."""

    plan: Text
    """The whole plan in Markdown, one heading per section."""


class RaisedObjection(_Output):
    claim: Text
    type: ObjectionType
    severity: Severity
    """The critic's own estimate; triage scores it independently."""
    evidence: list[Citation] = Field(min_length=1)


class Critique(_Output):
    """The critic's objections to the plan."""

    objections: list[RaisedObjection]


class Answer(_Output):
    id: Text
    response: Literal["fix", "rebuttal"]
    note: Text
    """What changed in the plan, or why the objection does not hold."""
    citations: list[Citation] = Field(default_factory=list)


class Responses(_Output):
    """The planner's answer to every open objection, with the revised plan."""

    plan: Text
    """The whole revised plan, not just the changes."""
    answers: list[Answer]


class ResolutionOutput(_Output):
    kind: ResolutionKind
    citations: list[Citation] = Field(min_length=1)

    @model_validator(mode="after")
    def _valid_resolution(self) -> Self:
        self.to_resolution()
        return self

    def to_resolution(self) -> Resolution:
        return Resolution(self.kind, refs(self.citations))


class Ruling(_Output):
    id: Text
    verdict: Literal["accept", "reject", "dispute"]
    """Accept the answer, reject it, or send a factual disagreement to the verifier."""
    note: Text
    resolution: ResolutionOutput | None = None
    """Required to accept: the plan change or evidence that settles it."""

    @model_validator(mode="after")
    def _resolution_only_to_accept(self) -> Self:
        if (self.verdict == "accept") != (self.resolution is not None):
            raise ValueError("an accepted answer needs a resolution, and only an accepted one")
        return self


class Rulings(_Output):
    """The critic's ruling on each answered objection."""

    rulings: list[Ruling]


class Verdict(_Output):
    """The verifier's finding on one disputed fact, from reading the repo."""

    finding: Literal["planner_right", "critic_right", "unverifiable"]
    note: Text
    citations: list[Citation] = Field(default_factory=list)

    @model_validator(mode="after")
    def _evidence_to_side_with_the_planner(self) -> Self:
        if self.finding == "planner_right" and not self.citations:
            raise ValueError("siding with the planner closes the objection: cite the evidence")
        return self


class JudgeRuling(_Output):
    """The judge's decision on one deadlocked design objection."""

    favours: Literal["planner", "critic"]
    ruling: Text
    """What should happen, and why."""
