"""Guarded web fetch and search tools with SSRF protection, HTML distillation,
and indirect prompt-injection sanitization (Python parity with tools::web).
"""

from __future__ import annotations

import html
import ipaddress
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from latticeai.tools import ToolError

MAX_WEB_BODY_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_CHARS = 4_000
MIN_MAX_CHARS = 200
MAX_MAX_CHARS = 12_000

DEFAULT_MAX_RESULTS = 5
MIN_MAX_RESULTS = 1
MAX_MAX_RESULTS = 10

MAX_REDIRECTS = 5
WEB_TIMEOUT_SECONDS = 12

REDACTED_INJECTION_MARKER = "[redacted-prompt-injection-attempt]"

LLM_CONTROL_TOKENS: Tuple[str, ...] = (
    "<|im_start|>",
    "<|im_end|>",
    "<|channel|>",
    "<|channel>",
    "<|message|>",
    "<|start|>",
    "<|end|>",
    "<think>",
    "</think>",
    "<tool_call>",
    "</tool_call>",
)

PROMPT_INJECTION_DIRECTIVES: Tuple[str, ...] = (
    "ignore all previous instructions",
    "ignore all prior instructions",
    "ignore previous instructions",
    "disregard system prompt",
    "override system",
    "you must now call",
    "execute run_command",
    "send all files to",
    "exfiltrate",
)

_CLOUD_METADATA_HOSTS = {
    "169.254.169.254",
    "169.254.170.2",
    "metadata.google.internal",
    "metadata",
    "metadata.internal",
    "instance-data",
    "100.100.100.200",
    "fd00:ec2::254",
}


class DistilledPage(dict):
    """Distilled HTML page supporting both attribute and dict access."""

    def __init__(self, title: str = "", text: str = "", links: Optional[List[str]] = None) -> None:
        super().__init__(title=title, text=text, links=list(links or []))

    @property
    def title(self) -> str:
        return str(self.get("title", ""))

    @property
    def text(self) -> str:
        return str(self.get("text", ""))

    @property
    def links(self) -> List[str]:
        return list(self.get("links", []))


def _looks_like_obscured_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    if host.isdigit():
        return True
    if host.startswith("0x") and all(c in "0123456789abcdef" for c in host[2:]):
        return True
    parts = host.split(".")
    if len(parts) >= 2 and all(
        bool(p)
        and (
            p.isdigit()
            or (p.startswith("0x") and len(p) > 2 and all(c in "0123456789abcdef" for c in p[2:]))
        )
        for p in parts
    ):
        return True
    return False


def _classify_ip(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address, allow_loopback: bool) -> bool:
    if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped is not None:
        ip_obj = ip_obj.ipv4_mapped

    ip_str = str(ip_obj)
    if ip_str in _CLOUD_METADATA_HOSTS:
        raise ToolError(f"Blocked cloud metadata IP address: {ip_str}")

    if ip_obj.is_loopback:
        if allow_loopback:
            return True
        raise ToolError(
            f"Blocked loopback address: {ip_str} (LATTICEAI_WEB_ALLOW_LOOPBACK is not enabled)."
        )

    if ip_obj.is_unspecified or (isinstance(ip_obj, ipaddress.IPv4Address) and ip_obj.packed[0] == 0):
        raise ToolError(f"Blocked unspecified IP address: {ip_str}")

    if ip_obj.is_link_local:
        raise ToolError(f"Blocked link-local IP address: {ip_str}")

    if ip_obj.is_private:
        raise ToolError(f"Blocked private RFC1918/ULA IP address: {ip_str}")

    if ip_obj.is_multicast or ip_obj.is_reserved:
        raise ToolError(f"Blocked reserved/multicast IP address: {ip_str}")

    if isinstance(ip_obj, ipaddress.IPv4Address) and ip_obj == ipaddress.IPv4Address("255.255.255.255"):
        raise ToolError(f"Blocked broadcast IP address: {ip_str}")

    return False


