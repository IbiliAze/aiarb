from __future__ import annotations

from typing import TYPE_CHECKING

from aiarb import output
from aiarb.domain.outcome import outcome

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

    from aiarb.graph.context import RunContext
    from aiarb.graph.state import RunState, StateUpdate


def finalise(state: RunState, runtime: Runtime[RunContext]) -> StateUpdate:
    """Decide how the run ended, and write its files to the run's folder.

    This is the only node that writes files. Without a run folder, as in Studio,
    it writes none. The folder is named by the run's ID, which `run.json` records.
    """
    run_dir = runtime.context.run_dir
    if run_dir is not None:
        report = output.Report.of(state, runtime.context.charter, run_id=run_dir.name)
        output.write(run_dir, output.render(report))
    return {"status": outcome(state["ledger"]).status}
