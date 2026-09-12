from __future__ import annotations

from agent.holdings import MalformedHoldingError
from agent.intake import run_intake

AMFI_DIRECTORY = {
    "axis bluechip fund": {
        "scheme_code": "120503", "name": "Axis Bluechip Fund", "amc": "Axis Mutual Fund",
        "amc_id": "axis", "isin": "INF846K01131", "nav": 62.35, "nav_date": "01-Sep-2026",
    },
}


class FakeClient:
    """Dispatches on tool name, then picks a canned response by looking
    for a marker substring in the page text - mirrors test_hunter.py's
    `_FakeHTTP` url-fragment matching."""

    def __init__(self, identity_by_marker=None, constraints_by_marker=None, lots_by_marker=None):
        self.identity_by_marker = identity_by_marker or {}
        self.constraints_by_marker = constraints_by_marker or {}
        self.lots_by_marker = lots_by_marker or {}

    def extract(self, system, page_text, tool):
        name = tool["name"]
        if name == "record_document_identity":
            for marker, resp in self.identity_by_marker.items():
                if marker in page_text:
                    return resp
            return {"doc_type": "other", "document_kind": "other"}
        if name == "record_constraints":
            for marker, resp in self.constraints_by_marker.items():
                if marker in page_text:
                    return {"constraints": resp}
            return {"constraints": []}
        if name == "record_purchase_lots":
            for marker, resp in self.lots_by_marker.items():
                if marker in page_text:
                    return {"lots": resp}
            return {"lots": []}
        return {}


def _fake_parse_pdf(monkeypatch, pages_by_filename: dict[str, list[str]]):
    monkeypatch.setattr("agent.intake.parse_pdf", lambda content: pages_by_filename[content])


def test_fund_document_is_resolved_and_mined_for_constraints(monkeypatch):
    _fake_parse_pdf(monkeypatch, {b"sid.pdf": ["AXIS_SID cover page naming Axis Bluechip Fund"]})
    client = FakeClient(
        identity_by_marker={
            "AXIS_SID": {"doc_type": "fund_document", "document_kind": "sid", "scheme_name": "Axis Bluechip Fund"},
        },
        constraints_by_marker={
            "AXIS_SID": [{"kind": "exit_load", "value": {"pct": 1.0, "window_days": 365},
                          "clause": "7.2", "confidence": "verified"}],
        },
    )
    result = run_intake([("sid.pdf", b"sid.pdf")], extraction_client=client, amfi_directory=AMFI_DIRECTORY)

    assert len(result.funds) == 1
    fund = result.funds[0]
    assert fund.scheme_id == "120503"
    assert fund.resolved
    assert fund.nav == 62.35
    assert result.navs["120503"] == 62.35
    assert [c.kind for c in result.constraints] == ["exit_load"]
    assert not result.warnings


def test_statement_lot_matches_a_fund_document_uploaded_alongside_it(monkeypatch):
    _fake_parse_pdf(monkeypatch, {
        b"sid.pdf": ["AXIS_SID cover page naming Axis Bluechip Fund"],
        b"statement.pdf": ["AXIS_STMT one page of transactions"],
    })
    client = FakeClient(
        identity_by_marker={
            "AXIS_SID": {"doc_type": "fund_document", "document_kind": "sid", "scheme_name": "Axis Bluechip Fund"},
            "AXIS_STMT": {"doc_type": "statement", "document_kind": "other"},
        },
        constraints_by_marker={"AXIS_SID": []},
        lots_by_marker={
            "AXIS_STMT": [{"scheme_name": "Axis Bluechip Fund", "folio": "F001", "units": 100.0,
                           "purchase_date": "2022-01-15", "purchase_nav": 45.0}],
        },
    )
    result = run_intake(
        [("sid.pdf", b"sid.pdf"), ("statement.pdf", b"statement.pdf")],
        extraction_client=client, amfi_directory=AMFI_DIRECTORY,
    )

    assert len(result.funds) == 1  # the statement's lot resolved to the SID's fund, not a second one
    assert len(result.holdings.lots) == 1
    lot = result.holdings.lots[0]
    assert lot.scheme_id == "120503"
    assert lot.folio == "F001"


def test_statement_lot_with_no_document_still_resolves_via_amfi(monkeypatch):
    _fake_parse_pdf(monkeypatch, {b"statement.pdf": ["AXIS_STMT only, no SID uploaded"]})
    client = FakeClient(
        identity_by_marker={"AXIS_STMT": {"doc_type": "statement", "document_kind": "other"}},
        lots_by_marker={
            "AXIS_STMT": [{"scheme_name": "Axis Bluechip Fund", "folio": "F002", "units": 10.0,
                           "purchase_date": "2023-05-01", "purchase_nav": 50.0}],
        },
    )
    result = run_intake([("statement.pdf", b"statement.pdf")], extraction_client=client, amfi_directory=AMFI_DIRECTORY)
    assert len(result.holdings.lots) == 1
    assert result.holdings.lots[0].scheme_id == "120503"
    assert result.funds[0].document_kind == "statement_only"


