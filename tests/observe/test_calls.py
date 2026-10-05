import itertools
import json
from pathlib import Path
from typing import Any

import pytest

from aiarb.observe.calls import Meter, OverBudget, WatchedAgent, WatchedScorer
from aiarb.observe.log import RunLog
from aiarb.observe.redact import Redactor
from aiarb.ports.calls import Cost, FatalCallError, RetryableCallError, SpendCapReached
from aiarb.ports.code_agent import AgentRequest, AgentResult, OutputT
from aiarb.ports.scorer import Level
from aiarb.schemas.outputs import PlanDraft
from tests.fakes import AgentStep, ScorerStep, ScriptedCodeAgent, ScriptedScorer

LEVELS = [Level(0.0, "Low."), Level(1.0, "High.")]


def run_log(tmp_path: Path) -> RunLog:
    return RunLog(tmp_path / "log.jsonl", Redactor())


def events(log: RunLog) -> list[dict[str, Any]]:
    return [json.loads(line) for line in log.path.read_text().splitlines()]


def request(*, cap: float | None = None) -> AgentRequest[PlanDraft]:
    return AgentRequest(
        prompt="Draft the plan.",
        repo=Path("."),
        model="claude-sonnet-5-5",
        schema=PlanDraft,
        role="planner",
        spend_cap_usd=cap,
    )


def drafting(cost: float = 1.5, **step: Any) -> ScriptedCodeAgent:
    return ScriptedCodeAgent(
        [
            AgentStep.model_validate(
                {"schema": "PlanDraft", "output": {"plan": "# Plan"}, "cost": {"usd": cost}, **step}
            )
        ]
    )


def ticking() -> dict[str, Any]:
    ticks = itertools.count(0, 2.5)
    return {"clock": lambda: float(next(ticks))}


class TestMeter:
    def test_counts_what_is_spent_against_the_budget(self) -> None:
        meter = Meter(10, Cost(4))
        meter.add(Cost(2.5))

        assert (meter.spent, meter.remaining) == (Cost(6.5), 3.5)
        meter.check()

    def test_refuses_once_the_budget_is_spent(self) -> None:
        meter = Meter(10, Cost(10))

        with pytest.raises(OverBudget, match=r"spent \$10\.00 of its \$10\.00 budget") as raised:
            meter.check()
        assert raised.value.budget == 10
        assert meter.remaining == 0

    def test_an_over_budget_stop_is_not_retried(self) -> None:
        assert issubclass(OverBudget, FatalCallError)
        assert not issubclass(OverBudget, RetryableCallError)


class TestWatchedAgent:
    async def test_counts_and_logs_each_call(self, tmp_path: Path) -> None:
        log, meter = run_log(tmp_path), Meter(10)
        agent = WatchedAgent(drafting(1.5), log, meter, **ticking())

        result = await agent.run(request())

        assert meter.spent == Cost(1.5)
        [call] = events(log)
        assert call | {"time": None} == {
            "time": None,
            "event": "call",
            "role": "planner",
            "model": "claude-sonnet-5-5",
            "answer": "PlanDraft",
            "continues": None,
            "seconds": 2.5,
            "cost_usd": 1.5,
            "input_tokens": 0,
            "output_tokens": 0,
            "session": result.session_id,
        }

    async def test_caps_each_call_at_what_is_left_of_the_budget(self, tmp_path: Path) -> None:
        scripted = drafting()
        agent = WatchedAgent(scripted, run_log(tmp_path), Meter(10, Cost(7.5)))

        await agent.run(request(cap=5))

        assert scripted.calls[0].spend_cap_usd == 2.5

    async def test_keeps_a_lower_cap_the_request_already_has(self, tmp_path: Path) -> None:
        scripted = drafting()
        agent = WatchedAgent(scripted, run_log(tmp_path), Meter(10))

        await agent.run(request(cap=1))

        assert scripted.calls[0].spend_cap_usd == 1

    async def test_refuses_calls_once_the_budget_is_spent(self, tmp_path: Path) -> None:
        scripted, log = ScriptedCodeAgent([]), run_log(tmp_path)
        agent = WatchedAgent(scripted, log, Meter(10, Cost(10.2)))

        with pytest.raises(OverBudget):
            await agent.run(request())

        assert scripted.calls == []
        assert [event["event"] for event in events(log)] == ["over_budget"]
        assert events(log)[0] | {"time": None} == {
            "time": None,
            "event": "over_budget",
            "spent_usd": 10.2,
            "budget_usd": 10,
        }

    async def test_counts_what_a_failed_call_spent(self, tmp_path: Path) -> None:
        log, meter = run_log(tmp_path), Meter(10)
        agent = WatchedAgent(drafting(0.75, error="retryable", message="overloaded"), log, meter)

        with pytest.raises(RetryableCallError):
            await agent.run(request())

        assert meter.spent == Cost(0.75)
        [call] = events(log)
        assert (call["error"], call["retryable"], call["cost_usd"]) == ("overloaded", True, 0.75)

    async def test_a_call_stopped_by_the_budget_cap_stops_the_run(self, tmp_path: Path) -> None:
        log, meter = run_log(tmp_path), Meter(10, Cost(9))
        agent = WatchedAgent(CappedAgent(by_request=True), log, meter)

        with pytest.raises(OverBudget, match=r"spent \$10\.05 of its \$10\.00"):
            await agent.run(request())

        assert [event["event"] for event in events(log)] == ["call", "over_budget"]

    async def test_a_call_stopped_by_its_own_cap_fails_as_it_was(self, tmp_path: Path) -> None:
        agent = WatchedAgent(CappedAgent(by_request=False), run_log(tmp_path), Meter(100))

        with pytest.raises(SpendCapReached) as raised:
            await agent.run(request())
        assert not isinstance(raised.value, OverBudget)


class CappedAgent:
    """Stops every call at its spend cap, having spent a little more."""

    def __init__(self, *, by_request: bool) -> None:
        self.by_request = by_request

    async def run(self, request: AgentRequest[OutputT]) -> AgentResult[OutputT]:
        raise SpendCapReached("spend cap", cost=Cost(1.05), by_request=self.by_request)


class TestWatchedScorer:
    async def test_counts_and_logs_each_call(self, tmp_path: Path) -> None:
        log, meter = run_log(tmp_path), Meter(10)
        steps = [ScorerStep(call="score", value=0.6, confidence=0.8, cost=Cost(0.01))]
        scorer = WatchedScorer(ScriptedScorer(steps), log, meter, **ticking())

        await scorer.score("OBJ-3 (design, major, open): The queue is lost.", "Impact?", LEVELS)

        assert meter.spent == Cost(0.01)
        [call] = events(log)
        assert call | {"time": None} == {
            "time": None,
            "event": "call",
            "role": "scorer",
            "asks": "score",
            "about": "OBJ-3",
            "seconds": 2.5,
            "cost_usd": 0.01,
            "input_tokens": 0,
            "output_tokens": 0,
            "value": 0.6,
            "confidence": 0.8,
            "escalated": False,
        }

    async def test_refuses_calls_once_the_budget_is_spent(self, tmp_path: Path) -> None:
        scripted = ScriptedScorer([])
        scorer = WatchedScorer(scripted, run_log(tmp_path), Meter(1, Cost(1)))

        with pytest.raises(OverBudget):
            await scorer.yes_no("OBJ-1", "Is it a blocker?")

        assert scripted.calls == []
