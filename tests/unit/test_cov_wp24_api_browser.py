"""wp24 coverage — ``latticeai.api.browser`` URL validation + local fetch.

This module is the one place the runtime reaches out to the public internet, so
its guards are the point: what a URL must look like before a socket is opened,
that every resolved address is globally routable (SSRF), that the response is
bounded and textual, and that redirects are followed against the *original*
URL rather than the pinned IP one.

No socket is opened here. DNS is a fake resolver and HTTP is an
``httpx.MockTransport`` — both are already parameters of the fetch function, so
the real code path runs end to end.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.api import browser
from latticeai.api.browser import (
    BrowserFetchError,
    _default_fetch_url,
    _is_public_ip,
    _parse_http_url,
    _read_limited_response,
    _resolve_public_target,
    create_browser_router,
    extract_readable_text,
)

_PUBLIC_V4 = "93.184.216.34"
_PUBLIC_V6 = "2606:2800:220:1:248:1893:25c8:1946"
_LONG_HOST = "http://" + ".".join(["a" * 63, "b" * 63, "c" * 63, "d" * 63]) + "/"


class _ExhaustedRedirectBudget:
    """A redirect allowance of zero attempts (``max_redirects + 1 == 0``)."""

    def __lt__(self, _other):
        return False

    def __add__(self, _other):
        return 0


class _UndecodableResponse(httpx.Response):
    """A page whose declared charset is not a codec this interpreter can load."""

    @property
    def encoding(self):
        return "definitely-not-a-codec"


def _resolver(*addresses, port_override=None):
    def resolve(hostname, port, family=None, type=None):  # noqa: A002 - getaddrinfo's own name
        assert hostname
        return [(2, 1, 6, "", (address, port_override or port)) for address in addresses]

    return resolve


def _transport(*responses):
    """A MockTransport replaying one response (or exception) per request."""
    queue = list(responses)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item

    return httpx.MockTransport(handler), seen


# ── readable text ───────────────────────────────────────────────────────────


def test_extract_readable_text_keeps_the_title_and_drops_scripts():
    title, text = extract_readable_text(
        "<html><head><title>Doc</title><script>bad()</script></head>"
        "<body><p>First line</p><p>Second line</p></body></html>"
    )

    assert title == "Doc"
    assert text.splitlines() == ["First line", "Second line"]
    assert "bad()" not in text


def test_extract_readable_text_never_raises_on_a_hostile_parser(monkeypatch):
    class _BrokenExtractor(browser._TextExtractor):
        def feed(self, data):
            raise ValueError("markup exploded")

    monkeypatch.setattr(browser, "_TextExtractor", _BrokenExtractor)

    assert extract_readable_text("<p>hi</p>") == ("", "")


# ── URL parsing ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("", "url is required."),
        ("http://example.com/" + "a" * 8192, "URL is too long."),
        ("http://exa\\mple.com/", "Malformed URL."),
        ("http://exa\x01mple.com/", "Malformed URL."),
        ("http://[::1", "Malformed URL."),
        ("http://example.com:notaport/", "Malformed URL."),
        ("ftp://example.com/", "Only http(s) URLs are supported."),
        ("http:///just-a-path", "Malformed URL."),
        ("http://user:pw@example.com/", "URLs containing credentials are not supported."),
        ("http://[fe80::1%25eth0]/", "Scoped IP addresses are not supported."),
        ("http://./", "Malformed URL."),
        ("http://a..b/", "Malformed URL hostname."),
        (_LONG_HOST, "Malformed URL hostname."),
        ("http://example.com:0/", "Malformed URL port."),
    ],
)
def test_parse_http_url_refuses_unsafe_or_ambiguous_urls(url, message):
    with pytest.raises(ValueError, match=message.replace(".", r"\.").replace("(", r"\(").replace(")", r"\)")):
        _parse_http_url(url)


@pytest.mark.parametrize(
    ("url", "hostname", "port"),
    [
        ("https://Example.COM/docs", "example.com", 443),
        ("http://example.com./docs", "example.com", 80),
        ("http://example.com:8443/docs", "example.com", 8443),
    ],
)
def test_parse_http_url_normalizes_a_public_url(url, hostname, port):
    cleaned, parsed, ascii_hostname, resolved_port = _parse_http_url(url)

    assert cleaned == url
    assert ascii_hostname == hostname
    assert resolved_port == port
    assert parsed.path == "/docs"


@pytest.mark.parametrize(
    ("value", "public"),
    [
        (_PUBLIC_V4, True),
        (_PUBLIC_V6, True),
        ("127.0.0.1", False),
        ("10.0.0.5", False),
        ("169.254.1.1", False),
        ("224.0.0.1", False),
        ("not-an-ip-at-all", False),
    ],
)
def test_only_globally_routable_addresses_count_as_public(value, public):
    assert _is_public_ip(value) is public


# ── DNS resolution guard ────────────────────────────────────────────────────


def test_resolution_rejects_a_malformed_url_before_touching_dns():
    with pytest.raises(BrowserFetchError, match="Only http"):
        _resolve_public_target("ftp://example.com/", resolver=_resolver(_PUBLIC_V4))


def test_resolution_rejects_localhost_by_name():
    with pytest.raises(BrowserFetchError, match="Local and private network URLs"):
        _resolve_public_target("http://api.localhost/x", resolver=_resolver(_PUBLIC_V4))


def test_resolution_reports_a_dns_failure_as_an_unreachable_host():
    def failing(_hostname, _port, family=None, type=None):  # noqa: A002 - getaddrinfo's own name
        raise OSError("temporary failure in name resolution")

    with pytest.raises(BrowserFetchError, match="Could not resolve the page host"):
        _resolve_public_target("https://example.com/", resolver=failing)


def test_resolution_rejects_a_malformed_dns_record():
    def malformed(_hostname, _port, family=None, type=None):  # noqa: A002 - getaddrinfo's own name
        return [(2, 1, 6, "", ())]

    with pytest.raises(BrowserFetchError, match="invalid DNS record"):
        _resolve_public_target("https://example.com/", resolver=malformed)


def test_resolution_rejects_a_private_address_behind_a_public_name():
    with pytest.raises(BrowserFetchError, match="Local and private network URLs"):
        _resolve_public_target("https://example.com/", resolver=_resolver("127.0.0.1"))


def test_resolution_reports_an_empty_answer_as_unresolvable():
    def empty(_hostname, _port, family=None, type=None):  # noqa: A002 - getaddrinfo's own name
        return []

    with pytest.raises(BrowserFetchError, match="Could not resolve the page host"):
        _resolve_public_target("https://example.com/", resolver=empty)


def test_resolution_deduplicates_the_addresses_it_will_connect_to():
    cleaned, _parsed, hostname, port, addresses = _resolve_public_target(
        "https://example.com/docs", resolver=_resolver(_PUBLIC_V4, _PUBLIC_V4, _PUBLIC_V6),
    )

    assert cleaned == "https://example.com/docs"
    assert hostname == "example.com"
    assert port == 443
    assert addresses == (_PUBLIC_V4, _PUBLIC_V6)


# ── response size guard ─────────────────────────────────────────────────────


def test_an_unparseable_content_length_does_not_bypass_the_streaming_cap():
    response = httpx.Response(
        200, headers={"content-length": "not-a-number"}, content=b"x" * 10,
    )

    assert _read_limited_response(response, 64) == b"x" * 10

    oversized = httpx.Response(
        200, headers={"content-length": "not-a-number"}, content=b"x" * 100,
    )
    with pytest.raises(BrowserFetchError, match="too large"):
        _read_limited_response(oversized, 8)


def test_a_declared_size_over_the_cap_is_refused_before_reading():
    response = httpx.Response(200, headers={"content-length": "999999"}, content=b"x")

    with pytest.raises(BrowserFetchError, match="too large"):
        _read_limited_response(response, 1024)


# ── fetch ───────────────────────────────────────────────────────────────────


def test_fetch_refuses_impossible_limits():
    with pytest.raises(BrowserFetchError, match="Invalid URL fetch limits"):
        _default_fetch_url("https://example.com/", max_bytes=0)


def test_fetch_pins_the_resolved_address_and_keeps_the_origin_host_header():
    transport, seen = _transport(
        httpx.Response(
            200, headers={"content-type": "text/html"},
            content=b"<html><head><title>Docs</title></head><body><p>Body text</p></body></html>",
        ),
    )

    title, text = _default_fetch_url(
        "https://example.com/docs", resolver=_resolver(_PUBLIC_V4), transport=transport,
    )

    assert (title, text) == ("Docs", "Body text")
    assert str(seen[0].url) == f"https://{_PUBLIC_V4}/docs"
    assert seen[0].headers["host"] == "example.com"
    assert seen[0].extensions["sni_hostname"] == "example.com"


def test_fetch_tries_the_next_address_when_one_refuses_the_connection():
    transport, seen = _transport(
        httpx.ConnectError("connection refused"),
        httpx.Response(200, headers={"content-type": "text/plain"}, content=b"plain body"),
    )

    title, text = _default_fetch_url(
        "https://example.com/", resolver=_resolver(_PUBLIC_V4, _PUBLIC_V6), transport=transport,
    )

    assert text == "plain body"
    assert title == "https://example.com/"
    assert len(seen) == 2


def test_fetch_reports_the_last_connect_error_when_every_address_fails():
    transport, _seen = _transport(httpx.ConnectError("connection refused"))

    with pytest.raises(BrowserFetchError, match="Could not reach the page: connection refused"):
        _default_fetch_url(
            "https://example.com/", resolver=_resolver(_PUBLIC_V4, _PUBLIC_V6),
            transport=transport,
        )


def test_fetch_follows_a_redirect_against_the_original_url():
    transport, seen = _transport(
        httpx.Response(302, headers={"location": "/moved"}),
        httpx.Response(200, headers={"content-type": "text/html"}, content=b"<p>Moved body</p>"),
    )

    _title, text = _default_fetch_url(
        "https://example.com/start", resolver=_resolver(_PUBLIC_V4), transport=transport,
    )

    assert text == "Moved body"
    assert [request.url.path for request in seen] == ["/start", "/moved"]


def test_fetch_refuses_a_redirect_without_a_location():
    transport, _seen = _transport(httpx.Response(302))

    with pytest.raises(BrowserFetchError, match="redirect without a location"):
        _default_fetch_url(
            "https://example.com/", resolver=_resolver(_PUBLIC_V4), transport=transport,
        )


def test_fetch_refuses_to_exceed_the_redirect_budget():
    transport, _seen = _transport(httpx.Response(302, headers={"location": "/next"}))

    with pytest.raises(BrowserFetchError, match="redirected too many times"):
        _default_fetch_url(
            "https://example.com/", resolver=_resolver(_PUBLIC_V4),
            transport=transport, max_redirects=0,
        )


def test_fetch_reports_an_exhausted_redirect_allowance():
    transport, _seen = _transport(httpx.Response(200, content=b"never read"))

    with pytest.raises(BrowserFetchError, match="redirected too many times"):
        _default_fetch_url(
            "https://example.com/", resolver=_resolver(_PUBLIC_V4),
            transport=transport, max_redirects=_ExhaustedRedirectBudget(),
        )


def test_fetch_reports_a_target_that_resolves_to_no_address(monkeypatch):
    transport, _seen = _transport(httpx.Response(200, content=b"never read"))
    parsed = urlsplit("https://example.com/")
    monkeypatch.setattr(
        browser,
        "_resolve_public_target",
        lambda url, resolver=None: ("https://example.com/", parsed, "example.com", 443, ()),
    )

    with pytest.raises(BrowserFetchError, match="Could not reach the page"):
        _default_fetch_url("https://example.com/", transport=transport)


@pytest.mark.parametrize(
    ("status", "message"),
    [(401, "login-required or blocked"), (403, "login-required or blocked"), (500, "HTTP 500")],
)
def test_fetch_reports_a_blocked_or_failing_page_gracefully(status, message):
    transport, _seen = _transport(httpx.Response(status, content=b""))

    with pytest.raises(BrowserFetchError, match=message):
        _default_fetch_url(
            "https://example.com/", resolver=_resolver(_PUBLIC_V4), transport=transport,
        )


def test_fetch_falls_back_to_utf8_when_the_declared_charset_is_not_a_codec():
    transport, _seen = _transport(
        _UndecodableResponse(
            200, headers={"content-type": "text/html; charset=definitely-not-a-codec"},
            content="<p>본문</p>".encode(),
        ),
    )

    _title, text = _default_fetch_url(
        "https://example.com/", resolver=_resolver(_PUBLIC_V4), transport=transport,
    )

    assert text == "본문"


# ── router ──────────────────────────────────────────────────────────────────


def test_capture_routes_report_a_disabled_ingestion_pipeline():
    app = FastAPI()
    app.include_router(
        create_browser_router(pipeline=None, require_user=lambda _request: "u@example.com"),
    )
    client = TestClient(app)

    read_url = client.post("/api/browser/read-url", json={"url": "https://example.com/"})
    ingest_tab = client.post(
        "/api/browser/ingest-current-tab",
        json={"url": "https://example.com/", "text": "captured"},
    )

    assert read_url.status_code == 503
    assert ingest_tab.status_code == 503
