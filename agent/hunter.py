"""The Hunter - name to governing clause (diagram: 'HUNT the open web',
name -> AMC -> SID -> addenda -> clause).

Two implementations behind one interface:

  * FolderHunter - serves constraints from an on-disk fixture, re-stamping
    `retrieved_at` on every call so a REFETCH genuinely refreshes. Lets
    the whole loop run offline and deterministically (tests, demo).
  * WebHunter - the real thing. Resolves the AMC against the AMFI
    directory, finds SID / addendum PDFs via a per-AMC adapter (or a
    generic scan), fetches them politely (robots.txt, delay, honest UA),
    parses with pdfplumber (OCR fallback for scans), and runs the
    extractor. A dead link is caught, not crashed on (robustness 4/5).

Live use needs: outbound network, ANTHROPIC_API_KEY (extraction), and -
only for scanned SIDs - the `tesseract` binary.
"""

from __future__ import annotations

import difflib
import hashlib
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Optional, Protocol
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

from agent.amc_registry import adapter_for, amc_id_for, load_amfi_directory
from agent.contracts import Constraint
from agent.evidence import now_iso
from agent.extractor import AnthropicExtractionClient, extract_document
from agent.pdfparse import ocr_pages as _ocr_pages
from agent.pdfparse import pdf_pages as _pdf_pages

USER_AGENT = "nikaas-agent/1.0 (+https://example.invalid/nikaas; research; contact ops@example.invalid)"


@dataclass
class HuntOutcome:
    constraints: list[Constraint] = field(default_factory=list)
    content_hash: str = ""
    ok: bool = False
    detail: str = ""
    tokens_spent: int = 0


class Hunter(Protocol):
    def hunt(
        self, fund_id: str, fund_name: str, kinds: list[str], *, force: bool = False
    ) -> HuntOutcome: ...


def resolve_amc(
    fund_name: str, amc_directory: dict[str, str], threshold: float = 0.6
) -> Optional[str]:
    """Fuzzy-resolve a (possibly inconsistently formatted) fund name to an
    AMC id. Returns None - never a guess - when nothing clears the
    threshold; the loop treats that as an abstain."""
    if not amc_directory:
        return None
    match = difflib.get_close_matches(fund_name.lower(), list(amc_directory), n=1, cutoff=threshold)
    return amc_directory[match[0]] if match else None


# --- FolderHunter --------------------------------------------------------


class FolderHunter:
    def __init__(self, constraints: list[Constraint]):
        self._pool = list(constraints)

    def hunt(
        self, fund_id: str, fund_name: str, kinds: list[str], *, force: bool = False
    ) -> HuntOutcome:
        from dataclasses import replace

        found = [c for c in self._pool if c.scheme_id == fund_id and c.kind in kinds]
        if not found:
            return HuntOutcome(ok=False, detail=f"no fixture constraint for {fund_id} / {kinds}")
        stamped = [
            replace(c, source=replace(c.source, retrieved_at=now_iso())) for c in found
        ]
        digest = hashlib.sha256("|".join(sorted(c.id for c in found)).encode()).hexdigest()
        return HuntOutcome(
            constraints=stamped, content_hash=digest, ok=True,
            detail=f"folder: {len(stamped)} constraint(s)",
        )


# --- WebHunter ----------------------------------------------------------