def validate_web_url(raw: str) -> str:
    """Validate a web URL against SSRF, cloud metadata, private IPs, and offline mode."""
    trimmed = str(raw or "").strip()
    if not trimmed:
        raise ToolError("URL is required.")
    if any(ch.isspace() or ord(ch) < 32 for ch in trimmed):
        raise ToolError("URL contains invalid whitespace or control characters.")

    parsed = urllib.parse.urlsplit(trimmed)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise ToolError(
            f"Unsupported URL scheme '{scheme or trimmed}': only http:// and https:// are allowed."
        )
    if not parsed.netloc:
        raise ToolError("URL is missing a host.")
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise ToolError("URLs with userinfo (@) are not allowed.")

    try:
        port = parsed.port
    except ValueError as exc:
        raise ToolError(f"Invalid port in URL: {exc}") from exc
    if port is not None and (port <= 0 or port > 65535):
        raise ToolError("Invalid port in URL.")

    host = (parsed.hostname or "").strip().rstrip(".").lower()
    if not host:
        raise ToolError("URL is missing a host.")

    if host in _CLOUD_METADATA_HOSTS or host.endswith(".metadata.google.internal"):
        raise ToolError(f"Blocked cloud metadata endpoint: {host}")

    if _looks_like_obscured_ip_literal(host):
        raise ToolError(f"Blocked non-standard IP literal: {host}")

    allow_loopback = os.environ.get("LATTICEAI_WEB_ALLOW_LOOPBACK", "").strip() == "1"
    offline_mode = os.environ.get("LATTICEAI_NETWORK_MODE", "").strip().lower() == "offline"

    is_loopback = False
    if host == "localhost" or host.endswith(".localhost"):
        if not allow_loopback:
            raise ToolError(
                f"Blocked loopback host: {host} (LATTICEAI_WEB_ALLOW_LOOPBACK is not enabled)."
            )
        is_loopback = True
    else:
        try:
            ip_obj = ipaddress.ip_address(host)
        except ValueError:
            ip_obj = None
        if ip_obj is not None:
            is_loopback = _classify_ip(ip_obj, allow_loopback)

    if offline_mode:
        if not allow_loopback:
            raise ToolError("Web access is disabled because LATTICEAI_NETWORK_MODE is offline.")
        if not is_loopback:
            raise ToolError(
                f"External host '{host}' is blocked because LATTICEAI_NETWORK_MODE is offline."
            )

    return trimmed


def _resolve_href(href: str, base_url: str) -> Optional[str]:
    trimmed = html.unescape(str(href or "")).strip()
    if not trimmed or trimmed.startswith("#"):
        return None
    lower = trimmed.lower()
    if lower.startswith(("javascript:", "mailto:", "tel:", "data:", "vbscript:")):
        return None
    if lower.startswith(("http://", "https://")):
        return trimmed
    if not base_url:
        return None
    joined = urllib.parse.urljoin(base_url, trimmed)
    parsed = urllib.parse.urlsplit(joined)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        return None
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))


def _strip_all_tags(fragment: str) -> str:
    return re.sub(r"<[^>]*>", " ", fragment)


def _collapse_ws(text: str) -> str:
    return " ".join(text.split())


_STRIP_BLOCK_PATTERNS = [
    re.compile(r"(?is)<!--.*?-->"),
    re.compile(r"(?is)<script\b[^>]*>.*?</script>"),
    re.compile(r"(?is)<style\b[^>]*>.*?</style>"),
    re.compile(r"(?is)<noscript\b[^>]*>.*?</noscript>"),
    re.compile(r"(?is)<svg\b[^>]*>.*?</svg>"),
    re.compile(r"(?is)<nav\b[^>]*>.*?</nav>"),
    re.compile(r"(?is)<footer\b[^>]*>.*?</footer>"),
    re.compile(r"(?is)<iframe\b[^>]*>.*?</iframe>"),
]


_HEADING_PREFIXES = {
    "h1": "\n\n# ",
    "h2": "\n\n## ",
    "h3": "\n\n### ",
    "h4": "\n\n#### ",
    "h5": "\n\n#### ",
    "h6": "\n\n#### ",
}
_BLOCK_BREAK_TAGS = frozenset({"p", "article", "section", "main", "blockquote", "pre"})
_LINE_BREAK_TAGS = frozenset({"div", "header", "ul", "ol", "table", "br", "hr"})


def _extract_html_links(cleaned: str, base_url: str) -> List[str]:
    links: List[str] = []
    for match in re.finditer(r"(?is)<a\b([^>]*)>(.*?)</a>", cleaned):
        if len(links) >= 15:
            break
        attrs, inner = match.group(1), match.group(2)
        href_match = re.search(
            r"""(?i)\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>"']+))""",
            attrs,
        )
        if not href_match:
            continue
        raw_href = href_match.group(1) or href_match.group(2) or href_match.group(3) or ""
        resolved = _resolve_href(raw_href, base_url)
        if not resolved:
            continue
        label = _collapse_ws(html.unescape(_strip_all_tags(inner))) or resolved
        formatted = f"[{label}]({resolved})"
        if formatted not in links:
            links.append(formatted)
    return links


