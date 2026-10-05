"""Collectors: each reads one aspect of the repo into a section of the fact sheet.

They are fast and need no model, so a run starts from facts every agent shares
rather than each one rediscovering the basics. A collector returns None when
its aspect does not apply, such as a repo with no CI.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiarb.scan.collectors import churn, ci, dependencies, infrastructure, stack, tests

if TYPE_CHECKING:
    from collections.abc import Callable

    from aiarb.scan.files import RepoFiles
    from aiarb.scan.section import Section

COLLECTORS: tuple[Callable[[RepoFiles], Section | None], ...] = (
    stack.collect,
    dependencies.collect,
    tests.collect,
    infrastructure.collect,
    ci.collect,
    churn.collect,
)
"""Every collector, in the order their sections appear."""
