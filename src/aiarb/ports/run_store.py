"""The port for keeping runs: where each one lives and how its checkpoints are saved.

A run's ID names it everywhere: its folder, and the LangGraph thread its
checkpoints belong to. The SQLite adapter keeps runs on disk; tests can point it
at a temporary folder.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager

    from langgraph.checkpoint.base import BaseCheckpointSaver

RUN_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
"""Lower-case letters, digits and hyphens: safe as a folder name and a thread ID."""


class RunNotFound(LookupError):
    """No run has this ID."""


def check_run_id(run_id: str) -> str:
    if not RUN_ID.match(run_id):
        raise ValueError(f"not a run ID: {run_id!r} (use lower-case letters, digits and hyphens)")
    return run_id


class RunStore(Protocol):
    def create(self, name: str | None) -> str:
        """Make a new run named after `name`, and return its ID."""
        ...

    def exists(self, run_id: str) -> bool: ...

    def checkpointer(self, run_id: str) -> AbstractAsyncContextManager[BaseCheckpointSaver[Any]]:
        """The run's checkpoint saver, open for as long as the context lasts.

        Raises `RunNotFound` for a run that was never created.
        """
        ...
