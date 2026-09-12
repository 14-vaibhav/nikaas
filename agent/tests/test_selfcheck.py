from __future__ import annotations

from agent.contracts import Abstention
from agent.selfcheck import run as self_check
from agent.tests.conftest import load_scenario
from agent.loop import State
from agent.evidence import EvidenceStore
from agent.selector import select


def _state_with_plans(scenario):
    from agent.loop import run
    from agent.hunter import FolderHunter

    ev = EvidenceStore()
    ev.put_many(scenario["constraints"], "seed")
    res = run(
        scenario["goal"], "A", scenario["funds"], scenario["holdings"],
        scenario["navs"], hunter=FolderHunter(scenario["constraints"]), evidence=ev,
    )
    st = State(goal=scenario["goal"], mode="A", holdings=scenario["holdings"],
               navs=scenario["navs"], evidence=ev)
    st.candidates = select("A", scenario["funds"], scenario["holdings"], scenario["navs"])
    st.plans = res.plans
    return st


def test_clean_plan_raises_no_doubts():
    st = _state_with_plans(load_scenario("demo_basic"))
    assert self_check(st) == []


def test_unexplained_abstention_is_a_doubt():
    st = _state_with_plans(load_scenario("demo_basic"))
    st.plans.cheapest.abstained.append(Abstention(scheme_id="s_mystery", reason=""))
    labels = {d.label for d in self_check(st)}
    assert "unexplained_abstention" in labels


def test_unverified_constraint_applied_is_a_doubt():
    st = _state_with_plans(load_scenario("demo_basic"))
    # Downgrade a constraint the cheapest plan applied.
    applied = st.plans.cheapest.legs[0].constraints_applied[0]
    for e in st.evidence.all():
        if e.constraint.id == applied:
            e.constraint.confidence = "inferred"
    labels = {d.label for d in self_check(st)}
    assert "unverified_constraint" in labels
