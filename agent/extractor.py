"""LLM-based constraint extraction (PRD §5.2, §8; Build Spec A §2 item 2).

Input: page text + page number for one document. Output: Constraint
objects with structured JSON output from the model, not freeform prose
parsed after the fact. Hard rule: every constraint must carry a page
and clause; if the model can't cite one, this returns nothing for that
page rather than an uncited constraint. `verifier.py` never imports
this module — extraction and checking stay strictly separated.
"""

from __future__ import annotations

import json
import os
from typing import Optional, Protocol

from agent.contracts import Constraint, Source

MODEL = "claude-sonnet-5"

# The per-kind numeric fields, as FLAT top-level properties rather than a
# nested "value: object" - a schema-following weakness observed live with
# Gemini's function-calling: a loosely-typed, undeclared nested object
# reliably comes back empty (`value: {}`) even when the model clearly read
# the number correctly, because the top-level, explicitly-declared "clause"
# field right next to it comes back populated every time. Flattening this
# out fixed it in testing against a real Gemini call, and does not hurt
# Anthropic's extraction (Claude already handled the nested shape fine).
_VALUE_FIELDS: dict[str, tuple[str, ...]] = {
    "exit_load": ("pct", "window_days"),
    "lock_in": ("window_days",),
    "expense_ratio": ("pct",),
    "tax_rule": ("ltcg_threshold_days", "ltcg_rate", "stcg_rate", "ltcg_exemption"),
    "cutoff": ("settlement_days",),
    "minimum": ("amount",),
}

EXTRACTION_TOOL = {
    "name": "record_constraints",
    "description": "Record every redemption-governing constraint found on this page.",
    "input_schema": {
        "type": "object",
        "properties": {
            "constraints": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["exit_load", "lock_in", "tax_rule", "cutoff", "minimum", "expense_ratio"]},
                        "pct": {"type": ["number", "null"], "description": "a percentage - exit load % or total expense ratio %"},
                        "window_days": {"type": ["integer", "null"], "description": "day window the rule applies for - exit load or lock-in"},
                        "ltcg_threshold_days": {"type": ["integer", "null"], "description": "days after which a gain becomes long-term (tax_rule)"},
                        "ltcg_rate": {"type": ["number", "null"], "description": "long-term capital gains tax rate as a decimal, e.g. 0.125 (tax_rule)"},
                        "stcg_rate": {"type": ["number", "null"], "description": "short-term capital gains tax rate as a decimal (tax_rule)"},
                        "ltcg_exemption": {"type": ["number", "null"], "description": "annual LTCG exemption amount in rupees (tax_rule)"},
                        "settlement_days": {"type": ["integer", "null"], "description": "days from redemption to settlement (cutoff)"},
                        "amount": {"type": ["number", "null"], "description": "minimum redemption amount in rupees (minimum)"},
                        "clause": {"type": "string", "description": "the clause number or heading this was found under"},
                        "effective_date": {"type": "string", "description": "ISO date this rule takes effect, if stated"},
                        "confidence": {"type": "string", "enum": ["verified", "inferred"]},
                    },
                    "required": ["kind", "clause", "confidence"],
                },
            }
        },
        "required": ["constraints"],
    },
}

SYSTEM_PROMPT = (
    "You extract mutual-fund redemption constraints (exit load, lock-in, tax rule, "
    "cutoff time, minimum redemption amount, total expense ratio) from one page of a "
    "Scheme Information Document or addendum. Extract only what this page states explicitly. If a rule "
    "is stated but you cannot point to the clause it comes from, do not include it — "
    "an uncited constraint is worse than a missing one. Fill in whichever numeric fields "
    "apply to this constraint's kind (e.g. pct and window_days for exit_load) directly on "
    "the constraint object - a rule with no numeric field filled in is as useless as one "
    "with no clause, so always populate at least the field(s) its kind needs. A stated "
    "nil / no-load rule is pct=0, not an omitted field. Use confidence='inferred' only "
    "when the value requires combining wording across sentences on this same page; use "
    "'verified' when the clause states the value directly."
)


def _value_is_usable(kind: str, value: dict) -> bool:
    """The numeric fields a `Constraint` of this kind needs to be usable
    downstream - `_build_leg` / the verifier read them via
    `.value.get(key, 0.0)`, which means an empty or partial value doesn't
    fail loudly, it silently reads as a 0% / nil rule. That is worse than
    no constraint at all, exactly like a missing clause, so it is rejected
    here with the same rigor."""
    required = _VALUE_FIELDS.get(kind)
    if not required:  # a kind extract_page has never heard of - let it through, harmless
        return True
    if kind in ("exit_load", "lock_in", "expense_ratio"):
        return all(value.get(k) is not None for k in required)
    return bool(value)  # tax_rule / cutoff / minimum - no fixed shape, just require *something*


class LLMClient(Protocol):
    def extract(self, system: str, page_text: str, tool: dict) -> dict:
        """Returns the tool_use input dict, e.g. {"constraints": [...]}"""
        ...


