"""Explain-to-client mode (PRD §5.10): the verified plan, regenerated in
plain language with every citation kept.

This is the artifact a distributor actually forwards. It is pure string
assembly over the already-verified `Plan` - **no LLM call**, so it can add
no claim the verifier did not already check and introduces no new failure
mode. Numbers are read straight off the plan; sources are read straight
off the constraints the plan applied.
"""

from __future__ import annotations

from datetime import date

from agent.contracts import Abstention, Constraint, Plan

_KIND_LABEL = {
    "exit_load": "exit load",
    "lock_in": "lock-in",
    "tax_rule": "capital-gains tax",
    "cutoff": "cut-off / settlement",
    "minimum": "minimum redemption",
    "expense_ratio": "expense ratio",
}


def _rupees(n: float) -> str:
    return "Rs." + f"{round(n):,}"


def _name(scheme_id: str, names: dict[str, str] | None) -> str:
    if names and scheme_id in names:
        return names[scheme_id]
    return scheme_id.removeprefix("s_").replace("_", " ").title()


def explain(
    plan: Plan,
    constraints: list[Constraint],
    *,
    names: dict[str, str] | None = None,
    mode_label: str = "",
    abstentions: list[Abstention] | None = None,
) -> str:
    by_id = {c.id: c for c in constraints}
    lines: list[str] = []

    goal = plan.goal
    lines.append(
        f"Plan to raise {_rupees(goal.amount)} (net of costs) by {goal.by_date}."
        + (f"  Basis: {mode_label}." if mode_label else "")
    )
    if plan.verdict != "verified":
        lines.append(
            "NOTE: this plan did not pass the independent verifier and is shown "
            "for reference only."
        )
    lines.append("")

    lines.append("What would be redeemed")
    lines.append("----------------------")
    for i, leg in enumerate(plan.legs, 1):
        held = _name(leg.scheme_id, names)
        load_txt = (
            f"exit load {_rupees(leg.exit_load)}" if leg.exit_load > 0.5
            else "no exit load"
        )
        lines.append(
            f"{i}. Sell {leg.units:,.2f} units of {held} from folio {leg.folio} "
            f"on {leg.sell_date}."
        )
        lines.append(
            f"   Oldest units are redeemed first (FIFO, as the tax rules require). "
            f"Gross {_rupees(leg.gross)}; {load_txt}."
        )
        for cid in leg.constraints_applied:
            c = by_id.get(cid)
            if c is None:
                continue
            s = c.source
            label = _KIND_LABEL.get(c.kind, c.kind)
            cite = f"{s.doc}"
            if s.page:
                cite += f", p.{s.page}"
            if s.clause:
                cite += f", clause {s.clause}"
            cite += f" (effective {s.effective_date})"
            lines.append(f"   - {label}: {_describe_value(c)} - {cite}")
        lines.append("")

    lines.append("Cost summary")
    lines.append("------------")
    lines.append(f"Gross proceeds        {_rupees(plan.totals.gross)}")
    lines.append(f"Exit load             {_rupees(plan.totals.exit_load)}")
    lines.append(
        f"Capital-gains tax     {_rupees(plan.totals.tax)}   "
        f"(the Rs.1,25,000 long-term exemption is applied once across the whole plan)"
    )
    lines.append(f"Net to you            {_rupees(plan.totals.net)}")
    lines.append("")

    cheaper = _cheaper_date(plan)
    if cheaper is not None:
        d, saving = cheaper
        lines.append(
            f"Timing: redeeming on {d} instead would cost about {_rupees(saving)} "
            f"less in exit load - still inside the {goal.by_date} deadline."
        )
        lines.append("")

    abst = abstentions if abstentions is not None else plan.abstained
    if abst:
        lines.append("Deliberately not touched")
        lines.append("------------------------")
        for a in abst:
            lines.append(f"- {_name(a.scheme_id, names)}: {a.reason or 'excluded'}")
        lines.append("")

    lines.append(
        "Figures were computed by an independent checker (no AI) against fund rules "
        "retrieved on the dates cited above. This is a cost calculation, not "
        "investment advice; the decision to redeem is yours."
    )
    return "\n".join(lines)


def _describe_value(c: Constraint) -> str:
    v = c.value
    if c.kind == "exit_load":
        pct = v.get("pct", 0)
        win = v.get("window_days", 0)
        if not pct or not win:
            return "nil"
        return f"{pct}% if redeemed within {win} days of purchase"
    if c.kind == "lock_in":
        return f"{v.get('window_days', 0)}-day lock-in"
    if c.kind == "tax_rule":
        return (
            f"long-term above {v.get('ltcg_threshold_days', 365)} days at "
            f"{v.get('ltcg_rate', 0.125) * 100:g}%, short-term at "
            f"{v.get('stcg_rate', 0.2) * 100:g}%"
        )
    if c.kind == "expense_ratio":
        return f"{v.get('pct', 0)}% total expense ratio"
    return str(v)


def _cheaper_date(plan: Plan) -> tuple[str, float] | None:
    if not plan.frontier:
        return None
    best = min(plan.frontier, key=lambda f: f.total_cost)
    if best.total_cost + 1.0 < plan.totals.exit_load:
        return best.date, plan.totals.exit_load - best.total_cost
    return None
