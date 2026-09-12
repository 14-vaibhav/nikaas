"""Constraint supersession resolver (PRD §5.5, Build Spec A §2 item 6).

SID clause + N addenda -> which rule governs on the redemption date?
Order by effective_date; later supersedes earlier for the same
(scheme_id, kind). Ambiguous cases (same effective date, contradictory
values) are never picked — they're returned separately so the
orchestrator can abstain and escalate instead of guessing.
"""

from __future__ import annotations

from dataclasses import replace

from agent.contracts import Constraint


class Ambiguity:
    def __init__(self, scheme_id: str, kind: str, constraints: list[Constraint]):
        self.scheme_id = scheme_id
        self.kind = kind
        self.constraints = constraints

    def __repr__(self) -> str:
        ids = [c.id for c in self.constraints]
        return f"Ambiguity(scheme={self.scheme_id}, kind={self.kind}, constraints={ids})"


def resolve(constraints: list[Constraint]) -> tuple[list[Constraint], list[Ambiguity]]:
    """Returns (resolved_constraints, ambiguities).

    `resolved_constraints` is the input list with `superseded_by` set on
    every constraint an addendum has overtaken, ordered by effective_date
    ascending within each (scheme_id, kind) group. The winning constraint
    in each unambiguous group keeps `superseded_by=None`.

    An `Ambiguity` is emitted instead of a silent pick whenever the two
    most-recent constraints in a group share an effective_date and
    disagree on `value` — per §5.5, the resolver must never choose here.
    """
    groups: dict[tuple[str, str], list[Constraint]] = {}
    for c in constraints:
        groups.setdefault((c.scheme_id, c.kind), []).append(c)

    resolved: list[Constraint] = []
    ambiguities: list[Ambiguity] = []

    for (scheme_id, kind), group in groups.items():
        ordered = sorted(group, key=lambda c: c.source.effective_date)
        latest_date = ordered[-1].source.effective_date
        tied = [c for c in ordered if c.source.effective_date == latest_date]

        if len(tied) > 1 and _values_conflict(tied):
            ambiguities.append(Ambiguity(scheme_id, kind, tied))
            # Every constraint in this group is unusable until a human
            # resolves the conflict — mark them all as pointing nowhere
            # definitive, but do NOT invent a superseded_by chain that
            # would let one silently win.
            resolved.extend(ordered)
            continue

        winner = tied[0] if len(tied) == 1 else max(tied, key=lambda c: c.id)
        for c in ordered:
            if c.id == winner.id:
                resolved.append(replace(c, superseded_by=None))
            else:
                resolved.append(replace(c, superseded_by=winner.id))

    return resolved, ambiguities


def _values_conflict(constraints: list[Constraint]) -> bool:
    first = constraints[0].value
    return any(c.value != first for c in constraints[1:])
