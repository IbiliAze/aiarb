from __future__ import annotations

from typing import TYPE_CHECKING

from aiarb.graph import prompts
from aiarb.graph.nodes._calls import ask
from aiarb.schemas.outputs import AskFirst, Opening, PlanDraft

if TYPE_CHECKING:
    from collections.abc import Sequence

    from langgraph.runtime import Runtime

    from aiarb.graph.context import RunContext
    from aiarb.graph.state import Clarification, PlannerQuestion, RunState, StateUpdate
    from aiarb.schemas.outputs import PlanQuestion

QUESTION_ROUNDS = 2
"""The most rounds of questions the planner may ask the user before it drafts."""


async def draft_plan(state: RunState, runtime: Runtime[RunContext]) -> StateUpdate:
    """The planner drafts the plan, unless the user brought one to critique.

    When someone can answer, the planner may ask the user questions first
    instead; the clarify step pauses for the answers and sends the run back here,
    where the planner's session carries on with them.
    """
    if state["given_plans"]:
        return {"plan": "\n\n---\n\n".join(state["given_plans"])}
    context = runtime.context
    answered = state.get("clarifications", [])
    may_ask = context.planner_may_ask and question_rounds(answered) < QUESTION_ROUNDS
    prompt = prompts.draft(state, context.charter, may_ask=may_ask)
    session = state["sessions"].get("planner") if answered else None
    if not may_ask:
        drafted = await ask(runtime, "planner", prompt, PlanDraft, session_id=session)
        plan, cost, session_id = drafted.output.plan, drafted.cost, drafted.session_id
    else:
        opened = await ask(runtime, "planner", prompt, Opening, session_id=session)
        cost, session_id = opened.cost, opened.session_id
        answer = opened.output.answer
        if isinstance(answer, AskFirst):
            return {
                "questions": _numbered(answer.questions, answered),
                "costs": {"planner": cost},
                "sessions": {"planner": session_id},
            }
        plan = answer.plan
    return {
        "plan": plan,
        "questions": [],
        "costs": {"planner": cost},
        "sessions": {"planner": session_id},
    }


def question_rounds(answered: Sequence[Clarification]) -> int:
    """How many rounds of questions the planner has asked."""
    return max((item["batch"] for item in answered), default=0)


def _numbered(
    questions: Sequence[PlanQuestion], answered: Sequence[Clarification]
) -> list[PlannerQuestion]:
    batch = question_rounds(answered) + 1
    return [
        {
            "id": f"Q-{len(answered) + number}",
            "batch": batch,
            "question": question.question,
            "why": question.why,
            "options": [
                {
                    "label": option.label,
                    "description": option.description,
                    "recommended": option.recommended,
                }
                for option in question.options
            ],
        }
        for number, question in enumerate(questions, start=1)
    ]
