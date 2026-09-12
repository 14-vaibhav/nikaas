/**
 * Thin client for the Nikaas agent API (see api/main.py). Paths are
 * relative - Vite proxies /api to localhost:8000 in dev; in production
 * FastAPI serves the built app itself.
 */

import type {
  Constraint,
  Mode,
  Plan,
  PlanChoice,
  PlansResponse,
  ReconcileResponse,
  ReversePlan,
  RunContext,
  ScenarioSummary,
  TraceEvent,
  Violation,
  WatchCheckResponse,
} from "./contracts";

export interface RunRequest {
  scenario?: string;
  intake_id?: string;
  mode?: Mode;
  goal?: { amount: number; by_date: string; mode?: Mode };
  funds?: string[];
  holdings?: Record<string, unknown>[];
  constraints?: Record<string, unknown>[];
  navs?: Record<string, number>;
}

async function asError(res: Response): Promise<never> {
  let detail = `${res.status} ${res.statusText}`;
  try {
    const body = await res.json();
    if (body?.detail)
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
  } catch {
    /* keep the status line */
  }
  throw new Error(detail);
}

export async function startRun(req: RunRequest = {}): Promise<string> {
  const res = await fetch("/api/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) await asError(res);
  const { run_id } = (await res.json()) as { run_id: string };
  return run_id;
}

export interface TraceStream {
  close: () => void;
}

export function streamTrace(
  runId: string,
  onEvent: (e: TraceEvent) => void,
  onDone: () => void,
  onError?: (err: Error) => void,
): TraceStream {
  const es = new EventSource(`/api/stream/${runId}`);
  es.onmessage = (msg) => {
    try {
      onEvent(JSON.parse(msg.data) as TraceEvent);
    } catch {
      /* ignore keep-alive frames */
    }
  };
  es.addEventListener("done", () => {
    es.close();
    onDone();
  });
  es.onerror = () => {
    if (es.readyState === EventSource.CLOSED) {
      onError?.(new Error("trace stream closed unexpectedly"));
    }
  };
  return { close: () => es.close() };
}

/** GET /api/plans/{run_id} - 202 until the run finishes. */
export async function getPlans(runId: string): Promise<PlansResponse> {
  const res = await fetch(`/api/plans/${runId}`);
  if (!res.ok) await asError(res);
  return (await res.json()) as PlansResponse;
}

/** POST /api/plans/{run_id}/approve - the human gate (intent, not correctness). */
export async function approvePlan(
  runId: string,
  choice: PlanChoice,
): Promise<{ approved: boolean; approved_choice: PlanChoice }> {
  const res = await fetch(`/api/plans/${runId}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ choice }),
  });
  if (!res.ok) await asError(res);
  return await res.json();
}

export interface IntakeFund {
  scheme_id: string;
  label: string;
  amc: string | null;
  resolved: boolean;
  document: string;
  document_kind: string;
  isin: string | null;
  nav: number | null;
}

export interface IntakeLot {
  lot_id: string;
  scheme_id: string;
  folio: string;
  units: number;
  purchase_date: string;
  purchase_nav: number;
}

export interface IncompleteHolding {
  filename: string;
  scheme_id: string;
  label: string;
  units: number;
  missing: string[];
  folio: string | null;
  current_value: number | null;
}

export interface IntakeResponse {
  intake_id: string;
  funds: IntakeFund[];
  holdings_preview: IntakeLot[];
  incomplete_holdings: IncompleteHolding[];
  warnings: string[];
}

/** POST /api/intake - upload any mix of SID/addendum/statement PDFs AND
 * portfolio screenshots (PNG/JPG/WEBP, or a PDF with no text layer - both
 * read directly by vision, no OCR binary needed). The agent classifies
 * each, resolves fund identity against AMFI, and mines constraints or
 * holdings from them - this is what the Mode A/B picker then runs against
 * instead of a scenario fixture. A screenshot row missing its purchase
 * date/NAV comes back in `incomplete_holdings`, not silently dropped -
 * see `completeIntake`. */
export async function uploadIntake(files: File[]): Promise<IntakeResponse> {
  const form = new FormData();
  files.forEach((f) => form.append("documents", f));
  const res = await fetch("/api/intake", { method: "POST", body: form });
  if (!res.ok) await asError(res);
  return (await res.json()) as IntakeResponse;
}

export interface HoldingCompletion {
  scheme_id: string;
  units: number;
  folio: string;
  purchase_date: string;
  purchase_nav: number;
}

/** POST /api/intake/{id}/complete - fills in the purchase date / NAV /
 * folio a portfolio screenshot didn't show. Never guessed on the server -
 * the human supplies these, or the fund stays out of any plan. */
export async function completeIntake(
  intakeId: string,
  completions: HoldingCompletion[],
): Promise<{ holdings_preview: IntakeLot[]; incomplete_holdings: IncompleteHolding[] }> {
  const res = await fetch(`/api/intake/${intakeId}/complete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ completions }),
  });
  if (!res.ok) await asError(res);
  return await res.json();
}

