"""Unit tests for verifier.py — the only file where tests pay for
themselves this week (Build Spec A §2 item 1)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from agent.contracts import (
    Abstention,
    Constraint,
    Goal,
    Holdings,
    Lot,
    Plan,
    PlanLeg,
    Source,
    Totals,
)
from agent.verifier import verify, verify_mode_b_factors


def _source(effective_date: str, retrieved_at_offset_days: int = 0, doc="sid.pdf", page=7, clause="7.2"):
    retrieved_at = (date.today() - timedelta(days=retrieved_at_offset_days)).isoformat() + "T10:00:00Z"
    return Source(
        doc=doc, page=page, clause=clause, url="https://example.com/sid.pdf",
        effective_date=effective_date, retrieved_at=retrieved_at,
    )


def _exit_load_constraint(scheme_id, pct=1.0, window_days=365, superseded_by=None, stale_days=0):
    return Constraint(
        id=f"c_{scheme_id}_exitload", scheme_id=scheme_id, kind="exit_load",
        value={"pct": pct, "window_days": window_days},
        source=_source("2025-01-01", stale_days), confidence="verified",
        superseded_by=superseded_by,
    )


def _tax_rule_constraint(scheme_id):
    return Constraint(
        id=f"c_{scheme_id}_tax", scheme_id=scheme_id, kind="tax_rule",
        value={"ltcg_threshold_days": 365, "ltcg_rate": 0.125, "stcg_rate": 0.20, "ltcg_exemption": 125_000},
        source=_source("2025-01-01"), confidence="verified",
    )


def _lock_in_constraint(scheme_id, window_days=400):
    return Constraint(
        id=f"c_{scheme_id}_lockin", scheme_id=scheme_id, kind="lock_in",
        value={"window_days": window_days},
        source=_source("2025-01-01"), confidence="verified",
    )


def _base_plan(**overrides):
    defaults = dict(
        plan_id="p_test",
        goal=Goal(amount=15000, by_date="2026-09-10", mode="A"),
        legs=[PlanLeg(
            scheme_id="s_axis", folio="F1", units=1000, gross=15000,
            exit_load=0, tax=0, net=15000, sell_date="2026-09-01",
            constraints_applied=["c_s_axis_exitload", "c_s_axis_tax"],
        )],
        totals=Totals(gross=15000, exit_load=0, tax=0, net=15000),
        abstained=[],
        verdict="verified",
        violations=[],
    )
    defaults.update(overrides)
    return Plan(**defaults)


def _base_holdings():
    return Holdings(lots=[
        Lot(lot_id="l1", scheme_id="s_axis", folio="F1", units=5000,
            purchase_date="2024-01-01", purchase_nav=10.0),
    ])


def test_valid_plan_has_no_violations():
    plan = _base_plan()
    constraints = [_exit_load_constraint("s_axis"), _tax_rule_constraint("s_axis")]
    violations = verify(plan, constraints, _base_holdings())
    assert violations == []


def test_shortfall_detected_when_target_not_met():
    plan = _base_plan(goal=Goal(amount=20000, by_date="2026-09-10", mode="A"))
    constraints = [_exit_load_constraint("s_axis"), _tax_rule_constraint("s_axis")]
    violations = verify(plan, constraints, _base_holdings())
    assert any(v.code == "SHORTFALL" for v in violations)


def test_lock_in_breach_detected():
    plan = _base_plan(legs=[PlanLeg(
        scheme_id="s_axis", folio="F1", units=1000, gross=15000,
        exit_load=0, tax=0, net=15000, sell_date="2026-09-01",
        constraints_applied=["c_s_axis_exitload", "c_s_axis_tax", "c_s_axis_lockin"],
    )])
    holdings = Holdings(lots=[
        Lot(lot_id="l1", scheme_id="s_axis", folio="F1", units=5000,
            purchase_date="2026-08-01", purchase_nav=10.0),  # ~31 days held
    ])
    constraints = [
        _exit_load_constraint("s_axis"),
        _tax_rule_constraint("s_axis"),
        _lock_in_constraint("s_axis", window_days=400),
    ]
    violations = verify(plan, constraints, holdings)
    assert any(v.code == "LOCK_IN_BREACH" for v in violations)


def test_fifo_merge_flagged_when_folio_alone_is_insufficient():
    plan = _base_plan(legs=[PlanLeg(
        scheme_id="s_axis", folio="F1", units=1000, gross=15000,
        exit_load=0, tax=0, net=15000, sell_date="2026-09-01",
        constraints_applied=["c_s_axis_exitload", "c_s_axis_tax"],
    )])
    holdings = Holdings(lots=[
        Lot(lot_id="l1", scheme_id="s_axis", folio="F1", units=200,
            purchase_date="2024-01-01", purchase_nav=10.0),
        Lot(lot_id="l2", scheme_id="s_axis", folio="F2", units=5000,
            purchase_date="2024-01-01", purchase_nav=10.0),
    ])
    constraints = [_exit_load_constraint("s_axis"), _tax_rule_constraint("s_axis")]
    violations = verify(plan, constraints, holdings)
    assert any(v.code == "FIFO_MERGE" for v in violations)


def test_shortfall_flagged_when_no_folio_has_enough_units_anywhere():
    plan = _base_plan()
    holdings = Holdings(lots=[
        Lot(lot_id="l1", scheme_id="s_axis", folio="F1", units=200,
            purchase_date="2024-01-01", purchase_nav=10.0),
    ])
    constraints = [_exit_load_constraint("s_axis"), _tax_rule_constraint("s_axis")]
    violations = verify(plan, constraints, holdings)
    assert any(v.code == "SHORTFALL" for v in violations)
    assert not any(v.code == "FIFO_MERGE" for v in violations)


def test_no_provenance_when_constraint_id_unresolvable():
    plan = _base_plan(legs=[PlanLeg(
        scheme_id="s_axis", folio="F1", units=1000, gross=15000,
        exit_load=0, tax=0, net=15000, sell_date="2026-09-01",
        constraints_applied=["c_does_not_exist"],
    )])
    constraints = [_tax_rule_constraint("s_axis")]
    violations = verify(plan, constraints, _base_holdings())
    assert any(v.code == "NO_PROVENANCE" for v in violations)


def test_stale_source_flagged_beyond_freshness_threshold():
    plan = _base_plan()
    constraints = [
        _exit_load_constraint("s_axis", stale_days=30),  # exit_load threshold is 7 days
        _tax_rule_constraint("s_axis"),
    ]
    violations = verify(plan, constraints, _base_holdings())
    assert any(v.code == "STALE_SOURCE" for v in violations)


def test_superseded_constraint_flagged_when_applied_anyway():
    plan = _base_plan()
    constraints = [
        _exit_load_constraint("s_axis", superseded_by="c_s_axis_exitload_v2"),
        _tax_rule_constraint("s_axis"),
    ]
    violations = verify(plan, constraints, _base_holdings())
    assert any(v.code == "SUPERSEDED" for v in violations)


def test_silent_drop_when_scheme_absent_from_legs_and_abstained():
    plan = _base_plan()  # only mentions s_axis
    holdings = Holdings(lots=[
        Lot(lot_id="l1", scheme_id="s_axis", folio="F1", units=5000,
            purchase_date="2024-01-01", purchase_nav=10.0),
        Lot(lot_id="l2", scheme_id="s_other", folio="F1", units=1000,
            purchase_date="2024-01-01", purchase_nav=10.0),
    ])
    constraints = [_exit_load_constraint("s_axis"), _tax_rule_constraint("s_axis")]
    violations = verify(plan, constraints, holdings)
    assert any(v.code == "SILENT_DROP" and "s_other" in v.detail for v in violations)


def test_silent_drop_absent_when_scheme_is_abstained():
    plan = _base_plan(abstained=[Abstention(scheme_id="s_other", reason="SID unreachable")])
    holdings = Holdings(lots=[
        Lot(lot_id="l1", scheme_id="s_axis", folio="F1", units=5000,
            purchase_date="2024-01-01", purchase_nav=10.0),
        Lot(lot_id="l2", scheme_id="s_other", folio="F1", units=1000,
            purchase_date="2024-01-01", purchase_nav=10.0),
    ])
    constraints = [_exit_load_constraint("s_axis"), _tax_rule_constraint("s_axis")]
    violations = verify(plan, constraints, holdings)
    assert not any(v.code == "SILENT_DROP" for v in violations)


def test_ltcg_exemption_applied_once_across_plan():
    # Two legs, each with LTCG gain of 100,000. Combined gain (200,000) exceeds
    # the single 125,000 exemption, so tax must be charged on the excess only.
    plan = _base_plan(
        goal=Goal(amount=1, by_date="2026-09-10", mode="A"),
        legs=[
            PlanLeg(scheme_id="s_axis", folio="F1", units=1000, gross=110_000,
                    exit_load=0, tax=0, net=110_000, sell_date="2026-09-01",
                    constraints_applied=["c_s_axis_exitload", "c_s_axis_tax"]),
            PlanLeg(scheme_id="s_hdfc", folio="F1", units=1000, gross=110_000,
                    exit_load=0, tax=0, net=110_000, sell_date="2026-09-01",
                    constraints_applied=["c_s_hdfc_exitload", "c_s_hdfc_tax"]),
        ],
        totals=Totals(gross=220_000, exit_load=0, tax=0, net=220_000),
    )
    holdings = Holdings(lots=[
        Lot(lot_id="l1", scheme_id="s_axis", folio="F1", units=5000,
            purchase_date="2024-01-01", purchase_nav=10.0),
        Lot(lot_id="l2", scheme_id="s_hdfc", folio="F1", units=5000,
            purchase_date="2024-01-01", purchase_nav=10.0),
    ])
    constraints = [
        _exit_load_constraint("s_axis"), _tax_rule_constraint("s_axis"),
        _exit_load_constraint("s_hdfc"), _tax_rule_constraint("s_hdfc"),
    ]
    violations = verify(plan, constraints, holdings)
    # plan claims tax=0, but 200,000 gain - 125,000 exemption = 75,000 taxable @ 12.5% = 9,375
    shortfalls = [v for v in violations if v.code == "SHORTFALL" and "tax" in v.detail.lower()]
    assert shortfalls, f"expected a tax-mismatch SHORTFALL, got: {violations}"


def test_verify_mode_b_factors_flags_missing_source():
    candidates = [{
        "scheme_id": "s_axis",
        "factors": [
            {"name": "expense_ratio", "value": 1.2, "weight": 0.3,
             "source": {"doc": "factsheet.pdf", "effective_date": "2026-06-01"}},
            {"name": "manager_change", "value": True, "weight": 0.2, "source": None},
        ],
    }]
    violations = verify_mode_b_factors(candidates)
    assert len(violations) == 1
    assert violations[0].code == "NO_PROVENANCE"
    assert "manager_change" in violations[0].detail
