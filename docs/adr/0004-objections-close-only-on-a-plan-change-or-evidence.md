# ADR-0004: Objections close only on a plan change or cited evidence, and every move is checked

- **Status:** Accepted
- **Date:** 2026-10-06

## Context

Left to themselves, models settle arguments socially: the planner says "good
point", the critic says "fair enough", and the objection disappears without the
plan changing. A board that lets that happen produces a confident plan with the
original flaws in it. The debate also needs a record a person can audit: who
said what, when, and why each item ended as it did.

## Decision

The ledger in `domain/ledger.py` is the debate's single record. Every objection
must cite the code or the plan when raised. It moves through a fixed set of
statuses, and `Objection.transition` checks each move against `TRANSITIONS`:
which moves exist, which actor may make each one, and for which objection types.
An objection is resolved only with a `Resolution` that cites a plan change or
evidence. Agreement alone is not a resolution. Objections are immutable; each
move returns a new objection with one more event in its history.

## Consequences

- The ledger can be trusted: `ledger.md` and `run.json` show the whole debate,
  and nothing closes without a reason that can be checked.
- Agents' answers must fit the rules. A critic that accepts without saying what
  settled it gets a validation error and the call is retried.
- Adding a status or a move means changing `TRANSITIONS`, the prompts that
  explain statuses to the agents, the output labels, and the routing.
- The ledger reducer refuses an update that does not extend an objection's
  history, which catches two nodes changing the same item at once, at the cost
  of nodes having to work on the latest version.

## Enforcement

`TransitionError` on every illegal move; `tests/unit/test_ledger.py` covers the
table. The answer schemas in `schemas/outputs.py` require citations and
resolutions. `merge_ledger` in `graph/state.py` refuses stale updates.
