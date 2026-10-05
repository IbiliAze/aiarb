"""What the runner watches as a run goes: its log, its budget, the repo, and
secrets in what it writes."""

import json
from pathlib import Path
from typing import Any

import pytest

from aiarb.config.charter import Charter
from aiarb.graph.context import RunContext
from aiarb.graph.runner import load, resume, start
from aiarb.graph.state import initial_state, total_cost
from aiarb.observe.calls import OverBudget
from aiarb.observe.log import LOG
from aiarb.observe.redact import REDACTED, Redactor
from aiarb.ports.calls import Cost
from aiarb.ports.code_agent import AgentRequest, AgentResult, CodeAgent, OutputT
from aiarb.ports.scorer import Scorer
from tests.fakes import Scenario
from tests.graph.test_runner import FEATURE, SCENARIOS, Recorder, store

SECRET = "sk-ant-api03-" + "q1W2e3R4" * 6


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    (path / "app.py").write_text("print('hi')\n")
    return path


def context(repo: Path, agent: CodeAgent, scorer: Scorer, *, budget: float = 60) -> RunContext:
    charter = Charter.model_validate({"budget": {"per_call": min(10, budget), "per_run": budget}})
    return RunContext(
        charter=charter,
        repo=repo,
        agent=agent,
        scorer=scorer,
        confirmer=scorer,
        redactor=Redactor(),
    )


def clean_settle(repo: Path, *, budget: float = 60) -> tuple[RunContext, Scenario]:
    scenario = Scenario.from_yaml(SCENARIOS / "clean_settle.yaml")
    return context(repo, scenario.agent, scenario.scorer, budget=budget), scenario


def events(tmp_path: Path, run_id: str) -> list[dict[str, Any]]:
    path = store(tmp_path).folder(run_id) / LOG
    return [json.loads(line) for line in path.read_text().splitlines()]


class TestLog:
    async def test_records_the_run_its_steps_and_its_calls(
        self, tmp_path: Path, repo: Path
    ) -> None:
        ctx, _ = clean_settle(repo)

        result = await start(store(tmp_path), ctx, initial_state(feature=FEATURE))

        log = events(tmp_path, result.run_id)
        assert log[0]["event"] == "opened"
        assert (log[0]["how"], log[0]["feature"], log[0]["budget_usd"]) == ("start", FEATURE, 60)
        assert log[-1] | {"time": None} == {
            "time": None,
            "event": "finished",
            "status": "settled",
            "rounds": 1,
            "spent_usd": 3.77,
        }
        finished = [event["step"] for event in log if event["event"] == "step_finished"]
        assert finished[:3] == ["scan", "draft_plan", "critique"]
        assert finished[-1] == "finalise"
        calls = [event for event in log if event["event"] == "call"]
        assert [call["role"] for call in calls] == [
            "planner",
            "critic",
            "scorer",
            "scorer",
            "planner",
            "critic",
            "explainer",
        ]
        assert sum(call["cost_usd"] for call in calls) == pytest.approx(3.77)

    async def test_a_step_records_how_long_it_took_and_what_it_spent(
        self, tmp_path: Path, repo: Path
    ) -> None:
        ctx, _ = clean_settle(repo)

        result = await start(store(tmp_path), ctx, initial_state(feature=FEATURE))

        draft = next(
            event
            for event in events(tmp_path, result.run_id)
            if event["event"] == "step_finished" and event["step"] == "draft_plan"
        )
        assert (draft["cost_usd"], draft["spent_usd"], draft["round"]) == (2, 2, 0)
        assert draft["seconds"] >= 0

    async def test_a_failed_step_is_logged_and_its_spending_counted(
        self, tmp_path: Path, repo: Path
    ) -> None:
        failing = Scenario.from_data(
            {
                "agent": [
                    {"schema": "PlanDraft", "output": {"plan": "# Plan"}, "cost": {"usd": 2}},
                    {"schema": "Critique", "error": "fatal", "cost": {"usd": 0.5}},
                ]
            }
        )
        ctx = context(repo, failing.agent, failing.scorer)

        with pytest.raises(Exception, match="scripted failure"):
            await start(store(tmp_path), ctx, initial_state(feature=FEATURE))

        [run_id] = store(tmp_path).runs()
        log = events(tmp_path, run_id)
        assert [event["event"] for event in log][-3:] == ["step_started", "call", "stopped"]
        assert (log[-1]["step"], log[-1]["error"]) == ("critique", "scripted failure")
        stopped = await load(store(tmp_path), run_id)
        assert total_cost(stopped.state) == Cost(2)
        assert stopped.spent == Cost(2.5)


