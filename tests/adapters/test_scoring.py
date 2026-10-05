from pathlib import Path

import pytest

from aiarb.adapters.jev import JevScorer
from aiarb.adapters.llm_scorer import LanguageModelScorer
from aiarb.adapters.scoring import EscalatingScorer, MissingKeyError, scorers_for
from aiarb.config.charter import Charter
from aiarb.ports.calls import Cost, FatalCallError
from aiarb.ports.scorer import Level, Scorer
from tests.fakes import ScorerStep, ScriptedCodeAgent, ScriptedScorer

LEVELS = [Level(0.0, "Cosmetic."), Level(1.0, "Data is lost.")]


def scripted(**step: object) -> ScriptedScorer:
    return ScriptedScorer([ScorerStep.model_validate(step)])


class TestEscalatingScorer:
    async def test_keeps_a_confident_first_answer(self) -> None:
        first = scripted(call="score", value=0.4, confidence=0.7)
        fallback = ScriptedScorer([])
        scorer: Scorer = EscalatingScorer(first, fallback, threshold=0.7)

        scored = await scorer.score("OBJ-1", "How much harm?", LEVELS)

        assert (scored.value, scored.escalated_from) == (0.4, None)
        assert fallback.calls == []

    async def test_asks_the_fallback_when_the_first_is_unsure(self) -> None:
        first = scripted(call="score", value=0.4, confidence=0.69, cost={"usd": 0.25})
        fallback = scripted(call="score", value=0.6, confidence=0.9, cost={"usd": 0.5})
        scorer = EscalatingScorer(first, fallback, threshold=0.7)

        scored = await scorer.score("OBJ-1", "How much harm?", LEVELS)

        assert (scored.value, scored.confidence, scored.cost) == (0.6, 0.9, Cost(usd=0.75))
        assert scored.escalated_from is not None
        assert scored.escalated_from.value == 0.4
        assert fallback.calls == [("score", "OBJ-1", "How much harm?")]

    async def test_escalates_every_kind_of_question(self) -> None:
        first = ScriptedScorer(
            [
                ScorerStep(call="choose", value="design", confidence=0.1),
                ScorerStep(call="yes_no", value=True, confidence=0.1),
            ]
        )
        fallback = ScriptedScorer(
            [
                ScorerStep(call="choose", value="factual", confidence=0.9),
                ScorerStep(call="yes_no", value=False, confidence=0.9),
            ]
        )
        scorer = EscalatingScorer(first, fallback, threshold=0.5)

        chosen = await scorer.choose("OBJ-1", "What kind?", ["factual", "design"])
        confirmed = await scorer.yes_no("OBJ-1", "Is it a blocker?")

        assert (chosen.value, confirmed.value) == ("factual", False)

    async def test_a_failure_is_not_escalated(self) -> None:
        first = scripted(call="yes_no", error="fatal", message="bad key")
        fallback = ScriptedScorer([])

        with pytest.raises(FatalCallError, match="bad key"):
            await EscalatingScorer(first, fallback, threshold=0.7).yes_no("OBJ-1", "?")

        assert fallback.calls == []

    def test_the_threshold_is_a_confidence(self) -> None:
        with pytest.raises(ValueError, match="between 0 and 1"):
            EscalatingScorer(ScriptedScorer([]), ScriptedScorer([]), threshold=1.5)


class TestScorersFor:
    def test_a_language_model_scores_and_confirms_by_default(self, tmp_path: Path) -> None:
        chosen = scorers_for(
            Charter(), agent=ScriptedCodeAgent([]), repo=tmp_path, typesafe_api_key="ts-key"
        )

        assert isinstance(chosen.scorer, LanguageModelScorer)
        assert chosen.confirmer is chosen.scorer

    def test_jev_scores_with_a_language_model_behind_it(self, tmp_path: Path) -> None:
        charter = Charter.model_validate(
            {"scoring": {"provider": "jev", "confidence_threshold": 0.6}}
        )

        chosen = scorers_for(
            charter, agent=ScriptedCodeAgent([]), repo=tmp_path, typesafe_api_key="ts-key"
        )

        assert isinstance(chosen.scorer, EscalatingScorer)
        assert isinstance(chosen.scorer.first, JevScorer)
        assert chosen.scorer.fallback is chosen.confirmer
        assert isinstance(chosen.confirmer, LanguageModelScorer)
        assert chosen.scorer.threshold == 0.6

    @pytest.mark.parametrize("key", [None, ""])
    def test_jev_without_a_key_says_how_to_fix_it(self, tmp_path: Path, key: str | None) -> None:
        charter = Charter.model_validate({"scoring": {"provider": "jev"}})

        with pytest.raises(MissingKeyError, match="TYPESAFE_API_KEY is not set"):
            scorers_for(charter, agent=ScriptedCodeAgent([]), repo=tmp_path, typesafe_api_key=key)
