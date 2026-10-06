# ADR-0011: Exit codes say how a run came out; JSON goes to stdout and everything else to stderr

- **Status:** Accepted
- **Date:** 2026-10-06

## Context

aiarb is meant to run in scripts and CI as well as at a terminal. A script needs
to know whether a plan is settled, usable with known risks, or not ready, and to
read the details without scraping human text. Typer, through click, exits with 2
on bad usage, which would read as "not ready".

## Decision

| Code | Meaning |
| ---- | ------- |
| 0 | Settled |
| 1 | Settled with open items |
| 2 | Not ready: an open blocker |
| 3 | Waiting for the user's decisions |
| 4 | aiarb could not run as asked, or the run stopped part way |
| 5 | Stopped at the budget |
| 130 | Interrupted with Ctrl-C |

Codes 0, 1 and 2 mean only how a finished run came out. A custom Typer group
remaps click's usage errors, and unexpected exceptions, to 4.

`plan` and `resume` print one JSON object describing the run to standard output:
its ID, status, exit code, folder, files, open items or questions, rounds and
cost. Everything meant for people (progress, summaries, questions) goes to
standard error.

## Consequences

- `aiarb plan ... > result.json` works at a terminal: the user still sees
  progress and is still asked for decisions.
- The JSON shape is an interface: fields can be added, not renamed or removed.
- Every new way for a run to end needs a code and a JSON status, chosen with
  care, since scripts will branch on them.

## Enforcement

`ExitCode` in `cli/setup.py` and `_Group` in `cli/app.py`. `tests/cli/` checks
codes and JSON for each outcome, including that bad usage exits 4, not 2.
