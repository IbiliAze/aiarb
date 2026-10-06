---
name: aiarb-testing
description: Write and fix aiarb's tests — scripted scenarios for the graph, the fake agent and scorer, CLI tests, recorded real runs, and the live tests. Use when adding a test, writing a scenario YAML, when a test fails with ScriptError or "never used", when recording a real run as a regression test, or when deciding whether a change needs a live test.
---

# Testing aiarb

The suite runs the whole graph on scripted fakes and makes no API calls
([ADR-0003](../../../docs/adr/0003-ports-and-adapters-tested-on-fakes.md)).
It takes about 15 seconds: `uv run pytest`. `make check` adds ruff and strict
mypy, which cover `tests/` too.

## Where tests go

| Testing | Folder | Uses |
| ------- | ------ | ---- |
| Domain rules, charter, schemas | `tests/unit/` | plain objects |
| One node, routing, the runner, watching | `tests/graph/` | scenarios, `RunContext` |
| Adapters | `tests/adapters/` | fake SDK `query`, fake HTTP |
| Output files | `tests/output/` | `tests/output/examples.py` |
| Commands, exit codes, JSON, prompts | `tests/cli/` | `tests/cli/helpers.aiarb` |
| Repo scan | `tests/scan/` | fixture repos, `tests/scan/repos.py` |
| Real models | `tests/live/` | Claude Code, Jev; opt-in |

Name tests as sentences saying what should happen, grouped in classes by
behaviour: `test_a_run_over_its_budget_stops_with_exit_5`.

## Scenarios

A scenario is a YAML script for the fake agent and scorer, in
`tests/fixtures/scenarios/`:

```yaml
agent:
  - schema: PlanDraft                # the answer schema the call asks for
    output: {plan: "# Queue\n\n..."} # validated against the real schema
    cost: {usd: 2}
  - schema: Verdict
    contains: OBJ-1                  # text the prompt must contain
    error: retryable                 # fail instead of answering (or fatal)
scorer:
  - {call: score, contains: OBJ-1, value: 0.6, confidence: 0.9}
```

- A call takes the first **unused** step that matches its schema and `contains`,
  so concurrent calls need not arrive in order. Use `contains: OBJ-n` whenever two
  calls ask the same schema.
- `ScriptError` means a call nothing in the script matched: the code asked
  something the scenario did not expect. Call `scenario.assert_finished()` at the
  end of a test, which fails on steps never used.
- A run that resumes an agent session needs the fake to know it:
  `ScriptedCodeAgent(steps, sessions=["session-1", "session-2"])`.
- Build small variations in the test with `Scenario.from_data(...)`, or load a
  file and drop steps, as `until_paused()` in `tests/cli/test_resume.py` does.
- The named scenarios cover each way a run ends: `clean_settle`, `round_limit`,
  `blocker_left_open`, `decision`, `judge_path`, `stalemate`.

## CLI tests

`tests/cli/helpers.aiarb(repo, *args, ports=..., can_ask=..., input=...)` runs
the real CLI on scripted ports, a fixed date, a clock that moves a minute per
read, a plain console and a redactor that knows no environment secrets. It
returns an `Outcome` with `.code`, `.stderr` and `.json` (stdout parsed). Passing
the same `Scenario` to two calls continues its script, which is how plan-then-
resume tests work.

## Recording a real run as a regression test

Runs on real repos find what fakes cannot. To keep one:

1. Read the run's `run.json`.
2. Turn it into a scenario under `tests/fixtures/scenarios/real/`: the plan as
   the `PlanDraft` output, the critic's objections as `Critique` (with the
   severity from each objection's first event), the scores from the scorer's
   history notes, and the explanation as `Explanation`. Write long text as YAML
   block scalars so it reads well in review.
3. Head it with a comment: date, models, feature, and what the run exposed.
4. Add it to `FEATURES` in `tests/cli/test_real_runs.py` with an assertion about
   what it exposed. Check the test fails without the fix.

## Live tests

`tests/live/` runs each role, the adapter and the scorers against real Claude
Code and Jev, on a copy of the `python-api` fixture repo. They are skipped unless
`AIARB_LIVE_TESTS=1`, use Haiku where they can, and cost about a dollar in all.
**Run them only when the user agrees to spend that.** They are the check after
changing a brief, a prompt, a schema or the adapter.
