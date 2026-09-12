"""THE WATCHER (diagram: standing goal 'keep plans valid').

No human prompts it. It holds a baseline - the NAVs and document hashes a
verified plan was built on - and, when something moves (a NAV, the
calendar crossing the target date, a cited document's hash), it re-enters
the loop with `trigger=WATCHER` and the previous plans as `prior`, then
reports whether the plans actually changed. Most days: nothing moved,
nothing to do - which is the correct outcome.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from agent.contracts import Goal, Holdings, LoopResult, Mode, Trigger, TwoPlans
from agent.evidence import EvidenceStore
from agent.loop import run as run_loop
from agent.trace import TraceLog

NAV_EPSILON = 0.005  # 0.5% move is worth a re-check


@dataclass
class WatchReport:
    run_id: str
    triggered: bool
    reasons: list[str] = field(default_factory=list)
    plans_changed: bool = False
    result: LoopResult | None = None


def snapshot_hashes(evidence: EvidenceStore) -> dict[str, str]:
    return {e.constraint.id: e.content_hash for e in evidence.all()}


def detect_triggers(
    baseline_navs: dict[str, float],
    current_navs: dict[str, float],
    baseline_hashes: dict[str, str],
    current_hashes: dict[str, str],
    by_date: str,
    today: date | None = None,
) -> list[str]:
    today = today or date.today()
    reasons: list[str] = []

    if today >= date.fromisoformat(by_date):
        reasons.append(f"calendar reached the target date {by_date}")

    for fund, nav in current_navs.items():
        base = baseline_navs.get(fund)
        if base and abs(nav - base) / base > NAV_EPSILON:
            reasons.append(f"NAV moved for {fund}: {base} -> {nav}")

    for cid, h in current_hashes.items():
        base = baseline_hashes.get(cid)
        if base is not None and h and h != base:
            reasons.append(f"cited document changed under constraint {cid}")

    return reasons


def check_plan(
    *,
    run_id: str,
    goal: Goal,
    mode: Mode,
    fund_ids: list[str],
    holdings: Holdings,
    current_navs: dict[str, float],
    evidence: EvidenceStore,
    hunter,
    baseline_navs: dict[str, float],
    baseline_hashes: dict[str, str],
    prior: TwoPlans | None,
    today: date | None = None,
) -> WatchReport:
    current_hashes = snapshot_hashes(evidence)
    reasons = detect_triggers(
        baseline_navs, current_navs, baseline_hashes, current_hashes, goal.by_date, today
    )
    if not reasons:
        return WatchReport(run_id=run_id, triggered=False)

    trace = TraceLog()
    trace.emit("watch", "Baseline moved - re-entering the loop", output={"reasons": reasons})
    result = run_loop(
        goal, mode, fund_ids, holdings, current_navs,
        hunter=hunter, evidence=evidence, trace=trace,
        trigger=Trigger.WATCHER, prior=prior,
    )
    changed = _plans_differ(prior, result.plans)
    trace.emit(
        "watch",
        "Plans changed - needs re-approval" if changed else "Plans still valid - no action",
        verdict="violation" if changed else "ok",
    )
    return WatchReport(
        run_id=run_id, triggered=True, reasons=reasons, plans_changed=changed, result=result
    )


def _plans_differ(a: TwoPlans | None, b: TwoPlans | None) -> bool:
    if a is None or b is None:
        return a is not b
    return _legs(a) != _legs(b)


def _legs(tp: TwoPlans):
    return {
        kind: sorted((l.scheme_id, l.folio, round(l.units, 2), l.sell_date) for l in plan.legs)
        for kind, plan in (("cheapest", tp.cheapest), ("merit_preserving", tp.merit_preserving))
    }
