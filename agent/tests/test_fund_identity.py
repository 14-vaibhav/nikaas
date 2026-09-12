from __future__ import annotations

import pytest

from agent.fund_identity import identify_document, resolve_fund_identity


class FakeClient:
    def __init__(self, response: dict):
        self._response = response

    def extract(self, system, page_text, tool):
        return self._response


DIRECTORY = {
    "axis bluechip fund": {
        "scheme_code": "120503", "name": "Axis Bluechip Fund", "amc": "Axis Mutual Fund",
        "amc_id": "axis", "isin": "INF846K01131", "nav": 62.35, "nav_date": "01-Sep-2026",
    },
}


def test_identify_document_reads_only_the_first_two_pages():
    client = FakeClient({"doc_type": "fund_document", "document_kind": "sid", "scheme_name": "Axis Bluechip Fund"})
    identity = identify_document(client, ["cover page", "page 2", "page 3 - never sent"])
    assert identity.doc_type == "fund_document"
    assert identity.scheme_name == "Axis Bluechip Fund"


def test_resolve_fund_identity_matches_against_amfi():
    identity = identify_document(
        FakeClient({"doc_type": "fund_document", "document_kind": "sid", "scheme_name": "Axis Blue Chip Fund"}),
        ["Axis Blue Chip Fund - Scheme Information Document"],
    )
    resolved = resolve_fund_identity(identity, DIRECTORY)
    assert resolved.resolved
    assert resolved.scheme_id == "120503"
    assert resolved.nav == 62.35


def test_resolve_fund_identity_falls_back_to_a_stable_synthetic_id_when_unresolved():
    identity = identify_document(
        FakeClient({"doc_type": "fund_document", "document_kind": "sid", "scheme_name": "Totally Unknown Fund"}),
        ["Totally Unknown Fund SID"],
    )
    resolved = resolve_fund_identity(identity, DIRECTORY)
    assert not resolved.resolved
    assert resolved.scheme_id == "unresolved_totally_unknown_fund"
    assert resolved.nav is None


def test_resolve_fund_identity_rejects_a_document_with_no_scheme_name():
    identity = identify_document(
        FakeClient({"doc_type": "statement", "document_kind": "other"}), ["some statement text"],
    )
    with pytest.raises(ValueError):
        resolve_fund_identity(identity, DIRECTORY)
