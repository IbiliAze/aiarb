---
name: aiarb-cli
description: Change aiarb's command line — add or change a command or option, an exit code, the JSON on stdout, the summary or progress on stderr, the questions it asks, or the guided session. Use when adding a flag to plan, resume or init, changing what a command prints, adding a new way for a run to end, or when a command exits with the wrong code.
---

# Changing the CLI

The CLI is `src/aiarb/cli/`, on Typer with rich:

| File | Owns |
| ---- | ---- |
| `app.py` | the `aiarb` app, the error-to-exit-code mapping, the no-command guided session |
| `init.py`, `plan.py`, `resume.py` | one command each, plus a plain function (`init_repo`, `start_run`, `resume_run`) the guided session also calls |
| `interactive.py` | `aiarb` with no arguments |
| `session.py` | running a run: progress, asking for decisions, the summary on stderr and JSON on stdout |
| `progress.py` | the live step line |
| `common.py` | shared options and helpers that fail with a clear message |
| `setup.py` | `ExitCode`, and `Setup`: everything outside the CLI it reaches |

Read [ADR-0011](../../../docs/adr/0011-exit-codes-and-output-streams.md) first.

## Rules

- **People's text goes to stderr, through `setup.console`.** Standard output is
  only the one JSON object per run, printed by `Session.report`.
- **Exit codes 0, 1 and 2 are run outcomes only.** A new way for a command to
  end gets its own `ExitCode` member, a JSON `status`, a line in the README's
  exit code table, and an ADR if it changes what scripts must handle.
- **Fail with `common.fail(setup, message)`**, which prints `Error: ...` and
  exits 4. Do not let an exception reach Typer for a mistake the user can fix.
- **Reach the outside world through `Setup`**: ports, the run store, whether
  someone can answer, the console, the clock, the redactor. Tests replace each
  of them. A command gets it with `setup_of(ctx)`.
- **Ask only when `can_ask`**: `not no_input and setup.can_ask()`. Every
  question must have a non-interactive path: an option, or stopping with the
  command to run.
- **Redact anything an agent wrote before showing it**, with `setup.redactor`
  ([ADR-0013](../../../docs/adr/0013-redact-what-is-shown-not-what-is-checkpointed.md)).
- **Build the context before asking questions** when the run will go ahead, so
  a setup problem such as a missing key does not throw the user's answers away.

## Adding an option

1. Add the parameter with `Annotated[..., typer.Option(...)]`, short help text
   in the voice of the neighbours, and a `metavar` for anything taking a value.
   Shared options (`RepoOption`, `NoInputOption`, `max_rounds_option`) live in
   `common.py`.
2. Pass it through the command's plain function, so the guided session can use
   it too, and offer it there if a user would want it.
3. Validate early and fail with a message that says what to do instead.
4. Update the README's *Commands* table, and the quick start if it is common.
5. Test it with `tests/cli/helpers.aiarb`: the exit code, the JSON, and the
   stderr line a user relies on. Include the mistake case.

## Checks

```bash
uv run pytest tests/cli
make check
uv run aiarb --help    # read the help as a user would
```
