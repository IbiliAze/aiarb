# ADR-0009: Minor objections are noted rather than debated, and listed in the plan

- **Status:** Accepted
- **Date:** 2026-10-06

## Context

A critic raises everything it notices: an unexplained constant as readily as
data loss on deploy. Debating every item costs a planner and a critic turn per
round, and buries the serious ones.

The first version passed minor items to the planner as notes in the next round.
Real runs on three repos (TODO section 14) showed the flaw: when nothing else was
open, there was no next round, so the planner never saw the note and `plan.md`
did not mention it. One such note was a real problem: a planned test that could
not work.

## Decision

Triage scores every new objection, and those rated minor are moved to `noted`
without debate. The planner is shown them as notes in any later round, to apply
where worthwhile. Whether or not a round follows, `plan.md` lists every noted
item under *Minor notes*, and points to it from each plan section it cites,
saying the plan may not reflect it.

## Consequences

- The debate spends its rounds on items that matter, and a small feature with
  only minor objections settles in one pass.
- A minor note is never lost: the builder sees it in the plan.
- Whether an item is minor depends on the scorer ([ADR-0005](0005-a-scorer-measures-and-never-decides.md));
  thresholds are in the charter. A noted item can be reopened with new evidence.

## Enforcement

`tests/cli/test_real_runs.py` replays the three recorded real runs and checks
each note appears under *Minor notes*. `tests/output/test_plan.py` covers the
rendering.
