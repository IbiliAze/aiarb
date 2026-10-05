from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Section:
    """One part of the fact sheet: a title and a list of plain facts."""

    title: str
    facts: tuple[str, ...]
