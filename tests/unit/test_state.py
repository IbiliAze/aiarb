import operator
from typing import Any

import pytest
from langgraph.errors import InvalidUpdateError
from langgraph.graph import END, START, StateGraph

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
from aiarb.graph.state import (
    LedgerConflict,
    RunState,
    add_costs,
    checkpoint_serializer,
    initial_state,
    merge_ledger,
    merge_sessions,
    total_cost,
)
from aiarb.ports.calls import Cost


def raised(id: str) -> Objection:
    return Objection.raised(
        id=id,
        claim=f"Claim {id}.",
        evidence=[CodeRef("src/queue.py", 10)],
        type=ObjectionType.DESIGN,
        severity=Severity.MAJOR,
        round=0,
    )


def answered(item: Objection, note: str = "Fixed.") -> Objection:
    return item.transition(Status.ANSWERED, actor=Actor.PLANNER, round=1, note=note)


class TestMergeLedger:
    def test_adds_new_objections(self) -> None:
        first, second = raised("OBJ-1"), raised("OBJ-2")

        merged = merge_ledger({"OBJ-1": first}, {"OBJ-2": second})

        assert merged == {"OBJ-1": first, "OBJ-2": second}

    def test_replaces_an_objection_whose_history_the_update_extends(self) -> None:
        before = raised("OBJ-1")
        after = answered(before)

        assert merge_ledger({"OBJ-1": before}, {"OBJ-1": after}) == {"OBJ-1": after}

    def test_an_unchanged_objection_is_accepted(self) -> None:
        item = raised("OBJ-1")

        assert merge_ledger({"OBJ-1": item}, {"OBJ-1": item}) == {"OBJ-1": item}

    def test_a_stale_update_is_refused(self) -> None:
        before = raised("OBJ-1")

        with pytest.raises(LedgerConflict, match="does not extend the ledger's history"):
            merge_ledger({"OBJ-1": answered(before)}, {"OBJ-1": before})

    def test_two_changes_from_the_same_starting_point_conflict(self) -> None:
        before = raised("OBJ-1")
        ledger = merge_ledger({"OBJ-1": before}, {"OBJ-1": answered(before, "Fix A.")})

        with pytest.raises(LedgerConflict, match="OBJ-1"):
            merge_ledger(ledger, {"OBJ-1": answered(before, "Fix B.")})

    def test_an_objection_filed_under_the_wrong_id_is_refused(self) -> None:
        with pytest.raises(LedgerConflict, match="filed under 'OBJ-9'"):
            merge_ledger({}, {"OBJ-9": raised("OBJ-1")})

    def test_does_not_change_the_current_ledger(self) -> None:
        current = {"OBJ-1": raised("OBJ-1")}

        merge_ledger(current, {"OBJ-2": raised("OBJ-2")})

        assert list(current) == ["OBJ-1"]


class TestAddCosts:
    def test_adds_spend_per_role(self) -> None:
        totals = add_costs(
            {"planner": Cost(usd=2, input_tokens=100)},
            {"planner": Cost(usd=1, input_tokens=50), "critic": Cost(usd=3)},
        )

        assert totals == {"planner": Cost(usd=3, input_tokens=150), "critic": Cost(usd=3)}

    def test_total_cost_adds_every_role(self) -> None:
        state = initial_state(feature="A waiting room.")
        state["costs"] = {"planner": Cost(usd=2), "critic": Cost(usd=3), "scorer": Cost(usd=0.5)}

        assert total_cost(state) == Cost(usd=5.5)


def test_merge_sessions_keeps_the_latest_per_role() -> None:
    merged = merge_sessions(
        {"planner": "session-1", "critic": "session-2"}, {"planner": "session-3"}
    )

    assert merged == {"planner": "session-3", "critic": "session-2"}


