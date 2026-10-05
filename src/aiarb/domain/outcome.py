"""What a finished ledger means for the run: its status and exit code."""

from dataclasses import dataclass
from enum import StrEnum

from aiarb.domain.ledger import Ledger, Objection
from aiarb.domain.scoring import Severity


class RunStatus(StrEnum):
    SETTLED = "settled"
    """No open objections."""
    SETTLED_WITH_OPEN_ITEMS = "settled_with_open_items"
    """The plan is usable; unresolved items are listed as known risks."""
    NOT_READY = "not_ready"
    """At least one open item is a blocker. Do not build from this plan."""

    @property
    def exit_code(self) -> int:
        return _EXIT_CODES[self]


_EXIT_CODES = {
    RunStatus.SETTLED: 0,
    RunStatus.SETTLED_WITH_OPEN_ITEMS: 1,
    RunStatus.NOT_READY: 2,
}


@dataclass(frozen=True)
class Outcome:
    status: RunStatus
    open_items: tuple[Objection, ...]
    blockers: tuple[Objection, ...]
    """The open items that are blockers."""

    @property
    def exit_code(self) -> int:
        return self.status.exit_code


def outcome(ledger: Ledger) -> Outcome:
    open_items = tuple(item for item in ledger.values() if item.is_open)
    blockers = tuple(item for item in open_items if item.severity is Severity.BLOCKER)
    if blockers:
        status = RunStatus.NOT_READY
    elif open_items:
        status = RunStatus.SETTLED_WITH_OPEN_ITEMS
    else:
        status = RunStatus.SETTLED
    return Outcome(status=status, open_items=open_items, blockers=blockers)
