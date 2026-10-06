"""The answer schemas, checked two ways.

Claude Code checks an answer against the JSON schema it is given, and the agent
fixes a refused answer in the same session. Pydantic then reads the answer, and
an answer it refuses costs a whole call to retry. So each rule is checked
against the JSON schema as Claude Code receives it, and against pydantic, and
the few rules only pydantic can check are listed as such.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ValidationError

from aiarb.adapters.claude_code import answer_schema
from aiarb.domain.ledger import CodeRef, PlanRef, Resolution, ResolutionKind
from aiarb.schemas import outputs
from aiarb.schemas.outputs import (
    Accept,
    Critique,
    Explanation,
    Fix,
    JudgeRuling,
    Opening,
    PlanDraft,
    Rating,
    Rebuttal,
    Responses,
    Rulings,
    Verdict,
)

CODE = {"kind": "code", "path": "src/queue.py", "start_line": 10, "end_line": 12}
PLAN = {"kind": "plan", "section": "Queue storage", "quote": "Keep places in Redis."}
FIX = {"id": "OBJ-1", "response": "fix", "note": "Moved to Postgres.", "changed": [PLAN]}
REBUTTAL = {"id": "OBJ-2", "response": "rebuttal", "note": "Already retried.", "evidence": [CODE]}
OPTIONS = [
    {"label": "Per tenant", "description": "Each tenant gets its own queue.", "recommended": True},
    {"label": "Global", "description": "One queue for everyone."},
]
QUESTION = {
    "question": "Is the queue per tenant or global?",
    "why": "It decides where the queue's state is kept.",
    "options": OPTIONS,
}
ACCEPT = {
    "id": "OBJ-1",
    "verdict": "accept",
    "note": "Postgres keeps places.",
    "resolution": {"kind": "plan_change", "sections": [PLAN], "code": [CODE]},
}

ANSWER_SCHEMAS = [
    model
    for _, model in inspect.getmembers(outputs, inspect.isclass)
    if issubclass(model, outputs._Output) and model is not outputs._Output
]

VALID: list[tuple[type[BaseModel], dict[str, Any]]] = [
    (PlanDraft, {"plan": "# Waiting room\n\nAdmit users in batches."}),
    (PlanDraft, {"plan": "Intro line.\n\n## Approach\n\nBatches."}),
    (Opening, {"answer": {"kind": "questions", "questions": [QUESTION]}}),
    (Opening, {"answer": {"kind": "plan", "plan": "# Waiting room\n\nBatches."}}),
    (
        Critique,
        {
            "objections": [
                {
                    "claim": "Redis loses places on failover.",
                    "type": "design",
                    "severity": "major",
                    "evidence": [PLAN, CODE],
                }
            ]
        },
    ),
    (Critique, {"objections": []}),
    (Responses, {"plan": "# Queue storage\n\nPostgres.", "answers": [FIX, REBUTTAL]}),
    (
        Rulings,
        {
            "rulings": [
                ACCEPT,
                {
                    "id": "OBJ-2",
                    "verdict": "accept",
                    "note": "The retry is there.",
                    "resolution": {"kind": "evidence", "citations": [CODE]},
                },
                {"id": "OBJ-3", "verdict": "reject", "note": "Still loses places."},
                {"id": "OBJ-4", "verdict": "dispute", "note": "Is AOF on in production?"},
            ]
        },
    ),
    (Verdict, {"result": {"finding": "planner_right", "note": "AOF is on.", "evidence": [CODE]}}),
    (Verdict, {"result": {"finding": "critic_right", "note": "AOF is off.", "evidence": [CODE]}}),
    (
        Verdict,
        {"result": {"finding": "unverifiable", "note": "Set at deploy.", "task": "Check prod."}},
    ),
    (JudgeRuling, {"favours": "planner", "ruling": "Keep Redis.", "reason": "Cost matters."}),
    (
        Explanation,
        {
            "summary": "The plan moved queue places to Postgres.",
            "items": [
                {
                    "id": "OBJ-1",
                    "title": "Places lost on failover",
                    "problem": "Redis kept places only in memory.",
                    "consequence": "A failover mid-sale would empty the queue.",
                    "outcome": "Places are now kept in Postgres.",
                }
            ],
        },
    ),
    (Rating, {"reason": "A degraded path.", "score": 0.4, "confidence": 0.8}),
    (outputs.Choice, {"reason": "Fits best.", "choice": "design", "confidence": 1}),
    (outputs.YesNo, {"reason": "Nothing is cited.", "answer": False, "confidence": 0}),
]


def schema_accepts(model: type[BaseModel], data: dict[str, Any]) -> bool:
    """Whether the JSON schema Claude Code is given accepts the answer."""
    return Draft202012Validator(answer_schema(model)).is_valid(data)


def pydantic_accepts(model: type[BaseModel], data: dict[str, Any]) -> bool:
    try:
        model.model_validate(data)
    except ValidationError:
        return False
    return True


def test_every_answer_schema_is_a_valid_json_schema() -> None:
    for model in ANSWER_SCHEMAS:
        Draft202012Validator.check_schema(answer_schema(model))


@pytest.mark.parametrize(("model", "data"), VALID, ids=lambda value: getattr(value, "__name__", ""))
def test_good_answers_pass_both_checks(model: type[BaseModel], data: dict[str, Any]) -> None:
    assert schema_accepts(model, data)
    assert pydantic_accepts(model, data)


def without(data: dict[str, Any], key: str) -> dict[str, Any]:
    return {name: value for name, value in data.items() if name != key}


def responses(*answers: dict[str, Any]) -> dict[str, Any]:
    return {"plan": "# Queue storage\n\nPostgres.", "answers": list(answers)}


def opening(kind: str = "questions", **answer: Any) -> dict[str, Any]:
    return {"answer": {"kind": kind, **answer}}


def rulings(*items: dict[str, Any]) -> dict[str, Any]:
    return {"rulings": list(items)}


def verdict(**result: Any) -> dict[str, Any]:
    return {"result": result}


REFUSED_BY_THE_SCHEMA: list[tuple[str, type[BaseModel], dict[str, Any]]] = [
    ("a plan with no headings", PlanDraft, {"plan": "Admit users in batches."}),
    ("a revised plan with no headings", Responses, {"plan": "Postgres.", "answers": [FIX]}),
    ("a blank plan", PlanDraft, {"plan": ""}),
    ("an opening that asks nothing", Opening, opening(questions=[])),
    ("more than four questions", Opening, opening(questions=[QUESTION] * 5)),
    (
        "a question with one option",
        Opening,
        opening(questions=[{**QUESTION, "options": OPTIONS[:1]}]),
    ),
    ("a question with no reason", Opening, opening(questions=[without(QUESTION, "why")])),
    ("an opening plan with no headings", Opening, opening("plan", plan="Batches.")),
    ("an opening of an unknown kind", Opening, opening("maybe", plan="# Plan")),
    (
        "an objection that cites nothing",
        Critique,
        {
            "objections": [
                {"claim": "Too slow.", "type": "design", "severity": "major", "evidence": []}
            ]
        },
    ),
    (
        "an objection of an unknown type",
        Critique,
        {
            "objections": [
                {"claim": "Too slow.", "type": "style", "severity": "major", "evidence": [CODE]}
            ]
        },
    ),
    ("a fix that names no changed section", Responses, responses(without(FIX, "changed"))),
    ("a fix that cites code as the change", Responses, responses({**FIX, "changed": [CODE]})),
    ("a rebuttal with no evidence", Responses, responses({**REBUTTAL, "evidence": []})),
    ("an answer that agrees", Responses, responses({**FIX, "response": "agree"})),
    ("an answer to a made-up id", Responses, responses({**FIX, "id": "objection 1"})),
    ("an acceptance with no resolution", Rulings, rulings(without(ACCEPT, "resolution"))),
    (
        "a plan change that cites only code",
        Rulings,
        rulings({**ACCEPT, "resolution": {"kind": "plan_change", "sections": [CODE]}}),
    ),
    (
        "evidence that cites nothing",
        Rulings,
        rulings({**ACCEPT, "resolution": {"kind": "evidence", "citations": []}}),
    ),
    (
        "a rejection that resolves",
        Rulings,
        rulings({**ACCEPT, "verdict": "reject"}),
    ),
    (
        "siding with the planner without evidence",
        Verdict,
        verdict(finding="planner_right", note="Looks fine.", evidence=[]),
    ),
    (
        "siding with the critic without evidence",
        Verdict,
        verdict(finding="critic_right", note="Looks wrong."),
    ),
    (
        "an unverifiable fact with no task",
        Verdict,
        verdict(finding="unverifiable", note="Set at deploy."),
    ),
    (
        "a ruling with no reason",
        JudgeRuling,
        {"favours": "critic", "ruling": "Use Postgres."},
    ),
    (
        "a line number of 0",
        Verdict,
        verdict(finding="critic_right", note="x", evidence=[{**CODE, "start_line": 0}]),
    ),
    ("a score above 1", Rating, {"reason": "Bad.", "score": 1.5, "confidence": 0.5}),
    ("a negative confidence", Rating, {"reason": "Bad.", "score": 0.5, "confidence": -0.1}),
    ("an extra field", JudgeRuling, {"favours": "critic", "ruling": "x", "reason": "y", "z": 1}),
]


@pytest.mark.parametrize(
    ("model", "data"),
    [pytest.param(model, data, id=name) for name, model, data in REFUSED_BY_THE_SCHEMA],
)
def test_bad_answers_are_refused_by_the_schema_claude_code_checks(
    model: type[BaseModel], data: dict[str, Any]
) -> None:
    assert not schema_accepts(model, data)
    assert not pydantic_accepts(model, data)


def critic_right(citation: dict[str, Any]) -> dict[str, Any]:
    return verdict(finding="critic_right", note="x", evidence=[citation])


@pytest.mark.parametrize(
    ("data", "problem"),
    [
        pytest.param(
            critic_right({"kind": "code", "path": "/etc/passwd"}),
            "must be a path inside the repo",
            id="absolute-path",
        ),
        pytest.param(
            critic_right({"kind": "code", "path": "../secrets.env"}),
            "must be a path inside the repo",
            id="path-out-of-the-repo",
        ),
        pytest.param(
            critic_right({**CODE, "start_line": 12, "end_line": 10}),
            "is before start line",
            id="end-before-start",
        ),
        pytest.param(
            critic_right({"kind": "code", "path": "src/queue.py", "end_line": 3}),
            "needs a start line",
            id="end-without-start",
        ),
    ],
)
def test_rules_only_pydantic_checks(data: dict[str, Any], problem: str) -> None:
    """JSON Schema cannot express these, so a break costs a retried call. They are
    the domain's own rules for a citation."""
    assert schema_accepts(Verdict, data)
    with pytest.raises(ValidationError, match=problem):
        Verdict.model_validate(data)


