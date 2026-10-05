"""The graph's nodes. Each is a small function that returns a state update."""

from aiarb.graph.nodes.critique import critique
from aiarb.graph.nodes.decide import decide
from aiarb.graph.nodes.draft_plan import draft_plan
from aiarb.graph.nodes.finalise import finalise
from aiarb.graph.nodes.judge import judge
from aiarb.graph.nodes.rescore import rescore
from aiarb.graph.nodes.respond import respond
from aiarb.graph.nodes.rule import rule
from aiarb.graph.nodes.scan import scan
from aiarb.graph.nodes.triage import triage
from aiarb.graph.nodes.verify import verify

__all__ = [
    "critique",
    "decide",
    "draft_plan",
    "finalise",
    "judge",
    "rescore",
    "respond",
    "rule",
    "scan",
    "triage",
    "verify",
]
