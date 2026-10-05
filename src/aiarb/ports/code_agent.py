"""The port for running one agent turn inside the target repo.

The graph depends only on this protocol. The Claude Code adapter implements it
for real runs, and a scripted fake implements it for tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Generic, Protocol, TypeVar

from pydantic import BaseModel

OutputT = TypeVar("OutputT", bound=BaseModel)


@dataclass(frozen=True)
class Cost:
    """What one or more agent calls spent."""

    usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0

    def __post_init__(self) -> None:
        if self.usd < 0 or self.input_tokens < 0 or self.output_tokens < 0:
            raise ValueError(f"cost cannot be negative: {self}")

    def __add__(self, other: Cost) -> Cost:
        return Cost(
            usd=self.usd + other.usd,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


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


class AgentError(Exception):
    """An agent call that did not produce a valid result."""

    def __init__(self, message: str, *, cost: Cost | None = None) -> None:
        super().__init__(message)
        self.cost = cost or Cost()
        """What the call spent before it failed, so budgets stay accurate."""


class RetryableAgentError(AgentError):
    """Worth trying again: a timeout, rate limit, overload or dropped connection."""


class FatalAgentError(AgentError):
    """Trying again will not help: bad credentials, a missing install, or a spend cap."""


class CodeAgent(Protocol):
    async def run(self, request: AgentRequest[OutputT]) -> AgentResult[OutputT]:
        """Run one turn and return its validated output.

        Raises RetryableAgentError or FatalAgentError when there is no valid result.
        """
        ...
