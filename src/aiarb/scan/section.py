from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Section:
    """One part of the fact sheet: a title and a list of plain facts."""

    title: str
    facts: tuple[str, ...]


def count(number: int, noun: str) -> str:
    """`1 file`, `2 files`."""
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"
