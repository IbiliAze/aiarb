from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from langgraph.graph import StateGraph
from langgraph.runtime import Runtime

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
from aiarb.graph.context import RunContext
from aiarb.graph.routing import (
    after_judge,
    after_round,
    after_rule,
    after_triage,
    last_progress,
    stalled,
)
from aiarb.graph.state import RunState, initial_state
from tests.fakes import ScriptedCodeAgent, ScriptedScorer


def runtime(repo: Path, max_rounds: int = 4) -> Runtime[RunContext]:
    return Runtime(
        context=RunContext(
            charter=Charter.model_validate({"debate": {"max_rounds": max_rounds}}),
            repo=repo,
            agent=ScriptedCodeAgent([]),
            scorer=ScriptedScorer([]),
        )
    )


def state(*items: Objection, round: int = 0) -> RunState:
    base = initial_state(feature="Add a virtual waiting room.")
    base.update({"ledger": {item.id: item for item in items}, "round": round})
    return base


def raised(id: str, type: ObjectionType = ObjectionType.DESIGN) -> Objection:
    return Objection.raised(
        id=id,
        claim=f"Claim {id}.",
        evidence=[CodeRef("src/queue.py", 10)],
        type=type,
        severity=Severity.MAJOR,
        round=0,
    )


def noted(item: Objection) -> Objection:
    """Scored minor at triage and passed on as a note."""
    item = item.rescore(Severity.MINOR, round=0, note="Scored 0.10.")
    return item.transition(Status.NOTED, actor=Actor.SYSTEM, round=0, note="Minor.")


def answered(item: Objection, round: int) -> Objection:
    return item.transition(Status.ANSWERED, actor=Actor.PLANNER, round=round, note="Fixed.")


def rejected(item: Objection, round: int) -> Objection:
    """A round in which the critic was not persuaded."""
    return answered(item, round).transition(
        Status.OPEN, actor=Actor.CRITIC, round=round, note="Still wrong."
    )


def accepted(item: Objection, round: int) -> Objection:
    return answered(item, round).transition(
        Status.RESOLVED,
        actor=Actor.CRITIC,
        round=round,
        note="OK.",
        resolution=Resolution(ResolutionKind.PLAN_CHANGE, (PlanRef("Queue storage"),)),
    )


def disputed(item: Objection, round: int) -> Objection:
    return answered(item, round).transition(
        Status.DISPUTED, actor=Actor.CRITIC, round=round, note="Not so."
    )


def unverifiable(item: Objection, round: int) -> Objection:
    return disputed(item, round).transition(
        Status.DEFERRED, actor=Actor.VERIFIER, round=round, note="Needs a load test."
    )


def awaiting_decision(item: Objection, round: int) -> Objection:
    return item.transition(
        Status.AWAITING_DECISION, actor=Actor.SYSTEM, round=round, note="For the user."
    )


class TestAfterTriage:
    def test_open_objections_are_debated(self) -> None:
        assert after_triage(state(raised("OBJ-1"), noted(raised("OBJ-2")))) == "respond"

    def test_minor_notes_alone_skip_the_debate(self) -> None:
        assert after_triage(state(noted(raised("OBJ-1")))) == "rescore"

    def test_no_objections_skip_the_debate(self) -> None:
        assert after_triage(state()) == "rescore"


class TestAfterRule:
    def test_disputed_facts_go_to_the_verifier(self, tmp_path: Path) -> None:
        ledger = state(disputed(raised("OBJ-1"), 1), rejected(raised("OBJ-2"), 1), round=1)

        assert after_rule(ledger, runtime(tmp_path)) == "verify"

    def test_disputes_are_verified_even_at_the_round_limit(self, tmp_path: Path) -> None:
        ledger = state(disputed(raised("OBJ-1"), 4), round=4)

        assert after_rule(ledger, runtime(tmp_path, max_rounds=4)) == "verify"

    def test_without_disputes_the_round_ends(self, tmp_path: Path) -> None:
        ledger = state(rejected(raised("OBJ-1"), 1), round=1)

        assert after_rule(ledger, runtime(tmp_path)) == "respond"


