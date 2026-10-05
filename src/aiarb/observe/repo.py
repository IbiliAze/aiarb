"""Checking that a run leaves the target repo as it found it.

aiarb writes only to `.aiarb/` in the target repo, and its agents can only read.
As a check on both, the runner notes every file before each invocation and
compares after it. Any file outside `.aiarb/` that was added, removed or changed
in between is reported: most likely the user edited it during the run, which
the review may not reflect.

The files compared are those the scan sees: under git, what git tracks and the
untracked files it does not ignore.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aiarb.scan.files import RepoFiles

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class RepoSnapshot:
    files: Mapping[str, tuple[int, int]]
    """Each file's size and modification time, in nanoseconds, by path."""

    @classmethod
    def take(cls, repo: Path) -> RepoSnapshot:
        files: dict[str, tuple[int, int]] = {}
        listed = RepoFiles.scan(repo)
        for path in listed.paths:
            try:
                stat = os.lstat(listed.root / path)
            except OSError:
                continue
            files[str(path)] = (stat.st_size, stat.st_mtime_ns)
        return cls(files)

    def changes(self, later: RepoSnapshot) -> list[str]:
        """The paths added, removed or changed since this snapshot, sorted."""
        paths = self.files.keys() | later.files.keys()
        return sorted(path for path in paths if self.files.get(path) != later.files.get(path))
