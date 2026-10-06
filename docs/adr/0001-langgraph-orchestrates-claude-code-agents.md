# ADR-0001: LangGraph orchestrates the debate, and every agent is a Claude Code session

- **Status:** Accepted
- **Date:** 2026-10-06

## Context

A review has to read the target repo in depth: the planner and critic need to
open files, search for callers and follow imports, sometimes for an hour on a
large codebase. Their later turns must remember what they read, or every round
pays for the exploration again.

Around the agents sits a process with real control flow: rounds, branches by
objection type, a pause of hours or days for the user's decisions, retries,
resuming after a crash, and reopening items later.

The options were a hand-written loop over the Messages API with our own file
tools; the Messages API inside LangGraph; or Claude Code through the Claude Agent
SDK inside LangGraph.

## Decision

LangGraph runs the process: one node per step, routers for every branch,
checkpoints after every step, `interrupt()` for the user's decisions and retry
policies for failed calls. Each agent turn is one Claude Code session through
the Claude Agent SDK, answering in a pydantic schema as structured output. A
role's later turns resume its earlier session, so it keeps what it read.

## Consequences

- Claude Code brings mature repo tools, context management and prompt caching
  that we do not maintain. Session resume makes later rounds short and cheap.
- Pausing, resuming and crash recovery come from LangGraph's checkpointer rather
  than our own persistence. The graph can be inspected in LangGraph Studio.
- Every agent needs Claude Code available: the SDK bundles it, or `claude` on the
  `PATH`. Models outside the Claude family need another `CodeAgent` adapter.
- We depend on two fast-moving libraries. Their behaviour, such as how streamed
  chunks and interrupts look, is pinned down by tests rather than assumed.
- A session Claude Code has cleaned up cannot be resumed; the adapter starts a
  fresh one, which rereads what it needs.

## Enforcement

The graph only knows the `CodeAgent` port ([ADR-0003](0003-ports-and-adapters-tested-on-fakes.md)).
`tests/graph/` pins LangGraph's behaviour: routing edges, streaming, interrupts,
reopening. The live tests in `tests/live/` run each role through real Claude
Code.
