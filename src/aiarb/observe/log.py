"""A run's log: `log.jsonl` in the run's folder, one JSON object per line.

Each line has the time, in UTC, and the event, with its details:

    opened         the run started, or carried on: how, the round, budget, spent so far
    step_started   a step began
    step_finished  a step finished: how long it took and what it spent
    call           one outside call: who made it, on what model, how long it took,
                   what it spent, and how it failed if it did
    over_budget    a call was refused because the run has spent its budget
    repo_changed   files outside .aiarb/ changed while the run went
    paused         the run is waiting for the user's decisions
    stopped        the run stopped part way, at a step that failed, or was interrupted
    finished       the run finished: how it came out and what it spent

Lines are appended as things happen, so a run that is killed keeps its log up
to that point, and a run that carries on adds to it. Every string is redacted.

The log is also the record of what the run has spent. The state counts what
each finished step spent; the log counts every call, including those in steps
that failed and were retried, which the budget must count too.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from aiarb.ports.calls import Cost

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from aiarb.observe.redact import Redactor

LOG = "log.jsonl"
"""The log's name in the run's folder."""


def _now() -> datetime:
    return datetime.now(UTC)


class RunLog:
    def __init__(
        self, path: Path, redactor: Redactor, *, now: Callable[[], datetime] = _now
    ) -> None:
        self.path = path
        self._redactor = redactor
        self._now = now

    def write(self, event: str, **details: Any) -> None:
        record = {
            "time": self._now().isoformat(timespec="milliseconds"),
            "event": event,
            **self._redactor.data(details),
        }
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self.path.open("a", encoding="utf-8") as file:
            file.write(line + "\n")

    def spent(self) -> Cost:
        return logged_spend(self.path)


def logged_spend(path: Path) -> Cost:
    """What every call in the log spent. A line cut short by a kill is skipped."""
    total = Cost()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return total
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("event") == "call":
            total += _cost(record)  # pyright: ignore[reportUnknownArgumentType]
    return total


def cost_details(cost: Cost) -> dict[str, Any]:
    """How a call's cost is written in the log."""
    return {
        "cost_usd": round(cost.usd, 6),
        "input_tokens": cost.input_tokens,
        "output_tokens": cost.output_tokens,
    }


def _cost(record: dict[str, Any]) -> Cost:
    def number(key: str) -> float:
        value = record.get(key)
        return value if isinstance(value, int | float) and value > 0 else 0

    return Cost(
        usd=float(number("cost_usd")),
        input_tokens=int(number("input_tokens")),
        output_tokens=int(number("output_tokens")),
    )
