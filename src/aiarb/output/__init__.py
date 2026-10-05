"""The files a finished run leaves in its folder:

    plan.md        the settled plan, with open items attached to the sections they affect
    ledger.md      every objection in plain English: problem, consequence, how it ended
    decisions.md   the trade-offs the user decided
    run.json       the whole run as data

Rendering is pure. Only the graph's final node writes, through `write`. Every
file is redacted as it is rendered: secrets an agent quoted from the repo are
taken out.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from aiarb.observe.redact import Redactor
from aiarb.output import decisions, ledger, plan, record
from aiarb.output.report import Report

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

__all__ = ["FILES", "Report", "render", "write"]

FILES = ("plan.md", "ledger.md", "decisions.md", "run.json")


def render(report: Report, *, redactor: Redactor | None = None) -> dict[str, str]:
    """Each file's name and text, redacted. By default, only secrets recognisable
    by their shape are taken out; a run's redactor also knows its environment's."""
    redactor = redactor or Redactor()
    return {
        "plan.md": redactor.text(plan.render(report)),
        "ledger.md": redactor.text(ledger.render(report)),
        "decisions.md": redactor.text(decisions.render(report)),
        "run.json": record.render(report, redactor),
    }


def write(folder: Path, files: Mapping[str, str]) -> None:
    """Write each file whole: a reader never sees one half-written, and a write
    that fails leaves the previous version in place."""
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        partial = folder / f".{name}.partial"
        partial.write_text(text, encoding="utf-8")
        os.replace(partial, folder / name)
