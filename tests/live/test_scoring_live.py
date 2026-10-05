"""Both scorers on objections whose impact is plain, through the real services.
The language model runs on Claude Code; Jev needs TYPESAFE_API_KEY as well.
These spend money, a few cents a run, so they run only when asked:

    AIARB_LIVE_TESTS=1 uv run pytest tests/live/test_scoring_live.py -s
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from aiarb.adapters.claude_code import ClaudeCodeAgent
from aiarb.adapters.jev import JevScorer
from aiarb.adapters.llm_scorer import LanguageModelScorer
from aiarb.config.charter import Charter
from aiarb.domain.ledger import CodeRef, Objection, ObjectionType, PlanRef
from aiarb.domain.scoring import Severity
from aiarb.graph import prompts
from aiarb.graph.nodes._calls import severity_levels

if TYPE_CHECKING:
    from collections.abc import Callable

    from aiarb.ports.scorer import Scorer

pytestmark = pytest.mark.skipif(
    os.environ.get("AIARB_LIVE_TESTS") != "1",
    reason="calls real services; set AIARB_LIVE_TESTS=1 to run",
)

FIXTURE = Path(__file__).parent.parent / "fixtures" / "repos" / "python-api"
HAIKU = "claude-haiku-4-5-20251001"
CHARTER = Charter.model_validate(
    {
        "priorities": {"data_integrity": 5, "running_cost": 2},
        "constraints": ["no new managed services"],
        "budget": {"per_call": 1, "per_run": 10},
    }
)
THRESHOLDS = CHARTER.scoring.thresholds


def raised(id: str, claim: str, *evidence: CodeRef | PlanRef) -> Objection:
    return Objection.raised(
        id=id,
        claim=claim,
        evidence=evidence,
        type=ObjectionType.DESIGN,
        severity=Severity.MAJOR,
        round=0,
    )


DOUBLE_SALE = raised(
    "OBJ-1",
    "Holds are kept in a Python dict in one worker process, but the app runs four "
    "workers. Two buyers on different workers can both hold and pay for the same "
    "ticket, so tickets are routinely sold twice.",
    PlanRef("Approach", "Keep holds in a module-level Python dict"),
    CodeRef("Dockerfile"),
)
TYPO = raised(
    "OBJ-2",
    'The docstring of the new hold endpoint says "recieve" instead of "receive".',
    PlanRef("Changes"),
)


def language_model() -> LanguageModelScorer:
    agent = ClaudeCodeAgent.from_charter(CHARTER)
    return LanguageModelScorer(agent, model=HAIKU, repo=FIXTURE)


def jev() -> JevScorer:
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        pytest.skip("set TYPESAFE_API_KEY to call Jev")
    return JevScorer(key)


SCORERS = [pytest.param(language_model, id="language-model"), pytest.param(jev, id="jev")]


async def impact(scorer: Scorer, item: Objection) -> Severity:
    judgement = await scorer.score(
        prompts.scored(item, CHARTER), prompts.IMPACT, severity_levels(THRESHOLDS)
    )
    severity = THRESHOLDS.classify(judgement.value)
    print(
        f"\n{item.id}: {judgement.value:.2f} ({severity}), confidence "
        f"{judgement.confidence:.2f}, ${judgement.cost.usd:.4f}"
    )
    return severity


@pytest.mark.parametrize("make", SCORERS)
async def test_a_routine_double_sale_is_severe(make: Callable[[], Scorer]) -> None:
    assert await impact(make(), DOUBLE_SALE) in {Severity.MAJOR, Severity.BLOCKER}


@pytest.mark.parametrize("make", SCORERS)
async def test_a_typo_is_minor(make: Callable[[], Scorer]) -> None:
    assert await impact(make(), TYPO) is Severity.MINOR


@pytest.mark.parametrize("make", SCORERS)
async def test_a_typo_is_not_confirmed_as_a_blocker(make: Callable[[], Scorer]) -> None:
    answer = await make().yes_no(prompts.scored(TYPO, CHARTER), prompts.IS_BLOCKER)

    print(f"\nIs OBJ-2 a blocker? {answer.value}, confidence {answer.confidence:.2f}")
    assert answer.value is False
