"""Turns uploaded documents into the loop's inputs (diagram step 0's
prerequisite - before a human or the agent can pick candidates, the loop
needs to know what funds exist and what the client actually holds).

One upload bucket, any mix of documents - PDFs and, for a portfolio
screen, images too. Each is classified first:

  * a PDF with a text layer goes through `agent/fund_identity.identify_document`
    (text-based, as before);
  * an uploaded image (PNG/JPG/WEBP), or a PDF with NO text layer at all
    (a scan, or a screenshot saved as a PDF), is read directly by a
    vision-capable model instead - `agent/fund_identity.identify_image` +
    `agent/screenshot.py`. No OCR binary is needed for this path.

Then, by what it turns out to be:

  * a fund document (SID / addendum / SAI / KIM) is resolved to a scheme
    identity and mined for constraints via `agent/extractor.py`, exactly
    as a live HUNT would (text only - a photographed clause page isn't
    supported yet, and says so rather than guessing);
  * a portfolio statement or screen is mined for holdings, each matched
    back to a resolved fund identity by name (folio kept verbatim - see
    `agent/holdings.py`). A row missing its purchase date or NAV - which
    most portfolio *screens* don't show, unlike a real CAS - is never
    silently dropped or guessed: it comes back in `incomplete_holdings`
    for a human to complete;
  * anything else is skipped with a warning, not silently dropped.

No PDF or image ever reaches the allocator directly - everything here
becomes the same `Constraint` / `Lot` shapes the rest of the loop already
trusts, so `agent/loop.py` cannot tell an upload from a scenario fixture.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from agent.amc_registry import resolve_scheme
from agent.contracts import Constraint, Holdings
from agent.evidence import now_iso
from agent.extractor import LLMClient, extract_document
from agent.fund_identity import (
    DocumentIdentity,
    ResolvedFund,
    identify_document,
    identify_image,
    resolve_fund_identity,
)
from agent.holdings import MalformedHoldingError, load_holdings
from agent.pdfparse import parse_pdf, render_page_png
from agent.screenshot import PortfolioRow, extract_portfolio_image, guess_image_mime
from agent.statement_extractor import RawLot, extract_statement


@dataclass
class IntakeFund:
    scheme_id: str
    label: str
    amc: str | None
    resolved: bool
    document: str
    document_kind: str
    isin: str | None = None
    nav: float | None = None


@dataclass
class IncompleteHolding:
    """A holding a screenshot named but didn't fully specify - most
    portfolio screens show current units/value, not the purchase date and
    NAV FIFO needs. Never guessed; surfaced for a human to complete via
    `POST /api/intake/{id}/complete`."""

    filename: str
    scheme_id: str
    label: str
    units: float
    missing: list[str]
    folio: str | None = None
    current_value: float | None = None


@dataclass
class IntakeResult:
    funds: list[IntakeFund] = field(default_factory=list)
    holdings: Holdings = field(default_factory=Holdings)
    constraints: list[Constraint] = field(default_factory=list)
    navs: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    incomplete_holdings: list[IncompleteHolding] = field(default_factory=list)


def run_intake(
    documents: list[tuple[str, bytes]],
    *,
    extraction_client: LLMClient,
    amfi_directory: dict,
) -> IntakeResult:
    result = IntakeResult()
    name_to_fund: dict[str, ResolvedFund] = {}
    pending_lots: list[tuple[str, RawLot]] = []
    pending_rows: list[tuple[str, PortfolioRow]] = []

    for filename, content in documents:
        image_mime = guess_image_mime(filename)
        if image_mime is not None:
            _ingest_image(filename, content, image_mime, extraction_client, result, pending_rows)
            continue

        try:
            pages = parse_pdf(content)
        except Exception as exc:
            result.warnings.append(f"{filename}: could not be parsed as a PDF - {exc}")
            continue

        if not any(p.strip() for p in pages):
            rendered = render_page_png(content, page=0)
            if rendered is not None:
                _ingest_image(filename, rendered, "image/png", extraction_client, result, pending_rows)
                continue
            result.warnings.append(f"{filename}: no extractable text (a scan with no OCR text found)")
            continue

        try:
            identity = identify_document(extraction_client, pages)
        except Exception as exc:
            result.warnings.append(f"{filename}: could not classify this document - {exc}")
            continue

        if identity.doc_type == "fund_document":
            _ingest_fund_document(
                filename, pages, identity, extraction_client, amfi_directory, result, name_to_fund,
            )
        elif identity.doc_type == "statement":
            pending_lots.extend((filename, lot) for lot in extract_statement(extraction_client, pages))
        else:
            result.warnings.append(
                f"{filename}: not recognized as a fund document or a portfolio statement - skipped"
            )

    _resolve_lots(pending_lots, amfi_directory, result, name_to_fund)
    _resolve_portfolio_rows(pending_rows, amfi_directory, result, name_to_fund)
    return result


def _ingest_fund_document(
    filename: str,
    pages: list[str],
    identity: DocumentIdentity,
    client: LLMClient,
    amfi_directory: dict,
    result: IntakeResult,
    name_to_fund: dict[str, ResolvedFund],
) -> None:
    try:
        resolved = resolve_fund_identity(identity, amfi_directory)
    except ValueError as exc:
        result.warnings.append(f"{filename}: {exc}")
        return

    name_to_fund[identity.scheme_name.lower()] = resolved
    if not resolved.resolved:
        result.warnings.append(
            f"{filename}: {identity.scheme_name!r} was not found in AMFI's scheme master - "
            f"using it as-is, unresolved"
        )

    constraints = extract_document(
        client, filename, pages, resolved.scheme_id,
        effective_date_fallback=date.today().isoformat(), retrieved_at=now_iso(),
    )
    result.constraints.extend(constraints)
    if resolved.nav is not None:
        result.navs[resolved.scheme_id] = resolved.nav

    result.funds.append(IntakeFund(
        scheme_id=resolved.scheme_id, label=resolved.label, amc=resolved.amc,
        resolved=resolved.resolved, document=filename, document_kind=identity.document_kind,
        isin=resolved.isin, nav=resolved.nav,
    ))


def _ingest_image(
    filename: str,
    image_bytes: bytes,
    mime_type: str,
    client: LLMClient,
    result: IntakeResult,
    pending_rows: list[tuple[str, PortfolioRow]],
) -> None:
    """The vision path: no text layer to work with, so classification and
    extraction both go straight to the model as an image. A photographed
    scheme document is recognized and declined rather than mis-read as a
    portfolio, or silently ignored."""
    try:
        identity = identify_image(client, image_bytes, mime_type)
    except Exception as exc:
        result.warnings.append(f"{filename}: could not classify this image - {exc}")
        return

    if identity.doc_type == "fund_document":
        result.warnings.append(
            f"{filename}: this looks like a scheme document, not a portfolio screen - reading a "
            f"scheme's clauses from an image isn't supported yet; upload the actual PDF instead"
        )
        return
    if identity.doc_type != "statement":
        result.warnings.append(f"{filename}: not recognized as a portfolio screen - skipped")
        return

    try:
        rows = extract_portfolio_image(client, image_bytes, mime_type)
    except Exception as exc:
        result.warnings.append(f"{filename}: could not read holdings from this image - {exc}")
        return
    if not rows:
        result.warnings.append(f"{filename}: no holdings found in this image")
        return
    pending_rows.extend((filename, row) for row in rows)


def _lookup_or_resolve_fund(
    name: str,
    amfi_directory: dict,
    name_to_fund: dict[str, ResolvedFund],
    filename: str,
    result: IntakeResult,
    document_kind: str,
) -> ResolvedFund | None:
    """Shared by the text-statement and portfolio-image paths: a holding
    can arrive before (or without) its scheme's own document, so this
    checks what's already registered before falling back to an AMFI
    lookup - never a guess, `None` when nothing matches."""
    resolved = name_to_fund.get(name.lower())
    if resolved is not None:
        return resolved
    match = resolve_scheme(name, amfi_directory)
    if match is None:
        return None
    resolved = ResolvedFund(
        scheme_id=match["scheme_code"], label=match["name"], amc=match["amc"],
        isin=match.get("isin"), nav=match.get("nav"), resolved=True,
    )
    name_to_fund[name.lower()] = resolved
    result.funds.append(IntakeFund(
        scheme_id=resolved.scheme_id, label=resolved.label, amc=resolved.amc,
        resolved=True, document=filename, document_kind=document_kind,
        isin=resolved.isin, nav=resolved.nav,
    ))
    if resolved.nav is not None:
        result.navs.setdefault(resolved.scheme_id, resolved.nav)
    return resolved


def _resolve_lots(
    pending_lots: list[tuple[str, RawLot]],
    amfi_directory: dict,
    result: IntakeResult,
    name_to_fund: dict[str, ResolvedFund],
) -> None:
    """A statement can arrive before (or without) its scheme's own
    document, so lots are matched in a second pass, once every uploaded
    fund document has already registered its identity."""
    lot_seq = 0
    for filename, raw in pending_lots:
        resolved = _lookup_or_resolve_fund(
            raw.scheme_name, amfi_directory, name_to_fund, filename, result, "statement_only",
        )
        if resolved is None:
            result.warnings.append(
                f"{filename}: holding in {raw.scheme_name!r} has no matching fund document "
                f"and no AMFI match - excluded from any plan"
            )
            continue

        lot_seq += 1
        try:
            validated = load_holdings([{
                "lot_id": f"lot_{lot_seq}", "scheme_id": resolved.scheme_id, "folio": raw.folio,
                "units": raw.units, "purchase_date": raw.purchase_date,
                "purchase_nav": raw.purchase_nav,
            }])
        except MalformedHoldingError as exc:
            result.warnings.append(f"{filename}: skipped a lot for {raw.scheme_name!r} - {exc}")
            continue
        result.holdings.lots.extend(validated.lots)


def _resolve_portfolio_rows(
    pending_rows: list[tuple[str, PortfolioRow]],
    amfi_directory: dict,
    result: IntakeResult,
    name_to_fund: dict[str, ResolvedFund],
) -> None:
    """Same identity resolution as `_resolve_lots`, but a row missing its
    purchase date or NAV - the common case for a portfolio *screen*,
    unlike a CAS - goes to `incomplete_holdings` instead of becoming a lot
    or being dropped. `POST /api/intake/{id}/complete` fills these in."""
    lot_seq = 0
    for filename, row in pending_rows:
        resolved = _lookup_or_resolve_fund(
            row.scheme_name, amfi_directory, name_to_fund, filename, result, "screenshot",
        )
        if resolved is None:
            result.warnings.append(
                f"{filename}: holding in {row.scheme_name!r} has no matching fund document "
                f"and no AMFI match - excluded from any plan"
            )
            continue

        if not row.is_complete():
            result.incomplete_holdings.append(IncompleteHolding(
                filename=filename, scheme_id=resolved.scheme_id, label=resolved.label,
                units=row.units, missing=row.missing_fields(),
                folio=row.folio, current_value=row.current_value,
            ))
            continue

        lot_seq += 1
        try:
            validated = load_holdings([{
                "lot_id": f"shot_{lot_seq}", "scheme_id": resolved.scheme_id, "folio": row.folio,
                "units": row.units, "purchase_date": row.purchase_date,
                "purchase_nav": row.purchase_nav,
            }])
        except MalformedHoldingError as exc:
            result.warnings.append(f"{filename}: skipped a holding for {row.scheme_name!r} - {exc}")
            continue
        result.holdings.lots.extend(validated.lots)
