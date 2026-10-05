"""`run.json`: the whole run as data, for tools and for later runs to read.

`format` is the version of this layout. It goes up only when a field is renamed
or removed, or changes meaning; new fields can appear at any time.

`costs` and `total_cost` count the steps that finished. `failed_steps_cost` is
what calls in steps that failed spent: steps that were retried, or that stopped
the run before it was carried on. The run spent the two together.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import aiarb
from aiarb.domain.ledger import CodeRef, Event, Objection, Ref
from aiarb.observe.redact import Redactor
from aiarb.ports.calls import Cost

if TYPE_CHECKING:
    from aiarb.domain.ledger import Resolution
    from aiarb.output.report import Report

FORMAT = 1


def render(report: Report, redactor: Redactor | None = None) -> str:
    redactor = redactor or Redactor()
    outcome = report.outcome
    total = sum(report.costs.values(), Cost())
    explanation = report.explanation
    record: dict[str, Any] = {
        "format": FORMAT,
        "aiarb_version": aiarb.__version__,
        "run_id": report.run_id,
        "status": outcome.status.value,
        "exit_code": outcome.exit_code,
        "feature": report.feature,
        "rounds": report.rounds,
        "summary": explanation.summary if explanation is not None else None,
        "open_items": [item.id for item in outcome.open_items],
        "blockers": [item.id for item in outcome.blockers],
        "downgraded": [item.id for item in outcome.downgraded],
        "objections": [_objection(item, report) for item in report.ledger.values()],
        "plan": report.plan,
        "costs": {role: _cost(cost) for role, cost in report.costs.items()},
        "total_cost": _cost(total),
        "failed_steps_cost": _cost(_less(report.spent, total)),
        "charter": report.charter.model_dump(mode="json"),
    }
    return json.dumps(redactor.data(record), indent=2, ensure_ascii=False) + "\n"


def _objection(item: Objection, report: Report) -> dict[str, Any]:
    explained = report.explained(item)
    return {
        "id": item.id,
        "claim": item.claim,
        "type": item.type.value,
        "severity": item.severity.value,
        "status": item.status.value,
        "open": item.is_open,
        "dropped_from": item.dropped_from.value if item.dropped_from else None,
        "evidence": [_ref(ref) for ref in item.evidence],
        "explanation": (
            explained.model_dump(mode="json", exclude={"id"}) if explained is not None else None
        ),
        "history": [_event(event) for event in item.history],
    }


def _event(event: Event) -> dict[str, Any]:
    return {
        "round": event.round,
        "actor": event.actor.value,
        "status": event.status.value,
        "severity": event.severity.value,
        "note": event.note,
        "refs": [_ref(ref) for ref in event.refs],
        "resolution": _resolution(event.resolution),
    }


def _resolution(resolution: Resolution | None) -> dict[str, Any] | None:
    if resolution is None:
        return None
    return {"kind": resolution.kind.value, "refs": [_ref(ref) for ref in resolution.refs]}


def _ref(ref: Ref) -> dict[str, Any]:
    if isinstance(ref, CodeRef):
        return {
            "kind": "code",
            "path": ref.path,
            "start_line": ref.start_line,
            "end_line": ref.end_line,
        }
    return {"kind": "plan", "section": ref.section, "quote": ref.quote}


def _less(spent: Cost | None, total: Cost) -> Cost:
    """What was spent beyond `total`, or nothing if the run's spending is unknown."""
    if spent is None:
        return Cost()
    return Cost(
        usd=max(spent.usd - total.usd, 0.0),
        input_tokens=max(spent.input_tokens - total.input_tokens, 0),
        output_tokens=max(spent.output_tokens - total.output_tokens, 0),
    )


def _cost(cost: Cost) -> dict[str, Any]:
    return {
        "usd": round(cost.usd, 6),
        "input_tokens": cost.input_tokens,
        "output_tokens": cost.output_tokens,
    }
