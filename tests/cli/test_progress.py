import io

import pytest
from rich.console import Console

from aiarb.cli.progress import LiveProgress, duration
from aiarb.graph.state import RunState, initial_state
from aiarb.ports.calls import Cost


def state(round: int = 0, usd: float = 0, given_plans: tuple[str, ...] = ()) -> RunState:
    base = initial_state(feature="A queue.", given_plans=given_plans)
    base.update({"round": round, "costs": {"planner": Cost(usd=usd)} if usd else {}})
    return base


def progress(*, terminal: bool) -> tuple[LiveProgress, io.StringIO]:
    out = io.StringIO()
    console = Console(file=out, force_terminal=terminal, width=200, color_system=None)
    clock = iter(float(n) for n in range(0, 10_000, 30))
    return LiveProgress(console, lambda: next(clock)), out


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(4.4, "4s"), (59.6, "1m 00s"), (125, "2m 05s"), (3599, "59m 59s"), (4320, "1h 12m")],
)
def test_durations_read_at_a_glance(seconds: float, text: str) -> None:
    assert duration(seconds) == text


def test_the_planners_answer_starts_the_next_round() -> None:
    shown, out = progress(terminal=False)

    shown.started("respond", state(round=1, usd=2))
    shown.finished("respond", state(round=2, usd=2.5))

    assert out.getvalue().splitlines() == [
        "  … Planner answering the objections · round 2 · $2.00 so far",
        "  ✓ Planner answered the objections · round 2  30s  $0.50",
    ]


def test_a_plan_brought_in_is_taken_in_not_drafted() -> None:
    shown, out = progress(terminal=False)

    shown.started("draft_plan", state(given_plans=("Use Redis.",)))
    shown.finished("draft_plan", state(given_plans=("Use Redis.",)))

    assert "Taking in your plan" in out.getvalue()
    assert "✓ Took in your plan" in out.getvalue()


def test_names_the_run_once() -> None:
    shown, out = progress(terminal=False)

    shown.opened("2026-10-05-queue", state())
    shown.opened("2026-10-05-queue", state())

    assert out.getvalue() == "Run 2026-10-05-queue\n"


def test_at_a_terminal_the_step_under_way_is_live_and_then_cleared() -> None:
    shown, out = progress(terminal=True)

    shown.started("scan", state())
    live = shown._bar
    shown.finished("scan", state())

    assert live is not None
    assert not live.live.is_started
    assert "✓ Scanned the repo" in out.getvalue()
    assert "… Scanning" not in out.getvalue()


def test_warns_of_files_changed_while_the_run_went() -> None:
    shown, out = progress(terminal=False)

    shown.repo_changed([f"src/{n}.py" for n in range(7)])

    assert out.getvalue() == (
        "Files in the repo changed while the run went: src/0.py, src/1.py, src/2.py, src/3.py, "
        "src/4.py and 2 more. The review may not reflect those changes. aiarb itself writes "
        "only to .aiarb/.\n"
    )
