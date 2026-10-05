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
from aiarb.graph.runner import CannotReopen, NotWaiting, load, reopen, resume, start
from aiarb.graph.state import RunState, initial_state, total_cost
from aiarb.ports.code_agent import AgentRequest, AgentResult, CodeAgent, OutputT
from aiarb.ports.run_store import RunNotFound
from aiarb.ports.scorer import Scorer
from tests.fakes import (
    AgentStep,
    Scenario,
    ScorerStep,
    ScriptedCodeAgent,
    ScriptedScorer,
    explanation,
)

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
    return RunContext(charter=charter, repo=repo, agent=agent, scorer=scorer, confirmer=scorer)


def scenario_context(repo: Path, name: str, max_rounds: int = 4) -> tuple[RunContext, Scenario]:
    scenario = Scenario.from_yaml(SCENARIOS / f"{name}.yaml")
    return context(repo, scenario.agent, scenario.scorer, max_rounds), scenario


def idle(repo: Path) -> RunContext:
    """A context whose ports fail the test if anything calls them."""
    return context(repo, ScriptedCodeAgent([]), ScriptedScorer([]), max_rounds=1)


def finishing(repo: Path) -> RunContext:
    """A context for the rest of the decision scenario: once decided, the explainer."""
    explainer = ScriptedCodeAgent([AgentStep.model_validate(explanation("OBJ-1"))])
    return context(repo, explainer, ScriptedScorer([]), max_rounds=1)


def until_paused(repo: Path) -> tuple[RunContext, Scenario]:
    """The decision scenario up to its pause, before the explainer is called."""
    data: dict[str, Any] = yaml.safe_load((SCENARIOS / "decision.yaml").read_text())
    data["agent"] = [step for step in data["agent"] if step["schema"] != "Explanation"]
    scenario = Scenario.from_data(data)
    return context(repo, scenario.agent, scenario.scorer, max_rounds=1), scenario


async def paused_run(tmp_path: Path, repo: Path) -> str:
    ctx, scenario = until_paused(repo)
    result = await start(store(tmp_path), ctx, initial_state(feature=FEATURE))
    scenario.assert_finished()
    assert result.paused
    return result.run_id


class TestDecisions:
    async def test_run_to_the_interrupt_reload_from_disk_resume_and_finish(
        self, tmp_path: Path, repo: Path
    ) -> None:
        ctx, scenario = until_paused(repo)

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

        assert not (tmp_path / "runs" / RUN_ID / "plan.md").exists()

        finished = await resume(
            store(tmp_path),
            RUN_ID,
            finishing(repo),
            {"OBJ-1": "  Fairness first: batches of 100. "},
        )

        assert not finished.paused
        item = finished.state["ledger"]["OBJ-1"]
        assert item.status is Status.DECIDED
        assert item.history[-1].actor is Actor.HUMAN
        assert item.history[-1].note == "Decided: Fairness first: batches of 100."
        assert finished.state["status"] is RunStatus.SETTLED
        assert finished.state["plan"] == paused.state["plan"]
        assert finished.state["costs"].keys() - paused.state["costs"].keys() == {"explainer"}
        folder = tmp_path / "runs" / RUN_ID
        assert {"plan.md", "ledger.md", "decisions.md", "run.json"} <= {
            path.name for path in folder.iterdir()
        }
        assert "Fairness first: batches of 100." in (folder / "decisions.md").read_text()

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

        accepted = await resume(store(tmp_path), run_id, finishing(repo), {"OBJ-1": "Fair."})

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

        finished = await resume(store(tmp_path), run_id, finishing(repo), {"OBJ-1": "Fair."})

        assert finished.state["ledger"]["OBJ-1"].history[-1].note == "Decided: Fair."
        assert finished.state["status"] is RunStatus.SETTLED

    async def test_answers_for_a_finished_run_are_refused(self, tmp_path: Path, repo: Path) -> None:
        run_id = await paused_run(tmp_path, repo)
        await resume(store(tmp_path), run_id, finishing(repo), {"OBJ-1": "Fair."})

        with pytest.raises(NotWaiting):
            await resume(store(tmp_path), run_id, idle(repo), {"OBJ-1": "Fast."})

    async def test_a_finished_run_resumes_as_it_was(self, tmp_path: Path, repo: Path) -> None:
        run_id = await paused_run(tmp_path, repo)
        finished = await resume(store(tmp_path), run_id, finishing(repo), {"OBJ-1": "Fair."})

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
    before_kill = ScriptedCodeAgent(agent_steps[:-2])
    hanging = HangsOnSecondRuling(before_kill)
    first = context(repo, hanging, ScriptedScorer(scorer_steps[:2]), max_rounds=2)

    task = asyncio.create_task(start(store(tmp_path), first, initial_state(feature=FEATURE)))
    await hanging.hung.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    before_kill.assert_finished()
    after_kill = ScriptedCodeAgent(agent_steps[-2:], sessions=["session-1", "session-2"])
    rescorer = ScriptedScorer(scorer_steps[2:])
    second = context(repo, after_kill, rescorer, max_rounds=2)

    result = await resume(store(tmp_path), RUN_ID, second)

    after_kill.assert_finished()
    rescorer.assert_finished()
    assert [call.schema.__name__ for call in after_kill.calls] == ["Rulings", "Explanation"]
    assert after_kill.calls[0].session_id == "session-2"
    assert result.state["round"] == 2
    assert result.state["ledger"]["OBJ-1"].status is Status.DEFERRED
    assert result.state["ledger"]["OBJ-2"].status is Status.RESOLVED
    assert result.state["status"] is RunStatus.SETTLED_WITH_OPEN_ITEMS


