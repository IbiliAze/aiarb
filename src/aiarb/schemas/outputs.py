"""The shape of each role's answer.

These are the JSON schemas agents answer in. Field and class docstrings become
the schema's descriptions, which is what an agent reads about each field, so
they are written for the agent.

Every rule that JSON Schema can express is written into the schema itself: a
required citation is a minimum length, a field that only some answers need is a
branch of a union. Claude Code checks an answer against the schema before
accepting it, so an agent that breaks one of these rules fixes its answer in the
same session. The few rules JSON Schema cannot express, such as a citation's end
line coming after its start, are checked by pydantic as the answer is read. An
answer that breaks one of those is a bad answer, retried as a whole call, so
they are kept to what the domain cannot do without.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from aiarb.domain.ledger import (
    CodeRef,
    ObjectionType,
    PlanRef,
    Ref,
    Resolution,
    ResolutionKind,
)
from aiarb.domain.scoring import Severity

if TYPE_CHECKING:
    from collections.abc import Iterable

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

ObjectionId = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^OBJ-[0-9]+$")]

PlanText = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"(^|\n) {0,3}#{1,6} \S")
]
"""A plan in Markdown, with at least one heading: objections cite sections by heading."""

Confidence = Annotated[float, Field(ge=0, le=1)]


class _Output(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)


# Citations


class CodeCitation(_Output):
    """A file in the repo, optionally narrowed to a range of lines."""

    kind: Literal["code"]
    path: Text
    """Relative to the root of the repo, such as `src/app/queue.py`."""
    start_line: int | None = Field(default=None, ge=1)
    """The first line meant, counting from 1. Leave out to mean the whole file."""
    end_line: int | None = Field(default=None, ge=1)
    """The last line meant, when it is more than one. Needs a start line."""

    @model_validator(mode="after")
    def _valid_ref(self) -> Self:
        self.to_ref()
        return self

    def to_ref(self) -> CodeRef:
        return CodeRef(self.path, self.start_line, self.end_line)


class PlanCitation(_Output):
    """A section of the plan, optionally with the line meant."""

    kind: Literal["plan"]
    section: Text
    """The section's heading as written in the plan, without the leading #s."""
    quote: Text | None = None
    """A short quote of the line meant, when it is one line. A quote still finds the
    line after the plan is revised, where a line number would not."""

    @model_validator(mode="after")
    def _valid_ref(self) -> Self:
        self.to_ref()
        return self

    def to_ref(self) -> PlanRef:
        return PlanRef(self.section, self.quote)


Citation = Annotated[CodeCitation | PlanCitation, Field(discriminator="kind")]


def refs(citations: Iterable[CodeCitation | PlanCitation]) -> tuple[Ref, ...]:
    return tuple(citation.to_ref() for citation in citations)


# Planner


class PlanDraft(_Output):
    """The first plan for the feature."""

    plan: PlanText
    """The whole plan in Markdown, with a heading for each section."""


class QuestionOption(_Output):
    """One answer the user can pick."""

    label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    """The answer in a few words, such as `Per tenant`."""
    description: Text
    """What choosing it would mean for the plan."""
    recommended: bool = False
    """True for the one option you would choose. Mark at most one."""


class PlanQuestion(_Output):
    """A question for the user, whose answer would change the plan."""

    question: Text
    """The question, asked plainly and ending with a question mark."""
    why: Text
    """What in the plan turns on the answer, and why the code cannot settle it."""
    options: list[QuestionOption] = Field(min_length=2, max_length=4)
    """The answers you see as reasonable. The user can also give their own."""


class AskFirst(_Output):
    """Questions for the user, asked before you draft the plan. You draft it once
    they answer."""

    kind: Literal["questions"]
    questions: list[PlanQuestion] = Field(min_length=1, max_length=4)
    """Only what the repo cannot tell you and the plan depends on. Ask the
    questions whose answers matter most."""


