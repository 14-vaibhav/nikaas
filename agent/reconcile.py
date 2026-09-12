"""Multi-RTA reconciliation (PRD §5.4).

A client's holdings arrive from more than one registrar - CAMS and
KFintech - with the same scheme spelled differently on each feed, the
same purchase sometimes reported twice, and folio numbers that must stay
separate because *in law they are separate FIFO queues*.

This module does four things and refuses to do a fifth:

  1. resolves each row's scheme name to a scheme id (fuzzy - client feeds
     are inconsistent), with a confidence band;
  2. detects exact duplicate lots reported by two feeds and keeps one;
  3. detects near-duplicates (same date + NAV, units a hair apart) and
     **flags them for a human** rather than picking;
  4. keeps every distinct folio as its own queue.

It never merges two folios, and never force-matches a scheme name it
isn't sure of - an unsure match is dropped and flagged, not guessed.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Literal, Optional

from agent.amc_registry import normalize_scheme_name, resolve_scheme
from agent.contracts import Holdings
from agent.holdings import MalformedHoldingError, load_holdings

CONFIDENT = 0.86   # >= this (on the normalized name): accept the match silently
PLAUSIBLE = 0.60   # [PLAUSIBLE, CONFIDENT): accept but flag for confirmation
                   # < PLAUSIBLE: drop the row, flag it unresolved

FlagKind = Literal[
    "ambiguous_scheme",   # matched, but not confidently - human should confirm
    "unresolved_scheme",  # no match cleared the bar - row dropped
    "duplicate_lot",      # identical lot from two feeds - one kept
    "near_duplicate",     # same date + NAV, units differ slightly - human decides
    "malformed_lot",      # negative units / future date - rejected at the door
]


@dataclass
class RawRow:
    source: str            # "CAMS", "KFintech", or a filename
    scheme_name: str       # exactly as printed on that feed
    folio: str
    units: float
    purchase_date: str
    purchase_nav: float


@dataclass
class ReconcileFlag:
    kind: FlagKind
    detail: str
    rows: list[int] = field(default_factory=list)   # indices into the input
    needs_human: bool = False


@dataclass
class ReconcileResult:
    holdings: Holdings
    flags: list[ReconcileFlag] = field(default_factory=list)
    resolved: dict[str, str] = field(default_factory=dict)   # raw name -> scheme_id
    kept_rows: int = 0
    dropped_rows: int = 0

    @property
    def needs_human(self) -> bool:
        return any(f.needs_human for f in self.flags)


def _norm_folio(folio: str) -> str:
    return re.sub(r"\s+", "", folio).upper().strip("/")


def _match_score(
    name: str, norm_index: dict[str, str], directory: dict
) -> tuple[Optional[dict], float, bool, bool]:
    """Best directory entry, its similarity score on the *normalized* name,
    whether a second entry is nearly as close (genuine ambiguity), and
    whether the query's identity tokens actually appear in the candidate
    (so a renamed scheme doesn't quietly land on a same-length neighbour)."""
    if not norm_index:
        return None, 0.0, False, False
    q = normalize_scheme_name(name)
    keys = difflib.get_close_matches(q, list(norm_index), n=2, cutoff=0.0)
    if not keys:
        return None, 0.0, False, False
    best = keys[0]
    best_score = difflib.SequenceMatcher(None, q, best).ratio()
    q_tokens, b_tokens = set(q.split()), set(best.split())
    token_ok = bool(q_tokens) and (q_tokens.issubset(b_tokens) or b_tokens.issubset(q_tokens))
    contested = False
    if len(keys) > 1:
        second_score = difflib.SequenceMatcher(None, q, keys[1]).ratio()
        contested = (best_score - second_score) < 0.06 and not token_ok
    return directory[norm_index[best]], best_score, contested, token_ok


def reconcile(
    rows: list[RawRow],
    amfi_directory: dict,
    *,
    known: dict[str, str] | None = None,
) -> ReconcileResult:
    """`known` optionally maps an already-resolved scheme name (any casing)
    to a scheme id - e.g. identities the intake step pinned from uploaded
    SIDs. Those win outright and are never flagged."""
    result = ReconcileResult(holdings=Holdings())
    flags = result.flags
    known_norm = {normalize_scheme_name(k): v for k, v in (known or {}).items()}
    norm_index: dict[str, str] = {}
    for key in amfi_directory:
        norm_index.setdefault(normalize_scheme_name(key), key)

    # --- 1. resolve scheme identity, per distinct printed name ----------
    name_to_id: dict[str, Optional[str]] = {}
    for name in {r.scheme_name for r in rows}:
        pinned = known_norm.get(normalize_scheme_name(name))
        if pinned is not None:
            name_to_id[name] = pinned
            continue
        match, score, contested, token_ok = _match_score(name, norm_index, amfi_directory)
        if match and score >= CONFIDENT and token_ok and not contested:
            name_to_id[name] = match["scheme_code"]
        elif match and score >= PLAUSIBLE:
            name_to_id[name] = match["scheme_code"]
            flags.append(ReconcileFlag(
                kind="ambiguous_scheme",
                detail=(
                    f"{name!r} matched to {match['name']!r} "
                    f"(similarity {score:.2f}"
                    + (", and another scheme is nearly as close" if contested else "")
                    + ") - confirm before relying on it"
                ),
                rows=[i for i, r in enumerate(rows) if r.scheme_name == name],
                needs_human=True,
            ))
        else:
            name_to_id[name] = None
            flags.append(ReconcileFlag(
                kind="unresolved_scheme",
                detail=f"{name!r} did not match any scheme in the AMFI master - rows dropped",
                rows=[i for i, r in enumerate(rows) if r.scheme_name == name],
                needs_human=True,
            ))
    result.resolved = {n: sid for n, sid in name_to_id.items() if sid}

    # --- 2/3. dedupe lots within each (scheme, folio) queue -------------
    # Folios are NEVER merged: the match key carries the normalized folio,
    # so two folios of one scheme stay two independent FIFO queues.
    seen: dict[tuple, int] = {}          # (sid, norm_folio, date, nav, units) -> input idx
    lot_seq = 0

    for i, r in enumerate(rows):
        sid = name_to_id.get(r.scheme_name)
        if sid is None:
            result.dropped_rows += 1
            continue

        try:
            units = float(r.units)
            nav = float(r.purchase_nav)
        except (TypeError, ValueError):
            flags.append(ReconcileFlag(
                kind="malformed_lot",
                detail=f"{r.scheme_name!r} from {r.source}: units/NAV are not numeric",
                rows=[i], needs_human=True,
            ))
            result.dropped_rows += 1
            continue

        nf = _norm_folio(r.folio)
        exact_key = (sid, nf, r.purchase_date, round(nav, 4), round(units, 4))
        if exact_key in seen:
            flags.append(ReconcileFlag(
                kind="duplicate_lot",
                detail=(
                    f"{r.scheme_name!r} folio {r.folio} {r.purchase_date}: identical lot from "
                    f"{rows[seen[exact_key]].source} and {r.source} - one kept"
                ),
                rows=[seen[exact_key], i],
                needs_human=False,
            ))
            result.dropped_rows += 1
            continue

        near = _near_duplicate(exact_key, seen, rows)
        if near is not None:
            flags.append(ReconcileFlag(
                kind="near_duplicate",
                detail=(
                    f"{r.scheme_name!r} folio {r.folio} {r.purchase_date}: {units:g} units from "
                    f"{r.source} vs {float(rows[near].units):g} from {rows[near].source} at the same "
                    f"NAV - kept both, confirm they are not the same purchase"
                ),
                rows=[near, i],
                needs_human=True,
            ))

        seen[exact_key] = i
        lot_seq += 1
        try:
            validated = load_holdings([{
                "lot_id": f"rec_{lot_seq}", "scheme_id": sid, "folio": r.folio,
                "units": units, "purchase_date": r.purchase_date,
                "purchase_nav": nav,
            }])
        except MalformedHoldingError as exc:
            flags.append(ReconcileFlag(
                kind="malformed_lot",
                detail=f"{r.scheme_name!r} from {r.source}: {exc}",
                rows=[i], needs_human=True,
            ))
            result.dropped_rows += 1
            continue
        result.holdings.lots.extend(validated.lots)

    result.kept_rows = len(result.holdings.lots)
    return result


def _near_duplicate(exact_key: tuple, seen: dict[tuple, int], rows: list[RawRow]) -> Optional[int]:
    sid, nf, pdate, pnav, units = exact_key
    for (s2, f2, d2, n2, u2), idx in seen.items():
        if (s2, f2, d2, n2) == (sid, nf, pdate, pnav) and u2 != units:
            if abs(u2 - units) <= 0.02 * max(u2, units):
                return idx
    return None