class VisionLLMClient(Protocol):
    """A client that can also read an image directly - screenshots and
    scanned pages with no text layer (`agent/screenshot.py`). Both
    concrete clients below implement this on the same object as `extract`;
    it's a separate Protocol only so a text-only fake never has to stub it."""

    def extract_image(self, system: str, image_bytes: bytes, mime_type: str, tool: dict) -> dict:
        ...


class AnthropicExtractionClient:
    """Thin wrapper so extractor.py's logic can be tested against a fake
    client without ever making a network call."""

    def __init__(self, api_key: Optional[str] = None):
        import anthropic  # imported here, not at module load, so importing
        # extractor.py never requires an API key to be present
        self._client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))

    def extract(self, system: str, page_text: str, tool: dict) -> dict:
        response = self._client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=system,
            tools=[tool],
            tool_choice={"type": "tool", "name": tool["name"]},
            messages=[{"role": "user", "content": page_text}],
        )
        for block in response.content:
            if block.type == "tool_use":
                return block.input
        return {"constraints": []}

    def extract_image(self, system: str, image_bytes: bytes, mime_type: str, tool: dict) -> dict:
        import base64

        response = self._client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=system,
            tools=[tool],
            tool_choice={"type": "tool", "name": tool["name"]},
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64", "media_type": mime_type,
                            "data": base64.b64encode(image_bytes).decode("ascii"),
                        },
                    },
                    {"type": "text", "text": "Read this image and call the tool with what you find."},
                ],
            }],
        )
        for block in response.content:
            if block.type == "tool_use":
                return block.input
        return {}


# Pages worth sending to the model - a redemption constraint almost always
# sits near one of these. Everything else is skipped to keep token spend
# proportional to the document.
_PAGE_KEYWORDS = (
    "exit load", "load structure", "lock-in", "lock in", "cut-off", "cut off",
    "cutoff", "redemption", "minimum redemption", "capital gain", "taxation",
    "stt", "stamp duty", "expense ratio", "total expense ratio", "ter ",
)


def candidate_pages(pages: list[str]) -> list[tuple[int, str]]:
    """1-indexed (page_number, text) for pages that mention a constraint
    keyword. Falls back to all pages if none match (a short addendum may
    not use the exact wording)."""
    hits = [
        (i + 1, text) for i, text in enumerate(pages)
        if any(k in text.lower() for k in _PAGE_KEYWORDS)
    ]
    return hits or [(i + 1, text) for i, text in enumerate(pages) if text.strip()]


def extract_document(
    client: LLMClient, doc_name: str, pages: list[str], scheme_id: str,
    effective_date_fallback: str, retrieved_at: str, url: Optional[str] = None,
) -> list[Constraint]:
    """Run `extract_page` over the constraint-bearing pages of one parsed
    document and concatenate the results."""
    out: list[Constraint] = []
    for page_number, page_text in candidate_pages(pages):
        out.extend(extract_page(
            client, doc_name, page_number, page_text, scheme_id,
            effective_date_fallback, retrieved_at, url,
        ))
    return out


def extract_page(
    client: LLMClient, doc_name: str, page_number: int, page_text: str,
    scheme_id: str, effective_date_fallback: str, retrieved_at: str, url: Optional[str] = None,
) -> list[Constraint]:
    """Extracts constraints from one page. Drops anything without a
    clause reference rather than passing an uncited constraint on - and,
    just as strictly, drops anything whose numeric fields the model didn't
    actually populate (see `_value_is_usable`), reassembling the flat
    per-kind fields the tool schema declares (`_VALUE_FIELDS`) into the
    same `{"pct":..., "window_days":...}`-shaped `value` dict every other
    module in the loop already expects."""
    result = client.extract(SYSTEM_PROMPT, page_text, EXTRACTION_TOOL)
    constraints: list[Constraint] = []
    for i, raw in enumerate(result.get("constraints", [])):
        if not isinstance(raw, dict):
            continue
        clause = raw.get("clause")
        if not clause:
            continue  # no clause, no constraint — never guess a citation
        kind = raw.get("kind", "")

        # Accept both the current flat schema and the older nested `value` shape.
        value = dict(raw.get("value") or {})
        for key in _VALUE_FIELDS.get(kind, ()):
            if raw.get(key) is not None:
                value[key] = raw[key]

        if not _value_is_usable(kind, value):
            continue  # the number itself never actually arrived - don't pass off a hollow rule as real

        source = Source(
            doc=doc_name, page=page_number, clause=clause, url=url,
            effective_date=raw.get("effective_date") or effective_date_fallback,
            retrieved_at=retrieved_at,
        )

        constraints.append(Constraint(
            id=f"c_{scheme_id}_{kind}_{doc_name}_{page_number}_{i}",
            scheme_id=scheme_id, kind=kind, value=value,
            source=source, confidence=raw.get("confidence", "inferred"),
        ))
    return constraints
