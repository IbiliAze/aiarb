from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from aiarb.ports.calls import CallError, Cost, FatalCallError, RetryableCallError
from aiarb.ports.code_agent import AgentRequest, CodeAgent
from aiarb.ports.scorer import Level, Scorer
from tests.fakes import (
    AgentStep,
    Scenario,
    ScorerStep,
    ScriptedCodeAgent,
    ScriptedScorer,
    ScriptError,
)

RUBRIC = [Level(0.0, "Cosmetic."), Level(1.0, "Data is lost.")]


class PlanDraft(BaseModel):
    summary: str


class Responses(BaseModel):
    answers: list[str]


def ask(
    schema: type[BaseModel], prompt: str = "Do the thing.", session_id: str | None = None
) -> AgentRequest[Any]:
    return AgentRequest(
        prompt=prompt, repo=Path("."), model="claude-opus-5-5", schema=schema, session_id=session_id
    )


def agent_step(**fields: Any) -> AgentStep:
    return AgentStep.model_validate(fields)


def scorer_step(**fields: Any) -> ScorerStep:
    return ScorerStep.model_validate(fields)


def test_the_fakes_satisfy_the_ports() -> None:
    agent: CodeAgent = ScriptedCodeAgent([])
    scorer: Scorer = ScriptedScorer([])

    assert agent is not None
    assert scorer is not None


class TestScriptedCodeAgent:
    async def test_answers_with_the_scripted_output_as_the_requested_type(self) -> None:
        agent = ScriptedCodeAgent(
            [agent_step(schema="PlanDraft", output={"summary": "Add a queue."}, cost={"usd": 2})]
        )

        result = await agent.run(ask(PlanDraft))

        assert result.output == PlanDraft(summary="Add a queue.")
        assert result.cost == Cost(usd=2)
        agent.assert_finished()

    async def test_matches_steps_by_schema_and_prompt_text_not_by_order(self) -> None:
        agent = ScriptedCodeAgent(
            [
                agent_step(schema="Responses", contains="OBJ-2", output={"answers": ["two"]}),
                agent_step(schema="Responses", contains="OBJ-1", output={"answers": ["one"]}),
                agent_step(schema="PlanDraft", output={"summary": "Plan."}),
            ]
        )

        plan = await agent.run(ask(PlanDraft))
        first = await agent.run(ask(Responses, "Answer OBJ-1."))
        second = await agent.run(ask(Responses, "Answer OBJ-2."))

        assert plan.output == PlanDraft(summary="Plan.")
        assert first.output == Responses(answers=["one"])
        assert second.output == Responses(answers=["two"])

    async def test_each_step_is_used_once_in_script_order(self) -> None:
        agent = ScriptedCodeAgent(
            [
                agent_step(schema="PlanDraft", output={"summary": "First."}),
                agent_step(schema="PlanDraft", output={"summary": "Second."}),
            ]
        )

        first = await agent.run(ask(PlanDraft))
        second = await agent.run(ask(PlanDraft))

        assert [first.output.summary, second.output.summary] == ["First.", "Second."]
        with pytest.raises(ScriptError, match="no unused step in the script matches a PlanDraft"):
            await agent.run(ask(PlanDraft))

    async def test_records_every_request(self) -> None:
        agent = ScriptedCodeAgent([agent_step(schema="PlanDraft", output={"summary": "Plan."})])
        request = ask(PlanDraft)

        await agent.run(request)

        assert agent.calls == [request]

    async def test_issues_new_sessions_and_keeps_continued_ones(self) -> None:
        agent = ScriptedCodeAgent(
            [
                agent_step(schema="PlanDraft", output={"summary": "Plan."}),
                agent_step(schema="Responses", output={"answers": []}),
                agent_step(schema="PlanDraft", output={"summary": "Critic's view."}),
            ]
        )

        planner = await agent.run(ask(PlanDraft))
        resumed = await agent.run(ask(Responses, session_id=planner.session_id))
        critic = await agent.run(ask(PlanDraft))

        assert planner.session_id == "session-1"
        assert resumed.session_id == "session-1"
        assert critic.session_id == "session-2"

    async def test_continuing_a_session_it_never_issued_is_a_script_error(self) -> None:
        agent = ScriptedCodeAgent([agent_step(schema="PlanDraft", output={"summary": "Plan."})])

        with pytest.raises(ScriptError, match="'session-9', which was never issued"):
            await agent.run(ask(PlanDraft, session_id="session-9"))

    async def test_output_that_does_not_fit_the_schema_is_a_script_error(self) -> None:
        agent = ScriptedCodeAgent([agent_step(schema="PlanDraft", output={"summry": "Typo."})])

        with pytest.raises(ScriptError, match="does not fit PlanDraft"):
            await agent.run(ask(PlanDraft))

    @pytest.mark.parametrize(
        ("error", "error_type"),
        [("retryable", RetryableCallError), ("fatal", FatalCallError)],
    )
    async def test_a_step_can_fail_with_its_cost(
        self, error: str, error_type: type[CallError]
    ) -> None:
        agent = ScriptedCodeAgent(
            [
                agent_step(
                    schema="PlanDraft", error=error, message="overloaded", cost={"usd": 0.3}
                ),
                agent_step(schema="PlanDraft", output={"summary": "Second try."}),
            ]
        )

        with pytest.raises(error_type, match="overloaded") as caught:
            await agent.run(ask(PlanDraft))
        retried = await agent.run(ask(PlanDraft))

        assert caught.value.cost == Cost(usd=0.3)
        assert retried.output.summary == "Second try."

    async def test_unused_steps_are_reported(self) -> None:
        agent = ScriptedCodeAgent(
            [agent_step(schema="Responses", contains="OBJ-4", output={"answers": []})]
        )

        with pytest.raises(ScriptError, match="1 scripted step"):
            agent.assert_finished()