def test_statement_lot_with_no_match_anywhere_is_excluded_with_a_warning(monkeypatch):
    _fake_parse_pdf(monkeypatch, {b"statement.pdf": ["MYSTERY_STMT"]})
    client = FakeClient(
        identity_by_marker={"MYSTERY_STMT": {"doc_type": "statement", "document_kind": "other"}},
        lots_by_marker={
            "MYSTERY_STMT": [{"scheme_name": "Completely Unknown Scheme", "folio": "F999", "units": 5.0,
                              "purchase_date": "2024-01-01", "purchase_nav": 10.0}],
        },
    )
    result = run_intake([("statement.pdf", b"statement.pdf")], extraction_client=client, amfi_directory=AMFI_DIRECTORY)
    assert result.holdings.lots == []
    assert any("Completely Unknown Scheme" in w for w in result.warnings)


def test_malformed_lot_is_skipped_with_a_warning_not_raised(monkeypatch):
    _fake_parse_pdf(monkeypatch, {b"statement.pdf": ["AXIS_STMT"]})
    client = FakeClient(
        identity_by_marker={"AXIS_STMT": {"doc_type": "statement", "document_kind": "other"}},
        lots_by_marker={
            "AXIS_STMT": [{"scheme_name": "Axis Bluechip Fund", "folio": "F001", "units": -5.0,
                           "purchase_date": "2022-01-15", "purchase_nav": 45.0}],
        },
    )
    result = run_intake([("statement.pdf", b"statement.pdf")], extraction_client=client, amfi_directory=AMFI_DIRECTORY)
    assert result.holdings.lots == []
    assert any("skipped a lot" in w for w in result.warnings)


def test_a_corrupt_pdf_is_warned_about_not_crashed_on(monkeypatch):
    """A malformed upload (a real risk - not every PDF a user drags in
    will parse) must not take the whole batch down with it."""
    def fake_parse_pdf(content):
        if content == b"bad.pdf":
            raise Exception("No /Root object! - Is this really a PDF?")
        return {b"good.pdf": ["AXIS_SID naming Axis Bluechip Fund"]}[content]

    monkeypatch.setattr("agent.intake.parse_pdf", fake_parse_pdf)
    client = FakeClient(
        identity_by_marker={
            "AXIS_SID": {"doc_type": "fund_document", "document_kind": "sid", "scheme_name": "Axis Bluechip Fund"},
        },
        constraints_by_marker={"AXIS_SID": []},
    )
    result = run_intake(
        [("bad.pdf", b"bad.pdf"), ("good.pdf", b"good.pdf")],
        extraction_client=client, amfi_directory=AMFI_DIRECTORY,
    )
    assert any("bad.pdf" in w and "could not be parsed" in w for w in result.warnings)
    assert len(result.funds) == 1  # the other upload still went through
    assert result.funds[0].scheme_id == "120503"


def test_unrecognized_document_is_skipped_with_a_warning(monkeypatch):
    _fake_parse_pdf(monkeypatch, {b"random.pdf": ["a shopping receipt or something unrelated"]})
    client = FakeClient()
    result = run_intake([("random.pdf", b"random.pdf")], extraction_client=client, amfi_directory=AMFI_DIRECTORY)
    assert result.funds == []
    assert result.holdings.lots == []
    assert any("not recognized" in w for w in result.warnings)


def test_unresolved_fund_document_is_flagged_but_still_usable(monkeypatch):
    _fake_parse_pdf(monkeypatch, {b"sid.pdf": ["UNKNOWN_SID"]})
    client = FakeClient(
        identity_by_marker={
            "UNKNOWN_SID": {"doc_type": "fund_document", "document_kind": "sid", "scheme_name": "Totally New Fund"},
        },
        constraints_by_marker={"UNKNOWN_SID": []},
    )
    result = run_intake([("sid.pdf", b"sid.pdf")], extraction_client=client, amfi_directory=AMFI_DIRECTORY)
    assert result.funds[0].resolved is False
    assert result.funds[0].scheme_id == "unresolved_totally_new_fund"
    assert any("not found in AMFI" in w for w in result.warnings)


# --- screenshot / image intake (vision path, no OCR binary needed) -------


class FakeVisionClient(FakeClient):
    """Extends FakeClient with the vision entry point - dispatches on a
    marker substring found in the (fake) image bytes, same idea as the
    text-path FakeClient dispatching on page text."""

    def __init__(self, *, identity_image_by_marker=None, rows_by_marker=None, **kw):
        super().__init__(**kw)
        self.identity_image_by_marker = identity_image_by_marker or {}
        self.rows_by_marker = rows_by_marker or {}

    def extract_image(self, system, image_bytes, mime_type, tool):
        text = image_bytes.decode("latin-1", errors="ignore")
        name = tool["name"]
        if name == "record_document_identity":
            for marker, resp in self.identity_image_by_marker.items():
                if marker in text:
                    return resp
            return {"doc_type": "other", "document_kind": "other"}
        if name == "record_portfolio_rows":
            for marker, resp in self.rows_by_marker.items():
                if marker in text:
                    return {"rows": resp}
            return {"rows": []}
        return {}


