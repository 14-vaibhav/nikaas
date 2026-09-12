"""Cross-scheme allocator (diagram: step 5 ALLOCATE - 'merit first, then cost',
and 'TWO PLANS, NOT ONE').

Greedy over scheme-level marginal cost, FIFO-constrained. FIFO is applied,
never searched - within a scheme, units come out oldest-first; the only
decision here is how much to sell from each scheme. No optimiser: greedy
is explainable.

`allocate_two` produces the diagram's pair:
  * cheapest        - lowest exit-load path, may sell a fund worth keeping
  * merit_preserving - the same greedy, but with the "worth keeping" funds
                       held back from the sale pool unless the goal can't
                       otherwise be met
plus the 30-day cost frontier both plans carry.
"""

from __future__ import annotations

from datetime import date, timedelta

from agent.contracts import (
    Abstention,
    Constraint,
    FrontierPoint,
    Goal,
    Holdings,
    Plan,
    PlanChoice,
    PlanLeg,
    Totals,
    TwoPlans,
)
from agent.verifier import (
    ConstraintIndex,
    DEFAULT_LTCG_EXEMPTION,
    DEFAULT_LTCG_RATE,
    DEFAULT_LTCG_THRESHOLD_DAYS,
    DEFAULT_STCG_RATE,
    first_tax_rule,
)


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


def _marginal_cost_pct(scheme_id: str, sell_date: date, holdings: Holdings, index: ConstraintIndex) -> float:
    exit_load = index.active_for(scheme_id, "exit_load")
    if exit_load is None:
        return 0.0
    lots = [l for l in holdings.lots if l.scheme_id == scheme_id]
    if not lots:
        return 0.0
    oldest = min(lots, key=lambda l: l.purchase_date)
    holding_days = (sell_date - _parse_date(oldest.purchase_date)).days
    window_days = exit_load.value.get("window_days", 0)
    if holding_days < window_days:
        return exit_load.value.get("pct", 0.0)
    return 0.0


def _build_leg(
    scheme_id: str, target_units_value: float, sell_date: date,
    holdings: Holdings, index: ConstraintIndex, nav: float,
) -> tuple[PlanLeg | None, float, float, float]:
    lots = sorted(
        (l for l in holdings.lots if l.scheme_id == scheme_id),
        key=lambda l: l.purchase_date,
    )
    if not lots:
        return None, 0.0, 0.0, 0.0
    folio = lots[0].folio
    folio_lots = [l for l in lots if l.folio == folio]

    exit_load = index.active_for(scheme_id, "exit_load")
    tax_rule = index.active_for(scheme_id, "tax_rule")
    ltcg_threshold_days = (
        tax_rule.value.get("ltcg_threshold_days", DEFAULT_LTCG_THRESHOLD_DAYS) if tax_rule
        else DEFAULT_LTCG_THRESHOLD_DAYS
    )

    units_needed = target_units_value / nav if nav else 0.0
    units_taken = 0.0
    gross = 0.0
    exit_load_total = 0.0
    constraints_applied: list[str] = []
    if exit_load is not None:
        constraints_applied.append(exit_load.id)
    if tax_rule is not None:
        constraints_applied.append(tax_rule.id)

    ltcg_gain = 0.0
    stcg_gain = 0.0
    for lot in folio_lots:
        if units_taken >= units_needed - 1e-9:
            break
        take = min(lot.units, units_needed - units_taken)
        units_taken += take
        gross += take * nav
        purchase_date = _parse_date(lot.purchase_date)
        holding_days = (sell_date - purchase_date).days
        if exit_load is not None:
            window_days = exit_load.value.get("window_days", 0)
            if holding_days < window_days:
                pct = exit_load.value.get("pct", 0.0)
                exit_load_total += take * nav * (pct / 100.0)
        gain = (nav - lot.purchase_nav) * take
        if holding_days >= ltcg_threshold_days:
            ltcg_gain += gain
        else:
            stcg_gain += gain

    if units_taken <= 1e-9:
        return None, 0.0, 0.0, 0.0

    net = gross - exit_load_total  # tax applied portfolio-wide by the caller
    leg = PlanLeg(
        scheme_id=scheme_id, folio=folio, units=round(units_taken, 4),
        gross=round(gross, 2), exit_load=round(exit_load_total, 2),
        tax=0.0, net=round(net, 2), sell_date=sell_date.isoformat(),
        constraints_applied=constraints_applied,
    )
    return leg, gross - exit_load_total, ltcg_gain, stcg_gain


