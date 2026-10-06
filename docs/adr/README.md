# Architecture decision records

Why aiarb is built the way it is. Read the relevant record before changing a
decision it describes, and add one, from [the template](0000-template.md), when
you make a decision of the same weight. A record is never edited to say
something different: a new one supersedes it.

ADRs 0001 to 0015 were written down on 2026-10-06, after the v1 build in which
the decisions were made.

| ADR | Decision |
| --- | -------- |
| [0001](0001-langgraph-orchestrates-claude-code-agents.md) | LangGraph orchestrates the debate, and every agent is a Claude Code session |
| [0002](0002-agents-are-read-only-claude-code-sessions.md) | Agents are read-only Claude Code sessions confined to the target repo |
| [0003](0003-ports-and-adapters-tested-on-fakes.md) | The graph depends on ports, and is built and tested on scripted fakes |
| [0004](0004-objections-close-only-on-a-plan-change-or-evidence.md) | Objections close only on a plan change or cited evidence, and every move is checked |
| [0005](0005-a-scorer-measures-and-never-decides.md) | A scorer measures impact and never decides who is right |
| [0006](0006-leftovers-are-handled-by-type.md) | Items left at the round limit are handled by type, never forced into agreement |
| [0007](0007-each-run-is-a-checkpointed-thread-in-its-own-folder.md) | Each run is a checkpointed LangGraph thread with its own folder |
| [0008](0008-aiarb-writes-only-inside-dot-aiarb.md) | aiarb writes only inside `.aiarb/` in the target repo |
| [0009](0009-minor-objections-are-noted-not-debated.md) | Minor objections are noted rather than debated, and listed in the plan |
| [0010](0010-reopening-restarts-the-debate-from-triage.md) | Reopening items restarts the debate from triage, with a full set of rounds |
| [0011](0011-exit-codes-and-output-streams.md) | Exit codes say how a run came out; JSON goes to stdout and everything else to stderr |
| [0012](0012-the-budget-counts-every-call.md) | The run's budget counts every call, from the run's log |
| [0013](0013-redact-what-is-shown-not-what-is-checkpointed.md) | Redact secrets from what is shown and written, not from checkpoints |
| [0014](0014-the-charter-is-per-repo-and-settings-come-from-the-environment.md) | The charter is per repo and strict; personal settings come only from the environment |
| [0015](0015-released-to-a-private-gitlab-registry.md) | Releases go to a private GitLab package registry, not PyPI |
| [0016](0016-the-planner-may-ask-before-it-drafts.md) | The planner may ask the user questions before it drafts, between turns |
