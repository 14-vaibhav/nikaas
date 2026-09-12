from __future__ import annotations

from datetime import date, timedelta

import pytest

from agent.holdings import MalformedHoldingError, load_holdings


def _lot(**over):
    base = dict(lot_id="l1", scheme_id="s_x", folio="F1", units=100,
               purchase_date="2022-01-01", purchase_nav=10.0)
    base.update(over)
    return base


def test_valid_holdings_load():
    h = load_holdings([_lot(), _lot(lot_id="l2", folio="F2")])
    assert len(h.lots) == 2
    assert h.total_units("s_x") == 200


def test_negative_units_rejected_at_ingest():
    with pytest.raises(MalformedHoldingError):
        load_holdings([_lot(units=-5)])


def test_future_purchase_date_rejected_at_ingest():
    future = (date.today() + timedelta(days=30)).isoformat()
    with pytest.raises(MalformedHoldingError):
        load_holdings([_lot(purchase_date=future)])


def test_two_folios_stay_separate():
    h = load_holdings([_lot(folio="F1", units=100), _lot(lot_id="l2", folio="F2", units=100)])
    assert len(h.for_folio("s_x", "F1")) == 1
    assert len(h.for_folio("s_x", "F2")) == 1
