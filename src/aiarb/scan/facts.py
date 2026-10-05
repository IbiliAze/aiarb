"""The repo fact sheet every agent is shown.

The collectors' sections, as Markdown. A sheet for a committed state of the repo
is cached under `.aiarb/cache/`, keyed on the commit, so later runs on the same
commit skip the scan. A repo with uncommitted changes is scanned afresh each
time, since the commit no longer describes it.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

from aiarb.scan.collectors import COLLECTORS
from aiarb.scan.files import RepoFiles
from aiarb.scan.git import git

CACHE_PATH = Path(".aiarb/cache/facts")
"""Where sheets are cached, relative to the target repo."""

SCAN_VERSION = 1
"""Part of the cache key. Raise it when a collector changes what it reports, so
sheets cached by an older aiarb are not reused."""


def fact_sheet(repo: Path, *, cache: Path | None = None) -> str:
    """The fact sheet for `repo`, from the cache when its commit was scanned before.

    `cache` defaults to `.aiarb/cache/facts` in the repo.
    """
    repo = repo.resolve()
    commit = _commit(repo)
    if commit is None or _has_changes(repo):
        return build(repo, commit=commit, changed=commit is not None)
    path = (cache or repo / CACHE_PATH) / f"{commit}-v{SCAN_VERSION}.md"
    with contextlib.suppress(OSError):
        return path.read_text(encoding="utf-8")
    sheet = build(repo, commit=commit)
    _write(path, sheet)
    return sheet


def build(repo: Path, *, commit: str | None = None, changed: bool = False) -> str:
    """Scan `repo` and write its sheet, without the cache."""
    files = RepoFiles.scan(repo)
    sections = [section for collect in COLLECTORS if (section := collect(files))]
    lines = ["# Repo facts", ""]
    if commit:
        lines += [
            f"Scanned at commit {commit[:12]}"
            + (", with uncommitted changes on top." if changed else "."),
            "",
        ]
    if not sections:
        lines += ["Nothing could be read from the repo.", ""]
    for section in sections:
        lines += [f"## {section.title}", "", *(f"- {fact}" for fact in section.facts), ""]
    return "\n".join(lines)


def _commit(repo: Path) -> str | None:
    head = git(repo, "rev-parse", "HEAD")
    return head.strip() if head else None


def _has_changes(repo: Path) -> bool:
    """Uncommitted changes in the part of the repo being scanned, untracked files included."""
    status = git(repo, "status", "--porcelain", "--untracked-files=all", "--", ".")
    return status is None or any(
        line and not _ours(line[3:].strip().strip('"')) for line in status.splitlines()
    )


def _ours(path: str) -> bool:
    """aiarb's own folder, which the scan skips and the cache lives in."""
    return path == ".aiarb" or path.startswith(".aiarb/") or "/.aiarb/" in f"/{path}"


def _write(path: Path, sheet: str) -> None:
    """Write the sheet whole or not at all. A repo that cannot be written to just
    goes without a cache."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    except OSError:
        return
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write(sheet)
        os.replace(temporary, path)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
