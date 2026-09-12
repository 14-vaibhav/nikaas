from __future__ import annotations

from agent.screenshot import PortfolioRow, extract_portfolio_image, guess_image_mime


class FakeVisionClient:
    def __init__(self, response: dict):
        self._response = response

    def extract_image(self, system, image_bytes, mime_type, tool):
        return self._response


def test_guess_image_mime_from_extension():
    assert guess_image_mime("shot.PNG") == "image/png"
    assert guess_image_mime("shot.jpg") == "image/jpeg"
    assert guess_image_mime("shot.jpeg") == "image/jpeg"
    assert guess_image_mime("shot.webp") == "image/webp"
    assert guess_image_mime("statement.pdf") is None


def test_complete_row_has_folio_date_and_nav():
    row = PortfolioRow(scheme_name="Axis Bluechip Fund", units=100, folio="F1",
                       purchase_date="2022-01-15", purchase_nav=45.0)
    assert row.is_complete()
    assert row.missing_fields() == []


def test_row_missing_purchase_date_and_nav_is_incomplete():
    row = PortfolioRow(scheme_name="Axis Bluechip Fund", units=100, folio="F1")
    assert not row.is_complete()
    assert row.missing_fields() == ["purchase_date", "purchase_nav"]


def test_row_missing_folio_only_is_incomplete():
    row = PortfolioRow(scheme_name="X", units=1, purchase_date="2022-01-01", purchase_nav=10.0)
    assert not row.is_complete()
    assert row.missing_fields() == ["folio"]


def test_extract_reads_rows_and_leaves_missing_fields_null_not_guessed():
    client = FakeVisionClient({"rows": [
        {"scheme_name": "Axis Bluechip Fund", "units": 500, "folio": "F1",
         "purchase_date": "2022-01-15", "purchase_nav": 45.0},
        {"scheme_name": "HDFC Top 100 Fund", "units": 200},  # current-holding-only row
    ]})
    rows = extract_portfolio_image(client, b"fake-image-bytes", "image/png")
    assert len(rows) == 2
    assert rows[0].is_complete()
    assert not rows[1].is_complete()
    assert rows[1].purchase_date is None and rows[1].purchase_nav is None


def test_extract_reports_invalid_rows_instead_of_dropping_them():
    client = FakeVisionClient({"rows": [
        {"scheme_name": "", "units": 100},
        {"scheme_name": "Some Fund", "units": 0},
        {"scheme_name": "Some Fund", "units": -5},
        {"scheme_name": "Axis Bluechip Fund", "units": 10, "folio": "F1",
         "purchase_date": "2022-01-15", "purchase_nav": 45.0},
    ]})
    rows = extract_portfolio_image(client, b"x", "image/png")
    assert len(rows) == 4
    assert rows[0].status == "invalid"
    assert rows[1].status == "invalid"
    assert rows[2].status == "invalid"
    assert rows[3].status == "detected"
    assert any("scheme_name" in issue.lower() for issue in rows[0].issues)


def test_extract_marks_incomplete_rows_for_human_review_without_guessing():
    client = FakeVisionClient({"rows": [{"scheme_name": "Axis Bluechip Fund", "units": 500}]})
    rows = extract_portfolio_image(client, b"x", "image/png")
    assert len(rows) == 1
    row = rows[0]
    assert row.status == "incomplete"
    assert set(row.missing_fields()) == {"folio", "purchase_date", "purchase_nav"}
    assert row.purchase_date is None and row.purchase_nav is None


def test_extract_records_unusable_rows_as_review_items():
    client = FakeVisionClient({"rows": [{"scheme_name": "Axis Bluechip Fund"}, None, {}]})
    rows = extract_portfolio_image(client, b"x", "image/png")
    assert rows[0].status == "invalid"
    assert rows[1].status == "unusable"
    assert rows[2].status == "invalid"