def _tax_rate_inputs(constraints: list[Constraint]) -> tuple[float, float, float]:
    tax_rule = first_tax_rule(constraints)
    if tax_rule is None:
        return DEFAULT_LTCG_RATE, DEFAULT_STCG_RATE, DEFAULT_LTCG_EXEMPTION
    return (
        tax_rule.value.get("ltcg_rate", DEFAULT_LTCG_RATE),
        tax_rule.value.get("stcg_rate", DEFAULT_STCG_RATE),
        tax_rule.value.get("ltcg_exemption", DEFAULT_LTCG_EXEMPTION),
    )


def _greedy_pass(
    amount_needed: float, ranked: list[str], holdings: Holdings, index: ConstraintIndex,
    navs: dict[str, float], sell_date: date,
) -> tuple[list[PlanLeg], list[Abstention], float, float, float]:
    legs: list[PlanLeg] = []
    abstained: list[Abstention] = []
    raised = 0.0
    total_ltcg = 0.0
    total_stcg = 0.0

    for scheme_id in ranked:
        if raised >= amount_needed - 1e-6:
            break
        nav = navs.get(scheme_id)
        if nav is None:
            abstained.append(Abstention(scheme_id=scheme_id, reason="no NAV available"))
            continue
        need = amount_needed - raised
        leg, value, ltcg_gain, stcg_gain = _build_leg(scheme_id, need, sell_date, holdings, index, nav)
        if leg is None:
            abstained.append(Abstention(scheme_id=scheme_id, reason="no usable holdings found"))
            continue
        legs.append(leg)
        raised += value
        total_ltcg += ltcg_gain
        total_stcg += stcg_gain

    return legs, abstained, raised, total_ltcg, total_stcg


def allocate(
    goal: Goal, holdings: Holdings, constraints: list[Constraint],
    navs: dict[str, float], candidate_schemes: list[str],
    sell_date: date | None = None, kind: PlanChoice = "cheapest",
) -> Plan:
    """One greedy plan: rank candidates cheapest first, take from each
    until the target is met or candidates are exhausted. Iterates on tax
    the same way the verifier independently will, so an honest plan's
    totals agree with the recomputation."""
    index = ConstraintIndex(constraints)
    sell_date = sell_date or _parse_date(goal.by_date) - timedelta(days=3)
    ltcg_rate, stcg_rate, exemption = _tax_rate_inputs(constraints)

    ranked = sorted(
        candidate_schemes,
        key=lambda s: _marginal_cost_pct(s, sell_date, holdings, index),
    )

    target = goal.amount
    legs, abstained, raised, total_ltcg, total_stcg = [], [], 0.0, 0.0, 0.0
    for _ in range(6):
        legs, abstained, raised, total_ltcg, total_stcg = _greedy_pass(
            target, ranked, holdings, index, navs, sell_date
        )
        taxable_ltcg = max(0.0, total_ltcg - exemption)
        tax = taxable_ltcg * ltcg_rate + total_stcg * stcg_rate
        net = raised - tax
        shortfall = goal.amount - net
        if shortfall <= 1.0:
            break
        target += shortfall

    taxable_ltcg = max(0.0, total_ltcg - exemption)
    tax = round(taxable_ltcg * ltcg_rate + total_stcg * stcg_rate, 2)

    for scheme_id in candidate_schemes:
        if scheme_id not in {l.scheme_id for l in legs} and scheme_id not in {a.scheme_id for a in abstained}:
            abstained.append(Abstention(scheme_id=scheme_id, reason="not needed to reach target"))

    gross = round(sum(l.gross for l in legs), 2)
    exit_load_total = round(sum(l.exit_load for l in legs), 2)
    totals = Totals(
        gross=gross, exit_load=exit_load_total, tax=tax,
        net=round(gross - exit_load_total - tax, 2),
    )

    return Plan(
        plan_id=f"p_{kind}_{sell_date.isoformat()}_{len(legs)}",
        goal=goal, legs=legs, totals=totals, abstained=abstained,
        verdict="rejected", violations=[], kind=kind,
    )


