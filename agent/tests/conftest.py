from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.contracts import Goal
from agent.evidence import EvidenceStore
from agent.holdings import load_holdings
from agent.hunter import FolderHunter
from agent.serde import constraint_from_dict

SCENARIOS = Path(__file__).resolve().parent.parent.parent / "data" / "scenarios"


def load_scenario(name: str):
    raw = json.loads((SCENARIOS / f"{name}.json").read_text())
    goal = Goal(**raw["goal"])
    holdings = load_holdings(raw["holdings"])
    constraints = [constraint_from_dict(c) for c in raw["constraints"]]
    return {
        "goal": goal,
        "funds": raw["candidate_schemes"],
        "holdings": holdings,
        "navs": raw["navs"],
        "constraints": constraints,
    }


@pytest.fixture
def basic():
    return load_scenario("demo_basic")


@pytest.fixture
def conflict():
    return load_scenario("demo_conflict")


@pytest.fixture
def mode_b():
    return load_scenario("demo_mode_b")


def seeded_run(scenario, mode="A", **kwargs):
    from agent.loop import run

    ev = EvidenceStore()
    ev.put_many(scenario["constraints"], "seed")
    hunter = FolderHunter(scenario["constraints"])
    return run(
        scenario["goal"], mode, scenario["funds"], scenario["holdings"],
        scenario["navs"], hunter=hunter, evidence=ev, **kwargs,
    )
