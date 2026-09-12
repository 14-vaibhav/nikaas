"""Generates `data/scenarios/demo_real_funds.json`.

Every other scenario in this repo uses invented scheme names with a
placeholder "1% / 365 days" exit-load rule I made up myself. This one
uses four **real, currently-traded schemes and their real AMFI scheme
codes**, with exit-load rules pulled from public sources - not guessed -
so the demo's numbers are grounded in something real. Only the HOLDINGS
(units, folios, purchase dates) are synthetic, per this repo's own rule:
a fact that doesn't name a person or their units is fine to publish; a
holding is not.

Sourcing, per scheme (checked 2026-09):

  Quant Small Cap Fund (AMFI 100176) - exit load 1% within 1 year, nil
    after. Machine-verified against the fund's own public Scheme
    Information Document: quantmutual.com's SID PDF, page 3, in the
    "Highlights / Summary of the Scheme" table:
      "Exit Load: For redemptions / switch outs (including SIP/STP)
      within 1 year from the date of allotment of units, irrespective
      of the amount of investment: 1% ... on or after 1 year ... : Nil."
    URL: https://quantmutual.com/Admin/SIDPdf/quant%20Small%20Cap%20Fund_SID%202023.pdf

  HDFC Flexi Cap Fund (AMFI 101763) - exit load 1% within 1 year, nil
    after. Publicly stated on multiple fund-data aggregators quoting the
    same SID clause; page not machine-verified against the PDF directly
    in this build (unlike Quant Small Cap above) - flagged here rather
    than overstating the citation.

  UTI Nifty 50 Index Fund (AMFI 100823) - exit load NIL. Index funds are
    routinely load-free; multiple sources confirm nil for both plans.

  HDFC Liquid Fund (AMFI 100872) - SEBI mandated a graded exit load on
    ALL liquid funds in 2019 (SEBI/HO/IMD circular, formalised via AMFI):
    ~0.0070% on day 1 declining to ~0.0045% on day 6, NIL from day 7
    onward. This repo's Constraint schema only models a single flat
    pct/window pair, not a day-by-day schedule, so this is recorded as
    nil - which is what it resolves to for any holding older than a
    week, i.e. every realistic redemption. The simplification is
    documented, not hidden.

None of this is investment advice; exit-load rules change by AMC
addendum and should always be re-verified against the current SID before
being relied on for a real redemption - which is exactly what the live
Hunter (`agent/hunter.py`'s `WebHunter`) exists to do.

Run:  python tools/build_demo_real_funds.py
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "scenarios" / "demo_real_funds.json"

TODAY = date(2026, 9, 12)
TAX_112A = {"ltcg_threshold_days": 365, "ltcg_rate": 0.125, "stcg_rate": 0.20,
            "ltcg_exemption": 125000}


def d(offset_days: int) -> str:
    return (TODAY + timedelta(days=offset_days)).isoformat()


def tax(scheme_id: str) -> dict:
    return {
        "id": f"c_{scheme_id}_tax", "scheme_id": scheme_id, "kind": "tax_rule",
        "value": dict(TAX_112A),
        "source": {
            "doc": "income-tax-act-1961-sec-112A", "page": 1, "clause": "112A", "url": None,
            "effective_date": "2024-07-23", "retrieved_at": "2026-09-10T10:00:00Z",
        },
        "confidence": "verified", "superseded_by": None,
    }


def exit_load(scheme_id, pct, window_days, *, doc, page, clause, url, note, confidence="verified") -> dict:
    return {
        "id": f"c_{scheme_id}_exit", "scheme_id": scheme_id, "kind": "exit_load",
        "value": {"pct": pct, "window_days": window_days},
        "source": {
            "doc": doc, "page": page, "clause": clause, "url": url,
            "effective_date": "2023-01-01", "retrieved_at": "2026-09-10T10:00:00Z",
        },
        "confidence": confidence, "note": note, "superseded_by": None,
    }


def lot(lot_id, scheme_id, folio, units, purchase_date, purchase_nav) -> dict:
    return {"lot_id": lot_id, "scheme_id": scheme_id, "folio": folio, "units": units,
            "purchase_date": purchase_date, "purchase_nav": purchase_nav}


SCHEMES = {
    "100176": ("Quant Small Cap Fund", "quant Mutual Fund", 324.42),
    "101763": ("HDFC Flexi Cap Fund", "HDFC Mutual Fund", 2145.60),  # NAV approx., not machine-fetched
    "100823": ("UTI Nifty 50 Index Fund", "UTI Mutual Fund", 171.04),
    "100872": ("HDFC Liquid Fund", "HDFC Mutual Fund", 4912.85),     # NAV approx., not machine-fetched
}


def build_constraints() -> list[dict]:
    cs = []
    for sid in SCHEMES:
        cs.append(tax(sid))

    cs.append(exit_load(
        "100176", 1.0, 365,
        doc="quant Small Cap Fund - Scheme Information Document (2023)", page=3, clause="Highlights table",
        url="https://quantmutual.com/Admin/SIDPdf/quant%20Small%20Cap%20Fund_SID%202023.pdf",
        note="machine-verified against the live SID PDF, page 3, 2026-09",
    ))
    cs.append(exit_load(
        "101763", 1.0, 365,
        doc="HDFC Flexi Cap Fund - Scheme Information Document", page=0, clause="Load Structure",
        url="https://www.hdfcfund.com/explore/mutual-funds/hdfc-flexi-cap-fund/direct",
        note="publicly stated 1%/1yr exit load - page not machine-verified against the SID PDF directly",
        confidence="inferred",
    ))
    cs.append(exit_load(
        "100823", 0.0, 0,
        doc="UTI Nifty 50 Index Fund - Scheme Information Document", page=0, clause="Load Structure",
        url="https://www.utimf.com/mutual-fund-schemes/uti-nifty-50-index-fund",
        note="index fund - nil exit load, both plans",
    ))
    cs.append(exit_load(
        "100872", 0.0, 0,
        doc="SEBI-mandated graded exit load on liquid funds (SEBI/HO/IMD circular; formalised via AMFI, 2019)",
        page=0, clause="graded schedule, days 1-6; nil from day 7",
        url="https://www.hdfcfund.com/explore/mutual-funds/hdfc-liquid-fund/direct",
        note="real rule decays ~0.007%->0.0045% over days 1-6, NIL from day 7 - this schema models one flat "
             "pct/window, so it is recorded as nil, which is what it equals for any holding older than a week",
    ))
    return cs


def build_holdings() -> list[dict]:
    return [
        # Quant Small Cap: one old, fully load-free lot, and one recent lot
        # still genuinely inside the real 365-day window - this is the one
        # that pays real, sourced exit load in the executed plan. Sized so
        # the other three (all genuinely 0%-load right now) can't cover the
        # goal alone - the recent lot has to be touched, not just exist.
        lot("l_qsc_old", "100176", "17880021 / 55", 200, "2022-06-15", 145.20),
        lot("l_qsc_recent", "100176", "17880021 / 55", 350, "2026-04-20", 298.70),

        lot("l_hdfcfc_1", "101763", "20447781 / 12", 80, "2021-11-10", 980.40),

        lot("l_utin50_1", "100823", "18220077 / 19", 600, "2022-01-05", 128.60),

        lot("l_hdfcliq_1", "100872", "20447781 / 88", 10, "2026-08-20", 4890.10),
    ]


def build_scenario() -> dict:
    return {
        "description": (
            "Four REAL, currently-traded schemes under their real AMFI scheme codes, "
            "with exit-load rules sourced from public scheme documents (Quant Small Cap "
            "Fund's is machine-verified against its actual SID PDF, page 3) rather than "
            "invented - see tools/build_demo_real_funds.py for the full citation trail. "
            "Only the holdings (units, folios, purchase dates) are synthetic. The other "
            "three schemes are genuinely 0%-load right now and can't cover the goal alone, "
            "so the plan has to reach into the recent Quant Small Cap lot, which is still "
            "inside the real 365-day exit-load window - a real, sourced, non-zero exit load "
            "in the executed plan, not a placeholder."
        ),
        # Candidate order matters here: all four schemes' OLDEST lot is past
        # its own load window, so the greedy allocator ranks them equally
        # cheap and drains them in this order - Quant Small Cap deliberately
        # comes last, so it's the one whose recent lot gets reached into.
        "goal": {"amount": 450000, "by_date": d(30), "mode": "A"},
        "candidate_schemes": ["100823", "101763", "100872", "100176"],
        "navs": {sid: nav for sid, (_n, _a, nav) in SCHEMES.items()},
        "holdings": build_holdings(),
        "constraints": build_constraints(),
    }


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(build_scenario(), indent=2))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
