"""JSON <-> dataclass conversion for the §0 wire shapes.

Kept separate from contracts.py so that module stays a pure shape
definition, and separate from verifier.py so the verifier never needs
to know anything came from JSON at all.
"""

from __future__ import annotations

from agent.contracts import (
    Abstention,
    Constraint,
    FrontierPoint,
    Goal,
    Holdings,
    Lot,
    Plan,
    PlanLeg,
    Source,
    Totals,
    Violation,
)


def source_from_dict(d: dict) -> Source:
    return Source(
        doc=d["doc"], page=d["page"], clause=d.get("clause"), url=d.get("url"),
        effective_date=d["effective_date"], retrieved_at=d["retrieved_at"],
    )


def constraint_from_dict(d: dict) -> Constraint:
    return Constraint(
        id=d["id"], scheme_id=d["scheme_id"], kind=d["kind"], value=d["value"],
        source=source_from_dict(d["source"]), confidence=d.get("confidence", "unverified"),
        superseded_by=d.get("superseded_by"),
    )


def lot_from_dict(d: dict) -> Lot:
    return Lot(
        lot_id=d["lot_id"], scheme_id=d["scheme_id"], folio=d["folio"],
        units=d["units"], purchase_date=d["purchase_date"], purchase_nav=d["purchase_nav"],
    )


def holdings_from_dict(lots: list[dict]) -> Holdings:
    return Holdings(lots=[lot_from_dict(l) for l in lots])


def goal_from_dict(d: dict) -> Goal:
    return Goal(amount=d["amount"], by_date=d["by_date"], mode=d.get("mode", "A"))


def plan_leg_from_dict(d: dict) -> PlanLeg:
    return PlanLeg(
        scheme_id=d["scheme_id"], folio=d["folio"], units=d["units"], gross=d["gross"],
        exit_load=d["exit_load"], tax=d["tax"], net=d["net"], sell_date=d["sell_date"],
        constraints_applied=d.get("constraints_applied", []),
    )


def plan_from_dict(d: dict) -> Plan:
    return Plan(
        plan_id=d["plan_id"], goal=goal_from_dict(d["goal"]),
        legs=[plan_leg_from_dict(l) for l in d.get("legs", [])],
        totals=Totals(**d["totals"]),
        abstained=[Abstention(**a) for a in d.get("abstained", [])],
        verdict=d.get("verdict", "rejected"),
        violations=[violation_from_dict(v) for v in d.get("violations", [])],
        frontier=[FrontierPoint(**f) for f in d.get("frontier", [])],
        kind=d.get("kind", "cheapest"),
        approved=d.get("approved", False),
    )


def violation_from_dict(d: dict) -> Violation:
    return Violation(code=d["code"], leg_index=d.get("leg_index"), detail=d["detail"],
                      repair_hint=d.get("repair_hint", ""))
