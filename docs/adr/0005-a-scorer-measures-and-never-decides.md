# ADR-0005: A scorer measures impact and never decides who is right

- **Status:** Accepted
- **Date:** 2026-10-06

## Context

Not every objection deserves a debate, and a run's outcome turns on how serious
its open items are: one blocker makes a plan "not ready". Severity needs a
judgement separate from the planner and critic, who each have a side. Jev
(TypeSafe) gives calibrated scores with a confidence, but is optional and can be
unsure; a language model is always available.

## Decision

A `Scorer` rates how much an objection matters, on a rubric, with a confidence.
It never decides who is right; that is the debate's job. Triage scores new
objections, and the minor ones skip the debate ([ADR-0009](0009-minor-objections-are-noted-not-debated.md)).
Rescore rates what is still open after the debate.

With `scoring.provider: jev`, Jev scores first and a language model scores again
when Jev's confidence is below `confidence_threshold`. Whatever the provider, a
language model confirms every blocker left after the debate, and the blocker
stands unless that model confidently says it is not one. An open item rated
lower after the debate than before, though nobody settled it, is flagged.

## Consequences

- Scores never decide an objection, so a wrong score costs at worst a skipped
  debate or a mislabelled risk, both visible in the ledger with the score and
  confidence that produced them.
- The "not ready" verdict takes two independent judgements to reach.
- Every judgement records its scorer's confidence, and an escalated one records
  the first answer it replaced.
- Downgrades of unsettled items are flagged rather than trusted, since a debate
  can wear an objection down without answering it.

## Enforcement

`EscalatingScorer` and `scorers_for` in `adapters/scoring.py`, tested in
`tests/adapters/test_scoring.py`. Rescore's confirmation and flagging are tested
in `tests/graph/test_nodes.py`.
