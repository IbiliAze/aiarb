from collections.abc import Sequence

import pytest

from aiarb.ports.calls import Cost
from aiarb.ports.scorer import Judgement, Level, Scorer, check_levels, check_options

RUBRIC = [Level(0.0, "Cosmetic."), Level(0.5, "Users notice."), Level(1.0, "Data is lost.")]


class TestLevel:
    @pytest.mark.parametrize("score", [-0.1, 1.1])
    def test_score_is_between_zero_and_one(self, score: float) -> None:
        with pytest.raises(ValueError, match="between 0 and 1"):
            Level(score, "Something.")

    def test_needs_a_meaning(self) -> None:
        with pytest.raises(ValueError, match="needs a meaning"):
            Level(0.5, " ")


class TestCheckLevels:
    def test_a_rising_rubric_is_valid(self) -> None:
        check_levels(RUBRIC)

    @pytest.mark.parametrize(
        ("levels", "message"),
        [
            ([Level(0.5, "Only one.")], "at least two levels"),
            ([Level(0.5, "High."), Level(0.1, "Low.")], "must rise strictly"),
            ([Level(0.5, "Same."), Level(0.5, "Same again.")], "must rise strictly"),
        ],
    )
    def test_bad_rubrics_are_refused(self, levels: list[Level], message: str) -> None:
        with pytest.raises(ValueError, match=message):
            check_levels(levels)


class TestCheckOptions:
    def test_distinct_options_are_valid(self) -> None:
        check_options(["factual", "design", "trade_off"])

    @pytest.mark.parametrize(
        ("options", "message"),
        [
            (["design"], "at least two options"),
            (["design", "design"], "must be distinct"),
            (["design", " "], "cannot be blank"),
        ],
    )
    def test_bad_options_are_refused(self, options: list[str], message: str) -> None:
        with pytest.raises(ValueError, match=message):
            check_options(options)


class TestJudgement:
    @pytest.mark.parametrize("confidence", [-0.01, 1.01])
    def test_confidence_is_between_zero_and_one(self, confidence: float) -> None:
        with pytest.raises(ValueError, match="confidence must be between 0 and 1"):
            Judgement(value=True, confidence=confidence, cost=Cost())


class FixedScorer:
    """The smallest thing that satisfies the protocol; mypy checks that it does."""

    async def score(self, question: str, levels: Sequence[Level]) -> Judgement[float]:
        check_levels(levels)
        return Judgement(value=levels[-1].score, confidence=0.9, cost=Cost(usd=0.001))

    async def choose(self, question: str, options: Sequence[str]) -> Judgement[str]:
        check_options(options)
        return Judgement(value=options[0], confidence=0.6, cost=Cost(usd=0.001))

    async def yes_no(self, question: str) -> Judgement[bool]:
        return Judgement(value=True, confidence=0.95, cost=Cost(usd=0.001))


async def test_each_call_returns_a_value_with_its_confidence_and_cost() -> None:
    scorer: Scorer = FixedScorer()

    scored = await scorer.score("How much does losing queue places on failover matter?", RUBRIC)
    chosen = await scorer.choose("What kind of objection is this?", ["factual", "design"])
    confirmed = await scorer.yes_no("Is this a blocker?")

    assert (scored.value, scored.confidence) == (1.0, 0.9)
    assert (chosen.value, chosen.confidence) == ("factual", 0.6)
    assert (confirmed.value, confirmed.confidence) == (True, 0.95)
    assert sum((j.cost for j in (scored, chosen, confirmed)), Cost()).usd == pytest.approx(0.003)
