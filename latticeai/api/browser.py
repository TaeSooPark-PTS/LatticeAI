"""Browser & web ingestion — Knowledge Graph inputs, not standalone features.

v3.6.0 Knowledge Graph First: a public URL or an open browser tab is just another
source that converges into the Knowledge Graph through the unified ingestion
pipeline. Everything runs on the **local runtime** — the server fetches/reads
locally, stores into local SQLite, and never uploads to a cloud service.

Two layers, both feeding ``IngestionPipeline.ingest``:

* ``POST /api/browser/read-url`` — the runtime fetches a public URL locally,
  extracts readable text, stores it as ``source_type=web_url``. Fails gracefully
  on blocked / login-required pages.
* ``POST /api/browser/ingest-current-tab`` — accepts a payload from the local
  browser extension (url/title/text/selected_text/html), sanitizes + size-limits
  it, stores it as ``source_type=browser_tab``.
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any, Callable, Optional, Tuple
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from lattice_brain.ingestion import IngestionItem

MAX_TAB_BYTES = 4 * 1024 * 1024          # 4 MB per captured tab payload
MAX_URL_FETCH_BYTES = 4 * 1024 * 1024    # 4 MB cap on a fetched page
URL_FETCH_TIMEOUT = 12.0                  # seconds


class BrowserFetchError(Exception):
    """A URL could not be fetched (blocked, login-required, timeout, too big)."""


# ── readable-text extraction ─────────────────────────────────────────────────
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "head"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._chunks: list[str] = []
        self.title: str = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag in {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "section", "article"}:
            self._chunks.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._skip_depth == 0:
            text = data.strip()
            if text:
                self._chunks.append(text)

    def text(self) -> str:
        raw = " ".join(self._chunks)
        # collapse runs of whitespace while keeping paragraph breaks
        lines = [ln.strip() for ln in raw.replace("\r", "").split("\n")]
        return "\n".join([ln for ln in lines if ln]).strip()


def extract_readable_text(html: str) -> Tuple[str, str]:
    """Return (title, readable_text) from an HTML string. Never raises."""
    parser = _TextExtractor()
    try:
        parser.feed(html or "")
    except Exception:  # noqa: BLE001 — malformed HTML must still yield best-effort text
        pass
    return parser.title.strip(), parser.text()


def _default_fetch_url(url: str) -> Tuple[str, str]:
    """Fetch a public URL on the local runtime and extract readable text.

    Raises :class:`BrowserFetchError` on any non-success (blocked, login wall,
    timeout, oversized, non-HTML) so the route can fail gracefully.
    """
    import httpx

    try:
        with httpx.Client(
            follow_redirects=True, timeout=URL_FETCH_TIMEOUT,
            headers={"User-Agent": "LatticeAI-local/3.6 (+local-first knowledge graph)"},
        ) as client:
            resp = client.get(url)
    except httpx.HTTPError as exc:
        raise BrowserFetchError(f"Could not reach the page: {exc}") from exc

    if resp.status_code in (401, 403):
        raise BrowserFetchError("The page is login-required or blocked (HTTP %s)." % resp.status_code)
    if resp.status_code >= 400:
        raise BrowserFetchError(f"The page returned HTTP {resp.status_code}.")
    content_type = resp.headers.get("content-type", "")
    if content_type and "html" not in content_type and "text" not in content_type:
        raise BrowserFetchError(f"Unsupported content type: {content_type or 'unknown'}.")
    body = resp.text or ""
    if len(body.encode("utf-8", "ignore")) > MAX_URL_FETCH_BYTES:
        body = body.encode("utf-8", "ignore")[:MAX_URL_FETCH_BYTES].decode("utf-8", "ignore")
    title, text = extract_readable_text(body)
    return (title or url, text)


def _validate_http_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="url is required.")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Only http(s) URLs are supported.")
    if not parsed.netloc:
        raise HTTPException(status_code=400, detail="Malformed URL.")
    return url


# ── request models ───────────────────────────────────────────────────────────
class ReadUrlRequest(BaseModel):
    url: str
    workspace_id: Optional[str] = None


class IngestTabRequest(BaseModel):
    url: str
    title: Optional[str] = None
    text: Optional[str] = None
    selected_text: Optional[str] = None
    html: Optional[str] = None
    captured_at: Optional[str] = None
    workspace_id: Optional[str] = None


def create_browser_router(
    *,
    pipeline: Any,
    require_user: Callable[[Request], str],
    fetch_url: Optional[Callable[[str], Tuple[str, str]]] = None,
    max_tab_bytes: int = MAX_TAB_BYTES,
) -> APIRouter:
    router = APIRouter()
    _fetch = fetch_url or _default_fetch_url

    def _require_pipeline():
        if pipeline is None or not pipeline.available():
            raise HTTPException(status_code=503, detail="Knowledge Graph ingestion is disabled.")

    @router.post("/api/browser/read-url")
    async def read_url(req: ReadUrlRequest, request: Request):
        """Fetch a public URL locally and ingest it as a web_url source."""
        user = require_user(request)
        _require_pipeline()
        url = _validate_http_url(req.url)
        try:
            title, text = _fetch(url)
        except BrowserFetchError as exc:
            # Graceful failure — not a 5xx; the page was simply unreadable.
            raise HTTPException(status_code=422, detail=str(exc))
        if not (text or "").strip():
            return {"status": "empty", "source_type": "web_url", "url": url,
                    "detail": "No readable text was extracted from the page."}
        res = pipeline.ingest(
            IngestionItem(
                source_type="web_url", title=title, text=text, source_uri=url,
                owner=user, workspace_id=req.workspace_id,
            ),
            user_email=user,
        )
        return res.as_dict()

    @router.post("/api/browser/ingest-current-tab")
    async def ingest_current_tab(req: IngestTabRequest, request: Request):
        """Ingest a payload captured from the local browser extension."""
        user = require_user(request)
        _require_pipeline()
        url = _validate_http_url(req.url)
        # Sanitize: reject an oversized payload before doing any work.
        for value in (req.text, req.html, req.selected_text):
            if value and len(value.encode("utf-8", "ignore")) > max_tab_bytes:
                raise HTTPException(status_code=413, detail="Captured payload is too large.")
        text = (req.text or "").strip()
        if not text and req.html:
            _title, text = extract_readable_text(req.html)
        if not text:
            text = (req.selected_text or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="No text, html, or selected_text provided.")
        res = pipeline.ingest(
            IngestionItem(
                source_type="browser_tab",
                title=req.title or url,
                text=text,
                source_uri=url,
                captured_at=req.captured_at,
                owner=user,
                workspace_id=req.workspace_id,
                metadata={"has_selection": bool(req.selected_text)},
            ),
            user_email=user,
        )
        return res.as_dict()

    return router
