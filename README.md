# Nikaas

**It goes and finds out. Then it proves it.**

Given a redemption goal and a list of fund names, Nikaas runs a goal-driven
loop — observe what's missing, decide which gap to close and by which route,
hunt the open web / use cache / re-fetch / abstain / stop, resolve stacked
addenda, allocate **two** plans (cheapest-exit and merit-preserving), prove
both correct against an independent zero-AI verifier, self-check, and hand
them to a human to choose between. A Watcher keeps approved plans valid with
no prompt. Every step emits a trace event.

The loop diagram this repo implements is the ASCII sketch immediately below.

## The loop

```
  HUMAN: goal in
        │
  step 0  SELECT ─────────── Mode A (human ticks) / Mode B (agent scores from sourced evidence)
        │
  ┌─────────────────────────────── THE LOOP (order not fixed) ──────────────────────────────┐
  │ 1 OBSERVE   gap list rebuilt every pass: missing / stale evidence, unresolved conflict,  │
  │             no allocation, plan rejected, not self-checked, self-check doubt             │
  │ 2 DECIDE    which gap, which route → USE_CACHE ~0 · REFETCH · HUNT · RESOLVE ·           │
  │             ALLOCATE · SELF_CHECK · ABSTAIN (2 tries failed) · STOP (budget low)         │
  │ 3 act + INCORPORATE   fold the result back in — new gaps can appear here                 │
  └────────────────────────────────────────────────────────────────────────────────────────┘
        │  (no gaps left)
  VERIFIED PLANS   two plans · clause-level citations · abstentions listed · 30-day frontier
        │
  HUMAN: approve one   (intent — the verifier already checked correctness)

  THE WATCHER   standing goal "keep plans valid" — NAV move / target date / doc-hash change
  TRACE LOG     every step emits an event; retries and dead ends included, not hidden
```

Two independent safeguards doing different jobs: `agent/verifier.py` is pure
Python with **zero AI imports** and checks *correctness*; the human gate
(`POST /api/plans/{id}/approve`) checks *intent*. They are never conflated.

## File map

```
/agent
  contracts.py   shapes: Goal, Gap, Route, Trigger, Candidate, Constraint, Evidence,
                 Plan, TwoPlans, Violation, Abstention, TraceEvent
  budget.py      step / token budget; drives the STOP route
  trace.py       TraceLog — one event per step, no silent steps
  evidence.py    the evidence store — sourced/dated facts + content hash, freshness per kind
  observe.py     step 1 — the only place gaps are computed
  decide.py      step 2 — dynamic action selection (which gap, which route)
  act.py         step 3 — run the route, then incorporate the result into state
  loop.py        THE LOOP + the run State
  hunter.py      HUNT the open web — FolderHunter (offline) + WebHunter (real: httpx,
                 pdfplumber, OCR fallback, robots/delay)
  amc_registry.py  AMFI directory ingest + per-AMC document adapters
  extractor.py   LLM: page text → constraints, page + clause required
  resolver.py    supersession across stacked addenda by effective_date; tie → abstain
  selector.py    Mode A passthrough / Mode B transparent weighted score
  merit.py       Mode-A fallback merit heuristic ("funds worth keeping")
  allocator.py   allocate_two → cheapest + merit-preserving + frontier over the
                 decision window ending on the goal date
  verifier.py    pure Python, ZERO AI imports — recomputes FIFO / exit load / tax
  selfcheck.py   adversarial pass; doubts become gaps
  watcher.py     THE WATCHER — re-enters the loop on NAV / date / doc-hash change
  reverse.py     reverse mode (§5.9) — what is free to redeem today at zero exit
                 load and least tax; resolves stacked addenda, abstains on a tie
  explain.py     explain-to-client (§5.10) — the verified plan in plain language
                 with citations kept; pure string assembly, NO llm import
  reconcile.py   multi-RTA reconciliation (§5.4) — resolve scheme identity across
                 CAMS/KFintech spellings, dedupe repeated lots, keep every folio
                 as its own FIFO queue, flag ambiguity instead of merging
  holdings.py    holdings ingest + malformed-input rejection
  intake.py      uploaded documents → the loop's inputs (classify, resolve, extract)
  screenshot.py  portfolio SCREENSHOTS (§5.11 roadmap item, built) — read directly
                 by vision (no OCR binary), row missing purchase date/NAV goes to
                 `incomplete_holdings` for a human to fill in, never guessed
  pdfparse.py    PDF → page text (pdfplumber, OCR fallback) + `render_page_png`
                 (a page with no text layer at all falls back to the vision path)
  store.py       SQLite persistence for runs, traces, watches
  serde.py       JSON ↔ dataclass for the API
  tests/         pytest — loop, observe, decide, evidence, selfcheck, verifier,
                 allocator, hunter, holdings, watcher, extractor, intake,
                 screenshot, reverse, explain, reconcile, demo_cas_18,
                 demo_real_funds, demo_scenarios
/api
  main.py        FastAPI + SSE trace stream; serves web/dist at /. Endpoints:
                 /api/scenarios · /api/run · /api/stream · /api/plans · /approve
                 /api/verify ("prove me wrong") · /api/runs/{id}/context
                 /api/explain · /api/reverse · /api/reconcile · /api/watch* ·
                 /api/intake · /api/intake/{id}/complete (fill in a screenshot's
                 missing purchase data)
/web             React + Vite + Tailwind v4 + Recharts — built-in-scenario or
                 upload source tabs (PDFs AND screenshots), goal form, Mode A
                 fund-picker, an incomplete-holdings completion form, live trace
                 with the Mode B weighted-score breakdown, two-plan compare,
                 frontier chart, approve gate, and collapsible panels for
                 explain-to-client, "prove me wrong", the Watcher, reverse
                 mode, and multi-RTA reconciliation
/data
  scenarios/     synthetic fixtures — see the table below
  samples/       sample_cas_18.pdf — a synthetic CAMS-style statement matching
                 demo_cas_18 (visual artifact; the JSON scenario is the runnable
                 path)
tools/
  build_demo_cas_18.py       regenerates demo_cas_18.json, demo_mode_b_cas.json,
                             sample_cas_18.pdf
  build_demo_scenarios.py    regenerates demo_timing / demo_abstain / demo_unachievable
  build_demo_real_funds.py   regenerates demo_real_funds.json (real scheme codes
                             and sourced exit-load rules — see its docstring)
  build_sample_cas_pdfs.py   regenerates the CAMS/KFintech/scanned sample PDFs
                             under data/samples/ for exercising /api/intake
```

