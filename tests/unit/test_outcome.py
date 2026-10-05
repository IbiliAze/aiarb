import pytest

from aiarb.domain.ledger import (
    Actor,
    CodeRef,
    Event,
    Objection,
    ObjectionType,
    PlanRef,
    Resolution,
    ResolutionKind,
    Status,
)
from aiarb.domain.outcome import RunStatus, outcome
from aiarb.domain.scoring import Severity

OPEN_STATUSES = [status for status in Status if status.is_open]
CLOSED_STATUSES = [status for status in Status if not status.is_open]


def item(id: str, status: Status, severity: Severity = Severity.MAJOR) -> Objection:
    first = Event(
        round=0,
        actor=Actor.CRITIC,
        status=Status.OPEN,
        severity=severity,
        note="Raised.",
    )
    history = [first]
    if status is not Status.OPEN:
        resolution = None
        if status is Status.RESOLVED:
            resolution = Resolution(ResolutionKind.PLAN_CHANGE, (PlanRef("Rollout"),))
        history.append(
            Event(
                round=1,
                actor=Actor.SYSTEM,
                status=status,
                severity=severity,
                note="Set up for the test.",
                resolution=resolution,
            )
        )
    return Objection(
        id=id,
        claim="A claim.",
        evidence=(CodeRef("src/app.py"),),
        type=ObjectionType.DESIGN,
        history=tuple(history),
    )


def ledger(*items: Objection) -> dict[str, Objection]:
    return {objection.id: objection for objection in items}


@pytest.mark.parametrize(
    ("status", "exit_code"),
    [
        (RunStatus.SETTLED, 0),
        (RunStatus.SETTLED_WITH_OPEN_ITEMS, 1),
        (RunStatus.NOT_READY, 2),
    ],
)
def test_exit_codes(status: RunStatus, exit_code: int) -> None:
    assert status.exit_code == exit_code


def test_an_empty_ledger_is_settled() -> None:
    result = outcome({})

    assert result.status is RunStatus.SETTLED
    assert result.exit_code == 0
    assert result.open_items == ()
    assert result.blockers == ()


@pytest.mark.parametrize("status", CLOSED_STATUSES)
@pytest.mark.parametrize("severity", list(Severity))
def test_closed_items_of_any_severity_leave_the_run_settled(
    status: Status, severity: Severity
) -> None:
    result = outcome(ledger(item("OBJ-1", status, severity)))

    assert result.status is RunStatus.SETTLED
    assert result.exit_code == 0


@pytest.mark.parametrize("status", OPEN_STATUSES)
@pytest.mark.parametrize("severity", [s for s in Severity if s is not Severity.BLOCKER])
def test_an_open_item_below_blocker_leaves_open_items(status: Status, severity: Severity) -> None:
    open_item = item("OBJ-2", status, severity)

    result = outcome(ledger(item("OBJ-1", Status.RESOLVED), open_item))

    assert result.status is RunStatus.SETTLED_WITH_OPEN_ITEMS
    assert result.exit_code == 1
    assert result.open_items == (open_item,)
    assert result.blockers == ()


@pytest.mark.parametrize("status", OPEN_STATUSES)
def test_an_open_blocker_means_not_ready(status: Status) -> None:
    blocker = item("OBJ-2", status, Severity.BLOCKER)
    minor = item("OBJ-3", Status.OPEN, Severity.MINOR)

    result = outcome(ledger(item("OBJ-1", Status.RESOLVED), blocker, minor))

    assert result.status is RunStatus.NOT_READY
    assert result.exit_code == 2
    assert result.open_items == (blocker, minor)
    assert result.blockers == (blocker,)