export interface VerifyRequest {
  plan: Plan;
  constraints: Constraint[];
  holdings: Record<string, unknown>[];
}

export async function verifyPlan(req: VerifyRequest): Promise<Violation[]> {
  const res = await fetch("/api/verify", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) await asError(res);
  return (await res.json()) as Violation[];
}

/** GET /api/scenarios - the built-in folder-mode fixtures. */
export async function listScenarios(): Promise<ScenarioSummary[]> {
  const res = await fetch("/api/scenarios");
  if (!res.ok) await asError(res);
  return ((await res.json()) as { scenarios: ScenarioSummary[] }).scenarios;
}

/** GET /api/runs/{id}/context - goal, holdings and the constraints the
 * plan was verified against. Backs "prove me wrong" and explain. */
export async function getContext(runId: string): Promise<RunContext> {
  const res = await fetch(`/api/runs/${runId}/context`);
  if (!res.ok) await asError(res);
  return (await res.json()) as RunContext;
}

/** POST /api/explain - the verified plan in plain client language. */
export async function explainPlan(
  runId: string,
  choice: PlanChoice,
): Promise<{ text: string; verdict: string }> {
  const res = await fetch("/api/explain", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ run_id: runId, choice }),
  });
  if (!res.ok) await asError(res);
  return (await res.json()) as { text: string; verdict: string };
}

/** POST /api/reverse - what is free to redeem today at zero exit load. */
export async function reversePlan(
  body: { scenario?: string; intake_id?: string; goal?: unknown; funds?: string[] },
): Promise<ReversePlan> {
  const res = await fetch("/api/reverse", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) await asError(res);
  return (await res.json()) as ReversePlan;
}

/** POST /api/reconcile - multi-RTA identity resolution + dedupe. */
export async function reconcileRows(
  rows: {
    source: string;
    scheme_name: string;
    folio: string;
    units: number;
    purchase_date: string;
    purchase_nav: number;
  }[],
): Promise<ReconcileResponse> {
  const res = await fetch("/api/reconcile", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rows }),
  });
  if (!res.ok) await asError(res);
  return (await res.json()) as ReconcileResponse;
}

/** POST /api/watch/{id} - subscribe the Watcher to this plan. */
export async function addWatch(runId: string): Promise<void> {
  const res = await fetch(`/api/watch/${runId}`, { method: "POST" });
  if (!res.ok) await asError(res);
}

/** POST /api/watch/{id}/check - re-check the plan against revised NAVs. */
export async function checkWatch(
  runId: string,
  currentNavs: Record<string, number>,
): Promise<WatchCheckResponse> {
  const res = await fetch(`/api/watch/${runId}/check`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(currentNavs),
  });
  if (!res.ok) await asError(res);
  return (await res.json()) as WatchCheckResponse;
}
