import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceDot,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  addWatch,
  approvePlan,
  checkWatch,
  completeIntake,
  explainPlan,
  getContext,
  getPlans,
  listScenarios,
  reconcileRows,
  reversePlan,
  startRun,
  streamTrace,
  uploadIntake,
  verifyPlan,
} from "./lib/api";
import type { HoldingCompletion, IntakeResponse } from "./lib/api";
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
} from "./lib/contracts";

interface PickerFund {
  scheme_id: string;
  label: string;
  folios: string[];
  units: number;
  current_value: number | null;
}

const money = (n: number) => "Rs." + Math.round(n).toLocaleString("en-IN");

const VERDICT_COLOR: Record<string, string> = {
  ok: "text-emerald-600",
  violation: "text-rose-600",
  abstained: "text-amber-600",
  retry: "text-amber-600",
};

const TYPE_BADGE: Record<string, string> = {
  select: "bg-slate-200 text-slate-700",
  observe: "bg-slate-100 text-slate-600",
  decide: "bg-indigo-100 text-indigo-700",
  hunt: "bg-blue-100 text-blue-700",
  cache: "bg-cyan-100 text-cyan-700",
  extract: "bg-blue-50 text-blue-600",
  resolve: "bg-violet-100 text-violet-700",
  allocate: "bg-indigo-100 text-indigo-700",
  verify: "bg-teal-100 text-teal-700",
  repair: "bg-rose-100 text-rose-700",
  self_check: "bg-amber-100 text-amber-800",
  abstain: "bg-rose-100 text-rose-700",
  stop: "bg-rose-200 text-rose-800",
  gate: "bg-emerald-100 text-emerald-800",
  watch: "bg-emerald-50 text-emerald-700",
};

type SourceTab = "scenario" | "upload";

