"""ledger.md, decisions.md, run.json, and writing them."""

import json
from pathlib import Path

import pytest

from aiarb import output
from aiarb.config.charter import Charter
from aiarb.domain.ledger import PlanRef
from aiarb.domain.scoring import Severity
from aiarb.graph.state import initial_state
from aiarb.output import decisions, ledger, record
from tests.output.examples import (
    DECIDED,
    DEFERRED,
    EVERY_ENDING,
    NOTED,
    RESOLVED,
    RULED,
    raised,
    report,
)


class TestLedger:
    def test_opens_with_the_summary_and_a_count_by_status(self) -> None:
        text = ledger.render(report(*EVERY_ENDING))

        assert text.startswith(
            "# Ledger\n\nThe plan holds buyers in Redis. One question is open.\n\n"
            "**Not ready.** 5 objections: 1 deferred, 1 ruled, 1 decided, 1 resolved, 1 noted."
        )

    def test_explains_each_objection_beside_the_ledgers_facts(self) -> None:
        text = ledger.render(report(DEFERRED))

        entry = text.split("## OBJ-1: Title of OBJ-1\n\n")[1]
        assert entry.startswith(
            "*Blocker · factual · deferred: settle before building*\n\n"
            "**The problem.** Problem of OBJ-1.\n\n"
            "**In production.** Consequence of OBJ-1.\n\n"
            "**How it ended.** Outcome of OBJ-1.\n\n"
            '**Evidence.** plan § Queue storage: "Keep queue places in Redis"'
        )

    def test_keeps_the_whole_debate_under_each_entry(self) -> None:
        text = ledger.render(report(RESOLVED))

        assert "<summary>The debate</summary>" in text
        assert "- Round 0, critic: Raised.\n" in text
        assert "- Round 1, planner: Fix: Places persist in Redis.\n" in text
        assert "- Round 1, critic: Redis keeps them. (plan § Queue storage)" in text
        assert "**Evidence.** `app/queue.py:10-24`" in text

    def test_puts_open_items_first_most_severe_first(self) -> None:
        moderate = raised(
            "OBJ-9", "A moderate risk.", PlanRef("Rollout"), severity=Severity.MODERATE
        )

        text = ledger.render(report(RESOLVED, moderate, DEFERRED))

        assert text.index("## OBJ-1") < text.index("## OBJ-9") < text.index("## OBJ-2")

    def test_flags_an_open_item_rated_lower_though_unsettled(self) -> None:
        dropped = DEFERRED.rescore(Severity.MAJOR, round=2, note="Lower after the debate.")

        text = ledger.render(report(dropped))

        assert "**Flagged.** Rated blocker earlier and major now, though nobody settled it." in (
            text
        )

    def test_without_an_explanation_gives_the_claim_and_how_it_ended(self) -> None:
        text = ledger.render(report(RULED, explained=False))

        assert "## OBJ-3: The batch size is hard-coded\n" in text
        assert "**The claim.** The batch size is hard-coded." in text
        assert "**How it ended.** Ruled for the critic: Read it from config." in text

    def test_a_run_with_no_objections_says_so(self) -> None:
        assert ledger.render(report()) == "# Ledger\n\n**Settled.** No objections were raised.\n"


class TestDecisions:
    def test_gives_each_sides_last_word_and_the_decision(self) -> None:
        text = decisions.render(report(DECIDED, RULED))

        assert text == (
            "# Decisions\n\nThe trade-offs you decided in this run.\n\n"
            "## OBJ-4: Title of OBJ-4\n\n"
            "**The trade-off.** Problem of OBJ-4.\n\n"
            "**The planner's last word.** Rebuttal: It is fine.\n\n"
            "**The critic's last word.** Not persuaded.\n\n"
            "**Your decision** (round 1). Fairness first.\n"
        )

    def test_a_run_with_no_decisions_says_so(self) -> None:
        assert "No trade-offs needed your decision" in decisions.render(report(RULED))


class TestRecord:
    def test_holds_the_whole_run_as_data(self) -> None:
        data = json.loads(record.render(report(*EVERY_ENDING)))

        assert data["format"] == record.FORMAT
        assert data["run_id"] == "2026-10-05-waiting-room"
        assert (data["status"], data["exit_code"]) == ("not_ready", 2)
        assert data["open_items"] == ["OBJ-1"]
        assert data["blockers"] == ["OBJ-1"]
        assert [item["id"] for item in data["objections"]] == [
            "OBJ-1",
            "OBJ-2",
            "OBJ-3",
            "OBJ-4",
            "OBJ-5",
        ]
        assert data["costs"]["planner"] == {"usd": 1.25, "input_tokens": 1000, "output_tokens": 0}
        assert data["total_cost"]["usd"] == 1.26
        assert data["charter"]["debate"] == {"max_rounds": 4}

    def test_records_each_objection_with_its_history(self) -> None:
        data = json.loads(record.render(report(RESOLVED)))

        [item] = data["objections"]
        assert item["evidence"] == [
            {"kind": "code", "path": "app/queue.py", "start_line": 10, "end_line": 24}
        ]
        assert item["explanation"]["title"] == "Title of OBJ-2"
        assert item["history"][-1] == {
            "round": 1,
            "actor": "critic",
            "status": "resolved",
            "severity": "major",
            "note": "Redis keeps them.",
            "refs": [],
            "resolution": {
                "kind": "plan_change",
                "refs": [{"kind": "plan", "section": "Queue storage", "quote": None}],
            },
        }

    def test_records_the_round_limit_the_run_was_given(self) -> None:
        state = initial_state(feature="Add a virtual waiting room.", max_rounds=2)

        data = json.loads(record.render(output.Report.of(state, Charter(), run_id="run")))

        assert data["charter"]["debate"] == {"max_rounds": 2}

    def test_without_an_explanation_has_none(self) -> None:
        data = json.loads(record.render(report(NOTED, explained=False)))

        assert data["summary"] is None
        assert data["objections"][0]["explanation"] is None


class TestWrite:
    def test_writes_every_file_whole(self, tmp_path: Path) -> None:
        folder = tmp_path / "run"

        output.write(folder, output.render(report(*EVERY_ENDING)))

        assert sorted(path.name for path in folder.iterdir()) == sorted(output.FILES)

    def test_replaces_what_an_earlier_write_left(self, tmp_path: Path) -> None:
        output.write(tmp_path, {"plan.md": "old"})

        output.write(tmp_path, {"plan.md": "new"})

        assert (tmp_path / "plan.md").read_text() == "new"
        assert [path.name for path in tmp_path.iterdir()] == ["plan.md"]

    def test_a_failed_write_keeps_the_previous_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        output.write(tmp_path, {"plan.md": "old"})

        def fail(*_: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr("aiarb.output.os.replace", fail)
        with pytest.raises(OSError, match="disk full"):
            output.write(tmp_path, {"plan.md": "new"})

        assert (tmp_path / "plan.md").read_text() == "old"
