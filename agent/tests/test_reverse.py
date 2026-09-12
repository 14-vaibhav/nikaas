from __future__ import annotations

from datetime import date, timedelta

from agent.contracts import Constraint, Holdings, Lot, Source
from agent.reverse import reverse
from agent.tests.conftest import load_scenario


def _src(effective="2025-01-01", retrieved=None):
    return Source(doc="sid.pdf", page=7, clause="7.2", url="https://e.com",
                  effective_date=effective,
                  retrieved_at=(retrieved or date.today().isoformat()) + "T10:00:00Z")


def _load(scheme, pct, window, cid=None, effective="2025-01-01"):
    return Constraint(id=cid or f"c_{scheme}_load", scheme_id=scheme, kind="exit_load",
                      value={"pct": pct, "window_days": window}, source=_src(effective),
                      confidence="verified")


def _tax(scheme):
    return Constraint(id=f"c_{scheme}_tax", scheme_id=scheme, kind="tax_rule",
                      value={"ltcg_threshold_days": 365, "ltcg_rate": 0.125,
                             "stcg_rate": 0.20, "ltcg_exemption": 125_000},
                      source=_src(), confidence="verified")


def test_free_slice_excludes_units_still_in_the_load_window():
    old = (date.today() - timedelta(days=800)).isoformat()
    new = (date.today() - timedelta(days=30)).isoformat()
    holdings = Holdings(lots=[
        Lot("l1", "s_a", "F1", 1000, old, 10.0),
        Lot("l2", "s_a", "F1", 500, new, 18.0),
    ])
    cs = [_load("s_a", 1.0, 365), _tax("s_a")]
    plan = reverse(holdings, cs, {"s_a": 20.0}, ["s_a"])
    assert len(plan.slices) == 1
    s = plan.slices[0]
    assert s.free_units == 1000          # only the old lot is free
    assert s.load_bearing_units == 500   # the recent lot is reported, not sold
    assert s.free_value == 20000.0


def test_lock_in_units_are_not_free():
    new = (date.today() - timedelta(days=200)).isoformat()
    holdings = Holdings(lots=[Lot("l1", "s_elss", "F1", 1000, new, 10.0)])
    cs = [
        _load("s_elss", 0.0, 0),
        Constraint(id="c_elss_lock", scheme_id="s_elss", kind="lock_in",
                   value={"window_days": 1095}, source=_src(), confidence="verified"),
        _tax("s_elss"),
    ]
    plan = reverse(holdings, cs, {"s_elss": 20.0}, ["s_elss"])
    assert plan.slices == [] or plan.slices[0].free_units == 0
    assert plan.total_free_value == 0.0


def test_two_active_load_constraints_same_date_abstains_not_guesses():
    old = (date.today() - timedelta(days=800)).isoformat()
    holdings = Holdings(lots=[Lot("l1", "s_x", "F1", 1000, old, 10.0)])
    cs = [
        _load("s_x", 1.0, 365, cid="c_x_a", effective="2025-06-01"),
        _load("s_x", 0.5, 180, cid="c_x_b", effective="2025-06-01"),
        _tax("s_x"),
    ]
    plan = reverse(holdings, cs, {"s_x": 20.0}, ["s_x"])
    assert plan.slices == []
    assert plan.abstained and plan.abstained[0].scheme_id == "s_x"


def test_stacked_addenda_resolve_then_the_scheme_is_usable():
    old = (date.today() - timedelta(days=800)).isoformat()
    holdings = Holdings(lots=[Lot("l1", "s_x", "F1", 1000, old, 10.0)])
    cs = [
        _load("s_x", 1.0, 365, cid="c_x_sid", effective="2024-01-01"),
        _load("s_x", 0.0, 0, cid="c_x_add", effective="2025-07-01"),
        _tax("s_x"),
    ]
    plan = reverse(holdings, cs, {"s_x": 20.0}, ["s_x"])
    assert not plan.abstained
    assert plan.slices and plan.slices[0].free_units == 1000


def test_demo_cas_18_reverse_runs_and_separates_folios():
    sc = load_scenario("demo_cas_18")
    plan = reverse(sc["holdings"], sc["constraints"], sc["navs"], sc["funds"])
    assert plan.total_free_value > 0
    # Franklin's genuine same-date tie must abstain; Axis' ordinary stack resolves.
    abst = {a.scheme_id for a in plan.abstained}
    assert "s_franklin_focused" in abst
    assert "s_axis_bluechip" not in abst
    # slices are per (scheme, folio) - never merged
    keys = [(s.scheme_id, s.folio) for s in plan.slices]
    assert len(keys) == len(set(keys))