def _apply_distilled_tag(
    tag_content: str, out_parts: List[str], in_tr: bool, cell_in_row: int
) -> Tuple[bool, int]:
    is_closing = tag_content.startswith("/")
    tag_body = tag_content[1:].strip() if is_closing else tag_content.rstrip("/").strip()
    tag_name = re.split(r"[\s/]+", tag_body, maxsplit=1)[0].lower() if tag_body else ""

    if tag_name in _HEADING_PREFIXES:
        out_parts.append("\n\n" if is_closing else _HEADING_PREFIXES[tag_name])
    elif tag_name == "li":
        out_parts.append("\n" if is_closing else "\n- ")
    elif tag_name == "tr":
        if is_closing:
            if in_tr:
                out_parts.append(" |\n")
            return False, 0
        out_parts.append("\n| ")
        return True, 0
    elif not is_closing and tag_name in ("td", "th"):
        if in_tr and cell_in_row > 0:
            out_parts.append(" | ")
        return in_tr, cell_in_row + 1
    elif tag_name in _BLOCK_BREAK_TAGS:
        out_parts.append("\n\n")
    elif tag_name in _LINE_BREAK_TAGS:
        out_parts.append("\n")
    return in_tr, cell_in_row


def _normalize_distilled_lines(raw_text: str) -> str:
    lines: List[str] = []
    blank_streak = 0
    for raw_line in raw_text.splitlines():
        collapsed = _collapse_ws(raw_line)
        if not collapsed:
            blank_streak += 1
            if blank_streak == 1 and lines:
                lines.append("")
        else:
            blank_streak = 0
            lines.append(collapsed)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def distill_html_to_text(html_text: str, base_url: str = "") -> DistilledPage:
    """Strip noise elements and distill HTML into structured text, title, and links."""
    cleaned = str(html_text or "")
    for pat in _STRIP_BLOCK_PATTERNS:
        cleaned = pat.sub(" ", cleaned)

    title = ""
    title_match = re.search(r"(?is)<title\b[^>]*>(.*?)</title>", cleaned)
    if title_match:
        title = _collapse_ws(html.unescape(_strip_all_tags(title_match.group(1))))

    links = _extract_html_links(cleaned, base_url)
    body_html = re.sub(r"(?is)<head\b[^>]*>.*?</head>", "\n", cleaned)

    out_parts: List[str] = []
    in_tr = False
    cell_in_row = 0
    idx = 0
    length = len(body_html)

    while idx < length:
        if body_html[idx] == "<":
            close_idx = body_html.find(">", idx)
            if close_idx != -1:
                tag_content = body_html[idx + 1 : close_idx].strip()
                in_tr, cell_in_row = _apply_distilled_tag(
                    tag_content, out_parts, in_tr, cell_in_row
                )
                idx = close_idx + 1
                continue

        next_tag = body_html.find("<", idx)
        if next_tag == -1:
            next_tag = length
        chunk = html.unescape(body_html[idx:next_tag])
        out_parts.append(re.sub(r"[\r\n\t]+", " ", chunk))
        idx = next_tag

    return DistilledPage(
        title=title,
        text=_normalize_distilled_lines("".join(out_parts)),
        links=links,
    )


