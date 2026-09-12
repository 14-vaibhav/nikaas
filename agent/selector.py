"""Step 0 - which funds are candidates? (diagram: 'MODE A - human ticks' /
'MODE B - agent scores').

Mode A: the human ticked a list. This passes it through untouched; the
merit score each candidate carries for the two-plan split comes from the
`agent/merit.py` proxy.

Mode B: the agent shortlists from sourced, dated evidence and the score is
a transparent weighted sum over those factors - never a model verdict. A
factor with no retrievable source is omitted from the score and reported
as omitted.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from agent.contracts import Candidate, Constraint, Holdings, Mode
from agent.evidence import EvidenceStore
from agent.merit import merit_scores

# Mode B's weighted sum (diagram: "sourced evidence, weights shown"). Exit
# load and expense ratio are weighted equally as the two costs a holder
# actually pays; lock-in gets a smaller weight since it constrains timing,
# not money. Returns / risk-adjusted metrics are deliberately absent: no
# SID or addendum states them, so scoring on them would mean a number with
# no citation - the diagram's "sourced evidence" rule forbids that.
MODE_B_FACTOR_WEIGHTS: dict[str, float] = {
    "exit_load": 0.4,
    "expense_ratio": 0.4,
    "lock_in": 0.2,
}
_EXIT_LOAD_SCALE_PCT = 3.0       # exit loads above this are treated as worst-case
_EXPENSE_RATIO_SCALE_PCT = 3.0  # TERs above this are treated as worst-case
_LOCK_IN_SCALE_DAYS = 1095       # 3 years - the longest lock-in this scoring has seen (ELSS)


@dataclass
class ScoreFactor:
    name: str
    value: float
    weight: float
    source: dict | None  # {"doc","page","clause","url","effective_date","retrieved_at"} or None


@dataclass
class ModeBResult:
    scheme_id: str
    factors: list[ScoreFactor]
    score: float
    omitted_factors: list[str] = field(default_factory=list)


def select(
    mode: Mode,
    fund_ids: list[str],
    holdings: Holdings,
    navs: dict[str, float],
    raw_mode_b: list[dict] | None = None,
) -> list[Candidate]:
    """The loop's entry into step 0. Returns one `Candidate` per fund in
    scope, each carrying the merit score the allocator uses to decide
    which funds are 'worth keeping'."""
    if mode == "A":
        scored = merit_scores(fund_ids, holdings, navs)
        return [scored[f] for f in fund_ids]

    results = select_mode_b(raw_mode_b or [])
    return [
        Candidate(
            fund_id=r.scheme_id,
            merit_score=r.score,
            merit_basis="mode_b_weighted",
            omitted_factors=r.omitted_factors,
            factors=[asdict(f) for f in r.factors],
        )
        for r in results
    ]


def select_mode_a(scheme_ids: list[str]) -> list[str]:
    """Pass through untouched. The merit judgment is the licensed human's;
    the agent works strictly within this set."""
    return list(scheme_ids)


def select_mode_b(raw_candidates: list[dict]) -> list[ModeBResult]:
    """`raw_candidates` is one dict per scheme:
        {"scheme_id": ..., "factors": [{"name","value","weight","source"}, ...]}

    A factor whose source is missing or lacks an effective_date is dropped
    from the score and listed in `omitted_factors` - never scored on
    silently."""
    results: list[ModeBResult] = []
    for raw in raw_candidates:
        scored_factors: list[ScoreFactor] = []
        omitted: list[str] = []
        score = 0.0
        for f in raw.get("factors", []):
            source = f.get("source")
            if not source or not source.get("effective_date"):
                omitted.append(f["name"])
                continue
            factor = ScoreFactor(name=f["name"], value=f["value"], weight=f["weight"], source=source)
            scored_factors.append(factor)
            score += factor.value * factor.weight
        results.append(ModeBResult(
            scheme_id=raw["scheme_id"], factors=scored_factors,
            score=round(score, 4), omitted_factors=omitted,
        ))
    return sorted(results, key=lambda c: c.score, reverse=True)


def build_mode_b_factors(fund_ids: list[str], evidence: EvidenceStore) -> list[dict]:
    """Turns whatever the evidence store already holds for each candidate
    into the input `select_mode_b` scores against `MODE_B_FACTOR_WEIGHTS`.

    Lower exit load and lower expense ratio score higher; no lock-in scores
    higher than a long one. A fund with no active constraint of a given
    kind gets that factor with `source=None`, so `select_mode_b`'s existing
    omission path drops it and reports it - never a silently invented
    number."""
    out: list[dict] = []
    for fund_id in fund_ids:
        out.append({
            "scheme_id": fund_id,
            "factors": [
                _mode_b_factor(
                    "exit_load", evidence.active_for(fund_id, "exit_load"),
                    MODE_B_FACTOR_WEIGHTS["exit_load"], _EXIT_LOAD_SCALE_PCT,
                ),
                _mode_b_factor(
                    "expense_ratio", evidence.active_for(fund_id, "expense_ratio"),
                    MODE_B_FACTOR_WEIGHTS["expense_ratio"], _EXPENSE_RATIO_SCALE_PCT,
                ),
                _mode_b_lock_in_factor(evidence.active_for(fund_id, "lock_in")),
            ],
        })
    return out


def _mode_b_factor(
    name: str, constraint: Constraint | None, weight: float, scale_pct: float,
) -> dict:
    if constraint is None:
        return {"name": name, "value": 0.0, "weight": weight, "source": None}
    pct = constraint.value.get("pct", 0.0)
    return {
        "name": name, "value": _goodness(pct, scale_pct), "weight": weight,
        "source": asdict(constraint.source),
    }


def _mode_b_lock_in_factor(constraint: Constraint | None) -> dict:
    weight = MODE_B_FACTOR_WEIGHTS["lock_in"]
    if constraint is None:
        # No lock-in on record is itself a fact worth scoring - but with
        # nothing to cite, it is reported as omitted rather than assumed.
        return {"name": "lock_in", "value": 0.0, "weight": weight, "source": None}
    window_days = constraint.value.get("window_days", 0)
    return {
        "name": "lock_in", "value": _goodness(window_days, _LOCK_IN_SCALE_DAYS),
        "weight": weight, "source": asdict(constraint.source),
    }


def _goodness(value: float, scale: float) -> float:
    """1.0 = best (zero cost / no lock-in), 0.0 = at or beyond `scale`."""
    return round(max(0.0, min(1.0, 1 - value / scale)), 4) if scale else 1.0
