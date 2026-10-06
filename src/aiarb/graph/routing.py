"""Where the graph goes after each step of the debate.

Each router reads the state and names the next node. Their `Literal` return
types are the graph's edges: LangGraph reads them at runtime to know where a
router can go, so this module imports its annotation types for real and does
not use `from __future__ import annotations`. If those hints stop resolving,
LangGraph silently loses the edges, which the routing tests check for.
"""

from itertools import pairwise
from typing import Literal

from langgraph.runtime import Runtime

from aiarb.config.charter import Charter
from aiarb.domain.ledger import Status
from aiarb.graph.context import RunContext
from aiarb.graph.state import RunState

STALL_ROUNDS = 2
"""Rounds in a row with no objection leaving the debate before it counts as stalled."""

_IN_DEBATE = frozenset({Status.OPEN, Status.ANSWERED, Status.DISPUTED})
"""Statuses the planner, critic and verifier are still arguing over."""

AfterDraft = Literal["clarify", "critique"]
AfterTriage = Literal["respond", "rescore"]
AfterRule = Literal["verify", "respond", "judge", "decide", "rescore"]
AfterRound = Literal["respond", "judge", "decide", "rescore"]
AfterJudge = Literal["decide", "rescore"]


def after_draft(state: RunState) -> AfterDraft:
    """Pause for the user's answers if the planner asked questions, or critique the plan."""
    return "clarify" if state.get("questions") else "critique"


def after_triage(state: RunState) -> AfterTriage:
    """Debate the objections that survived triage, or skip to rescoring if none did."""
    return "respond" if _any_open(state) else "rescore"


def after_rule(state: RunState, runtime: Runtime[RunContext]) -> AfterRule:
    """Send disputed facts to the verifier, otherwise end the round."""
    if any(item.status is Status.DISPUTED for item in state["ledger"].values()):
        return "verify"
    return after_round(state, runtime)


def after_round(state: RunState, runtime: Runtime[RunContext]) -> AfterRound:
    """Go another round, hand the leftovers to the judge, or finish the debate.

    Trade-offs can be waiting for the user once the debate ends without the
    judge: after the user reopened other items instead of deciding them.
    """
    if not _any_open(state):
        return "decide" if decisions_needed(state) else "rescore"
    if judge_needed(state, round_limit(state, runtime.context.charter)):
        return "judge"
    return "respond"


def after_judge(state: RunState) -> AfterJudge:
    """Ask the user about trade-offs left for them, or move on to rescoring."""
    return "decide" if decisions_needed(state) else "rescore"


def judge_needed(state: RunState, max_rounds: int) -> bool:
    """The debate has items left but must stop: out of rounds, or going in circles."""
    return round_limit_reached(state, max_rounds) or stalled(state)


def round_limit(state: RunState, charter: Charter) -> int:
    """The run's round limit: the one it was given, or the charter's."""
    return state["max_rounds"] or charter.debate.max_rounds


def round_limit_reached(state: RunState, max_rounds: int) -> bool:
    """The current debate has had its rounds. A debate the user reopened gets a
    full set of its own."""
    return state["round"] - state["debate_start"] >= max_rounds


def stalled(state: RunState) -> bool:
    """No objection has left the debate for `STALL_ROUNDS` rounds.

    Leaving means being resolved, deferred, sent for a decision, or anything else
    that ends the argument over it. A rejected answer does not count, even if the
    plan changed, since the critic was not persuaded. The user reopening items
    restarts the clock.
    """
    return state["round"] - max(last_progress(state), state["debate_start"]) >= STALL_ROUNDS


def last_progress(state: RunState) -> int:
    """The last round in which an objection left the debate, or 0 if none has."""
    return max(
        (
            event.round
            for item in state["ledger"].values()
            for before, event in pairwise(item.history)
            if before.status in _IN_DEBATE and event.status not in _IN_DEBATE
        ),
        default=0,
    )


def decisions_needed(state: RunState) -> bool:
    return any(item.status is Status.AWAITING_DECISION for item in state["ledger"].values())


def _any_open(state: RunState) -> bool:
    """Anything left to argue over. Between rounds, every item still in debate is open."""
    return any(item.status is Status.OPEN for item in state["ledger"].values())
