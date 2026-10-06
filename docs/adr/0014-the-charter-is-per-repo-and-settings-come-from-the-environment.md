# ADR-0014: The charter is per repo and strict; personal settings come only from the environment

- **Status:** Accepted
- **Date:** 2026-10-06

## Context

How a board should behave depends on the codebase: what matters there, what
rules every plan must keep, which models and how much money. That belongs with
the repo, reviewed like code. API keys belong to whoever runs aiarb, and the
target repo's own `.env` holds that app's keys, not ours.

## Decision

`.aiarb/charter.yaml` is committed to the target repo. Every setting has a
default, so an empty charter is valid, and unknown keys are refused, with a
suggestion for a likely typo, so a mistake fails loudly instead of quietly
falling back. `aiarb init` writes one with every default spelled out.

Keys (`ANTHROPIC_API_KEY`, `TYPESAFE_API_KEY`) come from the environment only,
through `config/settings.py`. No `.env` file is read, the target repo's least of
all.

## Consequences

- A team shares one board configuration through its repo, and changes to it go
  through review.
- Typos in the charter are caught at the start of a run, before any money is
  spent.
- Adding a setting means adding it to the charter model, `aiarb init`'s template,
  and the README's charter reference.
- Users must export their keys, or use a signed-in Claude Code.

## Enforcement

`extra="forbid"` on every charter section; `tests/unit/test_charter.py` and
`tests/cli/test_init.py`, which checks the starter charter loads back equal to
the defaults. `tests/unit/test_settings.py` checks no `.env` is read.
