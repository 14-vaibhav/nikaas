from __future__ import annotations

from datetime import date, timedelta

from agent.contracts import Constraint, Goal, Source
from agent.evidence import EvidenceStore
from agent.loop import State
from agent.observe import observe
from agent.selector import select
from agent.tests.conftest import load_scenario


def _state(scenario, evidence):
    st = State(
        goal=scenario["goal"], mode="A", holdings=scenario["holdings"],
        navs=scenario["navs"], evidence=evidence,
    )
    st.candidates = select("A", scenario["funds"], scenario["holdings"], scenario["navs"])
    return st


def test_missing_evidence_gap_per_required_kind():
    scenario = load_scenario("demo_basic")
    st = _state(scenario, EvidenceStore())  # nothing loaded
    gaps = observe(st)
    kinds = {(g.fund_id, g.constraint_kind) for g in gaps if g.kind == "missing_evidence"}
    assert ("s_axis_bluechip", "exit_load") in kinds
    assert ("s_axis_bluechip", "tax_rule") in kinds


def test_stale_evidence_is_flagged():
    scenario = load_scenario("demo_basic")
    ev = EvidenceStore()
    ev.put_many(scenario["constraints"], "seed")
    st = _state(scenario, ev)
    gaps = observe(st)
    assert any(g.kind == "stale_evidence" and g.constraint_kind == "exit_load" for g in gaps)


def test_no_allocation_gap_once_evidence_is_complete_and_fresh():
    scenario = load_scenario("demo_basic")
    ev = EvidenceStore()
    fresh = date.today().isoformat() + "T00:00:00Z"
    for c in scenario["constraints"]:
        c.source.retrieved_at = fresh
    ev.put_many(scenario["constraints"], "seed")
    st = _state(scenario, ev)
    gaps = observe(st)
    assert [g.kind for g in gaps] == ["no_allocation"]


def test_unresolved_conflict_gap_when_two_constraints_are_active():
    scenario = load_scenario("demo_conflict")
    ev = EvidenceStore()
    fresh = date.today().isoformat() + "T00:00:00Z"
    for c in scenario["constraints"]:
        c.source.retrieved_at = fresh
    ev.put_many(scenario["constraints"], "seed")
    st = _state(scenario, ev)
    gaps = observe(st)
    assert any(
        g.kind == "unresolved_conflict" and g.fund_id == "s_axis_bluechip"
        and g.constraint_kind == "exit_load"
        for g in gaps
    )
