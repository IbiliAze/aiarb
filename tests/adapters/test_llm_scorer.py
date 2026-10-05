from pathlib import Path
from typing import Any

import pytest

from aiarb.adapters.claude_code import answer_schema
from aiarb.adapters.llm_scorer import LanguageModelScorer, _choice_among
from aiarb.graph import prompts
from aiarb.ports.calls import Cost, RetryableCallError
from aiarb.ports.scorer import Level, Scorer
from tests.fakes import AgentStep, ScriptedCodeAgent

LEVELS = [Level(0.0, "Cosmetic."), Level(0.5, "Users notice."), Level(0.8, "Data is lost.")]
OBJECTION = "OBJ-1 (design, major, open): Queue places are lost on failover."
MODEL = "claude-sonnet-5-5"


def scorer(tmp_path: Path, *steps: dict[str, Any]) -> tuple[LanguageModelScorer, ScriptedCodeAgent]:
    agent = ScriptedCodeAgent([AgentStep.model_validate(step) for step in steps])
    return LanguageModelScorer(agent, model=MODEL, repo=tmp_path), agent


def test_is_a_scorer(tmp_path: Path) -> None:
    language_model: Scorer = scorer(tmp_path)[0]
    assert isinstance(language_model, Scorer)


class TestScore:
    async def test_asks_for_a_rating_on_the_levels(self, tmp_path: Path) -> None:
        rating = {"reason": "Users lose their place.", "score": 0.6, "confidence": 0.8}
        language_model, agent = scorer(
            tmp_path, {"schema": "Rating", "output": rating, "cost": {"usd": 0.02}}
        )

        scored = await language_model.score(OBJECTION, "How much harm?", LEVELS)

        assert (scored.value, scored.confidence, scored.cost) == (0.6, 0.8, Cost(usd=0.02))
        request = agent.calls[0]
        assert (request.model, request.repo, request.session_id) == (MODEL, tmp_path, None)
        assert request.prompt.startswith(prompts.brief("scorer"))
        assert "# This turn\n\nHow much harm?\n\nRate the subject on the levels below." in (
            request.prompt
        )
        assert "Give any score from 0 to 0.8." in request.prompt
        assert f"<subject>\n{OBJECTION}\n</subject>" in request.prompt
        assert (
            "<levels>\nFrom 0.00: Cosmetic.\nFrom 0.50: Users notice.\nFrom 0.80: Data is lost."
            "\n</levels>"
        ) in request.prompt

    async def test_keeps_the_rating_within_the_levels(self, tmp_path: Path) -> None:
        rating = {"reason": "Everything breaks.", "score": 1.0, "confidence": 0.9}
        language_model, _ = scorer(tmp_path, {"schema": "Rating", "output": rating})

        scored = await language_model.score(OBJECTION, "How much harm?", LEVELS)

        assert scored.value == 0.8


class TestChoose:
    async def test_lists_the_options_in_the_prompt_and_the_schema(self, tmp_path: Path) -> None:
        choice = {"reason": "It has a right answer.", "choice": "design", "confidence": 0.7}
        language_model, agent = scorer(tmp_path, {"schema": "Choice", "output": choice})

        chosen = await language_model.choose(OBJECTION, "What kind?", ["factual", "design"])

        assert (chosen.value, chosen.confidence) == ("design", 0.7)
        request = agent.calls[0]
        assert "<options>\n- factual\n- design\n</options>" in request.prompt
        schema = answer_schema(request.schema)
        assert schema["properties"]["choice"]["enum"] == ["factual", "design"]

    async def test_a_choice_outside_the_options_is_retryable(self, tmp_path: Path) -> None:
        choice = {"reason": "Hmm.", "choice": "opinion", "confidence": 0.7}
        language_model, _ = scorer(
            tmp_path, {"schema": "Choice", "output": choice, "cost": {"usd": 0.01}}
        )

        with pytest.raises(RetryableCallError, match="'opinion', which is not one of") as error:
            await language_model.choose(OBJECTION, "What kind?", ["factual", "design"])

        assert error.value.cost == Cost(usd=0.01)

    def test_the_schema_for_each_set_of_options_is_made_once(self) -> None:
        assert _choice_among(("a", "b")) is _choice_among(("a", "b"))
        assert _choice_among(("a", "b")) is not _choice_among(("a", "c"))


async def test_answers_yes_or_no(tmp_path: Path) -> None:
    answer = {"reason": "Holds are lost on every deploy.", "answer": True, "confidence": 0.9}
    language_model, agent = scorer(tmp_path, {"schema": "YesNo", "output": answer})

    confirmed = await language_model.yes_no(OBJECTION, "Is it a blocker?")

    assert (confirmed.value, confirmed.confidence) == (True, 0.9)
    assert "# This turn\n\nIs it a blocker?\n\nAnswer yes or no." in agent.calls[0].prompt
