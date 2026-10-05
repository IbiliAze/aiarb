"""`ledger.md`: every objection in plain English, then the debate over it.

For each objection: what the problem is, what would have happened in production
had the plan been built as first written, and how it ended, in the explainer's
words. The facts beside them, its severity, status, evidence and every step of
the debate, come from the ledger itself, so the explainer can soften nothing.
Open items come first, most severe first; the rest follow in the order raised.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from aiarb.output.report import (
    RUN_STATUS_LABELS,
    SHORT_STATUS_LABELS,
    STATUS_LABELS,
    cite,
    ending,
    plural,
)

if TYPE_CHECKING:
    from aiarb.domain.ledger import Event, Objection
    from aiarb.output.report import Report


def render(report: Report) -> str:
    items = list(report.ledger.values())
    parts = ["# Ledger"]
    if report.explanation is not None:
        parts.append(report.explanation.summary)
    parts.append(_tally(report, items))
    open_items = sorted(report.outcome.open_items, key=lambda item: -item.severity.rank)
    closed = [item for item in items if not item.is_open]
    parts += [_entry(item, report) for item in [*open_items, *closed]]
    return "\n\n".join(parts) + "\n"


def _tally(report: Report, items: list[Objection]) -> str:
    status = f"**{RUN_STATUS_LABELS[report.outcome.status]}.**"
    if not items:
        return f"{status} No objections were raised."
    counts = Counter(item.status for item in items)
    listed = ", ".join(
        f"{counts[status]} {label}"
        for status, label in SHORT_STATUS_LABELS.items()
        if counts[status]
    )
    return f"{status} {plural(len(items), 'objection')}: {listed}."


def _entry(item: Objection, report: Report) -> str:
    explained = report.explained(item)
    lines = [
        f"## {item.id}: {report.title(item)}",
        f"*{item.severity.capitalize()} · {item.type.replace('_', '-')} · "
        f"{STATUS_LABELS[item.status]}*",
    ]
    if explained is not None:
        lines += [
            f"**The problem.** {explained.problem}",
            f"**In production.** {explained.consequence}",
            f"**How it ended.** {explained.outcome}",
        ]
    else:
        lines += [f"**The claim.** {item.claim}", f"**How it ended.** {ending(item).note}"]
    if item.dropped_from is not None:
        lines.append(
            f"**Flagged.** Rated {item.dropped_from} earlier and {item.severity} now, though "
            "nobody settled it. Worth a second look."
        )
    lines.append(f"**Evidence.** {cite(item.evidence)}")
    steps = "\n".join(_step(event, first=index == 0) for index, event in enumerate(item.history))
    lines.append(f"<details>\n<summary>The debate</summary>\n\n{steps}\n\n</details>")
    return "\n\n".join(lines)


def _step(event: Event, *, first: bool) -> str:
    """One event in the debate, with what it cited. The raise cites the evidence,
    which is listed above already."""
    cited = [] if first else list(event.refs)
    if event.resolution is not None:
        cited += event.resolution.refs
    return f"- Round {event.round}, {event.actor}: {event.note}" + (
        f" ({cite(cited)})" if cited else ""
    )
