# ADR-0013: Redact secrets from what is shown and written, not from checkpoints

- **Status:** Accepted
- **Date:** 2026-10-06

## Context

Agents read the whole repo, including hard-coded keys and `.env` files, and can
quote them in objections and the plan. Those texts end up in output files that
people share or commit, in the log, and on the terminal. But a resumed run must
carry on from exactly what the agents said, and the planner and critic resume
their own sessions.

## Decision

A `Redactor` (`observe/redact.py`) takes secrets out of everything aiarb shows or
writes for people: the four output files, `log.jsonl`, the CLI's JSON and its
summary, and the run ID made from the feature. It removes the values of
secret-looking environment variables, keys in well-known formats, private key
blocks, passwords in URLs, and values assigned to names like `password` or
`api_key`, while leaving code such as `password = request.form[...]` alone.

Checkpoints keep the text as it was. They stay in `.aiarb/runs/`, which is out
of git.

## Consequences

- Shared outputs do not leak keys an agent quoted.
- Redaction is best effort: a secret in no known format, assigned to an
  innocent name, gets through. The README says so.
- Secrets still reach the model provider when an agent reads them; preventing
  that would mean refusing to read secret files, which is not done.
- `checkpoints.sqlite` can hold secrets and must not be shared.

## Enforcement

`tests/observe/test_redact.py` covers what is and is not redacted.
`tests/graph/test_watching.py` and `tests/cli/test_safety.py` check secrets in
agents' answers reach no output file, log or command output.