def sanitize_untrusted_web_text(text: str) -> Tuple[str, int]:
    """Strip LLM control tokens and redact indirect prompt-injection directives."""
    current = str(text or "")
    redacted_count = 0

    for token in LLM_CONTROL_TOKENS:
        pattern = re.compile(re.escape(token), re.IGNORECASE)
        current, n = pattern.subn("", current)
        redacted_count += n

    for directive in PROMPT_INJECTION_DIRECTIVES:
        pattern = re.compile(re.escape(directive), re.IGNORECASE)
        current, n = pattern.subn(REDACTED_INJECTION_MARKER, current)
        redacted_count += n

    return current, redacted_count


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _get_with_validated_redirects(initial_url: str) -> Dict[str, Any]:
    current_url = validate_web_url(initial_url)
    opener = urllib.request.build_opener(_NoRedirectHandler)

    for hop in range(MAX_REDIRECTS + 1):
        req = urllib.request.Request(
            current_url,
            headers={
                "User-Agent": "LatticeAI-WebHarness/1.0",
                "Accept": "text/html,application/xhtml+xml,application/json,text/plain;q=0.9,*/*;q=0.8",
            },
            method="GET",
        )
        try:
            with opener.open(req, timeout=WEB_TIMEOUT_SECONDS) as resp:
                status_code = int(getattr(resp, "status", 200))
                content_type = resp.headers.get("Content-Type", "text/plain")
                raw_bytes = resp.read(MAX_WEB_BODY_BYTES + 1)
                body_truncated = len(raw_bytes) > MAX_WEB_BODY_BYTES
                if body_truncated:
                    raw_bytes = raw_bytes[:MAX_WEB_BODY_BYTES]
                return {
                    "final_url": current_url,
                    "status_code": status_code,
                    "content_type": content_type,
                    "body_text": raw_bytes.decode("utf-8", errors="replace"),
                    "body_truncated": body_truncated,
                }
        except urllib.error.HTTPError as err:
            if err.code in (301, 302, 303, 307, 308):
                if hop == MAX_REDIRECTS:
                    raise ToolError(f"Too many redirects (exceeded {MAX_REDIRECTS}).") from err
                location = err.headers.get("Location") if err.headers else None
                if not location:
                    raise ToolError("Redirect response is missing Location header.") from err
                resolved = _resolve_href(location, current_url)
                if not resolved:
                    raise ToolError(f"Invalid redirect Location: {location}") from err
                current_url = validate_web_url(resolved)
                continue
            raw_bytes = err.read(MAX_WEB_BODY_BYTES) if hasattr(err, "read") else b""
            content_type = (
                err.headers.get("Content-Type", "text/plain") if err.headers else "text/plain"
            )
            return {
                "final_url": current_url,
                "status_code": int(err.code),
                "content_type": content_type,
                "body_text": raw_bytes.decode("utf-8", errors="replace"),
                "body_truncated": False,
            }
        except Exception as exc:
            raise ToolError(f"web request failed: {exc}") from exc

    raise ToolError("Too many redirects.")


def _is_html_content(content_type: str, body: str) -> bool:
    ct_lower = (content_type or "").lower()
    if "text/html" in ct_lower or "application/xhtml" in ct_lower:
        return True
    if "application/json" in ct_lower or "text/plain" in ct_lower:
        return False
    sample = (body or "").lstrip()[:64].lower()
    return sample.startswith("<!doctype html") or sample.startswith("<html")


def _clamp_int(val: Any, default: int, low: int, high: int) -> int:
    try:
        parsed = int(val)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, parsed))


def web_fetch(url: str, max_chars: int = DEFAULT_MAX_CHARS) -> Dict[str, Any]:
    """Fetch a web page with SSRF validation, HTML distillation, and injection sanitization."""
    raw_url = str(url or "").strip()
    if not raw_url:
        raise ToolError("URL is required.")
    clamped_max = _clamp_int(max_chars, DEFAULT_MAX_CHARS, MIN_MAX_CHARS, MAX_MAX_CHARS)

    validated_url = validate_web_url(raw_url)
    fetched = _get_with_validated_redirects(validated_url)

    if _is_html_content(fetched["content_type"], fetched["body_text"]):
        distilled = distill_html_to_text(fetched["body_text"], fetched["final_url"])
        clean_title, r_title = sanitize_untrusted_web_text(distilled.title)
        clean_text, r_text = sanitize_untrusted_web_text(distilled.text)
        links = distilled.links
        redacted_count = r_title + r_text
    else:
        clean_title = ""
        clean_text, redacted_count = sanitize_untrusted_web_text(fetched["body_text"])
        links = []

    total_chars = len(clean_text)
    truncated = total_chars > clamped_max or bool(fetched["body_truncated"])
    content_excerpt = clean_text[:clamped_max]

    return {
        "url": validated_url,
        "final_url": fetched["final_url"],
        "status_code": fetched["status_code"],
        "content_type": fetched["content_type"],
        "title": clean_title,
        "content_excerpt": content_excerpt,
        "links": links,
        "char_count": len(content_excerpt),
        "truncated": truncated,
        "injection_signals_redacted": redacted_count,
        "untrusted_web_content": True,
    }


def _unwrap_ddg_url(href: str) -> str:
    decoded = html.unescape(str(href or "")).strip()
    parsed = urllib.parse.urlsplit(decoded)
    if parsed.query:
        qs = urllib.parse.parse_qs(parsed.query)
        uddg = qs.get("uddg", [None])[0]
        if uddg and uddg.startswith(("http://", "https://")):
            return uddg
    if decoded.startswith("//"):
        return f"https:{decoded}"
    return decoded


