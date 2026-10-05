"""The files in the target repo, as the collectors see them.

Under git, the listing is what git tracks plus untracked files it does not
ignore, so build output and dependencies stay out. Elsewhere, folders that are
usually generated are skipped. Reading never follows a link out of the repo.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from aiarb.scan.git import git

MAX_FILES = 100_000
MAX_READ_BYTES = 512_000

SKIPPED_FOLDERS = frozenset(
    {
        ".git",
        ".aiarb",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".next",
        ".nuxt",
        "dist",
        "build",
        "target",
        "vendor",
        "coverage",
    }
)
"""Skipped when walking a repo that is not under git."""


@dataclass(frozen=True)
class RepoFiles:
    root: Path
    paths: tuple[PurePosixPath, ...]
    """Every file, relative to the root, sorted."""
    truncated: bool = False
    """Whether the repo had more than `MAX_FILES` files and the rest were left out."""
    _names: frozenset[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_names", frozenset(str(path) for path in self.paths))

    @classmethod
    def scan(cls, root: Path) -> RepoFiles:
        root = root.resolve()
        listed = _git_files(root)
        names = listed if listed is not None else _walk(root)
        paths = sorted(
            PurePosixPath(name)
            for name in names
            if not set(PurePosixPath(name).parts) & {".git", ".aiarb"} and (root / name).is_file()
        )
        return cls(root, tuple(paths[:MAX_FILES]), truncated=len(paths) > MAX_FILES)

    def has(self, path: str) -> bool:
        return path in self._names

    def named(self, *names: str) -> list[PurePosixPath]:
        """Files with any of these names, in any folder."""
        return [path for path in self.paths if path.name in names]

    def matching(self, *patterns: str) -> list[PurePosixPath]:
        """Files matching any of these glob patterns, such as `*.tf` or `.github/*/*.yml`."""
        return [path for path in self.paths if any(path.match(p) for p in patterns)]

    def read(self, path: PurePosixPath | str) -> str | None:
        """A file's text, or None if it is too big, unreadable, or a link out of the repo."""
        full = (self.root / path).resolve()
        if not full.is_relative_to(self.root):
            return None
        try:
            if full.stat().st_size > MAX_READ_BYTES:
                return None
            return full.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None


def _git_files(root: Path) -> list[str] | None:
    output = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if output is None:
        return None
    return [name for name in output.split("\0") if name]


def _walk(root: Path) -> list[str]:
    found: list[str] = []
    for folder, subfolders, names in os.walk(root):
        subfolders[:] = sorted(name for name in subfolders if name not in SKIPPED_FOLDERS)
        relative = Path(folder).relative_to(root)
        found += [(relative / name).as_posix() for name in names]
        if len(found) > MAX_FILES:
            break
    return found
