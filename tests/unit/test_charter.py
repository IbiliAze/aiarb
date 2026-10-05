from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from aiarb.config.charter import CHARTER_PATH, Charter, CharterError, load_charter
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


def write_charter(repo: Path, content: str | bytes) -> Path:
    path = repo / CHARTER_PATH
    path.parent.mkdir(parents=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


class TestLoadCharter:
    def test_a_repo_without_a_charter_gets_the_defaults(self, tmp_path: Path) -> None:
        assert load_charter(tmp_path) == Charter()

    @pytest.mark.parametrize("content", ["", "\n", "# nothing configured yet\n"])
    def test_an_empty_charter_file_gets_the_defaults(self, tmp_path: Path, content: str) -> None:
        write_charter(tmp_path, content)

        assert load_charter(tmp_path) == Charter()

    def test_the_readme_example_loads_from_yaml(self, tmp_path: Path) -> None:
        write_charter(tmp_path, yaml.safe_dump(README_CHARTER))

        assert load_charter(tmp_path) == Charter.model_validate(README_CHARTER)

    def test_a_partial_charter_keeps_the_other_defaults(self, tmp_path: Path) -> None:
        write_charter(tmp_path, "debate:\n  max_rounds: 6\n")

        charter = load_charter(tmp_path)

        assert charter.debate.max_rounds == 6
        assert charter.models == Charter().models

    @pytest.mark.parametrize(
        ("content", "reason"),
        [
            ("debate: [max_rounds: 4", "is not valid YAML"),
            ("- models\n- debate\n", "must be a mapping of sections, not a list"),
            ("just some text", "must be a mapping of sections, not a str"),
            ("debate:\n  max_rounds: 0\n", "greater than or equal to 1"),
            ("budjet:\n  per_run: 60\n", "Extra inputs are not permitted"),
            (b"debate:\n  note: \xff\xfe\n", "is not UTF-8 text"),
        ],
    )
    def test_an_unusable_charter_names_the_file_and_the_reason(
        self, tmp_path: Path, content: str | bytes, reason: str
    ) -> None:
        path = write_charter(tmp_path, content)

        with pytest.raises(CharterError, match=reason) as caught:
            load_charter(tmp_path)

        assert caught.value.path == path
        assert str(caught.value).startswith(f"{path}: ")

    def test_an_unreadable_charter_is_an_error(self, tmp_path: Path) -> None:
        (tmp_path / CHARTER_PATH).mkdir(parents=True)

        with pytest.raises(CharterError, match="cannot be read"):
            load_charter(tmp_path)
