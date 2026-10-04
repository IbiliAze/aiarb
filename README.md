# aiarb

**An AI architecture review board for your codebase.** Give it a feature you want to add to an existing app. One model plans it, another attacks the plan, and they argue it out against your actual code until the plan is settled. You get one polished plan, a plain-English record of every objection, and a short list of the decisions only you can make.

> **Status: pre-release.** aiarb is under active development and not yet published. See [`aiarb-v1-todo.md`](./aiarb-v1-todo.md) for progress.

## Why

Ask one model to plan a feature and it will give you a confident plan. Show that plan to a second model and it will find twenty things wrong with it. Feed those back to the first and it will defend itself. You end up relaying messages between models and refereeing an argument that may go beyond what you can judge.

aiarb runs that argument for you, under rules:

- Every objection must cite the code or the plan line it concerns.
- An objection closes only when the plan changes or evidence settles it. "You're right" is not a resolution.
- Factual disputes are checked against the repo, not argued.
- Real trade-offs are brought to you as plain-English questions, not settled silently.

## How it works

```
aiarb plan
   │
   ▼
Scan the repo ──────────── builds a fact sheet, no LLM
   │
   ▼
Planner drafts the plan ── model A explores the repo
   │
   ▼
Critic raises objections ─ model B, each one cites code
   │
   ▼
Triage ─────────────────── objections scored for impact; minor ones skip debate
   │
   ▼
┌─▶ Planner responds ───── fixes or rebuts each open item
│     │
│     ▼
│  Verifier and critic rule ─ check facts, close items
│     │
└─────┘  repeat while items stay open (default: up to 4 rounds)
   │
   ▼
You decide the trade-offs ─ only if any remain
   │
   ▼
plan.md, ledger.md, decisions.md, run.json
```

The planner and critic each run inside [Claude Code](https://docs.claude.com/en/docs/claude-code/overview) with read-only access to your repo. [LangGraph](https://github.com/langchain-ai/langgraph) orchestrates the debate and checkpoints every step, so a run can pause for your decisions or recover from a crash and carry on.

## Requirements

- Python 3.11 or later
- Claude Code installed and on your `PATH`
- An Anthropic API key
- Optional: a TypeSafe API key, to use Jev for objection scoring

## Install

```bash
pipx install aiarb
```

Then set your keys:

```bash
export ANTHROPIC_API_KEY=...
export TYPESAFE_API_KEY=...   # optional
```

## Quick start

From the root of the repo you want to change:

```bash
aiarb init                                    # writes .aiarb/charter.yaml
aiarb plan --feature "add a virtual waiting room for ticket on-sales"
```

Already have a plan from an earlier session? Pass it in and the run starts at the critique:

```bash
aiarb plan --plan ./waiting-room-plan.md
```

Run `aiarb` with no arguments for a guided, interactive session. Every option can be left empty; aiarb infers what it can from the repo and states its assumptions.

## Commands

| Command        | What it does                                                              |
| -------------- | ------------------------------------------------------------------------- |
| `aiarb`        | Interactive mode                                                          |
| `aiarb init`   | Write a starter charter to `.aiarb/charter.yaml`                          |
| `aiarb plan`   | Start a run. Options: `--feature`, `--plan` (repeatable), `--max-rounds`  |
| `aiarb resume` | Continue a paused or interrupted run, with your decisions or new evidence |

## The charter

`.aiarb/charter.yaml` is committed to your repo and defines how the board behaves there.

```yaml
models:
  planner: <model-id> # your strongest model
  critic: <model-id> # must differ from the planner
  judge: <model-id> # takes no part in the debate
  verifier: <model-id> # a smaller model is fine
  explainer: <model-id> # a smaller model is fine

debate:
  max_rounds: 4

scoring:
  provider: jev # or: llm
  confidence_threshold: 0.7 # below this, a language model confirms the score

priorities: # what matters in this codebase, and how much
  data_integrity: 5
  availability_at_peak: 5
  delivery_time: 3
  running_cost: 2

constraints:
  - no new managed services
  - zero-downtime deploys only

budget:
  per_call: 10 # spend cap per agent call
  per_run: 60 # the run stops cleanly and stays resumable
```

Model IDs are any that Claude Code accepts. The planner and critic must be different models: a model reviewing its own plan shares its own blind spots.

## Roles

| Role      | Job                                                        |
| --------- | ---------------------------------------------------------- |
| Planner   | Drafts the plan, then fixes or rebuts each objection       |
| Critic    | Raises objections, then rules on the planner's responses   |
| Verifier  | Settles factual disputes by reading the code               |
| Judge     | Breaks deadlocks left after the final round                |
| Explainer | Rewrites the ledger in plain English                       |
| Scorer    | Rates each objection's impact, before and after the debate |

The scorer uses [Jev](https://typesafe.ai) when configured, with a language model as fallback. It rates how much an objection matters. It never decides who is right.

## What you get

Each run writes to `.aiarb/runs/<date>-<name>/`:

| File           | Contents                                                                                  |
| -------------- | ----------------------------------------------------------------------------------------- |
| `plan.md`      | The settled plan, with any open items attached to the sections they affect                |
| `ledger.md`    | Every objection: the problem, what would have happened in production, how it was resolved |
| `decisions.md` | The trade-offs you ruled on, and why                                                      |
| `run.json`     | The full machine-readable record                                                          |

## Run status and exit codes

| Status                  | Meaning                                                          | Exit code |
| ----------------------- | ---------------------------------------------------------------- | --------- |
| Settled                 | No open objections                                               | `0`       |
| Settled with open items | The plan is usable; unresolved items are listed as known risks   | `1`       |
| Not ready               | At least one open item is a blocker. Do not build from this plan | `2`       |

A run never forces agreement. If the round limit is reached, each leftover item is handled by type:

- **Unverifiable facts** become a task at the top of the plan, such as a load test to run first.
- **Trade-offs** come to you as a decision.
- **Circular arguments** go to the judge and are marked "ruled, not agreed".

After you have new evidence or have made a decision, `aiarb resume` reopens only the affected items.

## Safety

- Agents get read and search tools only. Nothing in your repo is modified outside `.aiarb/`.
- Every agent call carries a spend cap, and the run has a total budget.
- Secrets are redacted from logs and outputs.

## Cost and time

A run is not quick. The first round has two models each exploring your repo in depth, which can take an hour apiece on a large codebase. Later rounds are much shorter, because they cover only the open items and each model resumes its earlier session. The CLI shows live progress and running cost throughout.

## Is this a CAB?

No. A change advisory board authorises a finished change for release. aiarb works earlier, at design time, where the question is how to build something. It is closer to an architecture review board, with one difference: it produces and repairs the plan as well as judging it.

## Roadmap

- Multiple planners that each draft a plan, then converge on a merged one
- `aiarb backtest`: replay past changes to see what the board would have caught
- Harnesses for models outside the Claude family
- `aiarb review`: a specialist panel (security, operations, testing) for a single finished plan

## Development

```bash
git clone <repo-url> && cd aiarb
uv sync
uv run pytest
```

The full graph runs in tests on fake adapters, so the test suite makes no API calls.

## Acknowledgements

Inspired by [ai-hedge-fund](https://github.com/virattt/ai-hedge-fund) by Virat Singh, which applies the same idea, a board of specialist agents reaching one decision, to investing.

## Licence

[MIT](./LICENSE)
