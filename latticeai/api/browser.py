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

import ipaddress
import socket
from html.parser import HTMLParser
from typing import Any, Callable, Optional, Tuple
from urllib.parse import SplitResult, urljoin, urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from latticeai import __version__
from lattice_brain.ingestion import IngestionItem, capture_quality_verdict

MAX_TAB_BYTES = 4 * 1024 * 1024          # 4 MB per captured tab payload
MAX_URL_FETCH_BYTES = 4 * 1024 * 1024    # 4 MB cap on a fetched page
URL_FETCH_TIMEOUT = 12.0                  # seconds
MAX_URL_LENGTH = 8192
MAX_URL_REDIRECTS = 5

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_TEXTUAL_APPLICATION_TYPES = frozenset({
    "application/json",
    "application/ld+json",
    "application/xhtml+xml",
    "application/xml",
})


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


def _parse_http_url(url: str) -> tuple[str, SplitResult, str, int]:
    """Parse a URL into safe, unambiguous HTTP connection components."""
    cleaned = (url or "").strip()
    if not cleaned:
        raise ValueError("url is required.")
    if len(cleaned) > MAX_URL_LENGTH:
        raise ValueError("URL is too long.")
    if "\\" in cleaned or any(ord(char) < 32 or ord(char) == 127 for char in cleaned):
        raise ValueError("Malformed URL.")

    try:
        parsed = urlsplit(cleaned)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Malformed URL.") from exc
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError("Only http(s) URLs are supported.")
    if not parsed.netloc or not hostname:
        raise ValueError("Malformed URL.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URLs containing credentials are not supported.")
    if "%" in hostname:
        # Scoped IPv6 literals are interface-local by definition and also have
        # inconsistent URL parser semantics across HTTP clients.
        raise ValueError("Scoped IP addresses are not supported.")

    hostname = hostname.rstrip(".")
    if not hostname:
        raise ValueError("Malformed URL.")
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("Malformed URL hostname.") from exc
    if len(ascii_hostname) > 253:
        raise ValueError("Malformed URL hostname.")

    port = port if port is not None else (443 if parsed.scheme.lower() == "https" else 80)
    if port < 1 or port > 65535:
        raise ValueError("Malformed URL port.")
    return cleaned, parsed, ascii_hostname, port


def _is_public_ip(value: str) -> bool:
    """Return whether *value* is globally routable (IPv4 or IPv6)."""
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    # Some Python releases report multicast addresses as ``is_global``. Keep
    # the security classes explicit instead of relying on that single flag.
    return address.is_global and not any((
        address.is_loopback,
        address.is_private,
        address.is_link_local,
        address.is_multicast,
        address.is_unspecified,
        address.is_reserved,
        getattr(address, "is_site_local", False),
    ))