TWO_TRADE_OFFS: dict[str, Any] = {
    "agent": [
        {"schema": "PlanDraft", "output": {"plan": "# Queue\n\nAdmit users in batches of 500."}},
        {
            "schema": "Critique",
            "output": {
                "objections": [
                    {
                        "claim": f"Trade-off {n}.",
                        "type": "trade_off",
                        "severity": "moderate",
                        "evidence": [{"kind": "plan", "section": "Queue"}],
                    }
                    for n in (1, 2)
                ]
            },
        },
        {
            "schema": "Responses",
            "output": {
                "plan": "# Queue\n\nAdmit users in batches of 500.",
                "answers": [
                    {
                        "id": id,
                        "response": "rebuttal",
                        "note": "Speed matters more.",
                        "evidence": [{"kind": "code", "path": "src/queue.py"}],
                    }
                    for id in ("OBJ-1", "OBJ-2")
                ],
            },
        },
        {
            "schema": "Rulings",
            "output": {
                "rulings": [
                    {"id": id, "verdict": "reject", "note": "Not persuaded."}
                    for id in ("OBJ-1", "OBJ-2")
                ]
            },
        },
    ],
    "scorer": [
        {"call": "score", "contains": "OBJ-1", "value": 0.4},
        {"call": "score", "contains": "OBJ-2", "value": 0.4},
    ],
}
"""Run with max_rounds 1: two trade-offs, both left for the user."""

EVIDENCE = "Load tests show batches of 100 keep up."


def settling_obj_1(*more: dict[str, Any]) -> ScriptedCodeAgent:
    """The planner and critic settling OBJ-1 once it is reopened, then `more`."""
    steps = [
        {
            "schema": "Responses",
            "contains": EVIDENCE,
            "output": {
                "plan": "# Queue\n\nAdmit users in batches of 100.",
                "answers": [
                    {
                        "id": "OBJ-1",
                        "response": "fix",
                        "note": "Batches of 100.",
                        "changed": [{"kind": "plan", "section": "Queue"}],
                    }
                ],
            },
        },
        {
            "schema": "Rulings",
            "output": {
                "rulings": [
                    {
                        "id": "OBJ-1",
                        "verdict": "accept",
                        "note": "Fair and fast enough.",
                        "resolution": {
                            "kind": "plan_change",
                            "sections": [{"kind": "plan", "section": "Queue"}],
                        },
                    }
                ]
            },
        },
        *more,
    ]
    return ScriptedCodeAgent(
        [AgentStep.model_validate(step) for step in steps], sessions=["session-1", "session-2"]
    )


