# CLAUDE.md

## Read the core

Everything about how this repo works (what it is, commands, architecture, the
rules that are enforced, conventions) is in AGENTS.md. Read it before writing
code.

@AGENTS.md

## Then the docs

- [docs/adr/](docs/adr/): why the design is the way it is. Read the relevant ADR
  before proposing a change to a decision it records, and add one, from
  [the template](docs/adr/0000-template.md), when you make a decision of the same
  weight.
- [.claude/skills/](.claude/skills/): step-by-step guides for the recurring
  jobs: changing the graph, changing what an agent is asked, writing tests,
  changing the CLI, and releasing.

## Working here

- `make check` must pass before you call anything done. It is what CI runs.
- Do not run the live tests (`AIARB_LIVE_TESTS=1`) or a real `aiarb plan` unless
  asked: both spend money on real model calls.
- A real run writes into the target repo's `.aiarb/`. Never point one at a repo
  you have not been asked to use; use a scratch copy of
  `tests/fixtures/repos/python-api` instead.
- Keep README.md in step with behaviour a user can see: options, exit codes,
  output files, charter settings.
