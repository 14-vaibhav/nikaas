"""HTTP-level wiring tests. The intake pipeline itself (classification,
AMFI resolution, constraint/lot extraction) is unit-tested in
agent/tests/test_intake.py against fakes - these tests only check that
POST /api/intake -> POST /api/run -> GET /api/plans thread an uploaded
intake's funds/holdings/constraints into the loop correctly, without
needing a real ANTHROPIC_API_KEY or real PDFs.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

import api.main as main
from agent.contracts import Constraint, Holdings, Lot, Source
from agent.evidence import now_iso
from agent.intake import IntakeFund, IntakeResult


def _constraint(scheme_id: str, kind: str, value: dict) -> Constraint:
    # retrieved_at must stay fresh relative to whenever the suite runs - a
    # run built from an intake now hunts the real web on a stale-evidence
    # gap (see api/main.py's _select_hunter), so a hardcoded past date here
    # would eventually go stale and force a real network call these tests
    # are explicitly meant not to need.
    return Constraint(
        id=f"c_{scheme_id}_{kind}", scheme_id=scheme_id, kind=kind, value=value,
        source=Source(doc="sid.pdf", page=1, clause="1", url=None,
                       effective_date="2025-01-01", retrieved_at=now_iso()),
        confidence="verified",
    )


def _fake_intake_result() -> IntakeResult:
    return IntakeResult(
        funds=[IntakeFund(
            scheme_id="120503", label="Axis Bluechip Fund", amc="Axis Mutual Fund",
            resolved=True, document="sid.pdf", document_kind="sid", nav=62.35,
        )],
        holdings=Holdings(lots=[Lot(
            lot_id="lot_1", scheme_id="120503", folio="F001",
            units=30000, purchase_date="2022-01-15", purchase_nav=45.0,
        )]),
        constraints=[
            _constraint("120503", "exit_load", {"pct": 1.0, "window_days": 365}),
            _constraint("120503", "tax_rule", {
                "ltcg_threshold_days": 365, "ltcg_rate": 0.125, "stcg_rate": 0.20,
                "ltcg_exemption": 125000,
            }),
        ],
        navs={"120503": 62.35},
        warnings=[],
    )


def test_intake_then_run_produces_a_verified_plan(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-dummy")
    monkeypatch.setattr(main, "run_intake", lambda docs, **kw: _fake_intake_result())
    monkeypatch.setattr(main, "load_amfi_directory", lambda: {})

    client = TestClient(main.app)

    intake_res = client.post(
        "/api/intake", files={"documents": ("sid.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert intake_res.status_code == 200
    body = intake_res.json()
    assert body["funds"][0]["scheme_id"] == "120503"
    assert body["warnings"] == []
    intake_id = body["intake_id"]

    run_res = client.post("/api/run", json={
        "intake_id": intake_id, "mode": "A",
        "goal": {"amount": 1000000, "by_date": "2026-12-31", "mode": "A"},
    })
    assert run_res.status_code == 200
    run_id = run_res.json()["run_id"]

    with client.stream("GET", f"/api/stream/{run_id}") as resp:
        for _ in resp.iter_lines():
            pass  # drain the SSE stream so the background run finishes before we ask for plans

    plans_res = client.get(f"/api/plans/{run_id}")
    assert plans_res.status_code == 200
    plans = plans_res.json()
    assert plans["plans"]["cheapest"]["verdict"] == "verified"
    assert plans["plans"]["cheapest"]["legs"][0]["scheme_id"] == "120503"


def test_intake_requires_an_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    client = TestClient(main.app)
    res = client.post(
        "/api/intake", files={"documents": ("sid.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert res.status_code == 500
    assert "ANTHROPIC_API_KEY" in res.json()["detail"]


def test_intake_with_gemini_provider_requires_gemini_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client = TestClient(main.app)
    res = client.post(
        "/api/intake", files={"documents": ("sid.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert res.status_code == 500
    assert "GEMINI_API_KEY" in res.json()["detail"]


def test_intake_with_gemini_provider_builds_gemini_client(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-dummy-key")
    monkeypatch.setattr(main, "run_intake", lambda docs, **kw: _fake_intake_result())
    monkeypatch.setattr(main, "load_amfi_directory", lambda: {})

    from agent.gemini_client import GeminiExtractionClient

    seen = {}
    real_init = GeminiExtractionClient.__init__

    def spy_init(self, *a, **kw):
        seen["called"] = True
        return real_init(self, *a, **kw)

    monkeypatch.setattr(GeminiExtractionClient, "__init__", spy_init)

    client = TestClient(main.app)
    res = client.post(
        "/api/intake", files={"documents": ("sid.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert res.status_code == 200
    assert seen.get("called") is True


def test_unknown_provider_is_rejected(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "some-other-provider")
    client = TestClient(main.app)
    res = client.post(
        "/api/intake", files={"documents": ("sid.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert res.status_code == 500
    assert "some-other-provider" in res.json()["detail"]


def test_run_from_intake_requires_a_goal(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-dummy")
    monkeypatch.setattr(main, "run_intake", lambda docs, **kw: _fake_intake_result())
    monkeypatch.setattr(main, "load_amfi_directory", lambda: {})
    client = TestClient(main.app)

    intake_id = client.post(
        "/api/intake", files={"documents": ("sid.pdf", b"%PDF-1.4 fake", "application/pdf")},
    ).json()["intake_id"]

    res = client.post("/api/run", json={"intake_id": intake_id, "mode": "A"})
    assert res.status_code == 400
    assert "goal" in res.json()["detail"]


# --- new endpoints: scenario runs + the §5 feature surface ----------------


def _run_scenario(client, scenario="demo_cas_18", **body):
    run_id = client.post("/api/run", json={"scenario": scenario, **body}).json()["run_id"]
    with client.stream("GET", f"/api/stream/{run_id}") as resp:
        for _ in resp.iter_lines():
            pass
    return run_id


def test_scenarios_list_shows_the_presentable_set_and_hides_the_legacy_fixtures():
    client = TestClient(main.app)
    ids = {s["id"] for s in client.get("/api/scenarios").json()["scenarios"]}
    assert {
        "demo_cas_18", "demo_mode_b_cas", "demo_timing", "demo_abstain", "demo_unachievable",
    } <= ids
    # kept as unit-test fixtures, not shown on the hackathon page
    assert ids.isdisjoint({"demo_basic", "demo_conflict", "demo_mode_b"})
    # still fully runnable by id even though hidden from the listing
    assert client.get("/api/scenarios/demo_basic").status_code == 200


def test_scenario_run_context_and_explain():
    client = TestClient(main.app)
    run_id = _run_scenario(client)

    ctx = client.get(f"/api/runs/{run_id}/context")
    assert ctx.status_code == 200
    assert len(ctx.json()["constraints"]) > 10
    assert len(ctx.json()["holdings"]) > 10

    ex = client.post("/api/explain", json={"run_id": run_id, "choice": "cheapest"})
    assert ex.status_code == 200
    text = ex.json()["text"]
    assert "What would be redeemed" in text and "not investment advice" in text.lower()


def test_prove_me_wrong_verify_catches_a_tampered_constraint():
    client = TestClient(main.app)
    run_id = _run_scenario(client, scenario="demo_basic")
    ctx = client.get(f"/api/runs/{run_id}/context").json()
    plans = client.get(f"/api/plans/{run_id}").json()["plans"]
    plan = plans["cheapest"]

    applied = {cid for leg in plan["legs"] for cid in leg["constraints_applied"]}
    tampered = []
    for c in ctx["constraints"]:
        if c["id"] in applied and c["kind"] == "exit_load":
            c = {**c, "value": {**c["value"], "pct": 25.0, "window_days": 4000}}
        tampered.append(c)

    res = client.post("/api/verify", json={
        "plan": plan, "constraints": tampered, "holdings": ctx["holdings"],
    })
    assert res.status_code == 200
    assert len(res.json()) >= 1  # verifier independently rejects the tampered plan


def test_reverse_endpoint_on_the_18_scheme_scenario():
    client = TestClient(main.app)
    res = client.post("/api/reverse", json={"scenario": "demo_cas_18"})
    assert res.status_code == 200
    body = res.json()
    assert body["total_free_value"] > 0
    assert "s_franklin_focused" in {a["scheme_id"] for a in body["abstained"]}
    keys = [(s["scheme_id"], s["folio"]) for s in body["slices"]]
    assert len(keys) == len(set(keys))  # folios never merged


def test_reconcile_endpoint_dedupes_and_flags():
    client = TestClient(main.app)
    rows = [
        {"source": "CAMS", "scheme_name": "Quant ELSS Tax Saver Fund - Direct Growth",
         "folio": "17880021 / 55", "units": 150, "purchase_date": "2022-01-10",
         "purchase_nav": 250.30},
        {"source": "KFintech", "scheme_name": "QUANT ELSS TAX SAVER FUND DIRECT GROWTH",
         "folio": "17880021 / 55", "units": 150, "purchase_date": "2022-01-10",
         "purchase_nav": 250.30},
        {"source": "CAMS", "scheme_name": "Zzz Made Up Fund", "folio": "9 / 9",
         "units": 10, "purchase_date": "2022-01-01", "purchase_nav": 10.0},
    ]
    res = client.post("/api/reconcile", json={"rows": rows})
    assert res.status_code == 200
    body = res.json()
    kinds = {f["kind"] for f in body["flags"]}
    assert "duplicate_lot" in kinds
    assert "unresolved_scheme" in kinds
    assert body["kept_rows"] == 1


def test_watch_add_and_check_reports_no_trigger_when_navs_unchanged():
    client = TestClient(main.app)
    run_id = _run_scenario(client, scenario="demo_basic")
    assert client.post(f"/api/watch/{run_id}").status_code == 200
    ctx = client.get(f"/api/runs/{run_id}/context").json()
    res = client.post(f"/api/watch/{run_id}/check", json=ctx["navs"])
    assert res.status_code == 200
    assert res.json()["triggered"] is False


# --- hunter routing: scenario runs stay offline, intake runs hunt live ----


def test_intake_run_routes_missing_rules_to_web_hunter(monkeypatch):
    """A run built from an intake that only had a CAS (holdings, no SID for
    one scheme) must not silently stay empty forever the way FolderHunter
    would - it should route the gap to WebHunter. This fakes WebHunter
    itself (no real network in a test) but proves the wiring: api/main.py
    must construct and actually use it for an intake-based run."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-dummy")

    fake_result = IntakeResult(
        funds=[IntakeFund(
            scheme_id="999999", label="Some Fund With No Uploaded SID", amc=None,
            resolved=True, document="statement.pdf", document_kind="statement_only",
        )],
        holdings=Holdings(lots=[Lot(
            lot_id="lot_1", scheme_id="999999", folio="F001",
            units=1000, purchase_date="2022-01-15", purchase_nav=45.0,
        )]),
        constraints=[],  # no rules at all - only a holdings statement was uploaded
        navs={"999999": 50.0}, warnings=[],
    )
    monkeypatch.setattr(main, "run_intake", lambda docs, **kw: fake_result)
    monkeypatch.setattr(main, "load_amfi_directory", lambda: {})

    calls = {"constructed_with": None}

    class FakeWebHunter:
        def __init__(self, *, extraction_client=None, **kw):
            calls["constructed_with"] = extraction_client

        def hunt(self, fund_id, fund_name, kinds, *, force=False):
            from agent.hunter import HuntOutcome

            constraints = [
                _constraint("999999", "exit_load", {"pct": 1.0, "window_days": 365}),
                _constraint("999999", "tax_rule", {
                    "ltcg_threshold_days": 365, "ltcg_rate": 0.125, "stcg_rate": 0.20,
                    "ltcg_exemption": 125000,
                }),
            ]
            return HuntOutcome(
                constraints=[c for c in constraints if c.kind in kinds],
                content_hash="fakehash", ok=True, detail="found it on the live web (faked)",
            )

    monkeypatch.setattr(main, "WebHunter", FakeWebHunter)

    client = TestClient(main.app)
    intake_id = client.post(
        "/api/intake", files={"documents": ("statement.pdf", b"%PDF-1.4 fake", "application/pdf")},
    ).json()["intake_id"]

    run_res = client.post("/api/run", json={
        "intake_id": intake_id, "mode": "A",
        "goal": {"amount": 40000, "by_date": "2026-12-31", "mode": "A"},
    })
    run_id = run_res.json()["run_id"]
    with client.stream("GET", f"/api/stream/{run_id}") as resp:
        for _ in resp.iter_lines():
            pass

    assert calls["constructed_with"] is not None  # WebHunter was actually built and used
    plans = client.get(f"/api/plans/{run_id}").json()
    assert plans["plans"]["cheapest"]["verdict"] == "verified"
    assert plans["plans"]["cheapest"]["legs"][0]["scheme_id"] == "999999"


