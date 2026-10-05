"""What every node can reach besides the state: the run's fixed setup.

The context is passed with each invocation and is never checkpointed, so the
ports can be real adapters or scripted fakes, and `resume` builds it afresh
from the charter and the environment.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from aiarb.config.charter import Charter
from aiarb.ports.code_agent import CodeAgent
from aiarb.ports.scorer import Scorer


@dataclass(frozen=True, kw_only=True)
class RunContext:
    charter: Charter
    repo: Path
    """The target repo, as an absolute path."""
    agent: CodeAgent
    scorer: Scorer

    def __post_init__(self) -> None:
        if not self.repo.is_dir():
            raise ValueError(f"the repo to review must be a directory: {self.repo}")
        object.__setattr__(self, "repo", self.repo.resolve())
