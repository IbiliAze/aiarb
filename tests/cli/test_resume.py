from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.cli.helpers import FEATURE, RUN_ID, SCENARIOS, aiarb, scenario
from tests.fakes import AgentStep, Scenario, ScriptedCodeAgent, ScriptedScorer, explanation

EVIDENCE = "Load tests show batches of 100 keep up."

SETTLING_OBJ_1: list[dict[str, Any]] = [
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
]
"""The planner and critic settling the decision scenario's OBJ-1, once reopened."""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    return path


def until_paused() -> Scenario:
    data: dict[str, Any] = yaml.safe_load((SCENARIOS / "decision.yaml").read_text())
    data["agent"] = [step for step in data["agent"] if step["schema"] != "Explanation"]
    return Scenario.from_data(data)


def reopening() -> Scenario:
    """The planner and critic settling OBJ-1, carrying on their sessions, and the
    explainer."""
    steps = [AgentStep.model_validate(step) for step in [*SETTLING_OBJ_1, explanation("OBJ-1")]]
    agent = ScriptedCodeAgent(steps, sessions=["session-1", "session-2"])
    return Scenario(agent, ScriptedScorer([]))


def explainer() -> Scenario:
    return Scenario.from_data({"agent": [explanation("OBJ-1")]})


def paused(repo: Path) -> None:
    outcome = aiarb(repo, "plan", "-f", FEATURE, "--max-rounds", "1", ports=until_paused())
    assert outcome.code == 3


def finished(repo: Path) -> None:
    paused(repo)
    assert aiarb(repo, "resume", "-d", "OBJ-1=Fair.", ports=explainer()).code == 0


class TestDecisions:
    def test_finishes_a_run_with_the_decisions_given(self, repo: Path) -> None:
        paused(repo)
        ports = explainer()

        outcome = aiarb(repo, "resume", RUN_ID, "--decide", "OBJ-1=Fairness first.", ports=ports)

        ports.assert_finished()
        assert outcome.code == 0
        assert outcome.json["status"] == "settled"
        decisions = repo / ".aiarb" / "runs" / RUN_ID / "decisions.md"
        assert "Fairness first." in decisions.read_text()

    def test_reads_a_decision_from_a_file(self, repo: Path, tmp_path: Path) -> None:
        paused(repo)
        answer = tmp_path / "answer.md"
        answer.write_text("Fairness first, after the board met.\n")

        outcome = aiarb(repo, "resume", "-d", f"obj-1=@{answer}", ports=explainer())

        assert outcome.code == 0
        decisions = repo / ".aiarb" / "runs" / RUN_ID / "decisions.md"
        assert "Fairness first, after the board met." in decisions.read_text()

    def test_without_decisions_shows_the_questions_again(self, repo: Path) -> None:
        paused(repo)

        outcome = aiarb(repo, "resume")

        assert outcome.code == 3
        assert "Waiting for your decisions on 1 trade-off." in outcome.stderr
        assert outcome.built_ports == [], "nothing runs, so no agent is set up"

    def test_asks_for_the_decisions_when_someone_is_there(self, repo: Path) -> None:
        paused(repo)

        outcome = aiarb(repo, "resume", ports=explainer(), can_ask=True, input="Fair.\n")

        assert outcome.code == 0

    @pytest.mark.parametrize(
        ("args", "message"),
        [
            (["-d", "OBJ-7=Fast."], "not waiting for a decision on OBJ-7; it asks about OBJ-1"),
            (["-d", "OBJ-1"], "--decide takes ID=TEXT"),
            (["-d", "OBJ-1=  "], "--decide OBJ-1 is blank"),
            (["-d", "OBJ-1=a", "-d", "obj-1=b"], "--decide OBJ-1 is given twice"),
            (["-d", "OBJ-1=@/no/such/file"], "cannot read the file /no/such/file"),
        ],
    )
    def test_refuses_decisions_it_cannot_use(
        self, repo: Path, args: list[str], message: str
    ) -> None:
        paused(repo)

        outcome = aiarb(repo, "resume", *args)

        assert outcome.code == 4
        assert message in outcome.stderr

    def test_refuses_decisions_for_a_finished_run(self, repo: Path) -> None:
        finished(repo)

        outcome = aiarb(repo, "resume", "-d", "OBJ-1=Fast.")

        assert outcome.code == 4
        assert "is not waiting for decisions" in outcome.stderr


class TestEvidence:
    def test_reopens_an_item_and_debates_it_again(self, repo: Path) -> None:
        finished(repo)
        ports = reopening()

        outcome = aiarb(repo, "resume", "-e", f"OBJ-1={EVIDENCE}", ports=ports)

        ports.assert_finished()
        assert outcome.code == 0
        assert outcome.json["rounds"] == 2
        assert "Planner answering the objections · round 2" in outcome.stderr

    def test_reopens_instead_of_deciding_a_waiting_trade_off(self, repo: Path) -> None:
        paused(repo)
        ports = reopening()

        outcome = aiarb(repo, "resume", "-e", f"OBJ-1={EVIDENCE}", ports=ports)

        ports.assert_finished()
        assert outcome.code == 0

    def test_an_unknown_item_cannot_be_reopened(self, repo: Path) -> None:
        finished(repo)

        outcome = aiarb(repo, "resume", "-e", f"OBJ-9={EVIDENCE}")

        assert outcome.code == 4
        assert "the run has no objection OBJ-9 (it has: OBJ-1)" in outcome.stderr

    def test_max_rounds_needs_evidence(self, repo: Path) -> None:
        finished(repo)

        outcome = aiarb(repo, "resume", "--max-rounds", "2")

        assert outcome.code == 4
        assert "give --evidence too" in outcome.stderr


class TestWhichRun:
    def test_a_finished_run_is_shown_as_it_is(self, repo: Path) -> None:
        finished(repo)

        outcome = aiarb(repo, "resume", RUN_ID)

        assert outcome.code == 0
        assert f"Run {RUN_ID} has finished." in outcome.stderr
        assert outcome.json["status"] == "settled"

    def test_a_stopped_run_carries_on_from_where_it_stopped(self, repo: Path) -> None:
        failing = Scenario.from_data(
            {"agent": [{"schema": "PlanDraft", "error": "fatal", "message": "Not signed in."}]}
        )
        assert aiarb(repo, "plan", "-f", FEATURE, ports=failing).code == 4
        ports = scenario("clean_settle")

        outcome = aiarb(repo, "resume", ports=ports)

        ports.assert_finished()
        assert outcome.code == 0

    def test_a_repo_without_runs_has_nothing_to_resume(self, repo: Path) -> None:
        outcome = aiarb(repo, "resume")

        assert outcome.code == 4
        assert "this repo has no runs yet" in outcome.stderr

    @pytest.mark.parametrize("run_id", ["2026-10-05-nothing", "../escape"])
    def test_an_unknown_run_cannot_be_resumed(self, repo: Path, run_id: str) -> None:
        outcome = aiarb(repo, "resume", run_id)

        assert outcome.code == 4
