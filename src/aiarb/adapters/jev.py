"""The scorer port on Jev, TypeSafe's model for typed decisions.

Each call is one request to Jev's System One endpoint with one question: the
subject goes in as the `state`, and the question as the `instructions`. Jev
answers a Score with a probability for each level, a Choice with a probability
for each option, and a yes/no question (a "noul") with the probability of yes.

API reference: https://docs.typesafe.ai/api
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

import aiarb
from aiarb.ports.calls import Cost, FatalCallError, RetryableCallError
from aiarb.ports.scorer import Judgement, check_levels, check_options

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from aiarb.ports.scorer import Level

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
USD_PER_INPUT_TOKEN = 42 / 1_000_000_000
"""Jev 1.13's price: $42 per billion input tokens. Output tokens are free."""
TIMEOUT_SECONDS = 30.0
"""Jev answers in well under a second. A call this slow has stalled."""

_QUESTION = "question"
"""The key the one question in each request is filed under."""

_RETRYABLE_STATUSES = frozenset({408, 409, 429, 529})
"""Worth trying again, as is any 5xx: a rate limit or an overload passes."""

Probability = Annotated[float, Field(ge=0, le=1)]


class _Answer(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class _ScoreAnswer(_Answer):
    type: Literal["score"]
    probabilities: dict[str, Probability]
    """Each level's index, as a string, mapped to its probability."""
    confidence: Probability


class _ChoiceAnswer(_Answer):
    type: Literal["choice"]
    choice: str
    confidence: Probability


class _NoulAnswer(_Answer):
    type: Literal["noul"]
    noul: Probability
    """The probability that the answer is yes."""


class _Usage(_Answer):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class _Response(_Answer):
    answers: dict[
        str, Annotated[_ScoreAnswer | _ChoiceAnswer | _NoulAnswer, Field(discriminator="type")]
    ]
    usage: _Usage


_RESPONSE = TypeAdapter(_Response)


class JevScorer:
    """Scores with Jev.

    Each call opens its own connection, so the scorer holds nothing that needs
    closing. `transport` replaces the network in tests.
    """

    def __init__(
        self,
        api_key: str,
        *,
        model: str = MODEL,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Jev needs a TypeSafe API key")
        self._api_key = api_key
        self._model = model
        self._transport = transport

    def __repr__(self) -> str:
        return f"JevScorer(model={self._model!r})"

    async def score(self, subject: str, question: str, levels: Sequence[Level]) -> Judgement[float]:
        """The levels go to Jev as its criteria, lowest first. The value is each
        level's score weighted by Jev's probability for it."""
        check_levels(levels)
        criteria = [level.meaning for level in levels]
        answer, cost = await self._ask(subject, question, "score", criteria)
        assert isinstance(answer, _ScoreAnswer)
        expected = {str(index) for index in range(len(levels))}
        if set(answer.probabilities) != expected:
            raise RetryableCallError(
                f"Jev rated on levels {sorted(answer.probabilities)}, not the "
                f"{len(levels)} it was given",
                cost=cost,
            )
        value = sum(answer.probabilities[str(i)] * level.score for i, level in enumerate(levels))
        # Probabilities that sum to a hair over 1 must not push the value past the top.
        value = min(max(value, levels[0].score), levels[-1].score)
        return Judgement(value=value, confidence=answer.confidence, cost=cost)

    async def choose(self, subject: str, question: str, options: Sequence[str]) -> Judgement[str]:
        """Jev leans towards options listed first, so put the likeliest last if
        that matters."""
        check_options(options)
        criteria = dict.fromkeys(options)
        answer, cost = await self._ask(subject, question, "choice", criteria)
        assert isinstance(answer, _ChoiceAnswer)
        if answer.choice not in options:
            raise RetryableCallError(
                f"Jev chose {answer.choice!r}, which is not one of {list(options)}", cost=cost
            )
        return Judgement(value=answer.choice, confidence=answer.confidence, cost=cost)

    async def yes_no(self, subject: str, question: str) -> Judgement[bool]:
        """Jev gives the probability of yes and no confidence. Confidence is its
        distance from an even chance, as TypeSafe suggests: 0 at 0.5, 1 at 0 or 1."""
        answer, cost = await self._ask(subject, question, "noul", None)
        assert isinstance(answer, _NoulAnswer)
        return Judgement(value=answer.noul >= 0.5, confidence=abs(2 * answer.noul - 1), cost=cost)

    async def _ask(
        self, subject: str, question: str, kind: str, criteria: Any
    ) -> tuple[_ScoreAnswer | _ChoiceAnswer | _NoulAnswer, Cost]:
        asked: dict[str, Any] = {"type": kind, "instructions": question}
        if criteria is not None:
            asked["criteria"] = criteria
        body = {"state": subject, "model": self._model, "questions": {_QUESTION: asked}}
        response = await self._post(body)
        try:
            parsed = _RESPONSE.validate_json(response.content)
        except ValidationError as error:
            raise RetryableCallError(
                f"Jev's answer was not in the documented shape: {error}"
            ) from error
        usage = parsed.usage
        cost = Cost(
            usd=usage.input_tokens * USD_PER_INPUT_TOKEN,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )
        answer = parsed.answers.get(_QUESTION)
        if answer is None or answer.type != kind:
            raise RetryableCallError(f"Jev did not answer the {kind} question asked", cost=cost)
        return answer, cost

    async def _post(self, body: Mapping[str, Any]) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "User-Agent": f"aiarb/{aiarb.__version__}",
        }
        try:
            async with httpx.AsyncClient(
                timeout=TIMEOUT_SECONDS, transport=self._transport
            ) as client:
                response = await client.post(API_URL, json=body, headers=headers)
        except httpx.TimeoutException as error:
            raise RetryableCallError(f"Jev did not answer within {TIMEOUT_SECONDS:g}s") from error
        except httpx.TransportError as error:
            raise RetryableCallError(f"could not reach Jev: {error}") from error
        if response.is_success:
            return response
        raise _failure(response)


def _failure(response: httpx.Response) -> RetryableCallError | FatalCallError:
    status = response.status_code
    detail = f"Jev refused the request ({status}): {_detail(response)}"
    if status in (401, 403):
        return FatalCallError(
            f"Jev refused the TypeSafe API key ({status}). Check TYPESAFE_API_KEY, or set "
            "scoring.provider to llm in the charter to score with a language model."
        )
    if status in _RETRYABLE_STATUSES or status >= 500:
        return RetryableCallError(detail)
    return FatalCallError(detail)


def _detail(response: httpx.Response) -> str:
    text = " ".join(response.text.split())
    return text[:500] or response.reason_phrase or "no detail given"
