"""PDF -> page text, shared by whatever fetched the bytes (`hunter.py`'s
WebHunter from the open web, `intake.py` from an upload). One place for
the pdfplumber / OCR-fallback logic so the two never drift apart.
"""

from __future__ import annotations

from io import BytesIO
from typing import Optional


def pdf_pages(content: bytes) -> list[str]:
    import pdfplumber

    with pdfplumber.open(BytesIO(content)) as pdf:
        return [(page.extract_text() or "") for page in pdf.pages]


def ocr_pages(content: bytes) -> list[str]:
    """Best-effort OCR fallback for a scanned page. Missing Python packages
    (caught at import) and a missing `tesseract` binary (caught at the
    actual OCR call - pytesseract raises `TesseractNotFoundError`, a plain
    `OSError`, only once it tries to run it) both degrade to "no text found"
    rather than crashing the caller - §12 case 1 is "OCR path, or abstain
    with reason", never a stack trace."""
    try:
        import pypdfium2 as pdfium
        import pytesseract
        from PIL import Image
    except Exception:
        return []
    out: list[str] = []
    try:
        pdf = pdfium.PdfDocument(content)
        for i in range(len(pdf)):
            bitmap = pdf[i].render(scale=2.0)
            image: Image.Image = bitmap.to_pil()
            out.append(pytesseract.image_to_string(image))
    except Exception:
        return []
    return out


def parse_pdf(content: bytes) -> list[str]:
    """Text first; only pay for OCR when the PDF has none (a scan)."""
    pages = pdf_pages(content)
    if not any(p.strip() for p in pages):
        pages = ocr_pages(content)
    return pages


def render_page_png(content: bytes, page: int = 0, scale: float = 2.0) -> Optional[bytes]:
    """Renders one page of a PDF to PNG bytes - used when there is no text
    layer at all (a scanned page or a screenshot saved as a PDF) and the
    fallback is vision extraction (`agent/screenshot.py`) rather than OCR.
    Returns None rather than raising when the PDF can't be opened/rendered
    (e.g. it's genuinely corrupt) - the caller degrades to a warning."""
    try:
        import pypdfium2 as pdfium
    except Exception:
        return None
    try:
        from io import BytesIO as _BytesIO

        pdf = pdfium.PdfDocument(content)
        if page >= len(pdf):
            return None
        bitmap = pdf[page].render(scale=scale)
        buf = _BytesIO()
        bitmap.to_pil().save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None
