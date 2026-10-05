"""The debate, assembled as a LangGraph graph.

    scan → draft_plan → critique → triage
    triage   → respond, or rescore if nothing is left to debate
    respond  → rule
    rule     → verify if facts are disputed, otherwise on as after verify
    verify   → respond for another round, judge at the round limit or on a
               stalemate, or rescore once nothing is open
    judge    → decide if trade-offs are left for the user, otherwise rescore
    decide   → rescore → finalise

Agent nodes retry calls that are worth retrying, and each attempt has a time
limit. The routers in `routing.py` decide every branch.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from aiarb.graph import nodes, routing
from aiarb.graph.context import RunContext
from aiarb.graph.state import RunState
from aiarb.ports.calls import RetryableCallError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from langchain_core.runnables import RunnableConfig
    from langgraph.graph.state import CompiledStateGraph

    from aiarb.config.charter import Charter

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
}
"""How long one attempt of each node that makes outside calls may run.

The planner and critic explore the repo in depth on their first turns, which
can take an hour on a large codebase. Later turns resume those sessions and
cover only the open items.
"""

_FIXED_STEPS = 8
"""Steps every run can take outside the rounds: scan, draft_plan, critique,
triage, judge, decide, rescore and finalise."""

_STEPS_PER_ROUND = 3
"""respond, rule and verify."""

_INPUT_STEP = 1
"""LangGraph counts taking in the input as a step too."""


def build_graph(
    *, retry_policy: RetryPolicy = AGENT_RETRY
) -> CompiledStateGraph[RunState, RunContext, RunState, RunState]:
    """The debate graph, ready to run with a `RunContext`."""
    graph = StateGraph(RunState, context_schema=RunContext)

    graph.add_node("scan", nodes.scan)
    graph.add_node(
        "draft_plan", nodes.draft_plan, retry_policy=retry_policy, timeout=TIMEOUTS["draft_plan"]
    )
    graph.add_node(
        "critique", nodes.critique, retry_policy=retry_policy, timeout=TIMEOUTS["critique"]
    )
    graph.add_node("triage", nodes.triage, retry_policy=retry_policy, timeout=TIMEOUTS["triage"])
    graph.add_node("respond", nodes.respond, retry_policy=retry_policy, timeout=TIMEOUTS["respond"])
    graph.add_node("rule", nodes.rule, retry_policy=retry_policy, timeout=TIMEOUTS["rule"])
    graph.add_node("verify", nodes.verify, retry_policy=retry_policy, timeout=TIMEOUTS["verify"])
    graph.add_node("judge", nodes.judge, retry_policy=retry_policy, timeout=TIMEOUTS["judge"])
    graph.add_node("decide", nodes.decide)
    graph.add_node("rescore", nodes.rescore, retry_policy=retry_policy, timeout=TIMEOUTS["rescore"])
    graph.add_node("finalise", nodes.finalise)

    graph.add_edge(START, "scan")
    graph.add_edge("scan", "draft_plan")
    graph.add_edge("draft_plan", "critique")
    graph.add_edge("critique", "triage")
    graph.add_conditional_edges("triage", routing.after_triage)
    graph.add_edge("respond", "rule")
    graph.add_conditional_edges("rule", routing.after_rule)
    graph.add_conditional_edges("verify", routing.after_round)
    graph.add_conditional_edges("judge", routing.after_judge)
    graph.add_edge("decide", "rescore")
    graph.add_edge("rescore", "finalise")
    graph.add_edge("finalise", END)

    return graph.compile(name="aiarb")


def recursion_limit(max_rounds: int) -> int:
    """The most steps a run can take: every fixed step, and a full round each time.

    The routers already stop the debate at the round limit. This is the backstop
    if they ever fail to, so it is exact rather than generous.
    """
    return _INPUT_STEP + _FIXED_STEPS + _STEPS_PER_ROUND * max_rounds


def run_config(charter: Charter) -> RunnableConfig:
    """The config to invoke the graph with for a run under this charter."""
    return {"recursion_limit": recursion_limit(charter.debate.max_rounds)}
