"""The commands' safety: the budget, secrets, and leaving the repo alone."""

import hashlib
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from aiarb.observe.redact import REDACTED, Redactor
from tests.cli.helpers import FEATURE, RUN_ID, SCENARIOS, aiarb, scenario
from tests.cli.test_resume import explainer, until_paused
from tests.fakes import Scenario
from tests.scan import repos

FIXTURE_REPO = Path(__file__).parent.parent / "fixtures" / "repos" / "python-api"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    return path


def set_budget(repo: Path, per_run: float) -> None:
    charter = repo / ".aiarb" / "charter.yaml"
    charter.parent.mkdir(exist_ok=True)
    budget = {"per_call": min(per_run, 10), "per_run": per_run}
    charter.write_text(yaml.safe_dump({"budget": budget}))


class TestBudget:
    def test_a_run_over_its_budget_stops_with_exit_5(self, repo: Path) -> None:
        set_budget(repo, 2.5)

        outcome = aiarb(repo, "plan", "-f", FEATURE, ports=scenario("clean_settle"))

        assert outcome.code == 5
        data = outcome.json
        assert (data["status"], data["exit_code"], data["budget_usd"]) == ("over_budget", 5, 2.5)
        assert data["error"] == (
            "the run has spent $3.00 of its $2.50 budget (budget.per_run in the charter)"
        )
        assert data["cost_usd"] == 3
        assert "Stopped at the budget: the run has spent $3.00 of its $2.50." in outcome.stderr
        assert (
            "Raise budget.per_run in .aiarb/charter.yaml, then carry on with: "
            f"aiarb resume {RUN_ID}"
        ) in outcome.stderr

    def test_carries_on_once_the_budget_is_raised(self, repo: Path) -> None:
        set_budget(repo, 2.5)
        ports = scenario("clean_settle")
        assert aiarb(repo, "plan", "-f", FEATURE, ports=ports).code == 5

        set_budget(repo, 10)
        outcome = aiarb(repo, "resume", ports=ports)

        ports.assert_finished()
        assert outcome.code == 0
        assert outcome.json["cost_usd"] == 3.77


class TestSecrets:
    def test_are_taken_out_of_what_the_commands_print(self, repo: Path) -> None:
        secret = "q1w2e3r4t5y6u7i8"
        data: dict[str, Any] = yaml.safe_load((SCENARIOS / "decision.yaml").read_text())
        data["agent"] = [step for step in data["agent"] if step["schema"] != "Explanation"]
        objection = data["agent"][1]["output"]["objections"][0]
        objection["claim"] = f"Batches signed with {secret} are fairer but slower."

        outcome = aiarb(
            repo,
            "plan",
            "-f",
            FEATURE,
            "--max-rounds",
            "1",
            ports=Scenario.from_data(data),
            redactor=Redactor((secret,)),
        )

        assert outcome.code == 3
        assert secret not in outcome.stderr
        assert secret not in outcome.result.stdout
        assert f"Batches signed with {REDACTED} are fairer" in outcome.stderr
        assert outcome.json["questions"][0]["claim"].startswith(f"Batches signed with {REDACTED}")


def fingerprint(root: Path) -> dict[str, str]:
    """Every file under `root`, git's own included, by its content's hash, apart
    from aiarb's folder."""
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".aiarb" not in path.relative_to(root).parts
    }


def test_a_run_leaves_the_repo_untouched_apart_from_aiarbs_folder(tmp_path: Path) -> None:
    repo = shutil.copytree(FIXTURE_REPO, tmp_path / "repo")
    repos.init(repo)
    repos.commit(repo, {}, "Start")
    before = fingerprint(repo)

    waiting = aiarb(repo, "plan", "-f", FEATURE, "--max-rounds", "1", ports=until_paused())
    finished = aiarb(repo, "resume", "--decide", "OBJ-1=Fairness first.", ports=explainer())

    assert (waiting.code, finished.code) == (3, 0)
    assert "changed while the run went" not in waiting.stderr + finished.stderr
    assert fingerprint(repo) == before
    assert sorted(path.name for path in (repo / ".aiarb").iterdir()) == [
        ".gitignore",
        "cache",
        "runs",
    ]
    run = repo / ".aiarb" / "runs" / RUN_ID
    assert sorted(path.name for path in run.iterdir()) == [
        "checkpoints.sqlite",
        "decisions.md",
        "ledger.md",
        "log.jsonl",
        "plan.md",
        "run.json",
    ]
