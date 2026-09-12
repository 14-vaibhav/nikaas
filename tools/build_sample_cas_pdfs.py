"""Generates realistic-looking sample CAS PDFs to upload through the web
UI's "Upload my documents" tab (POST /api/intake) - separate from the
folder-mode scenario fixtures under data/scenarios/, which don't need a PDF
at all.

Three files, one synthetic investor split across two registrars, so
uploading the first two together is a genuine multi-RTA case
(`agent/reconcile.py`'s territory):

  cas_cams_demo_investor.pdf       CAMS-style consolidated statement,
                                    folio format "1234567 / 89"
  cas_kfintech_demo_investor.pdf   KFintech-style, folio format is a bare
                                    number; spells "Axis Bluechip Fund"
                                    differently and holds it under a
                                    SECOND folio - the same scheme, two
                                    RTAs, two independent FIFO queues
  cas_scanned_lowquality.pdf       the same CAMS content rendered as an
                                    image with NO text layer - exercises
                                    the OCR-fallback / graceful-degrade
                                    path (PRD §12 case 1). Needs the
                                    `tesseract` binary to actually OCR;
                                    without it, intake correctly warns
                                    "no extractable text" and skips it -
                                    that warning path is itself the demo.

Every row is fictional. No real investor, folio, or PAN. A "Redemption"
row is included in each statement on purpose: `statement_extractor.py`
must skip it (FIFO needs a lot's original purchase date and NAV, not a
running balance or a sale), which is worth showing.

Run:  python tools/build_sample_cas_pdfs.py
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "samples"

INVESTOR_NOTE = (
    "Investor: DEMO INVESTOR (synthetic)   PAN: XXXXX0000X (masked - not a real PAN)\n"
    "This is a generated sample for the Nikaas demo. No real investor, folio, or "
    "holding. Do not use for anything but testing document intake."
)

# (scheme, amc, folio, nav, [(date, kind, units, price)])
CAMS_ROWS = [
    ("Axis Bluechip Fund (Direct Plan - Growth Option)", "Axis Mutual Fund", "11002233 / 45", 62.35, [
        ("2022-01-15", "Purchase", 30000.000, 45.0000),
        ("2024-03-10", "SIP Installment", 500.000, 58.2000),
        ("2025-11-05", "Redemption", -5000.000, 61.5000),
    ]),
    ("HDFC Top 100 Fund (Direct Plan - Growth Option)", "HDFC Mutual Fund", "20447781 / 12", 1148.90, [
        ("2021-06-20", "Purchase", 2000.000, 620.0000),
        ("2023-09-01", "SIP Installment", 50.000, 850.0000),
    ]),
    ("Quant ELSS Tax Saver Fund (Direct Plan - Growth Option)", "Quant Mutual Fund", "17880021 / 55", 412.80, [
        ("2022-01-10", "Purchase", 1800.000, 250.3000),
        ("2023-02-14", "SIP Installment", 40.000, 300.1000),
    ]),
]

# Note: this deliberately spells "Axis Bluechip Fund" differently from the
# CAMS statement above, and files it under a second, KFintech-style folio -
# the point of uploading both together.
KFIN_ROWS = [
    ("AXIS BLUECHIP FUND - DIRECT PLAN - GROWTH", "Axis Mutual Fund", "91850027431", 62.35, [
        ("2023-08-05", "Purchase", 4000.000, 52.7500),
        ("2026-02-10", "Switch In", 600.000, 61.1000),
    ]),
    ("SBI Small Cap Fund - Direct Plan - Growth", "SBI Mutual Fund", "13559002001", 212.40, [
        ("2021-07-19", "Purchase", 1200.000, 95.3000),
        ("2026-03-12", "Purchase", 900.000, 178.4000),
    ]),
    ("Mirae Asset Large Cap Fund - Direct Plan - Growth", "Mirae Asset Mutual Fund", "90114400271", 135.70, [
        ("2021-12-02", "Purchase", 900.000, 84.2000),
        ("2025-04-18", "Dividend Reinvestment", 30.000, 118.4500),
        ("2026-06-01", "Redemption", -200.000, 132.0000),
    ]),
]


def _amount(units: float, price: float) -> float:
    return units * price


def _write_statement(pdf, title: str, rta_name: str, rows) -> None:
    from fpdf import FPDF  # noqa: F401  (imported by caller; kept local for clarity)

    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.multi_cell(0, 7, title, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 8)
    pdf.multi_cell(0, 4, INVESTOR_NOTE, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5, f"Statement period: 01-Apr-2020 to 10-Sep-2026   |   Registrar: {rta_name}",
              new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    for scheme, amc, folio, nav, txns in rows:
        pdf.set_font("Helvetica", "B", 10)
        pdf.multi_cell(0, 5, f"{scheme}", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(0, 4, f"AMC: {amc}     Folio No: {folio}     NAV as on 10-Sep-2026: {nav:.4f}",
                  new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Courier", "B", 8)
        pdf.cell(0, 4, "  Date         Transaction Type       Units          Price          Amount",
                  new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Courier", "", 8)
        balance = 0.0
        for txn_date, kind, units, price in txns:
            balance += units
            amt = _amount(units, price)
            pdf.cell(
                0, 4,
                f"  {txn_date}   {kind:<20} {units:>12,.3f}   {price:>10,.4f}   {amt:>14,.2f}",
                new_x="LMARGIN", new_y="NEXT",
            )
        pdf.set_font("Courier", "B", 8)
        pdf.cell(0, 4, f"  {'':<13}{'Closing Balance':<20} {balance:>12,.3f}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)


def build_cams_pdf() -> Path:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    _write_statement(
        pdf, "Consolidated Account Statement (SYNTHETIC - not a real statement)",
        "CAMS", CAMS_ROWS,
    )
    path = OUT / "cas_cams_demo_investor.pdf"
    pdf.output(str(path))
    return path


def build_kfintech_pdf() -> Path:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    _write_statement(
        pdf, "Consolidated Account Statement (SYNTHETIC - not a real statement)",
        "KFintech", KFIN_ROWS,
    )
    path = OUT / "cas_kfintech_demo_investor.pdf"
    pdf.output(str(path))
    return path


def build_scanned_pdf() -> Path:
    """Same CAMS content, rendered to an image and dropped onto a page with
    NO text layer - `pdfplumber` finds nothing, so `parse_pdf` falls back to
    OCR. Without the `tesseract` binary installed, that returns [] too, and
    `run_intake` correctly warns "no extractable text" instead of guessing -
    itself the point of including this file."""
    from fpdf import FPDF
    from PIL import Image, ImageDraw, ImageFont

    W, H = 1240, 1754  # ~150dpi A4
    img = Image.new("L", (W, H), color=245)  # a slightly grey "scan" background
    draw = ImageFont.load_default()
    d = ImageDraw.Draw(img)
    y = 60
    lines = [
        "CONSOLIDATED ACCOUNT STATEMENT (SYNTHETIC - not a real statement)",
        "", *INVESTOR_NOTE.splitlines(), "",
        "Statement period: 01-Apr-2020 to 10-Sep-2026   Registrar: CAMS", "",
    ]
    for scheme, amc, folio, nav, txns in CAMS_ROWS:
        lines.append(f"{scheme}")
        lines.append(f"AMC: {amc}   Folio No: {folio}   NAV: {nav:.4f}")
        for txn_date, kind, units, price in txns:
            lines.append(f"  {txn_date}  {kind:<20} {units:>12,.3f} @ {price:>10,.4f}")
        lines.append("")
    for line in lines:
        d.text((50, y), line, fill=20, font=draw)
        y += 26

    tmp_png = OUT / "_scan_render_tmp.png"
    OUT.mkdir(parents=True, exist_ok=True)
    img.save(tmp_png)

    pdf = FPDF(unit="pt", format=(W, H))
    pdf.add_page()
    pdf.image(str(tmp_png), x=0, y=0, w=W, h=H)  # image only - no text layer
    path = OUT / "cas_scanned_lowquality.pdf"
    pdf.output(str(path))
    tmp_png.unlink(missing_ok=True)
    return path


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for path in (build_cams_pdf(), build_kfintech_pdf(), build_scanned_pdf()):
        print(f"wrote {path}  ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
