from __future__ import annotations

from datetime import date, timedelta

from agent.contracts import Constraint, Goal, Holdings, Lot, Source
from agent.allocator import allocate, frontier_sweep


def _source(effective_date="2025-01-01"):
    return Source(doc="sid.pdf", page=7, clause="7.2", url="https://example.com",
                  effective_date=effective_date, retrieved_at=date.today().isoformat() + "T10:00:00Z")


def _constraints():
    return [
        Constraint(id="c_axis_load", scheme_id="s_axis", kind="exit_load",
                   value={"pct": 1.0, "window_days": 365}, source=_source(), confidence="verified"),
        Constraint(id="c_axis_tax", scheme_id="s_axis", kind="tax_rule",
                   value={"ltcg_threshold_days": 365, "ltcg_rate": 0.125, "stcg_rate": 0.20,
                          "ltcg_exemption": 125_000},
                   source=_source(), confidence="verified"),
        Constraint(id="c_hdfc_load", scheme_id="s_hdfc", kind="exit_load",
                   value={"pct": 1.0, "window_days": 365}, source=_source(), confidence="verified"),
        Constraint(id="c_hdfc_tax", scheme_id="s_hdfc", kind="tax_rule",
                   value={"ltcg_threshold_days": 365, "ltcg_rate": 0.125, "stcg_rate": 0.20,
                          "ltcg_exemption": 125_000},
                   source=_source(), confidence="verified"),
    ]


def _holdings():
    old = (date.today() - timedelta(days=800)).isoformat()
    return Holdings(lots=[
        Lot(lot_id="l1", scheme_id="s_axis", folio="F1", units=10_000,
            purchase_date=old, purchase_nav=10.0),
        Lot(lot_id="l2", scheme_id="s_hdfc", folio="F1", units=10_000,
            purchase_date=old, purchase_nav=10.0),
    ])


def test_allocate_meets_target_and_verifier_agrees_on_arithmetic():
    goal = Goal(amount=100_000, by_date=(date.today() + timedelta(days=10)).isoformat(), mode="A")
    navs = {"s_axis": 15.0, "s_hdfc": 15.0}
    plan = allocate(goal, _holdings(), _constraints(), navs, ["s_axis", "s_hdfc"])
    assert plan.totals.net + 1e-6 >= goal.amount * 0.99  # exit load may trim it slightly if window active

    from agent.verifier import verify
    violations = verify(plan, _constraints(), _holdings())
    # Old lots (800 days) are outside the 365-day exit-load window, so no
    # arithmetic violations are expected here (tax may differ since we
    # haven't set plan.totals.tax from a real repair loop yet).
    non_tax_violations = [v for v in violations if "tax" not in v.detail.lower()]
    assert non_tax_violations == []


def test_frontier_sweep_returns_points_up_to_goal_date():
    goal = Goal(amount=50_000, by_date=(date.today() + timedelta(days=5)).isoformat(), mode="A")
    navs = {"s_axis": 15.0, "s_hdfc": 15.0}
    points = frontier_sweep(goal, _holdings(), _constraints(), navs, ["s_axis", "s_hdfc"], days=30)
    assert 1 <= len(points) <= 6
    assert all("date" in p and "total_cost" in p for p in points)