class TestInitialState:
    def test_starts_empty_at_round_zero(self) -> None:
        state = initial_state(feature="A waiting room.")

        assert state["feature"] == "A waiting room."
        assert state["given_plans"] == []
        assert (state["plan"], state["facts"], state["round"]) == ("", "", 0)
        assert (state["ledger"], state["costs"], state["sessions"]) == ({}, {}, {})

    def test_every_input_is_optional(self) -> None:
        assert initial_state()["feature"] is None


class TestInAGraph:
    """The reducers as LangGraph applies them, with nodes running in parallel."""

    @staticmethod
    def fan_out(left: dict[str, Any], right: dict[str, Any]) -> Any:
        graph = StateGraph(RunState)
        graph.add_node("left", lambda _: left)
        graph.add_node("right", lambda _: right)
        graph.add_edge(START, "left")
        graph.add_edge(START, "right")
        graph.add_edge("left", END)
        graph.add_edge("right", END)
        return graph.compile()

    def test_parallel_updates_to_different_objections_combine(self) -> None:
        one, two = raised("OBJ-1"), raised("OBJ-2")
        start = initial_state(feature="A waiting room.")
        start["ledger"] = {"OBJ-1": one, "OBJ-2": two}
        graph = self.fan_out(
            {"ledger": {"OBJ-1": answered(one)}, "costs": {"planner": Cost(usd=1)}},
            {"ledger": {"OBJ-2": answered(two)}, "costs": {"planner": Cost(usd=2)}},
        )

        final = graph.invoke(start)

        assert final["ledger"] == {"OBJ-1": answered(one), "OBJ-2": answered(two)}
        assert final["costs"] == {"planner": Cost(usd=3)}

    def test_parallel_changes_to_the_same_objection_fail_the_run(self) -> None:
        one = raised("OBJ-1")
        start = initial_state(feature="A waiting room.")
        start["ledger"] = {"OBJ-1": one}
        graph = self.fan_out(
            {"ledger": {"OBJ-1": answered(one, "Fix A.")}},
            {"ledger": {"OBJ-1": answered(one, "Fix B.")}},
        )

        with pytest.raises(LedgerConflict):
            graph.invoke(start)

    def test_parallel_writes_to_a_plain_value_fail_the_run(self) -> None:
        graph = self.fan_out({"plan": "Plan A."}, {"plan": "Plan B."})

        with pytest.raises(InvalidUpdateError):
            graph.invoke(initial_state(feature="A waiting room."))


def test_state_survives_the_checkpoint_serializer() -> None:
    # The serializer refuses types missing from CHECKPOINT_TYPES and returns them as
    # plain dicts, so a missing registration fails the equality check below.
    state = initial_state(feature="A waiting room.", given_plans=["Use Redis."])
    plan_change = Resolution(ResolutionKind.PLAN_CHANGE, (PlanRef("Queue storage", "AOF on."),))
    resolved = answered(raised("OBJ-2")).transition(
        Status.RESOLVED, actor=Actor.CRITIC, round=1, note="Accepted.", resolution=plan_change
    )
    item = answered(raised("OBJ-1"))
    state["ledger"] = {"OBJ-1": item, "OBJ-2": resolved}
    state["costs"] = {"planner": Cost(usd=1.25, input_tokens=10, output_tokens=2)}
    state["sessions"] = {"planner": "session-1"}
    serializer = checkpoint_serializer()

    restored = serializer.loads_typed(serializer.dumps_typed(state))

    assert restored == state
    assert restored["ledger"]["OBJ-1"].status is Status.ANSWERED
    assert restored["ledger"]["OBJ-1"].history[0].severity is Severity.MAJOR


def test_cost_addition_is_what_the_cost_reducer_relies_on() -> None:
    assert operator.add(Cost(usd=1), Cost(usd=2)) == Cost(usd=3)


def test_a_restored_objection_can_still_be_updated() -> None:
    serializer = checkpoint_serializer()
    before = raised("OBJ-1")
    restored = serializer.loads_typed(serializer.dumps_typed({"OBJ-1": before}))

    merged = merge_ledger(restored, {"OBJ-1": answered(before)})

    assert merged["OBJ-1"].status is Status.ANSWERED
