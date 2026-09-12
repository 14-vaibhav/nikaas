"""Step 3 - act on the chosen (gap, route), then INCORPORATE (diagram: the
routes fanning out of DECIDE, and 'new gaps can appear right here').

`act()` runs the route and emits its trace event(s); it does not mutate
state. `incorporate()` folds the result back into state - and that is
where new gaps are born (a fresh SID that names an addendum, a violation
that becomes an exclusion). The loop then goes back to OBSERVE.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from agent.allocator import allocate_two
from agent.contracts import (
    Abstention,
    Constraint,
    Doubt,
    Gap,
    Route,
    TwoPlans,
    Violation,
)
from agent.merit import top_merit_fund_ids
from agent.resolver import resolve
from agent.selfcheck import run as run_self_check
from agent.verifier import verify

_MAX_SELF_CHECK_ROUNDS = 2


@dataclass
class ActResult:
    route: Route
    gap: Gap
    ok: bool = True
    detail: str = ""
    constraints: list[Constraint] = field(default_factory=list)
    content_hash: str = ""
    plans: TwoPlans | None = None
    doubts: list[Doubt] = field(default_factory=list)
    resolved_group: tuple[str, str] | None = None
    ambiguous: bool = False
    abstain_fund: str | None = None
    abstain_reason: str = ""
    sell_date_hint: str | None = None


# --- act -------------------------------------------------------------------


def act(route: Route, gap: Gap, state, hunter, budget, trace) -> ActResult:
    budget.spend(steps=1)
    fn = {
        Route.HUNT: _hunt,
        Route.REFETCH: _refetch,
        Route.USE_CACHE: _use_cache,
        Route.RESOLVE: _resolve,
        Route.ALLOCATE: _allocate,
        Route.SELF_CHECK: _self_check,
        Route.ABSTAIN: _abstain,
    }[route]
    return fn(gap, state, hunter, budget, trace)


def _hunt(gap: Gap, state, hunter, budget, trace, *, force: bool = False) -> ActResult:
    fund_id = gap.fund_id
    kinds = [gap.constraint_kind] if gap.constraint_kind else list(_required_kinds())
    fund_name = _fund_name(state, fund_id)
    outcome = hunter.hunt(fund_id, fund_name, kinds, force=force)
    budget.spend(tokens=outcome.tokens_spent)
    trace.emit(
        "hunt",
        f"Hunt {fund_id} for {', '.join(kinds)}"
        + ("" if outcome.ok else " - nothing usable found"),
        tool=type(hunter).__name__,
        input={"fund": fund_id, "kinds": kinds, "force": force},
        output={"constraints": len(outcome.constraints), "hash": outcome.content_hash[:12]},
        verdict="ok" if outcome.ok else "retry",
        reason=outcome.detail or None,
        budget=budget.as_dict(),
    )
    return ActResult(
        route=Route.HUNT, gap=gap, ok=outcome.ok, detail=outcome.detail,
        constraints=outcome.constraints, content_hash=outcome.content_hash,
    )


def _refetch(gap: Gap, state, hunter, budget, trace) -> ActResult:
    res = _hunt(gap, state, hunter, budget, trace, force=True)
    res.route = Route.REFETCH
    trace.emit(
        "cache", f"Re-fetched {gap.fund_id}/{gap.constraint_kind} (cache was stale)",
        verdict="ok" if res.ok else "retry", budget=budget.as_dict(),
    )
    return res


def _use_cache(gap: Gap, state, hunter, budget, trace) -> ActResult:
    trace.emit(
        "cache", f"Cache hit for {gap.fund_id}/{gap.constraint_kind} - still fresh",
        verdict="ok", budget=budget.as_dict(),
    )
    return ActResult(route=Route.USE_CACHE, gap=gap, ok=True)


def _resolve(gap: Gap, state, hunter, budget, trace) -> ActResult:
    fund_id, kind = gap.fund_id, gap.constraint_kind
    group = state.evidence.group(fund_id, kind)
    resolved, ambiguities = resolve(group)
    ambiguous = bool(ambiguities)
    trace.emit(
        "resolve",
        f"Resolved supersession for {fund_id}/{kind} across {len(group)} constraints",
        output={"ambiguous": ambiguous},
        verdict="violation" if ambiguous else "ok",
        reason=(f"contradictory {kind} constraints share an effective date" if ambiguous else None),
        budget=budget.as_dict(),
    )
    return ActResult(
        route=Route.RESOLVE, gap=gap, ok=True, constraints=resolved,
        resolved_group=(fund_id, kind), ambiguous=ambiguous,
        abstain_fund=fund_id if ambiguous else None,
        abstain_reason=(
            f"contradictory {kind} constraints share an effective date - abstaining rather than picking"
            if ambiguous else ""
        ),
    )


def _allocate(gap: Gap, state, hunter, budget, trace) -> ActResult:
    sell_hint = None
    if gap.kind == "self_check_doubt" and gap.reason.startswith("cheaper_date"):
        sell_hint = _best_frontier_date(state)

    constraints = state.evidence.constraints()
    ids = state.candidate_ids()
    merit_keep = state.merit_keep()
    two = allocate_two(
        state.goal, state.holdings, constraints, state.navs, ids, merit_keep,
        sell_date=date.fromisoformat(sell_hint) if sell_hint else None,
    )
    # A fund excluded at Step 0 is a held scheme the allocator never even
    # considered, so it can't appear in `abstained` on its own - without
    # this the verifier's SILENT_DROP check would mistake that boundary
    # for a fund the loop silently forgot.
    for plan in (two.cheapest, two.merit_preserving):
        _merge_step0_exclusions(plan, state.abstained)

    cheap_v = verify(two.cheapest, constraints, state.holdings)
    merit_v = verify(two.merit_preserving, constraints, state.holdings)
    two.cheapest.violations, two.cheapest.verdict = cheap_v, ("rejected" if cheap_v else "verified")
    two.merit_preserving.violations, two.merit_preserving.verdict = (
        merit_v, ("rejected" if merit_v else "verified")
    )

    trace.emit(
        "allocate",
        f"Built two plans - cheapest: {len(two.cheapest.legs)} leg(s) net "
        f"Rs.{two.cheapest.totals.net:.0f}; merit-preserving: {len(two.merit_preserving.legs)} "
        f"leg(s) net Rs.{two.merit_preserving.totals.net:.0f}"
        + (" (identical - no merit split)" if two.identical else ""),
        output={
            "keep": sorted(merit_keep),
            "cheapest_exit_load": two.cheapest.totals.exit_load,
            "merit_exit_load": two.merit_preserving.totals.exit_load,
            "sell_date_hint": sell_hint,
        },
        verdict="ok", budget=budget.as_dict(),
    )
    trace.emit(
        "verify",
        f"Verifier: cheapest {len(cheap_v)} violation(s), merit-preserving {len(merit_v)} violation(s)",
        output={"cheapest": [v.code for v in cheap_v], "merit_preserving": [v.code for v in merit_v]},
        verdict="violation" if (cheap_v or merit_v) else "ok",
        budget=budget.as_dict(),
    )
    return ActResult(route=Route.ALLOCATE, gap=gap, ok=True, plans=two, sell_date_hint=sell_hint)


def _self_check(gap: Gap, state, hunter, budget, trace) -> ActResult:
    doubts = run_self_check(state)
    if state.self_check_rounds + 1 >= _MAX_SELF_CHECK_ROUNDS:
        # Final round: report but do not spawn more gaps - the loop must end.
        for d in doubts:
            trace.emit("self_check", f"[accepted, round cap] {d.label}: {d.detail}",
                       verdict="ok", budget=budget.as_dict())
        doubts = []
    else:
        for d in doubts:
            trace.emit("self_check", f"{d.label}: {d.detail}", verdict="violation",
                       reason="doubt - going back to test it", budget=budget.as_dict())
    if not doubts:
        trace.emit("self_check", "Adversarial pass raised nothing to test", verdict="ok",
                   budget=budget.as_dict())
    return ActResult(route=Route.SELF_CHECK, gap=gap, ok=True, doubts=doubts)


def _abstain(gap: Gap, state, hunter, budget, trace) -> ActResult:
    reason = _abstain_reason(gap)
    trace.emit(
        "abstain",
        f"{gap.fund_id or 'goal'}: {reason}",
        verdict="abstained", reason=reason, budget=budget.as_dict(),
    )
    return ActResult(
        route=Route.ABSTAIN, gap=gap, ok=True,
        abstain_fund=gap.fund_id, abstain_reason=reason,
    )


# --- incorporate ---------------------------------------------------------


def incorporate(result: ActResult, state, trace) -> None:
    """Fold an ActResult into state. New gaps are not returned - OBSERVE
    recomputes them from the mutated state on the next pass."""
    route, gap = result.route, result.gap

    if route in (Route.HUNT, Route.REFETCH):
        if result.ok and result.constraints:
            by_kind: dict[str, list[Constraint]] = {}
            for c in result.constraints:
                by_kind.setdefault(c.kind, []).append(c)
            for kind, cs in by_kind.items():
                state.evidence.replace_fund_kind(gap.fund_id, kind, cs, result.content_hash)
            state.resolved_kinds.discard((gap.fund_id, gap.constraint_kind))
            state.invalidate_plans("new evidence hunted")
            state.clear_doubt(gap)
        return

    if route == Route.RESOLVE:
        state.evidence.apply_supersession(result.constraints)
        state.resolved_kinds.add(result.resolved_group)
        if result.ambiguous and result.abstain_fund:
            state.abstain(result.abstain_fund, result.abstain_reason)
            state.invalidate_plans("ambiguity abstention")
        return

    if route == Route.ALLOCATE:
        _apply_repair(gap, state, trace)
        state.plans = result.plans
        state.self_checked = False
        state.clear_doubt(gap)
        return

    if route == Route.SELF_CHECK:
        state.self_checked = True
        state.self_check_rounds += 1
        state.doubts = result.doubts
        return

    if route == Route.ABSTAIN:
        if result.abstain_fund:
            state.abstain(result.abstain_fund, result.abstain_reason)
            if gap.kind in ("missing_evidence", "stale_evidence", "plan_rejected"):
                state.invalidate_plans("fund abstained")
            else:
                _patch_plan_abstention_reason(state, result.abstain_fund, result.abstain_reason)
        else:
            state.stopped_reason = result.abstain_reason or "abstained without a recoverable path"
        state.clear_doubt(gap)
        return

    if route == Route.USE_CACHE:
        return


def _apply_repair(gap: Gap, state, trace) -> None:
    """The 'violations into sub-goals' arrow: mutate state so the coming
    re-allocation can't reproduce the violation."""
    if gap.kind != "plan_rejected" or gap.violation is None:
        return
    v: Violation = gap.violation
    if v.code in ("LOCK_IN_BREACH", "FIFO_MERGE") and gap.fund_id:
        state.excluded.add(gap.fund_id)
        state.abstain(gap.fund_id, v.detail)
        trace.emit("repair", f"Excluding {gap.fund_id} from allocation: {v.code}",
                   reason=v.detail, verdict="ok")
    elif v.code in ("NO_PROVENANCE", "STALE_SOURCE", "SUPERSEDED"):
        cid = _constraint_id_from_detail(v.detail)
        if cid:
            state.evidence.drop(cid)
            trace.emit("repair", f"Dropping tainted constraint {cid}: {v.code}",
                       reason=v.detail, verdict="ok")
    elif v.code == "SHORTFALL":
        state.widened = True
        trace.emit("repair", "Shortfall - no wider candidate set available in this mode",
                   reason=v.detail, verdict="abstained")


