"""What the app depends on, manifest by manifest, and whether installs are locked."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiarb.scan.manifests import LOCKFILES, manifests
from aiarb.scan.section import Section

if TYPE_CHECKING:
    from collections.abc import Sequence

    from aiarb.scan.files import RepoFiles

LISTED = 25
"""Names listed per manifest and kind before the rest are counted."""


def collect(files: RepoFiles) -> Section | None:
    facts = [
        f"{manifest.path} ({manifest.ecosystem}): "
        f"runtime: {_list(manifest.dependencies)}; dev: {_list(manifest.dev_dependencies)}"
        for manifest in manifests(files)
    ]
    locks = [f"{path} ({LOCKFILES[path.name]})" for path in files.named(*LOCKFILES)]
    if not facts and not locks:
        return None
    facts.append(f"Lockfiles: {', '.join(locks)}" if locks else "Lockfiles: none")
    return Section("Dependencies", tuple(facts))


def _list(names: Sequence[str]) -> str:
    if not names:
        return "none"
    shown = ", ".join(names[:LISTED])
    return f"{shown} (+{len(names) - LISTED} more)" if len(names) > LISTED else shown
