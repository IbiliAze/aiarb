"""Each role run alone, through its node and real Claude Code, on a copy of the
python-api fixture repo. Each test hands a role the input it would get mid-run
and checks the answer validates and moves the ledger as it should. These spend
money, a dollar or so a run, so they run only when asked:

    AIARB_LIVE_TESTS=1 uv run pytest tests/live -s

With `-s`, each role's answer is printed for reading.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest
from langgraph.runtime import Runtime

from aiarb.adapters.claude_code import ClaudeCodeAgent
from aiarb.config.charter import Charter
from aiarb.domain.ledger import (
    Actor,
    CodeRef,
    Objection,
    ObjectionType,
    PlanRef,
    Resolution,
    ResolutionKind,
    Status,
)
from aiarb.domain.scoring import Severity
from aiarb.graph import prompts
from aiarb.graph.context import RunContext
from aiarb.graph.nodes import critique, draft_plan, judge, respond, rule, verify
from aiarb.graph.nodes._calls import ask, by_id
from aiarb.graph.state import RunState, initial_state
from aiarb.scan.facts import build
from aiarb.schemas.outputs import Explanation
from tests.fakes import ScriptedScorer

pytestmark = pytest.mark.skipif(
    os.environ.get("AIARB_LIVE_TESTS") != "1",
    reason="calls real Claude Code; set AIARB_LIVE_TESTS=1 to run",
)

FIXTURE = Path(__file__).parent.parent / "fixtures" / "repos" / "python-api"
HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-5-5"
"""The critic must differ from the planner, so it runs on Sonnet."""

FEATURE = (
    "Hold a ticket for ten minutes while the buyer pays, so two buyers cannot pay for "
    "the same ticket."
)
PLAN = """## Summary

Hold a ticket for ten minutes while the buyer pays.

## Approach

Keep holds in a module-level Python dict in app/routes.py, keyed by ticket id, with
the time each hold expires. Redis is already used for caching, in app/cache.py.

## Changes

1. Add `POST /events/{event_id}/tickets/{ticket_id}/hold` to app/routes.py.
2. Check the dict before selling a ticket.

## Testing

