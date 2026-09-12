"""FastAPI surface for the loop (diagram: TRACE LOG -> UI -> HUMAN GATE).

One origin. `POST /api/run` starts the loop in a worker; the trace streams
over SSE as it runs; `GET /api/plans/{run_id}` returns the two verified
plans; `POST /api/plans/{run_id}/approve` is the human gate (intent, not
correctness - the verifier already checked correctness). `POST /api/verify`
is 'prove me wrong'. `/api/watch` drives the Watcher.

Folder mode only here: holdings and constraints come from a scenario file
or the request body, and the loop runs with a FolderHunter. Point it at a
WebHunter to hunt live.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()  # ANTHROPIC_API_KEY / GEMINI_API_KEY / LLM_PROVIDER from .env,
# read once at process start - restart uvicorn after editing .env.

from agent.amc_registry import load_amfi_directory
from agent.contracts import Goal, Trigger
from agent.evidence import EvidenceStore
from agent.extractor import AnthropicExtractionClient
from agent.gemini_client import GeminiExtractionClient
from agent.holdings import MalformedHoldingError, load_holdings
from agent.hunter import FolderHunter, WebHunter
from agent.intake import IntakeResult, run_intake
from agent.loop import run as run_loop
from agent.reconcile import RawRow, reconcile
from agent.reverse import reverse as reverse_mode
from agent.explain import explain as explain_plan
from agent.selector import build_mode_b_factors
from agent.serde import constraint_from_dict, plan_from_dict
from agent.store import Store
from agent.trace import TraceLog
from agent.verifier import verify
from agent.watcher import check_plan, snapshot_hashes

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS_DIR = ROOT / "data" / "scenarios"
WEB_DIST = ROOT / "web" / "dist"

app = FastAPI(title="Nikaas")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:4173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

store = Store()


class RunRequest(BaseModel):
    scenario: str = "demo_basic"
    intake_id: Optional[str] = None
    mode: Optional[str] = None
    goal: Optional[dict] = None
    funds: Optional[list[str]] = None
    holdings: Optional[list[dict]] = None
    constraints: Optional[list[dict]] = None
    navs: Optional[dict[str, float]] = None
    raw_mode_b: Optional[list[dict]] = None


class VerifyRequest(BaseModel):
    plan: dict
    constraints: list[dict]
    holdings: list[dict]


class ApproveRequest(BaseModel):
    choice: str  # "cheapest" | "merit_preserving"


class ExplainRequest(BaseModel):
    run_id: str
    choice: str = "cheapest"  # "cheapest" | "merit_preserving"


class ReverseRequest(RunRequest):
    """Reverse mode (§5.9) - 'what can I take out today at zero exit load
    and least tax?'. Same input shapes as a forward run; the goal amount /
    date are ignored."""


class ReconcileRow(BaseModel):
    source: str
    scheme_name: str
    folio: str
    units: float
    purchase_date: str
    purchase_nav: float


class ReconcileRequest(BaseModel):
    rows: list[ReconcileRow]
    known: Optional[dict[str, str]] = None


class _Handle:
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()
        self.result = None
        self.error: Optional[str] = None
        self.done = False
        self.inputs: dict = {}
        self.evidence: Optional[EvidenceStore] = None


_HANDLES: dict[str, _Handle] = {}
_INTAKE_HANDLES: dict[str, IntakeResult] = {}


def _load_scenario(name: str) -> dict:
    path = SCENARIOS_DIR / f"{name}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"no scenario named {name!r}")
    return json.loads(path.read_text())


def _mode_b_factors_if_needed(
    raw_mode_b: Optional[list[dict]], mode: str, funds: list[str], constraints: list,
) -> Optional[list[dict]]:
    if raw_mode_b is not None or mode != "B":
        return raw_mode_b
    # Mode B scores from evidence already on hand (the diagram's "sourced
    # evidence, weights shown") - seed a store from this run's constraints
    # and derive the weighted factors from it, rather than requiring a
    # hand-authored candidate list.
    seed = EvidenceStore()
    seed.put_many(constraints, "seed")
    return build_mode_b_factors(funds, seed)


def _build_inputs(req: RunRequest) -> dict:
    if req.intake_id:
        return _build_inputs_from_intake(req)

    sc = _load_scenario(req.scenario)
    goal_d = req.goal or sc["goal"]
    mode = req.mode or goal_d.get("mode", "A")
    goal = Goal(amount=goal_d["amount"], by_date=goal_d["by_date"], mode=mode)
    try:
        holdings = load_holdings(req.holdings or sc["holdings"])
    except MalformedHoldingError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    constraints = [constraint_from_dict(c) for c in (req.constraints or sc["constraints"])]
    funds = req.funds or sc["candidate_schemes"]
    raw_mode_b = _mode_b_factors_if_needed(
        req.raw_mode_b or sc.get("mode_b_candidates"), mode, funds, constraints,
    )
    return {
        "goal": goal,
        "mode": mode,
        "funds": funds,
        "holdings": holdings,
        "navs": req.navs or sc["navs"],
        "constraints": constraints,
        "raw_mode_b": raw_mode_b,
        "from_intake": False,
    }


def _build_inputs_from_intake(req: RunRequest) -> dict:
    handle = _INTAKE_HANDLES.get(req.intake_id)
    if handle is None:
        raise HTTPException(status_code=404, detail=f"no intake named {req.intake_id!r}")
    if not req.goal:
        raise HTTPException(status_code=400, detail="goal is required when running from an intake")
    mode = req.mode or req.goal.get("mode", "A")
    goal = Goal(amount=req.goal["amount"], by_date=req.goal["by_date"], mode=mode)
    funds = req.funds or sorted({f.scheme_id for f in handle.funds})
    raw_mode_b = _mode_b_factors_if_needed(req.raw_mode_b, mode, funds, handle.constraints)
    return {
        "goal": goal,
        "mode": mode,
        "funds": funds,
        "holdings": handle.holdings,
        "navs": handle.navs,
        "constraints": handle.constraints,
        "raw_mode_b": raw_mode_b,
        "from_intake": True,
    }


@app.get("/api/scenarios")
async def list_scenarios():
    """A scenario marked `"hidden": true` (demo_basic / demo_conflict /
    demo_mode_b - kept only as unit-test fixtures, superseded on stage by
    demo_cas_18 / demo_mode_b_cas) is left off this list but stays fully
    runnable by id - GET /api/scenarios/{name} and POST /api/run don't
    filter on it, so the test suite is unaffected."""
    out = []
    for path in sorted(SCENARIOS_DIR.glob("*.json")):
        sc = json.loads(path.read_text())
        if sc.get("hidden"):
            continue
        out.append({
            "id": path.stem,
            "description": sc.get("description", ""),
            "mode": sc["goal"].get("mode", "A"),
        })
    return {"scenarios": out}


@app.get("/api/scenarios/{name}")
async def get_scenario(name: str):
    """Backs the fund-picker (diagram step 0, Mode A: 'the distributor
    picks the funds') - the candidate list it ticks from is this run's
    actual holdings, not the whole AMFI universe."""
    sc = _load_scenario(name)
    holdings = load_holdings(sc["holdings"])
    funds = []
    for scheme_id in sorted(holdings.schemes()):
        lots = [l for l in holdings.lots if l.scheme_id == scheme_id]
        nav = sc["navs"].get(scheme_id)
        units = sum(l.units for l in lots)
        funds.append({
            "scheme_id": scheme_id,
            "label": _display_name(scheme_id),
            "folios": sorted({l.folio for l in lots}),
            "units": units,
            "current_value": round(units * nav, 2) if nav is not None else None,
        })
    return {
        "id": name,
        "goal": sc["goal"],
        "candidate_schemes": sc["candidate_schemes"],
        "funds": funds,
    }


def _display_name(scheme_id: str) -> str:
    return scheme_id.removeprefix("s_").replace("_", " ").title()


def _build_extraction_client():
    """Picks the extraction provider from LLM_PROVIDER (default
    "anthropic") - "gemini" works with a free Google AI Studio key, no
    billing needed. Fails fast with a clear message rather than letting a
    missing key surface as a buried per-document warning later."""
    provider = os.environ.get("LLM_PROVIDER", "anthropic").strip().lower()
    if provider == "gemini":
        if not os.environ.get("GEMINI_API_KEY"):
            raise HTTPException(
                status_code=500,
                detail="LLM_PROVIDER=gemini needs GEMINI_API_KEY set (see .env.example) - "
                       "get a free key at https://aistudio.google.com/apikey",
            )
        return GeminiExtractionClient()
    if provider != "anthropic":
        raise HTTPException(
            status_code=500,
            detail=f"unknown LLM_PROVIDER {provider!r} - use 'anthropic' or 'gemini'",
        )
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(
            status_code=500,
            detail="document intake needs ANTHROPIC_API_KEY set (see .env.example), "
                   "or set LLM_PROVIDER=gemini and GEMINI_API_KEY for a free key",
        )
    return AnthropicExtractionClient()


@app.post("/api/intake")
async def intake(documents: list[UploadFile] = File(...)):
    """Upload any mix of SID/addendum/SAI/KIM PDFs, portfolio-statement
    PDFs, and portfolio SCREENSHOTS (PNG/JPG/WEBP - or a PDF with no text
    layer, which falls back to the same vision path): each is classified,
    resolved to a fund identity against AMFI, and mined for constraints or
    holdings. A screenshot row missing its purchase date or NAV (most
    portfolio screens don't show them) comes back in `incomplete_holdings`
    rather than being dropped or guessed - complete it via
    `POST /api/intake/{id}/complete` before running. Returns the fund list
    the Mode A/B picker then runs against - no scenario file involved."""
    if not documents:
        raise HTTPException(status_code=400, detail="upload at least one file")

    client = _build_extraction_client()

    try:
        directory = load_amfi_directory()
    except Exception:
        directory = {}  # offline / first run with no cache - resolution just falls back

    raw_docs = [(f.filename or "upload.pdf", await f.read()) for f in documents]
    result = run_intake(raw_docs, extraction_client=client, amfi_directory=directory)

    intake_id = f"intake_{uuid.uuid4().hex[:12]}"
    _INTAKE_HANDLES[intake_id] = result
    return {
        "intake_id": intake_id,
        "funds": [asdict(f) for f in result.funds],
        "holdings_preview": [asdict(l) for l in result.holdings.lots],
        "incomplete_holdings": [asdict(h) for h in result.incomplete_holdings],
        "warnings": result.warnings,
    }


class CompleteHoldingItem(BaseModel):
    scheme_id: str
    units: float
    folio: str
    purchase_date: str
    purchase_nav: float


class CompleteIntakeRequest(BaseModel):
    completions: list[CompleteHoldingItem]


@app.post("/api/intake/{intake_id}/complete")
async def complete_intake(intake_id: str, req: CompleteIntakeRequest):
    """Fills in the purchase date / NAV / folio a portfolio screenshot
    didn't show - never guessed, always human-supplied. The UI shows every
    `incomplete_holdings` row with a small form; this takes the completed
    set back and turns it into real holdings. Clears the incomplete list -
    the UI is expected to resubmit everything it was shown, once."""
    handle = _INTAKE_HANDLES.get(intake_id)
    if handle is None:
        raise HTTPException(status_code=404, detail=f"no intake named {intake_id!r}")

    lot_seq = len(handle.holdings.lots)
    added = []
    for i, c in enumerate(req.completions):
        lot_seq += 1
        try:
            validated = load_holdings([{
                "lot_id": f"complete_{lot_seq}", "scheme_id": c.scheme_id, "folio": c.folio,
                "units": c.units, "purchase_date": c.purchase_date, "purchase_nav": c.purchase_nav,
            }])
        except MalformedHoldingError as exc:
            raise HTTPException(status_code=400, detail=f"completion {i}: {exc}")
        added.extend(validated.lots)

    handle.holdings.lots.extend(added)
    handle.incomplete_holdings = []
    return {
        "intake_id": intake_id,
        "holdings_preview": [asdict(l) for l in handle.holdings.lots],
        "incomplete_holdings": [],
    }


def _select_hunter(inputs: dict):
    """Folder-mode scenarios stay deterministic and offline - `FolderHunter`
    only ever serves what a fixture already carries, on purpose, so the
    demo is repeatable. A run built from an upload already needed a
    working LLM key to get this far (`/api/intake`), so HUNT continues
    onto the real web from there: a rule a CAS/statement upload didn't
    come with (no SID for that scheme, only holdings) is exactly what
    `WebHunter` is for - `FolderHunter` can never supply a rule it was
    never handed, no matter how many times the loop retries it."""
    if inputs.get("from_intake"):
        return WebHunter(extraction_client=_build_extraction_client())
    return FolderHunter(inputs["constraints"])


@app.post("/api/run")
async def start_run(req: RunRequest):
    inputs = _build_inputs(req)
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    handle = _Handle()
    handle.inputs = inputs
    _HANDLES[run_id] = handle
    store.create_run(run_id, Trigger.GOAL.value, datetime.now(timezone.utc).isoformat())

    loop = asyncio.get_event_loop()

    def on_event(ev: dict) -> None:
        store.append_trace(run_id, ev)
        loop.call_soon_threadsafe(handle.queue.put_nowait, ev)

    def execute():
        tracer = TraceLog(on_event=on_event)
        evidence = EvidenceStore()
        evidence.put_many(inputs["constraints"], "seed")
        handle.evidence = evidence
        try:
            hunter = _select_hunter(inputs)
            result = run_loop(
                inputs["goal"], inputs["mode"], inputs["funds"], inputs["holdings"],
                inputs["navs"], hunter=hunter, evidence=evidence, trace=tracer,
                raw_mode_b=inputs["raw_mode_b"],
            )
            handle.result = result
            store.finish_run(run_id, result)
        except Exception as exc:  # surfaced on the stream, not a crashed worker
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            handle.error = str(detail)
            store.fail_run(run_id, str(detail))
        finally:
            handle.done = True
            loop.call_soon_threadsafe(handle.queue.put_nowait, None)

    loop.run_in_executor(None, execute)
    return {"run_id": run_id}


@app.get("/api/stream/{run_id}")
async def stream(run_id: str):
    handle = _HANDLES.get(run_id)
    if handle is None:
        # Replay from the store for a completed run.
        events = store.get_trace(run_id)
        if not events:
            raise HTTPException(status_code=404, detail="unknown run_id")

        async def replay():
            for ev in events:
                yield f"data: {json.dumps(ev)}\n\n"
            yield "event: done\ndata: {}\n\n"

        return StreamingResponse(replay(), media_type="text/event-stream")

    async def event_source():
        while True:
            ev = await handle.queue.get()
            if ev is None:
                yield "event: done\ndata: {}\n\n"
                break
            yield f"data: {json.dumps(ev)}\n\n"

    return StreamingResponse(event_source(), media_type="text/event-stream")


@app.get("/api/plans/{run_id}")
async def get_plans(run_id: str):
    handle = _HANDLES.get(run_id)
    if handle is not None:
        if handle.error:
            raise HTTPException(status_code=500, detail=handle.error)
        if not handle.done or handle.result is None:
            raise HTTPException(status_code=202, detail="run still in progress")
        return _plans_payload(handle.result)

    row = store.get_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown run_id")
    if row["status"] == "error":
        raise HTTPException(status_code=500, detail=row.get("error") or "run failed")
    if row["status"] != "done":
        raise HTTPException(status_code=202, detail="run still in progress")
    return {
        "plans": row["plans"],
        "abstentions": row["abstentions"],
        "stopped_reason": row["stopped_reason"],
        "approved_choice": row["approved_choice"],
    }


def _plans_payload(result) -> dict:
    return {
        "plans": asdict(result.plans) if result.plans is not None else None,
        "abstentions": [asdict(a) for a in result.abstentions],
        "stopped_reason": result.stopped_reason,
        "trigger": result.trigger,
    }


def _names_for(scheme_ids) -> dict:
    return {sid: _display_name(sid) for sid in scheme_ids}


@app.get("/api/runs/{run_id}/context")
async def run_context(run_id: str):
    """The inputs a finished run acted on - goal, holdings, and the
    constraints as the evidence store finally held them (supersession
    applied). Backs the 'prove me wrong' panel and explain-to-client:
    both need the exact constraints the plan was verified against."""
    handle = _HANDLES.get(run_id)
    if handle is None or handle.result is None:
        raise HTTPException(status_code=404, detail="no completed in-memory run for that id")
    inp = handle.inputs
    constraints = handle.evidence.constraints() if handle.evidence else inp["constraints"]
    return {
        "goal": asdict(inp["goal"]),
        "mode": inp["mode"],
        "candidate_schemes": inp["funds"],
        "holdings": [asdict(l) for l in inp["holdings"].lots],
        "constraints": [asdict(c) for c in constraints],
        "navs": inp["navs"],
        "names": _names_for(inp["holdings"].schemes()),
    }


@app.post("/api/explain")
async def explain_endpoint(req: ExplainRequest):
    """Explain-to-client (§5.10): the verified plan regenerated in plain
    language, citations kept. Pure string assembly over the already-
    verified plan - no LLM call."""
    if req.choice not in ("cheapest", "merit_preserving"):
        raise HTTPException(status_code=400, detail="choice must be cheapest or merit_preserving")
    handle = _HANDLES.get(req.run_id)
    if handle is None or handle.result is None or handle.result.plans is None:
        raise HTTPException(status_code=404, detail="no completed in-memory run for that id")
    plan = handle.result.plans.get(req.choice)
    constraints = handle.evidence.constraints() if handle.evidence else handle.inputs["constraints"]
    mode_label = "Mode A (you picked the funds)" if handle.inputs["mode"] == "A" \
        else "Mode B (agent scored the funds from sourced evidence)"
    text = explain_plan(
        plan, constraints,
        names=_names_for(handle.inputs["holdings"].schemes()),
        mode_label=mode_label,
        abstentions=handle.result.abstentions,
    )
    return {"run_id": req.run_id, "choice": req.choice, "text": text,
            "verdict": plan.verdict}


@app.post("/api/reverse")
async def reverse_endpoint(req: ReverseRequest):
    """Reverse mode (§5.9). Same machinery as the forward loop - stacked
    addenda are resolved, a genuine tie abstains - but the question is
    'what is free to take out today', not 'how do I hit a target'."""
    inputs = _build_inputs(req)
    plan = reverse_mode(
        inputs["holdings"], inputs["constraints"], inputs["navs"], inputs["funds"],
    )
    return {
        "as_of": plan.as_of,
        "total_free_value": plan.total_free_value,
        "total_tax": plan.total_tax,
        "net_if_all_taken": plan.net_if_all_taken,
        "slices": [asdict(s) for s in plan.slices],
        "abstained": [asdict(a) for a in plan.abstained],
        "names": _names_for(inputs["funds"]),
    }


@app.post("/api/reconcile")
async def reconcile_endpoint(req: ReconcileRequest):
    """Multi-RTA reconciliation (§5.4): resolve scheme identity across
    feed spellings, dedupe lots reported twice, keep every folio as its
    own FIFO queue, and flag - never silently merge - anything ambiguous."""
    try:
        directory = load_amfi_directory()
    except Exception:
        directory = {}
    rows = [
        RawRow(source=r.source, scheme_name=r.scheme_name, folio=r.folio,
               units=r.units, purchase_date=r.purchase_date, purchase_nav=r.purchase_nav)
        for r in req.rows
    ]
    result = reconcile(rows, directory, known=req.known)
    return {
        "kept_rows": result.kept_rows,
        "dropped_rows": result.dropped_rows,
        "needs_human": result.needs_human,
        "resolved": result.resolved,
        "holdings": [asdict(l) for l in result.holdings.lots],
        "flags": [asdict(f) for f in result.flags],
    }


@app.post("/api/plans/{run_id}/approve")
async def approve(run_id: str, req: ApproveRequest):
    if req.choice not in ("cheapest", "merit_preserving"):
        raise HTTPException(status_code=400, detail="choice must be cheapest or merit_preserving")
    row = store.get_run(run_id)
    if row is None and run_id not in _HANDLES:
        raise HTTPException(status_code=404, detail="unknown run_id")
    handle = _HANDLES.get(run_id)
    result = handle.result if handle else None
    if result is not None:
        plan = result.plans.get(req.choice) if result.plans else None
        if plan is None:
            raise HTTPException(status_code=409, detail="no plan to approve")
        if plan.verdict != "verified":
            raise HTTPException(status_code=409, detail="that plan is not verified - cannot approve")
    store.approve(run_id, req.choice)
    return {"run_id": run_id, "approved_choice": req.choice, "approved": True}


@app.post("/api/verify")
async def verify_endpoint(req: VerifyRequest):
    """'Prove me wrong': edit any constraint in the UI, watch the verifier
    catch the injected error independently."""
    plan = plan_from_dict(req.plan)
    constraints = [constraint_from_dict(c) for c in req.constraints]
    try:
        holdings = load_holdings(req.holdings)
    except MalformedHoldingError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return [asdict(v) for v in verify(plan, constraints, holdings)]


@app.get("/api/watch")
async def list_watches():
    return {"watches": store.list_watches()}


@app.post("/api/watch/{run_id}")
async def add_watch(run_id: str):
    handle = _HANDLES.get(run_id)
    if handle is None or handle.result is None or handle.result.plans is None:
        raise HTTPException(status_code=404, detail="no completed run with plans for that id")
    store.add_watch(
        run_id, handle.inputs["goal"], handle.inputs["navs"],
        snapshot_hashes(handle.evidence), datetime.now(timezone.utc).isoformat(),
    )
    return {"run_id": run_id, "status": "watching"}


@app.post("/api/watch/{run_id}/check")
async def run_watch_check(run_id: str, current_navs: dict[str, float]):
    handle = _HANDLES.get(run_id)
    watches = {w["run_id"]: w for w in store.list_watches()}
    if handle is None or run_id not in watches:
        raise HTTPException(status_code=404, detail="not watching that run")
    inputs = handle.inputs
    report = check_plan(
        run_id=run_id, goal=inputs["goal"], mode=inputs["mode"], fund_ids=inputs["funds"],
        holdings=inputs["holdings"], current_navs=current_navs, evidence=handle.evidence,
        hunter=_select_hunter(inputs),
        baseline_navs=watches[run_id]["navs"], baseline_hashes=watches[run_id]["hashes"],
        prior=handle.result.plans if handle.result else None,
    )
    store.update_watch(
        run_id, navs=current_navs, hashes=snapshot_hashes(handle.evidence),
        last_checked=datetime.now(timezone.utc).isoformat(),
        last_event="changed" if report.plans_changed else "still valid",
    )
    return {
        "triggered": report.triggered,
        "reasons": report.reasons,
        "plans_changed": report.plans_changed,
        "plans": _plans_payload(report.result) if report.result else None,
    }


if WEB_DIST.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIST), html=True), name="web")