export default function App() {
  const [tab, setTab] = useState<SourceTab>("scenario");

  // --- scenario path ---
  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([]);
  const [scenarioId, setScenarioId] = useState<string>("");

  // --- shared goal / mode ---
  const [mode, setMode] = useState<Mode>("A");
  const [amount, setAmount] = useState("");
  const [byDate, setByDate] = useState("");
  const [overrideGoal, setOverrideGoal] = useState(false);

  // --- upload path ---
  const [files, setFiles] = useState<File[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [intake, setIntake] = useState<IntakeResponse | null>(null);
  const [intakeLoading, setIntakeLoading] = useState(false);
  const [selectedFunds, setSelectedFunds] = useState<Set<string>>(new Set());

  // --- run state ---
  const [runId, setRunId] = useState<string | null>(null);
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [plans, setPlans] = useState<PlansResponse | null>(null);
  const [approved, setApproved] = useState<PlanChoice | null>(null);
  const [context, setContext] = useState<RunContext | null>(null);
  const streamRef = useRef<{ close: () => void } | null>(null);

  useEffect(() => {
    listScenarios()
      .then((s) => {
        setScenarios(s);
        if (s.length && !scenarioId) {
          const preferred = s.find((x) => x.id === "demo_cas_18") ?? s[0];
          setScenarioId(preferred.id);
          setMode(preferred.mode);
        }
      })
      .catch((e) => setError((e as Error).message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggleFund = useCallback((schemeId: string) => {
    setSelectedFunds((prev) => {
      const next = new Set(prev);
      if (next.has(schemeId)) next.delete(schemeId);
      else next.add(schemeId);
      return next;
    });
  }, []);

  const analyze = useCallback(async () => {
    if (files.length === 0) return;
    setError(null);
    setIntake(null);
    setPlans(null);
    setEvents([]);
    setIntakeLoading(true);
    try {
      const result = await uploadIntake(files);
      setIntake(result);
      setSelectedFunds(new Set(result.funds.map((f) => f.scheme_id)));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setIntakeLoading(false);
    }
  }, [files]);

  const completeHoldings = useCallback(
    async (completions: HoldingCompletion[]) => {
      if (!intake) return;
      setError(null);
      try {
        const result = await completeIntake(intake.intake_id, completions);
        setIntake((prev) =>
          prev
            ? {
                ...prev,
                holdings_preview: result.holdings_preview,
                incomplete_holdings: result.incomplete_holdings,
              }
            : prev,
        );
      } catch (err) {
        setError((err as Error).message);
      }
    },
    [intake],
  );

  const pickerFunds = useMemo<PickerFund[]>(() => {
    if (!intake) return [];
    return intake.funds.map((f) => {
      const lots = intake.holdings_preview.filter((l) => l.scheme_id === f.scheme_id);
      const units = lots.reduce((sum, l) => sum + l.units, 0);
      return {
        scheme_id: f.scheme_id,
        label: f.label + (f.resolved ? "" : " (unresolved against AMFI)"),
        folios: Array.from(new Set(lots.map((l) => l.folio))),
        units,
        current_value: f.nav != null ? units * f.nav : null,
      };
    });
  }, [intake]);

  const routeSeq = useMemo(
    () =>
      events
        .filter((e) => e.type === "decide" && e.output?.route)
        .map((e) => String(e.output!.route)),
    [events],
  );

  const selectEvent = useMemo(
    () => events.find((e) => e.type === "select"),
    [events],
  );

  const beginRun = useCallback(
    async (req: Parameters<typeof startRun>[0]) => {
      streamRef.current?.close();
      setError(null);
      setEvents([]);
      setPlans(null);
      setApproved(null);
      setContext(null);
      setRunning(true);
      try {
        const id = await startRun(req);
        setRunId(id);
        streamRef.current = streamTrace(
          id,
          (e) => setEvents((prev) => [...prev, e]),
          async () => {
            setRunning(false);
            try {
              setPlans(await getPlans(id));
              setContext(await getContext(id));
            } catch (err) {
              setError((err as Error).message);
            }
          },
          (err) => {
            setRunning(false);
            setError(err.message);
          },
        );
      } catch (err) {
        setRunning(false);
        setError((err as Error).message);
      }
    },
    [],
  );

  const runScenario = useCallback(() => {
    if (!scenarioId) return;
    const req: Parameters<typeof startRun>[0] = { scenario: scenarioId, mode };
    if (overrideGoal && amount && byDate) {
      req.goal = { amount: Number(amount), by_date: byDate, mode };
    }
    void beginRun(req);
  }, [scenarioId, mode, overrideGoal, amount, byDate, beginRun]);

  const runUpload = useCallback(() => {
    if (!intake || !amount || !byDate) return;
    const req: Parameters<typeof startRun>[0] = {
      intake_id: intake.intake_id,
      mode,
      goal: { amount: Number(amount), by_date: byDate, mode },
    };
    if (mode === "A") req.funds = Array.from(selectedFunds);
    void beginRun(req);
  }, [intake, mode, amount, byDate, selectedFunds, beginRun]);

  const approve = useCallback(
    async (choice: PlanChoice) => {
      if (!runId) return;
      try {
        await approvePlan(runId, choice);
        setApproved(choice);
      } catch (err) {
        setError((err as Error).message);
      }
    },
    [runId],
  );

  const reverseBody = useMemo(
    () => (tab === "scenario" ? { scenario: scenarioId } : { intake_id: intake?.intake_id }),
    [tab, scenarioId, intake],
  );

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <div className="mx-auto max-w-6xl px-6 py-8">
        <header className="mb-6">
          <h1 className="text-2xl font-bold tracking-tight">Nikaas</h1>
          <p className="text-sm text-slate-500">
            It goes and finds out. Then it proves it. &mdash; observe &rarr; decide
            &rarr; act &rarr; incorporate, until two verified plans reach you.
          </p>
        </header>

        {/* Source: built-in scenario or uploaded documents */}
        <section className="mb-6 rounded-lg border border-slate-200 bg-white p-4">
          <div className="mb-3 flex gap-1 rounded border border-slate-300 p-0.5 text-sm w-fit">
            {(["scenario", "upload"] as SourceTab[]).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={
                  "rounded px-3 py-1 " +
                  (tab === t ? "bg-slate-800 text-white" : "text-slate-600")
                }
              >
                {t === "scenario" ? "Run a built-in scenario" : "Upload my documents"}
              </button>
            ))}
          </div>

          {tab === "scenario" ? (
            <div className="space-y-3">
              <p className="text-xs text-slate-500">
                Folder mode &mdash; holdings and the governing rules come from a synthetic
                fixture, so the whole loop runs offline. No API key needed.
              </p>
              <div className="flex flex-wrap items-end gap-4">
                <label className="text-sm">
                  <span className="mb-1 block font-medium text-slate-600">Scenario</span>
                  <select
                    value={scenarioId}
                    onChange={(e) => {
                      setScenarioId(e.target.value);
                      const s = scenarios.find((x) => x.id === e.target.value);
                      if (s) setMode(s.mode);
                    }}
                    className="rounded border border-slate-300 px-2 py-1"
                  >
                    {scenarios.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.id}
                      </option>
                    ))}
                  </select>
                </label>
                <ModeToggle mode={mode} setMode={setMode} />
                <button
                  onClick={runScenario}
                  disabled={running || !scenarioId}
                  className="rounded bg-indigo-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
                >
                  {running ? "Running the loop..." : "Run the loop"}
                </button>
              </div>
              {scenarios.find((s) => s.id === scenarioId)?.description && (
                <p className="rounded border border-dashed border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-500">
                  {scenarios.find((s) => s.id === scenarioId)!.description}
                </p>
              )}
              <label className="flex items-center gap-2 text-xs text-slate-500">
                <input
                  type="checkbox"
                  checked={overrideGoal}
                  onChange={(e) => setOverrideGoal(e.target.checked)}
                />
                override the scenario&apos;s goal
              </label>
              {overrideGoal && (
                <GoalInputs
                  amount={amount}
                  setAmount={setAmount}
                  byDate={byDate}
                  setByDate={setByDate}
                />
              )}
            </div>
          ) : (
            <UploadPanel
              files={files}
              setFiles={setFiles}
              fileInputRef={fileInputRef}
              intake={intake}
              intakeLoading={intakeLoading}
              analyze={analyze}
              onComplete={completeHoldings}
            />
          )}
        </section>

        {/* Goal + candidates for the upload path */}
        {tab === "upload" && intake && (
          <section className="mb-6 rounded-lg border border-slate-200 bg-white p-4">
            <h2 className="mb-3 text-sm font-semibold text-slate-700">
              What do you want done?
            </h2>
            <div className="flex flex-wrap items-end gap-4">
              <ModeToggle mode={mode} setMode={setMode} />
              <GoalInputs
                amount={amount}
                setAmount={setAmount}
                byDate={byDate}
                setByDate={setByDate}
              />
              <button
                onClick={runUpload}
                disabled={
                  running || !amount || !byDate ||
                  (mode === "A" && selectedFunds.size === 0)
                }
                className="rounded bg-indigo-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
              >
                {running ? "Running the loop..." : "Run the loop"}
              </button>
            </div>
            {mode === "A" ? (
              <FundPicker
                funds={pickerFunds}
                selected={selectedFunds}
                onToggle={toggleFund}
                disabled={running}
              />
            ) : (
              <p className="mt-3 rounded border border-dashed border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-500">
                Mode B &mdash; the agent shortlists from sourced evidence (exit load,
                expense ratio, lock-in). No human ticks anything here.
              </p>
            )}
          </section>
        )}

        {routeSeq.length > 0 && (
          <p className="mb-4 text-xs text-slate-500">
            routes chosen this run:{" "}
            <span className="font-mono text-slate-700">{routeSeq.join(" -> ")}</span>
          </p>
        )}

        {mode === "B" && selectEvent && (
          <ModeBBreakdown output={selectEvent.output ?? {}} />
        )}

        {error && (
          <p className="mb-4 rounded bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</p>
        )}

        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
          <div className="space-y-6">
            {plans?.plans ? (
              <>
                <TwoPlanCompare data={plans} approved={approved} onApprove={approve} />
                {runId && context && (
                  <>
                    <ExplainPanel runId={runId} plans={plans} />
                    <ProveMeWrongPanel runId={runId} context={context} plans={plans} />
                    <WatcherPanel runId={runId} context={context} />
                  </>
                )}
              </>
            ) : (
              <div className="rounded-lg border border-dashed border-slate-300 bg-white p-8 text-center text-sm text-slate-400">
                {running
                  ? "The loop is working - the trace on the right updates live."
                  : "Run the loop to see the two plans."}
              </div>
            )}

            <ReversePanel body={reverseBody} disabled={running} />
            <ReconcilePanel />
          </div>

          {/* Trace */}
          <div className="rounded-lg border border-slate-200 bg-white self-start">
            <div className="border-b border-slate-200 px-4 py-2 text-sm font-semibold text-slate-600">
              Trace log &mdash; every step, retries and dead ends included
            </div>
            <ol className="max-h-[70vh] divide-y divide-slate-100 overflow-y-auto text-xs">
              {events.map((e) => (
                <li key={e.step} className="px-3 py-2">
                  <div className="flex items-center gap-2">
                    <span className="w-6 text-right font-mono text-slate-400">{e.step}</span>
                    <span
                      className={
                        "rounded px-1.5 py-0.5 font-medium " +
                        (TYPE_BADGE[e.type] ?? "bg-slate-100 text-slate-600")
                      }
                    >
                      {e.type}
                    </span>
                    {e.verdict && (
                      <span className={VERDICT_COLOR[e.verdict] ?? ""}>{e.verdict}</span>
                    )}
                    {e.budget?.fraction_remaining != null && (
                      <span className="ml-auto flex items-center gap-1 text-slate-400">
                        <span className="inline-block h-1.5 w-16 rounded bg-slate-200">
                          <span
                            className="block h-1.5 rounded bg-indigo-400"
                            style={{
                              width: `${Math.max(0, Math.min(1, e.budget.fraction_remaining)) * 100}%`,
                            }}
                          />
                        </span>
                      </span>
                    )}
                  </div>
                  <p className="mt-1 pl-8 text-slate-600">{e.label}</p>
                  {e.reason && (
                    <p className="mt-0.5 pl-8 text-slate-400 italic">{e.reason}</p>
                  )}
                </li>
              ))}
              {events.length === 0 && (
                <li className="px-3 py-6 text-center text-slate-400">no events yet</li>
              )}
            </ol>
          </div>
        </div>
      </div>
    </div>
  );
}

