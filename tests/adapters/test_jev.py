"""The Jev adapter against the request and response shapes in TypeSafe's API
reference, with the network replaced by a mock transport."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from aiarb.adapters.jev import API_URL, MODEL, USD_PER_INPUT_TOKEN, JevScorer
from aiarb.ports.calls import Cost, FatalCallError, RetryableCallError
from aiarb.ports.scorer import Level, Scorer

LEVELS = [Level(0.0, "Cosmetic."), Level(0.5, "Users notice."), Level(0.8, "Data is lost.")]
OBJECTION = "OBJ-1 (design, major, open): Queue places are lost on failover."


class Server:
    """Answers each request with `reply`, and keeps the requests."""

    def __init__(self, reply: Callable[[dict[str, Any]], httpx.Response]) -> None:
        self.reply = reply
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.reply(json.loads(request.content))

    @property
    def body(self) -> dict[str, Any]:
        return dict(json.loads(self.requests[-1].content))


def answering(answer: dict[str, Any], *, input_tokens: int = 1000) -> Server:
    return Server(
        lambda _: httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {"question": answer},
                "usage": {"input_tokens": input_tokens, "output_tokens": 20},
            },
        )
    )


def failing(status: int, body: str = '{"detail": "no"}') -> Server:
    return Server(lambda _: httpx.Response(status, text=body))


def jev(server: Server | Callable[[httpx.Request], httpx.Response]) -> JevScorer:
    return JevScorer("ts-secret", transport=httpx.MockTransport(server))


SCORE = {
    "type": "score",
    "score": 1.1,
    "legend": {"0": "Cosmetic.", "1": "Users notice.", "2": "Data is lost."},
    "probabilities": {"0": 0.0, "1": 0.9, "2": 0.1},
    "confidence": 0.85,
}


def test_jev_is_a_scorer() -> None:
    scorer: Scorer = JevScorer("ts-secret")
    assert isinstance(scorer, Scorer)


def test_needs_a_key() -> None:
    with pytest.raises(ValueError, match="needs a TypeSafe API key"):
        JevScorer(" ")


def test_does_not_show_its_key() -> None:
    assert "ts-secret" not in repr(JevScorer("ts-secret"))


class TestScore:
    async def test_sends_the_subject_as_state_and_the_levels_as_criteria(self) -> None:
        server = answering(SCORE)

        await jev(server).score(OBJECTION, "How much harm?", LEVELS)

        request = server.requests[0]
        assert str(request.url) == API_URL
        assert request.method == "POST"
        assert request.headers["Authorization"] == "Bearer ts-secret"
        assert request.headers["User-Agent"].startswith("aiarb/")
        assert server.body == {
            "state": OBJECTION,
            "model": MODEL,
            "questions": {
                "question": {
                    "type": "score",
                    "instructions": "How much harm?",
                    "criteria": ["Cosmetic.", "Users notice.", "Data is lost."],
                }
            },
        }

    async def test_weights_each_levels_score_by_its_probability(self) -> None:
        scored = await jev(answering(SCORE)).score(OBJECTION, "How much harm?", LEVELS)

        assert scored.value == pytest.approx(0.9 * 0.5 + 0.1 * 0.8)
        assert scored.confidence == 0.85

    async def test_stays_within_the_levels_when_probabilities_overshoot(self) -> None:
        over = {**SCORE, "probabilities": {"0": 0.0, "1": 0.000001, "2": 1.0}}

        scored = await jev(answering(over)).score(OBJECTION, "How much harm?", LEVELS)

        assert scored.value == 0.8

    async def test_charges_for_input_tokens_only(self) -> None:
        scored = await jev(answering(SCORE, input_tokens=2000)).score(OBJECTION, "?", LEVELS)

        assert scored.cost == Cost(
            usd=2000 * USD_PER_INPUT_TOKEN, input_tokens=2000, output_tokens=20
        )

    async def test_an_answer_on_other_levels_is_retryable(self) -> None:
        wrong = {**SCORE, "probabilities": {"0": 0.5, "1": 0.5}}

        with pytest.raises(RetryableCallError, match="not the 3 it was given") as error:
            await jev(answering(wrong)).score(OBJECTION, "How much harm?", LEVELS)

        assert error.value.cost.input_tokens == 1000

    async def test_refuses_a_bad_rubric_before_calling(self) -> None:
        server = answering(SCORE)

        with pytest.raises(ValueError, match="at least two levels"):
            await jev(server).score(OBJECTION, "How much harm?", LEVELS[:1])

        assert server.requests == []


class TestChoose:
    async def test_sends_the_options_as_criteria(self) -> None:
        server = answering(
            {
                "type": "choice",
                "choice": "design",
                "probabilities": {"factual": 0.2, "design": 0.8},
                "confidence": 0.6,
            }
        )

        chosen = await jev(server).choose(OBJECTION, "What kind?", ["factual", "design"])

        assert server.body["questions"]["question"]["criteria"] == {
            "factual": None,
            "design": None,
        }
        assert (chosen.value, chosen.confidence) == ("design", 0.6)

    async def test_a_choice_outside_the_options_is_retryable(self) -> None:
        server = answering({"type": "choice", "choice": "opinion", "confidence": 0.9})

        with pytest.raises(RetryableCallError, match="'opinion', which is not one of"):
            await jev(server).choose(OBJECTION, "What kind?", ["factual", "design"])


class TestYesNo:
    @pytest.mark.parametrize(
        ("noul", "value", "confidence"),
        [(0.95, True, 0.9), (0.5, True, 0.0), (0.2, False, 0.6), (0.0, False, 1.0)],
    )
    async def test_confidence_is_the_distance_from_an_even_chance(
        self, noul: float, value: bool, confidence: float
    ) -> None:
        server = answering({"type": "noul", "noul": noul})

        answer = await jev(server).yes_no(OBJECTION, "Is it a blocker?")

        assert "criteria" not in server.body["questions"]["question"]
        assert answer.value is value
        assert answer.confidence == pytest.approx(confidence)


class TestFailures:
    @pytest.mark.parametrize("status", [401, 403])
    async def test_a_refused_key_is_fatal_and_says_what_to_do(self, status: int) -> None:
        with pytest.raises(FatalCallError, match="Check TYPESAFE_API_KEY") as error:
            await jev(failing(status)).yes_no(OBJECTION, "?")

        assert "ts-secret" not in str(error.value)

    @pytest.mark.parametrize("status", [429, 500, 502, 503, 529])
    async def test_rate_limits_and_overloads_are_retryable(self, status: int) -> None:
        with pytest.raises(RetryableCallError, match=f"\\({status}\\)"):
            await jev(failing(status)).yes_no(OBJECTION, "?")

    async def test_a_request_jev_cannot_read_is_fatal_with_its_detail(self) -> None:
        body = '{"detail": [{"loc": ["body", "questions"], "msg": "too many levels"}]}'

        with pytest.raises(FatalCallError, match="too many levels"):
            await jev(failing(422, body)).yes_no(OBJECTION, "?")

    async def test_a_timeout_is_retryable(self) -> None:
        def stall(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=request)

        with pytest.raises(RetryableCallError, match="did not answer within"):
            await jev(stall).yes_no(OBJECTION, "?")

    async def test_a_dropped_connection_is_retryable(self) -> None:
        def drop(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        with pytest.raises(RetryableCallError, match="could not reach Jev"):
            await jev(drop).yes_no(OBJECTION, "?")

    async def test_an_answer_in_another_shape_is_retryable(self) -> None:
        server = Server(lambda _: httpx.Response(200, json={"answers": {}}))

        with pytest.raises(RetryableCallError, match="not in the documented shape"):
            await jev(server).yes_no(OBJECTION, "?")

    async def test_an_answer_of_the_wrong_type_is_retryable(self) -> None:
        with pytest.raises(RetryableCallError, match="did not answer the noul question"):
            await jev(answering(SCORE)).yes_no(OBJECTION, "?")
