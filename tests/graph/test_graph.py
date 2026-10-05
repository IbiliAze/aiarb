import asyncio
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError, NodeTimeoutError
from langgraph.types import Command, RetryPolicy

from aiarb.config.charter import Charter
from aiarb.domain.ledger import Actor, Status
from aiarb.domain.outcome import RunStatus
from aiarb.domain.scoring import Severity
from aiarb.graph import builder
from aiarb.graph.builder import TIMEOUTS, build_graph, recursion_limit, run_config
from aiarb.graph.context import RunContext
from aiarb.graph.state import RunState, checkpoint_serializer, initial_state, total_cost
from aiarb.ports.calls import Cost, FatalCallError, RetryableCallError
from aiarb.ports.code_agent import AgentRequest, AgentResult, OutputT
from tests.fakes import Scenario, ScriptedScorer

SCENARIOS = Path(__file__).parent.parent / "fixtures" / "scenarios"

NO_WAIT = RetryPolicy(initial_interval=0, jitter=False, retry_on=RetryableCallError)


def charter(max_rounds: int = 4) -> Charter:
    return Charter.model_validate({"debate": {"max_rounds": max_rounds}})


async def run(
    repo: Path,
    scenario: Scenario,
    *,
    max_rounds: int = 4,
    retry_policy: RetryPolicy = NO_WAIT,
    config: RunnableConfig | None = None,
    decisions: dict[str, str] | None = None,
) -> RunState:
    """Run a scenario in memory, giving `decisions` if it pauses for them."""
    rules = charter(max_rounds)
    context = RunContext(charter=rules, repo=repo, agent=scenario.agent, scorer=scenario.scorer)
    graph = build_graph(
        checkpointer=InMemorySaver(serde=checkpoint_serializer()), retry_policy=retry_policy
    )
    config = config or run_config(rules, "test-run")
    await graph.ainvoke(
        initial_state(feature="Add a virtual waiting room."), config, context=context
    )
    if decisions is not None:
        assert (await graph.aget_state(config)).interrupts, "the run did not pause"
        await graph.ainvoke(Command(resume=decisions), config, context=context)
    return cast("RunState", (await graph.aget_state(config)).values)


def from_yaml(name: str) -> Scenario:
    return Scenario.from_yaml(SCENARIOS / f"{name}.yaml")


def with_planner_steps_first(name: str, *steps: dict[str, Any]) -> Scenario:
    """A scenario file with extra steps the planner's answers meet first."""
    data = yaml.safe_load((SCENARIOS / f"{name}.yaml").read_text(encoding="utf-8"))
    data["agent"] = [*steps, *data["agent"]]
    return Scenario.from_data(data)


def statuses(state: RunState) -> dict[str, Status]:
    return {id: item.status for id, item in state["ledger"].items()}


def actors(state: RunState, id: str) -> list[Actor]:
    return [event.actor for event in state["ledger"][id].history]


