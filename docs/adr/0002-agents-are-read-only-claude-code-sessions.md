# ADR-0002: Agents are read-only Claude Code sessions confined to the target repo

- **Status:** Accepted
- **Date:** 2026-10-06

## Context

aiarb runs models with tool access inside someone else's repo, often unattended.
A review must never change the code under review, run its commands, or read
outside it. Claude Code by default can edit files, run shell commands, load MCP
servers, and pick up the repo's own `.claude/` settings and hooks, so the repo
under review could add tools of its own.

## Decision

Each session is locked down in `adapters/claude_code.py`, three ways at once:

- Only Read, Grep and Glob are loaded. Writing tools are also listed as
  disallowed, in case a Claude Code release loads them anyway.
- A PreToolUse hook refuses every other tool, bar the one that returns the
  structured answer, and any path outside the repo, symbolic links included.
- No MCP servers are loaded, and only the user's own settings are read, never
  the target repo's. Permission mode is `dontAsk`, so nothing waits for approval.

The system prompt tells the agent it is advising, not changing anything.

## Consequences

- Agents cannot run tests, builds or linters to check a claim. The verifier
  settles facts by reading; what reading cannot settle becomes a task for the
  user ([ADR-0006](0006-leftovers-are-handled-by-type.md)).
- Agents cannot ask the user questions mid-run (AskUserQuestion is not loaded).
  Assumptions are written into the plan instead, and trade-offs come to the user
  at the decision step.
- Agents can still read secrets in the repo, such as a `.env`, and quote them.
  Outputs are redacted ([ADR-0013](0013-redact-what-is-shown-not-what-is-checkpointed.md)).

## Enforcement

`tests/adapters/test_claude_code.py` checks the options and the hook's refusals,
including paths that escape through `..` and symbolic links. The runner checks
the repo is unchanged after every invocation ([ADR-0008](0008-aiarb-writes-only-inside-dot-aiarb.md)).
