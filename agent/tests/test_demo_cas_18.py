"""The 18-scheme scenario is the PRD's hard case. This pins the behaviours
the demo depends on so a tweak to holdings or constraints can't quietly
flatten it."""

from __future__ import annotations

from agent.tests.conftest import load_scenario, seeded_run


def test_loop_produces_two_verified_plans_with_no_early_stop():
    sc = load_scenario("demo_cas_18")
    res = seeded_run(sc, "A")
    assert res.plans is not None
    assert res.plans.cheapest.verdict == "verified"
    assert res.plans.merit_preserving.verdict == "verified"
    assert not res.stopped_reason


def test_route_sequence_shows_hunt_resolve_and_repair():
    sc = load_scenario("demo_cas_18")
    from agent.evidence import EvidenceStore
    from agent.hunter import FolderHunter
    from agent.loop import run
    from agent.trace import TraceLog

    ev = EvidenceStore()
    ev.put_many(sc["constraints"], "seed")
    tr = TraceLog()
    run(sc["goal"], "A", sc["funds"], sc["holdings"], sc["navs"],
        hunter=FolderHunter(sc["constraints"]), evidence=ev, trace=tr)

    routes = tr.route_sequence()
    assert "HUNT" in routes          # the stale, url-less SBI Small Cap rule
    assert "RESOLVE" in routes       # the stacked Axis Bluechip addenda
    assert routes.count("ALLOCATE") >= 2  # first plan rejected -> repair -> re-allocate
    types = [e.type for e in tr.events]
    assert "repair" in types         # the ELSS lock-in exclusion


def test_franklin_tie_abstains_and_elss_lockin_abstains():
    sc = load_scenario("demo_cas_18")
    res = seeded_run(sc, "A")
    reasons = {a.scheme_id: a.reason for a in res.abstentions}
    assert "s_franklin_focused" in reasons
    assert "effective date" in reasons["s_franklin_focused"]
    assert "s_mirae_elss" in reasons
    assert "locked" in reasons["s_mirae_elss"]


def test_plans_diverge_and_pay_a_real_unavoidable_exit_load():
    sc = load_scenario("demo_cas_18")
    res = seeded_run(sc, "A")
    assert not res.plans.identical
    c, m = res.plans.cheapest, res.plans.merit_preserving
    assert c.totals.exit_load != m.totals.exit_load
    # Both recent, load-bearing lots' 365-day windows lapse *after* the goal
    # date - this cost is genuinely unavoidable inside the deadline, not a
    # tuning artifact, so the frontier is correctly flat at a non-zero cost.
    assert c.totals.exit_load > 0
    costs = {round(f.total_cost) for f in c.frontier}
    assert costs == {round(c.totals.exit_load)}


def test_mode_b_scores_from_evidence_and_still_verifies():
    sc = load_scenario("demo_cas_18")
    from agent.evidence import EvidenceStore
    from agent.selector import build_mode_b_factors

    seed = EvidenceStore()
    seed.put_many(sc["constraints"], "seed")
    raw = build_mode_b_factors(sc["funds"], seed)
    res = seeded_run(sc, "B", raw_mode_b=raw)
    assert res.plans.cheapest.verdict == "verified"
    assert res.plans.merit_preserving.verdict == "verified"
