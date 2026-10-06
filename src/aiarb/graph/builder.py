"""The debate, assembled as a LangGraph graph.

    scan → draft_plan → critique → triage
    draft_plan → clarify if the planner asked the user questions, then back to
               draft_plan with the answers
    triage   → respond, or rescore if nothing is left to debate
    respond  → rule
    rule     → verify if facts are disputed, otherwise on as after verify
    verify   → respond for another round, judge at the round limit or on a
               stalemate, or once nothing is open, decide if trade-offs are
               waiting for the user, otherwise rescore
    judge    → decide if trade-offs are left for the user, otherwise rescore
    decide   → rescore → explain → finalise

A finished or paused run whose items the user reopens carries on from triage,
as if triage had just sent those items to debate.

Agent nodes retry calls that are worth retrying, and each attempt has a time
limit. The routers in `routing.py` decide every branch. Only `finalise` writes
files.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from langgraph.graph import END, START, StateGraph  # pyright: ignore[reportMissingTypeStubs]
from langgraph.types import RetryPolicy

from aiarb.graph import nodes, routing
from aiarb.graph.context import RunContext
from aiarb.graph.state import RunState
from aiarb.ports.calls import RetryableCallError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from langchain_core.runnables import RunnableConfig
    from langgraph.checkpoint.base import BaseCheckpointSaver
    from langgraph.graph.state import CompiledStateGraph  # pyright: ignore[reportMissingTypeStubs]

AGENT_RETRY = RetryPolicy(
    initial_interval=5.0,
    backoff_factor=2.0,
    max_interval=60.0,
    max_attempts=3,
    retry_on=RetryableCallError,
)
"""For calls that failed in a way worth trying again: rate limits, overloads,
dropped connections, or an answer that skipped an objection.

A node that runs out of time is not retried. Its limit is generous, so hitting
it means the call is stuck, and trying again would only spend more.
"""

TIMEOUTS: Mapping[str, timedelta] = {
    "draft_plan": timedelta(hours=2),
    "critique": timedelta(hours=2),
    "respond": timedelta(hours=1),
    "rule": timedelta(hours=1),
    "verify": timedelta(minutes=30),
    "judge": timedelta(minutes=30),
    "triage": timedelta(minutes=10),
    "rescore": timedelta(minutes=10),
    "explain": timedelta(minutes=30),
}
"""How long one attempt of each node that makes outside calls may run.

The planner and critic explore the repo in depth on their first turns, which
can take an hour on a large codebase. Later turns resume those sessions and
cover only the open items.
"""

_FIXED_STEPS = 8
"""The most steps outside the rounds in one invocation: scan, draft_plan,
critique, triage, judge, rescore, explain and finalise. A run that needs
decisions pauses at decide instead, and is finished by a later invocation with
its own count. So does a planner that asks questions: it pauses at clarify, and
the invocation that resumes it runs clarify and draft_plan in place of scan and
draft_plan."""

_STEPS_PER_ROUND = 3
"""respond, rule and verify."""

_INPUT_STEP = 1
"""LangGraph counts taking in the input as a step too."""


def build_graph(
    *,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    retry_policy: RetryPolicy = AGENT_RETRY,
) -> CompiledStateGraph[RunState, RunContext, RunState, RunState]:
    """The debate graph, ready to run with a `RunContext`.

    A run needs a checkpointer to pause for the user's decisions. Studio brings
    its own.
    """
    graph = StateGraph(RunState, context_schema=RunContext)

    graph.add_node("scan", nodes.scan)  # pyright: ignore[reportUnknownMemberType]
    graph.add_node(  # pyright: ignore[reportUnknownMemberType]
        "draft_plan", nodes.draft_plan, retry_policy=retry_policy, timeout=TIMEOUTS["draft_plan"]
    )
    graph.add_node(  # pyright: ignore[reportUnknownMemberType]
        "critique", nodes.critique, retry_policy=retry_policy, timeout=TIMEOUTS["critique"]
    )
    graph.add_node("triage", nodes.triage, retry_policy=retry_policy, timeout=TIMEOUTS["triage"])  # pyright: ignore[reportUnknownMemberType]
    graph.add_node("respond", nodes.respond, retry_policy=retry_policy, timeout=TIMEOUTS["respond"])  # pyright: ignore[reportUnknownMemberType]
    graph.add_node("rule", nodes.rule, retry_policy=retry_policy, timeout=TIMEOUTS["rule"])  # pyright: ignore[reportUnknownMemberType]
    graph.add_node("verify", nodes.verify, retry_policy=retry_policy, timeout=TIMEOUTS["verify"])  # pyright: ignore[reportUnknownMemberType]
    graph.add_node("judge", nodes.judge, retry_policy=retry_policy, timeout=TIMEOUTS["judge"])  # pyright: ignore[reportUnknownMemberType]
    graph.add_node("clarify", nodes.clarify)  # pyright: ignore[reportUnknownMemberType]
    graph.add_node("decide", nodes.decide)  # pyright: ignore[reportUnknownMemberType]
    graph.add_node("rescore", nodes.rescore, retry_policy=retry_policy, timeout=TIMEOUTS["rescore"])  # pyright: ignore[reportUnknownMemberType]
    graph.add_node("explain", nodes.explain, retry_policy=retry_policy, timeout=TIMEOUTS["explain"])  # pyright: ignore[reportUnknownMemberType]
    graph.add_node("finalise", nodes.finalise)  # pyright: ignore[reportUnknownMemberType]

    graph.add_edge(START, "scan")
    graph.add_edge("scan", "draft_plan")
    graph.add_conditional_edges("draft_plan", routing.after_draft)
    graph.add_edge("clarify", "draft_plan")
    graph.add_edge("critique", "triage")
    graph.add_conditional_edges("triage", routing.after_triage)
    graph.add_edge("respond", "rule")
    graph.add_conditional_edges("rule", routing.after_rule)
    graph.add_conditional_edges("verify", routing.after_round)
    graph.add_conditional_edges("judge", routing.after_judge)
    graph.add_edge("decide", "rescore")
    graph.add_edge("rescore", "explain")
    graph.add_edge("explain", "finalise")
    graph.add_edge("finalise", END)

    return graph.compile(checkpointer=checkpointer, name="aiarb")  # pyright: ignore[reportUnknownMemberType]


def recursion_limit(max_rounds: int) -> int:
    """The most steps one invocation of a run can take.

    The routers already stop the debate at the round limit. This is the backstop
    if they ever fail to, so it is exact rather than generous.
    """
    return _INPUT_STEP + _FIXED_STEPS + _STEPS_PER_ROUND * max_rounds


def run_config(run_id: str, max_rounds: int) -> RunnableConfig:
    """The config to invoke the graph with: the run is the thread its checkpoints
    belong to, and its step limit comes from its round limit."""
    return {
        "recursion_limit": recursion_limit(max_rounds),
        "configurable": {"thread_id": run_id},
    }