class DraftNow(_Output):
    """The first plan for the feature, drafted without asking anything."""

    kind: Literal["plan"]
    plan: PlanText
    """The whole plan in Markdown, with a heading for each section."""


class Opening(_Output):
    """Your first turn: questions for the user, or the plan itself when nothing
    the plan depends on is unclear."""

    answer: Annotated[AskFirst | DraftNow, Field(discriminator="kind")]
    """Questions, when the answers would change the plan and the repo cannot give
    them; otherwise the plan."""


class Fix(_Output):
    """The plan was changed to deal with the objection."""

    id: ObjectionId
    """The objection's id, such as OBJ-3."""
    response: Literal["fix"]
    note: Text
    """What changed in the plan, and how that deals with the objection."""
    changed: list[PlanCitation] = Field(min_length=1)
    """The sections of the revised plan that changed."""
    evidence: list[Citation] = Field(default_factory=list)
    """Any code that shows the fix will work."""

    @property
    def cited(self) -> tuple[Ref, ...]:
        return refs([*self.changed, *self.evidence])


class Rebuttal(_Output):
    """The objection does not hold, so the plan stays as it is."""

    id: ObjectionId
    """The objection's id, such as OBJ-3."""
    response: Literal["rebuttal"]
    note: Text
    """Why the objection does not hold."""
    evidence: list[Citation] = Field(min_length=1)
    """The code or plan lines that show it. Saying so is not enough."""

    @property
    def cited(self) -> tuple[Ref, ...]:
        return refs(self.evidence)


Answer = Annotated[Fix | Rebuttal, Field(discriminator="response")]


class Responses(_Output):
    """The answer to every open objection, with the revised plan."""

    plan: PlanText
    """The whole revised plan in Markdown, not just the changes."""
    answers: list[Answer]
    """One answer for each objection, by id. Agreeing without changing the plan is
    not an answer: fix the plan, or rebut the objection with evidence."""


# Critic


class RaisedObjection(_Output):
    """One thing wrong with the plan."""

    claim: Text
    """What is wrong, in a sentence or two, stated so it can be checked or argued."""
    type: ObjectionType
    """`factual`: a claim about what the code does or how the system behaves, which
    reading the code could settle. `design`: a flaw in the plan that has a right
    answer. `trade_off`: a choice between legitimate priorities, which only the user
    can make."""
    severity: Severity
    """Your estimate of the impact if the objection is right. It is scored again
    independently."""
    evidence: list[Citation] = Field(min_length=1)
    """The code and plan lines the objection concerns."""


class Critique(_Output):
    """The objections to the plan."""

    objections: list[RaisedObjection]
    """Every objection, most severe first. Empty if nothing is wrong."""


class PlanChangeResolution(_Output):
    """The revised plan deals with the objection."""

    kind: Literal["plan_change"]
    sections: list[PlanCitation] = Field(min_length=1)
    """The plan sections whose change settles it."""
    code: list[CodeCitation] = Field(default_factory=list)
    """Any code that shows the change will work."""

    def to_resolution(self) -> Resolution:
        return Resolution(ResolutionKind.PLAN_CHANGE, refs([*self.sections, *self.code]))


class EvidenceResolution(_Output):
    """Evidence shows the objection does not hold, or is already handled."""

    kind: Literal["evidence"]
    citations: list[Citation] = Field(min_length=1)
    """The code or plan lines that show it."""

    def to_resolution(self) -> Resolution:
        return Resolution(ResolutionKind.EVIDENCE, refs(self.citations))


ResolutionOutput = Annotated[PlanChangeResolution | EvidenceResolution, Field(discriminator="kind")]


class Accept(_Output):
    """The answer settles the objection, which closes it."""

    id: ObjectionId
    """The objection's id, such as OBJ-3."""
    verdict: Literal["accept"]
    note: Text
    """Why it is settled."""
    resolution: ResolutionOutput
    """What settles it: a plan change or cited evidence. Agreement alone does not."""