class TestAfterRound:
    def test_open_items_go_another_round(self, tmp_path: Path) -> None:
        ledger = state(accepted(raised("OBJ-1"), 1), rejected(raised("OBJ-2"), 1), round=1)

        assert after_round(ledger, runtime(tmp_path)) == "respond"

    def test_nothing_open_ends_the_debate(self, tmp_path: Path) -> None:
        ledger = state(accepted(raised("OBJ-1"), 1), noted(raised("OBJ-2")), round=1)

        assert after_round(ledger, runtime(tmp_path)) == "rescore"

    def test_deferred_items_are_not_debated_again(self, tmp_path: Path) -> None:
        ledger = state(unverifiable(raised("OBJ-1", ObjectionType.FACTUAL), 1), round=1)

        assert after_round(ledger, runtime(tmp_path)) == "rescore"

    def test_items_open_at_the_round_limit_go_to_the_judge(self, tmp_path: Path) -> None:
        item = raised("OBJ-1")
        for round in (1, 2):
            item = rejected(item, round)
        other = accepted(raised("OBJ-2"), 2)

        assert after_round(state(item, other, round=2), runtime(tmp_path, max_rounds=2)) == "judge"

    def test_settling_in_the_last_round_skips_the_judge(self, tmp_path: Path) -> None:
        ledger = state(accepted(raised("OBJ-1"), 2), round=2)

        assert after_round(ledger, runtime(tmp_path, max_rounds=2)) == "rescore"

    def test_a_stalled_debate_goes_to_the_judge_early(self, tmp_path: Path) -> None:
        item = raised("OBJ-1")
        for round in (1, 2):
            item = rejected(item, round)

        assert after_round(state(item, round=2), runtime(tmp_path, max_rounds=4)) == "judge"


class TestStalled:
    def test_one_quiet_round_is_not_a_stall(self) -> None:
        assert not stalled(state(rejected(raised("OBJ-1"), 1), round=1))

    def test_two_quiet_rounds_are(self) -> None:
        item = rejected(rejected(raised("OBJ-1"), 1), 2)

        assert stalled(state(item, round=2))

    def test_closing_any_item_counts_as_progress(self) -> None:
        stuck = rejected(rejected(raised("OBJ-1"), 1), 2)
        closed = accepted(rejected(raised("OBJ-2"), 1), 2)

        assert last_progress(state(stuck, closed, round=2)) == 2
        assert not stalled(state(stuck, closed, round=2))

    def test_the_clock_runs_from_the_last_progress(self) -> None:
        stuck = rejected(rejected(rejected(raised("OBJ-1"), 1), 2), 3)
        closed = accepted(raised("OBJ-2"), 1)

        assert last_progress(state(stuck, closed, round=3)) == 1
        assert stalled(state(stuck, closed, round=3))

    @pytest.mark.parametrize(
        ("leave", "expected"),
        [
            pytest.param(lambda item: unverifiable(item, 2), 2, id="deferred by the verifier"),
            pytest.param(lambda item: disputed(item, 2), 0, id="still disputed"),
            pytest.param(lambda item: answered(item, 2), 0, id="still answered"),
        ],
    )
    def test_progress_means_leaving_the_debate(
        self, leave: Callable[[Objection], Objection], expected: int
    ) -> None:
        item = leave(rejected(raised("OBJ-1", ObjectionType.FACTUAL), 1))

        assert last_progress(state(item, round=2)) == expected

    def test_triage_notes_do_not_count_as_debate_progress(self) -> None:
        ledger = state(noted(raised("OBJ-1")), rejected(rejected(raised("OBJ-2"), 1), 2), round=2)

        assert stalled(ledger)


class TestAfterJudge:
    def test_trade_offs_go_to_the_user(self) -> None:
        trade_off = awaiting_decision(raised("OBJ-1", ObjectionType.TRADE_OFF), 4)

        assert after_judge(state(trade_off, round=4)) == "decide"

    def test_otherwise_the_run_moves_on(self) -> None:
        fact = unverifiable(raised("OBJ-1", ObjectionType.FACTUAL), 4)

        assert after_judge(state(fact, round=4)) == "rescore"


@pytest.mark.parametrize(
    ("router", "targets"),
    [
        (after_triage, {"respond", "rescore"}),
        (after_rule, {"verify", "respond", "judge", "rescore"}),
        (after_round, {"respond", "judge", "rescore"}),
        (after_judge, {"decide", "rescore"}),
    ],
)
def test_langgraph_reads_each_routers_edges_from_its_return_type(
    router: Callable[..., Any], targets: set[str]
) -> None:
    graph = StateGraph(RunState, context_schema=RunContext)
    graph.add_node("source", lambda state: {})
    graph.add_conditional_edges("source", router)

    ends = graph.branches["source"][router.__name__].ends

    assert ends is not None, "the return type did not resolve, so the edges are unknown"
    assert set(ends) == targets
