"""Independent plan verifier (PRD §6.1, Build Spec A §2 item 1).

Pure Python. Zero AI imports — check this with `grep -n "import" agent/verifier.py`
before committing; nothing here should ever import an LLM client. This file
defines what a *correct* plan is, and it is intentionally the first file
built: everything upstream (allocator, orchestrator, repair loop) is shaped
by what it checks.

The verifier does not trust the allocator's arithmetic. It recomputes FIFO
consumption, exit load, and tax independently from `holdings` and
`constraints`, then compares the recomputed numbers against what the plan
claims. A mismatch is a violation regardless of why it happened.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from agent.evidence import FRESHNESS_DAYS, DEFAULT_FRESHNESS_DAYS
from agent.contracts import Constraint, Holdings, Plan, PlanLeg, Violation

TOLERANCE_RUPEES = 1.0

DEFAULT_LTCG_THRESHOLD_DAYS = 365
DEFAULT_LTCG_RATE = 0.125
DEFAULT_STCG_RATE = 0.20
DEFAULT_LTCG_EXEMPTION = 125_000.0
DEFAULT_SETTLEMENT_DAYS = 3


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


def _is_stale(constraint: Constraint, today: date) -> bool:
    retrieved_at = _parse_date(constraint.source.retrieved_at[:10])
    threshold_days = FRESHNESS_DAYS.get(constraint.kind, DEFAULT_FRESHNESS_DAYS)
    return (today - retrieved_at) > timedelta(days=threshold_days)


class ConstraintIndex:
    """Lookup helpers over the flat constraint list the orchestrator hands in."""

    def __init__(self, constraints: list[Constraint]):
        self._by_id = {c.id: c for c in constraints}
        self._by_scheme_kind: dict[tuple[str, str], list[Constraint]] = {}
        for c in constraints:
            self._by_scheme_kind.setdefault((c.scheme_id, c.kind), []).append(c)

    def get(self, constraint_id: str) -> Optional[Constraint]:
        return self._by_id.get(constraint_id)

    def active_for(self, scheme_id: str, kind: str) -> Optional[Constraint]:
        """The constraint of this kind for this scheme that is not itself
        superseded. If several are active (ambiguous), returns the most
        recently effective one — callers that need to *detect* ambiguity
        should use resolver.py, not this."""
        candidates = [
            c for c in self._by_scheme_kind.get((scheme_id, kind), [])
            if c.superseded_by is None
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda c: c.source.effective_date)

    def active_count(self, scheme_id: str, kind: str) -> int:
        return len([
            c for c in self._by_scheme_kind.get((scheme_id, kind), [])
            if c.superseded_by is None
        ])

    def ambiguous(self, scheme_id: str, kind: str) -> bool:
        """Two or more un-superseded constraints of one kind for one scheme -
        the resolver's territory, never something to silently pick between."""
        return self.active_count(scheme_id, kind) >= 2


