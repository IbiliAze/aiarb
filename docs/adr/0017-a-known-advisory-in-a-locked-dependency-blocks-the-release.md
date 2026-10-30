# ADR-0017: A known advisory in a locked dependency blocks the release

- **Status:** Accepted
- **Date:** 2026-10-30

## Context

A release is whatever `uv.lock` resolved on the day it was tagged
([ADR-0015](0015-released-to-a-private-gitlab-registry.md)), and nothing checked
those packages for published vulnerabilities. The other projects in the group
gate their deploys on osv-scanner (or their package manager's audit). The
alternatives were `pip-audit`, which needs an installed environment or an
exported requirements file, and osv-scanner, which reads `uv.lock` directly and
is already pinned and trusted elsewhere.

## Decision

A `test:audit` job runs osv-scanner over `uv.lock` in GitLab CI, and the
`publish` job needs it, along with lint and tests. GitHub Actions runs the same
check as the `audit` job, so a push to `main` shows it too; GitHub publishes
nothing. The job fails on any advisory OSV knows, dev tools included, unless
`osv-scanner.toml` lists it with a reason and an `ignoreUntil` date. The scanner
image is pinned by tag and digest.

## Consequences

- An advisory published after the last release fails the next pipeline, even
  on a change that does not touch dependencies. The fix is to upgrade the
  package (`uv lock --upgrade-package <name>`) or, when no fix exists, to list
  it in `osv-scanner.toml` with why it does not reach aiarb.
- Dev tools (LangGraph Studio's server, mypy, ruff and their dependencies)
  count. They never ship, but they run on developers' machines.
- Bumping the scanner means changing the tag and digest in both CI files.

## Enforcement

`publish` in `.gitlab-ci.yml` lists `test:audit` in its `needs`. `make publish`
does not run the scan: publishing by hand skips it, which is a convention to
avoid rather than something enforced.
