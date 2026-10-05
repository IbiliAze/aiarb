import asyncio
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml

from aiarb.adapters.checkpointer import CHECKPOINTS, SqliteRunStore
from aiarb.config.charter import Charter
from aiarb.domain.ledger import Actor, Status
from aiarb.domain.outcome import RunStatus
from aiarb.graph.context import RunContext
from aiarb.graph.runner import NotWaiting, resume, start
from aiarb.graph.state import initial_state
from aiarb.ports.code_agent import AgentRequest, AgentResult, CodeAgent, OutputT
from aiarb.ports.run_store import RunNotFound
from aiarb.ports.scorer import Scorer
from tests.fakes import AgentStep, Scenario, ScorerStep, ScriptedCodeAgent, ScriptedScorer

SCENARIOS = Path(__file__).parent.parent / "fixtures" / "scenarios"
FEATURE = "Add a virtual waiting room."
RUN_ID = "2026-10-05-add-a-virtual-waiting-room"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    return path


def store(tmp_path: Path) -> SqliteRunStore:
    """A store on disk. Each call opens it afresh, as a new process would."""
    return SqliteRunStore(tmp_path / "runs", today=lambda: date(2026, 10, 5))


def context(repo: Path, agent: CodeAgent, scorer: Scorer, max_rounds: int = 4) -> RunContext:
    charter = Charter.model_validate({"debate": {"max_rounds": max_rounds}})
    return RunContext(charter=charter, repo=repo, agent=agent, scorer=scorer)


def scenario_context(repo: Path, name: str, max_rounds: int = 4) -> tuple[RunContext, Scenario]:
    scenario = Scenario.from_yaml(SCENARIOS / f"{name}.yaml")
    return context(repo, scenario.agent, scenario.scorer, max_rounds), scenario


def idle(repo: Path) -> RunContext:
    """A context whose ports fail the test if anything calls them."""
    return context(repo, ScriptedCodeAgent([]), ScriptedScorer([]), max_rounds=1)


async def paused_run(tmp_path: Path, repo: Path) -> str:
    ctx, scenario = scenario_context(repo, "decision", max_rounds=1)
    result = await start(store(tmp_path), ctx, initial_state(feature=FEATURE))
    scenario.assert_finished()
    assert result.paused
    return result.run_id


class TestDecisions:
    async def test_run_to_the_interrupt_reload_from_disk_resume_and_finish(
        self, tmp_path: Path, repo: Path
    ) -> None:
        ctx, scenario = scenario_context(repo, "decision", max_rounds=1)

        paused = await start(store(tmp_path), ctx, initial_state(feature=FEATURE))

        scenario.assert_finished()
        assert paused.run_id == RUN_ID
        assert (tmp_path / "runs" / RUN_ID / CHECKPOINTS).is_file()
        assert paused.request is not None
        [question] = paused.request["questions"]
        assert question["id"] == "OBJ-1"
        assert question["claim"] == "Small batches are fairer but make the queue slower."
        assert "Speed matters more." in question["detail"]
        assert paused.state["ledger"]["OBJ-1"].status is Status.AWAITING_DECISION
        assert paused.state["status"] is None

        finished = await resume(
            store(tmp_path), RUN_ID, idle(repo), {"OBJ-1": "  Fairness first: batches of 100. "}
        )

        assert not finished.paused
        item = finished.state["ledger"]["OBJ-1"]
        assert item.status is Status.DECIDED
        assert item.history[-1].actor is Actor.HUMAN
        assert item.history[-1].note == "Decided: Fairness first: batches of 100."
        assert finished.state["status"] is RunStatus.SETTLED
        assert finished.state["plan"] == paused.state["plan"]
        assert finished.state["costs"] == paused.state["costs"]

    async def test_resuming_without_answers_leaves_the_run_waiting(
        self, tmp_path: Path, repo: Path
    ) -> None:
        run_id = await paused_run(tmp_path, repo)

        again = await resume(store(tmp_path), run_id, idle(repo))

        assert again.paused
        assert again.request is not None
        assert [q["id"] for q in again.request["questions"]] == ["OBJ-1"]

    @pytest.mark.parametrize(
        ("answers", "problem"),
        [
            ({"OBJ-1": "Fair.", "OBJ-9": "Fast."}, "not asked: OBJ-9"),
            ({"OBJ-1": "   "}, "blank: OBJ-1"),
            ({"OBJ-9": "Fast."}, "unanswered: OBJ-1; not asked: OBJ-9"),
        ],
    )
    async def test_bad_answers_are_refused_and_the_questions_asked_again(
        self, tmp_path: Path, repo: Path, answers: dict[str, str], problem: str
    ) -> None:
        run_id = await paused_run(tmp_path, repo)

        refused = await resume(store(tmp_path), run_id, idle(repo), answers)

        assert refused.request is not None
        assert refused.request["problem"] == problem
        assert refused.state["ledger"]["OBJ-1"].status is Status.AWAITING_DECISION

        accepted = await resume(store(tmp_path), run_id, idle(repo), {"OBJ-1": "Fair."})

        assert accepted.state["status"] is RunStatus.SETTLED

    async def test_an_empty_set_of_answers_is_refused(self, tmp_path: Path, repo: Path) -> None:
        run_id = await paused_run(tmp_path, repo)

        with pytest.raises(ValueError, match="a decision for each question"):
            await resume(store(tmp_path), run_id, idle(repo), {})

    async def test_answers_after_a_refusal_need_not_be_the_first_try(
        self, tmp_path: Path, repo: Path
    ) -> None:
        run_id = await paused_run(tmp_path, repo)
        for wrong in ({"OBJ-9": "Fast."}, {"OBJ-1": " "}):
            assert (await resume(store(tmp_path), run_id, idle(repo), wrong)).paused

        finished = await resume(store(tmp_path), run_id, idle(repo), {"OBJ-1": "Fair."})

        assert finished.state["ledger"]["OBJ-1"].history[-1].note == "Decided: Fair."
        assert finished.state["status"] is RunStatus.SETTLED

    async def test_answers_for_a_finished_run_are_refused(self, tmp_path: Path, repo: Path) -> None:
        run_id = await paused_run(tmp_path, repo)
        await resume(store(tmp_path), run_id, idle(repo), {"OBJ-1": "Fair."})

        with pytest.raises(NotWaiting):
            await resume(store(tmp_path), run_id, idle(repo), {"OBJ-1": "Fast."})

    async def test_a_finished_run_resumes_as_it_was(self, tmp_path: Path, repo: Path) -> None:
        run_id = await paused_run(tmp_path, repo)
        finished = await resume(store(tmp_path), run_id, idle(repo), {"OBJ-1": "Fair."})

        again = await resume(store(tmp_path), run_id, idle(repo))

        assert again.state == finished.state
        assert not again.paused


