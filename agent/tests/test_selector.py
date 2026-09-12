from __future__ import annotations

from agent.contracts import Constraint, Source
from agent.evidence import EvidenceStore
from agent.selector import build_mode_b_factors, select, select_mode_b


def _constraint(scheme_id: str, kind: str, value: dict, effective_date="2025-01-01") -> Constraint:
    return Constraint(
        id=f"c_{scheme_id}_{kind}",
        scheme_id=scheme_id,
        kind=kind,
        value=value,
        source=Source(
            doc="sid.pdf", page=1, clause="1", url=None,
            effective_date=effective_date, retrieved_at="2026-09-01T00:00:00Z",
        ),
        confidence="verified",
    )


def test_select_mode_b_weights_and_sums():
    raw = [{
        "scheme_id": "s_a",
        "factors": [
            {"name": "exit_load", "value": 0.6, "weight": 0.4,
             "source": {"effective_date": "2025-01-01"}},
            {"name": "expense_ratio", "value": 0.5, "weight": 0.4,
             "source": {"effective_date": "2025-01-01"}},
        ],
    }]
    [result] = select_mode_b(raw)
    assert result.score == round(0.6 * 0.4 + 0.5 * 0.4, 4)
    assert result.omitted_factors == []


def test_select_mode_b_omits_unsourced_factor():
    raw = [{
        "scheme_id": "s_a",
        "factors": [
            {"name": "exit_load", "value": 0.6, "weight": 0.4, "source": None},
            {"name": "expense_ratio", "value": 0.5, "weight": 0.4,
             "source": {"effective_date": "2025-01-01"}},
        ],
    }]
    [result] = select_mode_b(raw)
    assert result.omitted_factors == ["exit_load"]
    assert result.score == round(0.5 * 0.4, 4)


def test_build_mode_b_factors_prefers_lower_cost_fund():
    evidence = EvidenceStore()
    evidence.put_many([
        _constraint("s_cheap", "exit_load", {"pct": 0.0, "window_days": 0}),
        _constraint("s_cheap", "expense_ratio", {"pct": 1.0}),
        _constraint("s_pricey", "exit_load", {"pct": 1.0, "window_days": 365}),
        _constraint("s_pricey", "expense_ratio", {"pct": 2.5}),
    ])
    raw = build_mode_b_factors(["s_cheap", "s_pricey"], evidence)
    results = {r.scheme_id: r for r in select_mode_b(raw)}
    assert results["s_cheap"].score > results["s_pricey"].score
    # both lack a lock_in constraint - reported as omitted, not guessed at
    assert results["s_cheap"].omitted_factors == ["lock_in"]


def test_build_mode_b_factors_omits_missing_expense_ratio():
    evidence = EvidenceStore()
    evidence.put_many([_constraint("s_a", "exit_load", {"pct": 1.0, "window_days": 365})])
    raw = build_mode_b_factors(["s_a"], evidence)
    [result] = select_mode_b(raw)
    assert set(result.omitted_factors) == {"expense_ratio", "lock_in"}


def test_select_mode_b_end_to_end_ranks_by_merit(mode_b):
    evidence = EvidenceStore()
    evidence.put_many(mode_b["constraints"], "seed")
    raw = build_mode_b_factors(mode_b["funds"], evidence)
    candidates = {
        c.fund_id: c
        for c in select("B", mode_b["funds"], mode_b["holdings"], mode_b["navs"], raw)
    }
    assert candidates["s_axis_bluechip"].merit_score > candidates["s_hdfc_top100"].merit_score
    assert candidates["s_axis_bluechip"].merit_basis == "mode_b_weighted"
