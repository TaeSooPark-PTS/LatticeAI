"""v3.6.0 runtime hook-coverage: KG ingestion paths now fire the unified
pre_tool/post_tool lifecycle — the one honest carry-over gap from v3.5.0.

Exercises the browser ingestion router end-to-end through a real HooksRegistry
(route -> IngestionPipeline -> dispatch_tool) and asserts both that the lifecycle
fires and that a blocking pre_tool hook stops ingestion.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph.store import KnowledgeGraphStore
from lattice_brain.ingestion import IngestionPipeline
from lattice_brain.runtime.hooks import HooksRegistry
from latticeai.api.browser import create_browser_router


@pytest.fixture()
def registry(tmp_path):
    return HooksRegistry(tmp_path / "hooks.json")


def _client(tmp_path, registry):
    store = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    pipeline = IngestionPipeline(store, hooks=registry)
    app = FastAPI()
    app.include_router(create_browser_router(
        pipeline=pipeline,
        require_user=lambda request: "u@x.com",
        fetch_url=lambda url: ("Title", "Readable body for coverage."),
    ))
    return TestClient(app, raise_server_exceptions=False)


def test_read_url_runs_through_pre_and_post_tool(tmp_path, registry):
    events = []
    registry.register_hook("builtin:tool-permission-gate",
                           lambda ctx: events.append(("pre", ctx.event)))
    post = registry.register(name="ingest post probe", kind="post_tool")
    registry.register_hook(post["id"],
                           lambda ctx: events.append(("post", ctx.event, ctx.payload.get("status"))))

    client = _client(tmp_path, registry)
    r = client.post("/api/browser/read-url", json={"url": "https://example.com/a"})
    assert r.status_code == 200

    assert any(e[0] == "pre" and e[1].startswith("tool.kg_ingest.") for e in events), events
    assert any(e[0] == "post" and e[1].startswith("tool.kg_ingest.") and e[2] == "ok" for e in events), events


def test_ingest_tab_blocked_by_pre_tool(tmp_path, registry):
    registry.register_hook("builtin:tool-permission-gate", lambda ctx: ctx.block("ingest denied"))
    client = _client(tmp_path, registry)
    r = client.post("/api/browser/ingest-current-tab",
                    json={"url": "https://example.com/b", "text": "blocked body"})
    # The route returns 200 with an honest blocked result (not a 5xx).
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "blocked"
    assert "denied" in (body.get("detail") or "")


def test_coverage_doc_lists_new_paths():
    doc = (Path(__file__).resolve().parents[2] / "docs" / "DEVELOPMENT.md").read_text(
        encoding="utf-8"
    )
    for needle in ["kg_ingest", "read-url", "ingest-current-tab", "IngestionPipeline"]:
        assert needle in doc, f"coverage doc missing {needle}"