### Demo scenarios (the web UI's dropdown)

Each isolates one behaviour so it reads on stage. Every scheme NAME in
`demo_cas_18` / `demo_mode_b_cas` / `demo_timing` / `demo_abstain` /
`demo_unachievable` is invented (so is its exit-load rule); holdings are
always synthetic, everywhere, no exceptions.

| scenario | what it shows |
|----------|---------------|
| `demo_real_funds` | **real, currently-traded schemes under their real AMFI scheme codes**, with exit-load rules sourced from public scheme documents (Quant Small Cap Fund's is machine-verified against its actual SID PDF, page 3) instead of invented — see `tools/build_demo_real_funds.py` for the full citation trail. Holdings are still synthetic. The plan pays a real, sourced, non-zero exit load |
| `demo_cas_18` | the PRD's hard case — 18 (invented) schemes, RESOLVE + HUNT + ELSS lock-in repair + a same-date tie → abstain + two-folio FIFO separation, one run, non-zero exit load |
| `demo_mode_b_cas` | the same 18-scheme portfolio through Mode B — the weighted-score panel at scale |
| `demo_timing` | the cost-vs-sell-date frontier steps from ~Rs.5,800 to zero as a 365-day window lapses; the self-check reaches for the cheaper date, tests it, and backs off because T+3 settlement would miss the deadline |
| `demo_abstain` | a candidate fund with no retrievable rule — HUNT fails twice, the agent abstains, excludes it, and still meets the goal from the rest |
| `demo_unachievable` | the goal is larger than the whole portfolio — SHORTFALL, the loop stops with "goal not reachable from the available holdings", nothing is approvable |

`demo_basic` / `demo_conflict` / `demo_mode_b` still exist under
`data/scenarios/` (`"hidden": true`) — they're the fixtures behind most of
`agent/tests/`, superseded on stage by the two above at a more presentable
scale. `GET /api/scenarios` (the dropdown) leaves them out; `GET
/api/scenarios/{name}` and `POST /api/run` still run them by id.

## Setup

### Backend

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # an LLM key is needed only for PDF intake / live WebHunter;
                              # the scenarios and the whole test suite run without one
pytest -q                     # 107 tests
uvicorn api.main:app --reload --port 8000
```

### Frontend

```bash
cd web
npm install
npm run dev                   # proxies /api to :8000 (web/vite.config.ts)
```

## Trying it

```bash
curl localhost:8000/api/scenarios                                     # demo_basic, demo_cas_18, ...
curl -X POST localhost:8000/api/run -H 'Content-Type: application/json' -d '{"scenario":"demo_cas_18"}'
# -> {"run_id":"run_..."}
curl -N localhost:8000/api/stream/run_...      # live SSE trace
curl localhost:8000/api/plans/run_...          # two verified plans + frontier + abstentions
curl localhost:8000/api/runs/run_.../context   # goal, holdings, the constraints the plan used
curl -X POST localhost:8000/api/explain -d '{"run_id":"run_...","choice":"cheapest"}'
curl -X POST localhost:8000/api/reverse -d '{"scenario":"demo_cas_18"}'
curl -X POST localhost:8000/api/plans/run_.../approve -d '{"choice":"merit_preserving"}'
```

The routes differ by scenario, which is the proof action selection is dynamic
rather than a fixed pipeline:

| scenario           | route sequence |
|--------------------|----------------|
| `demo_basic`       | `REFETCH → REFETCH → ALLOCATE → SELF_CHECK` |
| `demo_conflict`    | `HUNT → HUNT → RESOLVE → ALLOCATE → SELF_CHECK` |
| `demo_cas_18`      | `HUNT → RESOLVE → RESOLVE → ALLOCATE → ALLOCATE → ALLOCATE → SELF_CHECK` (the extra ALLOCATEs are the ELSS lock-in repair loop) |
| `demo_timing`      | `ALLOCATE → SELF_CHECK → ALLOCATE → ALLOCATE → SELF_CHECK` (self-check reaches for the cheaper date, settlement rejects it, it backs off) |
| `demo_abstain`     | `HUNT → HUNT → ABSTAIN → ALLOCATE → SELF_CHECK` |
| `demo_unachievable`| `ALLOCATE → ALLOCATE → ALLOCATE → STOP` |

`POST /api/verify` powers "prove me wrong": hand it a plan, constraints and
holdings (optionally with one constraint tampered) and the verifier catches
it independently. The web UI's "Prove me wrong" panel wires this to a
one-field editor over the constraints the plan actually used.

## Live hunting (WebHunter)

`agent/hunter.py`'s `WebHunter` is real: it resolves the AMC against AMFI's
scheme master, finds SID / addendum PDFs via a per-AMC adapter (or a generic
scan), fetches them politely (robots.txt, delay, honest User-Agent), parses
with `pdfplumber` (OCR fallback via `pytesseract` for scans), and runs the
extractor. Live use needs: outbound network, an extraction API key, and —
only for scanned SIDs — the `tesseract` binary. Tests use `FolderHunter` and
never touch the network.

**Which hunter a run gets is automatic, not a manual choice**
(`api/main.py`'s `_select_hunter`): a **scenario run** always gets
`FolderHunter` seeded from the fixture, so the demo stays deterministic and
fully offline. A run built from an **upload** (`intake_id` set) always gets
`WebHunter` — because getting that far already needed a working key, and a
scheme a CAS/statement mentioned but no SID was uploaded for has no rule
`FolderHunter` could ever produce; it can only replay what it was already
handed, forever, on every retry. (Earlier this build always used
`FolderHunter`, including for uploads, which meant a fund named only in a
CAS could never resolve — silently abstained, never explained, since the
retry loop always failed the same static way.) The Watcher's re-check
(`POST /api/watch/{id}/check`) makes the same choice.

Still open before live hunting is production-ready: the AMC adapter set
covers five AMCs by AUM (Axis, HDFC, SBI, ICICI Prudential, Kotak) plus a
generic fallback (add more in `amc_registry.ADAPTERS`). The cached AMFI
directory under `data/amc/` is a partial capture of `NAVAll.txt` (missing
some common schemes) — `load_amfi_directory(refresh=True)` needs network to
rebuild it in full; until then `reconcile.py` flags an uncertain scheme
match for a human rather than guessing.

Mode B's merit score is a transparent weighted sum over exit load, expense
ratio and lock-in status — see `agent/selector.py`'s `build_mode_b_factors`.
Returns/risk-adjusted factors are intentionally not included: neither SID
nor addendum text carries them, so scoring on them would mean inventing a
number without a citation, which the diagram's "sourced evidence" rule
forbids. Wiring those in needs a new data source (AMFI NAV history for
returns, a ratings feed for risk-adjusted metrics) before they can be added
honestly.

## Screenshot / image intake (§5.11 roadmap item, built)

`POST /api/intake` also accepts a portfolio **screenshot** (PNG/JPG/WEBP) —
or a PDF with no text layer at all (a scan, or a screenshot saved as a PDF),
which falls back to the same path. Neither needs the `tesseract` binary:
the image goes straight to a vision-capable model
(`agent/screenshot.py` + the `extract_image` method on both LLM clients),
which reads it directly.

**The one rule that governs this path:** a portfolio *screen* usually shows
current units and current value, not the *purchase* date and NAV FIFO
actually needs — unlike a real CAS, which has these by construction. A row
missing either is never dropped and never backfilled with a guess: it comes
back in `incomplete_holdings`, and the web UI renders a small form for each
one (folio / purchase date / purchase NAV) that a human fills in and posts
to `POST /api/intake/{id}/complete` before running. A row that names no
recognizable scheme, or a screenshot that turns out to be a photographed
scheme document rather than a portfolio screen, is declined with a clear
warning rather than mis-read.

## Robustness (PRD §12)

Published with honest results — the cases that are only partially handled say
so. "Handled" means there is code on the path and a test; "partial" means the
behaviour is safe (degrade / flag / abstain) but not the full ideal.

| # | Case | Behaviour | Status |
|---|------|-----------|--------|
| 1 | SID is a scanned image, not text | `pdfparse` tries `pytesseract` OCR first; if that's unavailable or the page is a screenshot, `agent/screenshot.py` reads it directly via vision instead — a row it can't fully specify is surfaced (`incomplete_holdings`), not guessed or dropped; only if vision itself is unavailable (no LLM key) does intake warn "no extractable text" and skip the doc | handled |
| 2 | Two addenda share an effective_date and contradict | `resolver.resolve` returns an `Ambiguity`; the loop abstains that scheme and records why (`demo_cas_18` Franklin Focused) | handled — `test_resolver`, `test_demo_cas_18` |
| 3 | Scheme renamed after a merger; old name returns nothing | `reconcile` matches on the normalized name and, when the token identity doesn't agree, emits `ambiguous_scheme` / `unresolved_scheme` `needs_human` — it never force-matches | partial (flags for a human; no automatic rename resolution) |
| 4 | AMC site times out / 500s | `WebHunter.hunt` catches per-URL failures and continues; nothing usable → `ok=False` → the loop retries HUNT twice then ABSTAINs and flags the scheme unverified | handled (logic; no adversarial test) |
| 5 | Addendum exists but is unreachable | The reachable docs are still extracted; the missing one shows up as stale / low-confidence evidence the verifier's freshness check can catch | partial (not an explicit "known gap" flag yet) |
| 6 | Malformed input (negative units, future purchase date) | `holdings.load_holdings` raises `MalformedHoldingError` at ingest; `reconcile` turns it into a `malformed_lot` flag and drops the row | handled — `test_holdings`, `test_reconcile` |
| 7 | Goal unachievable (portfolio worth less than target) | Verifier raises `SHORTFALL`; repair widens once, still short → the plan stays `verdict: rejected` with the shortfall shown and the loop stops. No partial plan is passed off as done | handled — `test_verifier` |
| 8 | Two folios, same scheme, different histories | `Holdings.for_folio` keeps the queues separate; the verifier's `FIFO_MERGE` check rejects any leg that would need both (`demo_cas_18` Axis Bluechip / HDFC Top 100) | handled — `test_verifier`, `test_reconcile` |
| 9 | Mode B factor has no retrievable source | `selector.select_mode_b` omits the factor, lists it in `omitted_factors`, and never scores on it; `verifier.verify_mode_b_factors` independently rejects an uncited factor | handled — `test_selector` |

## No real client data

Everything under `data/` is synthetic. Nothing in this repo originates from
an actual client portfolio. `data/samples/sample_cas_18.pdf` is a generated
CAMS-style statement built from the `demo_cas_18` fixture — no real investor,
folio or PAN.