def _merge_step0_exclusions(plan, extra: dict[str, str]) -> None:
    have = {a.scheme_id for a in plan.abstained}
    for fund_id, reason in extra.items():
        if fund_id not in have:
            plan.abstained.append(Abstention(scheme_id=fund_id, reason=reason))


def _patch_plan_abstention_reason(state, fund_id: str, reason: str) -> None:
    if state.plans is None:
        return
    for plan in (state.plans.cheapest, state.plans.merit_preserving):
        for a in plan.abstained:
            if a.scheme_id == fund_id and not a.reason:
                a.reason = reason
        if fund_id not in {a.scheme_id for a in plan.abstained}:
            plan.abstained.append(Abstention(scheme_id=fund_id, reason=reason))


# --- helpers -----------------------------------------------------------


def _required_kinds() -> tuple[str, ...]:
    from agent.observe import REQUIRED_KINDS
    return REQUIRED_KINDS


def _fund_name(state, fund_id: str | None) -> str:
    for c in state.candidates:
        if c.fund_id == fund_id:
            return c.fund_name or fund_id or ""
    return fund_id or ""


def _abstain_reason(gap: Gap) -> str:
    if gap.violation is not None:
        return gap.violation.detail
    if gap.kind == "self_check_doubt" and gap.reason.startswith("unexplained_abstention"):
        return "retrieval did not return a source; recorded as abstained, not decided"
    if gap.attempts:
        return f"{gap.reason} - {gap.attempts} attempt(s) exhausted, abstaining rather than guessing"
    return gap.reason or "abstained"


def _best_frontier_date(state) -> str | None:
    if state.plans is None or not state.plans.frontier:
        return None
    best = min(state.plans.frontier, key=lambda f: f.total_cost)
    return best.date


def _constraint_id_from_detail(detail: str) -> str | None:
    import re
    m = re.search(r"constraint (\S+)", detail)
    return m.group(1) if m else None
