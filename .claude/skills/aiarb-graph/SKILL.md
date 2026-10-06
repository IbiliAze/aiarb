---
name: aiarb-graph
description: Change aiarb's LangGraph debate — add or change a node, a route, a state key or an objection status. Use when adding a step to the run, changing where the graph goes after a step, storing something new in the run's state, changing how rounds, the round limit, stalls, pausing or reopening work, or when a run takes the wrong path, loses an edge, or fails to resume.
---

# Changing the graph

The graph is `src/aiarb/graph/`: `state.py` (what a run carries), `nodes/`
(one step per file), `routing.py` (every branch), `builder.py` (wiring, retries,
time limits, step limit) and `runner.py` (start, resume, reopen, progress).
Read [ADR-0001](../../../docs/adr/0001-langgraph-orchestrates-claude-code-agents.md)
and [ADR-0006](../../../docs/adr/0006-leftovers-are-handled-by-type.md) first.

## Adding a node

1. **Write it** in `graph/nodes/<name>.py`: a function, `async` if it makes calls, taking
   `(state: RunState, runtime: Runtime[RunContext])` and returning a
   `StateUpdate`. Return `{}` when there is nothing to do, so it costs nothing.
   Export it from `graph/nodes/__init__.py`.
2. **Make calls** through `_calls.ask(runtime, role, prompt, Schema, session_id=...)`
   for agents, or `runtime.context.scorer` / `.confirmer`. Record what they spent
   under the role in `"costs"`, and new sessions under `"sessions"`.
3. **Move objections** only with `Objection.transition(...)`, `rescore(...)` and
   friends. If the move is new, add it to `TRANSITIONS` in `domain/ledger.py`
   ([ADR-0004](../../../docs/adr/0004-objections-close-only-on-a-plan-change-or-evidence.md)).
   Match replies to objections with `_calls.by_id`, which refuses a reply set that
   skips or invents an id.
4. **Wire it** in `builder.build_graph`. A node that makes calls gets
   `retry_policy=retry_policy` and an entry in `TIMEOUTS`.
5. **Step limit.** If it runs once per invocation, raise `_FIXED_STEPS`; once per
   round, `_STEPS_PER_ROUND`. The limit is exact on purpose: it is the backstop if
   routing fails to stop.
6. **Progress labels.** Add it to `DOING` and `DONE` in `cli/progress.py`, and to
   `_IN_ROUND` if it belongs to a round.
7. **Describe it** in `builder.py`'s module docstring and the diagram in
   AGENTS.md.

## Adding a route

Routers live in `routing.py` and return a `Literal` of node names.

- **Do not add `from __future__ import annotations` to `routing.py`.** LangGraph
  reads each router's return annotation at runtime to learn its edges; with
  postponed annotations it silently loses them.
- Add the new target to the router's `Literal` alias (`AfterRule`, `AfterRound`
  and so on). A target missing from the alias is an edge LangGraph does not know.
- `test_langgraph_reads_each_routers_edges_from_its_return_type` in
  `tests/graph/test_routing.py` fails if edges go missing. Add routing cases next
  to the existing ones.

## Adding a state key

1. Add it to `RunState` with a docstring, and to `StateUpdate` if nodes write it.
2. Give it a value in `initial_state`.
3. **Old checkpoints must still load.** A run paused by the previous version has
   no value for the new key. Read it with a fallback, or make sure nothing reads
   it before something writes it.
4. A new class stored in the state must be added to `CHECKPOINT_TYPES`, or the
   checkpointer will not restore it on resume.
5. If two nodes may write it in the same step, give it a reducer, as `ledger`,
   `costs` and `sessions` have.

## Rounds, limits and reopening

- `round` is bumped by `respond`: the planner's answer starts each round.
- `round_limit(state, charter)` is the run's limit: `max_rounds` from the state
  if set, else the charter's. Both the limit and the stall rule count from
  `debate_start`, which `runner.reopen` sets
  ([ADR-0010](../../../docs/adr/0010-reopening-restarts-the-debate-from-triage.md)).
- Pausing is `interrupt()` in `nodes/decide.py` (trade-offs) and
  `nodes/clarify.py` (the planner's questions), told apart by the request's
  `kind`; resuming sends `Command(resume=answers)`. A run that has asked its questions again can show
  no `next` step while paused, which is why `RunResult.finished` also checks it
  is not paused.

## Checks

```bash
uv run pytest tests/graph tests/cli
make check
```

Cover the new path with a scenario (see the `aiarb-testing` skill), and check a
run can be stopped before and resumed after the new step
(`test_a_run_killed_mid_round_resumes_from_its_last_checkpoint` is the model).
