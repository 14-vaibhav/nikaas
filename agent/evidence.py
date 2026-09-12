"""The evidence store (diagram: the box under HUNTER - 'source, page, clause,
effective_date, retrieved_at').

Every fact the loop acts on lives here as an `Evidence`: a `Constraint`
(which already carries its `Source`) plus the SHA-256 of the document it
was read from. Two jobs:

  * freshness - is what we hold for (fund, kind) still inside its window,
    or does DECIDE need to re-fetch / re-hunt?
  * change detection - the Watcher compares `content_hash` to notice a
    document being edited under a live plan.

In-memory by default. Persistence across process restarts is `store.py`'s
job; this class stays a plain object so the loop and the tests can use it
without a database.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional

from agent.contracts import Constraint, Evidence

# Freshness thresholds per constraint kind. Exit loads and lock-ins change
# rarely; tax rules move with Finance Acts; but an addendum can land any
# day, so the windows stay short and the Watcher re-checks.
FRESHNESS_DAYS: dict[str, int] = {
    "exit_load": 7,
    "lock_in": 7,
    "tax_rule": 30,
    "cutoff": 7,
    "minimum": 30,
    "expense_ratio": 90,
}
DEFAULT_FRESHNESS_DAYS = 7


def _as_date(value: str) -> date:
    return date.fromisoformat(value[:10])


class EvidenceStore:
    def __init__(self, entries: Optional[Iterable[Evidence]] = None):
        self._entries: list[Evidence] = list(entries or [])

    # --- writes ----------------------------------------------------------

    def put(self, constraint: Constraint, content_hash: str = "") -> Evidence:
        """Add or replace the entry with this constraint id."""
        ev = Evidence(
            constraint=constraint,
            content_hash=content_hash,
            retrieved_at=constraint.source.retrieved_at,
        )
        self._entries = [e for e in self._entries if e.constraint.id != constraint.id]
        self._entries.append(ev)
        return ev

    def put_many(self, constraints: Iterable[Constraint], content_hash: str = "") -> list[Evidence]:
        return [self.put(c, content_hash) for c in constraints]

    def replace_fund_kind(
        self, fund_id: str, kind: str, constraints: Iterable[Constraint], content_hash: str = ""
    ) -> list[Evidence]:
        """After a re-hunt: drop everything we held for (fund, kind) and
        write the freshly retrieved constraints in its place."""
        self._entries = [
            e for e in self._entries
            if not (e.constraint.scheme_id == fund_id and e.constraint.kind == kind)
        ]
        return self.put_many(constraints, content_hash)

    def drop(self, constraint_id: str) -> None:
        self._entries = [e for e in self._entries if e.constraint.id != constraint_id]

    def apply_supersession(self, resolved: list[Constraint]) -> None:
        """Write `superseded_by` back onto stored constraints after the
        resolver has ordered a (fund, kind) group."""
        by_id = {c.id: c for c in resolved}
        new: list[Evidence] = []
        for e in self._entries:
            r = by_id.get(e.constraint.id)
            if r is not None:
                new.append(replace(e, constraint=r))
            else:
                new.append(e)
        self._entries = new

    # --- reads ---------------------------------------------------------

    def all(self) -> list[Evidence]:
        return list(self._entries)

    def constraints(self) -> list[Constraint]:
        """Flat list the resolver / allocator / verifier consume."""
        return [e.constraint for e in self._entries]

    def for_fund(self, fund_id: str) -> list[Evidence]:
        return [e for e in self._entries if e.constraint.scheme_id == fund_id]

    def group(self, fund_id: str, kind: str) -> list[Constraint]:
        return [
            e.constraint for e in self._entries
            if e.constraint.scheme_id == fund_id and e.constraint.kind == kind
        ]

    def has(self, fund_id: str, kind: str) -> bool:
        return bool(self.group(fund_id, kind))

    def active_for(self, fund_id: str, kind: str) -> Optional[Constraint]:
        """The not-yet-superseded constraint for this (fund, kind). If more
        than one is active the group is ambiguous - callers that need to
        *detect* that use `active_count`, not this."""
        active = [c for c in self.group(fund_id, kind) if c.superseded_by is None]
        if not active:
            return None
        return max(active, key=lambda c: c.source.effective_date)

    def active_count(self, fund_id: str, kind: str) -> int:
        return len([c for c in self.group(fund_id, kind) if c.superseded_by is None])

    def known_url(self, fund_id: str, kind: str) -> Optional[str]:
        """A URL we've fetched before for this (fund, kind) - the target of
        a REFETCH route."""
        for c in self.group(fund_id, kind):
            if c.source and c.source.url:
                return c.source.url
        return None

    # --- freshness ---------------------------------------------------

    def is_fresh(self, fund_id: str, kind: str, today: Optional[date] = None) -> bool:
        c = self.active_for(fund_id, kind)
        if c is None:
            return False
        return not self._stale(c, today or date.today())

    def stale_entries(self, today: Optional[date] = None) -> list[Evidence]:
        d = today or date.today()
        return [e for e in self._entries if self._stale(e.constraint, d)]

    @staticmethod
    def _stale(constraint: Constraint, today: date) -> bool:
        threshold = FRESHNESS_DAYS.get(constraint.kind, DEFAULT_FRESHNESS_DAYS)
        return (today - _as_date(constraint.source.retrieved_at)) > timedelta(days=threshold)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
