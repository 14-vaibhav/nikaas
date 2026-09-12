from __future__ import annotations

from agent.reconcile import RawRow, reconcile

# A tiny stand-in AMFI directory (the real one is fetched/cached separately).
DIRECTORY = {
    "axis bluechip fund - direct plan - growth": {
        "scheme_code": "A1", "name": "Axis Bluechip Fund - Direct Plan - Growth",
        "amc": "Axis Mutual Fund", "amc_id": "axis", "isin": "INF1",
    },
    "hdfc top 100 fund - direct plan - growth": {
        "scheme_code": "H1", "name": "HDFC Top 100 Fund - Direct Plan - Growth",
        "amc": "HDFC Mutual Fund", "amc_id": "hdfc", "isin": "INF2",
    },
}


def _row(source, name, folio, units, d="2022-01-10", nav=10.0):
    return RawRow(source=source, scheme_name=name, folio=folio, units=units,
                  purchase_date=d, purchase_nav=nav)


def test_two_feed_spellings_resolve_to_one_scheme():
    rows = [
        _row("CAMS", "Axis Bluechip Fund - Direct Plan - Growth", "111 / 22", 100),
        _row("KFintech", "AXIS BLUECHIP FUND DIRECT GROWTH", "999000111", 50),
    ]
    r = reconcile(rows, DIRECTORY)
    assert r.kept_rows == 2
    assert set(r.resolved.values()) == {"A1"}


def test_exact_duplicate_lot_across_feeds_is_deduped():
    rows = [
        _row("CAMS", "HDFC Top 100 Fund - Direct Plan - Growth", "20 / 1", 300, "2021-05-01", 600.0),
        _row("KFintech", "HDFC Top 100 Fund - Direct Plan - Growth", "20 / 1", 300, "2021-05-01", 600.0),
    ]
    r = reconcile(rows, DIRECTORY)
    assert r.kept_rows == 1
    assert any(f.kind == "duplicate_lot" for f in r.flags)


def test_near_duplicate_is_flagged_for_a_human_and_both_kept():
    rows = [
        _row("CAMS", "HDFC Top 100 Fund - Direct Plan - Growth", "20 / 1", 300.0, "2021-05-01", 600.0),
        _row("KFintech", "HDFC Top 100 Fund - Direct Plan - Growth", "20 / 1", 300.4, "2021-05-01", 600.0),
    ]
    r = reconcile(rows, DIRECTORY)
    assert r.kept_rows == 2
    f = next(f for f in r.flags if f.kind == "near_duplicate")
    assert f.needs_human


def test_two_folios_of_one_scheme_stay_two_queues():
    rows = [
        _row("CAMS", "Axis Bluechip Fund - Direct Plan - Growth", "111 / 22", 100),
        _row("CAMS", "Axis Bluechip Fund - Direct Plan - Growth", "333 / 44", 100),
    ]
    r = reconcile(rows, DIRECTORY)
    folios = {(l.scheme_id, l.folio) for l in r.holdings.lots}
    assert folios == {("A1", "111 / 22"), ("A1", "333 / 44")}


def test_unknown_scheme_is_dropped_and_flagged_not_guessed():
    rows = [_row("CAMS", "Totally Made Up Global Opportunities Fund", "1/1", 10)]
    r = reconcile(rows, DIRECTORY)
    assert r.kept_rows == 0
    assert r.dropped_rows == 1
    assert any(f.kind == "unresolved_scheme" and f.needs_human for f in r.flags)


def test_malformed_lot_rejected_at_the_door():
    rows = [_row("CAMS", "Axis Bluechip Fund - Direct Plan - Growth", "1/1", -5)]
    r = reconcile(rows, DIRECTORY)
    assert r.kept_rows == 0
    assert any(f.kind == "malformed_lot" for f in r.flags)


def test_known_map_pins_identity_without_a_flag():
    rows = [_row("CAMS", "Axis Bluechip Fund Renamed 2026 Edition", "1/1", 10)]
    r = reconcile(rows, DIRECTORY, known={"Axis Bluechip Fund Renamed 2026 Edition": "A1"})
    assert r.resolved["Axis Bluechip Fund Renamed 2026 Edition"] == "A1"
    assert not r.flags
