# aiarb

**An AI architecture review board for your codebase.** Give it a feature you want to add to an existing app. One model plans it, another attacks the plan, and they argue it out against your actual code until the plan is settled. You get one polished plan, a plain-English record of every objection, and a short list of the decisions only you can make.

> **Status: 1.0.0.** aiarb is not on public PyPI: it is published to a private GitLab registry, as below.

## Why

Ask one model to plan a feature and it will give you a confident plan. Show that plan to a second model and it will find twenty things wrong with it. Feed those back to the first and it will defend itself. You end up relaying messages between models and refereeing an argument that may go beyond what you can judge.

aiarb runs that argument for you, under rules:

- Every objection must cite the code or the plan line it concerns.
- An objection closes only when the plan changes or evidence settles it. "You're right" is not a resolution.
- Factual disputes are checked against the repo, not argued.
- Real trade-offs are brought to you as plain-English questions, not settled silently.

## What is an architecture review board?

In larger engineering organisations, an architecture review board is a group of senior engineers who look at significant designs before they are built. They check a proposal against the system as it really is, the organisation's standards and what the business cares about, and they catch the expensive mistakes while they are still cheap to fix: the queue that loses data on deploy, the table that locks at peak, the new service nobody can run.

Their weakness is that they are slow and scarce. A design waits for the next meeting, and small changes never get reviewed at all.

aiarb gives you that review on demand, with two differences:

- **It writes the plan as well as judging it.** The board drafts the plan, attacks it and repairs it, so you get a revised plan rather than a list of complaints.
- **It knows what it cannot decide.** Questions of fact are checked against the code. Questions of priority, such as fairness against speed, come to you, because only you know the answer.

It is not a change advisory board (CAB). A CAB approves a finished change for release. aiarb works earlier, at design time, where the question is how to build something.

## How it works

```
aiarb plan
   │
   ▼
Scan the repo ──────────── builds a fact sheet, no LLM
   │
   ▼
Planner drafts the plan ── model A explores the repo, and may ask you
   │                         a few questions first, as Claude Code does
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
- Claude Code, signed in, or an Anthropic API key. aiarb uses the copy of Claude Code that comes with the Claude Agent SDK, or the `claude` on your `PATH` where the SDK has none
- Optional: a TypeSafe API key, to use Jev for objection scoring

## Install

aiarb is published to the package registry of its GitLab project, which is private. Install it with [pipx](https://pipx.pypa.io), using a GitLab token that can read the registry: a deploy token with `read_package_registry`, or a personal access token with `read_api`:

```bash
pipx install aiarb --index-url \
  "https://<token-name>:<token>@gitlab.com/api/v4/projects/projects4833423%2Faiarb/packages/pypi/simple"
```

For a personal access token, `<token-name>` is your GitLab username. The registry passes requests for aiarb's dependencies on to PyPI. Use `--index-url`, not `--extra-index-url`, so a package of the same name on public PyPI can never be picked instead.

Or install from a clone:

```bash
git clone git@gitlab.com:projects4833423/aiarb.git && cd aiarb
pipx install .
```

`uv tool install` works in place of `pipx install` in both.

If Claude Code is signed in, aiarb uses that. Otherwise, set an API key:

```bash
export ANTHROPIC_API_KEY=...
export TYPESAFE_API_KEY=...   # optional, to score with Jev
```

Check the install with `aiarb --version`.

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

If the feature leaves something open that the plan depends on, the planner asks you first: up to four questions, each with options and the one it recommends. Pick a number, type your own answer, or leave it blank to let the planner choose. It asks only at a terminal; without one, or with `--no-input`, it states its assumptions in the plan instead.

While a run goes, aiarb shows each step, the round, how many objections are open and what the run has spent. If the run needs your decisions, it asks for them there and then. Without a terminal, or with `--no-input`, it stops and prints the questions and the command to answer them:

```bash
aiarb resume 2026-10-05-add-a-virtual-waiting-room --decide OBJ-4="Fairness first: batches of 100"
```

Got new evidence after a run, such as load-test results? Reopen the items it bears on, and they are debated again with a full set of rounds:

```bash
aiarb resume --evidence OBJ-1=@load-test-results.md
```

`--decide` also answers the planner's questions, by their ids (`Q-1`). `ID=@file` reads a decision, an answer or evidence from a file. Without a run ID, `aiarb resume` takes the most recently active run. A run that stopped part way, from a failed call or Ctrl-C, carries on from its last finished step.

## Commands

| Command        | What it does                                                              |
| -------------- | ------------------------------------------------------------------------- |
| `aiarb`        | Interactive mode                                                          |
| `aiarb init`   | Write a starter charter to `.aiarb/charter.yaml`, and keep runs out of git. Options: `--jev`/`--llm` for the scorer (default: Jev if `TYPESAFE_API_KEY` is set), `--force` to replace a charter |
| `aiarb plan`   | Start a run. Options: `--feature`, `--plan` (repeatable), `--max-rounds`, `--no-input` |
| `aiarb resume` | Continue a paused or stopped run. Options: `--decide ID=...` and `--evidence ID=...` (both repeatable), `--max-rounds` for a reopened debate, `--no-input` |

`plan` and `resume` print JSON describing the run to standard output, and everything meant for people to standard error, so scripts can read the result and the exit code.

## The charter

`.aiarb/charter.yaml` is committed to your repo and defines how the board behaves there. `aiarb init` writes one with every default spelled out. For example:

```yaml
models:
  planner: <model-id> # your strongest model
  critic: <model-id> # must differ from the planner
  judge: <model-id> # takes no part in the debate
  verifier: <model-id> # a smaller model is fine
  explainer: <model-id> # a smaller model is fine
  scorer: <model-id> # scores when Jev is not used, and confirms blockers

