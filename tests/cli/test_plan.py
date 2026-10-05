import json
from pathlib import Path

import pytest

from aiarb import output
from tests.cli.helpers import FEATURE, RUN_ID, aiarb, scenario
from tests.fakes import Scenario

FATAL_DRAFT = {
    "agent": [{"schema": "PlanDraft", "error": "fatal", "message": "Claude Code is not installed."}]
}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    return path


def run_folder(repo: Path) -> Path:
    return repo / ".aiarb" / "runs" / RUN_ID


class TestExitCodes:
    @pytest.mark.parametrize(
        ("name", "rounds", "code", "status"),
        [
            ("clean_settle", "4", 0, "settled"),
            ("round_limit", "2", 1, "settled_with_open_items"),
            ("blocker_left_open", "4", 2, "not_ready"),
        ],
    )
    def test_say_how_the_run_came_out(
        self, repo: Path, name: str, rounds: str, code: int, status: str
    ) -> None:
        ports = scenario(name)

        outcome = aiarb(repo, "plan", "-f", FEATURE, "--max-rounds", rounds, ports=ports)

        ports.assert_finished()
        assert outcome.code == code
        assert (outcome.json["status"], outcome.json["exit_code"]) == (status, code)

    def test_a_run_waiting_for_decisions_exits_3(self, repo: Path) -> None:
        outcome = aiarb(
            repo, "plan", "-f", FEATURE, "--max-rounds", "1", ports=scenario("decision")
        )

        assert outcome.code == 3
        assert outcome.json["status"] == "waiting_for_decisions"

    def test_bad_usage_exits_4_not_2(self, repo: Path) -> None:
        assert aiarb(repo, "plan", "--max-rounds", "0").code == 4
        assert aiarb(repo, "plan", "--plan", str(repo / "missing.md")).code == 4


class TestFinished:
    def test_writes_json_to_stdout(self, repo: Path) -> None:
        outcome = aiarb(repo, "plan", "-f", FEATURE, ports=scenario("blocker_left_open"))

        data = outcome.json
        assert data["run_id"] == RUN_ID
        assert data["folder"] == str(run_folder(repo))
        assert data["files"] == {name: str(run_folder(repo) / name) for name in output.FILES}
        assert (data["open_items"], data["blockers"], data["downgraded"]) == (
            ["OBJ-1"],
            ["OBJ-1"],
            [],
        )
        assert data["rounds"] == 1
        assert all(Path(path).is_file() for path in data["files"].values())

    def test_sums_up_on_stderr(self, repo: Path) -> None:
        outcome = aiarb(repo, "plan", "-f", FEATURE, ports=scenario("blocker_left_open"))

        assert "Not ready: 1 open blocker. Do not build from this plan yet" in outcome.stderr
        assert "  OBJ-1  blocker  Checkout may stall on table locks" in outcome.stderr
        assert "  ledger.md     every objection, in plain English" in outcome.stderr

    def test_shows_each_step_with_the_round_and_what_it_spent(self, repo: Path) -> None:
        outcome = aiarb(repo, "plan", "-f", FEATURE, ports=scenario("clean_settle"))

        lines = outcome.stderr.splitlines()
        assert f"Run {RUN_ID}" in lines
        assert "  … Planner answering the objections · round 1 · 1 open · $3.02 so far" in lines
        assert "  ✓ Planner answered the objections · round 1 · 1 open  1m 00s  $0.50" in lines
        assert lines[-1].startswith("1 round · $3.77 · ")

    def test_keeps_the_round_limit_it_was_given(self, repo: Path) -> None:
        aiarb(repo, "plan", "-f", FEATURE, "--max-rounds", "2", ports=scenario("round_limit"))

        record = json.loads((run_folder(repo) / "run.json").read_text())
        assert record["charter"]["debate"] == {"max_rounds": 2}

    def test_keeps_runs_out_of_git(self, repo: Path) -> None:
        aiarb(repo, "plan", "-f", FEATURE, ports=scenario("clean_settle"))

        assert "runs/" in (repo / ".aiarb" / ".gitignore").read_text()


