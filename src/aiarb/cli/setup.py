"""What the commands reach outside themselves: the terminal, where runs are kept,
and the agent and scorers a run uses. Tests give their own."""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path

from rich.console import Console

from aiarb.adapters.checkpointer import SqliteRunStore
from aiarb.adapters.claude_code import ClaudeCodeAgent
from aiarb.adapters.scoring import scorers_for
from aiarb.config.charter import Charter
from aiarb.config.settings import Settings
from aiarb.ports.code_agent import CodeAgent
from aiarb.ports.run_store import RunStore
from aiarb.ports.scorer import Scorer


class ExitCode(IntEnum):
    OK = 0
    SETTLED = 0
    SETTLED_WITH_OPEN_ITEMS = 1
    NOT_READY = 2
    WAITING = 3
    """Paused for the user's decisions."""
    ERROR = 4
    """aiarb could not run as asked, or the run stopped part way. A stopped run
    can be resumed."""
    INTERRUPTED = 130
    """Stopped with Ctrl-C. The run can be resumed."""


@dataclass(frozen=True)
class Ports:
    agent: CodeAgent
    scorer: Scorer
    confirmer: Scorer


def real_ports(charter: Charter, repo: Path) -> Ports:
    """Claude Code for every role, and the scorers the charter asks for.

    Raises `MissingKeyError` when the charter asks for Jev without a key.
    """
    settings = Settings()
    agent = ClaudeCodeAgent.from_charter(charter)
    key = settings.typesafe_api_key
    scorers = scorers_for(
        charter,
        agent=agent,
        repo=repo,
        typesafe_api_key=key.get_secret_value() if key is not None else None,
    )
    return Ports(agent=agent, scorer=scorers.scorer, confirmer=scorers.confirmer)


def _at_a_terminal() -> bool:
    return sys.stdin.isatty() and sys.stderr.isatty()


@dataclass(frozen=True)
class Setup:
    ports: Callable[[Charter, Path], Ports] = real_ports
    stores: Callable[[Path], RunStore] = SqliteRunStore.in_repo
    """The run store for a repo."""
    can_ask: Callable[[], bool] = _at_a_terminal
    """Whether someone is there to answer questions."""
    console: Console = field(default_factory=lambda: Console(stderr=True, highlight=False))
    """Where people read: progress, summaries, questions. Standard output is
    kept for JSON."""
    clock: Callable[[], float] = time.monotonic
