from __future__ import annotations

from datetime import date, timedelta

from agent.contracts import Constraint, Source
from agent.evidence import EvidenceStore


def _c(cid, kind="exit_load", days_ago=0, effective="2025-01-01", value=None):
    retrieved = (date.today() - timedelta(days=days_ago)).isoformat() + "T00:00:00Z"
    return Constraint(
        id=cid, scheme_id="s_x", kind=kind, value=value or {"pct": 1.0, "window_days": 365},
        source=Source(doc="sid.pdf", page=1, clause="1", url="https://e.com/sid.pdf",
                      effective_date=effective, retrieved_at=retrieved),
        confidence="verified",
    )


def test_freshness_window_per_kind():
    ev = EvidenceStore()
    ev.put(_c("c1", "exit_load", days_ago=10))   # window 7 -> stale
    ev.put(_c("c2", "tax_rule", days_ago=10))    # window 30 -> fresh
    assert not ev.is_fresh("s_x", "exit_load")
    assert ev.is_fresh("s_x", "tax_rule")
    assert {e.constraint.id for e in ev.stale_entries()} == {"c1"}


def test_replace_fund_kind_swaps_the_group():
    ev = EvidenceStore()
    ev.put(_c("old", "exit_load", days_ago=30))
    ev.replace_fund_kind("s_x", "exit_load", [_c("new", "exit_load", days_ago=0)], "hash2")
    ids = {e.constraint.id for e in ev.for_fund("s_x")}
    assert ids == {"new"}
    assert ev.is_fresh("s_x", "exit_load")


def test_known_url_is_recoverable_for_refetch():
    ev = EvidenceStore()
    ev.put(_c("c1"))
    assert ev.known_url("s_x", "exit_load") == "https://e.com/sid.pdf"


def test_apply_supersession_writes_back():
    from dataclasses import replace

    ev = EvidenceStore()
    a = _c("a", effective="2024-01-01")
    b = _c("b", effective="2025-07-01")
    ev.put(a)
    ev.put(b)
    assert ev.active_count("s_x", "exit_load") == 2
    resolved = [replace(a, superseded_by="b"), replace(b, superseded_by=None)]
    ev.apply_supersession(resolved)
    assert ev.active_count("s_x", "exit_load") == 1
    assert ev.active_for("s_x", "exit_load").id == "b"
