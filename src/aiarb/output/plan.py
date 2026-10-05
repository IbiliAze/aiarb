"""`plan.md`: the settled plan, with what it does not yet reflect attached.

The plan text is the planner's last revision, unchanged. Above it go the run's
status, the open items to settle before building, the judge's rulings and the
user's decisions, which the planner never revised the plan for, and the minor
notes, which were never debated. Each of those is also noted under every plan
section it cites.

A minor note reaches the planner only in a later round of the debate, and often
there is none, so the plan may not reflect it at all.

Citations are matched to the plan's real headings leniently, since the agents
cite sections from memory: exact names first, then a quoted line found in a
section, then a heading that contains the name or is contained by it. An item
that matches no heading is still listed at the top.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aiarb.domain.ledger import PlanRef, Status
from aiarb.domain.outcome import RunStatus
from aiarb.output.report import decision, ending, plural

if TYPE_CHECKING:
    from collections.abc import Sequence

    from aiarb.domain.ledger import Objection
    from aiarb.output.report import Report

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")
_NUMBERING = re.compile(r"^(?:\d+[.)]?\s*)+")
_PATH_SEPARATORS = re.compile(r"\s*(?:>|/|»|→|::)\s*")
_MIN_CONTAINED = 4
"""The fewest characters a name must have to match a heading by containment."""


@dataclass(frozen=True)
class Section:
    heading: str
    line: int
    """The heading's line, counting from 0."""
    end: int
    """The line after the section's own text, before the next heading of any level."""


def sections(plan: str) -> list[Section]:
    """The plan's ATX headings, outside fenced code blocks."""
    lines = plan.splitlines()
    found: list[tuple[str, int]] = []
    fence: str | None = None
    for number, line in enumerate(lines):
        opening = _FENCE.match(line)
        if opening is not None:
            marker = opening.group(1)
            if fence is None:
                fence = marker[0] * 3
            elif marker.startswith(fence):
                fence = None
            continue
        heading = _HEADING.match(line) if fence is None else None
        if heading is not None:
            found.append((heading.group(2), number))
    ends = [line for _, line in found[1:]]
    if found:
        ends.append(len(lines))
    return [Section(text, line, end) for (text, line), end in zip(found, ends, strict=True)]


def find(ref: PlanRef, plan: str, within: Sequence[Section]) -> Section | None:
    """The section a citation means, or None if no heading fits."""
    wanted = _normal(ref.section)
    names = [wanted, _normal(_PATH_SEPARATORS.split(ref.section)[-1])]
    for name in names:
        exact = next((s for s in within if name and _normal(s.heading) == name), None)
        if exact is not None:
            return exact
    if ref.quote:
        quoted = _squash(ref.quote)
        lines = plan.splitlines()
        for section in within:
            if quoted in _squash("\n".join(lines[section.line : section.end])):
                return section
    contained = [
        (abs(len(_normal(s.heading)) - len(name)), index)
        for index, s in enumerate(within)
        for name in names
        if _contains(_normal(s.heading), name)
    ]
    return within[min(contained)[1]] if contained else None


def render(report: Report) -> str:
    plan = report.plan.strip()
    within = sections(plan)
    items = list(report.ledger.values())
    to_settle = list(report.outcome.open_items)
    to_apply = [item for item in items if item.status in (Status.RULED, Status.DECIDED)]
    minor = [item for item in items if item.status is Status.NOTED]

    notes: dict[int, list[str]] = {}
    for item in [*to_settle, *to_apply, *minor]:
        for section in _cited_sections(item, plan, within):
            notes.setdefault(section.line, []).append(_note(item, report))

    lines = plan.splitlines()
    body: list[str] = []
    for number, line in enumerate(lines):
        body.append(line)
        if number in notes:
            body += ["", "\n>\n".join(f"> {note}" for note in notes[number])]
            if number + 1 < len(lines) and lines[number + 1].strip():
                body.append("")

    parts = [_banner(report, len(to_apply))]
    if to_settle:
        listed = sorted(to_settle, key=lambda item: -item.severity.rank)
        parts.append("## Before you build\n\n" + "\n".join(_task(i, report) for i in listed))
    if to_apply:
        parts.append(
            "## Rulings and decisions to apply\n\n"
            "The plan below was not revised for these.\n\n"
            + "\n".join(_applied(item, report) for item in to_apply)
        )
    if minor:
        parts.append(
            "## Minor notes\n\n"
            "Rated minor, so not debated: the plan may not reflect them. Worth a look as you "
            "build.\n\n" + "\n".join(_minor(item, report) for item in minor)
        )
    parts += ["---", "\n".join(body) if plan else "*No plan was written.*"]
    return "\n\n".join(parts) + "\n"


