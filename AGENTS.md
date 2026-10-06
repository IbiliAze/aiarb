# AGENTS.md

Core working notes for `aiarb`. If you are an agent, read this before writing
code. If you are a human, it is also the fastest way in.

## What this is

A command-line AI architecture review board. Given a feature and a target repo,
a **planner** drafts a plan, a **critic** raises objections that must cite the
code or the plan, and the two debate them in rounds. A **verifier** settles
disputed facts by reading the code, a **judge** rules on design deadlocks left at
the round limit, and trade-offs come to the **user** as decisions. A **scorer**
rates each objection's impact, and an **explainer** writes the result up in
plain English. The run ends with `plan.md`, `ledger.md`, `decisions.md` and
`run.json` in the target repo's `.aiarb/runs/<run-id>/`.

Python 3.11+ · LangGraph (orchestration, checkpoints in SQLite) · Claude Agent
SDK (every agent is a read-only Claude Code session) · pydantic · Typer + rich ·
uv. Optional scoring through Jev (TypeSafe). Published to the project's private
GitLab package registry, not to PyPI.

## Commands

```bash
make check                 # what CI runs: ruff check, ruff format --check, mypy, pytest
uv run pytest              # the suite: no API calls, about 15 seconds
uv run pytest tests/graph/test_runner.py -k reopen
AIARB_LIVE_TESTS=1 uv run pytest tests/live   # real Claude Code and Jev: costs about $1
uv run aiarb plan --repo ../some-repo -f "..."  # a real run against another repo
uv run langgraph dev       # the graph in LangGraph Studio
make publish               # release by hand; see the aiarb-release skill
```

- `mypy` is `strict` over `src` and `tests`. Ruff's `TC` rules move imports into
  `TYPE_CHECKING` blocks, except where they are needed at runtime (see below).
- Live tests are skipped unless `AIARB_LIVE_TESTS=1`. They use Haiku where they
  can, but they spend real money. Do not run them unasked.
- A real run writes only to `<repo>/.aiarb/`. Point it at a scratch copy of a
  repo, such as `tests/fixtures/repos/python-api`, when trying things out.

## Architecture in one screen

```
src/aiarb/
├── domain/        plain Python, no I/O: the objection ledger and its transition
│                  rules, severity scoring, how a ledger becomes a run status
├── config/        charter.yaml (per repo, committed) and Settings (env vars only)
├── ports/         Protocols the graph depends on: CodeAgent, Scorer, RunStore,
│                  plus Cost and the CallError hierarchy
├── adapters/      the real implementations: Claude Code, Jev, the language-model
│                  scorer, the SQLite run store
├── scan/          the repo fact sheet: git, file listing, collectors. No model
├── schemas/       pydantic models every agent answers in (structured output)
├── prompts/       each role's standing brief, as Markdown
├── graph/         the LangGraph: state, nodes/, routing, builder, runner
├── observe/       watching a run: the JSON log, the budget meter, redaction,
│                  the repo-unchanged check
├── output/        rendering plan.md, ledger.md, decisions.md, run.json. Pure
└── cli/           Typer commands, live progress, the guided session
```

Dependencies point inwards: `domain` imports nothing outside itself, `ports`
imports only `domain`, `graph` imports `ports` but never `adapters`, and only `cli` (and the
live tests) build real adapters. Tests run the whole graph on scripted fakes in
`tests/fakes.py`. See [ADR-0003](docs/adr/0003-ports-and-adapters-tested-on-fakes.md).

### The graph

```
scan → draft_plan → critique → triage ─┬─▶ respond → rule ─┬─▶ verify ─┐
                                       │      ▲            │           │
                                       │      └────────────┴───────────┤ another round
                                       │                               ▼
                                       └───────────────▶ judge → decide → rescore → explain → finalise
```

`routing.py` decides every branch. Reopening items restarts the run from triage
with a fresh set of rounds ([ADR-0010](docs/adr/0010-reopening-restarts-the-debate-from-triage.md)).

### Rules that are actually enforced

1. **Agents can only read.** Read, Grep and Glob inside the target repo, enforced
   by the tool list, a PreToolUse hook and no MCP or repo settings. Tests in
   `tests/adapters/test_claude_code.py`. [ADR-0002](docs/adr/0002-agents-are-read-only-claude-code-sessions.md).
2. **Every ledger move is checked.** `Objection.transition` refuses any move not
   in `domain/ledger.TRANSITIONS`, by the wrong actor, or closing an item without
   a plan change or cited evidence. [ADR-0004](docs/adr/0004-objections-close-only-on-a-plan-change-or-evidence.md).
