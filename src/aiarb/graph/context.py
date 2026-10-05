"""What every node can reach besides the state: the run's fixed setup.

The context is passed with each invocation and is never checkpointed, so the
ports can be real adapters or scripted fakes, and `resume` builds it afresh
from the charter and the environment.

The ports, the run folder, the redactor and the meter are left out of the
context's JSON schema, which LangGraph Studio reads to offer a context form:
they are not settings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, ClassVar

from pydantic import ConfigDict
from pydantic.json_schema import SkipJsonSchema

from aiarb.config.charter import Charter
from aiarb.observe.calls import Meter
from aiarb.observe.redact import Redactor
from aiarb.ports.code_agent import CodeAgent
from aiarb.ports.scorer import Scorer


@dataclass(frozen=True, kw_only=True)
class RunContext:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(arbitrary_types_allowed=True)

    charter: Charter
    repo: Path
    """The target repo, as an absolute path."""
    agent: Annotated[CodeAgent, SkipJsonSchema()]
    scorer: Annotated[Scorer, SkipJsonSchema()]
    """Rates each objection's impact, before and after the debate."""
    confirmer: Annotated[Scorer, SkipJsonSchema()]
    """A language model that confirms blocker calls, which decide that a plan is
    not ready. It may be the scorer itself."""
    run_dir: Annotated[Path | None, SkipJsonSchema()] = None
    """The run's folder, named by its ID, where the final step writes the run's
    files. None writes no files, as in Studio. The runner sets it."""
    redactor: Annotated[Redactor, SkipJsonSchema()] = field(
        default_factory=Redactor.from_environment
    )
    """Takes secrets out of the run's files and log."""
    meter: Annotated[Meter | None, SkipJsonSchema()] = None
    """What the run has spent against its budget, counting every call. The runner
    sets it."""

    def __post_init__(self) -> None:
        if not self.repo.is_dir():
            raise ValueError(f"the repo to review must be a directory: {self.repo}")
        object.__setattr__(self, "repo", self.repo.resolve())
        if self.run_dir is not None:
            object.__setattr__(self, "run_dir", self.run_dir.resolve())
