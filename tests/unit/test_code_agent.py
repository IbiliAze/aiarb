from pathlib import Path

import pytest
from pydantic import BaseModel

from aiarb.ports.calls import Cost
from aiarb.ports.code_agent import AgentRequest, AgentResult, CodeAgent, OutputT


class Draft(BaseModel):
    summary: str


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
