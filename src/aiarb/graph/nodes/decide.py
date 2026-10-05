from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, NotRequired, TypedDict

from langgraph.types import interrupt

from aiarb.domain.ledger import Actor, Status
from aiarb.graph import prompts
from aiarb.graph.nodes._calls import in_status

if TYPE_CHECKING:
    from aiarb.domain.ledger import Objection
    from aiarb.graph.state import RunState, StateUpdate


class Question(TypedDict):
    id: str
    claim: str
    detail: str
    """The objection and the debate over it, as the planner and critic saw it."""


class DecisionRequest(TypedDict):
    """What the run passes out when it pauses for the user's decisions."""

    questions: list[Question]
    problem: NotRequired[str]
    """Why the last answers were refused, when they were."""


ANSWERS_SCHEMA = {
    "type": "object",
    "description": "A decision for each question, by objection id.",
    "additionalProperties": {"type": "string", "minLength": 1},
}


def decide(state: RunState) -> StateUpdate:
    """Pause the run until the user decides each trade-off left after the debate.

    The run resumes with a decision per objection id. Answers that miss a
    question, or answer one that was not asked, are refused and the questions
    asked again, so the run stays paused until it has a full set.
    """
    waiting = in_status(state, Status.AWAITING_DECISION)
    if not waiting:
        return {}
    request: DecisionRequest = {"questions": [_question(item) for item in waiting]}
    while True:
        answers = interrupt(request, response_schema=ANSWERS_SCHEMA)
        problem = _problem(answers, waiting)
        if problem is None:
            break
        request = {**request, "problem": problem}
    round = state["round"]
    return {"ledger": {item.id: decided(item, answers[item.id], round) for item in waiting}}


def decided(item: Objection, answer: str, round: int) -> Objection:
    """The trade-off, decided by the user."""
    return item.transition(
        Status.DECIDED, actor=Actor.HUMAN, round=round, note=f"Decided: {answer.strip()}"
    )


def _question(item: Objection) -> Question:
    return {"id": item.id, "claim": item.claim, "detail": prompts.describe(item)}


def _problem(answers: Any, waiting: list[Objection]) -> str | None:
    if not isinstance(answers, Mapping):
        return "answer with a decision for each objection id"
    asked = {item.id for item in waiting}
    problems = [
        f"{label}: {', '.join(sorted(ids))}"
        for label, ids in (
            ("unanswered", asked - set(answers)),
            ("not asked", set(answers) - asked),
            ("blank", {id for id in asked & set(answers) if not _text(answers[id])}),
        )
        if ids
    ]
    return "; ".join(problems) or None


def _text(answer: Any) -> bool:
    return isinstance(answer, str) and bool(answer.strip())
