/**
 * Shared contract shapes. Mirrors agent/contracts.py - the diagram
 * ("NIKAAS - the agent loop") is the spec.
 */

export type TraceType =
  | "select"
  | "observe"
  | "decide"
  | "hunt"
  | "cache"
  | "extract"
  | "resolve"
  | "allocate"
  | "verify"
  | "repair"
  | "self_check"
  | "abstain"
  | "stop"
  | "gate"
  | "watch";

export type Verdict = "ok" | "violation" | "abstained" | "retry";

export type ConstraintKind =
  | "exit_load"
  | "lock_in"
  | "tax_rule"
  | "cutoff"
  | "minimum"
  | "expense_ratio";

export type Confidence = "verified" | "inferred" | "unverified";

export type ViolationCode =
  | "LOCK_IN_BREACH"
  | "NO_PROVENANCE"
  | "SHORTFALL"
  | "FIFO_MERGE"
  | "STALE_SOURCE"
  | "SUPERSEDED"
  | "SILENT_DROP";

export type Mode = "A" | "B";
export type PlanVerdict = "verified" | "rejected";
export type PlanChoice = "cheapest" | "merit_preserving";

export interface Source {
  doc: string;
  page: number;
  clause: string | null;
  url: string | null;
  effective_date: string;
  retrieved_at: string;
}

export interface TraceEvent {
  step: number;
  type: TraceType;
  label: string;
  tool?: string | null;
  input?: Record<string, unknown> | null;
  output?: Record<string, unknown> | null;
  verdict?: Verdict | null;
  reason?: string | null;
  budget?: Record<string, number> | null;
  source?: Source | null;
  duration_ms?: number | null;
}

export interface Constraint {
  id: string;
  scheme_id: string;
  kind: ConstraintKind;
  value: Record<string, unknown>;
  source: Source;
  confidence: Confidence;
  superseded_by: string | null;
}

export interface Goal {
  amount: number;
  by_date: string;
  mode: Mode;
}

export interface PlanLeg {
  scheme_id: string;
  folio: string;
  units: number;
  gross: number;
  exit_load: number;
  tax: number;
  net: number;
  sell_date: string;
  constraints_applied: string[];
}

export interface Totals {
  gross: number;
  exit_load: number;
  tax: number;
  net: number;
}

export interface Abstention {
  scheme_id: string;
  reason: string;
}

export interface FrontierPoint {
  date: string;
  total_cost: number;
}

export interface Violation {
  code: ViolationCode;
  leg_index: number | null;
  detail: string;
  repair_hint: string;
}

export interface Plan {
  plan_id: string;
  goal: Goal;
  legs: PlanLeg[];
  totals: Totals;
  abstained: Abstention[];
  verdict: PlanVerdict;
  violations: Violation[];
  frontier: FrontierPoint[];
  kind: PlanChoice;
  approved: boolean;
}

export interface TwoPlans {
  cheapest: Plan;
  merit_preserving: Plan;
  frontier: FrontierPoint[];
  identical: boolean;
}

export interface PlansResponse {
  plans: TwoPlans | null;
  abstentions: Abstention[];
  stopped_reason: string;
  trigger?: string;
  approved_choice?: PlanChoice | null;
}

export interface ScenarioSummary {
  id: string;
  description: string;
  mode: Mode;
}

export interface RunContext {
  goal: Goal;
  mode: Mode;
  candidate_schemes: string[];
  holdings: Record<string, unknown>[];
  constraints: Constraint[];
  navs: Record<string, number>;
  names: Record<string, string>;
}

export interface ReverseSlice {
  scheme_id: string;
  folio: string;
  free_units: number;
  free_value: number;
  ltcg_gain: number;
  stcg_gain: number;
  tax_if_sold: number;
  locked_units: number;
  load_bearing_units: number;
}

export interface ReversePlan {
  as_of: string;
  total_free_value: number;
  total_tax: number;
  net_if_all_taken: number;
  slices: ReverseSlice[];
  abstained: Abstention[];
  names: Record<string, string>;
}

export interface ReconcileFlag {
  kind: string;
  detail: string;
  rows: number[];
  needs_human: boolean;
}

export interface ReconcileResponse {
  kept_rows: number;
  dropped_rows: number;
  needs_human: boolean;
  resolved: Record<string, string>;
  holdings: Record<string, unknown>[];
  flags: ReconcileFlag[];
}

export interface WatchCheckResponse {
  triggered: boolean;
  reasons: string[];
  plans_changed: boolean;
  plans: PlansResponse | null;
}