def test_screenshot_upload_with_complete_rows_becomes_holdings(monkeypatch):
    monkeypatch.setattr("agent.intake.parse_pdf", lambda content: (_ for _ in ()).throw(AssertionError))
    client = FakeVisionClient(
        identity_image_by_marker={"PORTFOLIO_SHOT": {"doc_type": "statement", "document_kind": "other"}},
        rows_by_marker={"PORTFOLIO_SHOT": [
            {"scheme_name": "Axis Bluechip Fund", "units": 500, "folio": "F1",
             "purchase_date": "2022-01-15", "purchase_nav": 45.0},
        ]},
    )
    result = run_intake([("shot.png", b"PORTFOLIO_SHOT")], extraction_client=client, amfi_directory=AMFI_DIRECTORY)
    assert result.warnings == []
    assert result.incomplete_holdings == []
    assert len(result.holdings.lots) == 1
    assert result.holdings.lots[0].scheme_id == "120503"
    assert result.funds[0].document_kind == "screenshot"


def test_screenshot_row_missing_purchase_data_is_incomplete_not_dropped(monkeypatch):
    client = FakeVisionClient(
        identity_image_by_marker={"PORTFOLIO_SHOT": {"doc_type": "statement", "document_kind": "other"}},
        rows_by_marker={"PORTFOLIO_SHOT": [
            {"scheme_name": "Axis Bluechip Fund", "units": 500},  # current value only, no purchase history
        ]},
    )
    result = run_intake([("shot.jpg", b"PORTFOLIO_SHOT")], extraction_client=client, amfi_directory=AMFI_DIRECTORY)
    assert result.holdings.lots == []
    assert len(result.incomplete_holdings) == 1
    inc = result.incomplete_holdings[0]
    assert inc.scheme_id == "120503"
    assert inc.units == 500
    assert set(inc.missing) == {"folio", "purchase_date", "purchase_nav"}


def test_screenshot_of_a_scheme_document_is_declined_not_misread(monkeypatch):
    client = FakeVisionClient(
        identity_image_by_marker={"CLAUSE_PHOTO": {"doc_type": "fund_document", "document_kind": "sid"}},
    )
    result = run_intake([("clause.png", b"CLAUSE_PHOTO")], extraction_client=client, amfi_directory=AMFI_DIRECTORY)
    assert result.holdings.lots == []
    assert any("supported yet" in w for w in result.warnings)


def test_screenshot_not_recognized_is_skipped_with_a_warning():
    client = FakeVisionClient(identity_image_by_marker={"RANDOM": {"doc_type": "other", "document_kind": "other"}})
    result = run_intake([("random.png", b"RANDOM")], extraction_client=client, amfi_directory=AMFI_DIRECTORY)
    assert result.holdings.lots == []
    assert any("not recognized as a portfolio screen" in w for w in result.warnings)


def test_pdf_with_no_text_layer_falls_back_to_vision(monkeypatch):
    """A scanned/screenshotted PDF: pdfplumber finds nothing, so intake
    renders the page to an image and reads it via vision instead of
    giving up - no `tesseract` binary needed."""
    monkeypatch.setattr("agent.intake.parse_pdf", lambda content: [""])
    monkeypatch.setattr("agent.intake.render_page_png", lambda content, page=0: b"RENDERED_SHOT")
    client = FakeVisionClient(
        identity_image_by_marker={"RENDERED_SHOT": {"doc_type": "statement", "document_kind": "other"}},
        rows_by_marker={"RENDERED_SHOT": [
            {"scheme_name": "Axis Bluechip Fund", "units": 100, "folio": "F1",
             "purchase_date": "2022-01-15", "purchase_nav": 45.0},
        ]},
    )
    result = run_intake([("scan.pdf", b"scanned-bytes")], extraction_client=client, amfi_directory=AMFI_DIRECTORY)
    assert not any("no extractable text" in w for w in result.warnings)
    assert len(result.holdings.lots) == 1


def test_pdf_with_no_text_and_no_renderable_page_still_warns_cleanly(monkeypatch):
    monkeypatch.setattr("agent.intake.parse_pdf", lambda content: [""])
    monkeypatch.setattr("agent.intake.render_page_png", lambda content, page=0: None)
    result = run_intake(
        [("scan.pdf", b"corrupt-scan")], extraction_client=FakeVisionClient(), amfi_directory=AMFI_DIRECTORY,
    )
    assert result.holdings.lots == []
    assert any("no extractable text" in w for w in result.warnings)
