from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal, NotRequired, TypedDict

from langgraph.types import interrupt

if TYPE_CHECKING:
    from aiarb.graph.state import Clarification, PlannerQuestion, RunState, StateUpdate


class ClarifyRequest(TypedDict):
    """What the run passes out when it pauses for answers to the planner's questions."""

    kind: Literal["clarify"]
    questions: list[PlannerQuestion]
    problem: NotRequired[str]
    """Why the last answers were refused, when they were."""


ANSWERS_SCHEMA = {
    "type": "object",
    "description": "An answer for each question, by question id. A blank answer leaves "
    "the question to the planner.",
    "additionalProperties": {"type": "string"},
}


def clarify(state: RunState) -> StateUpdate:
    """Pause the run until the user answers the planner's questions.

    The run resumes with an answer per question id; a blank one leaves the
    question to the planner. Answers that miss a question, or answer one that was
    not asked, are refused and the questions asked again.
    """
    waiting = state.get("questions", [])
    if not waiting:
        return {}
    request: ClarifyRequest = {"kind": "clarify", "questions": waiting}
    while True:
        answers = interrupt(request, response_schema=ANSWERS_SCHEMA)
        problem = _problem(answers, waiting)
        if problem is None:
            break
        request = {**request, "problem": problem}
    answered: list[Clarification] = [
        {**question, "answer": answers[question["id"]].strip()} for question in waiting
    ]
    return {"clarifications": [*state.get("clarifications", []), *answered], "questions": []}


def _problem(answers: Any, waiting: list[PlannerQuestion]) -> str | None:
    if not isinstance(answers, Mapping):
        return "answer with a mapping from each question id to an answer"
    asked = {question["id"] for question in waiting}
    problems = [
        f"{label}: {', '.join(sorted(ids))}"
        for label, ids in (
            ("unanswered", asked - set(answers)),
            ("not asked", set(answers) - asked),
            ("not text", {id for id in asked & set(answers) if not isinstance(answers[id], str)}),
        )
        if ids
    ]
    return "; ".join(problems) or None
