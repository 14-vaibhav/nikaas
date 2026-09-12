"""AMC directory + per-AMC document adapters (backs the Hunter's
name -> AMC -> repository hops).

`load_amfi_directory` ingests AMFI's public scheme master (the same
NAVAll.txt feed the NAV numbers come from) into
{scheme_name_lower: {scheme_code, amc, isin}} and caches it under
data/amc/. `ADAPTERS` holds hand-written navigation hints for a first few
AMCs; everything else falls back to a generic "find the PDF links near
the scheme name / the word Addendum" scan.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

AMFI_NAV_URL = "https://www.amfiindia.com/spages/NAVAll.txt"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "amc"
DIRECTORY_CACHE = CACHE_DIR / "amfi_directory.json"


@dataclass
class AMCAdapter:
    amc_id: str
    # Page(s) that list Scheme Information Documents / statutory disclosures.
    disclosures_urls: list[str] = field(default_factory=list)
    # Substrings that mark a link as an SID or an addendum.
    sid_hints: tuple[str, ...] = ("scheme information document", "sid")
    addendum_hints: tuple[str, ...] = ("addendum", "notice-cum-addendum")


# A starter set. Add an AMC here to give the Hunter a precise path;
# without one it uses the generic scan.
ADAPTERS: dict[str, AMCAdapter] = {
    "axis": AMCAdapter(
        amc_id="axis",
        disclosures_urls=["https://www.axismf.com/statutory-disclosure"],
    ),
    "hdfc": AMCAdapter(
        amc_id="hdfc",
        disclosures_urls=["https://www.hdfcfund.com/statutory-disclosures"],
    ),
    "sbi": AMCAdapter(
        amc_id="sbi",
        disclosures_urls=["https://www.sbimf.com/statutory-disclosures"],
    ),
    "icici": AMCAdapter(
        amc_id="icici",
        disclosures_urls=["https://www.icicipruamc.com/statutory-disclosures"],
    ),
    "kotak": AMCAdapter(
        amc_id="kotak",
        disclosures_urls=["https://www.kotakmf.com/Information/statutory-disclosure"],
    ),
}

# Map an AMFI "... Mutual Fund" name to our short amc_id.
_AMC_ID_HINTS = {
    "axis": "axis", "hdfc": "hdfc", "sbi": "sbi", "icici": "icici",
    "nippon": "nippon", "kotak": "kotak", "aditya birla": "adityabirla",
    "uti": "uti", "dsp": "dsp", "mirae": "mirae",
}


def amc_id_for(amc_name: str) -> str:
    low = amc_name.lower()
    for hint, amc_id in _AMC_ID_HINTS.items():
        if hint in low:
            return amc_id
    return re.sub(r"[^a-z0-9]+", "", low.replace("mutual fund", "").strip()) or "unknown"


def adapter_for(amc_id: str) -> Optional[AMCAdapter]:
    return ADAPTERS.get(amc_id)


# Words a CAS / statement row adds that carry no scheme identity - stripped
# before matching so "Axis Bluechip Fund - Direct Plan - Growth" and
# "Axis Bluechip Fund" compare as the same thing.
_NOISE_TOKENS = {
    "direct", "regular", "reg", "dir", "plan", "option", "growth", "idcw",
    "dividend", "payout", "reinvestment", "reinvest", "the", "of", "fund",
    "scheme", "an", "open", "ended", "-", "cum", "and", "&",
}


def normalize_scheme_name(name: str) -> str:
    """Lower-case, drop punctuation and plan/option noise words, collapse
    whitespace. The identity-bearing residue is what fuzzy matching should
    see - not the registrar's formatting."""
    cleaned = re.sub(r"[^a-z0-9]+", " ", name.lower())
    tokens = [t for t in cleaned.split() if t and t not in _NOISE_TOKENS]
    return " ".join(tokens)


