"""Shared contract shapes for Nikaas.

These are the shapes every module agrees on. The diagram
("NIKAAS - the agent loop") is the spec: a goal-driven loop of
observe -> decide (which gap, which route) -> act -> incorporate,
producing two verified plans for a human to choose between.

The §0 wire shapes (`Source`, `Constraint`, `Plan`, `PlanLeg`,
`Violation`, `Abstention`, `FrontierPoint`) are frozen and mirrored in
`web/src/lib/contracts.ts`. The loop shapes (`Gap`, `Route`, `Trigger`,
`Doubt`, `Evidence`, `Candidate`, `TwoPlans`, `LoopResult`) are new and
model the diagram directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Optional

# --- Enumerations -----------------------------------------------------------

TraceType = Literal[
    "select",      # step 0 - candidate selection (Mode A / Mode B)
    "observe",     # step 1 - gap list rebuilt
    "decide",      # step 2 - which gap, which route
    "hunt",        # route  - HUNT the open web
    "cache",       # route  - use cache / re-fetch
    "extract",     # incorporate - page text -> constraints
    "resolve",     # step 3 - supersession
    "allocate",    # step 5 - two plans
    "verify",      # step 6 - zero-AI checker
    "repair",      # violations -> sub-goals
    "self_check",  # step 7 - adversarial
    "abstain",     # a gap we chose not to guess at
    "stop",        # budget low - finish safe
    "gate",        # handed to the human
    "watch",       # the Watcher
]

Verdict = Literal["ok", "violation", "abstained", "retry"]

ConstraintKind = Literal["exit_load", "lock_in", "tax_rule", "cutoff", "minimum", "expense_ratio"]

Confidence = Literal["verified", "inferred", "unverified"]

ViolationCode = Literal[
    "LOCK_IN_BREACH", "NO_PROVENANCE", "SHORTFALL", "FIFO_MERGE",
    "STALE_SOURCE", "SUPERSEDED", "SILENT_DROP",
]

Mode = Literal["A", "B"]

PlanVerdict = Literal["verified", "rejected"]

PlanChoice = Literal["cheapest", "merit_preserving"]

GapKind = Literal[
    "missing_evidence",     # a candidate fund has no governing constraint of a required kind
    "stale_evidence",       # an evidence entry is past its freshness window
    "unresolved_conflict",  # >=2 active constraints for one (fund, kind)
    "no_allocation",        # evidence is complete but no plans exist yet
    "plan_rejected",        # the verifier found a violation
    "not_self_checked",     # plans verify but the adversarial pass hasn't run
    "self_check_doubt",     # the adversarial pass raised something to test
]


class Route(str, Enum):
    """The routes out of DECIDE, roughly ordered by cost."""

    USE_CACHE = "USE_CACHE"    # fresh in the evidence store, cost ~ 0
    REFETCH = "REFETCH"        # cache stale, re-pull the known URL, medium
    HUNT = "HUNT"             # name -> AMC -> SID -> addenda -> clause, expensive
    RESOLVE = "RESOLVE"       # supersession across stacked addenda
    ALLOCATE = "ALLOCATE"     # build / rebuild the two plans
    VERIFY = "VERIFY"         # run the zero-AI checker
    SELF_CHECK = "SELF_CHECK"  # adversarial pass
    ABSTAIN = "ABSTAIN"       # two tries failed - do not guess
    STOP = "STOP"            # budget low - finish with what's verified


class Trigger(str, Enum):
    """One agent, several ways in (README: 'one agent, four entry points')."""

    GOAL = "goal"
    SCHEDULER = "scheduler"
    REVERSE_QUERY = "reverse_query"
    SELF_CHECK = "self_check"
    WATCHER = "watcher"


# --- §0 wire shapes (frozen) ---------------------------------------------------


@dataclass
class Source:
    doc: str
    page: int
    clause: Optional[str]
    url: Optional[str]
    effective_date: str
    retrieved_at: str


@dataclass
class Budget:
    """Serialisable snapshot of a run's budget, carried on trace events.
    The live object with the spend logic lives in `agent/budget.py`."""

    steps_used: int
    steps_max: int
    tokens_used: int


@dataclass
class TraceEvent:
    """One event per loop step. No silent steps - retries, dead ends and
    abandoned branches all emit."""

    step: int
    type: TraceType
    label: str
    tool: Optional[str] = None
    input: Optional[dict] = None
    output: Optional[dict] = None
    verdict: Optional[Verdict] = None
    reason: Optional[str] = None
    budget: Optional[dict] = None
    source: Optional[Source] = None
    duration_ms: Optional[int] = None


@dataclass
class Constraint:
    """No source, no constraint. An object without a resolvable `source`
    must never reach the allocator."""

    id: str
    scheme_id: str
    kind: ConstraintKind
    value: dict
    source: Source
    confidence: Confidence
    superseded_by: Optional[str] = None


@dataclass
class Goal:
    amount: float
    by_date: str
    mode: Mode


@dataclass
class PlanLeg:
    scheme_id: str
    folio: str
    units: float
    gross: float
    exit_load: float
    tax: float
    net: float
    sell_date: str
    constraints_applied: list[str] = field(default_factory=list)


@dataclass
class Totals:
    gross: float
    exit_load: float
    tax: float
    net: float


@dataclass
class Abstention:
    scheme_id: str
    reason: str


@dataclass
class FrontierPoint:
    date: str
    total_cost: float


@dataclass
class Violation:
    code: ViolationCode
    leg_index: Optional[int]
    detail: str
    repair_hint: str


@dataclass
class Plan:
    plan_id: str
    goal: Goal
    legs: list[PlanLeg]
    totals: Totals
    abstained: list[Abstention]
    verdict: PlanVerdict
    violations: list[Violation]
    frontier: list[FrontierPoint] = field(default_factory=list)
    kind: PlanChoice = "cheapest"
    approved: bool = False


# --- Loop shapes (new - model the diagram) ----------------------------------


@dataclass
class Gap:
    """Something the loop knows is missing or wrong. OBSERVE rebuilds the
    whole list every pass; DECIDE picks one and a route to close it."""

    kind: GapKind
    fund_id: Optional[str] = None
    constraint_kind: Optional[str] = None
    reason: str = ""
    attempts: int = 0
    violation: Optional[Violation] = None
    plan_key: Optional[PlanChoice] = None

    def key(self) -> tuple:
        return (self.kind, self.fund_id, self.constraint_kind, self.plan_key)


@dataclass
class Doubt:
    """A question the self-check pass wants answered before the human sees
    the plan. Each becomes a Gap."""

    label: str
    fund_id: Optional[str] = None
    constraint_kind: Optional[str] = None
    detail: str = ""


@dataclass
class Evidence:
    """One sourced, dated fact in the evidence store: a Constraint plus the
    hash of the document it was read from (so the Watcher can notice the
    document changing under us)."""

    constraint: Constraint
    content_hash: str
    retrieved_at: str


@dataclass
class Candidate:
    """A fund in scope for this run, with its merit score. In Mode A the
    score comes from `agent/merit.py`; in Mode B from `agent/selector.py`."""

    fund_id: str
    fund_name: str = ""
    merit_score: float = 0.0
    merit_basis: str = ""          # "mode_b_weighted" | "ltcg_runway" | "passthrough"
    omitted_factors: list[str] = field(default_factory=list)
    factors: list[dict] = field(default_factory=list)  # Mode B only - the weighted breakdown, weights shown


@dataclass
class TwoPlans:
    """The diagram's 'two plans, not one'. Both are independently verified
    before they reach the human gate."""

    cheapest: Plan
    merit_preserving: Plan
    frontier: list[FrontierPoint] = field(default_factory=list)
    identical: bool = False        # Mode A with no merit signal, or they coincide

    def get(self, choice: PlanChoice) -> Plan:
        return self.cheapest if choice == "cheapest" else self.merit_preserving


@dataclass
class LoopResult:
    plans: Optional[TwoPlans]
    abstentions: list[Abstention]
    trace: list[TraceEvent]
    stopped_reason: str = ""       # "" = clean finish, ready for the human gate
    trigger: str = "goal"


# --- Internal shapes (not part of the frozen §0 wire contract) --------------


@dataclass
class Lot:
    """One purchase lot within one folio. FIFO consumes these oldest-first,
    and never mixes lots from two folios even when the scheme matches."""

    lot_id: str
    scheme_id: str
    folio: str
    units: float
    purchase_date: str
    purchase_nav: float


@dataclass
class Holdings:
    lots: list[Lot] = field(default_factory=list)

    def for_folio(self, scheme_id: str, folio: str) -> list[Lot]:
        return sorted(
            (l for l in self.lots if l.scheme_id == scheme_id and l.folio == folio),
            key=lambda l: l.purchase_date,
        )

    def schemes(self) -> set[str]:
        return {l.scheme_id for l in self.lots}

    def total_units(self, scheme_id: str) -> float:
        return sum(l.units for l in self.lots if l.scheme_id == scheme_id)
