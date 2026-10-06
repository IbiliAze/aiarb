# ADR-0008: aiarb writes only inside .aiarb/ in the target repo

- **Status:** Accepted
- **Date:** 2026-10-06

## Context

aiarb runs in repos that belong to someone else, often with uncommitted work in
them. Anything it wrote outside its own folder would show up in their diffs, and
anything it changed would be a serious breach of trust. Runs also need
somewhere to keep checkpoints, logs, outputs and the scan cache.

## Decision

Everything aiarb writes in a target repo goes under `.aiarb/`: the committed
`charter.yaml`, a `.gitignore` that keeps `runs/` and `cache/` out of git, the
run folders, and the cached fact sheet. Within a run, only the `finalise` node
writes the output files. Git is called read-only, with `GIT_OPTIONAL_LOCKS=0` so
that even `git status` does not refresh the index. Agents cannot write at all
([ADR-0002](0002-agents-are-read-only-claude-code-sessions.md)).

As a check on all of this, the runner notes every file the scan would see before
each invocation and compares afterwards. Changes outside `.aiarb/` are logged
and shown as a warning. Most likely the user edited files during the run, which
the review may not reflect.

## Consequences

- A run is safe to point at a working copy with uncommitted changes.
- Users see one untracked folder, and decide whether to commit the charter.
- The check costs one file listing and stat per file at each end of an
  invocation. It does not cover files git ignores.

## Enforcement

`tests/cli/test_safety.py` runs a full plan and resume on a git copy of a
fixture repo and checks every byte outside `.aiarb/`, `.git` included, is
unchanged. `tests/graph/test_watching.py` checks the runner reports a change made
during a run.