def verify(plan: Plan, constraints: list[Constraint], holdings: Holdings) -> list[Violation]:
    violations: list[Violation] = []
    index = ConstraintIndex(constraints)
    today = date.today()

    total_ltcg_gain = 0.0
    total_stcg_gain = 0.0
    recomputed_exit_load_total = 0.0
    latest_sell_date = None

    for leg_index, leg in enumerate(plan.legs):
        violations.extend(_check_provenance(leg, leg_index, index, today))

        fifo_result = _check_fifo_and_lock_in(leg, leg_index, holdings, index)
        violations.extend(fifo_result.violations)
        if fifo_result.fatal:
            continue  # can't recompute exit load/tax without a valid FIFO consumption

        recomputed_exit_load_total += fifo_result.recomputed_exit_load
        if abs(fifo_result.recomputed_exit_load - leg.exit_load) > TOLERANCE_RUPEES:
            violations.append(Violation(
                code="SHORTFALL",
                leg_index=leg_index,
                detail=(
                    f"{leg.scheme_id}/{leg.folio}: exit load recomputed per-unit against each "
                    f"lot's own purchase date is Rs.{fifo_result.recomputed_exit_load:.2f}, "
                    f"plan claims Rs.{leg.exit_load:.2f}"
                ),
                repair_hint="recompute_exit_load",
            ))

        total_ltcg_gain += fifo_result.ltcg_gain
        total_stcg_gain += fifo_result.stcg_gain

        sell_date = _parse_date(leg.sell_date)
        if latest_sell_date is None or sell_date > latest_sell_date:
            latest_sell_date = sell_date

    violations.extend(_check_silent_drops(plan, holdings))

    tax_rule = first_tax_rule(constraints)
    ltcg_rate = tax_rule.value.get("ltcg_rate", DEFAULT_LTCG_RATE) if tax_rule else DEFAULT_LTCG_RATE
    stcg_rate = tax_rule.value.get("stcg_rate", DEFAULT_STCG_RATE) if tax_rule else DEFAULT_STCG_RATE
    exemption = tax_rule.value.get("ltcg_exemption", DEFAULT_LTCG_EXEMPTION) if tax_rule else DEFAULT_LTCG_EXEMPTION

    taxable_ltcg = max(0.0, total_ltcg_gain - exemption)  # exemption applied ONCE, portfolio-wide
    recomputed_tax = taxable_ltcg * ltcg_rate + total_stcg_gain * stcg_rate
    if abs(recomputed_tax - plan.totals.tax) > TOLERANCE_RUPEES:
        violations.append(Violation(
            code="SHORTFALL",
            leg_index=None,
            detail=(
                f"Portfolio-wide tax recomputed with the Rs.{exemption:.0f} LTCG exemption "
                f"applied once is Rs.{recomputed_tax:.2f}, plan claims Rs.{plan.totals.tax:.2f}"
            ),
            repair_hint="recompute_tax_single_exemption",
        ))

    recomputed_gross = sum(leg.gross for leg in plan.legs)
    recomputed_net = recomputed_gross - recomputed_exit_load_total - recomputed_tax
    if recomputed_net + TOLERANCE_RUPEES < plan.goal.amount:
        violations.append(Violation(
            code="SHORTFALL",
            leg_index=None,
            detail=(
                f"Recomputed net proceeds Rs.{recomputed_net:.2f} fall short of the "
                f"Rs.{plan.goal.amount:.2f} target"
            ),
            repair_hint="raise_more_or_widen_candidate_set",
        ))

    cutoff = _first_constraint_of_kind(constraints, "cutoff")
    settlement_days = cutoff.value.get("settlement_days", DEFAULT_SETTLEMENT_DAYS) if cutoff else DEFAULT_SETTLEMENT_DAYS
    if latest_sell_date is not None:
        delivered_on = latest_sell_date + timedelta(days=settlement_days)
        by_date = _parse_date(plan.goal.by_date)
        if delivered_on > by_date:
            violations.append(Violation(
                code="SHORTFALL",
                leg_index=None,
                detail=(
                    f"Last leg sells on {latest_sell_date.isoformat()}; with a "
                    f"{settlement_days}-day settlement that delivers {delivered_on.isoformat()}, "
                    f"after the {by_date.isoformat()} target"
                ),
                repair_hint="move_sell_dates_earlier",
            ))

    return violations


def first_tax_rule(constraints: list[Constraint]) -> Optional[Constraint]:
    """Public: allocator.py reuses this so its tax estimate and the
    verifier's independent recomputation read the same rule."""
    return _first_constraint_of_kind(constraints, "tax_rule")


def _first_constraint_of_kind(constraints: list[Constraint], kind: str) -> Optional[Constraint]:
    matches = [c for c in constraints if c.kind == kind and c.superseded_by is None]
    return matches[0] if matches else None


def _check_provenance(
    leg: PlanLeg, leg_index: int, index: ConstraintIndex, today: date
) -> list[Violation]:
    violations: list[Violation] = []
    for constraint_id in leg.constraints_applied:
        constraint = index.get(constraint_id)
        if constraint is None or constraint.source is None:
            violations.append(Violation(
                code="NO_PROVENANCE",
                leg_index=leg_index,
                detail=f"{leg.scheme_id}/{leg.folio}: constraint {constraint_id} has no resolvable source",
                repair_hint="drop_constraint_or_refetch",
            ))
            continue
        if constraint.superseded_by is not None:
            violations.append(Violation(
                code="SUPERSEDED",
                leg_index=leg_index,
                detail=(
                    f"{leg.scheme_id}/{leg.folio}: constraint {constraint_id} is superseded "
                    f"by {constraint.superseded_by} but was applied anyway"
                ),
                repair_hint="use_superseding_constraint",
            ))
        if _is_stale(constraint, today):
            violations.append(Violation(
                code="STALE_SOURCE",
                leg_index=leg_index,
                detail=(
                    f"{leg.scheme_id}/{leg.folio}: constraint {constraint_id} retrieved "
                    f"{constraint.source.retrieved_at} exceeds freshness threshold for kind={constraint.kind}"
                ),
                repair_hint="refetch_source",
            ))
    return violations


class _FifoResult:
    def __init__(self):
        self.violations: list[Violation] = []
        self.recomputed_exit_load = 0.0
        self.ltcg_gain = 0.0
        self.stcg_gain = 0.0
        self.fatal = False


