"""What every node can reach besides the state: the run's fixed setup.

The context is passed with each invocation and is never checkpointed, so the
ports can be real adapters or scripted fakes, and `resume` builds it afresh
from the charter and the environment.

The ports are left out of the context's JSON schema, which LangGraph Studio
reads to offer a context form: they are objects, not settings.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, ClassVar

from pydantic import ConfigDict
from pydantic.json_schema import SkipJsonSchema

from aiarb.config.charter import Charter
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

    def __post_init__(self) -> None:
        if not self.repo.is_dir():
            raise ValueError(f"the repo to review must be a directory: {self.repo}")
        object.__setattr__(self, "repo", self.repo.resolve())
