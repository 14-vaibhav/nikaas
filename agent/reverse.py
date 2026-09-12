"""Reverse mode (PRD §5.9): the second door into the same machinery.

The forward loop answers "I need Rs.X by date D - which schemes, how much,
when". Reverse mode answers the mirror question: "what can I take out
*today* at zero exit load and the least tax?" - useful when the amount is
flexible and the constraint is "don't pay to leave".

Same constraint model, same FIFO discipline, same provenance rule as the
verifier - a slice of a folio's queue is "free" only when every lot in it
has cleared both its exit-load window and any lock-in. Nothing here
guesses: a scheme whose exit-load rule is missing or ambiguous is
abstained, exactly as the forward loop would.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from agent.contracts import Abstention, Constraint, Holdings, Source
from agent.resolver import resolve as resolve_supersession
from agent.verifier import (
    ConstraintIndex,
    DEFAULT_LTCG_EXEMPTION,
    DEFAULT_LTCG_RATE,
    DEFAULT_LTCG_THRESHOLD_DAYS,
    DEFAULT_STCG_RATE,
    _parse_date,
    first_tax_rule,
)


@dataclass
class FreeSlice:
    """The part of one folio's FIFO queue that can be redeemed today with
    no exit load and no lock-in breach."""

    scheme_id: str
    folio: str
    free_units: float
    free_value: float
    ltcg_gain: float
    stcg_gain: float
    tax_if_sold: float          # this slice's share of tax, exemption applied portfolio-wide
    locked_units: float         # units still inside a lock-in
    load_bearing_units: float   # units still inside an exit-load window
    sources: list[Source] = field(default_factory=list)


@dataclass
class ReversePlan:
    as_of: str
    slices: list[FreeSlice]
    total_free_value: float
    total_tax: float
    abstained: list[Abstention] = field(default_factory=list)

    @property
    def net_if_all_taken(self) -> float:
        return round(self.total_free_value - self.total_tax, 2)


def reverse(
    holdings: Holdings,
    constraints: list[Constraint],
    navs: dict[str, float],
    candidate_schemes: list[str],
    *,
    as_of: date | None = None,
) -> ReversePlan:
    as_of = as_of or date.today()

    # Same first move as the forward loop: fold stacked addenda down by
    # effective_date. A genuine tie (same date, contradictory values) stays
    # unresolved and its scheme is abstained below - never picked.
    resolved, ambiguities = resolve_supersession(constraints)
    ambiguous_schemes = {(a.scheme_id, a.kind) for a in ambiguities}
    index = ConstraintIndex(resolved)

    tax_rule = first_tax_rule(constraints)
    ltcg_rate = tax_rule.value.get("ltcg_rate", DEFAULT_LTCG_RATE) if tax_rule else DEFAULT_LTCG_RATE
    stcg_rate = tax_rule.value.get("stcg_rate", DEFAULT_STCG_RATE) if tax_rule else DEFAULT_STCG_RATE
    exemption = (
        tax_rule.value.get("ltcg_exemption", DEFAULT_LTCG_EXEMPTION) if tax_rule
        else DEFAULT_LTCG_EXEMPTION
    )

    raw_slices: list[FreeSlice] = []
    abstained: list[Abstention] = []

    for scheme_id in candidate_schemes:
        nav = navs.get(scheme_id)
        if nav is None:
            abstained.append(Abstention(scheme_id=scheme_id, reason="no NAV available"))
            continue

        # A genuine same-date tie the resolver could not break: abstain,
        # never pick. (Ordinary stacked addenda were already folded down.)
        if (scheme_id, "exit_load") in ambiguous_schemes:
            abstained.append(Abstention(
                scheme_id=scheme_id,
                reason="contradictory exit-load addenda share an effective date - abstaining rather than guessing",
            ))
            continue
        if index.ambiguous(scheme_id, "exit_load"):
            abstained.append(Abstention(
                scheme_id=scheme_id,
                reason="two active exit-load constraints could not be resolved - abstaining rather than guessing",
            ))
            continue

        exit_load = index.active_for(scheme_id, "exit_load")
        if exit_load is None:
            abstained.append(Abstention(
                scheme_id=scheme_id,
                reason="no exit-load rule on file - cannot certify a zero-load exit",
            ))
            continue

        lock_in = index.active_for(scheme_id, "lock_in")
        tax_for_scheme = index.active_for(scheme_id, "tax_rule")
        ltcg_threshold = (
            tax_for_scheme.value.get("ltcg_threshold_days", DEFAULT_LTCG_THRESHOLD_DAYS)
            if tax_for_scheme else DEFAULT_LTCG_THRESHOLD_DAYS
        )
        load_window = exit_load.value.get("window_days", 0)
        lock_window = lock_in.value.get("window_days", 0) if lock_in else 0

        folios = sorted({l.folio for l in holdings.lots if l.scheme_id == scheme_id})
        for folio in folios:
            lots = holdings.for_folio(scheme_id, folio)  # oldest-first
            free_units = locked = load_bearing = 0.0
            ltcg_gain = stcg_gain = 0.0
            for lot in lots:
                held = (as_of - _parse_date(lot.purchase_date)).days
                if held < lock_window:
                    locked += lot.units
                    continue
                if held < load_window:
                    load_bearing += lot.units
                    continue
                free_units += lot.units
                gain = (nav - lot.purchase_nav) * lot.units
                if held >= ltcg_threshold:
                    ltcg_gain += gain
                else:
                    stcg_gain += gain
            if free_units <= 1e-9 and locked <= 1e-9 and load_bearing <= 1e-9:
                continue
            raw_slices.append(FreeSlice(
                scheme_id=scheme_id, folio=folio,
                free_units=round(free_units, 4),
                free_value=round(free_units * nav, 2),
                ltcg_gain=round(ltcg_gain, 2), stcg_gain=round(stcg_gain, 2),
                tax_if_sold=0.0,  # filled in below, once the exemption is shared out
                locked_units=round(locked, 4),
                load_bearing_units=round(load_bearing, 4),
                sources=[s for s in (exit_load.source, getattr(lock_in, "source", None),
                                     getattr(tax_for_scheme, "source", None)) if s is not None],
            ))

    # The Rs.1.25L LTCG exemption is one portfolio-wide budget. Allocate it
    # to the highest-gain slices first so the reported tax is the honest
    # minimum, then split each slice's own tax out for display.
    _apply_exemption_and_tax(raw_slices, exemption, ltcg_rate, stcg_rate)

    slices = sorted(
        (s for s in raw_slices if s.free_units > 1e-9),
        key=lambda s: (s.tax_if_sold / s.free_value if s.free_value else 0.0),
    )
    total_free = round(sum(s.free_value for s in slices), 2)
    total_tax = round(sum(s.tax_if_sold for s in slices), 2)
    return ReversePlan(
        as_of=as_of.isoformat(), slices=slices,
        total_free_value=total_free, total_tax=total_tax, abstained=abstained,
    )


def _apply_exemption_and_tax(
    slices: list[FreeSlice], exemption: float, ltcg_rate: float, stcg_rate: float
) -> None:
    remaining_exemption = exemption
    for s in sorted(slices, key=lambda s: s.ltcg_gain, reverse=True):
        used = min(remaining_exemption, max(0.0, s.ltcg_gain))
        remaining_exemption -= used
        taxable_ltcg = max(0.0, s.ltcg_gain - used)
        s.tax_if_sold = round(taxable_ltcg * ltcg_rate + max(0.0, s.stcg_gain) * stcg_rate, 2)
