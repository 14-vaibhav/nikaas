from __future__ import annotations

import json
from pathlib import Path

from agent.amc_registry import load_amfi_directory, parse_amfi_navall


def test_parse_amfi_navall_handles_partial_rows():
    text = """
Axis Mutual Fund
09;INF00000001;X;Axis Bluechip Fund;10.5;2026-09-01
10;INF00000002;Y;Axis Midcap Fund;11.5;2026-09-01
bad row without enough fields
""".strip()
    directory = parse_amfi_navall(text)
    assert "axis bluechip fund" in directory
    assert "axis midcap fund" in directory
    assert len(directory) == 2


def test_load_amfi_directory_cache_hit_returns_cached_data(tmp_path):
    cache = tmp_path / "amfi_directory.json"
    cache.write_text(json.dumps({"axis bluechip fund": {"scheme_code": "1", "name": "Axis Bluechip Fund"}}))
    loaded = load_amfi_directory(path=cache)
    assert loaded["axis bluechip fund"]["name"] == "Axis Bluechip Fund"


def test_load_amfi_directory_refreshes_on_cache_miss(monkeypatch, tmp_path):
    cache = tmp_path / "amfi_directory.json"

    class FakeHTTP:
        def get(self, *args, **kwargs):
            class Resp:
                text = "Axis Mutual Fund\n10;INF00000001;X;Axis Bluechip Fund;10.5;2026-09-01\n"
                def raise_for_status(self):
                    return None
            return Resp()

    monkeypatch.setattr("agent.amc_registry._fetch_navall", lambda http=None: "Axis Mutual Fund\n10;INF00000001;X;Axis Bluechip Fund;10.5;2026-09-01\n")
    loaded = load_amfi_directory(path=cache)
    assert "axis bluechip fund" in loaded
    assert cache.exists()


def test_load_amfi_directory_handles_failed_refresh_gracefully(monkeypatch, tmp_path):
    cache = tmp_path / "amfi_directory.json"
    cache.write_text(json.dumps({"axis bluechip fund": {"scheme_code": "1", "name": "Axis Bluechip Fund"}}))

    def boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr("agent.amc_registry._fetch_navall", boom)
    loaded = load_amfi_directory(path=cache, refresh=True)
    assert loaded["axis bluechip fund"]["name"] == "Axis Bluechip Fund"


def test_load_amfi_directory_reports_missing_scheme_after_refresh(monkeypatch, tmp_path):
    cache = tmp_path / "amfi_directory.json"
    cache.write_text(json.dumps({}))

    monkeypatch.setattr("agent.amc_registry._fetch_navall", lambda http=None: "Axis Mutual Fund\n10;INF00000001;X;Axis Bluechip Fund;10.5;2026-09-01\n")
    loaded = load_amfi_directory(path=cache, refresh=True)
    assert "totally unknown fund" not in loaded
    assert "axis bluechip fund" in loaded
