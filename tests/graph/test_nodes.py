from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
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
from aiarb.domain.outcome import RunStatus
from aiarb.domain.scoring import Severity
from aiarb.graph import prompts
from aiarb.graph.context import RunContext
from aiarb.graph.nodes import (
    critique,
    draft_plan,
    finalise,
    judge,
    rescore,
    respond,
    rule,
    scan,
    triage,
    verify,
)
from aiarb.graph.state import RunState, initial_state
from aiarb.ports.calls import Cost, RetryableCallError
from aiarb.ports.scorer import Judgement, Level
from tests.fakes import AgentStep, ScorerStep, ScriptedCodeAgent, ScriptedScorer

CODE = {"kind": "code", "path": "src/queue.py", "start_line": 10}
PLAN = {"kind": "plan", "section": "Queue storage"}


def runtime(
    repo: Path,
    agent: list[dict[str, Any]] | None = None,
    scorer: list[dict[str, Any]] | None = None,
) -> Runtime[RunContext]:
    scripted = ScriptedScorer([ScorerStep.model_validate(s) for s in scorer or []])
    return Runtime(
        context=RunContext(
            charter=Charter(),
            repo=repo,
            agent=ScriptedCodeAgent([AgentStep.model_validate(s) for s in agent or []]),
            scorer=scripted,
            confirmer=scripted,
        )
    )


def fakes(rt: Runtime[RunContext]) -> tuple[ScriptedCodeAgent, ScriptedScorer]:
    agent, scorer = rt.context.agent, rt.context.scorer
    assert isinstance(agent, ScriptedCodeAgent)
    assert isinstance(scorer, ScriptedScorer)
    return agent, scorer


def objection(
    id: str,
    *,
    type: ObjectionType = ObjectionType.DESIGN,
    severity: Severity = Severity.MAJOR,
    status: Status = Status.OPEN,
) -> Objection:
    """An objection in `status`, reached by the moves the debate would make."""
    item = Objection.raised(
        id=id,
        claim=f"Claim {id}.",
        evidence=[CodeRef("src/queue.py", 10)],
        type=type,
        severity=severity,
        round=0,
    )
    if status is Status.OPEN:
        return item
    if status is Status.NOTED:
        return item.transition(Status.NOTED, actor=Actor.SYSTEM, round=0, note="Minor.")
    item = item.transition(Status.ANSWERED, actor=Actor.PLANNER, round=1, note="Fixed.")
    if status is Status.ANSWERED:
        return item
    if status is Status.RESOLVED:
        resolution = Resolution(ResolutionKind.PLAN_CHANGE, (PlanRef("Queue storage"),))
        return item.transition(
            Status.RESOLVED, actor=Actor.CRITIC, round=1, note="OK.", resolution=resolution
        )
    item = item.transition(Status.DISPUTED, actor=Actor.CRITIC, round=1, note="Not so.")
    if status is Status.DISPUTED:
        return item
    raise ValueError(f"no route to {status} in this helper")


def scored(item: Objection, severity: Severity) -> Objection:
    """The objection as triage left it, at `severity`."""
    return item.rescore(severity, round=0, note="Scored.")


class AnsweringScorer:
    """A scorer that gives one answer to every impact question."""

    def __init__(self, answer: Judgement[float]) -> None:
        self.answer = answer

    async def score(self, subject: str, question: str, levels: Sequence[Level]) -> Judgement[float]:
        return self.answer

    async def choose(self, subject: str, question: str, options: Sequence[str]) -> Judgement[str]:
        raise NotImplementedError

    async def yes_no(self, subject: str, question: str) -> Judgement[bool]:
        raise NotImplementedError


def state(*items: Objection, round: int = 0, **fields: Any) -> RunState:
    base = initial_state(feature="Add a virtual waiting room.")
    base.update({"ledger": {item.id: item for item in items}, "round": round, **fields})  # type: ignore[typeddict-item]
    return base


