---
name: aiarb-release
description: Release aiarb — set the version, tag, publish to the private GitLab package registry, and check the install. Use when cutting a release, when `make publish` or the CI publish job fails, when a version needs republishing, or when someone asks how to install aiarb.
---

# Releasing aiarb

aiarb is published to the PyPI-compatible registry of its GitLab project, not to
public PyPI ([ADR-0015](../../../docs/adr/0015-released-to-a-private-gitlab-registry.md)).

- Registry: `https://gitlab.com/api/v4/projects/projects4833423%2Faiarb/packages/pypi`
- Remotes: `origin` is GitHub, `gitlab` is GitLab. Both get `main`; tags that
  publish go to GitLab.

## Cutting a release

1. `make check` passes on `main`.
2. Set `version` in `pyproject.toml`, run `uv lock` (it records the project's
   own version), update the README if behaviour changed, and commit.
3. Tag that commit: `git tag -a v<version> -m "aiarb <version>"`.
4. Push: `git push origin main && git push gitlab main v<version>`.
5. CI publishes it once lint, tests and the dependency audit (`test:audit`,
   [ADR-0017](../../../docs/adr/0017-a-known-advisory-in-a-locked-dependency-blocks-the-release.md))
   pass. The publish job refuses a tag that does not match `pyproject.toml`.

**CI needs a runner.** The project's instance runners are off, and the group's
only runner (`ibi-mac`) takes jobs tagged `macos` only. Until that is sorted,
publish by hand.

## Publishing by hand

```bash
make publish
```

- Builds from the tag `v<version>`, not the working tree, so uncommitted edits
  can never be released. It stops if the tag does not exist.
- Needs a GitLab token that can write to the registry, as `UV_PUBLISH_USERNAME`
  and `UV_PUBLISH_PASSWORD`, exported or in `.env` (gitignored; see
  `.env.example`). For a deploy token, the username is
  `gitlab+deploy-token-<id>`.
- Files the registry already has are skipped. Builds are reproducible, so a
  rebuilt tag matches what was uploaded.
- **Never print, commit or write a token into a tracked file.** The check URL
  carries the token because uv sends publish credentials only with uploads.

If the registry answers `File name has already been taken`, the files differ
from what is there: the version was published from different code. Releases are
never replaced; bump the version.

## Checking the install

In a fresh shell, outside the project's virtualenv:

```bash
export UV_INDEX_AIARB_USERNAME=<token username>
export UV_INDEX_AIARB_PASSWORD=<token>
uv tool install aiarb \
  --index aiarb=https://gitlab.com/api/v4/projects/projects4833423%2Faiarb/packages/pypi/simple
aiarb --version
```

To check without touching the user's tools, set `UV_TOOL_DIR` and
`UV_TOOL_BIN_DIR` to a scratch folder first. Then `aiarb init` in an empty git
repo, and a small real run only if the user agrees to spend on it.
