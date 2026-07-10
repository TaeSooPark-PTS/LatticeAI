"""v3.6.0 browser & web ingestion route tests.

Drives create_browser_router over a real IngestionPipeline + temp store with an
injected fetcher (no network). Verifies both layers feed the Knowledge Graph as
web_url / browser_tab sources, fail gracefully, and enforce auth + size limits.
"""
from __future__ import annotations

import socket
import sys
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from knowledge_graph import KnowledgeGraphStore
from latticeai.api.browser import (
    BrowserFetchError,
    _default_fetch_url,
    create_browser_router,
    extract_readable_text,
)
from lattice_brain.ingestion import IngestionPipeline


def _client(
    tmp_path,
    *,
    fetch_url=None,
    require_user=None,
    enable_graph=True,
    workspace_service=None,
):
    store = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    pipeline = IngestionPipeline(store, enable_graph=enable_graph)
    app = FastAPI()
    app.include_router(create_browser_router(
        pipeline=pipeline,
        require_user=require_user or (lambda request: "user@example.com"),
        workspace_service=workspace_service,
        fetch_url=fetch_url or (lambda url: ("Example", "Example readable body about Lattice AI.")),
    ))
    return TestClient(app), store


def test_read_url_ingests_web_source(tmp_path):
    client, store = _client(tmp_path)
    r = client.post("/api/browser/read-url", json={"url": "https://example.com/post"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["source_type"] == "web_url"
    assert body["node_id"]
    prov = store.get_provenance(body["node_id"])
    assert prov["source_type"] == "web_url"
    assert prov["source_uri"] == "https://example.com/post"


def test_read_url_blocked_page_fails_gracefully(tmp_path):
    def blocked(url):
        raise BrowserFetchError("login-required")

    client, _ = _client(tmp_path, fetch_url=blocked)
    r = client.post("/api/browser/read-url", json={"url": "https://paywall.example/x"})
    assert r.status_code == 422
    assert "login-required" in r.json()["detail"]


def test_read_url_rejects_non_http_scheme(tmp_path):
    client, _ = _client(tmp_path)
    r = client.post("/api/browser/read-url", json={"url": "file:///etc/passwd"})
    assert r.status_code == 400


def test_read_url_rejects_embedded_credentials(tmp_path):
    client, _ = _client(tmp_path)
    r = client.post(
        "/api/browser/read-url",
        json={"url": "https://user:secret@example.com/private"},
    )
    assert r.status_code == 400
    assert "credentials" in r.json()["detail"]


def test_read_url_empty_text(tmp_path):
    client, _ = _client(tmp_path, fetch_url=lambda url: ("T", "   "))
    r = client.post("/api/browser/read-url", json={"url": "https://blank.example"})
    assert r.status_code == 200
    assert r.json()["status"] == "empty"


def test_ingest_current_tab_with_text(tmp_path):
    client, store = _client(tmp_path)
    r = client.post("/api/browser/ingest-current-tab", json={
        "url": "https://example.com/tab",
        "title": "My Tab",
        "text": "Captured tab content for the Knowledge Graph.",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source_type"] == "browser_tab"
    assert body["node_id"]
    prov = store.get_provenance(body["node_id"])
    assert prov["source_type"] == "browser_tab"


def test_ingest_current_tab_extracts_from_html(tmp_path):
    client, _ = _client(tmp_path)
    html = "<html><head><title>Doc</title><style>.x{}</style></head><body><p>Hello graph</p>" \
           "<script>ignore()</script></body></html>"
    r = client.post("/api/browser/ingest-current-tab", json={
        "url": "https://example.com/htmltab", "html": html,
    })
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"


def test_ingest_current_tab_rejects_oversized(tmp_path):
    client, _ = _client(tmp_path)
    big = "x" * (5 * 1024 * 1024)
    r = client.post("/api/browser/ingest-current-tab", json={
        "url": "https://example.com/big", "text": big,
    })
    assert r.status_code == 413


def test_ingest_current_tab_caps_combined_capture_size(tmp_path):
    # Neither field exceeds the cap independently; the combined capture does.
    app = FastAPI()
    store = KnowledgeGraphStore(tmp_path / "combined-cap.sqlite", tmp_path / "combined-blobs")
    pipeline = IngestionPipeline(store)
    app.include_router(create_browser_router(
        pipeline=pipeline,
        require_user=lambda request: "user@example.com",
        fetch_url=lambda url: ("Example", "body"),
        max_tab_bytes=150,
    ))
    small_client = TestClient(app)
    r = small_client.post("/api/browser/ingest-current-tab", json={
        "url": "https://example.com/big-combined",
        "text": "x" * 60,
        "html": "y" * 60,
    })
    assert r.status_code == 413


def test_ingest_current_tab_requires_content(tmp_path):
    client, _ = _client(tmp_path)
    r = client.post("/api/browser/ingest-current-tab", json={"url": "https://example.com/empty"})
    assert r.status_code == 400


def test_routes_require_auth(tmp_path):
    def deny(request: Request):
        raise HTTPException(status_code=401, detail="auth required")

    client, _ = _client(tmp_path, require_user=deny)
    assert client.post("/api/browser/read-url", json={"url": "https://x.example"}).status_code == 401
    assert client.post("/api/browser/ingest-current-tab",
                       json={"url": "https://x.example", "text": "y"}).status_code == 401


def test_browser_ingestion_rejects_mismatched_or_unauthorized_workspace(tmp_path):
    class WorkspaceService:
        def resolve_write_scope(self, requested, user):
            if requested == "org:denied":
                raise PermissionError(f"{user} cannot write {requested}")
            return requested or "personal"

    client, _ = _client(tmp_path, workspace_service=WorkspaceService())

    mismatch = client.post(
        "/api/browser/ingest-current-tab",
        headers={"X-Workspace-Id": "org:one"},
        json={"url": "https://x.example", "text": "y", "workspace_id": "org:two"},
    )
    denied = client.post(
        "/api/browser/read-url",
        headers={"X-Workspace-Id": "org:denied"},
        json={"url": "https://x.example"},
    )

    assert mismatch.status_code == 403
    assert denied.status_code == 403
    assert "cannot write" in denied.json()["detail"]


def test_extract_readable_text_strips_scripts_and_styles():
    title, text = extract_readable_text(
        "<title>T</title><style>a{}</style><script>x()</script><p>Visible</p>"
    )
    assert title == "T"
    assert "Visible" in text
    assert "x()" not in text


def _resolver_for(mapping, calls=None):
    def resolve(host, port, **_kwargs):
        if calls is not None:
            calls.append((host, port))
        address = mapping[host]
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        sockaddr = (address, port, 0, 0) if family == socket.AF_INET6 else (address, port)
        return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)]

    return resolve


def test_default_fetch_pins_validated_dns_address_and_preserves_origin():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=b"<html><title>Public</title><body><p>Safe page</p></body></html>",
        )

    title, text = _default_fetch_url(
        "https://public.example/article?q=1",
        resolver=_resolver_for({"public.example": "93.184.216.34"}),
        transport=httpx.MockTransport(handler),
    )

    assert title == "Public"
    assert "Safe page" in text
    assert len(seen) == 1
    assert seen[0].url.host == "93.184.216.34"
    assert seen[0].headers["host"] == "public.example"
    assert seen[0].extensions["sni_hostname"] == "public.example"


@pytest.mark.parametrize("address", [
    "127.0.0.1",       # loopback
    "10.0.0.7",        # private
    "169.254.169.254", # link-local / cloud metadata
    "224.0.0.1",       # multicast
    "0.0.0.0",         # unspecified
    "240.0.0.1",       # reserved
    "100.64.0.1",      # shared carrier-grade NAT
    "::1",             # IPv6 loopback
    "fe80::1",         # IPv6 link-local
    "ff02::1",         # IPv6 multicast
    "::",              # IPv6 unspecified
])
def test_default_fetch_rejects_every_non_public_dns_result(address):
    called = False

    def handler(_request):
        nonlocal called
        called = True
        return httpx.Response(200, headers={"content-type": "text/html"}, text="no")

    with pytest.raises(BrowserFetchError, match="private network"):
        _default_fetch_url(
            "https://target.example/resource",
            resolver=_resolver_for({"target.example": address}),
            transport=httpx.MockTransport(handler),
        )
    assert called is False


def test_default_fetch_rejects_localhost_before_dns():
    resolved = False

    def resolver(*_args, **_kwargs):
        nonlocal resolved
        resolved = True
        return []

    with pytest.raises(BrowserFetchError, match="private network"):
        _default_fetch_url(
            "http://api.localhost/admin",
            resolver=resolver,
            transport=httpx.MockTransport(lambda request: httpx.Response(200)),
        )
    assert resolved is False


def test_default_fetch_rejects_mixed_public_and_private_dns_answers():
    def resolver(_host, port, **_kwargs):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", port),
            ),
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("10.0.0.8", port),
            ),
        ]

    called = False

    def handler(_request):
        nonlocal called
        called = True
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="no")

    with pytest.raises(BrowserFetchError, match="private network"):
        _default_fetch_url(
            "https://mixed.example/data",
            resolver=resolver,
            transport=httpx.MockTransport(handler),
        )
    assert called is False


