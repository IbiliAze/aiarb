"""The port for running one agent turn inside the target repo.

The graph depends only on this protocol. The Claude Code adapter implements it
for real runs, and a scripted fake implements it for tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Generic, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

from aiarb.ports.calls import Cost

OutputT = TypeVar("OutputT", bound=BaseModel)


@dataclass(frozen=True, kw_only=True)
class AgentRequest(Generic[OutputT]):
    prompt: str
    repo: Path
    """The target repo. The agent may read and search it, never change it."""
    model: str
    schema: type[OutputT]
    """The shape the agent's final answer must take."""
    session_id: str | None = None
    """An earlier session to continue, so the agent keeps what it already read."""

    def __post_init__(self) -> None:
        if not self.prompt.strip():
            raise ValueError("an agent request needs a prompt")
        if not self.model.strip():
            raise ValueError("an agent request needs a model")


@dataclass(frozen=True, kw_only=True)
class AgentResult(Generic[OutputT]):
    output: OutputT
    session_id: str
    """Pass back as `AgentRequest.session_id` to continue this session."""
    cost: Cost


@runtime_checkable
class CodeAgent(Protocol):
    async def run(self, request: AgentRequest[OutputT]) -> AgentResult[OutputT]:
        """Run one turn and return its validated output.

        Raises RetryableCallError or FatalCallError when there is no valid result.
        """
        ...
