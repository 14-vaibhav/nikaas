"""Screenshot / image intake (PRD §5.11 roadmap item, promoted: "holdings
screenshot read via vision").

A portfolio screen (a broker app, a fund house dashboard, a photo of a
paper statement) is read directly by a vision-capable model - no text
layer, no OCR binary required. The same tool call also backs the
"scanned page with no text" fallback for a PDF (`agent/intake.py`): once
`pdfplumber` finds nothing, the page is rendered to an image
(`agent/pdfparse.render_page_png`) and read the same way.

The one rule that matters more here than anywhere else in the repo: most
portfolio screens show *current* units and *current* value, not the
*purchase* date and NAV FIFO actually needs. A row missing either is
never dropped silently and never backfilled with a guess - it comes back
as an `incomplete` row for a human to complete (or the investor uploads
the real CAS instead, which has these by construction).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

PORTFOLIO_ROW_TOOL = {
    "name": "record_portfolio_rows",
    "description": (
        "Record every mutual fund holding row visible in this image of a portfolio "
        "or holdings screen, exactly as shown - never compute or infer a field that "
        "isn't visible."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "scheme_name": {"type": "string", "description": "exactly as printed/shown"},
                        "folio": {"type": ["string", "null"], "description": "folio number, only if shown"},
                        "units": {"type": "number"},
                        "purchase_date": {
                            "type": ["string", "null"],
                            "description": "ISO date (YYYY-MM-DD) of the original purchase, ONLY if this "
                                           "screen shows it - null if only a current holding is shown",
                        },
                        "purchase_nav": {
                            "type": ["number", "null"],
                            "description": "the NAV at purchase, ONLY if shown - null if not visible",
                        },
                        "current_value": {
                            "type": ["number", "null"],
                            "description": "current market value shown for this holding, if any",
                        },
                    },
                    "required": ["scheme_name", "units"],
                },
            }
        },
        "required": ["rows"],
    },
}

SYSTEM_PROMPT = (
    "You read one screenshot or photo of a mutual fund portfolio / holdings screen. "
    "Record one row per holding: the scheme name exactly as shown, the folio number "
    "only if the screen shows one, units held, and - only if the screen actually "
    "displays them - the original purchase date and purchase NAV. Most portfolio "
    "screens show current units and current value, not purchase history: if a field "
    "is not visible, leave it null rather than computing, estimating, or copying the "
    "current NAV into it. Skip rows that are not a current holding (a pending order, "
    "a closed/zero position, a watchlist item)."
)


@dataclass
class PortfolioRow:
    scheme_name: str
    units: float
    folio: Optional[str] = None
    purchase_date: Optional[str] = None
    purchase_nav: Optional[float] = None
    current_value: Optional[float] = None

    def is_complete(self) -> bool:
        """Everything a `Lot` needs to FIFO correctly. Missing any of
        these means a human has to fill it in - never guessed."""
        return bool(self.folio) and self.purchase_date is not None and self.purchase_nav is not None

    def missing_fields(self) -> list[str]:
        missing = []
        if not self.folio:
            missing.append("folio")
        if self.purchase_date is None:
            missing.append("purchase_date")
        if self.purchase_nav is None:
            missing.append("purchase_nav")
        return missing


def extract_portfolio_image(client, image_bytes: bytes, mime_type: str) -> list[PortfolioRow]:
    """`client` must implement `VisionLLMClient.extract_image`
    (`AnthropicExtractionClient` / `GeminiExtractionClient` both do)."""
    result = client.extract_image(SYSTEM_PROMPT, image_bytes, mime_type, PORTFOLIO_ROW_TOOL)
    out: list[PortfolioRow] = []
    for raw in result.get("rows", []):
        try:
            name = str(raw["scheme_name"]).strip()
            units = float(raw["units"])
        except (KeyError, TypeError, ValueError):
            continue  # can't even place this row - worse than dropping it
        if not name or units <= 0:
            continue
        out.append(PortfolioRow(
            scheme_name=name,
            units=units,
            folio=(str(raw["folio"]).strip() if raw.get("folio") else None),
            purchase_date=(str(raw["purchase_date"]) if raw.get("purchase_date") else None),
            purchase_nav=(float(raw["purchase_nav"]) if raw.get("purchase_nav") is not None else None),
            current_value=(float(raw["current_value"]) if raw.get("current_value") is not None else None),
        ))
    return out


MIME_BY_EXT = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif",
}


def guess_image_mime(filename: str) -> Optional[str]:
    lower = filename.lower()
    for ext, mime in MIME_BY_EXT.items():
        if lower.endswith(ext):
            return mime
    return None
