# ADR-0015: Releases go to a private GitLab package registry, not PyPI

- **Status:** Accepted
- **Date:** 2026-10-06

## Context

aiarb is not ready to be offered publicly, but should install with one command
on any machine that needs it, and released versions must be exactly what was
tagged. The code lives on GitHub and GitLab; GitHub's package registry does not
host Python packages.

## Decision

Releases are published to the PyPI-compatible package registry of the GitLab
project. A release is a commit tagged `v<version>` matching `pyproject.toml`.
Pushing the tag to GitLab publishes it from CI once lint and tests pass, with
the job's own token. `make publish` does the same by hand with a deploy token,
building from the tag rather than the working tree. Both skip files the registry
already has, so publishing again is harmless.

## Consequences

- Installing needs a GitLab token that can read the registry, given to uv or
  pipx with `--index-url`, never `--extra-index-url`, so a same-named package on
  public PyPI cannot be picked instead.
- Builds are reproducible: rebuilding a tag gives byte-identical files, which is
  what lets the registry check recognise them.
- uv sends publish credentials only with uploads, so the registry check URL
  carries the token itself.
- CI needs a runner: the project's instance runners are off, and the group's
  only runner takes `macos`-tagged jobs.

## Enforcement

The CI publish job checks the tag matches the version. `make publish` refuses
when the version has no tag. Nothing stops publishing elsewhere by hand; that is
a convention.
