import pytest

from aiarb.domain.ledger import Objection, PlanRef
from aiarb.output import plan
from aiarb.output.plan import Section, find, sections
from tests.output.examples import (
    DECIDED,
    DEFERRED,
    EVERY_ENDING,
    NOTED,
    PLAN,
    RESOLVED,
    RULED,
    raised,
    report,
)


class TestSections:
    def test_finds_every_heading_with_where_its_text_ends(self) -> None:
        assert sections(PLAN) == [
            Section("Waiting room", 0, 2),
            Section("Summary", 2, 6),
            Section("Queue storage", 6, 10),
            Section("Rollout", 10, 13),
        ]

    def test_ignores_headings_inside_code_blocks(self) -> None:
        text = "## Real\n\n```bash\n# a comment\n```\n\n~~~\n## Not one\n~~~\n## Also real ##\n"

        assert [s.heading for s in sections(text)] == ["Real", "Also real"]

    def test_a_plan_without_headings_has_no_sections(self) -> None:
        assert sections("Just do it.") == []


class TestFind:
    @pytest.mark.parametrize(
        ("ref", "heading"),
        [
            pytest.param(PlanRef("Queue storage"), "Queue storage", id="exact"),
            pytest.param(PlanRef("queue STORAGE"), "Queue storage", id="any case"),
            pytest.param(PlanRef("3. Queue storage"), "Queue storage", id="numbered"),
            pytest.param(PlanRef("**Rollout**"), "Rollout", id="marked up"),
            pytest.param(PlanRef("Waiting room > Rollout"), "Rollout", id="a path"),
            pytest.param(
                PlanRef("Storage design", "keep queue places in  redis"),
                "Queue storage",
                id="by the quoted line",
            ),
            pytest.param(PlanRef("Storage"), "Queue storage", id="part of a heading"),
            pytest.param(
                PlanRef("Rollout and rollback plan"), "Rollout", id="a heading within the name"
            ),
        ],
    )
    def test_matches_leniently(self, ref: PlanRef, heading: str) -> None:
        section = find(ref, PLAN, sections(PLAN))

        assert section is not None
        assert section.heading == heading

    @pytest.mark.parametrize(
        "ref",
        [PlanRef("Testing"), PlanRef("Out"), PlanRef("Testing", "a line the plan does not have")],
    )
    def test_a_section_the_plan_does_not_have_matches_nothing(self, ref: PlanRef) -> None:
        assert find(ref, PLAN, sections(PLAN)) is None

    def test_prefers_the_closest_heading_when_several_contain_the_name(self) -> None:
        text = "## Queue storage and eviction policy\n\n## Queue storage plan\n"

        section = find(PlanRef("Queue storage"), text, sections(text))

        assert section is not None
        assert section.heading == "Queue storage plan"


class TestRender:
    def test_keeps_the_plan_text_and_notes_what_it_does_not_reflect(self) -> None:
        text = plan.render(report(*EVERY_ENDING))

        assert "Hold buyers in a queue and admit them in batches." in text
        under_storage = text.split("## Queue storage")[1].split("## Rollout")[0]
        assert "> **OBJ-1, open blocker:** Title of OBJ-1. See *Before you build*." in (
            under_storage
        )
        assert "OBJ-2" not in under_storage, "resolved items are in the plan already"
        under_rollout = text.split("## Rollout")[1]
        assert "> **OBJ-4, your decision:** Fairness first." in under_rollout
        under_summary = text.split("## Summary")[1].split("## Queue storage")[0]
        assert "> **OBJ-3, ruled by the judge:** Ruled for the critic" in under_summary
        assert "OBJ-5" not in text, "minor notes went to the planner"

    def test_lists_what_to_settle_first_then_what_to_apply(self) -> None:
        text = plan.render(report(*EVERY_ENDING))

        assert text.startswith("> **Not ready: 1 open blocker.** Do not build from this plan")
        tasks = text.split("## Before you build\n\n")[1].split("\n\n")[0]
        assert tasks == (
            "- [ ] **OBJ-1, blocker.** Redis may not take one write per buyer at the on-sale "
            "peak. *Next:* Outcome of OBJ-1."
        )
        applied = text.split("## Rulings and decisions to apply\n\n")[1].split("\n\n---")[0]
        assert "- **OBJ-3, ruled by the judge, not agreed.** The batch size" in applied
        assert "- **OBJ-4, your decision.** Small batches are fairer but slower. Decided: " in (
            applied
        )
        assert text.index("## Before you build") < text.index("---\n\n# Waiting room")

    def test_without_an_explanation_uses_the_ledgers_own_words(self) -> None:
        text = plan.render(report(DEFERRED, explained=False))

        assert "*Next:* The repo has no load figures. To settle before building: load test" in (
            text
        )
        assert "> **OBJ-1, open blocker:** Redis may not take one write per buyer" in text

    def test_lists_an_item_that_cites_no_section_at_the_top_only(self) -> None:
        unplaced = raised("OBJ-6", "Nobody owns the queue.", PlanRef("Ownership"))

        text = plan.render(report(unplaced))

        assert "- [ ] **OBJ-6, major.** Nobody owns the queue." in text
        assert "> **OBJ-6" not in text

    def test_notes_each_section_once_per_item(self) -> None:
        twice = raised(
            "OBJ-6", "Two citations.", PlanRef("Rollout"), PlanRef("rollout", "Ship behind")
        )

        assert plan.render(report(twice)).count("> **OBJ-6") == 1

    @pytest.mark.parametrize(
        ("items", "banner"),
        [
            ((RESOLVED,), "> **Settled.** No objections are open.\n"),
            (
                (RESOLVED, RULED),
                "> **Settled.** No objections are open. Apply the rulings and decisions below",
            ),
            (
                (raised("OBJ-6", "Minor risk.", PlanRef("Rollout"), severity=NOTED.severity),),
                "> **Settled with 1 open item.** The plan is usable",
            ),
        ],
    )
    def test_the_banner_says_how_the_run_ended(
        self, items: tuple[Objection, ...], banner: str
    ) -> None:
        assert plan.render(report(*items)).startswith(banner)

    def test_the_banner_flags_items_rated_lower_though_unsettled(self) -> None:
        dropped = DEFERRED.rescore(DECIDED.severity, round=2, note="Lower after the debate.")

        text = plan.render(report(dropped))

        assert "Rated lower after the debate, though nobody settled them: OBJ-1." in text

    def test_an_empty_plan_says_so(self) -> None:
        assert plan.render(report(plan="")).endswith("---\n\n*No plan was written.*\n")
