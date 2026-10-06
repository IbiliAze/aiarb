from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

from aiarb.cli.app import app
from aiarb.cli.setup import Ports, Setup
from aiarb.config.charter import Charter, load_charter
from tests.cli.helpers import FEATURE, RUN_ID, aiarb, scenario
from tests.cli.test_resume import until_paused
from tests.fakes import Scenario, explanation


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    monkeypatch.chdir(path)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    return path


class TestGuided:
    def test_writes_a_charter_asks_what_to_build_and_runs(self, repo: Path) -> None:
        ports = scenario("clean_settle", asking=True)
        answers = ["y", FEATURE, "", "", "y"]

        outcome = aiarb(repo, ports=ports, can_ask=True, input="\n".join(answers) + "\n")

        ports.assert_finished()
        assert outcome.code == 0
        assert load_charter(repo) == Charter()
        assert FEATURE in ports.agent.calls[0].prompt
        assert "The planner is claude-opus-5-5 and the critic claude-sonnet-5-5." in (
            outcome.stderr
        )
        assert (repo / ".aiarb" / "runs" / RUN_ID / "plan.md").is_file()

    def test_takes_a_plan_and_a_round_limit(self, repo: Path, tmp_path: Path) -> None:
        plan = tmp_path / "my plan.md"
        plan.write_text("# Queue\n\nUse Redis.")
        ports = Scenario.from_data(
            {
                "agent": [
                    {"schema": "Critique", "contains": "Use Redis.", "output": {"objections": []}}
                ]
            }
        )
        answers = ["n", "", "missing.md", f'"{plan}"', "2", "y"]

        outcome = aiarb(repo, ports=ports, can_ask=True, input="\n".join(answers) + "\n")

        ports.assert_finished()
        assert outcome.code == 0
        assert "No such file:" in outcome.stderr
        assert not (repo / ".aiarb" / "charter.yaml").exists()

    def test_offers_to_answer_a_run_that_is_waiting(self, repo: Path) -> None:
        aiarb(repo, "plan", "-f", FEATURE, "--max-rounds", "1", ports=until_paused())
        ports = Scenario.from_data({"agent": [explanation("OBJ-1")]})

        outcome = aiarb(repo, ports=ports, can_ask=True, input="n\ny\nFair.\n")

        ports.assert_finished()
        assert f"Run {RUN_ID} is waiting for your decisions on 1 trade-off." in outcome.stderr
        assert outcome.code == 0

    def test_can_be_left_without_starting(self, repo: Path) -> None:
        outcome = aiarb(repo, can_ask=True, input="n\n\n\n\nn\n")

        assert outcome.code == 0
        assert not (repo / ".aiarb" / "runs").exists()

    def test_without_a_terminal_shows_the_help(self, repo: Path) -> None:
        outcome = aiarb(repo)

        assert outcome.code == 0
        assert "Usage: aiarb" in outcome.result.stdout


def test_an_unexpected_error_exits_4_with_its_traceback(tmp_path: Path) -> None:
    def broken(charter: Charter, repo: Path) -> Ports:
        raise RuntimeError("a bug")

    setup = Setup(ports=broken, console=Console(stderr=True, width=200, color_system=None))

    result = CliRunner().invoke(app, ["plan", "--repo", str(tmp_path)], obj=setup)

    assert result.exit_code == 4
    assert "RuntimeError: a bug" in result.stderr