def test_every_field_is_described_for_the_agent() -> None:
    undescribed = [
        f"{name}.{field}"
        for model in ANSWER_SCHEMAS
        for name, definition in [
            (model.__name__, answer_schema(model)),
            *answer_schema(model).get("$defs", {}).items(),
        ]
        for field, spec in definition.get("properties", {}).items()
        if "description" not in spec and "const" not in spec
    ]

    assert undescribed == []


class TestReadingAnswers:
    def test_a_fix_cites_the_changed_sections_then_its_evidence(self) -> None:
        fix = Fix.model_validate({**FIX, "evidence": [CODE]})

        assert fix.cited == (
            PlanRef("Queue storage", "Keep places in Redis."),
            CodeRef("src/queue.py", 10, 12),
        )

    def test_a_rebuttal_cites_its_evidence(self) -> None:
        assert Rebuttal.model_validate(REBUTTAL).cited == (CodeRef("src/queue.py", 10, 12),)

    def test_an_acceptance_becomes_a_resolution(self) -> None:
        accept = Accept.model_validate(ACCEPT)

        assert accept.resolution.to_resolution() == Resolution(
            ResolutionKind.PLAN_CHANGE,
            (PlanRef("Queue storage", "Keep places in Redis."), CodeRef("src/queue.py", 10, 12)),
        )

    def test_text_is_trimmed(self) -> None:
        data = {"plan": "\n  # Queue\n\nBatches.  \n"}

        assert schema_accepts(PlanDraft, data)
        assert PlanDraft.model_validate(data).plan == "# Queue\n\nBatches."
