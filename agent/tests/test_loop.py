from __future__ import annotations

from agent.evidence import EvidenceStore
from agent.hunter import FolderHunter
from agent.loop import run
from agent.selector import build_mode_b_factors
from agent.tests.conftest import seeded_run


def test_basic_produces_two_verified_distinct_plans(basic):
    res = seeded_run(basic)
    assert res.plans is not None
    assert res.plans.cheapest.verdict == "verified"
    assert res.plans.merit_preserving.verdict == "verified"
    # Holding the higher-merit fund back still meets the goal, so the plans
    # genuinely diverge.
    assert not res.plans.identical
    cheap_funds = {l.scheme_id for l in res.plans.cheapest.legs}
    merit_funds = {l.scheme_id for l in res.plans.merit_preserving.legs}
    assert cheap_funds != merit_funds
    # The merit-preserving plan names what it kept and why.
    assert any(a.reason for a in res.plans.merit_preserving.abstained)


def test_basic_has_a_populated_frontier(basic):
    res = seeded_run(basic)
    assert len(res.plans.frontier) > 1
    assert res.plans.cheapest.frontier == res.plans.frontier


def test_route_sequence_is_dynamic_not_fixed(basic, conflict):
    """The whole point of the loop: the order of steps depends on the
    input. demo_basic has stale exit loads (REFETCH); demo_conflict has a
    stale sourceless tax rule (HUNT) and stacked addenda (RESOLVE)."""
    basic_routes = seeded_run(basic).trace
    conflict_routes = seeded_run(conflict).trace
    b = [e.output["route"] for e in basic_routes if e.type == "decide"]
    c = [e.output["route"] for e in conflict_routes if e.type == "decide"]
    assert b != c
    assert "REFETCH" in b and "REFETCH" not in c
    assert "HUNT" in c and "RESOLVE" in c


def test_every_step_emits_a_trace_event(basic):
    res = seeded_run(basic)
    steps = [e.step for e in res.trace]
    assert steps == list(range(1, len(steps) + 1))
    assert res.trace[0].type == "select"
    assert res.trace[-1].type == "gate"


def test_mode_b_scores_from_evidence_not_a_human_tick_list(mode_b):
    ev = EvidenceStore()
    ev.put_many(mode_b["constraints"], "seed")
    raw = build_mode_b_factors(mode_b["funds"], ev)
    res = run(
        mode_b["goal"], "B", mode_b["funds"], mode_b["holdings"], mode_b["navs"],
        hunter=FolderHunter(mode_b["constraints"]), evidence=ev, raw_mode_b=raw,
    )
    assert res.plans is not None
    assert res.plans.cheapest.verdict == "verified"
    select_event = res.trace[0]
    assert select_event.type == "select"
    assert select_event.output["mode"] == "B"
    # Axis's lower expense ratio should win it the merit-preserving hold-back.
    assert select_event.output["merit"]["s_axis_bluechip"] > select_event.output["merit"]["s_hdfc_top100"]


def test_fund_untucked_at_step0_is_abstained_not_silently_dropped(basic):
    """A held fund the human never ticked must not trip the verifier's
    SILENT_DROP check - Step 0's own boundary is not a dropped fund."""
    ev = EvidenceStore()
    ev.put_many(basic["constraints"], "seed")
    res = run(
        basic["goal"], "A", ["s_axis_bluechip"], basic["holdings"], basic["navs"],
        hunter=FolderHunter(basic["constraints"]), evidence=ev,
    )
    assert res.plans is not None
    assert res.plans.cheapest.verdict == "verified"
    assert not any(v.code == "SILENT_DROP" for v in res.plans.cheapest.violations)
    assert any(
        a.scheme_id == "s_hdfc_top100" and "Step 0" in a.reason
        for a in res.abstentions
    )


def test_abstains_rather_than_looping_forever_on_ambiguity(conflict):
    # Turn the two Axis exit-load constraints into a same-date contradiction.
    for c in conflict["constraints"]:
        if c.kind == "exit_load" and c.scheme_id == "s_axis_bluechip":
            c.source.effective_date = "2025-07-01"
    res = seeded_run(conflict)
    # It must terminate with a decision, not spin.
    assert res.plans is not None or res.stopped_reason
    axis_abstained = any(a.scheme_id == "s_axis_bluechip" for a in res.abstentions)
    axis_in_plan = res.plans and any(
        l.scheme_id == "s_axis_bluechip" for l in res.plans.cheapest.legs
    )
    assert axis_abstained or not axis_in_plan
