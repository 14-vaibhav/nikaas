"""Step 2 - DECIDE: which gap, which route? (diagram: '* dynamic action
selection').

Given the gap list from OBSERVE, pick one gap and the route to close it.
Routes are roughly cost-ordered - USE_CACHE (~0) < REFETCH < HUNT - and
the choice depends on what's already in the evidence store and how much
budget is left. Two failed tries on the same gap => ABSTAIN (never
guess). Budget nearly spent and still no plan => STOP.
"""

from __future__ import annotations

from agent.contracts import Gap, Route

MAX_GAP_ATTEMPTS = 2  # diagram: "2 tries failed"

_RETRIABLE = {"missing_evidence", "stale_evidence", "self_check_doubt"}


def decide(gaps: list[Gap], state, budget) -> tuple[Gap, Route]:
    gap = _prioritize(gaps, budget)
    return gap, _route_for(gap, state, budget)


def _prioritize(gaps: list[Gap], budget) -> Gap:
    if not budget.low():
        return gaps[0]  # OBSERVE already ordered by priority
    # Budget is low: prefer a gap we can close without an expensive hunt.
    cheap_first = sorted(gaps, key=lambda g: 0 if g.kind in _CHEAP_KINDS else 1)
    return cheap_first[0]


_CHEAP_KINDS = {"no_allocation", "unresolved_conflict", "not_self_checked", "plan_rejected"}


def _route_for(gap: Gap, state, budget) -> Route:
    if gap.kind in _RETRIABLE and gap.attempts >= MAX_GAP_ATTEMPTS:
        return Route.ABSTAIN

    if gap.kind == "missing_evidence":
        if budget.low():
            return Route.STOP if state.plans is None else Route.ABSTAIN
        if state.evidence.known_url(gap.fund_id, gap.constraint_kind):
            return Route.REFETCH
        return Route.HUNT

    if gap.kind == "stale_evidence":
        if budget.low():
            return Route.ABSTAIN
        if state.evidence.known_url(gap.fund_id, gap.constraint_kind):
            return Route.REFETCH
        return Route.HUNT

    if gap.kind == "unresolved_conflict":
        return Route.RESOLVE

    if gap.kind == "no_allocation":
        return Route.ALLOCATE

    if gap.kind == "plan_rejected":
        code = gap.violation.code if gap.violation else ""
        if gap.attempts >= MAX_GAP_ATTEMPTS:
            return Route.ABSTAIN if gap.fund_id else Route.STOP
        if code in ("NO_PROVENANCE", "STALE_SOURCE", "SUPERSEDED"):
            return Route.HUNT if not budget.low() else Route.ABSTAIN
        # LOCK_IN_BREACH, FIFO_MERGE, SHORTFALL, SILENT_DROP - a re-allocation
        # after act() applies the repair mutation.
        return Route.ALLOCATE

    if gap.kind == "not_self_checked":
        return Route.SELF_CHECK

    if gap.kind == "self_check_doubt":
        label = gap.reason.split(":", 1)[0].strip()
        if label in ("unverified_constraint", "silent_skip"):
            return Route.HUNT if not budget.low() else Route.ABSTAIN
        if label == "cheaper_date":
            return Route.ALLOCATE
        # unexplained_abstention - record the reason and move on
        return Route.ABSTAIN

    return Route.STOP
