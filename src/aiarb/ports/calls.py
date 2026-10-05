"""What every outside call shares, whichever port makes it: what it spent, and
how it failed.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Cost:
    """What one or more calls spent."""

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


class CallError(Exception):
    """A call that did not produce a valid result."""

    def __init__(self, message: str, *, cost: Cost | None = None) -> None:
        super().__init__(message)
        self.cost = cost or Cost()
        """What the call spent before it failed, so budgets stay accurate."""


class RetryableCallError(CallError):
    """Worth trying again: a timeout, rate limit, overload or dropped connection."""


class FatalCallError(CallError):
    """Trying again will not help: bad credentials, a missing install, or a spend cap."""
