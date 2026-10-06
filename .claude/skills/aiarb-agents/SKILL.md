---
name: aiarb-agents
description: Change what aiarb's agents are asked or how they answer — a role's brief, a turn's prompt, an answer schema, or how the Claude Code adapter runs a session. Use when an agent misunderstands its job, gives answers that fail validation, skips or invents objection ids, cites badly, when adding a field an agent must return, or when changing models, tools, spend caps or session handling.
---

# Changing what agents are asked

Every agent turn is one read-only Claude Code session
([ADR-0002](../../../docs/adr/0002-agents-are-read-only-claude-code-sessions.md)).
A turn is made of three things, each in its own place:

| What | Where |
| ---- | ----- |
| The role's standing brief: who it is, how it works, the rules of the debate | `src/aiarb/prompts/<role>.md` |
| This turn's task and inputs, as XML-tagged blocks | `src/aiarb/graph/prompts.py` |
| The shape of the answer, as structured output | `src/aiarb/schemas/outputs.py` |

The brief is sent on every turn, not only a session's first, because a session
Claude Code has lost restarts from the latest prompt alone.

## Changing a brief or a prompt

- Write plainly, for the model: what to do, what not to do, and why. Keep the
  briefs consistent with each other; the critic's and planner's describe the same
  debate from two sides.
- Objections are always named by id (`OBJ-3`) in prompts. Scripted fakes match
  on it, and so does `_calls.by_id`.
- Status meanings shown to agents are `_STATUS_MEANINGS` in `graph/prompts.py`;
  the labels people read are in `output/report.py`. Keep them saying the same
  thing.
- Prompt tests are in `tests/graph/test_prompts.py`.

## Changing an answer schema

Models in `schemas/outputs.py` are pydantic, `extra="forbid"`, with field and
class docstrings that become the schema's descriptions: **the docstrings are
what the agent reads about each field**, so write them for the agent.

- Put every rule JSON Schema can express into the schema (minimum lengths,
  patterns, unions for answers of different kinds). Claude Code checks the answer
  against it and the agent fixes it in the same session, which is cheap.
- Rules JSON Schema cannot express go in pydantic validators. Breaking one fails
  the whole call, which is retried, so keep them few.
- `answer_schema` in `adapters/claude_code.py` strips keywords Claude Code's
  strict schema check refuses, such as `discriminator`. A new pydantic feature
  that adds another one gives `is not a valid JSON Schema`, a fatal error: add it
  to `_ANNOTATIONS`.
- After a change, update every scenario that scripts that schema:
  `grep -rn "schema: <Name>" tests/fixtures/scenarios`. `ScriptedCodeAgent`
  validates scripted outputs against the real schema, so stale scenarios fail.
- `tests/unit/test_outputs.py` checks each schema is valid JSON Schema, that
  bad answers are refused by it, and that every field is described for the agent.

## Changing the adapter

`adapters/claude_code.py` runs each session: tools, the read-only hook, spend
caps, session resume, and turning every failure into `RetryableCallError` or
`FatalCallError`.

- Never widen the tool list or the hook without an ADR.
- Retryable means trying again may work: timeouts, overloads, rate limits, an
  answer in the wrong shape. Fatal means it will not: sign-in, billing, a missing
  install, a spend cap. Getting this wrong either wastes money on hopeless
  retries or stops runs that would have recovered.
- A failed call carries what it spent in `CallError.cost`; the budget counts it
  ([ADR-0012](../../../docs/adr/0012-the-budget-counts-every-call.md)).
- Tests in `tests/adapters/test_claude_code.py` drive the adapter with a fake
  `query` function; no real session is started.

## Checking with real models

Fakes prove the graph handles an answer, not that a model gives a good one.
After changing a brief, prompt or schema, run the live test for that role, but
only when the user has agreed to spend the money:

```bash
AIARB_LIVE_TESTS=1 uv run pytest tests/live/test_roles_live.py -k critic -s
```

`-s` prints each answer for reading. A full live run costs about a dollar.