def test_scenario_run_never_touches_web_hunter(monkeypatch):
    """The inverse guarantee: a folder-mode scenario must stay fully
    offline and deterministic - WebHunter must never even be constructed."""
    def _boom(*a, **kw):
        raise AssertionError("WebHunter must not be constructed for a scenario run")

    monkeypatch.setattr(main, "WebHunter", _boom)
    client = TestClient(main.app)
    run_id = client.post("/api/run", json={"scenario": "demo_basic"}).json()["run_id"]
    with client.stream("GET", f"/api/stream/{run_id}") as resp:
        for _ in resp.iter_lines():
            pass
    plans = client.get(f"/api/plans/{run_id}").json()
    assert plans["plans"]["cheapest"]["verdict"] == "verified"


# --- screenshot upload + completing missing purchase data -----------------


def test_screenshot_upload_surfaces_incomplete_holdings_and_complete_endpoint_fills_them(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-dummy")

    class FakeVisionClient:
        def extract_image(self, system, image_bytes, mime_type, tool):
            if tool["name"] == "record_document_identity":
                return {"doc_type": "statement", "document_kind": "other"}
            if tool["name"] == "record_portfolio_rows":
                return {"rows": [{"scheme_name": "Axis Bluechip Fund", "units": 500}]}
            return {}

    from agent.amc_registry import resolve_scheme

    def fake_run_intake(docs, *, extraction_client, amfi_directory):
        from agent.intake import run_intake as real_run_intake
        return real_run_intake(docs, extraction_client=FakeVisionClient(), amfi_directory=amfi_directory)

    monkeypatch.setattr(main, "run_intake", fake_run_intake)
    monkeypatch.setattr(main, "load_amfi_directory", lambda: {
        "axis bluechip fund": {"scheme_code": "120503", "name": "Axis Bluechip Fund",
                               "amc": "Axis Mutual Fund", "isin": "INF846K01131", "nav": 62.35},
    })

    client = TestClient(main.app)
    res = client.post("/api/intake", files={"documents": ("shot.png", b"fake-png-bytes", "image/png")})
    assert res.status_code == 200
    body = res.json()
    assert body["holdings_preview"] == []
    assert len(body["incomplete_holdings"]) == 1
    inc = body["incomplete_holdings"][0]
    assert inc["scheme_id"] == "120503"
    assert set(inc["missing"]) == {"folio", "purchase_date", "purchase_nav"}
    intake_id = body["intake_id"]

    complete_res = client.post(f"/api/intake/{intake_id}/complete", json={
        "completions": [{
            "scheme_id": "120503", "units": 500, "folio": "F001",
            "purchase_date": "2022-01-15", "purchase_nav": 45.0,
        }],
    })
    assert complete_res.status_code == 200
    cbody = complete_res.json()
    assert cbody["incomplete_holdings"] == []
    assert len(cbody["holdings_preview"]) == 1
    assert cbody["holdings_preview"][0]["scheme_id"] == "120503"

    # the completed holding is now usable by a run
    run_res = client.post("/api/run", json={
        "intake_id": intake_id, "mode": "A",
        "goal": {"amount": 5000, "by_date": "2026-12-31", "mode": "A"},
    })
    assert run_res.status_code == 200


def test_complete_intake_rejects_malformed_completion(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-dummy")
    monkeypatch.setattr(main, "run_intake", lambda docs, **kw: _fake_intake_result())
    monkeypatch.setattr(main, "load_amfi_directory", lambda: {})
    client = TestClient(main.app)
    intake_id = client.post(
        "/api/intake", files={"documents": ("sid.pdf", b"%PDF-1.4 fake", "application/pdf")},
    ).json()["intake_id"]

    res = client.post(f"/api/intake/{intake_id}/complete", json={
        "completions": [{"scheme_id": "120503", "units": -5, "folio": "F1",
                        "purchase_date": "2022-01-15", "purchase_nav": 45.0}],
    })
    assert res.status_code == 400