def allocate_two(
    goal: Goal, holdings: Holdings, constraints: list[Constraint],
    navs: dict[str, float], candidate_schemes: list[str], merit_keep: set[str] | None = None,
    sell_date: date | None = None,
) -> TwoPlans:
    """The diagram's pair. `merit_keep` is the set of funds 'worth keeping'
    (from `agent/merit.py` or Mode B); the merit-preserving plan holds them
    back unless the goal is otherwise unreachable."""
    merit_keep = merit_keep or set()

    cheapest = allocate(goal, holdings, constraints, navs, candidate_schemes, sell_date, kind="cheapest")

    pool = [s for s in candidate_schemes if s not in merit_keep]
    if not pool:
        pool = list(candidate_schemes)
    merit_preserving = allocate(goal, holdings, constraints, navs, pool, sell_date, kind="merit_preserving")
    held_back = [s for s in candidate_schemes if s not in pool]
    if merit_preserving.totals.net + 1.0 < goal.amount and set(pool) != set(candidate_schemes):
        # Holding the merit funds back can't reach the target - a shortfall
        # is not a "preserved" plan, so fall back to the full set.
        merit_preserving = allocate(
            goal, holdings, constraints, navs, candidate_schemes, sell_date, kind="merit_preserving"
        )
    else:
        # Record the deliberately-untouched funds as abstentions with a
        # reason, so the verifier's SILENT_DROP check passes and the human
        # sees exactly what was kept and why.
        in_plan = {l.scheme_id for l in merit_preserving.legs}
        listed = {a.scheme_id for a in merit_preserving.abstained}
        for fund_id in held_back:
            if fund_id not in in_plan and fund_id not in listed:
                merit_preserving.abstained.append(Abstention(
                    scheme_id=fund_id,
                    reason="held back to preserve merit - selling it is not required to reach the target",
                ))

    frontier = frontier_sweep(goal, holdings, constraints, navs, candidate_schemes)
    fpts = [FrontierPoint(**p) for p in frontier]
    cheapest.frontier = fpts
    merit_preserving.frontier = list(fpts)

    identical = not merit_keep or _same_legs(cheapest, merit_preserving)
    return TwoPlans(cheapest=cheapest, merit_preserving=merit_preserving, frontier=fpts, identical=identical)


def _same_legs(a: Plan, b: Plan) -> bool:
    key = lambda p: sorted((l.scheme_id, l.folio, round(l.units, 2)) for l in p.legs)
    return key(a) == key(b)


def frontier_sweep(
    goal: Goal, holdings: Holdings, constraints: list[Constraint],
    navs: dict[str, float], candidate_schemes: list[str], days: int = 30,
) -> list[dict]:
    """Recompute total exit-load cost for each candidate sell date over the
    `days`-wide decision window that ends on the goal date - the curve the
    frontier chart is built from. When the goal is more than `days` out the
    window is the run-up to the deadline (where the load-window and
    LTCG-crossing inflections actually cluster), not an arbitrary 30 days
    starting today; when the goal is nearer, it starts today as before."""
    goal_date = _parse_date(goal.by_date)
    start = max(date.today(), goal_date - timedelta(days=days - 1))
    points = []
    for offset in range(days):
        sell_date = start + timedelta(days=offset)
        if sell_date > goal_date:
            break
        plan = allocate(goal, holdings, constraints, navs, candidate_schemes, sell_date=sell_date)
        points.append({"date": sell_date.isoformat(), "total_cost": plan.totals.exit_load})
    return points
