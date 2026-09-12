"""Pins the single-purpose stage fixtures (demo_timing / demo_abstain /
demo_unachievable / demo_mode_b_cas) so a change to the loop can't quietly
break the behaviour each one is meant to show on the day."""

from __future__ import annotations

from agent.evidence import EvidenceStore
from agent.hunter import FolderHunter
from agent.loop import run
from agent.selector import build_mode_b_factors
from agent.tests.conftest import load_scenario, seeded_run
from agent.trace import TraceLog


def _run_with_trace(name, mode="A", raw_mode_b=None):
    sc = load_scenario(name)
    ev = EvidenceStore()
    ev.put_many(sc["constraints"], "seed")
    tr = TraceLog()
    res = run(sc["goal"], mode, sc["funds"], sc["holdings"], sc["navs"],
              hunter=FolderHunter(sc["constraints"]), evidence=ev, trace=tr,
              raw_mode_b=raw_mode_b)
    return res, tr


def test_demo_timing_frontier_steps_and_self_check_reaches_for_the_cheaper_date():
    res, tr = _run_with_trace("demo_timing")
    costs = {round(f.total_cost) for f in res.plans.frontier}
    assert len(costs) > 1 and 0 in costs           # a real step in the curve
    labels = " ".join(e.label for e in tr.events if e.type == "self_check")
    assert "cheaper_date" in labels                 # self-check spotted it
    assert tr.route_sequence().count("ALLOCATE") >= 2  # tried it, then backed off
    # the deadline forces the load to be paid - the plan is honest about it
    assert res.plans.cheapest.verdict == "verified"
    assert res.plans.cheapest.totals.exit_load > 100


def test_demo_abstain_hunts_fails_abstains_and_still_meets_the_goal():
    res, tr = _run_with_trace("demo_abstain")
    routes = tr.route_sequence()
    assert routes[:3] == ["HUNT", "HUNT", "ABSTAIN"]
    reasons = {a.scheme_id: a.reason for a in res.abstentions}
    assert "s_ghost_c" in reasons and "abstain" in reasons["s_ghost_c"].lower()
    assert res.plans.cheapest.verdict == "verified"
    assert res.plans.cheapest.totals.net + 1 >= res.plans.cheapest.goal.amount
    assert all(l.scheme_id != "s_ghost_c" for l in res.plans.cheapest.legs)


def test_demo_unachievable_stops_plainly_with_no_approvable_plan():
    res, _ = _run_with_trace("demo_unachievable")
    assert "not reachable" in res.stopped_reason
    assert res.plans.cheapest.verdict == "rejected"
    assert any(v.code == "SHORTFALL" for v in res.plans.cheapest.violations)


def test_demo_mode_b_cas_scores_18_funds_and_verifies():
    sc = load_scenario("demo_mode_b_cas")
    seed = EvidenceStore()
    seed.put_many(sc["constraints"], "seed")
    raw = build_mode_b_factors(sc["funds"], seed)
    res = seeded_run(sc, "B", raw_mode_b=raw)
    assert res.plans.cheapest.verdict == "verified"
    assert res.plans.merit_preserving.verdict == "verified"
    assert not res.plans.identical