class TestInputs:
    def test_critiques_the_plans_it_is_given(self, repo: Path, tmp_path: Path) -> None:
        first, second = tmp_path / "one.md", tmp_path / "two.md"
        first.write_text("# Queue\n\nUse Redis.")
        second.write_text("# Rollout\n\nShip it.")
        ports = Scenario.from_data(
            {
                "agent": [
                    {"schema": "Critique", "contains": "Ship it.", "output": {"objections": []}}
                ]
            }
        )

        outcome = aiarb(repo, "plan", "-p", str(first), "-p", str(second), ports=ports)

        ports.assert_finished()
        assert outcome.code == 0
        plan = (repo / ".aiarb" / "runs" / "2026-10-05-plan" / "plan.md").read_text()
        assert "Use Redis." in plan
        assert "Ship it." in plan
        assert "Taking in your plan" in outcome.stderr

    def test_without_a_feature_the_planner_proposes_one(self, repo: Path) -> None:
        ports = scenario("clean_settle")

        outcome = aiarb(repo, "plan", ports=ports)

        assert "the planner will propose the most valuable next change" in outcome.stderr
        assert "Not given. Work out the most valuable next change" in ports.agent.calls[0].prompt

    def test_an_empty_plan_file_is_refused(self, repo: Path, tmp_path: Path) -> None:
        empty = tmp_path / "plan.md"
        empty.write_text("  \n")

        outcome = aiarb(repo, "plan", "-p", str(empty))

        assert outcome.code == 4
        assert f"the plan {empty} is empty" in outcome.stderr

    def test_a_broken_charter_is_refused_with_its_problems(self, repo: Path) -> None:
        (repo / ".aiarb").mkdir()
        (repo / ".aiarb" / "charter.yaml").write_text("debate:\n  max_round: 2\n")

        outcome = aiarb(repo, "plan", "-f", FEATURE)

        assert outcome.code == 4
        assert "did you mean 'max_rounds'?" in outcome.stderr
        assert outcome.result.stdout == ""

    def test_jev_without_a_key_is_refused(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
        (repo / ".aiarb").mkdir()
        (repo / ".aiarb" / "charter.yaml").write_text("scoring:\n  provider: jev\n")

        outcome = aiarb(repo, "plan", "-f", FEATURE, real_ports=True)

        assert outcome.code == 4
        assert "TYPESAFE_API_KEY is not set" in outcome.stderr


class TestPause:
    def test_shows_the_questions_and_how_to_answer_them(self, repo: Path) -> None:
        outcome = aiarb(
            repo, "plan", "-f", FEATURE, "--max-rounds", "1", ports=scenario("decision")
        )

        assert "Waiting for your decisions on 1 trade-off." in outcome.stderr
        assert "OBJ-1  Small batches are fairer but make the queue slower." in outcome.stderr
        assert "  Round 1, planner: Rebuttal: Speed matters more." in outcome.stderr
        assert f'aiarb resume {RUN_ID} --decide OBJ-1="..."' in outcome.stderr
        [question] = outcome.json["questions"]
        assert question["id"] == "OBJ-1"
        assert not (run_folder(repo) / "plan.md").exists()

    def test_asks_for_the_decisions_when_someone_is_there(self, repo: Path) -> None:
        ports = scenario("decision")

        outcome = aiarb(
            repo,
            "plan",
            "-f",
            FEATURE,
            "--max-rounds",
            "1",
            ports=ports,
            can_ask=True,
            input="Fairness first.\n",
        )

        ports.assert_finished()
        assert outcome.code == 0
        assert "The run needs your decision on 1 trade-off." in outcome.stderr
        assert "Fairness first." in (run_folder(repo) / "decisions.md").read_text()

    def test_a_blank_answer_leaves_the_decision_for_later(self, repo: Path) -> None:
        outcome = aiarb(
            repo,
            "plan",
            "-f",
            FEATURE,
            "--max-rounds",
            "1",
            ports=scenario("decision"),
            can_ask=True,
            input="\n",
        )

        assert outcome.code == 3
        assert f'aiarb resume {RUN_ID} --decide OBJ-1="..."' in outcome.stderr

    def test_no_input_never_asks(self, repo: Path) -> None:
        outcome = aiarb(
            repo,
            "plan",
            "-f",
            FEATURE,
            "--max-rounds",
            "1",
            "--no-input",
            ports=scenario("decision"),
            can_ask=True,
        )

        assert outcome.code == 3


class TestStopped:
    def test_a_failed_call_stops_the_run_and_says_how_to_carry_on(self, repo: Path) -> None:
        outcome = aiarb(repo, "plan", "-f", FEATURE, ports=Scenario.from_data(FATAL_DRAFT))

        assert outcome.code == 4
        assert "The run stopped part way: Claude Code is not installed." in outcome.stderr
        assert f"Carry on with: aiarb resume {RUN_ID}" in outcome.stderr
        data = outcome.json
        assert (data["status"], data["error"]) == ("stopped", "Claude Code is not installed.")
        assert "Traceback" not in outcome.stderr
