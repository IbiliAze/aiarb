"""Small repos built on the fly for the scan tests."""

import os
import subprocess
from pathlib import Path

GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def make(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, env=GIT_ENV, check=True, capture_output=True)


def commit(root: Path, files: dict[str, str], message: str = "Change") -> None:
    make(root, files)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)


def init(root: Path) -> Path:
    git(root, "init", "-q", "-b", "main")
    return root
