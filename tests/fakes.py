"""Scripted fakes for the ports, so the whole graph runs in tests with no API calls.

Each fake follows a script of steps. A call takes the first unused step that
matches it, so a script does not depend on the order concurrent calls arrive in.
Anything the script does not cover fails the test with a `ScriptError`.

Scripts are written as YAML scenario files:

    agent:
      - schema: PlanDraft          # the output schema the call asks for
        output: {summary: ...}     # checked against that schema
      - schema: Responses
        contains: OBJ-3            # optional: text the prompt must contain
        error: retryable           # optional: fail instead of answering
    scorer:
      - call: score                # score, choose or yes_no
        contains: OBJ-3
        value: 0.7
        confidence: 0.9
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Generic, Literal, Self, TypeVar

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from aiarb.ports.calls import CallError, Cost, FatalCallError, RetryableCallError
from aiarb.ports.code_agent import AgentRequest, AgentResult, OutputT
from aiarb.ports.scorer import Judgement, Level, check_levels, check_options

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence
    from pathlib import Path


class ScriptError(AssertionError):
    """The code under test made a call the script did not expect, or left steps unused."""


class _Step(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    contains: str | None = None
    """Text the prompt or question must contain for this step to match."""
    cost: Cost = Cost()
    error: Literal["retryable", "fatal"] | None = None
    message: str = "scripted failure"

    def matches_text(self, text: str) -> bool:
        return self.contains is None or self.contains in text

    def fail(self) -> CallError:
        kind = RetryableCallError if self.error == "retryable" else FatalCallError
        return kind(self.message, cost=self.cost)


class AgentStep(_Step):
    schema_name: str = Field(alias="schema")
    """The class name of the output schema the call asks for."""
    output: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = None
    """The session to report. By default, the one continued, or a new one."""


class ScorerStep(_Step):
    call: Literal["score", "choose", "yes_no"]
    value: float | str | bool | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)

    @model_validator(mode="after")
    def _value_fits_the_call(self) -> Self:
        if self.error is not None:
            return self
        value = self.value
        if self.call == "score" and not (
            isinstance(value, float | int) and not isinstance(value, bool) and 0 <= value <= 1
        ):
            raise ValueError(f"a score step needs a value between 0 and 1, got {value!r}")
        if self.call == "choose" and not isinstance(value, str):
            raise ValueError(f"a choose step needs one of the options as its value, got {value!r}")
        if self.call == "yes_no" and not isinstance(value, bool):
            raise ValueError(f"a yes_no step needs true or false as its value, got {value!r}")
        return self


StepT = TypeVar("StepT", bound=_Step)


class _Script(Generic[StepT]):
    def __init__(self, steps: Sequence[StepT]) -> None:
        self._steps = list(steps)
        self._used = [False] * len(self._steps)

    def take(self, description: str, matches: Callable[[StepT], bool]) -> StepT:
        for index, step in enumerate(self._steps):
            if not self._used[index] and matches(step):
                self._used[index] = True
                return step
        raise ScriptError(f"no unused step in the script matches {description}")

    def assert_finished(self) -> None:
        unused = [step for step, used in zip(self._steps, self._used, strict=True) if not used]
        if unused:
            listed = "\n".join(f"  {step!r}" for step in unused)
            raise ScriptError(f"{len(unused)} scripted step(s) were never used:\n{listed}")


class ScriptedCodeAgent:
    """A CodeAgent that answers from a script and records every request."""

    def __init__(self, steps: Sequence[AgentStep], *, sessions: Iterable[str] = ()) -> None:
        """`sessions` are ones issued before, as by an agent in a run that was
        stopped and is now resumed."""
        self._script = _Script(steps)
        self._sessions: set[str] = set(sessions)
        self.calls: list[AgentRequest[Any]] = []

    async def run(self, request: AgentRequest[OutputT]) -> AgentResult[OutputT]:
        self.calls.append(request)
        if request.session_id is not None and request.session_id not in self._sessions:
            raise ScriptError(f"continued session {request.session_id!r}, which was never issued")
        schema = request.schema.__name__
        step = self._script.take(
            f"a {schema} call with prompt {_preview(request.prompt)}",
            lambda step: step.schema_name == schema and step.matches_text(request.prompt),
        )
        if step.error is not None:
            raise step.fail()
        try:
            output = request.schema.model_validate(step.output)
        except ValidationError as error:
            raise ScriptError(f"scripted output does not fit {schema}:\n{error}") from error
        session_id = step.session_id or request.session_id or f"session-{len(self._sessions) + 1}"
        self._sessions.add(session_id)
        return AgentResult(output=output, session_id=session_id, cost=step.cost)

    def assert_finished(self) -> None:
        self._script.assert_finished()


class ScriptedScorer:
    """A Scorer that answers from a script and records every question."""

    def __init__(self, steps: Sequence[ScorerStep]) -> None:
        self._script = _Script(steps)
        self.calls: list[tuple[str, str]] = []
        """(call, question) for every call made."""

    async def score(self, question: str, levels: Sequence[Level]) -> Judgement[float]:
        check_levels(levels)
        step = self._take("score", question)
        assert isinstance(step.value, float | int)
        return Judgement(value=float(step.value), confidence=step.confidence, cost=step.cost)

    async def choose(self, question: str, options: Sequence[str]) -> Judgement[str]:
        check_options(options)
        step = self._take("choose", question)
        if step.value not in options:
            raise ScriptError(f"scripted choice {step.value!r} is not one of {list(options)}")
        assert isinstance(step.value, str)
        return Judgement(value=step.value, confidence=step.confidence, cost=step.cost)

    async def yes_no(self, question: str) -> Judgement[bool]:
        step = self._take("yes_no", question)
        assert isinstance(step.value, bool)
        return Judgement(value=step.value, confidence=step.confidence, cost=step.cost)

    def _take(self, call: str, question: str) -> ScorerStep:
        self.calls.append((call, question))
        step = self._script.take(
            f"a {call} call asking {_preview(question)}",
            lambda step: step.call == call and step.matches_text(question),
        )
        if step.error is not None:
            raise step.fail()
        return step

    def assert_finished(self) -> None:
        self._script.assert_finished()


class _ScenarioFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: list[AgentStep] = Field(default_factory=list)
    scorer: list[ScorerStep] = Field(default_factory=list)


class Scenario:
    """A scripted agent and scorer for one test run."""

    def __init__(self, agent: ScriptedCodeAgent, scorer: ScriptedScorer) -> None:
        self.agent = agent
        self.scorer = scorer

    @classmethod
    def from_yaml(cls, path: Path) -> Scenario:
        return cls.from_data(yaml.safe_load(path.read_text(encoding="utf-8")) or {})

    @classmethod
    def from_data(cls, data: Mapping[str, Any]) -> Scenario:
        """A scenario from parsed YAML, or from steps built in a test."""
        scenario = _ScenarioFile.model_validate(data)
        return cls(ScriptedCodeAgent(scenario.agent), ScriptedScorer(scenario.scorer))

    def assert_finished(self) -> None:
        self.agent.assert_finished()
        self.scorer.assert_finished()


def _preview(text: str, limit: int = 60) -> str:
    flat = " ".join(text.split())
    return repr(flat if len(flat) <= limit else flat[: limit - 1] + "…")
