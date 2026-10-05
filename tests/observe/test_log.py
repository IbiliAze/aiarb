import json
from datetime import UTC, datetime
from pathlib import Path

from aiarb.observe.log import RunLog, cost_details, logged_spend
from aiarb.observe.redact import REDACTED, Redactor
from aiarb.ports.calls import Cost


def run_log(tmp_path: Path) -> RunLog:
    return RunLog(
        tmp_path / "log.jsonl",
        Redactor(("s3cret-value-123",)),
        now=lambda: datetime(2026, 10, 5, 9, 30, tzinfo=UTC),
    )


def lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_appends_one_redacted_json_object_per_event(tmp_path: Path) -> None:
    log = run_log(tmp_path)

    log.write("opened", how="start", feature="Use s3cret-value-123 to sign in")
    log.write("step_started", step="scan", round=0)

    assert lines(log.path) == [
        {
            "time": "2026-10-05T09:30:00.000+00:00",
            "event": "opened",
            "how": "start",
            "feature": f"Use {REDACTED} to sign in",
        },
        {
            "time": "2026-10-05T09:30:00.000+00:00",
            "event": "step_started",
            "step": "scan",
            "round": 0,
        },
    ]


def test_a_run_that_carries_on_adds_to_its_log(tmp_path: Path) -> None:
    run_log(tmp_path).write("opened", how="start")
    run_log(tmp_path).write("opened", how="resume")

    assert [line["how"] for line in lines(tmp_path / "log.jsonl")] == ["start", "resume"]


class TestSpend:
    def test_adds_up_every_call(self, tmp_path: Path) -> None:
        log = run_log(tmp_path)
        log.write("call", role="planner", **cost_details(Cost(1.5, 100, 10)))
        log.write("step_finished", step="draft_plan", cost_usd=1.5)
        log.write("call", role="critic", error="failed", **cost_details(Cost(0.25, 50, 5)))

        assert log.spent() == Cost(1.75, 150, 15)

    def test_skips_a_line_cut_short(self, tmp_path: Path) -> None:
        log = run_log(tmp_path)
        log.write("call", **cost_details(Cost(1.0)))
        with log.path.open("a") as file:
            file.write('{"event": "call", "cost_u')

        assert logged_spend(log.path) == Cost(1.0)

    def test_a_missing_log_has_spent_nothing(self, tmp_path: Path) -> None:
        assert logged_spend(tmp_path / "missing.jsonl") == Cost()
