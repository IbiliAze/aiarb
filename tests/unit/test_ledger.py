import itertools

import pytest

from aiarb.domain.ledger import (
    TRANSITIONS,
    Actor,
    CodeRef,
    Event,
    Objection,
    ObjectionType,
    PlanRef,
    Resolution,
    ResolutionKind,
    Status,
    TransitionError,
)
from aiarb.domain.scoring import Severity

CODE = CodeRef("src/queue.py", 10, 24)
PLAN = PlanRef("Admission control", quote="Admit 500 users per second.")
FIXED = Resolution(ResolutionKind.PLAN_CHANGE, (PLAN,))
REFUTED = Resolution(ResolutionKind.EVIDENCE, (CODE,))


def raised(
    *, type: ObjectionType = ObjectionType.DESIGN, severity: Severity = Severity.MAJOR
) -> Objection:
    return Objection.raised(
        id="OBJ-1",
        claim="The queue loses its place on a Redis failover.",
        evidence=[CODE],
        type=type,
        severity=severity,
        round=0,
    )


def in_status(
    status: Status,
    *,
    type: ObjectionType = ObjectionType.DESIGN,
    severity: Severity = Severity.MAJOR,
) -> Objection:
    """An objection already in `status`, built directly rather than walked there."""
    item = raised(type=type, severity=severity)
    if status is Status.OPEN:
        return item
    event = Event(
        round=1,
        actor=Actor.SYSTEM,
        status=status,
        severity=severity,
        note="Set up for the test.",
        resolution=FIXED if status is Status.RESOLVED else None,
    )
    return Objection(
        id=item.id,
        claim=item.claim,
        evidence=item.evidence,
        type=item.type,
        history=(*item.history, event),
    )


def setup_for(source: Status, target: Status) -> Objection:
    """An objection in `source` that meets every precondition for moving to `target`."""
    types = TRANSITIONS[(source, target)].types
    type = min(types) if types else ObjectionType.DESIGN
    severity = Severity.MINOR if target is Status.NOTED else Severity.MAJOR
    return in_status(source, type=type, severity=severity)


def resolution_for(target: Status) -> Resolution | None:
    return FIXED if target is Status.RESOLVED else None


ALLOWED_MOVES = [
    (source, target, actor)
    for (source, target), rule in TRANSITIONS.items()
    for actor in sorted(rule.actors)
]
DISALLOWED_PAIRS = [
    (source, target)
    for source, target in itertools.product(Status, Status)
    if (source, target) not in TRANSITIONS
]
WRONG_ACTORS = [
    (source, target, actor)
    for (source, target), rule in TRANSITIONS.items()
    for actor in Actor
    if actor not in rule.actors
]
WRONG_TYPES = [
    (source, target, type)
    for (source, target), rule in TRANSITIONS.items()
    if rule.types is not None
    for type in ObjectionType
    if type not in rule.types
]


@pytest.mark.parametrize(("source", "target", "actor"), ALLOWED_MOVES)
def test_every_allowed_move_succeeds(source: Status, target: Status, actor: Actor) -> None:
    before = setup_for(source, target)

    after = before.transition(
        target, actor=actor, round=2, note="Moved.", resolution=resolution_for(target)
    )

    assert after.status is target
    assert after.severity is before.severity
    assert after.history[:-1] == before.history
    last = after.history[-1]
    assert (last.actor, last.round, last.note) == (actor, 2, "Moved.")


@pytest.mark.parametrize(("source", "target"), DISALLOWED_PAIRS)
def test_every_other_pair_of_statuses_is_refused(source: Status, target: Status) -> None:
    item = in_status(source, severity=Severity.MINOR)

    for actor in Actor:
        with pytest.raises(TransitionError, match="cannot move from"):
            item.transition(
                target, actor=actor, round=2, note="Moved.", resolution=resolution_for(target)
            )