debate:
  max_rounds: 4

scoring:
  provider: llm # or: jev, with TYPESAFE_API_KEY set
  confidence_threshold: 0.7 # below this, a language model rescores Jev's answer

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

Every setting is optional, and an empty charter is valid. Unknown keys are refused, so a typo fails loudly instead of quietly falling back to a default.

| Setting                        | Default             | Meaning |
| ------------------------------ | ------------------- | ------- |
| `models.planner`               | `claude-opus-5-5`   | Drafts and revises the plan. Use your strongest model |
| `models.critic`                | `claude-sonnet-5-5` | Raises objections and rules on answers. Must differ from the planner |
| `models.judge`                 | `claude-opus-5-5`   | Rules on design deadlocks left at the round limit |
| `models.verifier`              | `claude-sonnet-5-5` | Checks disputed facts against the code |
| `models.explainer`             | `claude-sonnet-5-5` | Writes the ledger in plain English |
| `models.scorer`                | `claude-sonnet-5-5` | Scores impact when Jev is not used, and confirms blockers |
| `debate.max_rounds`            | `4`                 | Rounds before leftover items go to the judge or to you. `--max-rounds` overrides it for one run |
| `scoring.provider`             | `llm`               | `llm`, or `jev` with `TYPESAFE_API_KEY` set |
| `scoring.confidence_threshold` | `0.7`               | Below this confidence, a language model scores Jev's answer again, and a language model's doubt about a blocker does not count |
| `scoring.thresholds.moderate`  | `0.25`              | The impact score, from 0 to 1, where moderate starts. Anything lower is minor and skips the debate |
| `scoring.thresholds.major`     | `0.5`               | Where major starts |
| `scoring.thresholds.blocker`   | `0.8`               | Where blocker starts. One open blocker makes a plan "not ready" |
| `priorities`                   | none                | What matters in this codebase, as `name: weight` from 1 (a little) to 5 (most). Names are lower case with underscores |
| `constraints`                  | none                | Rules every plan must keep to, one per line |
| `budget.per_call`              | `10`                | The most one agent call may spend, in US dollars |
| `budget.per_run`               | `60`                | The most one run may spend, in US dollars. At least `per_call` |

Model IDs are any that Claude Code accepts. The planner and critic must be different models: a model reviewing its own plan shares its own blind spots.

## Roles

| Role      | Job                                                        |
| --------- | ---------------------------------------------------------- |
| Planner   | Asks what it must, drafts the plan, then fixes or rebuts each objection |
| Critic    | Raises objections, then rules on the planner's responses   |
| Verifier  | Settles factual disputes by reading the code               |
| Judge     | Breaks deadlocks left after the final round                |
| Explainer | Rewrites the ledger in plain English                       |
| Scorer    | Rates each objection's impact, before and after the debate |

