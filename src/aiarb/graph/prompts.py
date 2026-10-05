"""The text each role is given.

Minimal for now: each prompt carries what the role needs and names objections by
id, which is also what the scripted fakes match on. Section 9 replaces these
with the real prompts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiarb.domain.ledger import CodeRef, Ref

if TYPE_CHECKING:
    from collections.abc import Iterable

    from aiarb.domain.ledger import Objection
    from aiarb.graph.state import RunState


def draft(state: RunState) -> str:
    feature = state["feature"] or (
        "Not given. Infer the most valuable next change from the repo, and state that "
        "assumption at the top of the plan."
    )
    return _join(
        "You are the planner. Draft an implementation plan for this repo.",
        f"Feature:\n{feature}",
        f"Repo facts:\n{state['facts']}",
        "Write the plan in Markdown, with a heading for each section.",
    )


def critique(state: RunState) -> str:
    return _join(
        "You are the critic. Find what is wrong with this plan for this repo.",
        f"Plan:\n{state['plan']}",
        f"Repo facts:\n{state['facts']}",
        "Every objection must cite the code or the plan section it concerns.",
    )


def respond(state: RunState, items: Iterable[Objection], notes: Iterable[Objection]) -> str:
    minor = _describe_all(notes)
    return _join(
        "You are the planner. Answer every objection below by id: fix the plan, or rebut "
        "the objection with evidence. Agreeing without changing the plan is not an answer.",
        f"Current plan:\n{state['plan']}",
        f"Objections:\n{_describe_all(items)}",
        *(
            [f"Minor notes, to apply where worthwhile (no answer needed):\n{minor}"]
            if minor
            else []
        ),
        "Return the whole revised plan, and one answer per objection.",
    )


def rule(state: RunState, items: Iterable[Objection]) -> str:
    return _join(
        "You are the critic. Rule on the planner's answer to each objection below, by id: "
        "accept it with the plan change or evidence that settles it, reject it, or dispute "
        "a factual claim so the verifier can check the repo.",
        f"Current plan:\n{state['plan']}",
        f"Objections and answers:\n{_describe_all(items)}",
    )


def verify(item: Objection) -> str:
    return _join(
        "You are the verifier. The planner and critic disagree on a fact about this repo. "
        "Read the code and say who is right, citing what you found. If reading the code "
        "cannot settle it, say it is unverifiable.",
        f"Disputed objection:\n{describe(item)}",
    )


def judge(state: RunState, item: Objection) -> str:
    return _join(
        "You are the judge. The planner and critic could not settle this objection within "
        "the round limit. Decide which side is right and what the plan should do.",
        f"Current plan:\n{state['plan']}",
        f"Objection:\n{describe(item)}",
    )


def score(item: Objection) -> str:
    return _join(
        "If this objection is right, how much does it matter? Rate its impact on the "
        "rubric. Do not judge whether it is right.",
        describe(item),
    )


def describe(item: Objection) -> str:
    lines = [
        f"{item.id} ({item.type}, {item.severity}): {item.claim}",
        f"  Evidence: {_cite(item.evidence)}",
    ]
    lines += [
        f"  Round {event.round}, {event.actor}: {event.note}"
        + (f" [{_cite(event.refs)}]" if event.refs else "")
        for event in item.history[1:]
    ]
    return "\n".join(lines)


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
