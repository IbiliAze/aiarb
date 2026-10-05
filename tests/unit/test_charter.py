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

    def test_an_unreadable_charter_is_an_error(self, tmp_path: Path) -> None:
        (tmp_path / CHARTER_PATH).mkdir(parents=True)

        with pytest.raises(CharterError, match="cannot be read"):
            load_charter(tmp_path)

    def test_a_section_with_everything_commented_out_uses_its_defaults(
        self, tmp_path: Path
    ) -> None:
        write_charter(tmp_path, "debate:\n  # max_rounds: 6\nmodels:\n")

        assert load_charter(tmp_path) == Charter()


def charter_error(repo: Path, content: str | bytes) -> CharterError:
    write_charter(repo, content)
    with pytest.raises(CharterError) as caught:
        load_charter(repo)
    return caught.value


class TestClearErrors:
    """What the user sees when the charter is wrong: the file, the line, and what to fix."""

    @pytest.mark.parametrize(
        ("content", "problem"),
        [
            ("debate:\n  max_rounds: 0\n", "line 2, debate.max_rounds: must be at least 1 (got 0)"),
            (
                "debate:\n  max_rounds: four\n",
                "line 2, debate.max_rounds: must be a whole number (got 'four')",
            ),
            ("budjet:\n  per_run: 60\n", "line 1, budjet: unknown setting; did you mean 'budget'?"),
            (
                "debate:\n  rounds: 4\n",
                "line 2, debate.rounds: unknown setting; did you mean 'max_rounds'?",
            ),
            (
                "models:\n  writer: claude-opus-5-5\n",
                "line 2, models.writer: unknown setting; expected one of "
                "planner, critic, judge, verifier, explainer, scorer",
            ),
            (
                "scoring:\n  thresholds:\n    moderat: 0.3\n",
                "line 3, scoring.thresholds.moderat: unknown setting; did you mean 'moderate'?",
            ),
            (
                "scoring:\n  thresholds:\n    moderate: 0.6\n    major: 0.5\n",
                "line 2, scoring.thresholds: severity thresholds must rise strictly within (0, 1]; "
                "got moderate=0.6, major=0.5, blocker=0.8",
            ),
            (
                "scoring:\n  provider: gpt\n",
                "line 2, scoring.provider: must be 'jev' or 'llm' (got 'gpt')",
            ),
            (
                "scoring:\n  confidence_threshold: 1.5\n",
                "line 2, scoring.confidence_threshold: must be at most 1 (got 1.5)",
            ),
            (
                "models:\n  planner: claude-opus-5-5\n  critic: claude-opus-5-5\n",
                "line 1, models: the critic must be a different model from the planner (both are "
                "'claude-opus-5-5'): a model reviewing its own plan shares its blind spots",
            ),
            (
                "models:\n  judge: ''\n",
                "line 2, models.judge: must not be blank (got '')",
            ),
            (
                "models: claude-opus-5-5\n",
                "line 1, models: must be a section with settings indented below it, "
                "not the text 'claude-opus-5-5'",
            ),
            (
                "priorities:\n  Data Integrity: 3\n",
                "line 2, priorities: name 'Data Integrity' must be lowercase words joined by "
                "underscores, like data_integrity",
            ),
            (
                "priorities:\n  running_cost: 2.5\n",
                "line 2, priorities.running_cost: must be a whole number (got 2.5)",
            ),
            (
                "priorities:\n  running_cost: 6\n",
                "line 2, priorities.running_cost: must be at most 5 (got 6)",
            ),
            (
                "constraints:\n  - no new managed services\n  - ''\n",
                "line 3, constraints[1]: must not be blank (got '')",
            ),
            (
                "constraints: no new managed services\n",
                "line 1, constraints: must be a list (got 'no new managed services')",
            ),
            ("budget:\n  per_call: 0\n", "line 2, budget.per_call: must be more than 0 (got 0)"),
            (
                "budget:\n  per_call: 80\n  per_run: 60\n",
                "line 1, budget: the per-call cap (80.0) cannot exceed the per-run cap (60.0)",
            ),
        ],
    )
    def test_each_problem_names_the_line_the_setting_and_the_fix(
        self, tmp_path: Path, content: str, problem: str
    ) -> None:
        error = charter_error(tmp_path, content)

        assert error.summary == "has 1 problem:"
        assert error.problems == (problem,)

    def test_every_problem_is_reported_at_once_in_file_order(self, tmp_path: Path) -> None:
        content = "budjet:\n  per_run: 60\ndebate:\n  max_rounds: 0\nscoring:\n  provider: gpt\n"

        error = charter_error(tmp_path, content)

        assert str(error) == (
            f"{error.path} has 3 problems:\n"
            "  line 1, budjet: unknown setting; did you mean 'budget'?\n"
            "  line 4, debate.max_rounds: must be at least 1 (got 0)\n"
            "  line 6, scoring.provider: must be 'jev' or 'llm' (got 'gpt')"
        )

    @pytest.mark.parametrize(
        ("content", "problem"),
        [
            (
                "debate: [max_rounds: 4\n",
                "line 2, column 1: expected ',' or ']', but got '<stream end>'",
            ),
            (
                "debate:\n  max_rounds: 4\ndebate:\n  max_rounds: 2\n",
                "line 3, column 1: duplicate key 'debate', first defined on line 1",
            ),
            (
                "debate:\n  max_rounds: 4\n  max_rounds: 2\n",
                "line 3, column 3: duplicate key 'max_rounds', first defined on line 2",
            ),
        ],
    )
    def test_broken_yaml_names_the_line_and_column(
        self, tmp_path: Path, content: str, problem: str
    ) -> None:
        error = charter_error(tmp_path, content)

        assert str(error) == f"{error.path} is not valid YAML:\n  {problem}"

    @pytest.mark.parametrize(
        ("content", "summary"),
        [
            (
                "- models\n- debate\n",
                "must be sections such as `models:` and `debate:`, not a list",
            ),
            (
                "just some text\n",
                "must be sections such as `models:` and `debate:`, not the text 'just some text'",
            ),
            (b"debate:\n  note: \xff\xfe\n", "is not UTF-8 text"),
        ],
    )
    def test_a_file_that_is_not_a_charter_at_all(
        self, tmp_path: Path, content: str | bytes, summary: str
    ) -> None:
        error = charter_error(tmp_path, content)

        assert error.path == tmp_path / CHARTER_PATH
        assert str(error) == f"{error.path} {summary}"