The scorer uses [Jev](https://typesafe.ai) when configured, with a language model as fallback. It rates how much an objection matters. It never decides who is right.

- With `provider: jev`, any score Jev gives with less confidence than `confidence_threshold` is scored again by a language model. Set `TYPESAFE_API_KEY` to use Jev.
- With `provider: llm`, a language model scores everything.
- Either way, a language model confirms every blocker left after the debate, since one blocker makes the run "not ready". The blocker stands unless the model is confident it is not one.
- An open item rated lower after the debate than before, though nobody settled it, is flagged for a second look.

## What you get

Each run writes to `.aiarb/runs/<date>-<name>/`, which `.aiarb/.gitignore` keeps out of git:

| File           | Contents                                                                                  |
| -------------- | ----------------------------------------------------------------------------------------- |
| `plan.md`      | The settled plan. Above it, what to settle before building, the rulings and decisions to apply, and minor notes that were never debated; each is also noted under the plan sections it affects |
| `ledger.md`    | Every objection in plain English: the problem, what would have happened in production, how it ended, and the full debate |
| `decisions.md` | The trade-offs you decided, with each side's last word                                    |
| `run.json`     | The full machine-readable record                                                          |
| `log.jsonl`    | One JSON object per line for each step and outside call: when, how long, on what model, what it spent, and how it failed if it did |

## Run status and exit codes

| Status                  | Meaning                                                          | Exit code |
| ----------------------- | ---------------------------------------------------------------- | --------- |
| Settled                 | No open objections                                               | `0`       |
| Settled with open items | The plan is usable; unresolved items are listed as known risks   | `1`       |
| Not ready               | At least one open item is a blocker. Do not build from this plan | `2`       |
| Waiting for decisions   | The run has paused for your decisions; `aiarb resume` takes them | `3`       |
| Stopped                 | aiarb could not run as asked, or the run stopped part way; a stopped run can be resumed | `4` |
| Over budget             | The run spent `budget.per_run`; raise it, then `aiarb resume`    | `5`       |

Ctrl-C exits with `130`, and the run can be resumed. Stopped at one of the planner's questions, its JSON status is `waiting_for_answers`, and `aiarb resume` asks them again.

A run never forces agreement. If the round limit is reached, each leftover item is handled by type:

- **Unverifiable facts** become a task under *Before you build* at the top of the plan, such as a load test to run first.
- **Trade-offs** come to you as a decision.
- **Circular arguments** go to the judge and are marked "ruled, not agreed".

After you have new evidence or have made a decision, `aiarb resume` reopens only the affected items.

## Safety

- Agents get read and search tools only, and cannot read outside your repo. Nothing in your repo is modified outside `.aiarb/`. As a check, each run compares the repo's files before and after, and warns if any outside `.aiarb/` changed, most likely because you edited them while it ran.
- Every agent call carries a spend cap, `budget.per_call`. The run has a total budget, `budget.per_run`, which counts every call, including those in steps that failed and were retried. Once the run has spent it, no further call starts and each Claude Code call is capped at what is left, so a run overshoots by at most the calls already under way. The run stops with exit code `5`, keeps its progress, and carries on with `aiarb resume` once you raise the budget.
- Secrets are redacted from the output files, the log and what the commands print: the values of secret-looking variables in your environment (`ANTHROPIC_API_KEY` and the like), keys in well-known formats, private keys, passwords in URLs, and values assigned to names like `password` or `api_key`. Redaction is best effort. The checkpoints keep the run's text as it was, so a resumed run carries on from what the agents actually said; they stay in `.aiarb/runs/`, out of git.

## Cost and time

Most of a run's time and cost is the planner's first draft, when it explores the repo. Later rounds are much shorter, because they cover only the open items and each model resumes its earlier session. The CLI shows live progress and running cost throughout.

Measured on real repos, with Opus 5.5 as planner and Sonnet 5.5 in every other role:

| Repo                          | Feature                                    | Rounds | Time   | Cost  |
| ----------------------------- | ------------------------------------------ | ------ | ------ | ----- |
| Next.js client, ~1,450 files  | Grow a venue map section on hover          | 0      | 2m 54s | $0.82 |
| Spring Boot API, ~430 files   | Add a count endpoint with the list filters | 0      | 1m 29s | $0.68 |
| Flutter app, ~350 files       | Show the scan count in a screen's title    | 0      | 1m 06s | $0.38 |

These were small features: the critic's only objection each time was minor, so there was no debate. The planner's draft was 65–80% of the cost. Each round of debate adds a planner and a critic turn, which took one to two minutes and around $0.15 in smaller test runs. Expect a large feature on a large codebase to take much longer, and set `budget.per_run` to match.

## Roadmap

- Multiple planners that each draft a plan, then converge on a merged one
- `aiarb backtest`: replay past changes to see what the board would have caught
- Harnesses for models outside the Claude family
- `aiarb review`: a specialist panel (security, operations, testing) for a single finished plan

## Development

```bash
git clone git@gitlab.com:projects4833423/aiarb.git && cd aiarb
uv sync
uv run pytest
```

The full graph runs in tests on fake adapters, so the test suite makes no API calls.

[AGENTS.md](AGENTS.md) has the working notes: architecture, the rules that are enforced and the conventions. [docs/adr/](docs/adr/) records why the design is the way it is, and `.claude/skills/` has step-by-step guides for changing the graph, the agents, the tests, the CLI and releases.

There are also live tests, which run each role and scorer through real Claude Code, mostly on Haiku, and call Jev when `TYPESAFE_API_KEY` is set. A full live run costs about a dollar:

```bash
AIARB_LIVE_TESTS=1 uv run pytest tests/live
```

`make help` lists the tasks: `make check` lints, type-checks and tests, as CI does.

To release, set the version in `pyproject.toml`, commit, and tag the commit `v<version>`. Pushing the tag to GitLab publishes it to the registry once the checks pass (`.gitlab-ci.yml`). To publish by hand instead, run `make publish`. It builds the tagged commit, not your working tree, and needs a GitLab token that can write to the registry, as `UV_PUBLISH_USERNAME` and `UV_PUBLISH_PASSWORD`, exported or in `.env`. Files the registry already has are skipped.

To see the graph in [LangGraph Studio](https://docs.langchain.com/langgraph-platform/langgraph-studio), run `uv run langgraph dev`.

## Acknowledgements

Inspired by [ai-hedge-fund](https://github.com/virattt/ai-hedge-fund) by Virat Singh, which applies the same idea, a board of specialist agents reaching one decision, to investing.

## Licence

[MIT](./LICENSE)