def parse_duckduckgo_html(html_text: str, max_results: int = DEFAULT_MAX_RESULTS) -> List[Tuple[str, str, str]]:
    """Extract `(title, url, snippet)` tuples from DuckDuckGo HTML search page."""
    cleaned = str(html_text or "")
    for pat in _STRIP_BLOCK_PATTERNS:
        cleaned = pat.sub(" ", cleaned)

    snippets: List[str] = []
    for s_match in re.finditer(
        r"""(?is)<(?:a|div|span)\b[^>]*class\s*=\s*["'][^"']*result__snippet[^"']*["'][^>]*>(.*?)</(?:a|div|span)>""",
        cleaned,
    ):
        snippets.append(_collapse_ws(html.unescape(_strip_all_tags(s_match.group(1)))))

    items: List[Tuple[str, str, str]] = []
    idx = 0
    for a_match in re.finditer(
        r"""(?is)(<a\b[^>]*class\s*=\s*["'][^"']*result__a[^"']*["'][^>]*>(.*?)</a>)""",
        cleaned,
    ):
        if len(items) >= max_results:
            break
        full_tag, inner = a_match.group(1), a_match.group(2)
        href_match = re.search(
            r"""(?i)\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>"']+))""",
            full_tag,
        )
        if not href_match:
            continue
        raw_href = href_match.group(1) or href_match.group(2) or href_match.group(3) or ""
        target_url = _unwrap_ddg_url(raw_href)
        if not target_url.startswith(("http://", "https://")):
            continue
        title = _collapse_ws(html.unescape(_strip_all_tags(inner)))
        snippet = snippets[idx] if idx < len(snippets) else ""
        idx += 1
        items.append((title, target_url, snippet))

    return items


def _parse_custom_search_json(payload: Any, max_results: int) -> List[Tuple[str, str, str]]:
    if isinstance(payload, list):
        entries = payload
    elif isinstance(payload, dict):
        entries = payload.get("results") or payload.get("items") or []
    else:
        entries = []

    out: List[Tuple[str, str, str]] = []
    for item in entries[:max_results]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or item.get("href") or item.get("link") or "").strip()
        snippet = str(
            item.get("snippet") or item.get("description") or item.get("content") or ""
        ).strip()
        if not url and not title:
            continue
        out.append((title, url, snippet))
    return out


def web_search(query: str, max_results: int = DEFAULT_MAX_RESULTS) -> Dict[str, Any]:
    """Search the web and return sanitized citations (`web:1`, `web:2`, ...)."""
    clean_query = str(query or "").strip()
    if not clean_query:
        raise ToolError("Search query is required.")
    clamped_max = _clamp_int(max_results, DEFAULT_MAX_RESULTS, MIN_MAX_RESULTS, MAX_MAX_RESULTS)

    custom_endpoint = os.environ.get("LATTICEAI_WEB_SEARCH_ENDPOINT", "").strip()
    if custom_endpoint:
        validated_base = validate_web_url(custom_endpoint)
        sep = "&" if "?" in validated_base else "?"
        encoded_q = urllib.parse.quote_plus(clean_query)
        target_url = f"{validated_base}{sep}q={encoded_q}&max_results={clamped_max}"
        fetched = _get_with_validated_redirects(target_url)
        try:
            parsed_json = json.loads(fetched["body_text"])
        except Exception as exc:
            raise ToolError(f"failed to parse web_search JSON response: {exc}") from exc
        provider = "custom_endpoint"
        raw_items = _parse_custom_search_json(parsed_json, clamped_max)
    else:
        encoded_q = urllib.parse.quote_plus(clean_query)
        ddg_url = f"https://html.duckduckgo.com/html/?q={encoded_q}"
        fetched = _get_with_validated_redirects(ddg_url)
        provider = "duckduckgo_html"
        raw_items = parse_duckduckgo_html(fetched["body_text"], clamped_max)

    results: List[Dict[str, Any]] = []
    for idx, (raw_title, item_url, raw_snippet) in enumerate(raw_items[:clamped_max], start=1):
        clean_title, _ = sanitize_untrusted_web_text(raw_title)
        clean_snippet, _ = sanitize_untrusted_web_text(raw_snippet)
        results.append(
            {
                "rank": idx,
                "citation_id": f"web:{idx}",
                "title": clean_title,
                "url": item_url,
                "snippet": clean_snippet,
            }
        )

    return {
        "query": clean_query,
        "provider": provider,
        "count": len(results),
        "results": results,
    }


__all__ = [
    "DistilledPage",
    "REDACTED_INJECTION_MARKER",
    "distill_html_to_text",
    "parse_duckduckgo_html",
    "sanitize_untrusted_web_text",
    "validate_web_url",
    "web_fetch",
    "web_search",
]