class Reject(_Output):
    """The answer does not settle the objection, which goes back to the planner."""

    id: ObjectionId
    """The objection's id, such as OBJ-3."""
    verdict: Literal["reject"]
    note: Text
    """What is still wrong, so the planner can answer it next round."""


class Dispute(_Output):
    """The answer rests on a fact about the repo that you dispute. A verifier reads
    the code to settle it."""

    id: ObjectionId
    """The objection's id, such as OBJ-3."""
    verdict: Literal["dispute"]
    note: Text
    """The fact in dispute, as a question that reading the code can answer."""


Ruling = Annotated[Accept | Reject | Dispute, Field(discriminator="verdict")]


class Rulings(_Output):
    """The ruling on each answered objection."""

    rulings: list[Ruling]
    """One ruling for each objection, by id."""


# Verifier


class PlannerRight(_Output):
    """The code shows the planner is right, which closes the objection."""

    finding: Literal["planner_right"]
    note: Text
    """What the code shows."""
    evidence: list[Citation] = Field(min_length=1)
    """Where it shows it."""


class CriticRight(_Output):
    """The code shows the critic is right, so the objection goes back to the planner."""

    finding: Literal["critic_right"]
    note: Text
    """What the code shows."""
    evidence: list[Citation] = Field(min_length=1)
    """Where it shows it."""


class Unverifiable(_Output):
    """Reading the code cannot settle it, as with how a service behaves under load."""

    finding: Literal["unverifiable"]
    note: Text
    """Why the code cannot settle it."""
    task: Text
    """What to run or check before building, to settle it: a load test, a query
    against production data, a question for the team that owns a service."""
    evidence: list[Citation] = Field(default_factory=list)
    """What you read on the way."""


Finding = Annotated[PlannerRight | CriticRight | Unverifiable, Field(discriminator="finding")]


class Verdict(_Output):
    """The finding on one disputed fact, from reading the repo."""

    result: Finding
    """Who the code shows is right, or that it cannot settle the question."""


# Judge


class JudgeRuling(_Output):
    """The decision on one design objection the debate could not settle."""

    favours: Literal["planner", "critic"]
    """The side that is right."""
    ruling: Text
    """What the plan should do."""
    reason: Text
    """Why, weighing both sides' arguments against the charter's priorities."""
    evidence: list[Citation] = Field(default_factory=list)
    """The code or plan lines the decision rests on."""


# Explainer


class ExplainedItem(_Output):
    """One objection, for a reader who did not follow the debate."""

    id: ObjectionId
    """The objection's id, such as OBJ-3."""
    title: Text
    """A short headline, under ten words."""
    problem: Text
    """What is wrong, in plain English, with no jargon the reader would have to look up."""
    consequence: Text
    """What would have happened in production if the plan had been built as first
    written."""
    outcome: Text
    """How it was settled. If it is still open, what the reader should do about it."""


class Explanation(_Output):
    """The ledger in plain English."""

    summary: Text
    """A few sentences on the run: what was planned, what the debate changed, and
    what is still open."""
    items: list[ExplainedItem]
    """One for each objection, by id, in the order given."""


# Scorer


class Rating(_Output):
    """A rating on the rubric given."""

    reason: Text
    """The reasoning behind the rating, in a sentence or two."""
    score: float = Field(ge=0, le=1)
    """From 0 to 1. Any value, not only the scores the rubric's levels start at."""
    confidence: Confidence
    """How sure you are, from 0 (a guess) to 1 (certain)."""


class Choice(_Output):
    """One of the options given."""

    reason: Text
    """The reasoning behind the choice, in a sentence or two."""
    choice: Text
    """Exactly one of the options, written as given."""
    confidence: Confidence
    """How sure you are, from 0 (a guess) to 1 (certain)."""


class YesNo(_Output):
    """An answer to a yes-or-no question."""

    reason: Text
    """The reasoning behind the answer, in a sentence or two."""
    answer: bool
    """True for yes, false for no."""
    confidence: Confidence
    """How sure you are, from 0 (a guess) to 1 (certain)."""