def test_scan_writes_the_fact_sheet(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("")

    update = scan(state(), runtime(tmp_path))

    assert update["facts"].startswith("# Repo facts\n")
    assert "- Languages: Python (1 file)" in update["facts"]


class TestDraftPlan:
    async def test_the_planner_drafts_on_its_own_model_and_session(self, tmp_path: Path) -> None:
        rt = runtime(
            tmp_path,
            agent=[{"schema": "PlanDraft", "output": {"plan": "# Queue"}, "cost": {"usd": 2}}],
        )

        update = await draft_plan(state(), rt)

        agent, _ = fakes(rt)
        assert update == {
            "plan": "# Queue",
            "costs": {"planner": Cost(usd=2)},
            "sessions": {"planner": "session-1"},
        }
        assert agent.calls[0].model == Charter().models.planner
        assert agent.calls[0].repo == tmp_path.resolve()

    async def test_given_plans_are_used_without_calling_the_planner(self, tmp_path: Path) -> None:
        rt = runtime(tmp_path)

        update = await draft_plan(state(given_plans=["# Part one", "# Part two"]), rt)

        assert update == {"plan": "# Part one\n\n---\n\n# Part two"}
        assert fakes(rt)[0].calls == []


class TestCritique:
    async def test_raises_objections_numbered_after_the_existing_ones(self, tmp_path: Path) -> None:
        output = {
            "objections": [
                {
                    "claim": "Failover loses places.",
                    "type": "factual",
                    "severity": "major",
                    "evidence": [CODE, PLAN],
                },
                {
                    "claim": "Hard-coded limit.",
                    "type": "design",
                    "severity": "minor",
                    "evidence": [PLAN],
                },
            ]
        }
        rt = runtime(tmp_path, agent=[{"schema": "Critique", "output": output}])

        update = await critique(state(objection("OBJ-1"), round=0), rt)

        raised = update["ledger"]
        assert list(raised) == ["OBJ-2", "OBJ-3"]
        assert raised["OBJ-2"].type is ObjectionType.FACTUAL
        assert raised["OBJ-2"].evidence == (CodeRef("src/queue.py", 10), PlanRef("Queue storage"))
        assert raised["OBJ-3"].severity is Severity.MINOR
        assert update["sessions"] == {"critic": "session-1"}


class TestTriage:
    async def test_scores_new_objections_and_notes_minor_ones(self, tmp_path: Path) -> None:
        rt = runtime(
            tmp_path,
            scorer=[
                {"call": "score", "contains": "OBJ-1", "value": 0.9, "cost": {"usd": 0.01}},
                {"call": "score", "contains": "OBJ-2", "value": 0.1, "cost": {"usd": 0.01}},
            ],
        )

        update = await triage(state(objection("OBJ-1"), objection("OBJ-2")), rt)

        blocker, minor = update["ledger"]["OBJ-1"], update["ledger"]["OBJ-2"]
        assert (blocker.status, blocker.severity) == (Status.OPEN, Severity.BLOCKER)
        assert blocker.history[-1].actor is Actor.SCORER
        assert (minor.status, minor.severity) == (Status.NOTED, Severity.MINOR)
        assert update["costs"] == {"scorer": Cost(usd=0.02)}

    async def test_asks_about_impact_with_the_objection_and_charter_as_the_subject(
        self, tmp_path: Path
    ) -> None:
        rt = runtime(tmp_path, scorer=[{"call": "score", "value": 0.5}])

        await triage(state(objection("OBJ-1")), rt)

        _, subject, question = fakes(rt)[1].calls[0]
        assert subject.startswith("OBJ-1 (design, major, open): Claim OBJ-1.")
        assert "The charter for this codebase:" in subject
        assert question == prompts.IMPACT

    async def test_records_the_score_and_any_escalation(self, tmp_path: Path) -> None:
        unsure = Judgement(value=0.3, confidence=0.4, cost=Cost(usd=0.25))
        sure = Judgement(value=0.6, confidence=0.9, cost=Cost(usd=0.5)).replacing(unsure)
        rt = runtime(tmp_path)
        context = replace(rt.context, scorer=AnsweringScorer(sure))

        update = await triage(state(objection("OBJ-1")), Runtime(context=context))

        note = update["ledger"]["OBJ-1"].history[-1].note
        assert note == (
            "Scored 0.60 for impact (major), with confidence 0.90. The fallback scorer gave "
            "this after the first scorer's 0.30, with confidence 0.40, fell below the "
            "confidence threshold."
        )
        assert update["costs"] == {"scorer": Cost(usd=0.75)}

    async def test_leaves_already_triaged_objections_alone(self, tmp_path: Path) -> None:
        triaged = objection("OBJ-1").rescore(Severity.MAJOR, round=0, note="Scored.")

        assert await triage(state(triaged), runtime(tmp_path)) == {}


class TestRespond:
    async def test_starts_a_round_answering_every_open_objection(self, tmp_path: Path) -> None:
        output = {
            "plan": "# Revised",
            "answers": [
                {"id": "OBJ-1", "response": "fix", "note": "Persist places.", "changed": [PLAN]},
                {"id": "OBJ-2", "response": "rebuttal", "note": "Handled.", "evidence": [CODE]},
            ],
        }
        rt = runtime(
            tmp_path,
            agent=[
                {"schema": "PlanDraft", "output": {"plan": "# Plan"}},
                {"schema": "Responses", "output": output},
            ],
        )
        planner = await draft_plan(state(), rt)
        before = state(
            objection("OBJ-1"),
            objection("OBJ-2"),
            objection("OBJ-3", severity=Severity.MINOR, status=Status.NOTED),
            sessions=planner["sessions"],
        )

        update = await respond(before, rt)

        assert update["round"] == 1
        assert update["plan"] == "# Revised"
        assert list(update["ledger"]) == ["OBJ-1", "OBJ-2"]
        fixed = update["ledger"]["OBJ-1"]
        assert fixed.status is Status.ANSWERED
        assert fixed.history[-1].note == "Fix: Persist places."
        assert fixed.history[-1].refs == (PlanRef("Queue storage"),)
        rebutted = update["ledger"]["OBJ-2"]
        assert rebutted.history[-1].note == "Rebuttal: Handled."
        assert rebutted.history[-1].refs == (CodeRef("src/queue.py", 10),)
        request = fakes(rt)[0].calls[1]
        assert request.session_id == "session-1"
        assert "Minor notes" in request.prompt
        assert "OBJ-3" in request.prompt

    async def test_an_answer_set_that_skips_an_objection_is_retryable(self, tmp_path: Path) -> None:
        fix = {"id": "OBJ-1", "response": "fix", "note": "x", "changed": [PLAN]}
        output = {"plan": "# Revised", "answers": [fix]}
        rt = runtime(
            tmp_path, agent=[{"schema": "Responses", "output": output, "cost": {"usd": 1}}]
        )

        with pytest.raises(RetryableCallError, match="missing: OBJ-2") as caught:
            await respond(state(objection("OBJ-1"), objection("OBJ-2")), rt)

        assert caught.value.cost == Cost(usd=1)

    async def test_nothing_open_means_nothing_to_do(self, tmp_path: Path) -> None:
        resolved = objection("OBJ-1", status=Status.RESOLVED)

        assert await respond(state(resolved), runtime(tmp_path)) == {}

    async def test_puts_the_most_severe_objections_first(self, tmp_path: Path) -> None:
        output = {
            "plan": "# Revised",
            "answers": [
                {"id": id, "response": "fix", "note": "Done.", "changed": [PLAN]}
                for id in ("OBJ-1", "OBJ-2", "OBJ-3")
            ],
        }
        rt = runtime(tmp_path, agent=[{"schema": "Responses", "output": output}])
        items = (
            objection("OBJ-1", severity=Severity.MODERATE),
            objection("OBJ-2", severity=Severity.BLOCKER),
            objection("OBJ-3", severity=Severity.MODERATE),
        )

        await respond(state(*items), rt)

        prompt = fakes(rt)[0].calls[0].prompt
        order = sorted(("OBJ-1", "OBJ-2", "OBJ-3"), key=lambda id: prompt.index(f"{id} ("))
        assert order == ["OBJ-2", "OBJ-1", "OBJ-3"]


class TestRule:
    async def test_the_critic_accepts_rejects_or_disputes(self, tmp_path: Path) -> None:
        output = {
            "rulings": [
                {
                    "id": "OBJ-1",
                    "verdict": "accept",
                    "note": "Fixed.",
                    "resolution": {"kind": "plan_change", "sections": [PLAN]},
                },
                {"id": "OBJ-2", "verdict": "reject", "note": "Still loses places."},
                {"id": "OBJ-3", "verdict": "dispute", "note": "AOF is off in prod."},
            ]
        }
        rt = runtime(tmp_path, agent=[{"schema": "Rulings", "output": output}])
        answered = [objection(f"OBJ-{n}", status=Status.ANSWERED) for n in (1, 2, 3)]

        update = await rule(state(*answered, round=1), rt)

        ledger = update["ledger"]
        assert ledger["OBJ-1"].status is Status.RESOLVED
        assert ledger["OBJ-1"].resolution == Resolution(
            ResolutionKind.PLAN_CHANGE, (PlanRef("Queue storage"),)
        )
        assert ledger["OBJ-2"].status is Status.OPEN
        assert ledger["OBJ-3"].status is Status.DISPUTED
        assert {item.history[-1].actor for item in ledger.values()} == {Actor.CRITIC}


class TestVerify:
    async def test_each_dispute_is_checked_on_its_own(self, tmp_path: Path) -> None:
        rt = runtime(
            tmp_path,
            agent=[
                {
                    "schema": "Verdict",
                    "contains": "OBJ-1",
                    "cost": {"usd": 0.5},
                    "output": {
                        "result": {
                            "finding": "planner_right",
                            "note": "AOF on.",
                            "evidence": [CODE],
                        }
                    },
                },
                {
                    "schema": "Verdict",
                    "contains": "OBJ-2",
                    "cost": {"usd": 0.5},
                    "output": {
                        "result": {
                            "finding": "critic_right",
                            "note": "No retry.",
                            "evidence": [CODE],
                        }
                    },
                },
                {
                    "schema": "Verdict",
                    "contains": "OBJ-3",
                    "cost": {"usd": 0.5},
                    "output": {
                        "result": {
                            "finding": "unverifiable",
                            "note": "It depends on load.",
                            "task": "Load-test Redis at 50k writes a second.",
                        }
                    },
                },
            ],
        )
        disputed = [objection(f"OBJ-{n}", status=Status.DISPUTED) for n in (1, 2, 3)]

        update = await verify(state(*disputed, round=1), rt)

        ledger = update["ledger"]
        assert ledger["OBJ-1"].status is Status.RESOLVED
        assert ledger["OBJ-1"].resolution == Resolution(
            ResolutionKind.EVIDENCE, (CodeRef("src/queue.py", 10),)
        )
        assert ledger["OBJ-2"].status is Status.OPEN
        assert ledger["OBJ-2"].history[-1].refs == (CodeRef("src/queue.py", 10),)
        assert ledger["OBJ-3"].status is Status.DEFERRED
        assert ledger["OBJ-3"].history[-1].note == (
            "It depends on load. To settle before building: Load-test Redis at 50k writes a second."
        )
        assert update["costs"] == {"verifier": Cost(usd=1.5)}
        assert all(call.session_id is None for call in fakes(rt)[0].calls)


class TestJudge:
    async def test_leftovers_are_handed_on_by_type(self, tmp_path: Path) -> None:
        rt = runtime(
            tmp_path,
            agent=[
                {
                    "schema": "JudgeRuling",
                    "contains": "OBJ-3",
                    "cost": {"usd": 1},
                    "output": {
                        "favours": "critic",
                        "ruling": "Persist places in Postgres.",
                        "reason": "Redis loses them on failover.",
                        "evidence": [CODE],
                    },
                },
            ],
        )
        leftovers = [
            objection("OBJ-1", type=ObjectionType.FACTUAL),
            objection("OBJ-2", type=ObjectionType.TRADE_OFF),
            objection("OBJ-3", type=ObjectionType.DESIGN),
        ]

        update = await judge(state(*leftovers, round=4), rt)

        ledger = update["ledger"]
        assert ledger["OBJ-1"].status is Status.DEFERRED
        assert ledger["OBJ-2"].status is Status.AWAITING_DECISION
        assert ledger["OBJ-3"].status is Status.RULED
        ruled = ledger["OBJ-3"].history[-1]
        assert ruled.note == (
            "Ruled for the critic: Persist places in Postgres. Why: Redis loses them on failover."
        )
        assert ruled.refs == (CodeRef("src/queue.py", 10),)
        assert {ledger[id].history[-1].round for id in ledger} == {4}
        assert update["costs"] == {"judge": Cost(usd=1)}

    async def test_no_judge_call_without_a_design_deadlock(self, tmp_path: Path) -> None:
        rt = runtime(tmp_path)

        update = await judge(state(objection("OBJ-1", type=ObjectionType.FACTUAL)), rt)

        assert "costs" not in update
        assert fakes(rt)[0].calls == []


class TestRescore:
    async def test_records_only_changed_severities(self, tmp_path: Path) -> None:
        rt = runtime(
            tmp_path,
            scorer=[
                {"call": "score", "contains": "OBJ-1", "value": 0.6, "cost": {"usd": 0.01}},
                {"call": "score", "contains": "OBJ-2", "value": 0.3, "cost": {"usd": 0.01}},
            ],
        )
        major = objection("OBJ-1", severity=Severity.MAJOR)
        was_major = objection("OBJ-2", severity=Severity.MAJOR)
        closed = objection("OBJ-3", status=Status.RESOLVED)

        update = await rescore(state(major, was_major, closed, round=2), rt)

        assert list(update["ledger"]) == ["OBJ-2"]
        assert update["ledger"]["OBJ-2"].severity is Severity.MODERATE
        assert update["costs"] == {"scorer": Cost(usd=0.02)}

    async def test_a_language_model_confirms_each_blocker(self, tmp_path: Path) -> None:
        rt = runtime(
            tmp_path,
            scorer=[
                {"call": "score", "value": 0.9},
                {"call": "yes_no", "value": True, "confidence": 0.8, "cost": {"usd": 0.03}},
            ],
        )
        blocker = scored(objection("OBJ-1"), Severity.BLOCKER)

        update = await rescore(state(blocker, round=2), rt)

        confirmed = update["ledger"]["OBJ-1"]
        assert confirmed.severity is Severity.BLOCKER
        assert confirmed.history[-1].note == (
            "A language model confirmed it as a blocker (yes, with confidence 0.80)."
        )
        _, subject, question = fakes(rt)[1].calls[1]
        assert "OBJ-1" in subject
        assert question == prompts.IS_BLOCKER
        assert update["costs"] == {"scorer": Cost(usd=0.03)}

    async def test_a_blocker_confidently_rejected_is_rated_major_and_flagged(
        self, tmp_path: Path
    ) -> None:
        rt = runtime(
            tmp_path,
            scorer=[
                {"call": "score", "value": 0.85},
                {"call": "yes_no", "value": False, "confidence": 0.9},
            ],
        )
        major = scored(objection("OBJ-1"), Severity.MAJOR)

        update = await rescore(state(major, round=2), rt)

        item = update["ledger"]["OBJ-1"]
        rescored, rejected = item.history[-2:]
        assert rescored.severity is Severity.BLOCKER
        assert rescored.note.startswith("Rescored after the debate: 0.85 for impact (blocker)")
        assert rejected.severity is Severity.MAJOR
        assert rejected.note == (
            "A language model did not confirm it as a blocker (no, with confidence 0.90), so "
            "it is rated major. Flagged: it was rated blocker before and is still open, though "
            "nobody settled it."
        )
        assert item.dropped_from is Severity.BLOCKER

    async def test_an_unsure_rejection_leaves_the_blocker_standing(self, tmp_path: Path) -> None:
        rt = runtime(
            tmp_path,
            scorer=[
                {"call": "score", "value": 0.9},
                {"call": "yes_no", "value": False, "confidence": 0.5},
            ],
        )
        blocker = scored(objection("OBJ-1"), Severity.BLOCKER)

        update = await rescore(state(blocker, round=2), rt)

        item = update["ledger"]["OBJ-1"]
        assert item.severity is Severity.BLOCKER
        assert "unsure (no, with confidence 0.50), so the call stands" in item.history[-1].note

    async def test_a_drop_without_settling_is_flagged(self, tmp_path: Path) -> None:
        rt = runtime(tmp_path, scorer=[{"call": "score", "value": 0.3}])
        blocker = scored(objection("OBJ-1"), Severity.BLOCKER)

        update = await rescore(state(blocker, round=2), rt)

        item = update["ledger"]["OBJ-1"]
        assert item.severity is Severity.MODERATE
        assert item.history[-1].note.endswith(
            "Flagged: it was rated blocker before and is still open, though nobody settled it."
        )


@pytest.mark.parametrize(
    ("items", "status"),
    [
        ((), RunStatus.SETTLED),
        ((objection("OBJ-1", severity=Severity.MODERATE),), RunStatus.SETTLED_WITH_OPEN_ITEMS),
        ((objection("OBJ-1", severity=Severity.BLOCKER),), RunStatus.NOT_READY),
    ],
)
def test_finalise_records_how_the_run_ended(
    items: tuple[Objection, ...], status: RunStatus
) -> None:
    assert finalise(state(*items)) == {"status": status}