3. **Router return types are the graph's edges.** LangGraph reads the `Literal`
   return annotations at runtime, so `routing.py` must **not** use
   `from __future__ import annotations`. `test_langgraph_reads_each_routers_edges_from_its_return_type`
   catches lost edges.
4. **Only `finalise` writes files, and only inside `.aiarb/`.** The runner
   compares the repo before and after each invocation, and
   `tests/cli/test_safety.py` checks a full run leaves every byte outside
   `.aiarb/` alone. [ADR-0008](docs/adr/0008-aiarb-writes-only-inside-dot-aiarb.md).
5. **Exit codes 0, 1 and 2 mean only how a finished run came out.** Usage errors
   are remapped from click's 2 to 4. [ADR-0011](docs/adr/0011-exit-codes-and-output-streams.md).
6. **Planner and critic are different models.** The charter refuses otherwise.
7. **The checkpoint serializer has an allow-list.** A new type stored in the
   state must be added to `CHECKPOINT_TYPES` in `graph/state.py`, or resuming
   a run fails to restore it.

### How the pieces you will actually touch work

- **State.** `graph/state.py`. Nodes return a `StateUpdate`, a partial dict. The
  ledger, costs and sessions have reducers; the ledger reducer refuses an update
  whose history does not extend what is there, which is what catches two nodes
  editing the same objection.
- **Nodes.** One file each in `graph/nodes/`. A node reads the state, makes its
  calls through `_calls.ask` (agents) or `runtime.context.scorer`, and returns
  objections moved with `Objection.transition`. Agent nodes retry on
  `RetryableCallError` (`builder.AGENT_RETRY`) and have time limits (`TIMEOUTS`).
- **Context.** `RunContext` carries the charter, repo, ports, run folder,
  redactor and meter. It is never checkpointed: `resume` builds it afresh.
- **Runner.** `graph/runner.py` starts, resumes and reopens runs, streams
  progress, and wraps the ports in `observe/calls.py` so every call is logged
  and counted against `budget.per_run` ([ADR-0012](docs/adr/0012-the-budget-counts-every-call.md)).
- **Pausing for the user.** `nodes/decide.py` calls `interrupt()`. The CLI asks
  at a terminal or prints the `aiarb resume --decide` command.
- **Prompts.** `graph/prompts.py` builds each turn from the role's brief in
  `prompts/<role>.md`, the task, and XML-tagged inputs. Scripted fakes match on
  objection ids in the prompt.
- **Outputs.** `output/` renders from a `Report`. Rendering is pure; `finalise`
  writes, redacted ([ADR-0013](docs/adr/0013-redact-what-is-shown-not-what-is-checkpointed.md)).
- **CLI.** Commands get their outside world from a `Setup` passed as Typer's
  `obj`, so tests swap in scripted ports, a fixed clock and a quiet console.

## Conventions

- **Write plainly.** Docstrings, comments, messages, prompts and docs are short
  sentences in British English, with no jargon a newcomer would have to look up.
  Say what something is for, not what the code already says.
- **Match the neighbours.** Comment density, naming and idioms follow the file
  you are in. Most modules start with a docstring that says what they own; keep
  it true.
- **Frozen data.** Domain types, ports' value types and the charter are frozen
  dataclasses or pydantic models. Objections change only by `transition`,
  `rescore` and friends, which return new objects.
- **Imports needed at runtime stay out of `TYPE_CHECKING`**: types in dataclass
  or pydantic fields, `routing.py`'s annotation types, and anything `isinstance`
  checks. Ruff's TC004 points these out.
- **No new state keys without a default** in `initial_state`, and remember old
  checkpoints: a run started by an earlier version must still resume.
- **Tests read as specifications.** Test names are sentences
  (`test_a_run_over_its_budget_stops_with_exit_5`), grouped in classes by
  behaviour. Use the scripted fakes; never mock LangGraph.
- **Commits.** Imperative subject of 72 characters or fewer, and a body only when
  it says why. `.githooks/prepare-commit-msg` drafts one if you enable it with
  `git config core.hooksPath .githooks`.

## Docs

- [docs/adr/](docs/adr/): why the design is the way it is. Read the relevant ADR
  before changing a decision it records, and write a new one when you make one.
- [.claude/skills/](.claude/skills/): how to do the recurring jobs here: change
  the graph, change what an agent is asked, write tests, change the CLI, release.
- [README.md](README.md): what users see. Keep it in step with behaviour.
- [TODO.md](TODO.md): the v1 build checklist, section by section.