class TestScriptedScorer:
    async def test_answers_each_kind_of_call(self) -> None:
        scorer = ScriptedScorer(
            [
                scorer_step(call="score", value=0.7, confidence=0.9, cost={"usd": 0.01}),
                scorer_step(call="choose", value="design", confidence=0.6),
                scorer_step(call="yes_no", value=True),
            ]
        )

        scored = await scorer.score("How much does OBJ-1 matter?", RUBRIC)
        chosen = await scorer.choose("What kind is OBJ-1?", ["factual", "design"])
        confirmed = await scorer.yes_no("Is OBJ-1 a blocker?")

        assert (scored.value, scored.confidence, scored.cost) == (0.7, 0.9, Cost(usd=0.01))
        assert (chosen.value, chosen.confidence) == ("design", 0.6)
        assert (confirmed.value, confirmed.confidence) == (True, 1.0)
        assert [call for call, _ in scorer.calls] == ["score", "choose", "yes_no"]
        scorer.assert_finished()

    async def test_matches_steps_by_question_text(self) -> None:
        scorer = ScriptedScorer(
            [
                scorer_step(call="score", contains="OBJ-2", value=0.2),
                scorer_step(call="score", contains="OBJ-1", value=0.9),
            ]
        )

        assert (await scorer.score("Rate OBJ-1.", RUBRIC)).value == 0.9
        assert (await scorer.score("Rate OBJ-2.", RUBRIC)).value == 0.2

    async def test_a_whole_number_score_is_a_float(self) -> None:
        scorer = ScriptedScorer([scorer_step(call="score", value=1)])

        result = await scorer.score("Rate it.", RUBRIC)

        assert result.value == 1.0
        assert isinstance(result.value, float)

    async def test_a_choice_outside_the_options_is_a_script_error(self) -> None:
        scorer = ScriptedScorer([scorer_step(call="choose", value="opinion")])

        with pytest.raises(ScriptError, match="'opinion' is not one of"):
            await scorer.choose("What kind?", ["factual", "design"])

    async def test_bad_input_is_refused_as_a_real_scorer_would(self) -> None:
        scorer = ScriptedScorer([scorer_step(call="score", value=0.5)])

        with pytest.raises(ValueError, match="at least two levels"):
            await scorer.score("Rate it.", RUBRIC[:1])

    async def test_a_step_can_fail(self) -> None:
        scorer = ScriptedScorer([scorer_step(call="yes_no", error="retryable")])

        with pytest.raises(RetryableCallError):
            await scorer.yes_no("Is it a blocker?")

    @pytest.mark.parametrize(
        ("fields", "message"),
        [
            ({"call": "score", "value": 1.5}, "a score step needs a value between 0 and 1"),
            ({"call": "score", "value": True}, "a score step needs a value between 0 and 1"),
            ({"call": "choose", "value": 0.5}, "a choose step needs one of the options"),
            ({"call": "yes_no", "value": "yes"}, "a yes_no step needs true or false"),
            ({"call": "yes_no", "value": True, "confidence": 2}, "less than or equal to 1"),
        ],
    )
    def test_malformed_steps_are_refused_when_the_script_loads(
        self, fields: dict[str, Any], message: str
    ) -> None:
        with pytest.raises(ValidationError, match=message):
            ScorerStep.model_validate(fields)


SCENARIO = """\
agent:
  - schema: PlanDraft
    output:
      summary: Add a waiting room.
    cost: {usd: 1.5, input_tokens: 1000, output_tokens: 200}
scorer:
  - call: score
    contains: OBJ-1
    value: 0.85
    confidence: 0.6
"""


class TestScenario:
    async def test_loads_both_scripts_from_a_yaml_file(self, tmp_path: Path) -> None:
        path = tmp_path / "scenario.yaml"
        path.write_text(SCENARIO)

        scenario = Scenario.from_yaml(path)
        plan = await scenario.agent.run(ask(PlanDraft))
        scored = await scenario.scorer.score("Rate OBJ-1.", RUBRIC)

        assert plan.output == PlanDraft(summary="Add a waiting room.")
        assert plan.cost == Cost(usd=1.5, input_tokens=1000, output_tokens=200)
        assert (scored.value, scored.confidence) == (0.85, 0.6)
        scenario.assert_finished()

    def test_an_empty_file_is_an_empty_scenario(self, tmp_path: Path) -> None:
        path = tmp_path / "scenario.yaml"
        path.write_text("")

        Scenario.from_yaml(path).assert_finished()

    def test_a_misspelt_key_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "scenario.yaml"
        path.write_text("agent:\n  - schema: PlanDraft\n    ouput: {}\n")

        with pytest.raises(ValidationError, match="ouput"):
            Scenario.from_yaml(path)