async def two_trade_offs_waiting(tmp_path: Path, repo: Path) -> str:
    scenario = Scenario.from_data(TWO_TRADE_OFFS)
    ctx = context(repo, scenario.agent, scenario.scorer, max_rounds=1)
    paused = await start(store(tmp_path), ctx, initial_state(feature=FEATURE))
    scenario.assert_finished()
    assert paused.request is not None
    assert [q["id"] for q in paused.request["questions"]] == ["OBJ-1", "OBJ-2"]
    return paused.run_id


class TestReopen:
    async def test_new_evidence_reopens_an_item_for_a_debate_of_its_own(
        self, tmp_path: Path, repo: Path
    ) -> None:
        run_id = await two_trade_offs_waiting(tmp_path, repo)
        agent = settling_obj_1()

        result = await reopen(
            store(tmp_path),
            run_id,
            context(repo, agent, ScriptedScorer([]), 1),
            {"OBJ-1": EVIDENCE},
        )

        agent.assert_finished()
        item = result.state["ledger"]["OBJ-1"]
        reopening = item.history[-3]
        assert (reopening.actor, reopening.status, reopening.round) == (Actor.HUMAN, Status.OPEN, 1)
        assert reopening.note == f"Reopened by the user with new evidence: {EVIDENCE}"
        assert item.status is Status.RESOLVED
        assert (result.state["round"], result.state["debate_start"]) == (2, 1)
        assert result.state["plan"] == "# Queue\n\nAdmit users in batches of 100."
        assert agent.calls[0].session_id == "session-1"

    async def test_trade_offs_left_waiting_are_asked_once_the_new_debate_ends(
        self, tmp_path: Path, repo: Path
    ) -> None:
        run_id = await two_trade_offs_waiting(tmp_path, repo)
        ctx = context(repo, settling_obj_1(), ScriptedScorer([]), 1)

        result = await reopen(store(tmp_path), run_id, ctx, {"OBJ-1": EVIDENCE})

        assert result.request is not None
        assert [q["id"] for q in result.request["questions"]] == ["OBJ-2"]
        explainer = ScriptedCodeAgent([AgentStep.model_validate(explanation("OBJ-1", "OBJ-2"))])
        finished = await resume(
            store(tmp_path),
            run_id,
            context(repo, explainer, ScriptedScorer([]), 1),
            {"OBJ-2": "Speed first."},
        )
        assert finished.finished
        assert finished.state["status"] is RunStatus.SETTLED

    async def test_decides_the_trade_offs_it_does_not_reopen(
        self, tmp_path: Path, repo: Path
    ) -> None:
        run_id = await two_trade_offs_waiting(tmp_path, repo)
        agent = settling_obj_1(explanation("OBJ-1", "OBJ-2"))

        result = await reopen(
            store(tmp_path),
            run_id,
            context(repo, agent, ScriptedScorer([]), 1),
            {"OBJ-1": EVIDENCE},
            answers={"OBJ-2": "Speed first."},
        )

        agent.assert_finished()
        assert result.finished
        assert result.state["ledger"]["OBJ-2"].history[-1].note == "Decided: Speed first."
        assert result.state["status"] is RunStatus.SETTLED
        assert (tmp_path / "runs" / run_id / "plan.md").is_file()

    async def test_reopens_a_finished_run_and_writes_its_files_again(
        self, tmp_path: Path, repo: Path
    ) -> None:
        run_id = await paused_run(tmp_path, repo)
        await resume(store(tmp_path), run_id, finishing(repo), {"OBJ-1": "Fair."})
        before = (tmp_path / "runs" / run_id / "decisions.md").read_text()
        agent = settling_obj_1(explanation("OBJ-1"))

        result = await reopen(
            store(tmp_path),
            run_id,
            context(repo, agent, ScriptedScorer([]), 1),
            {"OBJ-1": EVIDENCE},
            max_rounds=3,
        )

        agent.assert_finished()
        assert result.state["max_rounds"] == 3
        assert result.state["ledger"]["OBJ-1"].status is Status.RESOLVED
        after = (tmp_path / "runs" / run_id / "decisions.md").read_text()
        assert "Fair." in before
        assert "No trade-offs needed your decision" in after

    @pytest.mark.parametrize(
        ("evidence", "answers", "error", "message"),
        [
            ({"OBJ-7": EVIDENCE}, None, CannotReopen, "no objection OBJ-7"),
            ({"OBJ-1": "  "}, None, CannotReopen, "give the new evidence"),
            ({"OBJ-1": EVIDENCE}, {"OBJ-1": "Fast."}, CannotReopen, "not both"),
            ({"OBJ-1": EVIDENCE}, {"OBJ-2": " "}, ValueError, "blank"),
            ({}, None, ValueError, "at least one item"),
        ],
    )
    async def test_refuses_what_it_cannot_do(
        self,
        tmp_path: Path,
        repo: Path,
        evidence: dict[str, str],
        answers: dict[str, str] | None,
        error: type[Exception],
        message: str,
    ) -> None:
        run_id = await two_trade_offs_waiting(tmp_path, repo)

        with pytest.raises(error, match=message):
            await reopen(store(tmp_path), run_id, idle(repo), evidence, answers=answers)

        assert (await load(store(tmp_path), run_id)).paused

    async def test_decisions_need_a_run_waiting_for_them(self, tmp_path: Path, repo: Path) -> None:
        run_id = await paused_run(tmp_path, repo)
        await resume(store(tmp_path), run_id, finishing(repo), {"OBJ-1": "Fair."})

        with pytest.raises(NotWaiting):
            await reopen(
                store(tmp_path), run_id, idle(repo), {"OBJ-1": EVIDENCE}, answers={"OBJ-1": "x"}
            )


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []
        self.costs: list[float] = []

    def opened(self, run_id: str, state: RunState) -> None:
        self.events.append(("opened", run_id))

    def started(self, step: str, state: RunState) -> None:
        self.events.append(("started", step))

    def finished(self, step: str, state: RunState) -> None:
        self.events.append(("finished", step))
        self.costs.append(total_cost(state).usd)


