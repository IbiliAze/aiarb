"""Runs kept on disk, each with its checkpoints in a SQLite file.

    .aiarb/runs/2026-10-05-add-a-virtual-waiting-room/
        checkpoints.sqlite
        log.jsonl                                    (appended as the run goes)
        plan.md, ledger.md, decisions.md, run.json   (once the run finishes)

Each checkpoint is written as the step that made it finishes, so a run that is
killed carries on from its last finished step.
"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from aiarb.graph.state import checkpoint_serializer
from aiarb.ports.run_store import RUN_ID, RunNotFound, check_run_id

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

RUNS_PATH = Path(".aiarb/runs")
"""Where runs are kept, relative to the target repo."""

CHECKPOINTS = "checkpoints.sqlite"

_NAME_LENGTH = 40


class SqliteRunStore:
    def __init__(self, root: Path, *, today: Callable[[], date] = date.today) -> None:
        self.root = root
        self._today = today

    @classmethod
    def in_repo(cls, repo: Path) -> SqliteRunStore:
        return cls(repo / RUNS_PATH)

    def create(self, name: str | None) -> str:
        """A new run folder, named for the date and `name`: `2026-10-05-add-a-queue`.

        A second run with the same name on the same day gets `-2`, and so on.
        """
        base = f"{self._today().isoformat()}-{_slug(name)}"
        self.root.mkdir(parents=True, exist_ok=True)
        for number in range(1, 1000):
            run_id = base if number == 1 else f"{base}-{number}"
            try:
                (self.root / run_id).mkdir()
            except FileExistsError:
                continue
            return run_id
        raise RuntimeError(f"too many runs named {base} in {self.root}")

    def exists(self, run_id: str) -> bool:
        return self.folder(run_id).is_dir()

    def runs(self) -> list[str]:
        """Every run's ID, the run whose checkpoints changed last first."""
        if not self.root.is_dir():
            return []
        folders = [
            path for path in self.root.iterdir() if RUN_ID.match(path.name) and path.is_dir()
        ]
        return [path.name for path in sorted(folders, key=_last_active, reverse=True)]

    def folder(self, run_id: str) -> Path:
        return self.root / check_run_id(run_id)

    @asynccontextmanager
    async def checkpointer(self, run_id: str) -> AsyncIterator[AsyncSqliteSaver]:
        if not self.exists(run_id):
            raise RunNotFound(f"no run {run_id!r} in {self.root}")
        async with aiosqlite.connect(self.root / run_id / CHECKPOINTS) as connection:
            yield AsyncSqliteSaver(connection, serde=checkpoint_serializer())


def _last_active(folder: Path) -> tuple[float, str]:
    """When the run's checkpoints last changed, or its folder was made. The name
    breaks ties, so the order is stable."""
    checkpoints = folder / CHECKPOINTS
    path = checkpoints if checkpoints.exists() else folder
    return path.stat().st_mtime, folder.name


def _slug(name: str | None) -> str:
    """The first few words of `name`, as lower-case words joined by hyphens."""
    words = re.findall(r"[a-z0-9]+", (name or "").lower())
    slug = ""
    for word in words:
        longer = f"{slug}-{word}" if slug else word
        if len(longer) > _NAME_LENGTH:
            slug = slug or word[:_NAME_LENGTH]
            break
        slug = longer
    return slug or "run"