@pytest.mark.parametrize(("source", "target", "actor"), WRONG_ACTORS)
def test_a_move_by_the_wrong_actor_is_refused(source: Status, target: Status, actor: Actor) -> None:
    item = setup_for(source, target)

    with pytest.raises(TransitionError, match=f"{actor} cannot move"):
        item.transition(
            target, actor=actor, round=2, note="Moved.", resolution=resolution_for(target)
        )


@pytest.mark.parametrize(("source", "target", "type"), WRONG_TYPES)
def test_a_move_for_the_wrong_type_is_refused(
    source: Status, target: Status, type: ObjectionType
) -> None:
    item = in_status(source, type=type)
    (actor,) = TRANSITIONS[(source, target)].actors

    with pytest.raises(TransitionError, match=f"a {type} objection cannot move"):
        item.transition(target, actor=actor, round=2, note="Moved.")


def test_the_planner_cannot_close_its_own_objection() -> None:
    answered = in_status(Status.ANSWERED)

    with pytest.raises(TransitionError, match="planner cannot move"):
        answered.transition(
            Status.RESOLVED, actor=Actor.PLANNER, round=1, note="Fixed.", resolution=FIXED
        )


class TestClosingRule:
    def test_agreement_alone_does_not_resolve(self) -> None:
        answered = in_status(Status.ANSWERED)

        with pytest.raises(TransitionError, match="only by a plan change or cited evidence"):
            answered.transition(Status.RESOLVED, actor=Actor.CRITIC, round=1, note="You're right.")

    @pytest.mark.parametrize("resolution", [FIXED, REFUTED])
    def test_a_plan_change_or_cited_evidence_resolves(self, resolution: Resolution) -> None:
        answered = in_status(Status.ANSWERED)

        resolved = answered.transition(
            Status.RESOLVED,
            actor=Actor.CRITIC,
            round=1,
            note="Accepted.",
            resolution=resolution,
        )

        assert resolved.resolution == resolution
        assert not resolved.is_open

    def test_a_resolution_must_cite_something(self) -> None:
        with pytest.raises(ValueError, match="must cite what settled it"):
            Resolution(ResolutionKind.EVIDENCE, ())

    def test_a_plan_change_must_cite_the_plan(self) -> None:
        with pytest.raises(ValueError, match="must cite the plan section that changed"):
            Resolution(ResolutionKind.PLAN_CHANGE, (CODE,))

    def test_only_a_resolving_move_carries_a_resolution(self) -> None:
        answered = in_status(Status.ANSWERED)

        with pytest.raises(TransitionError, match="only a move to resolved has a resolution"):
            answered.transition(
                Status.OPEN, actor=Actor.CRITIC, round=1, note="Rejected.", resolution=FIXED
            )

    def test_an_unresolved_objection_has_no_resolution(self) -> None:
        assert in_status(Status.RULED).resolution is None


@pytest.mark.parametrize("severity", [s for s in Severity if not s.skips_debate])
def test_only_minor_objections_skip_debate(severity: Severity) -> None:
    item = raised(severity=severity)

    with pytest.raises(TransitionError, match="must be debated, not noted"):
        item.transition(Status.NOTED, actor=Actor.SYSTEM, round=0, note="Minor.")


def test_every_move_needs_a_note() -> None:
    with pytest.raises(TransitionError, match="needs a note"):
        raised().transition(Status.ANSWERED, actor=Actor.PLANNER, round=1, note="  ")


def test_rounds_cannot_go_backwards() -> None:
    answered = raised().transition(Status.ANSWERED, actor=Actor.PLANNER, round=2, note="Fixed.")

    with pytest.raises(TransitionError, match="round 1 is before round 2"):
        answered.transition(Status.OPEN, actor=Actor.CRITIC, round=1, note="Rejected.")


def test_a_move_returns_a_new_objection_and_leaves_the_old_one_alone() -> None:
    before = raised()

    after = before.transition(Status.ANSWERED, actor=Actor.PLANNER, round=1, note="Fixed.")

    assert before.status is Status.OPEN
    assert len(before.history) == 1
    assert after is not before


