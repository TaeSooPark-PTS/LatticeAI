"""Web extraction quality CTA tests (backlog #9, review §7.2 C).

Covers: the structured capture_quality verdict (thin vs ok, reasons in the
same schema as the pipeline's extraction_quality), its attachment to both
browser capture API responses (read-url and ingest-current-tab), the empty
extraction branch, and the guarantee that a thin verdict never blocks the
ingest itself (advisory, honest — never gating).
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph.store import KnowledgeGraphStore
from lattice_brain.ingestion import (
    CAPTURE_SUGGESTIONS_THIN,
    IngestionPipeline,
    assess_extraction_quality,
    capture_quality_verdict,
)
from latticeai.api.browser import create_browser_router

RICH_TEXT = (
    "Lattice AI ships a local-first knowledge graph. The ingestion pipeline "
    "scores extraction quality with deterministic heuristics. Users can "
    "recapture thin pages, paste text manually, or highlight the source."
)
THIN_TEXT = "Home Menu Login"


def _client(tmp_path, *, fetch_url=None):
    store = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    pipeline = IngestionPipeline(store, enable_graph=True)
    app = FastAPI()
    app.include_router(create_browser_router(
        pipeline=pipeline,
        require_user=lambda request: "user@example.com",
        fetch_url=fetch_url or (lambda url: ("Example", RICH_TEXT)),
    ))
    return TestClient(app), store


# ── verdict unit behavior ────────────────────────────────────────────────────

def test_verdict_ok_for_clean_extraction():
    quality = assess_extraction_quality(RICH_TEXT, source_type="web_url")
    verdict = capture_quality_verdict(quality, source_type="web_url")
    assert verdict["status"] == "ok"
    assert verdict["reason"] is None
    assert verdict["suggestions"] == []
    assert verdict["score"] == quality["score"]
    assert verdict["level"] == quality["level"]


def test_verdict_thin_for_low_quality_with_cta_suggestions():
    quality = assess_extraction_quality(THIN_TEXT, source_type="web_url")
    assert quality["level"] == "low"
    verdict = capture_quality_verdict(quality, source_type="web_url")
    assert verdict["status"] == "thin"
    assert verdict["reason"]
    assert verdict["suggestions"] == ["recapture", "paste_manually", "highlight_source"]
    assert verdict["reason_codes"]  # machine-readable, same codes as the pipeline


def test_verdict_handles_missing_quality_honestly():
    verdict = capture_quality_verdict(None)
    assert verdict["status"] == "thin"
    assert verdict["suggestions"] == list(CAPTURE_SUGGESTIONS_THIN)
    assert verdict["score"] is None


# ── API wiring ───────────────────────────────────────────────────────────────

def test_read_url_response_carries_ok_verdict(tmp_path):
    client, _ = _client(tmp_path)
    r = client.post("/api/browser/read-url", json={"url": "https://example.com/a"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["capture_quality"]["status"] == "ok"
    assert body["capture_quality"]["suggestions"] == []


def test_read_url_thin_extraction_gets_cta_but_still_ingests(tmp_path):
    client, store = _client(tmp_path, fetch_url=lambda url: ("Thin", THIN_TEXT))
    r = client.post("/api/browser/read-url", json={"url": "https://thin.example/x"})
    assert r.status_code == 200, r.text
    body = r.json()
    # Advisory, never gating: the ingest itself succeeded.
    assert body["status"] == "ok"
    assert body["node_id"]
    assert store.get_provenance(body["node_id"])["source_type"] == "web_url"
    cta = body["capture_quality"]
    assert cta["status"] == "thin"
    assert cta["suggestions"] == ["recapture", "paste_manually", "highlight_source"]
    assert cta["reason"]
    # Same schema as the pipeline's own quality annotation.
    assert body["extraction_quality"]["level"] == "low"
    assert cta["score"] == body["extraction_quality"]["score"]


def test_read_url_empty_extraction_returns_thin_verdict(tmp_path):
    client, _ = _client(tmp_path, fetch_url=lambda url: ("T", "   "))
    r = client.post("/api/browser/read-url", json={"url": "https://blank.example"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "empty"
    assert body["capture_quality"]["status"] == "thin"
    assert "recapture" in body["capture_quality"]["suggestions"]


def test_ingest_current_tab_carries_verdict_both_ways(tmp_path):
    client, _ = _client(tmp_path)
    ok = client.post("/api/browser/ingest-current-tab", json={
        "url": "https://example.com/tab", "title": "Tab", "text": RICH_TEXT,
    }).json()
    assert ok["capture_quality"]["status"] == "ok"

    thin = client.post("/api/browser/ingest-current-tab", json={
        "url": "https://example.com/tab2", "title": "Tab2", "text": THIN_TEXT,
    }).json()
    assert thin["status"] == "ok"  # still ingested
    assert thin["capture_quality"]["status"] == "thin"
    assert thin["capture_quality"]["suggestions"] == [
        "recapture", "paste_manually", "highlight_source",
    ]