def test_default_fetch_revalidates_redirect_destination_dns():
    dns_calls = []
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            302,
            headers={"location": "http://metadata.internal/latest"},
        )

    resolver = _resolver_for({
        "public.example": "93.184.216.34",
        "metadata.internal": "169.254.169.254",
    }, dns_calls)
    with pytest.raises(BrowserFetchError, match="private network"):
        _default_fetch_url(
            "https://public.example/start",
            resolver=resolver,
            transport=httpx.MockTransport(handler),
        )

    assert dns_calls == [("public.example", 443), ("metadata.internal", 80)]
    assert len(requests) == 1


def test_default_fetch_rejects_oversized_stream_without_buffering_all_chunks():
    class CountingStream(httpx.SyncByteStream):
        def __init__(self):
            self.chunks_read = 0

        def __iter__(self):
            for _ in range(100):
                self.chunks_read += 1
                yield b"12345"

    stream = CountingStream()

    def handler(_request):
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            stream=stream,
        )

    with pytest.raises(BrowserFetchError, match="too large"):
        _default_fetch_url(
            "https://large.example/data",
            resolver=_resolver_for({"large.example": "93.184.216.34"}),
            transport=httpx.MockTransport(handler),
            max_bytes=12,
        )
    assert stream.chunks_read == 3


@pytest.mark.parametrize("content_type", [
    "application/octet-stream",
    "application/pdf",
    "image/png",
    "",
])
def test_default_fetch_rejects_non_text_responses(content_type):
    def handler(_request):
        headers = {"content-type": content_type} if content_type else {}
        return httpx.Response(200, headers=headers, content=b"binary")

    with pytest.raises(BrowserFetchError, match="Unsupported content type"):
        _default_fetch_url(
            "https://files.example/download",
            resolver=_resolver_for({"files.example": "93.184.216.34"}),
            transport=httpx.MockTransport(handler),
        )
