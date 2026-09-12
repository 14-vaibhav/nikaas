"""Step 7 - SELF-CHECK (diagram: 'did I skip a scheme because retrieval
failed - and then treat that as a decision?').

Adversarial pass over the two plans. It does not fix anything; it returns
`Doubt`s, and the loop turns each doubt into a `Gap` and goes back to
OBSERVE to test it. That's the red 'doubts -> go test them' arrow.
"""

from __future__ import annotations

from agent.contracts import Doubt


def run(state) -> list[Doubt]:
    doubts: list[Doubt] = []
    if state.plans is None:
        return doubts

    plans = [state.plans.cheapest, state.plans.merit_preserving]
    by_id = {c.id: c for c in state.evidence.constraints()}

    # 1. Was a cheaper sell date dismissed during the first allocation pass?
    for plan in plans:
        if not plan.frontier:
            continue
        best = min(plan.frontier, key=lambda f: f.total_cost)
        if best.total_cost + 1.0 < plan.totals.exit_load:
            doubts.append(Doubt(
                label="cheaper_date",
                detail=(
                    f"{plan.kind}: frontier shows Rs.{best.total_cost:.2f} on {best.date}, "
                    f"plan pays Rs.{plan.totals.exit_load:.2f}"
                ),
            ))

    # 2. Did a plan apply a constraint that isn't confidence='verified'?
    for plan in plans:
        for leg in plan.legs:
            for cid in leg.constraints_applied:
                c = by_id.get(cid)
                if c is not None and c.confidence != "verified":
                    doubts.append(Doubt(
                        label="unverified_constraint",
                        fund_id=leg.scheme_id,
                        constraint_kind=c.kind,
                        detail=f"{cid} applied at confidence={c.confidence}",
                    ))

    # 3. An abstention with no reason is a retrieval failure dressed as a decision.
    for plan in plans:
        for a in plan.abstained:
            if not a.reason:
                doubts.append(Doubt(
                    label="unexplained_abstention",
                    fund_id=a.scheme_id,
                    detail="abstained with no recorded reason",
                ))

    # 4. A fund in scope with no evidence at all that nothing abstained on.
    for cand in state.candidates:
        if state.evidence.for_fund(cand.fund_id):
            continue
        covered = any(a.scheme_id == cand.fund_id for p in plans for a in p.abstained)
        if not covered:
            doubts.append(Doubt(
                label="silent_skip",
                fund_id=cand.fund_id,
                detail="in scope, no evidence retrieved, not abstained",
            ))

    return _dedupe(doubts)


def _dedupe(doubts: list[Doubt]) -> list[Doubt]:
    seen: set[tuple] = set()
    out: list[Doubt] = []
    for d in doubts:
        k = (d.label, d.fund_id, d.constraint_kind, d.detail)
        if k not in seen:
            seen.add(k)
            out.append(d)
    return out
