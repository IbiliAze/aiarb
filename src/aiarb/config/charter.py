"""The charter: how the board behaves in one repo, from `.aiarb/charter.yaml`.

Every field has a default, so an empty charter is a valid one. Unknown keys are
refused rather than ignored, so a typo fails loudly instead of silently falling
back to a default.
"""

from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from aiarb.domain.scoring import SeverityThresholds

CHARTER_PATH = Path(".aiarb/charter.yaml")
"""Where the charter lives, relative to the root of the target repo."""

ModelId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
PriorityName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]
Constraint = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Models(_Section):
    """The model each role runs on. Any model ID Claude Code accepts."""

    planner: ModelId = "claude-opus-5-5"
    critic: ModelId = "claude-sonnet-5-5"
    judge: ModelId = "claude-opus-5-5"
    verifier: ModelId = "claude-sonnet-5-5"
    explainer: ModelId = "claude-sonnet-5-5"
    scorer: ModelId = "claude-sonnet-5-5"
    """Used by the language-model scorer, whether chosen or as Jev's fallback."""

    @model_validator(mode="after")
    def _critic_differs_from_planner(self) -> "Models":
        if self.critic == self.planner:
            raise ValueError(
                f"the critic must be a different model from the planner (both are "
                f"{self.planner!r}): a model reviewing its own plan shares its blind spots"
            )
        return self


class Debate(_Section):
    max_rounds: int = Field(default=4, ge=1)


class Scoring(_Section):
    provider: Literal["jev", "llm"] = "llm"
    confidence_threshold: float = Field(default=0.7, ge=0, le=1)
    """Below this confidence, a language model confirms the score."""
    thresholds: SeverityThresholds = SeverityThresholds()


class Budget(_Section):
    """Spend caps in US dollars."""

    per_call: float = Field(default=10, gt=0)
    per_run: float = Field(default=60, gt=0)

    @model_validator(mode="after")
    def _call_fits_in_run(self) -> "Budget":
        if self.per_call > self.per_run:
            raise ValueError(
                f"the per-call cap ({self.per_call}) cannot exceed the per-run cap ({self.per_run})"
            )
        return self


class Charter(_Section):
    models: Models = Models()
    debate: Debate = Debate()
    scoring: Scoring = Scoring()
    priorities: dict[PriorityName, Annotated[int, Field(ge=1, le=5)]] = Field(default_factory=dict)
    """What matters in this codebase, weighted from 1 (a little) to 5 (most)."""
    constraints: tuple[Constraint, ...] = ()
    """Rules every plan must respect, such as "no new managed services"."""
    budget: Budget = Budget()


class CharterError(Exception):
    """The charter file exists but cannot be used."""

    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(f"{path}: {reason}")
        self.path = path
        self.reason = reason


def load_charter(repo: Path) -> Charter:
    """Load the repo's charter, or the defaults if it has none."""
    path = repo / CHARTER_PATH
    if not path.exists():
        return Charter()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise CharterError(path, f"cannot be read: {error.strerror or error}") from error
    except UnicodeDecodeError as error:
        raise CharterError(path, "is not UTF-8 text") from error
    except yaml.YAMLError as error:
        raise CharterError(path, f"is not valid YAML: {error}") from error
    if data is None:
        return Charter()
    if not isinstance(data, dict):
        raise CharterError(path, f"must be a mapping of sections, not a {type(data).__name__}")
    try:
        return Charter.model_validate(data)
    except ValidationError as error:
        raise CharterError(path, str(error)) from error