class TestProgress:
    async def test_tells_of_each_step_as_it_starts_and_finishes(
        self, tmp_path: Path, repo: Path
    ) -> None:
        ctx, _ = scenario_context(repo, "clean_settle")
        recorder = Recorder()

        await start(store(tmp_path), ctx, initial_state(feature=FEATURE), progress=recorder)

        steps = ["scan", "draft_plan", "critique", "triage", "respond", "rule"]
        steps += ["rescore", "explain", "finalise"]
        assert recorder.events == [
            ("opened", RUN_ID),
            *(event for step in steps for event in (("started", step), ("finished", step))),
        ]
        assert recorder.costs[steps.index("draft_plan")] == 2
        assert recorder.costs[-1] == 3.77

    async def test_a_pause_starts_the_decision_step_without_finishing_it(
        self, tmp_path: Path, repo: Path
    ) -> None:
        ctx, _ = until_paused(repo)
        recorder = Recorder()

        await start(store(tmp_path), ctx, initial_state(feature=FEATURE), progress=recorder)

        assert recorder.events[-2:] == [("finished", "judge"), ("started", "decide")]


class TestLoad:
    async def test_tells_a_paused_run_from_a_finished_one(self, tmp_path: Path, repo: Path) -> None:
        run_id = await paused_run(tmp_path, repo)

        paused = await load(store(tmp_path), run_id)
        await resume(store(tmp_path), run_id, finishing(repo), {"OBJ-1": "Fair."})
        finished = await load(store(tmp_path), run_id)

        assert (paused.paused, paused.finished, paused.stopped) == (True, False, False)
        assert (finished.paused, finished.finished, finished.stopped) == (False, True, False)
        assert finished.state["status"] is RunStatus.SETTLED

    async def test_an_unknown_run_cannot_be_loaded(self, tmp_path: Path) -> None:
        with pytest.raises(RunNotFound):
            await load(store(tmp_path), "2026-10-05-nothing")
