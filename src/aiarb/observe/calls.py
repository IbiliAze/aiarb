"""Every outside call a run makes: counted against the run's budget, and logged.

The runner wraps the run's agent and scorers before each invocation. Each call
is refused once the run has spent its budget, and a Claude Code call is capped
at what is left of it, so a run overshoots its budget by at most the calls
already under way. A refused call raises `OverBudget`, which is not retried:
the step fails, the run stops, and it carries on from that step once the budget
is raised.
"""

from __future__ import annotations

import re
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from aiarb.observe.log import cost_details
from aiarb.ports.calls import CallError, Cost, FatalCallError, RetryableCallError, SpendCapReached

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from aiarb.graph.context import RunContext
    from aiarb.observe.log import RunLog
    from aiarb.ports.code_agent import AgentRequest, AgentResult, CodeAgent, OutputT
    from aiarb.ports.scorer import Judgement, Level, Scorer, ValueT

_OBJECTION = re.compile(r"\bOBJ-\d+\b")


class OverBudget(FatalCallError):
    """The run has spent its budget. It stops, and carries on once the budget is raised."""

    def __init__(self, spent: Cost, budget: float) -> None:
        super().__init__(
            f"the run has spent ${spent.usd:.2f} of its ${budget:.2f} budget "
            "(budget.per_run in the charter)"
        )
        self.spent = spent
        self.budget = budget


class Meter:
    """What a run has spent, against its budget."""

    def __init__(self, budget: float, spent: Cost | None = None) -> None:
        if budget <= 0:
            raise ValueError(f"the budget must be positive, got {budget}")
        self.budget = budget
        self.spent = spent or Cost()

    @property
    def remaining(self) -> float:
        return max(self.budget - self.spent.usd, 0.0)

    def check(self) -> None:
        """Raise `OverBudget` if the budget is spent."""
        if self.spent.usd >= self.budget:
            raise OverBudget(self.spent, self.budget)

    def add(self, cost: Cost) -> None:
        self.spent += cost


def watch(context: RunContext, log: RunLog, meter: Meter) -> RunContext:
    """The context with its agent and scorers counted and logged."""
    return replace(
        context,
        agent=WatchedAgent(context.agent, log, meter),
        scorer=WatchedScorer(context.scorer, log, meter),
        confirmer=WatchedScorer(context.confirmer, log, meter),
        meter=meter,
    )


class _Watch:
    def __init__(
        self, log: RunLog, meter: Meter, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._log = log
        self._meter = meter
        self._clock = clock

    def _check(self) -> None:
        try:
            self._meter.check()
        except OverBudget as error:
            self._over_budget(error)
            raise

    def _over_budget(self, error: OverBudget) -> None:
        self._log.write("over_budget", spent_usd=round(error.spent.usd, 6), budget_usd=error.budget)

    async def _call(
        self,
        details: dict[str, Any],
        call: Callable[[], Awaitable[Any]],
        answered: Callable[[Any], tuple[Cost, dict[str, Any]]],
    ) -> Any:
        began = self._clock()
        try:
            result = await call()
        except BaseException as error:
            cost = error.cost if isinstance(error, CallError) else Cost()
            self._record(details, began, cost, {"error": _describe(error), **_kind(error)})
            raise
        cost, more = answered(result)
        self._record(details, began, cost, more)
        return result

    def _record(
        self, details: dict[str, Any], began: float, cost: Cost, more: dict[str, Any]
    ) -> None:
        self._meter.add(cost)
        seconds = round(self._clock() - began, 3)
        self._log.write("call", **details, seconds=seconds, **cost_details(cost), **more)


class WatchedAgent(_Watch):
    """A `CodeAgent` whose calls are counted against the budget and logged."""

    def __init__(self, agent: CodeAgent, log: RunLog, meter: Meter, **kwargs: Any) -> None:
        super().__init__(log, meter, **kwargs)
        self._agent = agent

    async def run(self, request: AgentRequest[OutputT]) -> AgentResult[OutputT]:
        self._check()
        remaining = self._meter.remaining
        if request.spend_cap_usd is None or remaining < request.spend_cap_usd:
            request = replace(request, spend_cap_usd=remaining)
        details = {
            "role": request.role,
            "model": request.model,
            "answer": request.schema.__name__,
            "continues": request.session_id,
        }

        def answered(result: AgentResult[OutputT]) -> tuple[Cost, dict[str, Any]]:
            return result.cost, {"session": result.session_id}

        try:
            return await self._call(details, lambda: self._agent.run(request), answered)  # type: ignore[no-any-return]
        except SpendCapReached as error:
            if not error.by_request:
                raise
            # The call was capped at what was left of the budget.
            over = OverBudget(self._meter.spent, self._meter.budget)
            self._over_budget(over)
            raise over from error


class WatchedScorer(_Watch):
    """A `Scorer` whose calls are counted against the budget and logged."""

    def __init__(self, scorer: Scorer, log: RunLog, meter: Meter, **kwargs: Any) -> None:
        super().__init__(log, meter, **kwargs)
        self._scorer = scorer

    async def score(self, subject: str, question: str, levels: Sequence[Level]) -> Judgement[float]:
        return await self._ask(
            "score", subject, lambda: self._scorer.score(subject, question, levels)
        )

    async def choose(self, subject: str, question: str, options: Sequence[str]) -> Judgement[str]:
        return await self._ask(
            "choose", subject, lambda: self._scorer.choose(subject, question, options)
        )

    async def yes_no(self, subject: str, question: str) -> Judgement[bool]:
        return await self._ask("yes_no", subject, lambda: self._scorer.yes_no(subject, question))

    async def _ask(
        self, kind: str, subject: str, call: Callable[[], Awaitable[Judgement[ValueT]]]
    ) -> Judgement[ValueT]:
        self._check()
        about = _OBJECTION.search(subject)
        details = {"role": "scorer", "asks": kind, "about": about.group(0) if about else None}

        def answered(judgement: Judgement[ValueT]) -> tuple[Cost, dict[str, Any]]:
            return judgement.cost, {
                "value": judgement.value,
                "confidence": judgement.confidence,
                "escalated": judgement.escalated_from is not None,
            }

        return await self._call(details, call, answered)  # type: ignore[no-any-return]


def _describe(error: BaseException) -> str:
    return str(error) or type(error).__name__


def _kind(error: BaseException) -> dict[str, Any]:
    if isinstance(error, RetryableCallError):
        return {"retryable": True}
    if isinstance(error, FatalCallError):
        return {"retryable": False}
    return {"error_type": type(error).__name__}