class TestScenarios:
    async def test_clean_settle(self, tmp_path: Path) -> None:
        scenario = from_yaml("clean_settle")

        final = await run(tmp_path, scenario)

        scenario.assert_finished()
        assert final["status"] is RunStatus.SETTLED
        assert final["round"] == 1
        assert statuses(final) == {"OBJ-1": Status.RESOLVED, "OBJ-2": Status.NOTED}
        assert final["plan"].startswith("# Queue\n\nKeep the queue in Redis")
        assert total_cost(final).usd == pytest.approx(3.77)
        respond_prompt = scenario.agent.calls[2].prompt
        assert "Minor notes" in respond_prompt
        assert "OBJ-2" in respond_prompt

    async def test_stalemate_goes_to_the_judge_before_the_round_limit(self, tmp_path: Path) -> None:
        scenario = from_yaml("stalemate")

        final = await run(tmp_path, scenario, max_rounds=4)

        scenario.assert_finished()
        assert final["round"] == 2
        assert statuses(final) == {"OBJ-1": Status.RULED}
        assert final["ledger"]["OBJ-1"].history[-1].note.startswith("Ruled for the critic")
        assert final["status"] is RunStatus.SETTLED

    async def test_round_limit_turns_an_unsettled_fact_into_a_task(self, tmp_path: Path) -> None:
        scenario = from_yaml("round_limit")

        final = await run(tmp_path, scenario, max_rounds=2)

        scenario.assert_finished()
        assert final["round"] == 2
        assert statuses(final) == {"OBJ-1": Status.DEFERRED, "OBJ-2": Status.RESOLVED}
        assert actors(final, "OBJ-1") == [
            Actor.CRITIC,
            Actor.SCORER,
            Actor.PLANNER,
            Actor.CRITIC,
            Actor.VERIFIER,
            Actor.PLANNER,
            Actor.CRITIC,
            Actor.SYSTEM,
        ]
        assert final["status"] is RunStatus.SETTLED_WITH_OPEN_ITEMS

    async def test_judge_path_hands_each_leftover_on_by_type(self, tmp_path: Path) -> None:
        scenario = from_yaml("judge_path")

        final = await run(
            tmp_path, scenario, max_rounds=1, decisions={"OBJ-2": "Speed matters more."}
        )

        scenario.assert_finished()
        assert statuses(final) == {
            "OBJ-1": Status.DEFERRED,
            "OBJ-2": Status.DECIDED,
            "OBJ-3": Status.RULED,
        }
        assert final["ledger"]["OBJ-2"].history[-1].note == "Decided: Speed matters more."
        assert [request.schema.__name__ for request in scenario.agent.calls].count(
            "JudgeRuling"
        ) == 1
        assert final["status"] is RunStatus.SETTLED_WITH_OPEN_ITEMS

    async def test_blocker_left_open_means_not_ready(self, tmp_path: Path) -> None:
        scenario = from_yaml("blocker_left_open")

        final = await run(tmp_path, scenario)

        scenario.assert_finished()
        item = final["ledger"]["OBJ-1"]
        assert item.status is Status.DEFERRED
        assert item.severity is Severity.BLOCKER
        assert final["status"] is RunStatus.NOT_READY
        assert final["status"].exit_code == 2


def worst_case(rounds: int, type: str) -> Scenario:
    """A dispute verified every round, then the judge. A design objection is
    ruled and the run finishes, which is the most steps one invocation can take;
    a trade-off pauses the run for a decision instead. One other item closes in
    round 2, so the debate runs to the limit instead of stalling."""
    resolution = {"kind": "plan_change", "citations": [{"kind": "plan", "section": "Queue"}]}
    agent: list[dict[str, Any]] = [
        {"schema": "PlanDraft", "output": {"plan": "# Queue"}},
        {
            "schema": "Critique",
            "output": {
                "objections": [
                    {
                        "claim": f"Claim {n}.",
                        "type": type,
                        "severity": "major",
                        "evidence": [{"kind": "plan", "section": "Queue"}],
                    }
                    for n in (1, 2)
                ]
            },
        },
    ]
    for round in range(1, rounds + 1):
        ids = ["OBJ-1", "OBJ-2"] if round <= 2 else ["OBJ-1"]
        answers = [{"id": id, "response": "rebuttal", "note": "No."} for id in ids]
        rulings: list[dict[str, Any]] = [{"id": "OBJ-1", "verdict": "dispute", "note": "Check."}]
        if round == 1:
            rulings.append({"id": "OBJ-2", "verdict": "reject", "note": "No."})
        elif round == 2:
            rulings.append(
                {"id": "OBJ-2", "verdict": "accept", "note": "OK.", "resolution": resolution}
            )
        agent += [
            {"schema": "Responses", "output": {"plan": "# Queue", "answers": answers}},
            {"schema": "Rulings", "output": {"rulings": rulings}},
            {"schema": "Verdict", "output": {"finding": "critic_right", "note": "As claimed."}},
        ]
    if type == "design":
        agent.append({"schema": "JudgeRuling", "output": {"favours": "critic", "ruling": "Do."}})
    scorer = [
        {"call": "score", "contains": "OBJ-1", "value": 0.6},
        {"call": "score", "contains": "OBJ-2", "value": 0.6},
    ]
    return Scenario.from_data({"agent": agent, "scorer": scorer})


