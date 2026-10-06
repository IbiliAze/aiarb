# ADR-0012: The run's budget counts every call, from the run's log

- **Status:** Accepted
- **Date:** 2026-10-06

## Context

The charter's `budget.per_run` is meant as a hard ceiling. But the state only
records what finished steps spent: when a step fails, LangGraph discards its
writes, including the cost of calls it made. A step retried three times after
paying for each attempt would spend money the state never sees. Also, a single
Claude Code call can spend up to `budget.per_call`, so a run with little budget
left could overshoot by a whole call.

## Decision

The runner wraps the run's agent and scorers (`observe/calls.py`) so every
call, successful or failed, is written to the run's `log.jsonl` with its cost.
The log is the record of what a run has spent; a run's spend is the larger of
the log's total and the state's, for runs whose log is missing or older.

Before each call, the meter refuses it with `OverBudget` once the budget is
spent. Each Claude Code call is capped at what is left of the budget, through
the request's `spend_cap_usd`. `OverBudget` is not retried: the step fails, the
run stops with exit code 5 and keeps its progress, and `aiarb resume` carries on
from that step once `budget.per_run` is raised in the charter.

## Consequences

- The budget counts what the account is actually charged, retries included.
  `run.json` reports the extra as `failed_steps_cost`.
- A run overshoots by at most the calls already under way, plus whatever a
  Claude Code call spends between checking its cap, which is about one turn.
- Raising the budget means editing the charter. There is deliberately no
  `--budget` flag: an override not stored with the run would be lost on the next
  resume, which would then stop straight away.
- The log becomes load-bearing for accounting, so its call lines must keep
  their cost fields.

## Enforcement

`tests/observe/test_calls.py` covers refusing, capping and counting failed
calls. `tests/graph/test_watching.py` and `tests/cli/test_safety.py` stop runs at
the budget and carry them on after it is raised.