def test_a_factual_dispute_settled_by_the_verifier() -> None:
    item = raised(type=ObjectionType.FACTUAL)
    item = item.transition(
        Status.ANSWERED, actor=Actor.PLANNER, round=1, note="Failover keeps the queue."
    )
    item = item.transition(
        Status.DISPUTED, actor=Actor.CRITIC, round=1, note="Not without AOF enabled."
    )
    item = item.transition(
        Status.RESOLVED,
        actor=Actor.VERIFIER,
        round=1,
        note="AOF is enabled in the Redis config.",
        resolution=Resolution(ResolutionKind.EVIDENCE, (CodeRef("infra/redis.conf", 3),)),
    )

    assert [event.status for event in item.history] == [
        Status.OPEN,
        Status.ANSWERED,
        Status.DISPUTED,
        Status.RESOLVED,
    ]
    assert [event.actor for event in item.history] == [
        Actor.CRITIC,
        Actor.PLANNER,
        Actor.CRITIC,
        Actor.VERIFIER,
    ]


class TestRescore:
    def test_records_the_new_severity_and_keeps_the_status(self) -> None:
        item = in_status(Status.DEFERRED, severity=Severity.MAJOR)

        rescored = item.rescore(Severity.MODERATE, round=3, note="Lower after the debate.")

        assert rescored.severity is Severity.MODERATE
        assert rescored.status is Status.DEFERRED
        assert rescored.history[-1].actor is Actor.SCORER

    @pytest.mark.parametrize("status", [s for s in Status if not s.is_open])
    def test_closed_objections_are_not_rescored(self, status: Status) -> None:
        with pytest.raises(TransitionError, match="only open objections are rescored"):
            in_status(status).rescore(Severity.MINOR, round=3, note="Lower.")


class TestSeverityDrops:
    def test_the_peak_counts_only_the_scorers_ratings(self) -> None:
        item = raised(severity=Severity.BLOCKER)

        assert item.peak_severity is None
        assert item.dropped_from is None

    def test_an_open_item_rated_lower_than_before_has_dropped(self) -> None:
        item = raised().rescore(Severity.BLOCKER, round=0, note="Scored.")

        dropped = item.rescore(Severity.MODERATE, round=3, note="Lower after the debate.")

        assert dropped.peak_severity is Severity.BLOCKER
        assert dropped.dropped_from is Severity.BLOCKER

    def test_rising_again_clears_the_drop(self) -> None:
        item = raised().rescore(Severity.MAJOR, round=0, note="Scored.")

        back = item.rescore(Severity.MINOR, round=2, note="Lower.").rescore(
            Severity.MAJOR, round=3, note="Higher."
        )

        assert back.dropped_from is None

    def test_a_settled_item_has_not_dropped_whatever_its_severity(self) -> None:
        item = raised().rescore(Severity.BLOCKER, round=0, note="Scored.")
        lower = item.rescore(Severity.MAJOR, round=1, note="Lower.")

        settled = lower.transition(
            Status.ANSWERED, actor=Actor.PLANNER, round=2, note="Fixed."
        ).transition(Status.RESOLVED, actor=Actor.CRITIC, round=2, note="OK.", resolution=FIXED)

        assert settled.dropped_from is None


@pytest.mark.parametrize(
    ("status", "is_open"),
    [
        (Status.OPEN, True),
        (Status.ANSWERED, True),
        (Status.DISPUTED, True),
        (Status.AWAITING_DECISION, True),
        (Status.DEFERRED, True),
        (Status.NOTED, False),
        (Status.RESOLVED, False),
        (Status.RULED, False),
        (Status.DECIDED, False),
    ],
)
def test_which_statuses_count_as_open(status: Status, is_open: bool) -> None:
    assert status.is_open is is_open
    assert in_status(status).is_open is is_open


