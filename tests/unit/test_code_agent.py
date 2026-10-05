from pathlib import Path

import pytest
from pydantic import BaseModel

from aiarb.ports.code_agent import (
    AgentError,
    AgentRequest,
    AgentResult,
    CodeAgent,
    Cost,
    FatalAgentError,
    OutputT,
    RetryableAgentError,
)


class Draft(BaseModel):
    summary: str


class TestCost:
    def test_starts_at_zero(self) -> None:
        assert Cost() == Cost(usd=0.0, input_tokens=0, output_tokens=0)

    def test_costs_add_up(self) -> None:
        total = Cost(usd=1.25, input_tokens=100, output_tokens=20) + Cost(
            usd=0.5, input_tokens=50, output_tokens=5
        )

        assert total == Cost(usd=1.75, input_tokens=150, output_tokens=25)

    def test_sum_of_many_costs(self) -> None:
        costs = [Cost(usd=1.0, input_tokens=1, output_tokens=1)] * 3

        assert sum(costs, Cost()) == Cost(usd=3.0, input_tokens=3, output_tokens=3)

    @pytest.mark.parametrize(
        "fields",
        [{"usd": -0.01}, {"input_tokens": -1}, {"output_tokens": -1}],
    )
    def test_cannot_be_negative(self, fields: dict[str, float]) -> None:
        with pytest.raises(ValueError, match="cannot be negative"):
            Cost(**fields)  # type: ignore[arg-type]


class TestAgentRequest:
    def test_defaults_to_a_new_session(self) -> None:
        request = AgentRequest(
            prompt="Draft a plan.", repo=Path("."), model="claude-opus-5-5", schema=Draft
        )

        assert request.session_id is None

    @pytest.mark.parametrize(
        ("prompt", "model", "message"),
        [(" ", "claude-opus-5-5", "needs a prompt"), ("Draft a plan.", "", "needs a model")],
    )
    def test_needs_a_prompt_and_a_model(self, prompt: str, model: str, message: str) -> None:
        with pytest.raises(ValueError, match=message):
            AgentRequest(prompt=prompt, repo=Path("."), model=model, schema=Draft)


class TestAgentErrors:
    @pytest.mark.parametrize("error_type", [RetryableAgentError, FatalAgentError])
    def test_both_kinds_are_agent_errors_and_carry_the_spend(
        self, error_type: type[AgentError]
    ) -> None:
        error = error_type("rate limited", cost=Cost(usd=0.4))

        assert isinstance(error, AgentError)
        assert error.cost == Cost(usd=0.4)
        assert str(error) == "rate limited"

    def test_spend_defaults_to_zero(self) -> None:
        assert AgentError("no Claude Code install").cost == Cost()


class EchoAgent:
    """The smallest thing that satisfies the protocol; mypy checks that it does."""

    async def run(self, request: AgentRequest[OutputT]) -> AgentResult[OutputT]:
        return AgentResult(
            output=request.schema.model_validate({"summary": request.prompt}),
            session_id=request.session_id or "session-1",
            cost=Cost(usd=0.01),
        )


async def test_a_result_is_typed_by_the_requested_schema() -> None:
    agent: CodeAgent = EchoAgent()
    request = AgentRequest(
        prompt="Draft a plan.", repo=Path("."), model="claude-opus-5-5", schema=Draft
    )

    result = await agent.run(request)

    assert result.output == Draft(summary="Draft a plan.")
    assert result.session_id == "session-1"


async def test_a_session_can_be_continued() -> None:
    request = AgentRequest(
        prompt="Respond to the objections.",
        repo=Path("."),
        model="claude-opus-5-5",
        schema=Draft,
        session_id="session-7",
    )

    result = await EchoAgent().run(request)

    assert result.session_id == "session-7"
