# ADR-0016: The planner may ask the user questions before it drafts, between turns

- **Status:** Accepted
- **Date:** 2026-10-06

## Context

Planning in Claude Code itself asks the user clarifying questions when a request
is ambiguous, and the answers often change the plan more than any objection the
critic could raise later. aiarb's planner drafted in one shot: an ambiguous
feature became a guess in the plan's Assumptions, and the debate then argued over
a plan built on it.

Agents cannot ask mid-session. AskUserQuestion is not loaded and the permission
mode is `dontAsk` ([ADR-0002](0002-agents-are-read-only-claude-code-sessions.md)).
Loading AskUserQuestion and answering it from the SDK's `can_use_tool` callback was
the alternative. It would leave a session open while the user thinks, so the node's
time limit would count their thinking, the live progress line would compete with
the prompt, and a crash or Ctrl-C would lose the answers, which are not
checkpointed. It also offers nothing to a run with nobody to answer.

## Decision

The planner's first turn may return questions instead of a plan (`Opening` in
`schemas/outputs.py`): at most four, each with two to four options and the one
it recommends. The run then pauses at a `clarify` step with `interrupt()`, as
`decide` does for trade-offs. Once answered, `draft_plan` carries on the
planner's session with the answers. The planner may ask at most
`QUESTION_ROUNDS` times, then must draft.

The planner is offered questions only when someone can answer: `RunContext.planner_may_ask`
is set from the CLI's `can_ask`. Otherwise it is asked for a `PlanDraft` and states
its assumptions, as before. The answers are requirements: the critic and judge
are shown them, and are told not to object to the choice itself.

## Consequences

- Interactive runs can take one or two more planner turns before the critic
  starts. They cost a little more, and a clearer feature costs nothing extra
  because the planner drafts straight away.
- Unattended runs (`--no-input`, CI, no terminal) behave as before. A run paused
  at `clarify` that is resumed with nobody to answer leaves the unanswered
  questions to the planner.
- The run's pause now comes in two kinds, told apart by the request's `kind`.
  Requests from runs paused before this change have none and mean `decide`.
- The tool list, the hook and the permission mode are unchanged: agents still
  cannot ask mid-session.

## Enforcement

- `tests/graph/test_runner.py::TestPlannerQuestions` pauses, reloads and resumes a run.
- `tests/graph/test_nodes.py::TestDraftPlan` checks that questions are offered only when
  someone can answer and only up to `QUESTION_ROUNDS` times.
- `tests/cli/test_plan.py::TestPlannerQuestions` checks that `--no-input` never offers them.
- `tests/adapters/test_claude_code.py` checks that the real Claude Code accepts the
  `Opening` schema.
