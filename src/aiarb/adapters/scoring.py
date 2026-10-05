"""Which scorers a run uses, from the charter's `scoring` section.

With `provider: llm`, a language model scores. With `provider: jev`, Jev scores,
and a language model takes over any answer Jev gives with less confidence than
`confidence_threshold`. Either way, a language model confirms blocker calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from aiarb.adapters.jev import JevScorer
from aiarb.adapters.llm_scorer import LanguageModelScorer
from aiarb.ports.scorer import Scorer

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from pathlib import Path

    from aiarb.config.charter import Charter
    from aiarb.ports.code_agent import CodeAgent
    from aiarb.ports.scorer import Judgement, Level, ValueT


class EscalatingScorer:
    """Asks the first scorer, and the fallback whenever the first is unsure.

    The fallback's answer replaces the first one, which it keeps as
    `escalated_from`. Failures are not escalated: a scorer that fails raises, as
    it would alone, so a broken setup is fixed rather than quietly paid around.
    """

    def __init__(self, first: Scorer, fallback: Scorer, *, threshold: float) -> None:
        if not 0 <= threshold <= 1:
            raise ValueError(f"the confidence threshold must be between 0 and 1, got {threshold}")
        self.first = first
        self.fallback = fallback
        self.threshold = threshold

    async def score(self, subject: str, question: str, levels: Sequence[Level]) -> Judgement[float]:
        return await self._ask(lambda scorer: scorer.score(subject, question, levels))

    async def choose(self, subject: str, question: str, options: Sequence[str]) -> Judgement[str]:
        return await self._ask(lambda scorer: scorer.choose(subject, question, options))

    async def yes_no(self, subject: str, question: str) -> Judgement[bool]:
        return await self._ask(lambda scorer: scorer.yes_no(subject, question))

    async def _ask(
        self, call: Callable[[Scorer], Awaitable[Judgement[ValueT]]]
    ) -> Judgement[ValueT]:
        first = await call(self.first)
        if first.confidence >= self.threshold:
            return first
        return (await call(self.fallback)).replacing(first)


class MissingKeyError(ValueError):
    """The charter asks for Jev, but there is no key to call it with."""


@dataclass(frozen=True)
class Scorers:
    scorer: Scorer
    """Rates each objection's impact, before and after the debate."""
    confirmer: Scorer
    """A language model, which confirms blocker calls."""


def scorers_for(
    charter: Charter, *, agent: CodeAgent, repo: Path, typesafe_api_key: str | None
) -> Scorers:
    language_model = LanguageModelScorer(agent, model=charter.models.scorer, repo=repo)
    scoring = charter.scoring
    if scoring.provider == "llm":
        return Scorers(scorer=language_model, confirmer=language_model)
    if not typesafe_api_key:
        raise MissingKeyError(
            "the charter's scoring.provider is jev, but TYPESAFE_API_KEY is not set. "
            "Set it, or set scoring.provider to llm to score with a language model."
        )
    escalating = EscalatingScorer(
        JevScorer(typesafe_api_key), language_model, threshold=scoring.confidence_threshold
    )
    return Scorers(scorer=escalating, confirmer=language_model)