def _check_fifo_and_lock_in(
    leg: PlanLeg, leg_index: int, holdings: Holdings, index: ConstraintIndex
) -> _FifoResult:
    result = _FifoResult()
    own_folio_lots = holdings.for_folio(leg.scheme_id, leg.folio)
    own_folio_total = sum(l.units for l in own_folio_lots)

    if own_folio_total + 1e-9 < leg.units:
        scheme_wide_total = holdings.total_units(leg.scheme_id)
        if scheme_wide_total + 1e-9 >= leg.units:
            # Enough units exist for this scheme, but only by counting units
            # held in a *different* folio — that would require merging two
            # legally-separate FIFO queues.
            result.violations.append(Violation(
                code="FIFO_MERGE",
                leg_index=leg_index,
                detail=(
                    f"{leg.scheme_id}/{leg.folio} only holds {own_folio_total:g} units but "
                    f"the leg claims {leg.units:g}; covering the gap requires pulling from "
                    f"another folio's FIFO queue, which is not permitted"
                ),
                repair_hint="split_leg_per_folio",
            ))
        else:
            result.violations.append(Violation(
                code="SHORTFALL",
                leg_index=leg_index,
                detail=(
                    f"{leg.scheme_id}/{leg.folio} holds only {own_folio_total:g} units, "
                    f"leg claims {leg.units:g}"
                ),
                repair_hint="reduce_leg_units",
            ))
        result.fatal = True
        return result

    exit_load_constraint = index.active_for(leg.scheme_id, "exit_load")
    lock_in_constraint = index.active_for(leg.scheme_id, "lock_in")
    tax_rule = index.active_for(leg.scheme_id, "tax_rule")
    ltcg_threshold_days = (
        tax_rule.value.get("ltcg_threshold_days", DEFAULT_LTCG_THRESHOLD_DAYS)
        if tax_rule else DEFAULT_LTCG_THRESHOLD_DAYS
    )

    sell_date = _parse_date(leg.sell_date)
    sell_price = leg.gross / leg.units if leg.units else 0.0

    remaining = leg.units
    for lot in own_folio_lots:  # already sorted oldest-first by Holdings.for_folio
        if remaining <= 1e-9:
            break
        take = min(lot.units, remaining)
        remaining -= take
        purchase_date = _parse_date(lot.purchase_date)
        holding_days = (sell_date - purchase_date).days

        if lock_in_constraint is not None:
            window_days = lock_in_constraint.value.get("window_days", 0)
            if holding_days < window_days:
                result.violations.append(Violation(
                    code="LOCK_IN_BREACH",
                    leg_index=leg_index,
                    detail=(
                        f"{leg.scheme_id}/{leg.folio} lot {lot.lot_id}: {take:g} units purchased "
                        f"{lot.purchase_date} are locked until day {window_days}, but sell date "
                        f"{leg.sell_date} is only day {holding_days}"
                    ),
                    repair_hint="exclude_locked_lots",
                ))

        if exit_load_constraint is not None:
            window_days = exit_load_constraint.value.get("window_days", 0)
            if holding_days < window_days:
                pct = exit_load_constraint.value.get("pct", 0.0)
                result.recomputed_exit_load += take * sell_price * (pct / 100.0)

        gain = (sell_price - lot.purchase_nav) * take
        if holding_days >= ltcg_threshold_days:
            result.ltcg_gain += gain
        else:
            result.stcg_gain += gain

    return result


def _check_silent_drops(plan: Plan, holdings: Holdings) -> list[Violation]:
    legs_schemes = {leg.scheme_id for leg in plan.legs}
    abstained_schemes = {a.scheme_id for a in plan.abstained}
    candidate_schemes = holdings.schemes()
    missing = candidate_schemes - legs_schemes - abstained_schemes
    return [
        Violation(
            code="SILENT_DROP",
            leg_index=None,
            detail=f"{scheme_id} has holdings but appears in neither legs nor abstained",
            repair_hint="explain_or_abstain",
        )
        for scheme_id in sorted(missing)
    ]


def verify_mode_b_factors(candidates: list[dict]) -> list[Violation]:
    """PRD §5.3 / §6.1: every Mode B score factor must trace to a dated
    source. A factor without one must be omitted from the score, never
    scored on silently. `candidates` is the Selector's raw output list of
    {scheme_id, factors: [{name, value, weight, source}]}."""
    violations: list[Violation] = []
    for candidate in candidates:
        for factor in candidate.get("factors", []):
            source = factor.get("source")
            if not source or not source.get("effective_date"):
                violations.append(Violation(
                    code="NO_PROVENANCE",
                    leg_index=None,
                    detail=(
                        f"{candidate.get('scheme_id')}: Mode B factor "
                        f"'{factor.get('name')}' has no dated source"
                    ),
                    repair_hint="omit_factor_from_score",
                ))
    return violations
