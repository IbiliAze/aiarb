"""The repo fact sheet the agents are shown.

A placeholder until the collectors in section 7: for now it lists the repo's
top-level entries.
"""

from pathlib import Path


def fact_sheet(repo: Path) -> str:
    entries = sorted(
        f"{entry.name}/" if entry.is_dir() else entry.name
        for entry in repo.iterdir()
        if entry.name != ".git"
    )
    return "Top-level entries:\n" + "\n".join(f"- {entry}" for entry in entries)
