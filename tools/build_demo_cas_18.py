"""Generates `data/scenarios/demo_cas_18.json` and `data/samples/sample_cas_18.pdf`.

The PRD's demo is the *hard* case, not the average: "₹18,00,000 by 20 March, on
an 18-scheme portfolio". This builds exactly that as a folder-mode scenario -
holdings reconstructed from a (synthetic) CAMS + KFintech consolidated account
statement, with the governing rules preloaded so the loop runs offline and
deterministically.

Everything here is invented. No real client, no real folio, no real PAN.

What the scenario deliberately exercises:
  * 18 candidate schemes across 9 AMCs
  * the same scheme (Axis Bluechip, HDFC Top 100) under two folios from two
    RTAs - two independent FIFO queues that must not be merged
  * a stacked-addendum conflict on Axis Bluechip (SID 1%/365d, later addendum
    0.5%/180d) -> RESOLVE by effective_date
  * a genuine tie on Franklin Focused (two addenda, same effective_date,
    contradictory pct) -> abstain, never guess
  * a stale, url-less exit-load rule on SBI Small Cap -> HUNT
  * two ELSS holdings still inside the 3-year lock-in -> first allocation
    breaches it, repair excludes them
  * index / liquid / corporate-bond schemes that are genuinely free to exit
    (zero load) so "cheapest" and "merit-preserving" diverge

Run:  python tools/build_demo_cas_18.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_JSON = ROOT / "data" / "scenarios" / "demo_cas_18.json"
OUT_PDF = ROOT / "data" / "samples" / "sample_cas_18.pdf"

RETRIEVED_FRESH = "2026-09-08T10:00:00Z"
RETRIEVED_STALE = "2026-06-15T10:00:00Z"  # > 30d before "today" (2026-09-10) => stale tax/exit

TAX_112A = {
    "ltcg_threshold_days": 365,
    "ltcg_rate": 0.125,
    "stcg_rate": 0.20,
    "ltcg_exemption": 125000,
}


def tax_rule(scheme_id: str, retrieved_at: str = RETRIEVED_FRESH) -> dict:
    return {
        "id": f"c_{scheme_id}_tax",
        "scheme_id": scheme_id,
        "kind": "tax_rule",
        "value": dict(TAX_112A),
        "source": {
            "doc": "income-tax-act-1961-sec-112A",
            "page": 1,
            "clause": "112A",
            "url": None,
            "effective_date": "2024-07-23",
            "retrieved_at": retrieved_at,
        },
        "confidence": "verified",
        "superseded_by": None,
    }


def exit_load(
    scheme_id: str,
    pct: float,
    window_days: int,
    *,
    doc: str,
    page: int,
    clause: str,
    url: str | None,
    effective_date: str,
    retrieved_at: str = RETRIEVED_FRESH,
    cid: str | None = None,
    confidence: str = "verified",
) -> dict:
    return {
        "id": cid or f"c_{scheme_id}_exit",
        "scheme_id": scheme_id,
        "kind": "exit_load",
        "value": {"pct": pct, "window_days": window_days},
        "source": {
            "doc": doc,
            "page": page,
            "clause": clause,
            "url": url,
            "effective_date": effective_date,
            "retrieved_at": retrieved_at,
        },
        "confidence": confidence,
        "superseded_by": None,
    }


def lock_in(scheme_id: str, *, doc: str, url: str) -> dict:
    return {
        "id": f"c_{scheme_id}_lockin",
        "scheme_id": scheme_id,
        "kind": "lock_in",
        "value": {"window_days": 1095},
        "source": {
            "doc": doc,
            "page": 8,
            "clause": "3.1",
            "url": url,
            "effective_date": "2025-04-01",
            "retrieved_at": RETRIEVED_FRESH,
        },
        "confidence": "verified",
        "superseded_by": None,
    }


def expense_ratio(scheme_id: str, pct: float, *, doc: str, url: str) -> dict:
    return {
        "id": f"c_{scheme_id}_ter",
        "scheme_id": scheme_id,
        "kind": "expense_ratio",
        "value": {"pct": pct},
        "source": {
            "doc": doc,
            "page": 6,
            "clause": "4.1",
            "url": url,
            "effective_date": "2025-04-01",
            "retrieved_at": RETRIEVED_FRESH,
        },
        "confidence": "verified",
        "superseded_by": None,
    }


def lot(lot_id, scheme_id, folio, units, purchase_date, purchase_nav) -> dict:
    return {
        "lot_id": lot_id,
        "scheme_id": scheme_id,
        "folio": folio,
        "units": units,
        "purchase_date": purchase_date,
        "purchase_nav": purchase_nav,
    }


# --- the 18 schemes ---------------------------------------------------------
# (scheme_id, display, amc, nav, ter_pct, sid_doc, sid_url)
SCHEMES = [
    ("s_axis_bluechip",       "Axis Bluechip Fund",                 "axis",     62.35,  1.72, "axis-bluechip-sid.pdf",        "https://www.axismf.com/schemeinfodocuments/axis-bluechip-sid.pdf"),
    ("s_hdfc_top100",          "HDFC Top 100 Fund",                  "hdfc",   1148.90,  1.68, "hdfc-top100-sid.pdf",          "https://www.hdfcfund.com/schemeinfodocuments/hdfc-top100-sid.pdf"),
    ("s_sbi_smallcap",         "SBI Small Cap Fund",                 "sbi",     212.40,  1.66, "sbi-smallcap-sid.pdf",         "https://www.sbimf.com/schemeinfodocuments/sbi-smallcap-sid.pdf"),
    ("s_icici_bluechip",       "ICICI Prudential Bluechip Fund",     "icici",   106.10,  1.45, "icici-bluechip-sid.pdf",       "https://www.icicipruamc.com/schemeinfodocuments/icici-bluechip-sid.pdf"),
    ("s_kotak_flexicap",       "Kotak Flexicap Fund",               "kotak",    88.20,  1.53, "kotak-flexicap-sid.pdf",       "https://www.kotakmf.com/schemeinfodocuments/kotak-flexicap-sid.pdf"),
    ("s_mirae_largecap",       "Mirae Asset Large Cap Fund",        "mirae",   135.70,  1.49, "mirae-largecap-sid.pdf",       "https://www.miraeassetmf.co.in/schemeinfodocuments/mirae-largecap-sid.pdf"),
    ("s_nippon_smallcap",      "Nippon India Small Cap Fund",       "nippon",  198.25,  1.55, "nippon-smallcap-sid.pdf",      "https://mf.nipponindiaim.com/schemeinfodocuments/nippon-smallcap-sid.pdf"),
    ("s_parag_flexicap",       "Parag Parikh Flexi Cap Fund",       "ppfas",    82.60,  1.33, "ppfas-flexicap-sid.pdf",       "https://amc.ppfas.com/schemeinfodocuments/ppfas-flexicap-sid.pdf"),
    ("s_quant_elss",           "Quant ELSS Tax Saver Fund",         "quant",   412.80,  1.77, "quant-elss-sid.pdf",           "https://www.quantmutual.com/schemeinfodocuments/quant-elss-sid.pdf"),
    ("s_mirae_elss",           "Mirae Asset ELSS Tax Saver Fund",   "mirae",    49.10,  1.58, "mirae-elss-sid.pdf",           "https://www.miraeassetmf.co.in/schemeinfodocuments/mirae-elss-sid.pdf"),
    ("s_uti_nifty50",          "UTI Nifty 50 Index Fund",           "uti",     168.40,  0.21, "uti-nifty50-sid.pdf",          "https://www.utimf.com/schemeinfodocuments/uti-nifty50-sid.pdf"),
    ("s_hdfc_liquid",          "HDFC Liquid Fund",                  "hdfc",   4895.20,  0.30, "hdfc-liquid-sid.pdf",          "https://www.hdfcfund.com/schemeinfodocuments/hdfc-liquid-sid.pdf"),
    ("s_sbi_balanced_adv",     "SBI Balanced Advantage Fund",       "sbi",      42.55,  1.61, "sbi-baf-sid.pdf",              "https://www.sbimf.com/schemeinfodocuments/sbi-baf-sid.pdf"),
    ("s_icici_corp_bond",      "ICICI Prudential Corporate Bond",   "icici",    29.85,  0.58, "icici-corpbond-sid.pdf",       "https://www.icicipruamc.com/schemeinfodocuments/icici-corpbond-sid.pdf"),
    ("s_axis_midcap",          "Axis Midcap Fund",                  "axis",    118.90,  1.70, "axis-midcap-sid.pdf",          "https://www.axismf.com/schemeinfodocuments/axis-midcap-sid.pdf"),
    ("s_canara_emerging",      "Canara Robeco Emerging Equities",   "canara",  268.10,  1.63, "canara-emerging-sid.pdf",      "https://www.canararobeco.com/schemeinfodocuments/canara-emerging-sid.pdf"),
    ("s_dsp_smallcap",         "DSP Small Cap Fund",                "dsp",     181.30,  1.71, "dsp-smallcap-sid.pdf",         "https://www.dspim.com/schemeinfodocuments/dsp-smallcap-sid.pdf"),
    ("s_franklin_focused",     "Franklin India Focused Equity Fund","franklin", 97.20,  1.74, "franklin-focused-sid.pdf",     "https://www.franklintempletonindia.com/schemeinfodocuments/franklin-focused-sid.pdf"),
]

NAVS = {sid: nav for sid, _n, _a, nav, *_ in SCHEMES}
CANDIDATES = [sid for sid, *_ in SCHEMES]

# Schemes that carry a real exit load window (equity, 1% / 365d) unless
# overridden below. Index / liquid / debt are free to exit.
ZERO_LOAD = {"s_uti_nifty50", "s_hdfc_liquid", "s_icici_corp_bond", "s_quant_elss", "s_mirae_elss"}


def build_constraints() -> list[dict]:
    cs: list[dict] = []
    for sid, name, amc, nav, ter, sid_doc, sid_url in SCHEMES:
        cs.append(tax_rule(sid))
        cs.append(expense_ratio(sid, ter, doc=sid_doc, url=sid_url))

        if sid == "s_axis_bluechip":
            # stacked addenda: SID 1%/365d, later addendum 0.5%/180d
            cs.append(exit_load(sid, 1.0, 365, doc=sid_doc, page=14, clause="7.2",
                                url=sid_url, effective_date="2024-01-01",
                                cid="c_axis_bluechip_exit_sid"))
            cs.append(exit_load(sid, 0.5, 180, doc="axis-notice-cum-addendum-2025-31.pdf", page=1,
                                clause="1", url="https://www.axismf.com/addenda/axis-2025-31.pdf",
                                effective_date="2025-08-01", cid="c_axis_bluechip_exit_add"))
        elif sid == "s_franklin_focused":
            # genuine tie: two addenda, SAME effective_date, contradictory pct
            cs.append(exit_load(sid, 1.0, 365, doc="franklin-addendum-2025-14.pdf", page=1, clause="2",
                                url="https://www.franklintempletonindia.com/addenda/ft-2025-14.pdf",
                                effective_date="2025-06-01", cid="c_franklin_exit_a"))
            cs.append(exit_load(sid, 0.75, 365, doc="franklin-addendum-2025-15.pdf", page=1, clause="1",
                                url="https://www.franklintempletonindia.com/addenda/ft-2025-15.pdf",
                                effective_date="2025-06-01", cid="c_franklin_exit_b"))
        elif sid == "s_sbi_smallcap":
            # stale AND no url -> the loop must HUNT for it
            cs.append(exit_load(sid, 1.0, 365, doc=sid_doc, page=17, clause="6.3",
                                url=None, effective_date="2024-05-01",
                                retrieved_at=RETRIEVED_STALE, cid="c_sbi_smallcap_exit"))
        elif sid in ZERO_LOAD:
            cs.append(exit_load(sid, 0.0, 0, doc=sid_doc, page=11, clause="5.1",
                                url=sid_url, effective_date="2025-04-01"))
        else:
            cs.append(exit_load(sid, 1.0, 365, doc=sid_doc, page=15, clause="7.1",
                                url=sid_url, effective_date="2025-04-01"))

        if sid in ("s_quant_elss", "s_mirae_elss"):
            cs.append(lock_in(sid, doc=sid_doc, url=sid_url))
    return cs


def build_holdings() -> list[dict]:
    """Sized so no single scheme covers the Rs.18,00,000 goal: the free-to-exit
    schemes (index / liquid / corporate bond / the one sellable ELSS lot) come
    to roughly Rs.8-9L, so the greedy allocator has to reach past them - first
    into the locked ELSS (repair excludes it), then into load-bearing equity,
    where recent 2026 lots make the exit load real and the 30-day frontier
    non-flat. Merit-preserving then holds the long-held compounders back and
    pays visibly more.
    """
    # folio conventions: CAMS folios look like 1234567 / 89, KFintech like NNNNNNNNNNN.
    # Portfolio ~Rs.30L against an Rs.18L goal, so ~60% must be raised - the
    # greedy allocator cannot stop after one or two schemes.
    h: list[dict] = []

    # Axis Bluechip across TWO RTAs / folios -> two independent FIFO queues.
    h.append(lot("l_axisbc_cams_1", "s_axis_bluechip", "11002233 / 45",  2400, "2021-02-11", 41.10))
    h.append(lot("l_axisbc_cams_2", "s_axis_bluechip", "11002233 / 45",  2000, "2026-06-20", 60.40))  # < 365d of the SID rule
    h.append(lot("l_axisbc_kfin_1", "s_axis_bluechip", "91850027431",    3000, "2023-08-05", 52.75))  # different folio - own FIFO queue

    # HDFC Top 100 across two folios too.
    h.append(lot("l_hdfctop_cams_1", "s_hdfc_top100", "20447781 / 12",  180, "2020-11-30", 610.00))
    h.append(lot("l_hdfctop_kfin_1", "s_hdfc_top100", "80231145566",    260, "2026-02-15", 1015.20))  # < 365d -> carries load

    # Free-to-exit pool (~Rs.4.8L usable once the locked ELSS is removed) -
    # deliberately short of the goal, so the loop must reach the locked ELSS
    # (repair drops it) and then load-bearing equity.
    h.append(lot("l_utin50_1",   "s_uti_nifty50",     "18220077 / 19",  700, "2021-05-06",  92.40))   # ~Rs.1.18L
    h.append(lot("l_hdfcliq_1",  "s_hdfc_liquid",     "20447781 / 88",   16, "2026-07-01", 4790.00))  # ~Rs.0.78L
    h.append(lot("l_icicicb_1",  "s_icici_corp_bond", "14778820 / 90", 3500, "2023-04-12",  25.60))   # ~Rs.1.04L
    h.append(lot("l_quantelss_1","s_quant_elss",      "17880021 / 55",  150, "2022-01-10", 250.30))   # > 3y: sellable ~Rs.0.62L
    h.append(lot("l_miraeelss_1","s_mirae_elss",      "90880012230",   4000, "2025-02-18",  40.10))   # < 3y: LOCKED -> repair drops it

    # Load-bearing equity - a mix of old (no load) and recent 2026 (load) lots.
    h.append(lot("l_sbismc_1",   "s_sbi_smallcap",     "13559002 / 01",  700, "2021-07-19",  95.30))
    # 2026-05-02 -> held 319d on the 2027-03-17 sell date: inside the 365-day
    # window, and the window doesn't lapse before by_date either - this
    # Rs.1,912 (900u x Rs.212.40 x 1%) is a real, unavoidable-by-this-deadline
    # cost, not a tuning artifact.
    h.append(lot("l_sbismc_2",   "s_sbi_smallcap",     "13559002 / 01",  900, "2026-05-02", 178.40))  # < 365d
    h.append(lot("l_icicibc_1",  "s_icici_bluechip",   "14778820 / 33", 1200, "2022-03-08",  71.40))
    # 2026-04-15 -> held 336d on 2027-03-17: same story, Rs.1,485 (1400u x
    # Rs.106.10 x 1%) that this deadline can't dodge.
    h.append(lot("l_icicibc_2",  "s_icici_bluechip",   "14778820 / 33", 1400, "2026-04-15",  99.80))  # < 365d
    h.append(lot("l_kotakfc_1",  "s_kotak_flexicap",   "15220034 / 77", 1500, "2022-09-14",  58.10))
    h.append(lot("l_miraelc_1",  "s_mirae_largecap",   "90114400271",    900, "2021-12-02",  84.20))
    h.append(lot("l_nipsmc_1",   "s_nippon_smallcap",  "90552113004",    600, "2023-01-25", 121.60))
    h.append(lot("l_axismc_1",   "s_axis_midcap",      "11002233 / 71",  700, "2021-10-04",  70.20))
    h.append(lot("l_canem_1",    "s_canara_emerging",  "19004455 / 02",  300, "2022-06-30", 178.40))
    h.append(lot("l_dspsmc_1",   "s_dsp_smallcap",     "90773311209",    500, "2023-03-17", 132.10))
    h.append(lot("l_frankf_1",   "s_franklin_focused", "21550098 / 06", 1000, "2022-08-22",  70.90))

    # Long-held compounders - what merit-preserving tries to keep.
    h.append(lot("l_ppfc_1",     "s_parag_flexicap",   "16003399 / 08", 4000, "2020-08-10",  38.90))  # big CAGR
    h.append(lot("l_sbibaf_1",   "s_sbi_balanced_adv", "13559002 / 44", 4000, "2022-11-21",  31.05))
    return h


def build_scenario() -> dict:
    return {
        "description": (
            "Synthetic 18-scheme portfolio - the PRD's hard case ('Rs.18,00,000 by "
            "20 March, 18-scheme portfolio'). Holdings reconstructed from a synthetic "
            "CAMS + KFintech consolidated account statement; governing rules preloaded "
            "so the loop runs offline. Exercises stacked-addendum RESOLVE (Axis "
            "Bluechip), a same-date tie -> abstain (Franklin Focused), a stale url-less "
            "rule -> HUNT (SBI Small Cap), ELSS lock-in breach -> repair (Mirae ELSS), "
            "and two-folio FIFO separation (Axis Bluechip, HDFC Top 100). Synthetic "
            "only - no real client data."
        ),
        "goal": {"amount": 1800000, "by_date": "2027-03-20", "mode": "A"},
        "candidate_schemes": CANDIDATES,
        "navs": NAVS,
        "holdings": build_holdings(),
        "constraints": build_constraints(),
    }


# --- a matching CAMS-style CAS PDF ----------------------------------------


def write_pdf(scenario: dict) -> None:
    try:
        from fpdf import FPDF
    except Exception as exc:  # fpdf2 is optional - the JSON is the runnable path
        print(f"(skipping PDF - fpdf2 not available: {exc})")
        return

    by_scheme: dict[str, list[dict]] = {}
    for l in scenario["holdings"]:
        by_scheme.setdefault(l["scheme_id"], []).append(l)
    name_of = {sid: n for sid, n, *_ in SCHEMES}
    amc_of = {sid: a for sid, _n, a, *_ in SCHEMES}
    nav_of = scenario["navs"]

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, "Consolidated Account Statement (SYNTHETIC - not a real statement)", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5, "Generated for Nikaas demo. No real investor, folio, PAN or holding.", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, "Statement period: 01-Apr-2020 to 10-Sep-2026   |   Source RTAs: CAMS, KFintech", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    total_val = 0.0
    for sid in CANDIDATES:
        lots = by_scheme.get(sid, [])
        if not lots:
            continue
        pdf.set_font("Helvetica", "B", 10)
        pdf.multi_cell(0, 5, f"{name_of[sid]}  ({amc_of[sid].upper()})   NAV {nav_of[sid]:.4f} as on 10-Sep-2026")
        pdf.set_font("Courier", "", 8)
        pdf.cell(0, 4, "  Folio            Txn Date     Units        Purchase NAV    Amount", new_x="LMARGIN", new_y="NEXT")
        sub_units = 0.0
        for l in lots:
            amt = l["units"] * l["purchase_nav"]
            sub_units += l["units"]
            pdf.cell(
                0, 4,
                f"  {l['folio']:<15} {l['purchase_date']}  {l['units']:>10,.3f}  {l['purchase_nav']:>13,.4f}  {amt:>13,.2f}",
                new_x="LMARGIN", new_y="NEXT",
            )
        cur_val = sub_units * nav_of[sid]
        total_val += cur_val
        pdf.set_font("Courier", "B", 8)
        pdf.cell(0, 4, f"  {'':<15} {'':<12} {sub_units:>10,.3f} units   current value  {cur_val:>13,.2f}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)

    pdf.set_font("Helvetica", "B", 10)
    pdf.ln(2)
    pdf.cell(0, 6, f"Portfolio current value (synthetic): Rs. {total_val:,.2f}", new_x="LMARGIN", new_y="NEXT")
    OUT_PDF.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(OUT_PDF))
    print(f"wrote {OUT_PDF}  ({OUT_PDF.stat().st_size} bytes)")


def build_mode_b_scenario() -> dict:
    """Same 18-scheme portfolio, run through Mode B - the agent scores every
    holding from the exit-load / expense-ratio / lock-in evidence itself, no
    human ticks anything. Good for showing the weighted-score panel at scale."""
    sc = build_scenario()
    sc["goal"] = {**sc["goal"], "mode": "B"}
    sc["description"] = (
        "The 18-scheme hard case run through Mode B instead of Mode A: no human "
        "ticks anything, agent/selector.build_mode_b_factors derives a transparent "
        "weighted merit score for every holding straight from its exit-load, "
        "expense-ratio and lock-in evidence. The index / liquid / corporate-bond "
        "schemes score highest (cheapest to hold and to leave) and are what the "
        "merit-preserving plan holds back. Synthetic only - no real client data."
    )
    return sc


def main() -> None:
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)

    scenario = build_scenario()
    OUT_JSON.write_text(json.dumps(scenario, indent=2))
    print(f"wrote {OUT_JSON}")
    print(f"  {len(scenario['candidate_schemes'])} schemes, "
          f"{len(scenario['holdings'])} lots, {len(scenario['constraints'])} constraints")

    mb = build_mode_b_scenario()
    mb_path = OUT_JSON.parent / "demo_mode_b_cas.json"
    mb_path.write_text(json.dumps(mb, indent=2))
    print(f"wrote {mb_path}  (Mode B)")

    write_pdf(scenario)


if __name__ == "__main__":
    main()
