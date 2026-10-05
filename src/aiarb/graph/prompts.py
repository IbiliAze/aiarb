"""The text each role is given.

Every prompt starts with the role's brief, from `aiarb/prompts/<role>.md`: who the
role is, how it works, and the rules of the debate. Then comes this turn's task,
then what the task works on, each in its own XML tag. The brief is sent on every
turn, not only a session's first: a session that has been lost restarts from the
latest prompt alone.

The scorer is the exception. It is given one objection as its subject and asked
one short question about it, since Jev reads both literally. The language-model
scorer adds its own brief.

Objections are always named by id, which is what scripted fakes match on.
"""

from __future__ import annotations

from functools import cache
from importlib.resources import files
from typing import TYPE_CHECKING

from aiarb.domain.ledger import CodeRef, Ref, Status
from aiarb.domain.scoring import RUBRIC, Severity

if TYPE_CHECKING:
    from collections.abc import Iterable

    from aiarb.config.charter import Charter
    from aiarb.domain.ledger import Objection
    from aiarb.graph.state import Role, RunState

_STATUS_MEANINGS = {
    Status.OPEN: "open: still being debated",
    Status.ANSWERED: "answered: the planner has answered, the critic has not yet ruled",
    Status.DISPUTED: "disputed: a fact the verifier has yet to check",
    Status.NOTED: "noted: minor, passed to the planner as a note and not debated",
    Status.RESOLVED: "resolved: settled by a plan change or cited evidence",
    Status.RULED: "ruled: the debate deadlocked and the judge decided it",
    Status.AWAITING_DECISION: "awaiting decision: a trade-off left for the user",
    Status.DECIDED: "decided: a trade-off the user ruled on",
    Status.DEFERRED: "deferred: the code cannot settle it; a task to do before building",
}


@cache
def brief(role: Role) -> str:
    """The role's standing instructions."""
    return (files("aiarb") / "prompts" / f"{role}.md").read_text(encoding="utf-8").strip()


def draft(state: RunState, charter: Charter) -> str:
    feature = state["feature"] or (
        "Not given. Work out the most valuable next change from the repo, and state that "
        "as your first assumption."
    )
    return _prompt(
        "planner",
        "Draft the plan for the feature below. Read the code it touches first.",
        feature=feature,
        charter=_charter(charter),
        repo_facts=state["facts"],
    )


def critique(state: RunState, charter: Charter) -> str:
    source = (
        "The user wrote this plan outside the review, so it may not follow the usual sections. "
        if state["given_plans"]
        else ""
    )
    return _prompt(
        "critic",
        f"Raise your objections to the plan below. {source}Read the code it touches, and "
        "check what it says about the codebase against the files.",
        feature=state["feature"] or "Not given: the plan states what it assumed.",
        plan=state["plan"],
        charter=_charter(charter),
        repo_facts=state["facts"],
    )


def respond(
    state: RunState, items: Iterable[Objection], notes: Iterable[Objection], charter: Charter
) -> str:
    minor = _describe_all(notes)
    return _prompt(
        "planner",
        f"Round {state['round'] + 1}. Answer every objection below by id, with a fix or a "
        "rebuttal, and return the whole revised plan. Each objection's history shows what "
        "has been said about it so far."
        + (" Minor notes follow the objections: apply them where worthwhile." if minor else ""),
        plan=state["plan"],
        objections=_describe_all(items),
        **({"minor_notes": minor} if minor else {}),
        charter=_charter(charter),
    )


def rule(state: RunState, items: Iterable[Objection], charter: Charter) -> str:
    return _prompt(
        "critic",
        f"Round {state['round']}. The planner has answered the objections below; the last "
        "entry in each history is the answer. Rule on every one, by id, against the revised "
        "plan below and the code. Rule only on these objections.",
        plan=state["plan"],
        objections=_describe_all(items),
        charter=_charter(charter),
    )


def verify(state: RunState, item: Objection) -> str:
    return _prompt(
        "verifier",
        "Settle the disputed fact below by reading the code.",
        question=item.history[-1].note,
        objection=describe(item),
        repo_facts=state["facts"],
    )


def judge(state: RunState, item: Objection, charter: Charter) -> str:
    return _prompt(
        "judge",
        f"Decide {item.id}, below. Its history holds both sides' arguments.",
        objection=describe(item),
        plan=state["plan"],
        charter=_charter(charter),
        repo_facts=state["facts"],
    )


def explain(state: RunState) -> str:
    items = list(state["ledger"].values())
    meanings = "\n".join(_STATUS_MEANINGS[status] for status in Status)
    return _prompt(
        "explainer",
        "Explain every objection below, by id and in the order given, and sum up the review. "
        "Each objection's status says how it ended.",
        feature=state["feature"] or "Not given: the plan states what it assumed.",
        plan=state["plan"],
        objections=_describe_all(items) or "None were raised.",
        statuses=meanings,
    )


IMPACT = (
    "Assume this objection is right. How much harm would the problem it describes do "
    "if the plan were built with that problem as it now stands? Rate the harm only, "
    "not whether the objection is right."
)
"""What the scorer is asked of each objection, before the debate and after it."""

IS_BLOCKER = (
    "Assume this objection is right. Would building the plan with the problem it "
    f"describes, as it now stands, mean the following? {RUBRIC[Severity.BLOCKER]}"
)
"""What a language model is asked to confirm a blocker call."""


def scored(item: Objection, charter: Charter) -> str:
    """What the scorer is shown of an objection: it, the debate over it so far, and
    the charter its impact is measured against."""
    return _join(describe(item), f"The charter for this codebase:\n{_charter(charter)}")


def describe(item: Objection) -> str:
    lines = [
        f"{item.id} ({item.type}, {item.severity}, {item.status}): {item.claim}",
        f"  Evidence: {_cite(item.evidence)}",
    ]
    lines += [
        f"  Round {event.round}, {event.actor}: {event.note}"
        + (f" [{_cite(event.refs)}]" if event.refs else "")
        for event in item.history[1:]
    ]
    return "\n".join(lines)


def _prompt(role: Role, task: str, **inputs: str) -> str:
    tagged = [f"<{tag}>\n{text.strip()}\n</{tag}>" for tag, text in inputs.items()]
    return _join(brief(role), f"# This turn\n\n{task}", *tagged)


def _charter(charter: Charter) -> str:
    lines: list[str] = []
    if charter.priorities:
        lines.append("Priorities, from 1 (matters a little) to 5 (matters most):")
        ranked = sorted(charter.priorities.items(), key=lambda pair: -pair[1])
        lines += [f"- {name.replace('_', ' ')}: {weight}" for name, weight in ranked]
    if charter.constraints:
        lines.append("Constraints every plan must keep to:")
        lines += [f"- {constraint}" for constraint in charter.constraints]
    return "\n".join(lines) or "No priorities or constraints are set: use sound judgement."


def _describe_all(items: Iterable[Objection]) -> str:
    return "\n\n".join(describe(item) for item in items)


def _cite(refs: Iterable[Ref]) -> str:
    return "; ".join(_cite_one(ref) for ref in refs)


def _cite_one(ref: Ref) -> str:
    if isinstance(ref, CodeRef):
        if ref.start_line is None:
            return ref.path
        end = f"-{ref.end_line}" if ref.end_line not in (None, ref.start_line) else ""
        return f"{ref.path}:{ref.start_line}{end}"
    return f"plan § {ref.section}" + (f' ("{ref.quote}")' if ref.quote else "")


def _join(*parts: str) -> str:
    return "\n\n".join(parts)
