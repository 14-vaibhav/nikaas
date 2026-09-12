from __future__ import annotations

from datetime import date

from agent.contracts import Constraint, Source
from agent.hunter import FolderHunter, WebHunter, resolve_amc


def _c(fund="s_axis_bluechip", kind="exit_load"):
    return Constraint(
        id=f"c_{fund}_{kind}", scheme_id=fund, kind=kind,
        value={"pct": 1.0, "window_days": 365},
        source=Source(doc="sid.pdf", page=1, clause="1", url="https://e.com/sid.pdf",
                      effective_date="2025-01-01", retrieved_at="2026-01-01T00:00:00Z"),
        confidence="verified",
    )


# --- resolve_amc ---------------------------------------------------------


def test_resolve_amc_matches_inconsistent_naming():
    directory = {"axis bluechip fund": "amc_axis", "hdfc top 100 fund": "amc_hdfc"}
    assert resolve_amc("Axis Blue Chip Fund", directory) == "amc_axis"


def test_resolve_amc_returns_none_rather_than_guessing():
    directory = {"axis bluechip fund": "amc_axis"}
    assert resolve_amc("Completely Unrelated Scheme Name", directory) is None


# --- FolderHunter -----------------------------------------------------


def test_folder_hunter_returns_fund_kind_and_restamps_retrieved_at():
    fh = FolderHunter([_c(), _c(kind="tax_rule"), _c(fund="s_other")])
    out = fh.hunt("s_axis_bluechip", "Axis Bluechip", ["exit_load"])
    assert out.ok
    assert [c.kind for c in out.constraints] == ["exit_load"]
    assert out.constraints[0].source.retrieved_at[:4] == str(date.today().year)
    assert out.content_hash


def test_folder_hunter_reports_miss_without_guessing():
    fh = FolderHunter([_c()])
    out = fh.hunt("s_unknown", "Unknown", ["exit_load"])
    assert not out.ok and out.constraints == []


# --- WebHunter with a fake HTTP client (no network) -----------------


class _Resp:
    def __init__(self, *, text="", content=b"", headers=None, status=200):
        self.text = text
        self.content = content
        self.headers = headers or {}
        self._status = status

    def raise_for_status(self):
        if self._status >= 400:
            raise RuntimeError(f"HTTP {self._status}")


class _FakeHTTP:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, headers=None):
        self.calls.append(url)
        for frag, resp in self.routes.items():
            if frag in url:
                return resp
        return _Resp(status=404)


def test_web_hunter_discovers_links_but_reports_when_nothing_extracts(monkeypatch):
    directory = {"axis bluechip fund": {"scheme_code": "1", "amc": "Axis Mutual Fund",
                                        "amc_id": "axis", "isin": "INF0"}}
    index_html = (
        '<html><body>'
        '<a href="/docs/axis-bluechip-sid.pdf">Axis Bluechip Scheme Information Document</a>'
        '</body></html>'
    )
    fake = _FakeHTTP({
        "statutory-disclosure": _Resp(text=index_html),
        "robots.txt": _Resp(status=404),
        "axis-bluechip-sid.pdf": _Resp(content=b"%PDF-1.4 not-really-a-pdf",
                                        headers={"content-type": "application/pdf"}),
    })

    # Parsing a bogus PDF yields no pages -> honest "nothing extracted".
    monkeypatch.setattr("agent.hunter._pdf_pages", lambda content: [])
    monkeypatch.setattr("agent.hunter._ocr_pages", lambda content: [])

    wh = WebHunter(directory=directory, extraction_client=object(), http=fake,
                   delay_seconds=0, obey_robots=False)
    out = wh.hunt("s_axis_bluechip", "Axis Bluechip Fund", ["exit_load"])
    assert not out.ok
    assert any("axis-bluechip-sid.pdf" in u for u in fake.calls)


def test_web_hunter_abstains_when_amc_unresolved():
    wh = WebHunter(directory={}, extraction_client=object(), http=_FakeHTTP({}),
                   delay_seconds=0, obey_robots=False)
    out = wh.hunt("s_x", "No Such Fund", ["exit_load"])
    assert not out.ok and "resolve" in out.detail
