# ADR-0003: The graph depends on ports, and is built and tested on scripted fakes

- **Status:** Accepted
- **Date:** 2026-10-06

## Context

A real run costs money and minutes, and model answers vary from run to run. The
debate's logic (statuses, rounds, routing, pausing, resuming, reopening) has
many paths, and debugging them against real models would be slow, expensive and
not repeatable.

## Decision

The graph talks to the outside world only through ports in `ports/`:
`CodeAgent`, `Scorer` and `RunStore`. Adapters in `adapters/` implement them
for real; scripted fakes in `tests/fakes.py` implement them for tests. A fake
follows a script of steps, matched by output schema and by text the prompt must
contain, such as an objection id, so concurrent calls need not arrive in order.
Scripts are YAML scenarios in `tests/fixtures/scenarios/`. The whole graph was
built and debugged on fakes before any real model was called.

## Consequences

- The full suite runs in seconds with no API calls, and every path of the
  debate is covered deterministically.
- Recorded real runs become regression tests: `tests/fixtures/scenarios/real/`
  holds what real agents answered, replayed through the CLI.
- Fakes only prove the graph handles the answers it is given. Whether real
  models give good answers is checked by the live tests and real runs, which
  must be run by hand.
- A fake left with unused steps, or asked something its script does not cover,
  fails the test, so scripts must be kept exact.

## Enforcement

`graph/` never imports `adapters/`; only `cli/setup.py` builds real adapters.
Nothing checks this mechanically: it is a convention, kept by review. Fakes
raise `ScriptError` on unexpected calls, and tests call `assert_finished()`.