Add a test for the hold endpoint to tests/test_routes.py.
"""
REVISED = PLAN.replace(
    "Keep holds in a module-level Python dict in app/routes.py, keyed by ticket id, with\n"
    "the time each hold expires.",
    "Keep holds in a new `holds` table in Postgres, added by an Alembic migration, with a\n"
    "unique constraint on the ticket id and the time each hold expires. A hold whose time\n"
    "has passed is replaced in the same statement that takes the new hold.",
).replace(
    "2. Check the dict before selling a ticket.",
    "2. Add `POST /events/{event_id}/tickets/{ticket_id}/buy`, which sells only to the\n"
    "   buyer holding an unexpired hold.",
)
"""The plan with the in-memory holds fixed, and its false claim about Redis kept."""
CHARTER = Charter.model_validate(
    {
        "models": {
            "planner": HAIKU,
            "critic": SONNET,
            "judge": HAIKU,
            "verifier": HAIKU,
            "explainer": HAIKU,
        },
        "priorities": {"data_integrity": 5, "running_cost": 2},
        "constraints": ["no new managed services"],
        "budget": {"per_call": 1, "per_run": 10},
    }
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return Path(shutil.copytree(FIXTURE, tmp_path / "python-api"))


def runtime(repo: Path) -> Runtime[RunContext]:
    agent = ClaudeCodeAgent.from_charter(CHARTER)
    context = RunContext(charter=CHARTER, repo=repo, agent=agent, scorer=ScriptedScorer([]))
    return Runtime(context=context)


def state(repo: Path, *items: Objection, round: int = 0, **fields: Any) -> RunState:
    base = initial_state(feature=FEATURE)
    changes = {
        "facts": build(repo),
        "plan": PLAN,
        "ledger": {item.id: item for item in items},
        "round": round,
        **fields,
    }
    base.update(changes)  # type: ignore[typeddict-item]
    return base


def raised(id: str, claim: str, type: ObjectionType, *evidence: CodeRef | PlanRef) -> Objection:
    return Objection.raised(
        id=id, claim=claim, evidence=evidence, type=type, severity=Severity.MAJOR, round=0
    )


IN_MEMORY = raised(
    "OBJ-1",
    "Holds kept in a Python dict are lost on every restart and are not shared between "
    "worker processes, so two buyers can hold the same ticket.",
    ObjectionType.DESIGN,
    PlanRef("Approach", "Keep holds in a module-level Python dict"),
    CodeRef("Dockerfile"),
)
NO_CACHE = raised(
    "OBJ-2",
    "The plan says Redis is already used for caching in app/cache.py, but that file does "
    "not exist and no code connects to Redis.",
    ObjectionType.FACTUAL,
    PlanRef("Approach", "Redis is already used for caching, in app/cache.py."),
)


def show(role: str, text: object) -> None:
    print(f"\n----- {role} -----\n{text}")


async def test_the_planner_drafts_a_plan(repo: Path) -> None:
    update = await draft_plan(state(repo, plan=""), runtime(repo))

    plan = update["plan"]
    show("planner: draft", plan)
    assert plan.count("\n## ") >= 3
    assert "app/" in plan
    assert update["costs"]["planner"].usd > 0


async def test_the_critic_raises_objections(repo: Path) -> None:
    update = await critique(state(repo), runtime(repo))

    raised = list(update["ledger"].values())
    show("critic: critique", "\n\n".join(prompts.describe(item) for item in raised))
    assert raised
    assert [item.id for item in raised] == [f"OBJ-{n}" for n in range(1, len(raised) + 1)]


async def test_the_planner_answers_every_objection(repo: Path) -> None:
    update = await respond(state(repo, IN_MEMORY, NO_CACHE), runtime(repo))

    show("planner: respond", update["plan"])
    show("planner: answers", "\n\n".join(prompts.describe(i) for i in update["ledger"].values()))
    assert update["round"] == 1
    assert {item.status for item in update["ledger"].values()} == {Status.ANSWERED}
    assert update["plan"] != PLAN


async def test_the_critic_rules_on_every_answer(repo: Path) -> None:
    fixed = IN_MEMORY.transition(
        Status.ANSWERED,
        actor=Actor.PLANNER,
        round=1,
        note="Fix: Holds now live in a Postgres table with a unique constraint on the "
        "ticket id, so they survive restarts and are shared by every worker.",
        refs=[PlanRef("Approach")],
    )
    rebutted = NO_CACHE.transition(
        Status.ANSWERED,
        actor=Actor.PLANNER,
        round=1,
        note="Rebuttal: Redis is a dependency in pyproject.toml, so it is in use.",
        refs=[CodeRef("pyproject.toml")],
    )

    update = await rule(state(repo, fixed, rebutted, round=1, plan=REVISED), runtime(repo))

    show("critic: rulings", "\n\n".join(prompts.describe(i) for i in update["ledger"].values()))
    statuses = {item.status for item in update["ledger"].values()}
    assert statuses <= {Status.RESOLVED, Status.OPEN, Status.DISPUTED}
    assert update["ledger"]["OBJ-2"].status is not Status.RESOLVED


async def test_the_verifier_settles_a_fact_from_the_code(repo: Path) -> None:
    disputed = NO_CACHE.transition(
        Status.ANSWERED,
        actor=Actor.PLANNER,
        round=1,
        note="Rebuttal: The app caches ticket counts in Redis.",
        refs=[CodeRef("docker-compose.yml")],
    ).transition(
        Status.DISPUTED,
        actor=Actor.CRITIC,
        round=1,
        note="Does any code in the app connect to Redis or cache anything in it?",
    )

    update = await verify(state(repo, disputed, round=1), runtime(repo))

    found = update["ledger"]["OBJ-2"]
    show("verifier", prompts.describe(found))
    assert found.status is Status.OPEN, "no code connects to Redis: the critic is right"


async def test_the_judge_rules_on_a_deadlock(repo: Path) -> None:
    deadlocked = IN_MEMORY.transition(
        Status.ANSWERED,
        actor=Actor.PLANNER,
        round=1,
        note="Rebuttal: The app runs as one process, so a dict is shared by every request.",
        refs=[CodeRef("Dockerfile")],
    ).transition(
        Status.OPEN,
        actor=Actor.CRITIC,
        round=1,
        note="One process today, but every deploy restarts it and drops every hold.",
    )

    update = await judge(state(repo, deadlocked, round=1), runtime(repo))

    ruled = update["ledger"]["OBJ-1"]
    show("judge", prompts.describe(ruled))
    assert ruled.status is Status.RULED


async def test_the_explainer_explains_every_objection(repo: Path) -> None:
    resolved = IN_MEMORY.transition(
        Status.ANSWERED,
        actor=Actor.PLANNER,
        round=1,
        note="Fix: Holds now live in a Postgres table.",
        refs=[PlanRef("Approach")],
    ).transition(
        Status.RESOLVED,
        actor=Actor.CRITIC,
        round=1,
        note="A table survives restarts and is shared.",
        resolution=Resolution(ResolutionKind.PLAN_CHANGE, (PlanRef("Approach"),)),
    )
    deferred = raised(
        "OBJ-2",
        "Postgres may not take a hold write per buyer at the on-sale peak.",
        ObjectionType.FACTUAL,
        PlanRef("Approach"),
    ).transition(
        Status.DEFERRED,
        actor=Actor.SYSTEM,
        round=1,
        note="Unsettled at the round limit: becomes a task to run before building.",
    )
    items = [resolved, deferred]

    result = await ask(
        runtime(repo),
        "explainer",
        prompts.explain(state(repo, *items, plan=REVISED)),
        Explanation,
    )

    show("explainer", result.output.model_dump_json(indent=2))
    explained = by_id("explainer", result.output.items, items, result.cost)
    assert list(explained) == ["OBJ-1", "OBJ-2"]
