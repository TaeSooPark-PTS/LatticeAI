"""v3.6.0 browser & web ingestion route tests.

Drives create_browser_router over a real IngestionPipeline + temp store with an
injected fetcher (no network). Verifies both layers feed the Knowledge Graph as
web_url / browser_tab sources, fail gracefully, and enforce auth + size limits.
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from knowledge_graph import KnowledgeGraphStore
from latticeai.api.browser import (
    BrowserFetchError,
    create_browser_router,
    extract_readable_text,
)
from lattice_brain.ingestion import IngestionPipeline


def _client(tmp_path, *, fetch_url=None, require_user=None, enable_graph=True):
    store = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    pipeline = IngestionPipeline(store, enable_graph=enable_graph)
    app = FastAPI()
    app.include_router(create_browser_router(
        pipeline=pipeline,
        require_user=require_user or (lambda request: "user@example.com"),
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


def test_extract_readable_text_strips_scripts_and_styles():
    title, text = extract_readable_text(
        "<title>T</title><style>a{}</style><script>x()</script><p>Visible</p>"
    )
    assert title == "T"
    assert "Visible" in text
    assert "x()" not in text
