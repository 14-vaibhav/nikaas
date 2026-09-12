"""Fallback merit heuristic for Mode A (diagram: ALLOCATE step 1 -
'drop funds worth keeping').

Mode B gives every fund a sourced, weighted score (`agent/selector.py`).
Mode A does not - the human only ticked a list. But the diagram's two
plans only diverge when "worth keeping" means something, so in Mode A we
derive a proxy from the holdings themselves:

  * how well the fund has actually compounded for this holder
    (value-weighted CAGR since purchase) - this is "your best fund";
  * how much of the position would be taxed as STCG if sold now
    (a reason to wait rather than sell today).

It is a heuristic, clearly labelled `merit_basis="ltcg_runway"`, and is
replaced wholesale the moment real Mode B factors are supplied.
"""

from __future__ import annotations

from datetime import date
from statistics import mean, pstdev

from agent.contracts import Candidate, Holdings

_LTCG_DAYS = 365
_CAGR_WEIGHT = 0.8
_STCG_WEIGHT = 0.2


def _years_between(earlier: str, as_of: date) -> float:
    return max((as_of - date.fromisoformat(earlier)).days / 365.25, 0.05)


def merit_scores(
    fund_ids: list[str], holdings: Holdings, navs: dict[str, float], as_of: date | None = None
) -> dict[str, Candidate]:
    """One `Candidate` per fund id, with `merit_score` in [0, 1]."""
    as_of = as_of or date.today()

    raw_cagr: dict[str, float] = {}
    stcg_fraction: dict[str, float] = {}
    for fund_id in fund_ids:
        lots = [l for l in holdings.lots if l.scheme_id == fund_id]
        nav = navs.get(fund_id)
        invested = sum(l.units * l.purchase_nav for l in lots)
        if not lots or nav is None or invested <= 0:
            raw_cagr[fund_id] = 0.0
            stcg_fraction[fund_id] = 0.0
            continue
        current = sum(l.units * nav for l in lots)
        wavg_years = sum(
            l.units * l.purchase_nav * _years_between(l.purchase_date, as_of) for l in lots
        ) / invested
        raw_cagr[fund_id] = (current / invested) ** (1 / wavg_years) - 1
        units_total = sum(l.units for l in lots)
        units_stcg = sum(
            l.units for l in lots
            if (as_of - date.fromisoformat(l.purchase_date)).days < _LTCG_DAYS
        )
        stcg_fraction[fund_id] = units_stcg / units_total if units_total else 0.0

    cagr_norm = _normalize(raw_cagr)

    out: dict[str, Candidate] = {}
    for fund_id in fund_ids:
        score = _CAGR_WEIGHT * cagr_norm[fund_id] + _STCG_WEIGHT * stcg_fraction[fund_id]
        out[fund_id] = Candidate(
            fund_id=fund_id,
            merit_score=round(score, 4),
            merit_basis="ltcg_runway",
        )
    return out


def top_merit_fund_ids(candidates: dict[str, Candidate]) -> set[str]:
    """The funds "worth keeping": those a clear margin above the pack.
    Empty when every fund scores the same (then the two plans coincide)."""
    scores = [c.merit_score for c in candidates.values()]
    if len(scores) < 2 or pstdev(scores) < 1e-6:
        return set()
    cutoff = mean(scores) + 0.5 * pstdev(scores)
    keep = {fid for fid, c in candidates.items() if c.merit_score >= cutoff}
    if not keep:  # spread exists but nothing cleared the cutoff - keep the single best
        best = max(candidates.values(), key=lambda c: c.merit_score)
        keep = {best.fund_id}
    return keep


def _normalize(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    lo, hi = min(values.values()), max(values.values())
    if hi - lo < 1e-9:
        return {k: 0.0 for k in values}
    return {k: (v - lo) / (hi - lo) for k, v in values.items()}
