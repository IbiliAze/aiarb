from __future__ import annotations

from typing import TYPE_CHECKING

from aiarb.domain.outcome import outcome

if TYPE_CHECKING:
    from aiarb.graph.state import RunState, StateUpdate


def finalise(state: RunState) -> StateUpdate:
    """Decide how the run ended. Writing the output files is added in section 11."""
    return {"status": outcome(state["ledger"]).status}