def _resolve_public_target(
    url: str,
    *,
    resolver: Optional[Callable[..., Any]] = None,
) -> tuple[str, SplitResult, str, int, tuple[str, ...]]:
    """Resolve a URL and reject every non-public address before connecting."""
    try:
        cleaned, parsed, hostname, port = _parse_http_url(url)
    except ValueError as exc:
        raise BrowserFetchError(str(exc)) from exc

    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise BrowserFetchError("Local and private network URLs are not allowed.")

    resolve = resolver or socket.getaddrinfo
    try:
        records = resolve(
            hostname,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
    except (OSError, socket.gaierror) as exc:
        raise BrowserFetchError(f"Could not resolve the page host: {hostname}.") from exc

    addresses: list[str] = []
    for record in records or ():
        try:
            address = str(record[4][0])
        except (IndexError, TypeError):
            raise BrowserFetchError("The page host returned an invalid DNS record.")
        if not _is_public_ip(address):
            raise BrowserFetchError("Local and private network URLs are not allowed.")
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise BrowserFetchError(f"Could not resolve the page host: {hostname}.")
    return cleaned, parsed, hostname, port, tuple(addresses)


def _origin_host_header(hostname: str, port: int, scheme: str) -> str:
    try:
        is_ipv6 = ipaddress.ip_address(hostname).version == 6
    except ValueError:
        is_ipv6 = False
    host = f"[{hostname}]" if is_ipv6 else hostname
    default_port = 443 if scheme == "https" else 80
    return host if port == default_port else f"{host}:{port}"


def _pinned_request_url(parsed: SplitResult, address: str, port: int) -> str:
    """Build a URL that connects to a pre-validated DNS result.

    The request retains the original Host header and TLS SNI separately. This
    closes the DNS-rebinding window between validation and socket connection.
    """
    ip = ipaddress.ip_address(address)
    host = f"[{ip.compressed}]" if ip.version == 6 else ip.compressed
    default_port = 443 if parsed.scheme.lower() == "https" else 80
    netloc = host if port == default_port else f"{host}:{port}"
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", parsed.query, ""))


def _read_limited_response(response: Any, max_bytes: int) -> bytes:
    content_length = response.headers.get("content-length")
    if content_length:
        try:
            declared_size = int(content_length)
        except ValueError:
            declared_size = -1
        if declared_size > max_bytes:
            raise BrowserFetchError("The page is too large to ingest.")

    body = bytearray()
    for chunk in response.iter_bytes():
        if len(body) + len(chunk) > max_bytes:
            raise BrowserFetchError("The page is too large to ingest.")
        body.extend(chunk)
    return bytes(body)


def _default_fetch_url(
    url: str,
    *,
    resolver: Optional[Callable[..., Any]] = None,
    transport: Any = None,
    max_bytes: int = MAX_URL_FETCH_BYTES,
    max_redirects: int = MAX_URL_REDIRECTS,
) -> Tuple[str, str]:
    """Fetch a public URL on the local runtime and extract readable text.

    Raises :class:`BrowserFetchError` on any non-success (blocked, login wall,
    timeout, oversized, non-HTML) so the route can fail gracefully.
    """
    import httpx

    if max_bytes < 1 or max_redirects < 0:
        raise BrowserFetchError("Invalid URL fetch limits.")

    client_options: dict[str, Any] = {
        "follow_redirects": False,
        "timeout": URL_FETCH_TIMEOUT,
        "trust_env": False,
        "limits": httpx.Limits(max_keepalive_connections=0),
    }
    if transport is not None:
        client_options["transport"] = transport

    current_url = url
    try:
        with httpx.Client(**client_options) as client:
            for redirect_count in range(max_redirects + 1):
                current_url, parsed, hostname, port, addresses = _resolve_public_target(
                    current_url,
                    resolver=resolver,
                )
                next_url: Optional[str] = None
                connect_errors: list[Exception] = []

                for address in addresses:
                    request = client.build_request(
                        "GET",
                        _pinned_request_url(parsed, address, port),
                        headers={
                            "Accept": "text/html, text/plain;q=0.9, application/xhtml+xml;q=0.8",
                            "Accept-Encoding": "identity",
                            "Connection": "close",
                            "Host": _origin_host_header(hostname, port, parsed.scheme.lower()),
                            "User-Agent": f"LatticeAI-local/{__version__} (+local-first knowledge graph)",
                        },
                        extensions={"sni_hostname": hostname},
                    )
                    response = None
                    try:
                        response = client.send(request, stream=True)
                    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                        connect_errors.append(exc)
                        continue

                    try:
                        if response.status_code in _REDIRECT_STATUSES:
                            location = (response.headers.get("location") or "").strip()
                            if not location:
                                raise BrowserFetchError("The page returned a redirect without a location.")
                            if redirect_count >= max_redirects:
                                raise BrowserFetchError("The page redirected too many times.")
                            # Join against the original public URL, never the pinned
                            # IP URL exposed only to the transport.
                            next_url = urljoin(current_url, location)
                            break
                        if response.status_code in (401, 403):
                            raise BrowserFetchError(
                                "The page is login-required or blocked "
                                f"(HTTP {response.status_code})."
                            )
                        if response.status_code >= 400:
                            raise BrowserFetchError(
                                f"The page returned HTTP {response.status_code}."
                            )

                        content_type = response.headers.get("content-type", "")
                        media_type = content_type.split(";", 1)[0].strip().lower()
                        if not (
                            media_type.startswith("text/")
                            or media_type in _TEXTUAL_APPLICATION_TYPES
                        ):
                            raise BrowserFetchError(
                                f"Unsupported content type: {content_type or 'unknown'}."
                            )

                        raw_body = _read_limited_response(response, max_bytes)
                        try:
                            encoding = response.encoding or "utf-8"
                            body = raw_body.decode(encoding, "replace")
                        except (LookupError, UnicodeError):
                            body = raw_body.decode("utf-8", "replace")
                        title, text = extract_readable_text(body)
                        return (title or current_url, text)
                    finally:
                        response.close()

                if next_url is not None:
                    current_url = next_url
                    continue
                if connect_errors:
                    raise BrowserFetchError(
                        f"Could not reach the page: {connect_errors[-1]}"
                    ) from connect_errors[-1]
                raise BrowserFetchError("Could not reach the page.")
    except BrowserFetchError:
        raise
    except httpx.HTTPError as exc:
        raise BrowserFetchError(f"Could not reach the page: {exc}") from exc

    raise BrowserFetchError("The page redirected too many times.")


def _validate_http_url(url: str) -> str:
    try:
        cleaned, _parsed, _hostname, _port = _parse_http_url(url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return cleaned


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
    workspace_service: Any = None,
    fetch_url: Optional[Callable[[str], Tuple[str, str]]] = None,
    max_tab_bytes: int = MAX_TAB_BYTES,
) -> APIRouter:
    router = APIRouter()
    _fetch = fetch_url or _default_fetch_url

    def _require_pipeline():
        if pipeline is None or not pipeline.available():
            raise HTTPException(status_code=503, detail="Knowledge Graph ingestion is disabled.")

    def _write_workspace(request: Request, body_workspace: Optional[str], user: str) -> Optional[str]:
        header_workspace = request.headers.get("X-Workspace-Id")
        header_workspace = header_workspace.strip() if header_workspace and header_workspace.strip() else None
        query_workspace = request.query_params.get("workspace_id")
        query_workspace = query_workspace.strip() if query_workspace and query_workspace.strip() else None
        supplied = [value for value in (body_workspace, header_workspace, query_workspace) if value]
        if len(set(supplied)) > 1:
            raise HTTPException(status_code=403, detail="Workspace selectors must match.")
        requested = supplied[0] if supplied else None
        if workspace_service is None:
            return requested
        try:
            return workspace_service.resolve_write_scope(requested, user or None)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @router.post("/api/browser/read-url")
    async def read_url(req: ReadUrlRequest, request: Request):
        """Fetch a public URL locally and ingest it as a web_url source."""
        user = require_user(request)
        _require_pipeline()
        workspace_id = _write_workspace(request, req.workspace_id, user)
        url = _validate_http_url(req.url)
        try:
            title, text = _fetch(url)
        except BrowserFetchError as exc:
            # Graceful failure — not a 5xx; the page was simply unreadable.
            raise HTTPException(status_code=422, detail=str(exc))
        if not (text or "").strip():
            return {"status": "empty", "source_type": "web_url", "url": url,
                    "detail": "No readable text was extracted from the page.",
                    # Structured CTA (backlog #9): the extraction produced
                    # nothing, tell the UI what the user can do about it.
                    "capture_quality": capture_quality_verdict(None, source_type="web_url")}
        res = pipeline.ingest(
            IngestionItem(
                source_type="web_url", title=title, text=text, source_uri=url,
                owner=user, workspace_id=workspace_id,
            ),
            user_email=user,
        )
        payload = res.as_dict()
        payload["capture_quality"] = capture_quality_verdict(
            res.extraction_quality, source_type="web_url",
        )
        return payload

    @router.post("/api/browser/ingest-current-tab")
    async def ingest_current_tab(req: IngestTabRequest, request: Request):
        """Ingest a payload captured from the local browser extension."""
        user = require_user(request)
        _require_pipeline()
        workspace_id = _write_workspace(request, req.workspace_id, user)
        url = _validate_http_url(req.url)
        # Bound the entire capture, not merely each field independently. The
        # extension commonly supplies full text, selection, and HTML together.
        captured_bytes = sum(
            len(value.encode("utf-8", "ignore"))
            for value in (
                req.url,
                req.title,
                req.text,
                req.selected_text,
                req.html,
                req.captured_at,
                workspace_id,
            )
            if value
        )
        if captured_bytes > max_tab_bytes:
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
                workspace_id=workspace_id,
                metadata={"has_selection": bool(req.selected_text)},
            ),
            user_email=user,
        )
        payload = res.as_dict()
        payload["capture_quality"] = capture_quality_verdict(
            res.extraction_quality, source_type="browser_tab",
        )
        return payload

    return router