class TestBudget:
    async def test_stops_the_run_once_it_has_spent_its_budget(
        self, tmp_path: Path, repo: Path
    ) -> None:
        # The planner spends 2 and the critic 1, which takes the run past 2.5.
        ctx, _ = clean_settle(repo, budget=2.5)

        with pytest.raises(OverBudget, match=r"spent \$3\.00 of its \$2\.50 budget"):
            await start(store(tmp_path), ctx, initial_state(feature=FEATURE))

        [run_id] = store(tmp_path).runs()
        stopped = await load(store(tmp_path), run_id)
        assert stopped.stopped
        assert stopped.next == ("triage",)
        log = events(tmp_path, run_id)
        # Triage scores both objections at once, and both calls are refused.
        assert [event["event"] for event in log][-4:] == [
            "step_started",
            "over_budget",
            "over_budget",
            "stopped",
        ]
        assert (log[-1]["step"], log[-1]["error_type"]) == ("triage", "OverBudget")

    async def test_carries_on_once_the_budget_is_raised(self, tmp_path: Path, repo: Path) -> None:
        ctx, scenario = clean_settle(repo, budget=2.5)
        with pytest.raises(OverBudget):
            await start(store(tmp_path), ctx, initial_state(feature=FEATURE))
        [run_id] = store(tmp_path).runs()

        with pytest.raises(OverBudget):
            await resume(store(tmp_path), run_id, ctx)
        raised = context(repo, scenario.agent, scenario.scorer, budget=10)
        result = await resume(store(tmp_path), run_id, raised)

        scenario.assert_finished()
        assert result.finished
        assert result.spent == total_cost(result.state) == Cost(3.77)
        data = json.loads((store(tmp_path).folder(run_id) / "run.json").read_text())
        assert data["failed_steps_cost"]["usd"] == 0

    async def test_counts_what_failed_steps_spent_against_the_budget(
        self, tmp_path: Path, repo: Path
    ) -> None:
        failing = Scenario.from_data(
            {"agent": [{"schema": "PlanDraft", "error": "fatal", "cost": {"usd": 3}}]}
        )
        with pytest.raises(Exception, match="scripted failure"):
            await start(
                store(tmp_path),
                context(repo, failing.agent, failing.scorer, budget=2.5),
                initial_state(feature=FEATURE),
            )
        [run_id] = store(tmp_path).runs()

        with pytest.raises(OverBudget, match=r"spent \$3\.00"):
            await resume(
                store(tmp_path), run_id, context(repo, failing.agent, failing.scorer, budget=2.5)
            )

    async def test_run_json_records_what_failed_steps_spent(
        self, tmp_path: Path, repo: Path
    ) -> None:
        data = Scenario.from_yaml(SCENARIOS / "clean_settle.yaml")
        first = Scenario.from_data(
            {"agent": [{"schema": "PlanDraft", "error": "fatal", "cost": {"usd": 0.4}}]}
        )
        with pytest.raises(Exception, match="scripted failure"):
            await start(
                store(tmp_path),
                context(repo, first.agent, first.scorer),
                initial_state(feature=FEATURE),
            )
        [run_id] = store(tmp_path).runs()

        result = await resume(store(tmp_path), run_id, context(repo, data.agent, data.scorer))

        assert result.spent == Cost(4.17)
        record = json.loads((store(tmp_path).folder(run_id) / "run.json").read_text())
        assert record["total_cost"]["usd"] == 3.77
        assert record["failed_steps_cost"]["usd"] == 0.4


class EditsTheRepo:
    """Writes to the repo during its first call, as nothing in aiarb may."""

    def __init__(self, agent: CodeAgent) -> None:
        self.agent = agent

    async def run(self, request: AgentRequest[OutputT]) -> AgentResult[OutputT]:
        (request.repo / "app.py").write_text("print('changed during the run')\n")
        return await self.agent.run(request)


class TestRepo:
    async def test_a_run_leaves_the_repo_as_it_found_it(self, tmp_path: Path, repo: Path) -> None:
        ctx, _ = clean_settle(repo)
        recorder = Recorder()

        result = await start(
            store(tmp_path), ctx, initial_state(feature=FEATURE), progress=recorder
        )

        assert ("repo_changed", "app.py") not in recorder.events
        assert all(event["event"] != "repo_changed" for event in events(tmp_path, result.run_id))

    async def test_reports_files_changed_while_the_run_went(
        self, tmp_path: Path, repo: Path
    ) -> None:
        scenario = Scenario.from_yaml(SCENARIOS / "clean_settle.yaml")
        ctx = context(repo, EditsTheRepo(scenario.agent), scenario.scorer)
        recorder = Recorder()

        result = await start(
            store(tmp_path), ctx, initial_state(feature=FEATURE), progress=recorder
        )

        assert recorder.events[-1] == ("repo_changed", "app.py")
        changed = [
            event for event in events(tmp_path, result.run_id) if event["event"] == "repo_changed"
        ]
        assert [(event["paths"], event["count"]) for event in changed] == [(["app.py"], 1)]


class TestRedaction:
    async def test_takes_secrets_out_of_the_files_and_the_log(
        self, tmp_path: Path, repo: Path
    ) -> None:
        text = (SCENARIOS / "clean_settle.yaml").read_text()
        text = text.replace(
            "An in-memory queue", f"The key {SECRET} in config.py and an in-memory queue"
        )
        scenario_file = tmp_path / "leaky.yaml"
        scenario_file.write_text(text)
        scenario = Scenario.from_yaml(scenario_file)
        environment_secret = "a1b2c3d4e5f6g7h8"
        ctx = context(repo, scenario.agent, scenario.scorer)
        ctx = RunContext(
            charter=ctx.charter,
            repo=repo,
            agent=ctx.agent,
            scorer=ctx.scorer,
            confirmer=ctx.confirmer,
            redactor=Redactor((environment_secret,)),
        )

        result = await start(
            store(tmp_path), ctx, initial_state(feature=f"Rotate {environment_secret}")
        )

        folder = store(tmp_path).folder(result.run_id)
        for name in ("plan.md", "ledger.md", "run.json", LOG):
            written = (folder / name).read_text()
            assert SECRET not in written
            assert environment_secret not in written
        assert f"The key {REDACTED} in config.py" in (folder / "run.json").read_text()
        assert f"Rotate {REDACTED}" in (folder / LOG).read_text()
        assert result.run_id == "2026-10-05-rotate-redacted"
        # The checkpoints keep the text as it was, for the run to carry on from.
        assert SECRET in next(iter(result.state["ledger"].values())).claim