class TestObjection:
    def test_raised_starts_open_with_the_critic(self) -> None:
        item = raised()

        assert item.status is Status.OPEN
        assert item.severity is Severity.MAJOR
        assert item.round == 0
        assert item.history[0].actor is Actor.CRITIC
        assert item.history[0].refs == (CODE,)

    def test_must_cite_the_code_or_plan(self) -> None:
        with pytest.raises(ValueError, match="must cite the code or plan line"):
            Objection.raised(
                id="OBJ-1",
                claim="This is wrong.",
                evidence=[],
                type=ObjectionType.DESIGN,
                severity=Severity.MAJOR,
                round=0,
            )

    @pytest.mark.parametrize(("id", "claim"), [("", "A claim."), ("OBJ-1", " ")])
    def test_needs_an_id_and_a_claim(self, id: str, claim: str) -> None:
        with pytest.raises(ValueError, match=r"needs an? (id|claim)"):
            Objection.raised(
                id=id,
                claim=claim,
                evidence=[CODE],
                type=ObjectionType.DESIGN,
                severity=Severity.MAJOR,
                round=0,
            )

    def test_history_must_start_with_the_raise(self) -> None:
        answered = Event(
            round=0,
            actor=Actor.PLANNER,
            status=Status.ANSWERED,
            severity=Severity.MAJOR,
            note="Fixed.",
        )

        with pytest.raises(ValueError, match="must start with the objection being raised"):
            Objection(
                id="OBJ-1",
                claim="A claim.",
                evidence=(CODE,),
                type=ObjectionType.DESIGN,
                history=(answered,),
            )

    def test_history_rounds_must_not_go_backwards(self) -> None:
        item = raised().transition(Status.ANSWERED, actor=Actor.PLANNER, round=2, note="Fixed.")
        late, early = item.history[1], item.history[0]

        with pytest.raises(ValueError, match="must not go backwards"):
            Objection(
                id="OBJ-1",
                claim="A claim.",
                evidence=(CODE,),
                type=ObjectionType.DESIGN,
                history=(early, late, Event(**{**vars(late), "round": 1})),
            )


class TestRefs:
    @pytest.mark.parametrize("path", ["", "/etc/passwd", "../other-repo/app.py", "src/../../x"])
    def test_code_refs_stay_inside_the_repo(self, path: str) -> None:
        with pytest.raises(ValueError, match="must be a path inside the repo"):
            CodeRef(path)

    @pytest.mark.parametrize(
        ("start", "end", "message"),
        [
            (None, 5, "needs a start line"),
            (0, None, "start at 1"),
            (10, 9, "before start line"),
        ],
    )
    def test_code_ref_line_ranges_are_checked(
        self, start: int | None, end: int | None, message: str
    ) -> None:
        with pytest.raises(ValueError, match=message):
            CodeRef("src/app.py", start, end)

    @pytest.mark.parametrize(("start", "end"), [(None, None), (1, None), (4, 4), (4, 9)])
    def test_valid_code_refs(self, start: int | None, end: int | None) -> None:
        assert CodeRef("src/app.py", start, end).path == "src/app.py"

    def test_plan_refs_name_a_section(self) -> None:
        with pytest.raises(ValueError, match="must name a section"):
            PlanRef(" ")


class TestEvent:
    def test_rounds_start_at_zero(self) -> None:
        with pytest.raises(ValueError, match="rounds start at 0"):
            Event(
                round=-1, actor=Actor.CRITIC, status=Status.OPEN, severity=Severity.MAJOR, note="x"
            )

    def test_needs_a_note(self) -> None:
        with pytest.raises(ValueError, match="needs a note"):
            Event(round=0, actor=Actor.CRITIC, status=Status.OPEN, severity=Severity.MAJOR, note="")


def test_sequences_given_as_lists_are_stored_as_tuples() -> None:
    item = Objection(
        id="OBJ-1",
        claim="A claim.",
        evidence=[CODE],  # type: ignore[arg-type]
        type=ObjectionType.DESIGN,
        history=list(raised().history),  # type: ignore[arg-type]
    )
    resolution = Resolution(ResolutionKind.EVIDENCE, [CODE])  # type: ignore[arg-type]

    assert item.evidence == (CODE,)
    assert isinstance(item.history, tuple)
    assert isinstance(item.history[0].refs, tuple)
    assert resolution.refs == (CODE,)
