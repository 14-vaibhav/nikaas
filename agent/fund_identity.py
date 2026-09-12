"""Identifies an uploaded document before anything else can happen to it
(diagram step 0's prerequisite: a human or the agent can only pick
candidates from funds the loop already knows exist).

One LLM call per document decides two things at once: what kind of
document this is (a scheme-governing document vs. a portfolio statement
vs. neither), and - if it governs one scheme - which scheme, read exactly
as printed. `agent/intake.py` then resolves that name against AMFI's
scheme master via `agent/amc_registry.resolve_scheme` (never a guess: an
unresolved name still gets a stable id, just flagged `resolved=False`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from agent.amc_registry import resolve_scheme
from agent.extractor import LLMClient

IDENTITY_TOOL = {
    "name": "record_document_identity",
    "description": (
        "Classify this document and, if it governs one mutual fund scheme, "
        "record which one."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "doc_type": {
                "type": "string",
                "enum": ["fund_document", "statement", "other"],
                "description": (
                    "fund_document = a Scheme Information Document, addendum, "
                    "Statement of Additional Information or Key Information "
                    "Memorandum governing one scheme's rules; statement = an "
                    "account/portfolio statement listing folios, units or "
                    "transactions; other = neither"
                ),
            },
            "scheme_name": {
                "type": ["string", "null"],
                "description": "the scheme name exactly as printed, only if doc_type is fund_document",
            },
            "amc_name": {"type": ["string", "null"], "description": "the AMC / trust name, if stated"},
            "document_kind": {"type": "string", "enum": ["sid", "addendum", "sai", "kim", "other"]},
        },
        "required": ["doc_type", "document_kind"],
    },
}

SYSTEM_PROMPT = (
    "You look at the opening pages of one uploaded PDF and classify it: is it a "
    "mutual-fund scheme document (Scheme Information Document, addendum, Statement "
    "of Additional Information, Key Information Memorandum - describes one scheme's "
    "rules), a portfolio / account statement (lists a holder's folios, units or "
    "transactions), or neither? If it is a scheme document, also record the scheme "
    "name exactly as printed - do not normalize, translate, or guess at a name that "
    "isn't there. If more than one scheme is named, pick the one the document is "
    "primarily about (its own heading), not one it merely cross-references."
)


@dataclass
class DocumentIdentity:
    doc_type: str  # "fund_document" | "statement" | "other"
    document_kind: str
    scheme_name: str = ""
    amc_name: Optional[str] = None


@dataclass
class ResolvedFund:
    scheme_id: str
    label: str
    amc: Optional[str]
    resolved: bool
    isin: Optional[str] = None
    nav: Optional[float] = None


def identify_document(client: LLMClient, pages: list[str]) -> DocumentIdentity:
    """Looks at the first couple of pages - a scheme name (or a statement's
    account/folio header) is almost always on the cover, never buried
    deep in a schedule."""
    text = "\n\n".join(p for p in pages[:2] if p.strip())
    result = client.extract(SYSTEM_PROMPT, text, IDENTITY_TOOL)
    return DocumentIdentity(
        doc_type=result.get("doc_type", "other"),
        document_kind=result.get("document_kind", "other"),
        scheme_name=(result.get("scheme_name") or "").strip(),
        amc_name=(result.get("amc_name") or None),
    )


def identify_image(client, image_bytes: bytes, mime_type: str) -> DocumentIdentity:
    """Same classification, for a page with no text layer at all (a scan
    or a screenshot saved as a PDF/PNG/JPG) - `client` must implement
    `VisionLLMClient.extract_image`."""
    result = client.extract_image(SYSTEM_PROMPT, image_bytes, mime_type, IDENTITY_TOOL)
    return DocumentIdentity(
        doc_type=result.get("doc_type", "other"),
        document_kind=result.get("document_kind", "other"),
        scheme_name=(result.get("scheme_name") or "").strip(),
        amc_name=(result.get("amc_name") or None),
    )


def resolve_fund_identity(identity: DocumentIdentity, amfi_directory: dict) -> ResolvedFund:
    """AMFI first; a name with no confident match still gets a stable,
    citable id rather than being dropped - never force-match a fund's
    economics onto the wrong scheme."""
    if not identity.scheme_name:
        raise ValueError("document identified no scheme name")
    match = resolve_scheme(identity.scheme_name, amfi_directory)
    if match:
        return ResolvedFund(
            scheme_id=match["scheme_code"], label=match["name"], amc=match["amc"],
            isin=match.get("isin"), nav=match.get("nav"), resolved=True,
        )
    return ResolvedFund(
        scheme_id=f"unresolved_{_slug(identity.scheme_name)}", label=identity.scheme_name,
        amc=identity.amc_name, resolved=False,
    )


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
