"""The charter: how the board behaves in one repo, from `.aiarb/charter.yaml`.

Every field has a default, so an empty charter is a valid one. Unknown keys are
refused rather than ignored, so a typo fails loudly instead of silently falling
back to a default.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from aiarb.domain.scoring import SeverityThresholds

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
