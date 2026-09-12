"""Generates the small single-purpose demo fixtures under data/scenarios/.

Each one isolates ONE behaviour so it reads cleanly on stage:

  demo_timing        the frontier chart does something dramatic - a big exit
                     load early, zero once a 365-day window lapses - and the
                     self-check spots the cheaper date, tests it, and backs
                     off because T+3 settlement would miss the deadline
  demo_abstain       a candidate scheme with no retrievable rule at all -
                     HUNT fails twice, the agent abstains and excludes it,
                     and still meets the goal from the rest
  demo_unachievable  the goal is larger than the whole portfolio - the
                     verifier raises SHORTFALL, nothing is passed off as a
                     plan, the loop stops and says so plainly

All synthetic. No real investor, folio or PAN.

Run:  python tools/build_demo_scenarios.py
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "scenarios"

TODAY = date(2026, 9, 11)
TAX_112A = {"ltcg_threshold_days": 365, "ltcg_rate": 0.125, "stcg_rate": 0.20,
            "ltcg_exemption": 125000}


def _src(doc, page, clause, url, effective, retrieved="2026-09-09T10:00:00Z"):
    return {"doc": doc, "page": page, "clause": clause, "url": url,
            "effective_date": effective, "retrieved_at": retrieved}


def tax(scheme_id):
    return {"id": f"c_{scheme_id}_tax", "scheme_id": scheme_id, "kind": "tax_rule",
            "value": dict(TAX_112A),
            "source": _src("income-tax-act-1961-sec-112A", 1, "112A", None, "2024-07-23"),
            "confidence": "verified", "superseded_by": None}


def exit_load(scheme_id, pct, window, doc, url, clause="7.1", effective="2025-04-01"):
    return {"id": f"c_{scheme_id}_exit", "scheme_id": scheme_id, "kind": "exit_load",
            "value": {"pct": pct, "window_days": window},
            "source": _src(doc, 14, clause, url, effective),
            "confidence": "verified", "superseded_by": None}


def lot(lot_id, scheme_id, folio, units, purchase_date, purchase_nav):
    return {"lot_id": lot_id, "scheme_id": scheme_id, "folio": folio, "units": units,
            "purchase_date": purchase_date, "purchase_nav": purchase_nav}


def d(offset_days: int) -> str:
    return (TODAY + timedelta(days=offset_days)).isoformat()


# --- demo_timing ---------------------------------------------------------


def demo_timing() -> dict:
    # Goal 22 days out. s_beta_flexi holds a lot bought 346 days ago, so its
    # 365-day exit-load window lapses on day 19 - inside the frontier window
    # and just past the default sell date (by_date - 3 = day 19... actually
    # day 19 exactly, so day 18 still pays, days 19-22 are free).
    by_date = d(22)
    return {
        "description": (
            "Timing frontier demo. The goal is 22 days out. One scheme (Beta "
            "Flexi) holds a large lot whose 365-day exit-load window lapses two "
            "days before the deadline, so the cost-vs-sell-date curve drops from "
            "~Rs.6,000 to zero in one step. The self-check spots the cheaper date, "
            "re-plans onto it, finds the T+3 settlement would land after the "
            "deadline, and backs off to the original date - visible in the trace. "
            "Synthetic only."
        ),
        "goal": {"amount": 900000, "by_date": by_date, "mode": "A"},
        "candidate_schemes": ["s_alpha_index", "s_beta_flexi", "s_gamma_large"],
        "navs": {"s_alpha_index": 100.0, "s_beta_flexi": 50.0, "s_gamma_large": 200.0},
        "holdings": [
            lot("l_alpha_1", "s_alpha_index", "IDX0001 / 11", 1600, "2022-04-01", 60.0),
            lot("l_gamma_1", "s_gamma_large", "LRG0002 / 22", 900, "2021-10-01", 120.0),
            # 2025-10-02 + 365 = 2026-10-02 = day 21: the exit-load window lapses
            # two days *after* the default sell date (by_date - 3 = day 19), so
            # the default plan pays the load and the self-check has a genuinely
            # cheaper date to reach for.
            lot("l_beta_1", "s_beta_flexi", "FLX0003 / 33", 14000, "2025-10-02", 44.0),
        ],
        "constraints": [
            exit_load("s_alpha_index", 0.0, 0, "alpha-index-sid.pdf",
                      "https://example.invalid/alpha-index-sid.pdf", clause="5.1"),
            tax("s_alpha_index"),
            exit_load("s_beta_flexi", 1.0, 365, "beta-flexi-sid.pdf",
                      "https://example.invalid/beta-flexi-sid.pdf"),
            tax("s_beta_flexi"),
            exit_load("s_gamma_large", 1.0, 365, "gamma-large-sid.pdf",
                      "https://example.invalid/gamma-large-sid.pdf"),
            tax("s_gamma_large"),
        ],
    }


# --- demo_abstain ------------------------------------------------------


def demo_abstain() -> dict:
    return {
        "description": (
            "Abstention demo. Three funds are candidates but one - Ghost Multi "
            "Asset - has no rule on file and no source the folder hunt can "
            "reach. The loop HUNTs for it, fails twice, and abstains rather than "
            "guessing a rule; it excludes the fund and still meets the goal from "
            "the other two. The abstained fund is listed with its reason, not "
            "silently dropped. Synthetic only."
        ),
        "goal": {"amount": 500000, "by_date": d(95), "mode": "A"},
        "candidate_schemes": ["s_solid_a", "s_solid_b", "s_ghost_c"],
        "navs": {"s_solid_a": 100.0, "s_solid_b": 75.0, "s_ghost_c": 60.0},
        "holdings": [
            lot("l_solid_a_1", "s_solid_a", "SLD0001 / 01", 8000, "2022-02-10", 40.0),
            lot("l_solid_b_1", "s_solid_b", "SLD0002 / 02", 4000, "2021-06-05", 50.0),
            lot("l_ghost_c_1", "s_ghost_c", "GHT0003 / 03", 5000, "2023-08-20", 45.0),
        ],
        "constraints": [
            # note: nothing for s_ghost_c - the hunt has nothing to find
            exit_load("s_solid_a", 0.0, 0, "solid-a-sid.pdf",
                      "https://example.invalid/solid-a-sid.pdf", clause="5.1"),
            tax("s_solid_a"),
            exit_load("s_solid_b", 0.0, 0, "solid-b-sid.pdf",
                      "https://example.invalid/solid-b-sid.pdf", clause="5.1"),
            tax("s_solid_b"),
        ],
    }


# --- demo_unachievable -------------------------------------------------


def demo_unachievable() -> dict:
    return {
        "description": (
            "Honesty demo. The goal (Rs.50,00,000) is far larger than the whole "
            "portfolio (~Rs.9,00,000). The allocator sells everything, the "
            "verifier raises SHORTFALL, and the loop stops with 'goal not "
            "reachable from the available holdings' - no partial plan is dressed "
            "up as done, and nothing is approvable. Synthetic only."
        ),
        "goal": {"amount": 5000000, "by_date": d(80), "mode": "A"},
        "candidate_schemes": ["s_tiny_a", "s_tiny_b"],
        "navs": {"s_tiny_a": 50.0, "s_tiny_b": 80.0},
        "holdings": [
            lot("l_tiny_a_1", "s_tiny_a", "TNY0001 / 01", 10000, "2022-01-15", 30.0),
            lot("l_tiny_b_1", "s_tiny_b", "TNY0002 / 02", 5000, "2021-09-01", 50.0),
        ],
        "constraints": [
            exit_load("s_tiny_a", 0.0, 0, "tiny-a-sid.pdf",
                      "https://example.invalid/tiny-a-sid.pdf", clause="5.1"),
            tax("s_tiny_a"),
            exit_load("s_tiny_b", 0.0, 0, "tiny-b-sid.pdf",
                      "https://example.invalid/tiny-b-sid.pdf", clause="5.1"),
            tax("s_tiny_b"),
        ],
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, builder in [
        ("demo_timing", demo_timing),
        ("demo_abstain", demo_abstain),
        ("demo_unachievable", demo_unachievable),
    ]:
        path = OUT / f"{name}.json"
        path.write_text(json.dumps(builder(), indent=2))
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
