"""Real runs, recorded and replayed through the CLI on scripted agents.

Each scenario under `fixtures/scenarios/real/` is what the agents answered in a
real run on a real repo. Replaying it checks that the same answers still lead
to the same outcome and files.
"""

from pathlib import Path

import pytest

from tests.cli.helpers import SCENARIOS, aiarb
from tests.fakes import Scenario

REAL = SCENARIOS / "real"

FEATURES = {
    "ticketops_api_hover": (
        "On the venue map in the web client, make a section grow slightly when the pointer "
        "is over it, with a short CSS transition, and shrink back when the pointer leaves."
    ),
    "acs_app_scan_count": (
        'Show how many scans are listed in the Scans screen\'s title, for example "Scans (42)".'
    ),
    "acs_api_reader_count": (
        "Add GET /api/reader/count, which returns the number of readers and takes the same "
        "filters as GET /api/reader."
    ),
}


def test_every_recorded_run_is_replayed() -> None:
    assert sorted(path.stem for path in REAL.glob("*.yaml")) == sorted(FEATURES)


@pytest.mark.parametrize("name", sorted(FEATURES))
def test_a_lone_minor_note_settles_and_is_listed_in_the_plan(tmp_path: Path, name: str) -> None:
    # Each run's only objection was minor, so it skipped the debate and the
    # planner never saw it. plan.md is the only place a builder would find it.
    ports = Scenario.from_yaml(REAL / f"{name}.yaml")

    outcome = aiarb(tmp_path, "plan", "-f", FEATURES[name], ports=ports)

    ports.assert_finished()
    assert (outcome.code, outcome.json["status"], outcome.json["rounds"]) == (0, "settled", 0)
    plan_md = Path(outcome.json["files"]["plan.md"]).read_text()
    notes = plan_md.split("## Minor notes")[1].split("\n---\n")[0]
    assert "Rated minor, so not debated: the plan may not reflect them." in notes
    assert "- **OBJ-1: " in notes
    ledger_md = Path(outcome.json["files"]["ledger.md"]).read_text()
    assert "*Minor · " in ledger_md
    assert "noted: minor, not debated" in ledger_md
