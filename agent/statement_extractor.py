"""Extracts purchase lots from an uploaded portfolio/account statement, so
holdings no longer have to be typed in by hand (diagram: the human's
portfolio going into the loop, now sourced from a document instead of a
form). Page-by-page, same discipline as `agent/extractor.py`: only a row
with a folio, a date and a NAV becomes a lot - a running balance or a
redemption is not a lot FIFO can sell from, and a row missing a field is
dropped rather than guessed.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.extractor import LLMClient

HOLDINGS_TOOL = {
    "name": "record_purchase_lots",
    "description": (
        "Record every purchase / SIP / switch-in lot found on this page of a "
        "mutual fund account or portfolio statement."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "lots": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "scheme_name": {"type": "string", "description": "exactly as printed"},
                        "folio": {"type": "string"},
                        "units": {"type": "number"},
                        "purchase_date": {"type": "string", "description": "ISO date (YYYY-MM-DD)"},
                        "purchase_nav": {"type": "number"},
                    },
                    "required": ["scheme_name", "folio", "units", "purchase_date", "purchase_nav"],
                },
            }
        },
        "required": ["lots"],
    },
}

SYSTEM_PROMPT = (
    "You extract mutual fund purchase lots from one page of an account/portfolio "
    "statement: scheme name, folio number, units, purchase date, purchase NAV. Only "
    "extract rows that are a purchase, an SIP instalment, or a switch-in - never a "
    "redemption, a switch-out, or a running/closing-balance line, since FIFO needs "
    "each lot's own original purchase date and NAV, not a summary. If a row is "
    "missing units, a date, or a NAV, skip it rather than guessing the missing field."
)


@dataclass
class RawLot:
    scheme_name: str
    folio: str
    units: float
    purchase_date: str
    purchase_nav: float


def extract_statement_page(client: LLMClient, page_text: str) -> list[RawLot]:
    result = client.extract(SYSTEM_PROMPT, page_text, HOLDINGS_TOOL)
    out: list[RawLot] = []
    for raw in result.get("lots", []):
        try:
            out.append(RawLot(
                scheme_name=str(raw["scheme_name"]).strip(),
                folio=str(raw["folio"]).strip(),
                units=float(raw["units"]),
                purchase_date=str(raw["purchase_date"]),
                purchase_nav=float(raw["purchase_nav"]),
            ))
        except (KeyError, TypeError, ValueError):
            continue  # an incomplete row is worse than a missing one
    return out


def extract_statement(client: LLMClient, pages: list[str]) -> list[RawLot]:
    out: list[RawLot] = []
    for page_text in pages:
        if page_text.strip():
            out.extend(extract_statement_page(client, page_text))
    return out
