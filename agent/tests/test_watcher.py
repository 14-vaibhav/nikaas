from __future__ import annotations

from datetime import date, timedelta

from agent.evidence import EvidenceStore
from agent.hunter import FolderHunter
from agent.loop import run
from agent.watcher import check_plan, detect_triggers, snapshot_hashes
from agent.tests.conftest import load_scenario


def test_no_trigger_when_nothing_moved():
    reasons = detect_triggers(
        baseline_navs={"s_x": 100.0}, current_navs={"s_x": 100.2},
        baseline_hashes={"c1": "h"}, current_hashes={"c1": "h"},
        by_date=(date.today() + timedelta(days=30)).isoformat(),
    )
    assert reasons == []


def test_nav_move_and_hash_change_and_date_each_trigger():
    far = (date.today() + timedelta(days=30)).isoformat()
    assert detect_triggers({"s_x": 100.0}, {"s_x": 120.0}, {}, {}, far)
    assert detect_triggers({}, {}, {"c1": "h1"}, {"c1": "h2"}, far)
    assert detect_triggers({}, {}, {}, {}, (date.today() - timedelta(days=1)).isoformat())


def test_check_plan_reruns_the_loop_when_a_nav_moves():
    scenario = load_scenario("demo_basic")
    ev = EvidenceStore()
    ev.put_many(scenario["constraints"], "seed")
    first = run(
        scenario["goal"], "A", scenario["funds"], scenario["holdings"], scenario["navs"],
        hunter=FolderHunter(scenario["constraints"]), evidence=ev,
    )
    baseline_navs = dict(scenario["navs"])
    moved = dict(scenario["navs"])
    moved[scenario["funds"][0]] *= 1.25

    report = check_plan(
        run_id="run_test", goal=scenario["goal"], mode="A", fund_ids=scenario["funds"],
        holdings=scenario["holdings"], current_navs=moved, evidence=ev,
        hunter=FolderHunter(scenario["constraints"]),
        baseline_navs=baseline_navs, baseline_hashes=snapshot_hashes(ev),
        prior=first.plans,
    )
    assert report.triggered
    assert report.result is not None
    assert any("NAV moved" in r for r in report.reasons)
