# ADR-0010: Reopening items restarts the debate from triage, with a full set of rounds

- **Status:** Accepted
- **Date:** 2026-10-06

## Context

New evidence often arrives after a run: load-test results, a decision from the
business, a correction to an assumption. The user should be able to bring it
back to the board without starting over, and without the debate counting the
rounds already spent against the new one.

## Decision

`runner.reopen` moves the named items back to `open` with the user's evidence in
their history, and writes that update to the checkpoint as if the triage node
had just run. LangGraph then routes onward from triage as usual, so the planner
answers the reopened items next. `debate_start` records the round the new debate
began at; the round limit and the stall rule both count from it. A paused run
can be reopened too, deciding the trade-offs it does not reopen. A per-run round
limit (`--max-rounds`) is stored in the state, so it survives resumes.

## Consequences

- Only the affected items are debated again; settled ones stay settled.
- The reopened debate gets the same number of rounds as a new one.
- Trade-offs still waiting are asked once the new debate ends, which is why the
  end-of-round router can route to `decide` without passing through the judge.
- Writing to a checkpoint "as" a node relies on LangGraph's `update_state`
  semantics; tests pin them down.

## Enforcement

`TestReopen` in `tests/graph/test_runner.py` and the reopen cases in
`tests/cli/test_resume.py`.