def _banner(report: Report, applied: int) -> str:
    outcome = report.outcome
    blockers, open_items = outcome.blockers, outcome.open_items
    if outcome.status is RunStatus.NOT_READY:
        lead = (
            f"**Not ready: {plural(len(blockers), 'open blocker')}.** Do not build from this "
            "plan until the items below are settled."
        )
    elif outcome.status is RunStatus.SETTLED_WITH_OPEN_ITEMS:
        lead = (
            f"**Settled with {plural(len(open_items), 'open item')}.** The plan is usable; "
            "the open items below are known risks."
        )
    else:
        lead = "**Settled.** No objections are open."
    if applied:
        lead += " Apply the rulings and decisions below as you build."
    lines = [lead]
    if outcome.downgraded:
        ids = ", ".join(item.id for item in outcome.downgraded)
        lines.append(
            f"Rated lower after the debate, though nobody settled them: {ids}. Worth a second look."
        )
    lines.append("Every objection, and how it ended, is in [ledger.md](ledger.md).")
    return "\n".join(f"> {line}" if i == 0 else f">\n> {line}" for i, line in enumerate(lines))


def _task(item: Objection, report: Report) -> str:
    return f"- [ ] **{item.id}, {item.severity}.** {item.claim} *Next:* {report.what_now(item)}"


def _applied(item: Objection, report: Report) -> str:
    if item.status is Status.RULED:
        return f"- **{item.id}, ruled by the judge, not agreed.** {item.claim} {ending(item).note}"
    return f"- **{item.id}, your decision.** {item.claim} Decided: {decision(item)}"


def _minor(item: Objection, report: Report) -> str:
    return f"- **{item.id}: {report.title(item)}.** {item.claim}"


def _note(item: Objection, report: Report) -> str:
    if item.status is Status.RULED:
        # Rulings run long, and an item can cite several sections: the ruling is
        # given in full once, at the top.
        return (
            f"**{item.id}, ruled by the judge:** {report.title(item)}. "
            "See *Rulings and decisions to apply*."
        )
    if item.status is Status.DECIDED:
        return f"**{item.id}, your decision:** {decision(item)}"
    if item.status is Status.NOTED:
        return f"**{item.id}, minor note:** {report.title(item)}. See *Minor notes*."
    return f"**{item.id}, open {item.severity}:** {report.title(item)}. See *Before you build*."


def _cited_sections(item: Objection, plan: str, within: Sequence[Section]) -> list[Section]:
    refs = [*item.evidence, *(ref for event in item.history for ref in event.refs)]
    found: list[Section] = []
    for ref in refs:
        if isinstance(ref, PlanRef):
            section = find(ref, plan, within)
            if section is not None and section not in found:
                found.append(section)
    return found


def _normal(text: str) -> str:
    """A heading or name compared loosely: no numbering, case, markup or punctuation."""
    plain = re.sub(r"[*_`]", "", text).strip().lower()
    plain = _NUMBERING.sub("", plain)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", plain).split())


def _squash(text: str) -> str:
    return " ".join(re.sub(r"[*_`]", "", text).lower().split())


def _contains(heading: str, name: str) -> bool:
    shorter = min(heading, name, key=len)
    return len(shorter) >= _MIN_CONTAINED and (name in heading or heading in name)
