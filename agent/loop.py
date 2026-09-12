"""THE LOOP (diagram centre): observe -> decide -> act -> incorporate,
running until there are no gaps left (hand to the human) or the budget
runs low (stop, finish safe).

The order of steps is not fixed in advance. Each pass, OBSERVE rebuilds
the gap list from current state and DECIDE picks one gap and a route.
That is what makes this an agent loop and not a pipeline.

Entry points (README: 'one agent, four entry points'): a human goal, the
Watcher, a scheduler, or a self-check re-run. `trigger` records which;
`prior` carries the previous two plans when the Watcher is re-running so
the caller can diff.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent.act import act, incorporate
from agent.budget import Budget
from agent.contracts import (
    Abstention,
    Candidate,
    Doubt,
    Goal,
    Holdings,
    LoopResult,
    Mode,
    Route,
    Trigger,
    TwoPlans,
)
from agent.decide import decide
from agent.evidence import EvidenceStore
from agent.merit import top_merit_fund_ids
from agent.observe import observe
from agent.selector import select
from agent.trace import TraceLog

MAX_PASSES = 80


@dataclass
class State:
    goal: Goal
    mode: Mode
    holdings: Holdings
    navs: dict[str, float]
    evidence: EvidenceStore
    candidates: list[Candidate] = field(default_factory=list)
    plans: TwoPlans | None = None
    self_checked: bool = False
    self_check_rounds: int = 0
    resolved_kinds: set[tuple[str, str]] = field(default_factory=set)
    abstained: dict[str, str] = field(default_factory=dict)
    excluded: set[str] = field(default_factory=set)
    gap_attempts: dict[tuple, int] = field(default_factory=dict)
    doubts: list[Doubt] = field(default_factory=list)
    widened: bool = False
    stopped_reason: str = ""
    prior: TwoPlans | None = None

    def candidate_ids(self) -> list[str]:
        return [
            c.fund_id for c in self.candidates
            if c.fund_id not in self.abstained and c.fund_id not in self.excluded
        ]

    def merit_keep(self) -> set[str]:
        active_ids = set(self.candidate_ids())
        active = {c.fund_id: c for c in self.candidates if c.fund_id in active_ids}
        if len(active) < 2:
            return set()
        return top_merit_fund_ids(active)

    def abstain(self, fund_id: str, reason: str) -> None:
        if fund_id not in self.abstained or (reason and not self.abstained[fund_id]):
            self.abstained[fund_id] = reason

    def invalidate_plans(self, _reason: str = "") -> None:
        self.plans = None
        self.self_checked = False

    def clear_doubt(self, gap) -> None:
        if gap.kind != "self_check_doubt":
            return
        label = gap.reason.split(":", 1)[0].strip()
        self.doubts = [
            d for d in self.doubts
            if not (d.label == label and d.fund_id == gap.fund_id)
        ]


def run(
    goal: Goal,
    mode: Mode,
    fund_ids: list[str],
    holdings: Holdings,
    navs: dict[str, float],
    *,
    hunter,
    evidence: EvidenceStore | None = None,
    trace: TraceLog | None = None,
    budget: Budget | None = None,
    trigger: Trigger = Trigger.GOAL,
    prior: TwoPlans | None = None,
    raw_mode_b: list[dict] | None = None,
) -> LoopResult:
    trace = trace or TraceLog()
    budget = budget or Budget()
    evidence = evidence or EvidenceStore()
    state = State(goal=goal, mode=mode, holdings=holdings, navs=navs, evidence=evidence, prior=prior)

    # Step 0 - which funds are candidates?
    state.candidates = select(mode, fund_ids, holdings, navs, raw_mode_b)

    # A held fund the human didn't tick (Mode A) or that Mode B never
    # shortlisted is a deliberate exclusion, not a fund the loop forgot -
    # record it now so the verifier's SILENT_DROP check sees it explained
    # rather than mistaking Step 0's own boundary for a dropped fund.
    for excluded_id in sorted(holdings.schemes() - set(fund_ids)):
        state.abstain(
            excluded_id,
            "not selected as a candidate at Step 0"
            + (" (Mode A tick list)" if mode == "A" else ""),
        )

    trace.emit(
        "select",
        f"Mode {mode}: {len(state.candidates)} candidate fund(s)",
        output={
            "mode": mode,
            "candidates": [c.fund_id for c in state.candidates],
            "merit": {c.fund_id: c.merit_score for c in state.candidates},
            "merit_basis": next((c.merit_basis for c in state.candidates), ""),
            "factors": {c.fund_id: c.factors for c in state.candidates if c.factors},
            "omitted_factors": {
                c.fund_id: c.omitted_factors for c in state.candidates if c.omitted_factors
            },
        },
        verdict="ok",
        budget=budget.as_dict(),
    )

    passes = 0
    while budget.alive() and passes < MAX_PASSES:
        passes += 1
        gaps = observe(state)
        trace.emit(
            "observe",
            f"{len(gaps)} open gap(s)" if gaps else "No open gaps - ready for the human gate",
            output={"gaps": [g.kind for g in gaps]},
            verdict="ok",
            budget=budget.as_dict(),
        )
        if not gaps:
            break

        gap, route = decide(gaps, state, budget)
        state.gap_attempts[gap.key()] = gap.attempts + 1
        trace.emit(
            "decide",
            f"{gap.kind} -> {route.value}"
            + (f" ({gap.fund_id})" if gap.fund_id else ""),
            output={
                "gap": gap.kind,
                "route": route.value,
                "fund": gap.fund_id,
                "attempts": gap.attempts + 1,
                "reason": gap.reason,
            },
            verdict="ok",
            budget=budget.as_dict(),
        )

        if route is Route.STOP:
            state.stopped_reason = state.stopped_reason or _stop_reason(gap, budget)
            trace.emit("stop", state.stopped_reason, verdict="abstained", budget=budget.as_dict())
            break

        result = act(route, gap, state, hunter, budget, trace)
        incorporate(result, state, trace)

    return _finalize(state, trace, trigger)


def _stop_reason(gap, budget) -> str:
    """STOP is reached two ways: the budget really is nearly spent, or a
    plan keeps failing the same repairable way and re-trying won't help.
    Say which - 'budget low' on an unreachable goal misleads."""
    if not budget.low() and gap.kind == "plan_rejected" and gap.violation is not None:
        if gap.violation.code == "SHORTFALL":
            return "goal not reachable from the available holdings - no verified plan produced"
        return f"could not repair {gap.violation.code} after repeated tries - no verified plan produced"
    return "budget low - finishing with what is verified"


def _finalize(state: State, trace: TraceLog, trigger: Trigger) -> LoopResult:
    abstentions = [Abstention(scheme_id=f, reason=r) for f, r in sorted(state.abstained.items())]

    if state.plans is not None:
        for plan in (state.plans.cheapest, state.plans.merit_preserving):
            _merge_abstentions(plan.abstained, state.abstained)

    if state.plans is None:
        trace.emit(
            "gate",
            "No verified plan to hand over"
            + (f" - {state.stopped_reason}" if state.stopped_reason else ""),
            verdict="abstained",
            output={"abstentions": [a.scheme_id for a in abstentions]},
        )
        return LoopResult(
            plans=None, abstentions=abstentions, trace=trace.events,
            stopped_reason=state.stopped_reason or "no allocation reached", trigger=trigger.value,
        )

    c, m = state.plans.cheapest, state.plans.merit_preserving
    trace.emit(
        "gate",
        f"Two plans ready for approval - cheapest: {c.verdict} (exit load Rs.{c.totals.exit_load:.0f}); "
        f"merit-preserving: {m.verdict} (exit load Rs.{m.totals.exit_load:.0f})"
        + (" [identical]" if state.plans.identical else ""),
        output={
            "cheapest_verdict": c.verdict,
            "merit_verdict": m.verdict,
            "identical": state.plans.identical,
            "abstentions": [a.scheme_id for a in abstentions],
        },
        verdict="ok" if c.verdict == "verified" else "violation",
    )
    return LoopResult(
        plans=state.plans, abstentions=abstentions, trace=trace.events,
        stopped_reason=state.stopped_reason, trigger=trigger.value,
    )


def _merge_abstentions(existing: list[Abstention], extra: dict[str, str]) -> None:
    have = {a.scheme_id for a in existing}
    for a in existing:
        if not a.reason and extra.get(a.scheme_id):
            a.reason = extra[a.scheme_id]
    for fund_id, reason in extra.items():
        if fund_id not in have:
            existing.append(Abstention(scheme_id=fund_id, reason=reason))
