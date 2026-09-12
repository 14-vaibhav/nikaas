"""Step 1 - OBSERVE (diagram: 'What do I know? What is missing?' - 'gap list
rebuilt every pass').

The only place gaps are computed. It reads current state and returns a
fresh, ordered list every call. DECIDE then picks one gap and a route.
Order here is priority: evidence before conflicts before allocation
before verification before self-check.
"""

from __future__ import annotations

from agent.contracts import Gap

REQUIRED_KINDS = ("exit_load", "tax_rule")


def observe(state) -> list[Gap]:
    gaps: list[Gap] = []

    active_ids = state.candidate_ids()

    # 1. Missing governing evidence for a candidate.
    for fund_id in active_ids:
        for kind in REQUIRED_KINDS:
            if not state.evidence.has(fund_id, kind):
                gaps.append(_track(state, Gap(
                    kind="missing_evidence", fund_id=fund_id, constraint_kind=kind,
                    reason=f"no {kind} constraint on file for {fund_id}",
                )))

    # 2. Stale evidence - past its freshness window.
    seen_stale: set[tuple] = set()
    for ev in state.evidence.stale_entries():
        c = ev.constraint
        if c.scheme_id not in active_ids or (c.scheme_id, c.kind) in seen_stale:
            continue
        seen_stale.add((c.scheme_id, c.kind))
        gaps.append(_track(state, Gap(
            kind="stale_evidence", fund_id=c.scheme_id, constraint_kind=c.kind,
            reason=f"{c.kind} for {c.scheme_id} retrieved {c.source.retrieved_at[:10]}, past freshness",
        )))

    # 3. Unresolved supersession - two active constraints for one (fund, kind).
    for fund_id in active_ids:
        for kind in _kinds_present(state, fund_id):
            if (fund_id, kind) in state.resolved_kinds:
                continue
            if state.evidence.active_count(fund_id, kind) >= 2:
                gaps.append(_track(state, Gap(
                    kind="unresolved_conflict", fund_id=fund_id, constraint_kind=kind,
                    reason=f"{state.evidence.active_count(fund_id, kind)} active {kind} constraints for {fund_id}",
                )))

    if gaps:
        return gaps

    # 4. Evidence is complete - do we have plans?
    if state.plans is None:
        return [_track(state, Gap(kind="no_allocation", reason="evidence complete, no plans built yet"))]

    # 5. Verifier violations become sub-goals.
    for key in ("cheapest", "merit_preserving"):
        plan = state.plans.get(key)
        for v in plan.violations:
            gaps.append(_track(state, Gap(
                kind="plan_rejected", fund_id=_leg_fund(plan, v), constraint_kind=None,
                reason=f"{key}: {v.code} - {v.detail}", violation=v, plan_key=key,
            )))
    if gaps:
        return gaps

    # 6. Plans verify but the adversarial pass hasn't run.
    if not state.self_checked:
        return [_track(state, Gap(kind="not_self_checked", reason="plans verified, self-check pending"))]

    # 7. Self-check doubts.
    for d in state.doubts:
        gaps.append(_track(state, Gap(
            kind="self_check_doubt", fund_id=d.fund_id, constraint_kind=d.constraint_kind,
            reason=f"{d.label}: {d.detail}",
        )))

    return gaps


def _track(state, gap: Gap) -> Gap:
    gap.attempts = state.gap_attempts.get(gap.key(), 0)
    return gap


def _kinds_present(state, fund_id: str) -> set[str]:
    return {e.constraint.kind for e in state.evidence.for_fund(fund_id)}


def _leg_fund(plan, violation) -> str | None:
    if violation.leg_index is not None and 0 <= violation.leg_index < len(plan.legs):
        return plan.legs[violation.leg_index].scheme_id
    return None
