from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import pytest

from aiarb.config.charter import Charter
from aiarb.domain.ledger import Actor, ObjectionType, Status
from aiarb.domain.scoring import RUBRIC, Severity
from aiarb.graph import prompts
from aiarb.graph.state import initial_state
from tests.graph.test_nodes import objection

if TYPE_CHECKING:
    from aiarb.domain.ledger import Objection
    from aiarb.graph.state import Role, RunState

CHARTER = Charter.model_validate(
    {
        "priorities": {"running_cost": 2, "data_integrity": 5},
        "constraints": ["no new managed services"],
    }
)
ROLES: list[Role] = ["planner", "critic", "verifier", "judge", "explainer", "scorer"]


def state(*items: Objection, round: int = 0, **fields: Any) -> RunState:
    base = initial_state(feature="Hold a ticket for ten minutes while the buyer pays.")
    changes = {
        "ledger": {item.id: item for item in items},
        "round": round,
        "plan": "## Summary\n\nHold seats in Postgres.",
        "facts": "# Repo facts\n\n- Frameworks: FastAPI",
        **fields,
    }
    base.update(changes)  # type: ignore[typeddict-item]
    return base


def tags(prompt: str) -> list[str]:
    return re.findall(r"^<([a-z_]+)>$", prompt, re.MULTILINE)


def disputed() -> Objection:
    return (
        objection("OBJ-2", type=ObjectionType.FACTUAL)
        .transition(
            Status.ANSWERED,
            actor=Actor.PLANNER,
            round=1,
            note="Rebuttal: Redis is used for sessions.",
        )
        .transition(
            Status.DISPUTED, actor=Actor.CRITIC, round=1, note="Does any code connect to Redis?"
        )
    )


class TestBriefs:
    @pytest.mark.parametrize("role", ROLES)
    def test_each_role_has_one(self, role: Role) -> None:
        brief = prompts.brief(role)

        assert brief.startswith(f"# You are the {role}\n")
        assert len(brief) > 400

    @pytest.mark.parametrize(
        ("prompt", "role"),
        [
            (lambda: prompts.draft(state(), CHARTER), "planner"),
            (lambda: prompts.critique(state(), CHARTER), "critic"),
            (lambda: prompts.respond(state(), [objection("OBJ-1")], [], CHARTER), "planner"),
            (lambda: prompts.rule(state(round=1), [objection("OBJ-1")], CHARTER), "critic"),
            (lambda: prompts.verify(state(), disputed()), "verifier"),
            (lambda: prompts.judge(state(), objection("OBJ-1"), CHARTER), "judge"),
            (lambda: prompts.explain(state(objection("OBJ-1"))), "explainer"),
        ],
    )
    def test_every_prompt_starts_with_its_roles_brief_then_the_task(
        self, prompt: Any, role: Role
    ) -> None:
        text = prompt()

        assert text.startswith(prompts.brief(role))
        assert "\n\n# This turn\n\n" in text


class TestTheCharter:
    def test_priorities_come_most_important_first_then_the_constraints(self) -> None:
        text = prompts.draft(state(), CHARTER)

        assert (
            "<charter>\nPriorities, from 1 (matters a little) to 5 (matters most):\n"
            "- data integrity: 5\n- running cost: 2\n"
            "Constraints every plan must keep to:\n- no new managed services\n</charter>"
        ) in text

    def test_an_empty_charter_says_so(self) -> None:
        assert "No priorities or constraints are set" in prompts.draft(state(), Charter())


class TestThePlanner:
    def test_drafts_from_the_feature_charter_and_facts(self) -> None:
        text = prompts.draft(state(), CHARTER)

        assert tags(text) == ["feature", "charter", "repo_facts"]
        assert "<feature>\nHold a ticket for ten minutes while the buyer pays.\n</feature>" in text
        assert "- Frameworks: FastAPI" in text

    def test_without_a_feature_works_one_out_and_says_so(self) -> None:
        text = prompts.draft(state(feature=None), CHARTER)

        assert "state that as your first assumption" in text

    def test_responds_to_each_objection_with_its_history(self) -> None:
        rejected = objection("OBJ-1", status=Status.ANSWERED).transition(
            Status.OPEN, actor=Actor.CRITIC, round=1, note="Still loses holds on restart."
        )

        text = prompts.respond(state(rejected, round=1), [rejected], [], CHARTER)

        assert text.count("# This turn\n\nRound 2.") == 1
        assert tags(text) == ["plan", "objections", "charter"]
        assert "OBJ-1 (design, major, open): Claim OBJ-1." in text
        assert "Round 1, critic: Still loses holds on restart." in text
        assert "<minor_notes>" not in text

    def test_passes_minor_notes_on_when_there_are_any(self) -> None:
        note = objection("OBJ-3", severity=Severity.MINOR, status=Status.NOTED)

        text = prompts.respond(state(), [objection("OBJ-1")], [note], CHARTER)

        assert tags(text) == ["plan", "objections", "minor_notes", "charter"]
        assert "Minor notes follow the objections" in text
        assert "<minor_notes>\nOBJ-3 (design, minor, noted): Claim OBJ-3." in text