function ModeToggle({ mode, setMode }: { mode: Mode; setMode: (m: Mode) => void }) {
  return (
    <label className="text-sm">
      <span className="mb-1 block font-medium text-slate-600">Mode</span>
      <div className="flex overflow-hidden rounded border border-slate-300">
        {(["A", "B"] as Mode[]).map((m) => (
          <button
            key={m}
            onClick={() => setMode(m)}
            className={
              "px-3 py-1 " +
              (mode === m ? "bg-slate-800 text-white" : "bg-white text-slate-600")
            }
          >
            {m === "A" ? "A - I pick funds" : "B - agent picks"}
          </button>
        ))}
      </div>
    </label>
  );
}

function GoalInputs({
  amount,
  setAmount,
  byDate,
  setByDate,
}: {
  amount: string;
  setAmount: (s: string) => void;
  byDate: string;
  setByDate: (s: string) => void;
}) {
  return (
    <>
      <label className="text-sm">
        <span className="mb-1 block font-medium text-slate-600">Target amount</span>
        <input
          className="w-36 rounded border border-slate-300 px-2 py-1"
          placeholder="e.g. 1800000"
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
        />
      </label>
      <label className="text-sm">
        <span className="mb-1 block font-medium text-slate-600">By date</span>
        <input
          type="date"
          className="rounded border border-slate-300 px-2 py-1"
          value={byDate}
          onChange={(e) => setByDate(e.target.value)}
        />
      </label>
    </>
  );
}

