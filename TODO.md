# aiarb v1 build checklist

## Scope

**In v1:** one planner, one critic, a verifier, a judge, Jev scoring with a language-model fallback, the human decision step, resume, and the four output files.

**Not in v1:** multiple planners, backtest, non-Claude harnesses.

**Build order:** the whole graph is built and tested on fakes before any real model is called, so the logic is debugged at no API cost.

**First milestone:** the end of section 6, a complete debate that pauses and resumes, running entirely on fakes. Everything after that replaces fakes with real components one at a time.

---

## 1. Project setup

- [x] Create the repo with the agreed directory structure and `src/` layout
- [x] Write `pyproject.toml`: dependencies, `aiarb` entry point, ruff, strict mypy, pytest
- [x] Add `.env.example`, `.gitignore` (include `.aiarb/runs/`), licence, README stub
- [x] Add the CI workflow: lint, type-check, tests
- [ ] ~~Publish a placeholder package to hold the `aiarb` name~~

## 2. Domain (plain Python)

- [x] Define `Objection`: id, claim, evidence reference, type, severity, status, history
- [x] Define the statuses and the allowed transitions between them
- [x] Enforce the closing rule: an item closes only with a plan change or cited evidence
- [x] Define the severity rubric and thresholds
- [x] Write the outcome function: ledger in, run status and exit code out
- [x] Unit-test every transition and outcome case

## 3. Config

- [x] Model the charter in pydantic: models per role, max rounds, rubric weights, thresholds, budget caps
- [x] Load and validate `.aiarb/charter.yaml`, with defaults when it's absent
- [x] Add settings for API keys from the environment
- [x] Test invalid charters produce clear errors

## 4. Ports and fakes

- [x] Define the `CodeAgent` protocol: prompt, repo, model, schema, session in; typed result, session ID, cost out
- [x] Define the `Scorer` protocol: score, choice, yes/no, each with confidence
- [x] Write a scripted fake for each, driven by test fixtures

## 5. Graph on fakes

- [x] Define the state schema with reducers for the ledger and cost totals
- [x] Define the runtime context: charter plus the injected ports
- [x] Write each node as a small function returning a state update
- [x] Write the routing functions: next round, stalemate, round limit, judge needed, decisions needed
- [x] Assemble the graph in `build_graph()` with retry policies and timeouts on agent nodes
- [x] Set the recursion limit from the charter's max rounds
- [x] Add `langgraph.json` and check the graph loads in Studio
- [x] Graph tests on fakes: clean settle, stalemate, round limit, judge path, blocker left open

## 6. Checkpointing and the decision step

- [x] Add the SQLite checkpointer under `.aiarb/runs/<run-id>/`
- [x] Use the run ID as the thread ID
- [x] Implement the decision node with `interrupt()`, passing the questions out
- [x] Test: run to the interrupt, reload from disk, resume with answers, finish
- [x] Test: kill a run mid-round and resume from the last checkpoint

## 7. Repo scan

- [x] Write collectors: stack, dependencies, tests, infrastructure files, CI config, git churn
- [x] Assemble and cache the fact sheet, keyed on the commit hash
- [x] Test against the fixture repos

## 8. Claude Code adapter

- [x] Implement `CodeAgent` with the Agent SDK
- [x] Restrict tools to read and search only, and test that a write attempt is refused
- [x] Pass the JSON schema for structured output and validate the result
- [x] Store and reuse session IDs so later rounds resume
- [x] Apply the per-call spend cap and capture cost and token usage
- [x] Map SDK errors to retryable and fatal
- [x] Handle a missing Claude Code install with a clear message

## 9. Prompts and schemas

- [x] Write the JSON schema for each role's output
- [ ] Write the planner prompts: draft, and respond to objections
- [ ] Write the critic prompts: raise objections, and rule on responses
- [ ] Write the verifier, judge and explainer prompts
- [ ] Run each role alone on a fixture repo and check the output validates

## 10. Scoring

- [ ] Implement the Jev adapter with the rubric as score levels
- [ ] Implement the language-model fallback scorer
- [ ] Escalate to the fallback when confidence is below the charter threshold
- [ ] Wire triage: minors skip debate, the rest are ordered by severity
- [ ] Wire rescoring of open items after the debate
- [ ] Flag items whose severity dropped without being withdrawn
- [ ] Confirm blocker and "not ready" calls with a language model

## 11. Output

- [ ] Write `plan.md`, with open items attached to the sections they affect
- [ ] Write `ledger.md` in plain English: problem, consequence, resolution
- [ ] Write `decisions.md` and `run.json`
- [ ] Keep all file writing in the final node only

## 12. CLI

- [ ] `aiarb init`: write a starter charter
- [ ] `aiarb plan`: with `--feature`, repeatable `--plan`, `--max-rounds`, all optional
- [ ] `aiarb resume`: continue a run, accepting decisions or new evidence
- [ ] Interactive mode when run with no arguments
- [ ] Live progress: current step, round, open items, running cost
- [ ] Exit codes 0, 1, 2, with JSON on stdout and the summary on stderr

## 13. Observability and safety

- [ ] Structured logs per run, written to the run folder
- [ ] A total budget cap that stops the run cleanly and leaves it resumable
- [ ] Redact secrets from logs and outputs
- [ ] Confirm nothing in the target repo is modified, apart from `.aiarb/`

## 14. Real-world proof

- [ ] Run it on the ticketing app's waiting room feature and compare with the decision reached by hand
- [ ] Run it on two other repos of different stacks
- [ ] Fix what those runs expose, then record their fixtures as regression tests
- [ ] Measure run time and cost and put typical figures in the README

## 15. Release

- [ ] Write the README: what an ARB is, install, quick start, charter reference, cost expectations
- [ ] Tag 1.0.0 and publish to PyPI
- [ ] Check a clean install works with `pipx install aiarb`
