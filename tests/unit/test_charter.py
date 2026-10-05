from typing import Any

import pytest
from pydantic import ValidationError

from aiarb.config.charter import Charter
from aiarb.domain.scoring import SeverityThresholds

# The example charter from the README, with placeholder model IDs filled in.
README_CHARTER: dict[str, Any] = {
    "models": {
        "planner": "claude-opus-5-5",
        "critic": "claude-fable-5-1",
        "judge": "claude-opus-5-5",
        "verifier": "claude-haiku-4-5",
        "explainer": "claude-haiku-4-5",
    },
    "debate": {"max_rounds": 4},
    "scoring": {"provider": "jev", "confidence_threshold": 0.7},
    "priorities": {
        "data_integrity": 5,
        "availability_at_peak": 5,
        "delivery_time": 3,
        "running_cost": 2,
    },
    "constraints": ["no new managed services", "zero-downtime deploys only"],
    "budget": {"per_call": 10, "per_run": 60},
}


def test_an_empty_charter_is_valid_and_uses_defaults() -> None:
    charter = Charter.model_validate({})

    assert charter.models.planner != charter.models.critic
    assert charter.debate.max_rounds == 4
    assert charter.scoring.provider == "llm"
    assert charter.scoring.thresholds == SeverityThresholds()
    assert charter.priorities == {}
    assert charter.constraints == ()
    assert (charter.budget.per_call, charter.budget.per_run) == (10, 60)


def test_the_readme_example_is_valid() -> None:
    charter = Charter.model_validate(README_CHARTER)

    assert charter.models.critic == "claude-fable-5-1"
    assert charter.scoring.provider == "jev"
    assert charter.priorities["data_integrity"] == 5
    assert charter.constraints == ("no new managed services", "zero-downtime deploys only")


def test_severity_thresholds_become_the_domain_type() -> None:
    charter = Charter.model_validate(
        {"scoring": {"thresholds": {"moderate": 0.2, "major": 0.4, "blocker": 0.9}}}
    )

    assert charter.scoring.thresholds == SeverityThresholds(moderate=0.2, major=0.4, blocker=0.9)


def test_a_partial_section_keeps_the_other_defaults() -> None:
    charter = Charter.model_validate({"models": {"planner": "claude-fable-5-1"}})

    assert charter.models.planner == "claude-fable-5-1"
    assert charter.models.critic == Charter().models.critic


def test_the_charter_cannot_be_changed_after_loading() -> None:
    charter = Charter()

    with pytest.raises(ValidationError, match="frozen"):
        charter.debate.max_rounds = 9  # type: ignore[misc]


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (
            {"models": {"planner": "claude-opus-5-5", "critic": "claude-opus-5-5"}},
            "critic must be a different model from the planner",
        ),
        ({"models": {"judge": "  "}}, "at least 1 character"),
        ({"debate": {"max_rounds": 0}}, "greater than or equal to 1"),
        ({"scoring": {"provider": "gpt"}}, "'jev' or 'llm'"),
        ({"scoring": {"confidence_threshold": 1.5}}, "less than or equal to 1"),
        (
            {"scoring": {"thresholds": {"moderate": 0.6, "major": 0.5, "blocker": 0.8}}},
            "must rise strictly",
        ),
        ({"priorities": {"data_integrity": 0}}, "greater than or equal to 1"),
        ({"priorities": {"data_integrity": 6}}, "less than or equal to 5"),
        ({"priorities": {"Data Integrity": 3}}, "should match pattern"),
        ({"constraints": ["no new services", " "]}, "at least 1 character"),
        ({"budget": {"per_call": 0}}, "greater than 0"),
        ({"budget": {"per_call": 80, "per_run": 60}}, "cannot exceed the per-run cap"),
        ({"debate": {"max_round": 4}}, "Extra inputs are not permitted"),
        ({"budjet": {}}, "Extra inputs are not permitted"),
    ],
)
def test_invalid_charters_are_refused(data: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Charter.model_validate(data)