function UploadPanel({
  files,
  setFiles,
  fileInputRef,
  intake,
  intakeLoading,
  analyze,
  onComplete,
}: {
  files: File[];
  setFiles: (f: File[]) => void;
  fileInputRef: React.RefObject<HTMLInputElement | null>;
  intake: IntakeResponse | null;
  intakeLoading: boolean;
  analyze: () => void;
  onComplete: (completions: HoldingCompletion[]) => Promise<void>;
}) {
  return (
    <div className="space-y-3">
      <p className="text-xs text-slate-500">
        Any mix of Scheme Information Documents, addenda, a portfolio/account
        statement (PDF), or a portfolio <strong>screenshot</strong> (PNG/JPG/WEBP -
        read directly by vision, no OCR install needed). Needs an extraction API
        key set on the server (ANTHROPIC_API_KEY or GEMINI_API_KEY).
      </p>
      <input
        ref={fileInputRef}
        type="file"
        accept="application/pdf,image/png,image/jpeg,image/webp"
        multiple
        onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
        className="hidden"
      />
      <div className="flex flex-wrap items-center gap-3">
        <button
          onClick={() => fileInputRef.current?.click()}
          className="rounded border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50"
        >
          Choose files
        </button>
        <span className="text-sm text-slate-500">
          {files.length === 0 ? "no files selected" : `${files.length} file(s) selected`}
        </span>
        <button
          onClick={analyze}
          disabled={files.length === 0 || intakeLoading}
          className="rounded bg-slate-800 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
        >
          {intakeLoading ? "Analyzing..." : "Analyze documents"}
        </button>
        {intake && (
          <span className="text-xs text-slate-500">
            {intake.funds.length} fund(s) &middot; {intake.holdings_preview.length} lot(s)
            {intake.incomplete_holdings.length > 0 &&
              ` · ${intake.incomplete_holdings.length} incomplete`}
          </span>
        )}
      </div>
      {intake && intake.warnings.length > 0 && (
        <ul className="space-y-1 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          {intake.warnings.map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      )}
      {intake && intake.incomplete_holdings.length > 0 && (
        <IncompleteHoldingsForm holdings={intake.incomplete_holdings} onComplete={onComplete} />
      )}
    </div>
  );
}

function IncompleteHoldingsForm({
  holdings,
  onComplete,
}: {
  holdings: IntakeResponse["incomplete_holdings"];
  onComplete: (completions: HoldingCompletion[]) => Promise<void>;
}) {
  type Draft = { folio: string; purchase_date: string; purchase_nav: string };
  const [drafts, setDrafts] = useState<Draft[]>(() =>
    holdings.map((h) => ({ folio: h.folio ?? "", purchase_date: "", purchase_nav: "" })),
  );
  const [busy, setBusy] = useState(false);

  const update = (i: number, field: keyof Draft, value: string) =>
    setDrafts((prev) => prev.map((d, idx) => (idx === i ? { ...d, [field]: value } : d)));

  const ready = drafts.every((d) => d.folio && d.purchase_date && d.purchase_nav);

  const submit = async () => {
    setBusy(true);
    try {
      await onComplete(
        holdings.map((h, i) => ({
          scheme_id: h.scheme_id,
          units: h.units,
          folio: drafts[i].folio,
          purchase_date: drafts[i].purchase_date,
          purchase_nav: Number(drafts[i].purchase_nav),
        })),
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded border border-amber-300 bg-amber-50 p-3">
      <p className="mb-2 text-xs font-medium text-amber-800">
        {holdings.length} holding(s) named a fund but not enough to FIFO correctly - most
        portfolio screens show current units/value, not the original purchase. Fill these
        in (or re-upload the actual CAS/statement instead) before running.
      </p>
      <div className="space-y-2">
        {holdings.map((h, i) => (
          <div key={i} className="grid grid-cols-2 gap-2 rounded bg-white p-2 text-xs sm:grid-cols-5 sm:items-end">
            <div className="col-span-2 sm:col-span-1">
              <div className="font-medium text-slate-700">{h.label}</div>
              <div className="text-slate-400">{h.units} units{h.current_value != null && ` · Rs.${Math.round(h.current_value).toLocaleString("en-IN")}`}</div>
            </div>
            {h.missing.includes("folio") && (
              <label className="block">
                <span className="mb-0.5 block text-slate-500">folio</span>
                <input
                  value={drafts[i].folio}
                  onChange={(e) => update(i, "folio", e.target.value)}
                  className="w-full rounded border border-slate-300 px-1.5 py-1"
                  placeholder="e.g. 12345 / 67"
                />
              </label>
            )}
            {h.missing.includes("purchase_date") && (
              <label className="block">
                <span className="mb-0.5 block text-slate-500">purchase date</span>
                <input
                  type="date"
                  value={drafts[i].purchase_date}
                  onChange={(e) => update(i, "purchase_date", e.target.value)}
                  className="w-full rounded border border-slate-300 px-1.5 py-1"
                />
              </label>
            )}
            {h.missing.includes("purchase_nav") && (
              <label className="block">
                <span className="mb-0.5 block text-slate-500">purchase NAV</span>
                <input
                  value={drafts[i].purchase_nav}
                  onChange={(e) => update(i, "purchase_nav", e.target.value)}
                  className="w-full rounded border border-slate-300 px-1.5 py-1"
                  placeholder="e.g. 45.00"
                />
              </label>
            )}
          </div>
        ))}
      </div>
      <button
        onClick={submit}
        disabled={!ready || busy}
        className="mt-3 rounded bg-amber-700 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-40"
      >
        {busy ? "Adding..." : `Add these ${holdings.length} holding(s)`}
      </button>
    </div>
  );
}

function TwoPlanCompare({
  data,
  approved,
  onApprove,
}: {
  data: PlansResponse;
  approved: PlanChoice | null;
  onApprove: (c: PlanChoice) => void;
}) {
  const tp = data.plans!;
  const frontier = tp.frontier ?? [];
  const best = frontier.length
    ? frontier.reduce((a, b) => (b.total_cost < a.total_cost ? b : a))
    : null;

  return (
    <div className="space-y-4">
      {data.stopped_reason && (
        <p className="rounded bg-amber-50 px-3 py-2 text-sm text-amber-800">
          Loop stopped early: {data.stopped_reason}
        </p>
      )}
      {tp.identical && (
        <p className="rounded bg-slate-100 px-3 py-2 text-sm text-slate-600">
          The two plans are identical for this input - holding the higher-merit fund
          back cannot reach the target.
        </p>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        <PlanCard
          plan={tp.cheapest}
          title="Cheapest exit"
          blurb="Lowest exit-load path. May sell a fund worth keeping."
          approved={approved === "cheapest"}
          onApprove={() => onApprove("cheapest")}
        />
        <PlanCard
          plan={tp.merit_preserving}
          title="Merit-preserving"
          blurb="Holds the funds worth keeping back where the goal still allows it."
          approved={approved === "merit_preserving"}
          onApprove={() => onApprove("merit_preserving")}
        />
      </div>

      {frontier.length > 1 && (
        <div className="rounded-lg border border-slate-200 bg-white p-4">
          <h3 className="mb-2 text-sm font-semibold text-slate-600">
            Cost frontier &mdash; exit load by sell date, over the decision window
          </h3>
          <div className="h-48">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={frontier}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="date" tick={{ fontSize: 10 }} />
                <YAxis tick={{ fontSize: 10 }} width={70} tickFormatter={(v) => money(Number(v))} />
                <Tooltip formatter={(v) => money(Number(v))} />
                <Line type="stepAfter" dataKey="total_cost" stroke="#4f46e5" dot={false} />
                {best && (
                  <ReferenceDot
                    x={best.date}
                    y={best.total_cost}
                    r={4}
                    fill="#059669"
                    stroke="none"
                  />
                )}
              </LineChart>
            </ResponsiveContainer>
          </div>
          {best && (
            <p className="mt-1 text-xs text-slate-500">
              cheapest sell date in the window: <span className="font-mono">{best.date}</span> at{" "}
              {money(best.total_cost)}
            </p>
          )}
        </div>
      )}

      {data.abstentions.length > 0 && (
        <div className="rounded-lg border border-slate-200 bg-white p-4">
          <h3 className="mb-2 text-sm font-semibold text-slate-600">Abstentions</h3>
          <ul className="space-y-1 text-sm">
            {data.abstentions.map((a) => (
              <li key={a.scheme_id}>
                <span className="font-mono text-slate-700">{a.scheme_id}</span>{" "}
                <span className="text-slate-500">&mdash; {a.reason}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function PlanCard({
  plan,
  title,
  blurb,
  approved,
  onApprove,
}: {
  plan: Plan;
  title: string;
  blurb: string;
  approved: boolean;
  onApprove: () => void;
}) {
  const verified = plan.verdict === "verified";
  return (
    <div
      className={
        "rounded-lg border bg-white p-4 " +
        (approved ? "border-emerald-400 ring-2 ring-emerald-200" : "border-slate-200")
      }
    >
      <div className="flex items-center justify-between">
        <h3 className="font-semibold">{title}</h3>
        <span
          className={
            "rounded px-2 py-0.5 text-xs font-semibold " +
            (verified ? "bg-emerald-100 text-emerald-700" : "bg-rose-100 text-rose-700")
          }
        >
          {plan.verdict}
        </span>
      </div>
      <p className="mt-1 text-xs text-slate-500">{blurb}</p>

      <table className="mt-3 w-full text-xs">
        <thead className="text-slate-400">
          <tr>
            <th className="text-left font-medium">fund / folio</th>
            <th className="text-right font-medium">units</th>
            <th className="text-right font-medium">sell</th>
            <th className="text-right font-medium">exit load</th>
          </tr>
        </thead>
        <tbody>
          {plan.legs.map((l, i) => (
            <tr key={i} className="border-t border-slate-100">
              <td className="py-1 font-mono">
                {l.scheme_id}
                <span className="text-slate-400"> /{l.folio}</span>
              </td>
              <td className="py-1 text-right">{l.units.toFixed(1)}</td>
              <td className="py-1 text-right">{l.sell_date}</td>
              <td className="py-1 text-right">{money(l.exit_load)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
        <dt className="text-slate-400">gross</dt>
        <dd className="text-right">{money(plan.totals.gross)}</dd>
        <dt className="text-slate-400">exit load</dt>
        <dd className="text-right">{money(plan.totals.exit_load)}</dd>
        <dt className="text-slate-400">tax</dt>
        <dd className="text-right">{money(plan.totals.tax)}</dd>
        <dt className="font-medium text-slate-600">net</dt>
        <dd className="text-right font-medium">{money(plan.totals.net)}</dd>
      </dl>

      {plan.legs.some((l) => l.constraints_applied.length > 0) && (
        <p className="mt-2 text-[11px] text-slate-400">
          citations:{" "}
          {[...new Set(plan.legs.flatMap((l) => l.constraints_applied))].join(", ")}
        </p>
      )}

      {plan.violations.length > 0 && (
        <ul className="mt-2 space-y-1 text-[11px] text-rose-600">
          {plan.violations.map((v, i) => (
            <li key={i}>
              <span className="font-semibold">{v.code}</span> &mdash; {v.detail}
            </li>
          ))}
        </ul>
      )}

      <button
        disabled={!verified || approved}
        onClick={onApprove}
        className="mt-3 w-full rounded bg-emerald-600 px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-40"
      >
        {approved ? "Approved - intent confirmed" : "Approve this plan"}
      </button>
    </div>
  );
}

function FundPicker({
  funds,
  selected,
  onToggle,
  disabled,
}: {
  funds: PickerFund[];
  selected: Set<string>;
  onToggle: (schemeId: string) => void;
  disabled: boolean;
}) {
  if (funds.length === 0) return null;
  return (
    <div className="mt-3 rounded border border-slate-200 bg-slate-50 p-3">
      <p className="mb-2 text-xs font-medium text-slate-500">
        Mode A &mdash; tick which of this client&apos;s holdings are candidates
      </p>
      <ul className="space-y-1.5">
        {funds.map((f) => (
          <li key={f.scheme_id} className="flex items-center justify-between text-sm">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={selected.has(f.scheme_id)}
                disabled={disabled}
                onChange={() => onToggle(f.scheme_id)}
              />
              <span>{f.label}</span>
              <span className="font-mono text-[11px] text-slate-400">{f.scheme_id}</span>
            </label>
            <span className="text-xs text-slate-500">
              {f.units.toFixed(0)} units
              {f.current_value != null && ` · ${money(f.current_value)}`}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ModeBBreakdown({ output }: { output: Record<string, unknown> }) {
  const factors = (output.factors ?? {}) as Record<
    string,
    { name: string; value: number; weight: number }[]
  >;
  const omitted = (output.omitted_factors ?? {}) as Record<string, string[]>;
  const merit = (output.merit ?? {}) as Record<string, number>;
  const fundIds = Object.keys(merit).sort((a, b) => merit[b] - merit[a]);
  if (fundIds.length === 0) return null;

  return (
    <div className="mb-6 rounded-lg border border-indigo-200 bg-indigo-50 p-4">
      <h3 className="mb-2 text-sm font-semibold text-indigo-800">
        Mode B scoring &mdash; sourced evidence, weights shown
      </h3>
      <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
        {fundIds.map((id) => (
          <div key={id} className="rounded border border-indigo-100 bg-white p-3 text-xs">
            <div className="flex items-center justify-between">
              <span className="font-mono font-medium text-slate-700">{id}</span>
              <span className="font-semibold text-indigo-700">
                {merit[id].toFixed(4)}
              </span>
            </div>
            <ul className="mt-2 space-y-0.5 text-slate-600">
              {(factors[id] ?? []).map((f) => (
                <li key={f.name} className="flex justify-between">
                  <span>{f.name}</span>
                  <span className="font-mono">
                    {f.value.toFixed(2)} &times; {f.weight.toFixed(2)}
                  </span>
                </li>
              ))}
            </ul>
            {(omitted[id] ?? []).length > 0 && (
              <p className="mt-1.5 text-amber-600">
                omitted (no source): {(omitted[id] ?? []).join(", ")}
              </p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// --- explain-to-client -----------------------------------------------------

function ExplainPanel({ runId, plans }: { runId: string; plans: PlansResponse }) {
  const [choice, setChoice] = useState<PlanChoice>("cheapest");
  const [text, setText] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const generate = useCallback(async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await explainPlan(runId, choice);
      setText(r.text);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, [runId, choice]);

  return (
    <details className="rounded-lg border border-slate-200 bg-white p-4" open>
      <summary className="cursor-pointer text-sm font-semibold text-slate-700">
        Explain to client &mdash; the verified plan in plain language
      </summary>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <select
          value={choice}
          onChange={(e) => setChoice(e.target.value as PlanChoice)}
          className="rounded border border-slate-300 px-2 py-1 text-sm"
        >
          <option value="cheapest">cheapest</option>
          <option value="merit_preserving">merit_preserving</option>
        </select>
        <button
          onClick={generate}
          disabled={busy || !plans.plans}
          className="rounded bg-slate-800 px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-50"
        >
          {busy ? "Generating..." : "Generate"}
        </button>
        {text && (
          <button
            onClick={() => navigator.clipboard?.writeText(text)}
            className="rounded border border-slate-300 px-3 py-1.5 text-sm text-slate-600"
          >
            Copy
          </button>
        )}
      </div>
      {err && <p className="mt-2 text-xs text-rose-600">{err}</p>}
      {text && (
        <pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap rounded bg-slate-50 p-3 text-xs text-slate-700">
          {text}
        </pre>
      )}
    </details>
  );
}

// --- prove me wrong ------------------------------------------------------

function ProveMeWrongPanel({
  runId,
  context,
  plans,
}: {
  runId: string;
  context: RunContext;
  plans: PlansResponse;
}) {
  void runId;
  const applied = useMemo(() => {
    const ids = new Set(
      (plans.plans ? [plans.plans.cheapest, plans.plans.merit_preserving] : []).flatMap((p) =>
        p.legs.flatMap((l) => l.constraints_applied),
      ),
    );
    return context.constraints.filter((c) => ids.has(c.id));
  }, [context, plans]);

  const [choice, setChoice] = useState<PlanChoice>("cheapest");
  const [cid, setCid] = useState<string>(applied[0]?.id ?? "");
  const [field, setField] = useState<string>("pct");
  const [value, setValue] = useState<string>("");
  const [result, setResult] = useState<Violation[] | null>(null);
  const [baseline, setBaseline] = useState<Violation[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const chosenConstraint = applied.find((c) => c.id === cid);
  const fields = chosenConstraint ? Object.keys(chosenConstraint.value) : [];

  const run = useCallback(
    async (tamper: boolean) => {
      if (!plans.plans) return;
      setErr(null);
      const plan = plans.plans[choice];
      const constraints: Constraint[] = context.constraints.map((c) => {
        if (tamper && c.id === cid) {
          const nv = { ...(c.value as Record<string, unknown>) };
          nv[field] = Number(value);
          return { ...c, value: nv };
        }
        return c;
      });
      try {
        const v = await verifyPlan({
          plan: plan as Plan,
          constraints,
          holdings: context.holdings,
        });
        if (tamper) setResult(v);
        else setBaseline(v);
      } catch (e) {
        setErr((e as Error).message);
      }
    },
    [plans, choice, context, cid, field, value],
  );

  return (
    <details className="rounded-lg border border-slate-200 bg-white p-4">
      <summary className="cursor-pointer text-sm font-semibold text-slate-700">
        Prove me wrong &mdash; tamper with a rule, watch the zero-AI verifier catch it
      </summary>
      <p className="mt-2 text-xs text-slate-500">
        The verifier recomputes FIFO, exit load and tax from scratch. Change a value the
        plan relied on and it should reject the plan for a reason that names the change.
      </p>
      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        <label className="text-xs">
          <span className="mb-1 block text-slate-500">plan</span>
          <select
            value={choice}
            onChange={(e) => setChoice(e.target.value as PlanChoice)}
            className="w-full rounded border border-slate-300 px-2 py-1"
          >
            <option value="cheapest">cheapest</option>
            <option value="merit_preserving">merit_preserving</option>
          </select>
        </label>
        <label className="text-xs">
          <span className="mb-1 block text-slate-500">constraint</span>
          <select
            value={cid}
            onChange={(e) => {
              setCid(e.target.value);
              const c = applied.find((x) => x.id === e.target.value);
              setField(c ? Object.keys(c.value)[0] : "");
            }}
            className="w-full rounded border border-slate-300 px-2 py-1 font-mono"
          >
            {applied.map((c) => (
              <option key={c.id} value={c.id}>
                {c.id} ({c.kind})
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs">
          <span className="mb-1 block text-slate-500">field</span>
          <select
            value={field}
            onChange={(e) => setField(e.target.value)}
            className="w-full rounded border border-slate-300 px-2 py-1 font-mono"
          >
            {fields.map((f) => (
              <option key={f} value={f}>
                {f} (now {String((chosenConstraint!.value as Record<string, unknown>)[f])})
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs">
          <span className="mb-1 block text-slate-500">tampered value</span>
          <input
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="e.g. 0"
            className="w-full rounded border border-slate-300 px-2 py-1"
          />
        </label>
      </div>
      <div className="mt-3 flex gap-2">
        <button
          onClick={() => run(false)}
          className="rounded border border-slate-300 px-3 py-1.5 text-xs text-slate-600"
        >
          Verify as-is (baseline)
        </button>
        <button
          onClick={() => run(true)}
          disabled={!cid || value === ""}
          className="rounded bg-rose-600 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50"
        >
          Verify with my change
        </button>
      </div>
      {err && <p className="mt-2 text-xs text-rose-600">{err}</p>}
      {baseline && (
        <p className="mt-2 text-xs text-slate-500">
          baseline: {baseline.length === 0 ? "no violations (plan is clean)" : `${baseline.length} violation(s)`}
        </p>
      )}
      {result && (
        <div className="mt-2 rounded bg-slate-50 p-2 text-xs">
          {result.length === 0 ? (
            <p className="text-amber-600">
              verifier still passed - that change did not affect this plan&apos;s arithmetic
            </p>
          ) : (
            <ul className="space-y-1 text-rose-600">
              {result.map((v, i) => (
                <li key={i}>
                  <span className="font-semibold">{v.code}</span> &mdash; {v.detail}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </details>
  );
}

// --- the Watcher -------------------------------------------------------

function WatcherPanel({ runId, context }: { runId: string; context: RunContext }) {
  const [watching, setWatching] = useState(false);
  const [navs, setNavs] = useState<Record<string, string>>(() =>
    Object.fromEntries(Object.entries(context.navs).map(([k, v]) => [k, String(v)])),
  );
  const [report, setReport] = useState<WatchCheckResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const start = useCallback(async () => {
    setErr(null);
    try {
      await addWatch(runId);
      setWatching(true);
    } catch (e) {
      setErr((e as Error).message);
    }
  }, [runId]);

  const check = useCallback(async () => {
    setBusy(true);
    setErr(null);
    try {
      const parsed = Object.fromEntries(
        Object.entries(navs).map(([k, v]) => [k, Number(v)]),
      );
      setReport(await checkWatch(runId, parsed));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, [runId, navs]);

  return (
    <details className="rounded-lg border border-emerald-200 bg-emerald-50 p-4">
      <summary className="cursor-pointer text-sm font-semibold text-emerald-800">
        The Watcher &mdash; standing goal &ldquo;keep this plan valid&rdquo;
      </summary>
      <p className="mt-2 text-xs text-emerald-700">
        Triggers on a NAV move past its threshold, the calendar reaching the target date,
        or a cited document changing. Nothing prompts it.
      </p>
      {!watching ? (
        <button
          onClick={start}
          className="mt-3 rounded bg-emerald-600 px-3 py-1.5 text-sm font-semibold text-white"
        >
          Watch this plan
        </button>
      ) : (
        <div className="mt-3 space-y-2">
          <p className="text-xs font-medium text-emerald-800">
            Revise a NAV and re-check (simulates the world moving):
          </p>
          <div className="grid gap-1.5 sm:grid-cols-2">
            {Object.keys(navs).map((k) => (
              <label key={k} className="flex items-center gap-2 text-xs">
                <span className="w-40 truncate font-mono text-slate-600">{k}</span>
                <input
                  value={navs[k]}
                  onChange={(e) => setNavs((p) => ({ ...p, [k]: e.target.value }))}
                  className="w-24 rounded border border-slate-300 px-1.5 py-0.5"
                />
              </label>
            ))}
          </div>
          <button
            onClick={check}
            disabled={busy}
            className="rounded bg-emerald-700 px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-50"
          >
            {busy ? "Re-checking..." : "Run watch check"}
          </button>
        </div>
      )}
      {err && <p className="mt-2 text-xs text-rose-600">{err}</p>}
      {report && (
        <div className="mt-3 rounded bg-white p-2 text-xs">
          {!report.triggered ? (
            <p className="text-emerald-700">
              Nothing moved past a threshold &mdash; plan still valid, no action. (Silence is
              the correct outcome most days.)
            </p>
          ) : (
            <>
              <p className="font-semibold text-amber-700">
                Triggered: {report.reasons.join("; ")}
              </p>
              <p className="mt-1">
                {report.plans_changed
                  ? "Re-planning changed the legs - needs re-approval."
                  : "Re-planned and the legs are unchanged - still valid."}
              </p>
            </>
          )}
        </div>
      )}
    </details>
  );
}

// --- reverse mode -----------------------------------------------------

function ReversePanel({
  body,
  disabled,
}: {
  body: { scenario?: string; intake_id?: string };
  disabled: boolean;
}) {
  const [plan, setPlan] = useState<ReversePlan | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const go = useCallback(async () => {
    setBusy(true);
    setErr(null);
    try {
      setPlan(await reversePlan(body));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, [body]);

  return (
    <details className="rounded-lg border border-slate-200 bg-white p-4">
      <summary className="cursor-pointer text-sm font-semibold text-slate-700">
        Reverse mode &mdash; what is free to take out today?
      </summary>
      <p className="mt-2 text-xs text-slate-500">
        The mirror question: zero exit load, no lock-in breach, least tax. Same
        constraint model &mdash; stacked addenda resolved, a genuine tie abstained.
      </p>
      <button
        onClick={go}
        disabled={busy || disabled || (!body.scenario && !body.intake_id)}
        className="mt-3 rounded bg-slate-800 px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-50"
      >
        {busy ? "Computing..." : "What can I redeem now?"}
      </button>
      {err && <p className="mt-2 text-xs text-rose-600">{err}</p>}
      {plan && (
        <div className="mt-3 text-xs">
          <p className="mb-2 text-slate-600">
            As of {plan.as_of}: <span className="font-semibold">{money(plan.total_free_value)}</span>{" "}
            free to redeem at zero exit load, tax {money(plan.total_tax)} &rarr; net{" "}
            <span className="font-semibold">{money(plan.net_if_all_taken)}</span>
          </p>
          <table className="w-full">
            <thead className="text-slate-400">
              <tr>
                <th className="text-left font-medium">scheme / folio</th>
                <th className="text-right font-medium">free units</th>
                <th className="text-right font-medium">free value</th>
                <th className="text-right font-medium">tax if sold</th>
                <th className="text-right font-medium">still locked / in-load</th>
              </tr>
            </thead>
            <tbody>
              {plan.slices.map((s, i) => (
                <tr key={i} className="border-t border-slate-100">
                  <td className="py-1 font-mono">
                    {s.scheme_id}
                    <span className="text-slate-400"> /{s.folio}</span>
                  </td>
                  <td className="py-1 text-right">{s.free_units.toFixed(1)}</td>
                  <td className="py-1 text-right">{money(s.free_value)}</td>
                  <td className="py-1 text-right">{money(s.tax_if_sold)}</td>
                  <td className="py-1 text-right text-slate-400">
                    {s.locked_units.toFixed(0)} / {s.load_bearing_units.toFixed(0)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {plan.abstained.length > 0 && (
            <ul className="mt-2 space-y-0.5 text-amber-600">
              {plan.abstained.map((a) => (
                <li key={a.scheme_id}>
                  <span className="font-mono">{a.scheme_id}</span> &mdash; {a.reason}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </details>
  );
}

// --- multi-RTA reconciliation ---------------------------------------------

const RECONCILE_SAMPLE = `CAMS | Quant ELSS Tax Saver Fund - Direct Growth | 17880021 / 55 | 150 | 2022-01-10 | 250.30
KFintech | QUANT ELSS TAX SAVER FUND DIRECT GROWTH | 17880021 / 55 | 150 | 2022-01-10 | 250.30
CAMS | Parag Parikh Flexi Cap Fund - Growth | 16003399 / 08 | 4000 | 2020-08-10 | 38.90
KFintech | Parag Parikh Flexi Cap Fund - Growth | 16003399 / 08 | 4000.4 | 2020-08-10 | 38.90
CAMS | Totally Made Up Global Fund | 99 / 9 | 100 | 2022-01-01 | 10.0`;

function ReconcilePanel() {
  const [raw, setRaw] = useState(RECONCILE_SAMPLE);
  const [res, setRes] = useState<ReconcileResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const go = useCallback(async () => {
    setBusy(true);
    setErr(null);
    try {
      const rows = raw
        .split("\n")
        .map((l) => l.trim())
        .filter(Boolean)
        .map((l) => {
          const [source, scheme_name, folio, units, purchase_date, purchase_nav] = l
            .split("|")
            .map((x) => x.trim());
          return {
            source,
            scheme_name,
            folio,
            units: Number(units),
            purchase_date,
            purchase_nav: Number(purchase_nav),
          };
        });
      setRes(await reconcileRows(rows));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, [raw]);

  return (
    <details className="rounded-lg border border-slate-200 bg-white p-4">
      <summary className="cursor-pointer text-sm font-semibold text-slate-700">
        Multi-RTA reconciliation &mdash; resolve identity, dedupe, keep folios separate
      </summary>
      <p className="mt-2 text-xs text-slate-500">
        One row per line: <code>source | scheme name | folio | units | date | nav</code>.
        Feeds spell schemes differently and report some lots twice; folios are never
        merged, and anything ambiguous is flagged, not guessed.
      </p>
      <textarea
        value={raw}
        onChange={(e) => setRaw(e.target.value)}
        rows={6}
        className="mt-2 w-full rounded border border-slate-300 p-2 font-mono text-[11px]"
      />
      <button
        onClick={go}
        disabled={busy}
        className="mt-2 rounded bg-slate-800 px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-50"
      >
        {busy ? "Reconciling..." : "Reconcile"}
      </button>
      {err && <p className="mt-2 text-xs text-rose-600">{err}</p>}
      {res && (
        <div className="mt-3 text-xs">
          <p className="text-slate-600">
            {res.kept_rows} lot(s) kept, {res.dropped_rows} dropped
            {res.needs_human && (
              <span className="ml-1 font-semibold text-amber-700">&mdash; needs human review</span>
            )}
          </p>
          <ul className="mt-2 space-y-1">
            {res.flags.map((f, i) => (
              <li
                key={i}
                className={
                  "rounded px-2 py-1 " +
                  (f.needs_human ? "bg-amber-50 text-amber-800" : "bg-slate-50 text-slate-600")
                }
              >
                <span className="font-mono font-semibold">{f.kind}</span> &mdash; {f.detail}
              </li>
            ))}
            {res.flags.length === 0 && <li className="text-slate-400">no flags - clean</li>}
          </ul>
        </div>
      )}
    </details>
  );
}