class TestRecursionLimit:
    @pytest.mark.parametrize(
        ("type", "outcome"),
        [
            pytest.param("design", Status.RULED, id="judged, then finished"),
            pytest.param("trade_off", Status.AWAITING_DECISION, id="paused for a decision"),
        ],
    )
    async def test_the_longest_invocations_fit_the_limit(
        self, tmp_path: Path, type: str, outcome: Status
    ) -> None:
        scenario = worst_case(3, type)

        final = await run(tmp_path, scenario, max_rounds=3)

        scenario.assert_finished()
        assert final["round"] == 3
        assert statuses(final) == {"OBJ-1": outcome, "OBJ-2": Status.RESOLVED}

    async def test_the_limit_is_exact(self, tmp_path: Path) -> None:
        config = run_config(charter(max_rounds=3), "test-run")
        config["recursion_limit"] = recursion_limit(3) - 1

        with pytest.raises(GraphRecursionError):
            await run(tmp_path, worst_case(3, "design"), max_rounds=3, config=config)

    def test_the_limit_comes_from_the_charters_max_rounds(self) -> None:
        config = run_config(charter(max_rounds=6), "2026-10-05-queue")

        assert config["recursion_limit"] == recursion_limit(6)
        assert recursion_limit(6) - recursion_limit(5) == 3

    def test_the_run_id_is_the_thread_id(self) -> None:
        config = run_config(charter(), "2026-10-05-queue")

        assert config["configurable"] == {"thread_id": "2026-10-05-queue"}


def failing_then(error: str, *, message: str = "overloaded") -> Scenario:
    """The clean scenario, with one failure scripted before the planner's answer."""
    step = {"schema": "Responses", "error": error, "message": message, "cost": {"usd": 0.1}}
    return with_planner_steps_first("clean_settle", step)


class TestRetries:
    async def test_a_retryable_failure_is_tried_again(self, tmp_path: Path) -> None:
        scenario = failing_then("retryable")

        final = await run(tmp_path, scenario)

        scenario.assert_finished()
        schemas = [request.schema.__name__ for request in scenario.agent.calls]
        assert schemas.count("Responses") == 2
        assert final["status"] is RunStatus.SETTLED

    async def test_an_answer_that_skips_an_objection_is_asked_again(self, tmp_path: Path) -> None:
        incomplete = {"schema": "Responses", "output": {"plan": "# Queue", "answers": []}}
        scenario = with_planner_steps_first("clean_settle", incomplete)

        final = await run(tmp_path, scenario)

        scenario.assert_finished()
        assert final["status"] is RunStatus.SETTLED

    async def test_a_fatal_failure_stops_the_run(self, tmp_path: Path) -> None:
        scenario = failing_then("fatal", message="bad credentials")

        with pytest.raises(FatalCallError, match="bad credentials"):
            await run(tmp_path, scenario)

        schemas = [request.schema.__name__ for request in scenario.agent.calls]
        assert schemas.count("Responses") == 1

    async def test_retries_stop_after_three_attempts(self, tmp_path: Path) -> None:
        failure = {"schema": "Responses", "error": "retryable"}
        scenario = with_planner_steps_first("clean_settle", failure, failure, failure)

        with pytest.raises(RetryableCallError):
            await run(tmp_path, scenario)

        schemas = [request.schema.__name__ for request in scenario.agent.calls]
        assert schemas.count("Responses") == NO_WAIT.max_attempts


class StuckAgent:
    """An agent whose call never returns."""

    def __init__(self) -> None:
        self.calls = 0

    async def run(self, request: AgentRequest[OutputT]) -> AgentResult[OutputT]:
        self.calls += 1
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class TestTimeouts:
    async def test_a_stuck_call_times_out_and_is_not_retried(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(builder.TIMEOUTS, "draft_plan", 0.05)
        agent = StuckAgent()
        context = RunContext(
            charter=Charter(), repo=tmp_path, agent=agent, scorer=ScriptedScorer([])
        )

        with pytest.raises(NodeTimeoutError):
            await build_graph(retry_policy=NO_WAIT).ainvoke(initial_state(), context=context)

        assert agent.calls == 1

    def test_every_node_that_makes_calls_has_a_retry_policy_and_a_timeout(self) -> None:
        nodes = build_graph().builder.nodes

        configured = {name for name, spec in nodes.items() if spec.retry_policy and spec.timeout}

        assert configured == set(TIMEOUTS)
        assert set(nodes) - configured == {"scan", "decide", "finalise"}
        assert nodes["respond"].retry_policy == builder.AGENT_RETRY


def test_the_graph_has_the_debate_loop() -> None:
    edges = {(edge.source, edge.target) for edge in build_graph().get_graph().edges}

    assert {("rule", "respond"), ("verify", "respond"), ("rule", "verify")} <= edges
    assert ("judge", "decide") in edges
    assert ("finalise", "__end__") in edges


async def test_costs_are_counted_per_role(tmp_path: Path) -> None:
    final = await run(tmp_path, from_yaml("clean_settle"))

    assert final["costs"]["planner"] == Cost(usd=2.5)
    assert final["costs"]["critic"] == Cost(usd=1.25)
    assert final["costs"]["scorer"].usd == pytest.approx(0.02)
