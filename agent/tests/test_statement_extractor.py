from __future__ import annotations

from agent.statement_extractor import extract_statement, extract_statement_page


class FakeClient:
    def __init__(self, lots: list[dict]):
        self._lots = lots

    def extract(self, system, page_text, tool):
        return {"lots": self._lots}


def test_extract_statement_page_keeps_complete_rows():
    client = FakeClient([{
        "scheme_name": "Axis Bluechip Fund", "folio": "F001", "units": 100.0,
        "purchase_date": "2022-01-15", "purchase_nav": 45.0,
    }])
    lots = extract_statement_page(client, "some statement text")
    assert len(lots) == 1
    assert lots[0].folio == "F001"
    assert lots[0].units == 100.0


def test_extract_statement_page_drops_incomplete_rows():
    client = FakeClient([
        {"scheme_name": "Axis Bluechip Fund", "folio": "F001"},  # missing units/date/nav
        {"scheme_name": "HDFC Top 100", "folio": "F010", "units": 50.0,
         "purchase_date": "2021-03-10", "purchase_nav": 700.0},
    ])
    lots = extract_statement_page(client, "some statement text")
    assert len(lots) == 1
    assert lots[0].scheme_name == "HDFC Top 100"


def test_extract_statement_skips_blank_pages():
    client = FakeClient([{
        "scheme_name": "Axis Bluechip Fund", "folio": "F001", "units": 100.0,
        "purchase_date": "2022-01-15", "purchase_nav": 45.0,
    }])
    lots = extract_statement(client, ["", "   ", "real page text"])
    assert len(lots) == 1
