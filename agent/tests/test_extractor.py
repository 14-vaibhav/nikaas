from __future__ import annotations

from agent.extractor import _value_is_usable, extract_page


class FakeClient:
    def __init__(self, constraints):
        self._constraints = constraints

    def extract(self, system, page_text, tool):
        return {"constraints": self._constraints}


def test_constraint_without_clause_is_dropped():
    client = FakeClient([
        {"kind": "exit_load", "pct": 1.0, "window_days": 365, "clause": "7.2", "confidence": "verified"},
        {"kind": "lock_in", "window_days": 1095, "clause": None, "confidence": "inferred"},
    ])
    constraints = extract_page(
        client, "sid.pdf", 14, "some page text", "s_axis",
        effective_date_fallback="2025-01-01", retrieved_at="2026-09-05T00:00:00Z",
    )
    assert len(constraints) == 1
    assert constraints[0].kind == "exit_load"
    assert constraints[0].value == {"pct": 1.0, "window_days": 365}
    assert constraints[0].source.clause == "7.2"
    assert constraints[0].source.page == 14


def test_constraint_with_no_numeric_fields_populated_is_dropped():
    """Regression: observed live against Gemini's function-calling, which
    reliably filled a declared top-level field like `clause` but left an
    undeclared nested `value: object` empty. The fix flattened the schema;
    this pins that a constraint whose kind-specific fields never arrived
    is rejected exactly like a missing clause, not silently read as 0%."""
    client = FakeClient([
        {"kind": "exit_load", "clause": "7.2", "confidence": "verified"},  # no pct, no window_days
    ])
    constraints = extract_page(
        client, "sid.pdf", 3, "An exit load of 1% is payable ...", "s_quant",
        effective_date_fallback="2025-01-01", retrieved_at="2026-09-05T00:00:00Z",
    )
    assert constraints == []


def test_exit_load_partially_populated_is_still_dropped():
    """pct with no window_days (or vice versa) is exactly as unusable as
    neither - the allocator needs both to apply the rule correctly."""
    client = FakeClient([
        {"kind": "exit_load", "pct": 1.0, "clause": "7.2", "confidence": "verified"},
    ])
    constraints = extract_page(
        client, "sid.pdf", 3, "page text", "s_quant",
        effective_date_fallback="2025-01-01", retrieved_at="2026-09-05T00:00:00Z",
    )
    assert constraints == []


def test_a_genuine_zero_is_not_mistaken_for_a_missing_field():
    """pct=0 (a real nil-load rule) must survive - only an actually
    missing/None field should be rejected."""
    client = FakeClient([
        {"kind": "exit_load", "pct": 0, "window_days": 0, "clause": "5.1", "confidence": "verified"},
    ])
    constraints = extract_page(
        client, "sid.pdf", 11, "no exit load", "s_index",
        effective_date_fallback="2025-01-01", retrieved_at="2026-09-05T00:00:00Z",
    )
    assert len(constraints) == 1
    assert constraints[0].value == {"pct": 0, "window_days": 0}


def test_tax_rule_only_needs_something_not_a_fixed_shape():
    client = FakeClient([
        {"kind": "tax_rule", "ltcg_rate": 0.125, "clause": "112A", "confidence": "verified"},
    ])
    constraints = extract_page(
        client, "sid.pdf", 1, "page text", "s_axis",
        effective_date_fallback="2025-01-01", retrieved_at="2026-09-05T00:00:00Z",
    )
    assert len(constraints) == 1
    assert constraints[0].value == {"ltcg_rate": 0.125}


def test_value_is_usable_directly():
    assert _value_is_usable("exit_load", {"pct": 1.0, "window_days": 365})
    assert not _value_is_usable("exit_load", {"pct": 1.0})
    assert not _value_is_usable("exit_load", {})
    assert _value_is_usable("exit_load", {"pct": 0, "window_days": 0})
    assert not _value_is_usable("lock_in", {})
    assert _value_is_usable("lock_in", {"window_days": 1095})
    assert not _value_is_usable("tax_rule", {})
    assert _value_is_usable("tax_rule", {"ltcg_rate": 0.125})