class TestTheCritic:
    def test_critiques_the_plan_against_the_charter_and_facts(self) -> None:
        text = prompts.critique(state(), CHARTER)

        assert tags(text) == ["feature", "plan", "charter", "repo_facts"]
        assert "<plan>\n## Summary\n\nHold seats in Postgres.\n</plan>" in text
        assert "outside the review" not in text

    def test_is_told_when_the_user_brought_the_plan(self) -> None:
        text = prompts.critique(state(given_plans=["Use Redis."]), CHARTER)

        assert "The user wrote this plan outside the review" in text

    def test_rules_on_the_answers_in_this_round_only(self) -> None:
        answered = objection("OBJ-1", status=Status.ANSWERED)

        text = prompts.rule(state(answered, round=1), [answered], CHARTER)

        assert "# This turn\n\nRound 1." in text
        assert "Rule only on these objections" in text
        assert "Round 1, planner: Fixed." in text


class TestTheOthers:
    def test_the_verifier_gets_the_disputed_question_on_its_own(self) -> None:
        text = prompts.verify(state(), disputed())

        assert tags(text) == ["question", "objection", "repo_facts"]
        assert "<question>\nDoes any code connect to Redis?\n</question>" in text
        assert "Round 1, planner: Rebuttal: Redis is used for sessions." in text

    def test_the_judge_gets_the_objection_plan_charter_and_facts(self) -> None:
        text = prompts.judge(state(), objection("OBJ-1"), CHARTER)

        assert "Decide OBJ-1" in text
        assert tags(text) == ["objection", "plan", "charter", "repo_facts"]

    def test_the_explainer_gets_every_objection_and_what_each_status_means(self) -> None:
        items = [
            objection("OBJ-1", status=Status.RESOLVED),
            objection("OBJ-2", severity=Severity.MINOR, status=Status.NOTED),
        ]

        text = prompts.explain(state(*items))

        assert tags(text) == ["feature", "plan", "objections", "statuses"]
        assert "OBJ-1 (design, major, resolved)" in text
        assert "OBJ-2 (design, minor, noted)" in text
        assert "\nruled: the debate deadlocked and the judge decided it\n" in text
        assert "\ndeferred: the code cannot settle it; a task to do before building\n" in text

    def test_the_explainer_is_told_when_nothing_was_raised(self) -> None:
        assert "<objections>\nNone were raised.\n</objections>" in prompts.explain(state())


class TestTheScorer:
    def test_is_shown_the_objection_with_its_debate_and_the_charter(self) -> None:
        text = prompts.scored(disputed(), CHARTER)

        assert text.startswith("OBJ-2 (factual, major, disputed): Claim OBJ-2.")
        assert "Round 1, planner: Rebuttal: Redis is used for sessions." in text
        assert text.endswith(
            "The charter for this codebase:\nPriorities, from 1 (matters a little) to 5 "
            "(matters most):\n- data integrity: 5\n- running cost: 2\nConstraints every "
            "plan must keep to:\n- no new managed services"
        )

    def test_is_asked_about_harm_not_who_is_right(self) -> None:
        assert "not whether the objection is right" in prompts.IMPACT

    def test_confirms_a_blocker_against_the_rubrics_own_words(self) -> None:
        assert prompts.IS_BLOCKER.endswith(RUBRIC[Severity.BLOCKER])


def test_inputs_are_trimmed_inside_their_tags() -> None:
    text = prompts.draft(state(facts="\n\n# Repo facts\n\n"), CHARTER)

    assert "<repo_facts>\n# Repo facts\n</repo_facts>" in text
