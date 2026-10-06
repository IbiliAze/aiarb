# ADR-0007: Each run is a checkpointed LangGraph thread with its own folder

- **Status:** Accepted
- **Date:** 2026-10-06

## Context

A run can take an hour, pause for days waiting on the user, be killed by Ctrl-C
or a laptop lid, or fail on a rate limit. Its progress must survive all of
these, and the user must be able to find a run's files and carry it on later.

## Decision

Every run gets an ID made from the date and its feature, such as
`2026-10-05-add-a-virtual-waiting-room`. The ID is the LangGraph thread its
checkpoints belong to, and names its folder, `.aiarb/runs/<id>/`, which holds
`checkpoints.sqlite`, `log.jsonl` and, once it finishes, the output files.
Checkpoints are written synchronously after every step, so a kill loses only the
step under way.

The `RunContext` (charter, ports, redactor, meter) is not checkpointed: each
invocation builds it afresh, so a resumed run picks up a changed charter or
budget. Agent sessions are recorded in the state and resumed by ID.

## Consequences

- `aiarb resume` carries on any paused or stopped run from its last finished
  step, and with no ID takes the most recently active run.
- Runs are self-contained folders: easy to inspect, copy or delete, and kept out
  of git by `.aiarb/.gitignore`.
- The checkpointer restores only allow-listed types (`CHECKPOINT_TYPES`), so a
  new type in the state must be added there.
- State written by an older aiarb must still load: new state keys need defaults
  that old checkpoints can live without.
- The step limit (`recursion_limit`) is computed exactly from the round limit, as
  a backstop if routing ever fails to stop.

## Enforcement

`tests/graph/test_runner.py` kills runs mid-round and resumes them, pauses and
resumes across processes, and reloads from disk.
`tests/adapters/test_checkpointer.py` covers the store.
