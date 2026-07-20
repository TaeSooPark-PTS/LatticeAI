"""v9.8.0 workstream A tests: extraction quality visibility + robust bg jobs.

A1 — pure heuristic extraction quality scoring (length, whitespace,
diversity/repetition, sentence structure, web nav remnants), upstream
confidence override, additive ``extraction_quality``/``warnings`` result
fields, and observation-mode ``quality_gate`` wiring (never skips).

A2 — background job progress (total/processed/failed/errors/timestamps),
per-item error isolation, resume-from-remaining, recent-jobs listing, and the
frozen ``/api/ingestion/*`` HTTP contract on the local-files router.
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from knowledge_graph import KnowledgeGraphStore
from lattice_brain.ingestion import (
    IngestionItem,
    IngestionPipeline,
    assess_extraction_quality,
)
from latticeai.api.browser import create_browser_router
from latticeai.api.local_files import create_local_files_router

GOOD_PROSE = (
    "Lattice AI is a local-first digital brain platform. The knowledge graph "
    "stores every source behind a single ingestion pipeline. Extraction quality "
    "scoring makes bad captures visible to the user. Each sentence here is "
    "normal prose with ordinary punctuation and vocabulary variety."
)

NAV_JUNK = "\n".join(
    ["Home", "Menu", "Login", "Sign up", "About", "Contact", "Search",
     "Subscribe", "Privacy Policy", "Cookie Policy", "Sitemap", "Back to top",
     "Copyright", "All rights reserved", "Footer"]
)


def _store(tmp_path: Path) -> KnowledgeGraphStore:
    return KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")


def _pipeline(tmp_path: Path, **kwargs) -> IngestionPipeline:
    return IngestionPipeline(_store(tmp_path), **kwargs)


# ── A1: assess_extraction_quality (pure heuristics) ──────────────────────────

def test_quality_good_prose_scores_high():
    quality = assess_extraction_quality(GOOD_PROSE, source_type="web_url")
    assert quality["level"] == "high"
    assert quality["score"] >= 0.7
    assert quality["reasons"] == ["clean_extraction"]


def test_quality_empty_text_is_low_with_zero_score():
    quality = assess_extraction_quality("   \n\t ")
    assert quality == {"score": 0.0, "level": "low", "reasons": ["empty_text"]}


def test_quality_repetitive_garbage_is_low():
    quality = assess_extraction_quality("x" * 500)
    assert quality["level"] == "low"
    assert "low_character_diversity" in quality["reasons"]


def test_quality_nav_menu_remnants_flagged_for_web_sources():
    quality = assess_extraction_quality(NAV_JUNK, source_type="web_url")
    assert quality["level"] == "low"
    assert "nav_menu_remnants" in quality["reasons"]
    # Same text from a non-web source uses the generic reason.
    generic = assess_extraction_quality(NAV_JUNK, source_type="file")
    assert "boilerplate_markers" in generic["reasons"]


def test_quality_upstream_confidence_wins():
    quality = assess_extraction_quality(GOOD_PROSE, upstream_confidence=0.1)
    assert quality["level"] == "low"
    assert quality["reasons"] == ["upstream_confidence"]
    # score of 0 is preserved (not treated as falsy/absent) and clamped.
    zero = assess_extraction_quality(GOOD_PROSE, upstream_confidence=0)
    assert zero["score"] == 0.0
    clamped = assess_extraction_quality("junk", upstream_confidence=7)
    assert clamped["score"] == 1.0


def test_quality_repetitive_words_flagged():
    quality = assess_extraction_quality("spam ham " * 40)
    assert "repetitive_words" in quality["reasons"]


# ── A1: pipeline result fields (additive) ────────────────────────────────────

def test_ingest_result_carries_extraction_quality_and_gate(tmp_path):
    pipe = _pipeline(tmp_path)
    res = pipe.ingest(IngestionItem(source_type="web_url", title="t", text=GOOD_PROSE,
                                    source_uri="https://example.com/a"))
    assert res.status == "ok"
    quality = res.extraction_quality
    assert quality is not None
    assert set(quality) == {"score", "level", "reasons"}
    assert 0.0 <= quality["score"] <= 1.0
    assert quality["level"] in {"high", "medium", "low"}
    assert res.warnings == []  # not low quality → no warnings
    gate = res.quality_gate
    assert gate is not None
    assert set(gate) == {"action", "detail"}
    assert gate["action"] in {"ingest", "skip_duplicate", "review"}
    payload = res.as_dict()
    assert payload["extraction_quality"] == quality
    assert payload["quality_gate"] == gate
    assert "warnings" not in payload


def test_low_quality_ingest_adds_korean_warning_but_still_ingests(tmp_path):
    pipe = _pipeline(tmp_path)
    res = pipe.ingest(IngestionItem(source_type="browser_tab", title="junk tab",
                                    text=NAV_JUNK, source_uri="https://example.com/nav"))
    assert res.status == "ok"          # observation only — never blocks
    assert res.node_id
    assert res.extraction_quality["level"] == "low"
    assert res.warnings and "추출 품질이 낮습니다" in res.warnings[0]
    assert res.as_dict()["warnings"] == res.warnings


def test_upstream_confidence_in_metadata_wins_over_heuristics(tmp_path):
    pipe = _pipeline(tmp_path)
    res = pipe.ingest_web_page(
        "https://example.com/confident", GOOD_PROSE,
        metadata={"extraction_confidence": 0.05},
    )
    assert res.status == "ok"
    assert res.extraction_quality["reasons"] == ["upstream_confidence"]
    assert res.extraction_quality["level"] == "low"
    assert res.warnings


def test_extracted_dict_confidence_wins_for_file_items(tmp_path):
    src = tmp_path / "doc.md"
    src.write_text(GOOD_PROSE, encoding="utf-8")
    pipe = _pipeline(tmp_path)
    res = pipe.ingest(IngestionItem(
        source_type="file", path=str(src),
        metadata={"extracted": {"content": GOOD_PROSE, "confidence": 0.92}},
    ))
    assert res.status == "ok"
    assert res.extraction_quality["reasons"] == ["upstream_confidence"]
    assert res.extraction_quality["level"] == "high"


def test_chat_messages_are_not_quality_scored(tmp_path):
    pipe = _pipeline(tmp_path)
    res = pipe.ingest(IngestionItem(source_type="chat_message", text="hi",
                                    metadata={"role": "user"}))
    assert res.status == "ok"
    assert res.extraction_quality is None
    assert res.quality_gate is None
    payload = res.as_dict()
    assert "extraction_quality" not in payload
    assert "quality_gate" not in payload


def test_quality_gate_failure_never_fails_the_ingest(tmp_path):
    pipe = _pipeline(tmp_path)

    def _broken_search(*_args, **_kwargs):
        raise RuntimeError("search backend down")

    pipe._kg.search = _broken_search
    res = pipe.ingest(IngestionItem(source_type="note", title="n", text=GOOD_PROSE))
    assert res.status == "ok"
    # gate_ingest_candidate fail-opens to review on search failure.
    assert res.quality_gate is not None
    assert res.quality_gate["action"] == "review"


# ── A2: background job progress + resume ─────────────────────────────────────

def test_run_background_job_isolates_per_item_errors(tmp_path):
    pipe = _pipeline(tmp_path)
    items = [
        IngestionItem(source_type="note", title="ok-1", text=GOOD_PROSE + " one"),
        IngestionItem(source_type="file", path=str(tmp_path / "missing.md")),
        IngestionItem(source_type="note", title="ok-2", text=GOOD_PROSE + " two"),
    ]
    job = pipe.schedule_background(items, user_email="runner@example.com")
    assert job.status == "queued"
    outcome = pipe.run_background_job(job.job_id)
    assert outcome["status"] == "partial"
    assert outcome["total"] == 3
    assert outcome["processed"] == 2
    assert outcome["failed"] == 1
    assert len(outcome["errors"]) == 1
    assert outcome["errors"][0]["index"] == 1
    assert "missing.md" in outcome["errors"][0]["source"]
    assert outcome["created_at"] and outcome["updated_at"]


def test_resume_processes_only_remaining_items(tmp_path):
    pipe = _pipeline(tmp_path)
    late_file = tmp_path / "late.md"
    items = [
        IngestionItem(source_type="note", title="early", text=GOOD_PROSE + " early"),
        IngestionItem(source_type="file", path=str(late_file)),
    ]
    job = pipe.schedule_background(items, user_email="runner@example.com")
    first = pipe.run_background_job(job.job_id)
    assert first["status"] == "partial"
    assert first["processed"] == 1

    # The failed item's input appears → resume retries only the remaining item.
    late_file.write_text(GOOD_PROSE + " late arrival", encoding="utf-8")
    resumed = pipe.resume_background_job(job.job_id)
    assert resumed["status"] == "completed"
    assert resumed["processed"] == 2
    assert resumed["failed"] == 0
    assert resumed["errors"] == []


def test_background_job_all_failures_reports_failed(tmp_path):
    pipe = _pipeline(tmp_path)
    items = [IngestionItem(source_type="file", path=str(tmp_path / f"no-{i}.md")) for i in range(3)]
    job = pipe.schedule_background(items)
    outcome = pipe.run_background_job(job.job_id)
    assert outcome["status"] == "failed"
    assert outcome["processed"] == 0
    assert outcome["failed"] == 3


def test_background_job_error_records_are_capped(tmp_path):
    pipe = _pipeline(tmp_path)
    items = [IngestionItem(source_type="file", path=str(tmp_path / f"no-{i}.md")) for i in range(5)]
    job = pipe.schedule_background(items)
    job.max_errors = 2
    outcome = pipe.run_background_job(job.job_id)
    assert outcome["failed"] == 5          # keeps counting
    assert len(outcome["errors"]) == 2     # records capped


def test_run_background_job_unknown_id(tmp_path):
    pipe = _pipeline(tmp_path)
    assert pipe.run_background_job("bg_ingest_9999")["status"] == "not_found"


def test_list_background_jobs_newest_first_with_frozen_schema(tmp_path):
    pipe = _pipeline(tmp_path)
    first = pipe.schedule_background([IngestionItem(source_type="note", title="a", text="alpha body one")])
    second = pipe.schedule_background([IngestionItem(source_type="note", title="b", text="beta body two")])
    jobs = pipe.list_background_jobs(limit=10)
    assert [j["job_id"] for j in jobs][:2] == [second.job_id, first.job_id]
    assert set(jobs[0]) == {
        "job_id", "status", "total", "processed", "failed",
        "errors", "created_at", "updated_at",
    }


# ── A2: /api/ingestion HTTP contract ─────────────────────────────────────────

class _Gateway:
    """Permission gateway stub mirroring the approval-dance contract."""

    def __init__(self, user="user@example.com"):
        self.user = user

    def require_local_user(self, request):
        return self.user

    def local_permission_response(self, path, action, user_email, content=""):
        return {
            "permission_required": True,
            "path": path,
            "action": action,
            "approval_token": "test-token",
        }

    def require_local_approval(self, *, token, path, action, user_email, content=""):
        if token != "test-token":
            raise HTTPException(status_code=403, detail="not approved")


def _client(tmp_path, *, pipeline=None, require_user=None, gateway=None):
    store = _store(tmp_path)
    pipe = pipeline or IngestionPipeline(store)
    app = FastAPI()
    app.include_router(create_local_files_router(
        require_user=require_user or (lambda request: "user@example.com"),
        tool_response=lambda fn, *args: fn(*args),
        permission_gateway=gateway or _Gateway(),
        knowledge_graph=pipe._kg,
        require_graph=lambda: None,
        static_dir=tmp_path,
        local_kg_watcher=None,
        ingestion_pipeline=pipe,
        data_dir=tmp_path,
    ))
    return TestClient(app), pipe


def test_jobs_endpoints_require_auth(tmp_path):
    def _deny(request: Request) -> str:
        raise HTTPException(status_code=401, detail="login required")

    client, _pipe = _client(tmp_path, require_user=_deny)
    assert client.get("/api/ingestion/jobs").status_code == 401
    assert client.get("/api/ingestion/jobs/bg_ingest_0001").status_code == 401


def test_jobs_listing_and_detail(tmp_path):
    client, pipe = _client(tmp_path)
    job = pipe.schedule_background([IngestionItem(source_type="note", title="n", text="body for jobs api")])
    listing = client.get("/api/ingestion/jobs")
    assert listing.status_code == 200
    jobs = listing.json()["jobs"]
    assert jobs and jobs[0]["job_id"] == job.job_id
    detail = client.get(f"/api/ingestion/jobs/{job.job_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert set(body) == {
        "job_id", "status", "total", "processed", "failed",
        "errors", "created_at", "updated_at",
    }
    assert client.get("/api/ingestion/jobs/bg_ingest_9999").status_code == 404


def test_folder_endpoint_requires_approval_then_ingests(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.md").write_text(GOOD_PROSE + " file a", encoding="utf-8")
    (corpus / "b.md").write_text(GOOD_PROSE + " file b", encoding="utf-8")
    client, pipe = _client(tmp_path)

    # First call without approval → permission dance payload, nothing ingested.
    denied = client.post("/api/ingestion/folder", json={"path": str(corpus)})
    assert denied.status_code == 200
    assert denied.json()["permission_required"] is True
    assert pipe._kg.stats()["nodes"].get("Document", 0) == 0

    approved = client.post("/api/ingestion/folder", json={
        "path": str(corpus), "approved": True, "approval_token": "test-token",
    })
    assert approved.status_code == 200
    summary = approved.json()
    assert summary["status"] == "ok"
    assert summary["ingested"] == 2


def test_folder_endpoint_background_returns_job_id_and_runs(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.md").write_text(GOOD_PROSE + " bg file a", encoding="utf-8")
    client, pipe = _client(tmp_path)

    res = client.post("/api/ingestion/folder", json={
        "path": str(corpus), "background": True,
        "approved": True, "approval_token": "test-token",
    })
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "scheduled"
    assert body["job_id"]
    # TestClient executes the queued BackgroundTasks before returning.
    detail = client.get(f"/api/ingestion/jobs/{body['job_id']}").json()
    assert detail["status"] == "completed"
    assert detail["processed"] == 1


def test_resume_endpoint_completes_partial_job(tmp_path):
    client, pipe = _client(tmp_path)
    late_file = tmp_path / "late.md"
    job = pipe.schedule_background([
        IngestionItem(source_type="note", title="ok", text=GOOD_PROSE + " resume ok"),
        IngestionItem(source_type="file", path=str(late_file)),
    ])
    pipe.run_background_job(job.job_id)
    assert job.status == "partial"

    late_file.write_text(GOOD_PROSE + " resume late", encoding="utf-8")
    res = client.post(f"/api/ingestion/jobs/{job.job_id}/resume")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "resuming"
    assert body["remaining"] == 1
    detail = client.get(f"/api/ingestion/jobs/{job.job_id}").json()
    assert detail["status"] == "completed"
    assert detail["failed"] == 0

    completed = client.post(f"/api/ingestion/jobs/{job.job_id}/resume")
    assert completed.json()["status"] == "nothing_to_resume"
    assert client.post("/api/ingestion/jobs/bg_ingest_9999/resume").status_code == 404


def test_folder_endpoint_rejects_blank_path(tmp_path):
    client, _pipe = _client(tmp_path)
    res = client.post("/api/ingestion/folder", json={"path": "   "})
    assert res.status_code == 400


# ── A1: browser routes pass the quality fields through ───────────────────────

def test_browser_read_url_response_includes_quality_fields(tmp_path):
    store = _store(tmp_path)
    pipe = IngestionPipeline(store)
    app = FastAPI()
    app.include_router(create_browser_router(
        pipeline=pipe,
        require_user=lambda request: "user@example.com",
        fetch_url=lambda url: ("Example", GOOD_PROSE),
    ))
    client = TestClient(app)
    res = client.post("/api/browser/read-url", json={"url": "https://example.com/post"})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert set(body["extraction_quality"]) == {"score", "level", "reasons"}
    assert body["quality_gate"]["action"] in {"ingest", "skip_duplicate", "review"}


def test_browser_ingest_current_tab_reports_low_quality_warning(tmp_path):
    store = _store(tmp_path)
    pipe = IngestionPipeline(store)
    app = FastAPI()
    app.include_router(create_browser_router(
        pipeline=pipe,
        require_user=lambda request: "user@example.com",
    ))
    client = TestClient(app)
    res = client.post("/api/browser/ingest-current-tab", json={
        "url": "https://example.com/navpage", "title": "Nav page", "text": NAV_JUNK,
    })
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["extraction_quality"]["level"] == "low"
    assert body["warnings"]
