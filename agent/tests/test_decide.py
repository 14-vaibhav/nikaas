from __future__ import annotations

from agent.budget import Budget
from agent.contracts import Gap, Route
from agent.decide import MAX_GAP_ATTEMPTS, decide
from agent.evidence import EvidenceStore
from agent.loop import State
from agent.tests.conftest import load_scenario


def _state():
    s = load_scenario("demo_basic")
    return State(goal=s["goal"], mode="A", holdings=s["holdings"], navs=s["navs"], evidence=EvidenceStore())


def test_missing_evidence_with_no_known_url_routes_hunt():
    gap = Gap(kind="missing_evidence", fund_id="s_axis_bluechip", constraint_kind="exit_load")
    _, route = decide([gap], _state(), Budget())
    assert route is Route.HUNT


def test_missing_evidence_with_known_url_routes_refetch():
    st = _state()
    scenario = load_scenario("demo_basic")
    st.evidence.put_many(scenario["constraints"], "seed")  # gives a known URL
    gap = Gap(kind="stale_evidence", fund_id="s_axis_bluechip", constraint_kind="exit_load")
    _, route = decide([gap], st, Budget())
    assert route is Route.REFETCH


def test_two_failed_tries_abstain_rather_than_guess():
    gap = Gap(kind="missing_evidence", fund_id="s_x", constraint_kind="exit_load",
              attempts=MAX_GAP_ATTEMPTS)
    _, route = decide([gap], _state(), Budget())
    assert route is Route.ABSTAIN


def test_budget_low_and_no_plan_routes_stop():
    st = _state()
    spent = Budget(steps_max=10, steps_used=10)
    gap = Gap(kind="missing_evidence", fund_id="s_x", constraint_kind="exit_load")
    _, route = decide([gap], st, spent)
    assert route is Route.STOP


def test_conflict_routes_resolve_and_no_allocation_routes_allocate():
    st = _state()
    _, r1 = decide([Gap(kind="unresolved_conflict", fund_id="s_x", constraint_kind="exit_load")], st, Budget())
    _, r2 = decide([Gap(kind="no_allocation")], st, Budget())
    assert r1 is Route.RESOLVE
    assert r2 is Route.ALLOCATE
