"""`decisions.md`: the trade-offs the user decided, what each side argued, and
what the user chose."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiarb.domain.ledger import Actor, Status
from aiarb.output.report import decision, ending, last_by

if TYPE_CHECKING:
    from aiarb.domain.ledger import Objection
    from aiarb.output.report import Report


def render(report: Report) -> str:
    decided = [item for item in report.ledger.values() if item.status is Status.DECIDED]
    if not decided:
        return "# Decisions\n\nNo trade-offs needed your decision in this run.\n"
    entries = [_entry(item, report) for item in decided]
    return "\n\n".join(["# Decisions", "The trade-offs you decided in this run.", *entries]) + "\n"


def _entry(item: Objection, report: Report) -> str:
    explained = report.explained(item)
    lines = [
        f"## {item.id}: {report.title(item)}",
        f"**The trade-off.** {explained.problem if explained is not None else item.claim}",
    ]
    planner = last_by(item, Actor.PLANNER)
    if planner is not None:
        lines.append(f"**The planner's last word.** {planner.note}")
    critic = last_by(item, Actor.CRITIC)
    if critic is not None and critic is not item.history[0]:
        lines.append(f"**The critic's last word.** {critic.note}")
    lines.append(f"**Your decision** (round {ending(item).round}). {decision(item)}")
    return "\n\n".join(lines)
