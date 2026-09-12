"""Holdings ingest (diagram: the human's portfolio going into the loop).

Two folios of the same scheme are two legally-separate FIFO queues;
merging them produces a wrong plan that looks entirely plausible, so the
folio field is preserved exactly as given and the verifier's FIFO_MERGE
check depends on it.

Malformed input is rejected at the door - negative/zero units, or a
purchase date in the future. A clear message here beats a plan that
silently sells units that don't exist.
"""

from __future__ import annotations

from datetime import date

from agent.contracts import Holdings, Lot


class MalformedHoldingError(ValueError):
    """Raised at ingest, never allowed to reach the allocator."""


def load_holdings(raw_lots: list[dict]) -> Holdings:
    today = date.today()
    lots: list[Lot] = []
    for raw in raw_lots:
        if raw["units"] <= 0:
            raise MalformedHoldingError(
                f"{raw.get('lot_id', '?')}: units must be positive, got {raw['units']}"
            )
        purchase_date = date.fromisoformat(raw["purchase_date"])
        if purchase_date > today:
            raise MalformedHoldingError(
                f"{raw.get('lot_id', '?')}: purchase_date {raw['purchase_date']} is in the future"
            )
        lots.append(Lot(
            lot_id=raw["lot_id"], scheme_id=raw["scheme_id"], folio=raw["folio"],
            units=raw["units"], purchase_date=raw["purchase_date"],
            purchase_nav=raw["purchase_nav"],
        ))
    return Holdings(lots=lots)
