"""Read-only git commands against the target repo."""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

TIMEOUT_SECONDS = 30


def git(repo: Path, *args: str) -> str | None:
    """The output of a git command run in `repo`, or None if it failed.

    A repo that is not under git, a missing git install and a slow command all
    give None: the scan reports what it can without them.
    """
    if shutil.which("git") is None:
        return None
    try:
        done = subprocess.run(
            ["git", "-c", "core.quotepath=off", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SECONDS,
            check=False,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None
