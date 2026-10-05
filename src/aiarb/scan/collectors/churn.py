"""Where the code has been changing lately: the parts most likely to change again."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from aiarb.scan.git import git
from aiarb.scan.section import Section, count

if TYPE_CHECKING:
    from aiarb.scan.files import RepoFiles

WINDOW_DAYS = 90
LISTED = 10


def collect(files: RepoFiles) -> Section | None:
    since = f"--since={WINDOW_DAYS}.days"
    total = git(files.root, "rev-list", "--count", "--no-merges", since, "HEAD", "--", ".")
    if total is None:
        return None
    latest = (git(files.root, "log", "-1", "--format=%cs", "--", ".") or "").strip()
    commits = int(total.strip() or 0)
    facts = [
        f"{count(commits, 'commit')} in the last {WINDOW_DAYS} days"
        + (f", the latest on {latest}" if latest else "")
    ]
    log = git(
        files.root, "log", "--no-merges", since, "--format=", "--name-only", "--relative", "--", "."
    )
    changed = Counter(line for line in (log or "").splitlines() if line and files.has(line))
    if changed:
        facts.append(
            "Most changed files: "
            + ", ".join(f"{path} ({times})" for path, times in changed.most_common(LISTED))
        )
    if (git(files.root, "rev-parse", "--is-shallow-repository") or "").strip() == "true":
        facts.append("The clone is shallow, so these counts may be low.")
    return Section("Recent changes", tuple(facts))