def resolve_scheme(name: str, directory: dict[str, dict], threshold: float = 0.6) -> Optional[dict]:
    """Fuzzy-resolve a scheme name (as printed on an uploaded document, or
    as read off a statement row) to its AMFI directory entry - never a
    guess, `None` when nothing clears the threshold. Used by
    `agent/fund_identity.py`, `agent/intake.py` and `agent/reconcile.py`
    to give an uploaded document (or a holding it never came with a
    document for) a stable, citable identity.

    Matches on the normalized residue (plan/option noise removed from both
    sides) and additionally requires every significant token of the query
    to appear in the candidate, so "Axis Bluechip" never lands on "Axis
    Value Fund" just because the strings are the same length."""
    if not directory:
        return None
    q = normalize_scheme_name(name)
    if not q:
        return None
    norm_index: dict[str, str] = {}
    for key in directory:
        norm_index.setdefault(normalize_scheme_name(key), key)
    hits = difflib.get_close_matches(q, list(norm_index), n=3, cutoff=threshold)
    q_tokens = set(q.split())
    q_collapsed = q.replace(" ", "")
    for h in hits:
        h_tokens = set(h.split())
        # A spacing difference ("Blue Chip" vs "Bluechip") shouldn't read as
        # a different fund, so compare the space-collapsed form too - but a
        # hit that only clears the similarity threshold with no token or
        # collapsed-form identity ("HDFC Top 100 Fund" vs "HDFC NIFTY 100
        # ETF") is exactly the wrong-scheme match this function promises
        # never to make.
        if q_tokens.issubset(h_tokens) or h_tokens.issubset(q_tokens) or q_collapsed == h.replace(" ", ""):
            return directory[norm_index[h]]
    return None


def load_amfi_directory(
    path: Path | None = None, *, refresh: bool = False, http=None
) -> dict[str, dict]:
    """Returns {scheme_name_lower: {"scheme_code", "amc", "isin"}}.

    The cache is the default fast path. If it is absent or stale/forced, a
    network refresh is attempted. A failed refresh never destroys the cached
    data; instead it falls back to the last known directory so the system can
    distinguish a cache miss from a real scheme absence. """
    path = path or DIRECTORY_CACHE
    cached: dict[str, dict] = {}
    if path.exists():
        try:
            cached = json.loads(path.read_text())
        except (TypeError, ValueError):
            cached = {}

    if not refresh and cached:
        return cached

    try:
        text = _fetch_navall(http)
    except Exception:
        if cached:
            return cached
        raise

    directory = parse_amfi_navall(text)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(directory))
    return directory


def parse_amfi_navall(text: str) -> dict[str, dict]:
    directory: dict[str, dict] = {}
    current_amc = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if ";" not in line:
            if "schemes" in line.lower() and "(" in line:
                continue  # scheme-type sub-header, not an AMC name
            current_amc = line
            continue
        parts = line.split(";")
        if len(parts) < 6 or parts[0].strip().lower() == "scheme code":
            continue
        scheme_code, isin_a, _isin_b, scheme_name = parts[0], parts[1], parts[2], parts[3]
        directory[scheme_name.strip().lower()] = {
            "scheme_code": scheme_code.strip(),
            "name": scheme_name.strip(),
            "amc": current_amc,
            "amc_id": amc_id_for(current_amc),
            "isin": isin_a.strip(),
            "nav": _parse_float(parts[4]) if len(parts) > 4 else None,
            "nav_date": parts[5].strip() if len(parts) > 5 else None,
        }
    return directory


def _parse_float(raw: str) -> Optional[float]:
    try:
        return float(raw.strip())
    except ValueError:
        return None


def _fetch_navall(http=None) -> str:
    if http is None:
        import httpx
        http = httpx.Client(timeout=30, follow_redirects=True)
        close = True
    else:
        close = False
    try:
        resp = http.get(AMFI_NAV_URL, headers={"User-Agent": "nikaas-agent/1.0 (+research)"})
        resp.raise_for_status()
        return resp.text
    finally:
        if close:
            http.close()