async def test_an_unknown_run_cannot_be_resumed(tmp_path: Path, repo: Path) -> None:
    with pytest.raises(RunNotFound):
        await resume(store(tmp_path), "2026-10-05-nothing", idle(repo))


class HangsOnSecondRuling:
    """Passes calls to a scripted agent, until the critic's ruling in round 2,
    which never returns: the process is killed while it waits."""

    def __init__(self, agent: ScriptedCodeAgent) -> None:
        self.agent = agent
        self.rulings = 0
        self.hung = asyncio.Event()

    async def run(self, request: AgentRequest[OutputT]) -> AgentResult[OutputT]:
        if request.schema.__name__ == "Rulings":
            self.rulings += 1
            if self.rulings == 2:
                self.hung.set()
                await asyncio.Event().wait()
        return await self.agent.run(request)


async def test_a_run_killed_mid_round_resumes_from_its_last_checkpoint(
    tmp_path: Path, repo: Path
) -> None:
    data: dict[str, Any] = yaml.safe_load((SCENARIOS / "round_limit.yaml").read_text())
    agent_steps = [AgentStep.model_validate(step) for step in data["agent"]]
    scorer_steps = [ScorerStep.model_validate(step) for step in data["scorer"]]
    before_kill = ScriptedCodeAgent(agent_steps[:-1])
    hanging = HangsOnSecondRuling(before_kill)
    first = context(repo, hanging, ScriptedScorer(scorer_steps[:2]), max_rounds=2)

    task = asyncio.create_task(start(store(tmp_path), first, initial_state(feature=FEATURE)))
    await hanging.hung.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    before_kill.assert_finished()
    after_kill = ScriptedCodeAgent(agent_steps[-1:], sessions=["session-1", "session-2"])
    rescorer = ScriptedScorer(scorer_steps[2:])
    second = context(repo, after_kill, rescorer, max_rounds=2)

    result = await resume(store(tmp_path), RUN_ID, second)

    after_kill.assert_finished()
    rescorer.assert_finished()
    assert [call.schema.__name__ for call in after_kill.calls] == ["Rulings"]
    assert after_kill.calls[0].session_id == "session-2"
    assert result.state["round"] == 2
    assert result.state["ledger"]["OBJ-1"].status is Status.DEFERRED
    assert result.state["ledger"]["OBJ-2"].status is Status.RESOLVED
    assert result.state["status"] is RunStatus.SETTLED_WITH_OPEN_ITEMS
