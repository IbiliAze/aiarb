# ADR-0006: Items left at the round limit are handled by type, never forced into agreement

- **Status:** Accepted
- **Date:** 2026-10-06

## Context

Some objections will not settle: the code cannot answer the question, the two
sides want different things, or they argue in circles. Forcing agreement would
hide exactly the items a builder most needs to know about, and letting the
debate run on costs money without converging.

## Decision

The debate stops at the round limit (`debate.max_rounds`), or earlier if no
objection has left the debate for two rounds (`STALL_ROUNDS`). Each item still
open is then handled by its type:

- **Factual**: deferred. It becomes a task under *Before you build* in `plan.md`,
  such as a load test to run first.
- **Trade-off**: waits for the user's decision. The run pauses and asks.
- **Design**: goes to the judge, a model that took no part in the debate. Its
  ruling is recorded as "ruled, not agreed".

## Consequences

- A run always ends, within a predictable number of rounds.
- Nothing is reported as agreed that was not. Deferred items count as open, and
  rulings are listed for the builder to apply, since the planner never revised
  the plan for them.
- Only the user decides trade-offs, which means a run can wait on a person.
  `--no-input` and CI stop with exit code 3 and the command to answer.
- The objection type the critic gives matters for how an item ends; the critic's
  brief defines the types carefully.

## Enforcement

`judge_needed`, `round_limit_reached` and `stalled` in `graph/routing.py`, and
the type rules in `TRANSITIONS`. Tested in `tests/graph/test_routing.py` and the
`round_limit`, `stalemate` and `judge_path` scenarios.
