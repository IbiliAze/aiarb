from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiarb.graph.state import RunState, StateUpdate


def decide(state: RunState) -> StateUpdate:
    """Ask the user to decide each trade-off left after the debate.

    A placeholder until section 6 adds `interrupt()` and the checkpointer: for
    now the trade-offs stay awaiting a decision, and the run reports them as open.
    """
    return {}