class WebHunter:
    def __init__(
        self,
        *,
        directory: dict[str, dict] | None = None,
        extraction_client=None,
        http=None,
        delay_seconds: float = 1.5,
        max_docs: int = 4,
        obey_robots: bool = True,
    ):
        self._dir = directory if directory is not None else load_amfi_directory()
        self._extractor = extraction_client or AnthropicExtractionClient()
        self._delay = delay_seconds
        self._max_docs = max_docs
        self._obey_robots = obey_robots
        self._robots: dict[str, RobotFileParser] = {}
        if http is not None:
            self._http = http
            self._owns_http = False
        else:
            import httpx

            self._http = httpx.Client(
                timeout=30, follow_redirects=True, headers={"User-Agent": USER_AGENT}
            )
            self._owns_http = True

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    # -- interface --

    def hunt(
        self, fund_id: str, fund_name: str, kinds: list[str], *, force: bool = False
    ) -> HuntOutcome:
        amc_id = self._resolve_amc(fund_name)
        if amc_id is None:
            return HuntOutcome(ok=False, detail=f"could not resolve an AMC for {fund_name!r}")

        doc_urls = self._discover_documents(amc_id, fund_name)
        if not doc_urls:
            return HuntOutcome(ok=False, detail=f"no SID/addendum links found for {fund_name!r}")

        constraints: list[Constraint] = []
        hashes: list[str] = []
        for url in doc_urls[: self._max_docs]:
            try:
                pages, doc_hash = self.fetch_and_parse(url)
            except Exception as exc:  # a dead / slow / 500 link is not fatal
                continue
            if not any(p.strip() for p in pages):
                continue
            hashes.append(doc_hash)
            got = extract_document(
                self._extractor, _doc_name(url), pages, fund_id,
                effective_date_fallback=date.today().isoformat(),
                retrieved_at=now_iso(), url=url,
            )
            constraints.extend(c for c in got if c.kind in kinds)

        if not constraints:
            return HuntOutcome(
                ok=False,
                detail=f"{len(doc_urls)} document(s) reachable but no {kinds} constraint extracted",
            )
        return HuntOutcome(
            constraints=constraints,
            content_hash=hashlib.sha256("|".join(sorted(hashes)).encode()).hexdigest(),
            ok=True,
            detail=f"{len(hashes)} document(s), {len(constraints)} constraint(s)",
        )

    # -- steps --

    def _resolve_amc(self, fund_name: str) -> Optional[str]:
        entry = self._dir.get(fund_name.lower())
        if entry:
            return entry["amc_id"]
        by_name = {name: meta["amc_id"] for name, meta in self._dir.items()}
        hit = resolve_amc(fund_name, by_name)
        if hit:
            return hit
        # last resort: only if the fund name itself carries a known AMC word
        from agent.amc_registry import _AMC_ID_HINTS

        low = fund_name.lower()
        if any(h in low for h in _AMC_ID_HINTS):
            return amc_id_for(fund_name)
        return None

    def _discover_documents(self, amc_id: str, fund_name: str) -> list[str]:
        adapter = adapter_for(amc_id)
        index_urls = adapter.disclosures_urls if adapter else []
        sid_hints = adapter.sid_hints if adapter else ("scheme information document", "sid")
        add_hints = adapter.addendum_hints if adapter else ("addendum",)

        found: list[str] = []
        for index_url in index_urls:
            try:
                html = self._get_text(index_url)
            except Exception:
                continue
            found.extend(_pdf_links(html, index_url, fund_name, sid_hints + add_hints))
        # SIDs before addenda so the resolver sees the base rule first.
        return _dedupe(found)

    def fetch_and_parse(self, url: str) -> tuple[list[str], str]:
        if self._obey_robots and not self._robots_allows(url):
            raise PermissionError(f"robots.txt disallows {url}")
        time.sleep(self._delay)
        resp = self._http.get(url)
        resp.raise_for_status()
        content = resp.content
        digest = hashlib.sha256(content).hexdigest()

        ctype = resp.headers.get("content-type", "").lower()
        if "pdf" in ctype or urlparse(url).path.lower().endswith(".pdf"):
            pages = _pdf_pages(content)
            if not any(p.strip() for p in pages):
                pages = _ocr_pages(content)
            return pages, digest
        return [_html_to_text(content)], digest

    def _get_text(self, url: str) -> str:
        if self._obey_robots and not self._robots_allows(url):
            raise PermissionError(f"robots.txt disallows {url}")
        time.sleep(self._delay)
        resp = self._http.get(url)
        resp.raise_for_status()
        return resp.text

    def _robots_allows(self, url: str) -> bool:
        parts = urlparse(url)
        base = f"{parts.scheme}://{parts.netloc}"
        rp = self._robots.get(base)
        if rp is None:
            rp = RobotFileParser()
            rp.set_url(urljoin(base, "/robots.txt"))
            try:
                rp.read()
            except Exception:
                rp = None
            self._robots[base] = rp
        return True if rp is None else rp.can_fetch(USER_AGENT, url)


# --- parsing helpers --------------------------------------------------


def _html_to_text(content: bytes) -> str:
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(content, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        return soup.get_text(" ", strip=True)
    except Exception:
        return content.decode("utf-8", "ignore")


def _pdf_links(html: str, base_url: str, fund_name: str, hints: tuple[str, ...]) -> list[str]:
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return []
    soup = BeautifulSoup(html, "html.parser")
    fund_tokens = {t for t in fund_name.lower().split() if len(t) > 3}
    out: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        label = (a.get_text(" ", strip=True) + " " + href).lower()
        if not href.lower().endswith(".pdf") and "pdf" not in label:
            continue
        if not any(h in label for h in hints):
            continue
        if fund_tokens and not (fund_tokens & set(label.split())):
            # keep addenda even if the fund name isn't in the link text
            if not any(h in label for h in ("addendum", "notice")):
                continue
        out.append(urljoin(base_url, href))
    return out


def _doc_name(url: str) -> str:
    name = urlparse(url).path.rsplit("/", 1)[-1] or url
    return name


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out
