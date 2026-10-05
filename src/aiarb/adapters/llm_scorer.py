"""The scorer port on a language model, run through the code agent port.

Used for scoring when Jev is not configured, as Jev's fallback when Jev is
unsure, and to confirm blocker calls. Each call is one fresh agent session in
the target repo, answered with one of the scorer schemas. The model reports its
own confidence.
"""

from __future__ import annotations

from functools import cache
from importlib.resources import files
from typing import TYPE_CHECKING

from pydantic import Field, create_model

from aiarb.ports.calls import RetryableCallError
from aiarb.ports.code_agent import AgentRequest
from aiarb.ports.scorer import Judgement, check_levels, check_options
from aiarb.schemas.outputs import Choice, Rating, YesNo

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from aiarb.ports.code_agent import AgentResult, CodeAgent, OutputT
    from aiarb.ports.scorer import Level


class LanguageModelScorer:
    def __init__(self, agent: CodeAgent, *, model: str, repo: Path) -> None:
        self._agent = agent
        self._model = model
        self._repo = repo

    async def score(self, subject: str, question: str, levels: Sequence[Level]) -> Judgement[float]:
        check_levels(levels)
        low, high = levels[0].score, levels[-1].score
        task = (
            f"{question}\n\nRate the subject on the levels below. Each level starts at the "
            f"score shown and runs up to where the next one starts. Give any score from "
            f"{low:g} to {high:g}."
        )
        anchors = "\n".join(f"From {level.score:.2f}: {level.meaning}" for level in levels)
        result = await self._ask(_prompt(task, subject=subject, levels=anchors), Rating)
        rating = result.output
        value = min(max(rating.score, low), high)
        return Judgement(value=value, confidence=rating.confidence, cost=result.cost)

    async def choose(self, subject: str, question: str, options: Sequence[str]) -> Judgement[str]:
        check_options(options)
        task = f"{question}\n\nChoose exactly one of the options below, written as given."
        listed = "\n".join(f"- {option}" for option in options)
        result = await self._ask(
            _prompt(task, subject=subject, options=listed), _choice_among(tuple(options))
        )
        choice = result.output
        if choice.choice not in options:
            raise RetryableCallError(
                f"the scorer chose {choice.choice!r}, which is not one of {list(options)}",
                cost=result.cost,
            )
        return Judgement(value=choice.choice, confidence=choice.confidence, cost=result.cost)

    async def yes_no(self, subject: str, question: str) -> Judgement[bool]:
        task = f"{question}\n\nAnswer yes or no."
        result = await self._ask(_prompt(task, subject=subject), YesNo)
        answer = result.output
        return Judgement(value=answer.answer, confidence=answer.confidence, cost=result.cost)

    async def _ask(self, prompt: str, schema: type[OutputT]) -> AgentResult[OutputT]:
        request = AgentRequest(
            prompt=prompt, repo=self._repo, model=self._model, schema=schema, role="scorer"
        )
        return await self._agent.run(request)


@cache
def _choice_among(options: tuple[str, ...]) -> type[Choice]:
    """The choice schema with its options listed, so the agent's answer is checked
    against them before it is returned."""
    return create_model(
        "Choice",
        __base__=Choice,
        choice=(
            str,
            Field(
                description="Exactly one of the options, written as given.",
                json_schema_extra={"enum": list(options)},
            ),
        ),
    )


@cache
def _brief() -> str:
    return (files("aiarb") / "prompts" / "scorer.md").read_text(encoding="utf-8").strip()


def _prompt(task: str, **inputs: str) -> str:
    """The brief, the task, then each input in its own tag, as the other roles get."""
    tagged = [f"<{tag}>\n{text.strip()}\n</{tag}>" for tag, text in inputs.items()]
    return "\n\n".join([_brief(), f"# This turn\n\n{task}", *tagged])
