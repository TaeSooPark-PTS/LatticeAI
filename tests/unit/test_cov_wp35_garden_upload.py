"""wp35: P-Reinforce garden vault + document upload pipeline.

``PReinforceGardener`` keeps its vault root in a module global, so every test
rebinds ``BRAIN_DIR`` to ``tmp_path`` before constructing one. The upload
pipeline takes every collaborator as a keyword argument and is driven with
``asyncio.run`` (repo idiom — no pytest-asyncio mode is configured).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi import HTTPException

import latticeai.services.p_reinforce as garden
import latticeai.services.upload_service as upload_mod


class FakeIngestResult:
    def __init__(self, status="ok", detail="", node_id="node-1", provenance_id="prov-1", duplicate=False):
        self.status = status
        self.detail = detail
        self.node_id = node_id
        self.provenance_id = provenance_id
        self.duplicate = duplicate
        self.content_hash = "sha-1"


class FakePipeline:
    def __init__(self, result=None, error=None):
        self._result = result or FakeIngestResult()
        self._error = error
        self.items: list = []

    def ingest(self, item, **kwargs):
        if self._error is not None:
            raise self._error
        self.items.append(item)
        return self._result


def _gardener(tmp_path: Path, monkeypatch, pipeline=None, kg=None):
    monkeypatch.setattr(garden, "BRAIN_DIR", tmp_path)
    return garden.PReinforceGardener(ingestion_pipeline=pipeline, knowledge_graph=kg)


# ── classification + note ingest ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "folder"),
    [
        ("what is a monad", "10_Wiki"),
        ("todo: ship the release", "30_Projects"),
    ],
)
def test_notes_are_classified_without_a_model(tmp_path: Path, monkeypatch, text, folder):
    gardener = _gardener(tmp_path, monkeypatch)

    result = asyncio.run(gardener.process(text))

    assert result["folder"] == folder
    assert result["graph"] == "unavailable"
    assert result["graph_detail"] == "ingestion pipeline not wired"
    assert (tmp_path / folder / result["filename"]).is_file()


def test_note_ingest_failure_is_reported_but_never_raises(tmp_path: Path, monkeypatch):
    gardener = _gardener(
        tmp_path, monkeypatch, pipeline=FakePipeline(error=RuntimeError("brain offline"))
    )

    result = asyncio.run(gardener.process("a plain note"))

    assert result["status"] == "saved"
    assert result["graph"] == "failed"
    assert result["graph_detail"] == "brain offline"


# ── vault import ─────────────────────────────────────────────────────────────


def test_import_vault_without_a_pipeline_is_unavailable(tmp_path: Path, monkeypatch):
    gardener = _gardener(tmp_path, monkeypatch)

    assert gardener.import_vault() == {"status": "unavailable", "imported": 0}


def test_import_vault_counts_unreadable_notes_as_failures(tmp_path: Path, monkeypatch):
    pipeline = FakePipeline()
    gardener = _gardener(tmp_path, monkeypatch, pipeline=pipeline)
    (tmp_path / "00_Raw" / "broken.md").write_text("x", encoding="utf-8")
    (tmp_path / "00_Raw" / "fine.md").write_text("keep me", encoding="utf-8")
    real_read_text = Path.read_text

    def guarded(self, *args, **kwargs):
        if self.name == "broken.md":
            raise OSError(5, "input/output error")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)

    result = gardener.import_vault()

    assert result == {"status": "ok", "imported": 1, "duplicates": 0, "failed": 1}


# ── tree ─────────────────────────────────────────────────────────────────────


def test_tree_skips_files_it_cannot_stat(tmp_path: Path, monkeypatch):
    gardener = _gardener(tmp_path, monkeypatch)
    (tmp_path / "00_Raw" / "boom.md").write_text("x", encoding="utf-8")
    (tmp_path / "00_Raw" / "ok.md").write_text("x", encoding="utf-8")
    real_stat = Path.stat

    def guarded(self, *args, **kwargs):
        if self.name == "boom.md":
            raise OSError(13, "permission denied")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", guarded)

    tree = gardener.get_tree()

    raw = next(folder for folder in tree["folders"] if folder["name"] == "00_Raw")
    assert [f["name"] for f in raw["files"]] == ["ok.md"]
    assert raw["count"] == 1


# ── chat context ─────────────────────────────────────────────────────────────


def test_brain_context_stops_at_the_requested_limit(tmp_path: Path, monkeypatch):
    class KG:
        def search(self, query, limit, **kwargs):
            return {
                "matches": [
                    {"title": "one", "summary": "first", "metadata": {"garden_folder": "10_Wiki"}},
                    {"title": "two", "summary": "second", "metadata": {"pipeline": "p-reinforce"}},
                    {"title": "skipped", "summary": "not a garden note", "metadata": {}},
                ]
            }

    gardener = _gardener(tmp_path, monkeypatch, kg=KG())

    context = gardener.get_relevant_context("release", limit=1)

    assert context == "--- Document: one ---\nfirst"


def test_scoped_brain_context_failure_returns_nothing(tmp_path: Path, monkeypatch):
    class BrokenKG:
        def search(self, query, limit, **kwargs):
            raise RuntimeError("brain search offline")

    gardener = _gardener(tmp_path, monkeypatch, kg=BrokenKG())

    assert gardener.get_relevant_context("release", allowed_workspaces={"personal"}) == ""


def test_vault_scan_stops_at_the_limit(tmp_path: Path, monkeypatch):
    gardener = _gardener(tmp_path, monkeypatch)
    for name in ("a.md", "b.md", "c.md"):
        (tmp_path / "00_Raw" / name).write_text("release notes", encoding="utf-8")

    context = gardener.get_relevant_context("release", limit=1)

    assert context.count("--- Document:") == 1


def test_vault_scan_skips_unreadable_notes(tmp_path: Path, monkeypatch):
    gardener = _gardener(tmp_path, monkeypatch)
    (tmp_path / "00_Raw" / "broken.md").write_text("release notes", encoding="utf-8")
    real_read_text = Path.read_text

    def guarded(self, *args, **kwargs):
        if self.name == "broken.md":
            raise OSError(5, "input/output error")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)

    assert gardener.get_relevant_context("release") == ""


# ── upload_service ───────────────────────────────────────────────────────────


class FakeRequest:
    def __init__(self, headers=None, query=None):
        self.headers = headers or {}
        self.query_params = query or {}


class FakeUpload:
    def __init__(self, filename, content=b"hello", content_type="text/plain"):
        self.filename = filename
        self.content_type = content_type
        self._content = content

    async def read(self):
        return self._content


class FakeHooks:
    def __init__(self, pre_upload=None):
        self.fired: list = []
        self._pre_upload = pre_upload or {}

    def fire_hook(self, name, event, *, payload=None, user_email=None):
        self.fired.append((name, event, payload))
        return self._pre_upload if name == "pre_upload" else {}


def _upload(**overrides) -> Dict[str, Any]:
    audit: list = []
    kwargs: Dict[str, Any] = {
        "request": FakeRequest(query={"workspace_id": " team "}),
        "file": FakeUpload("notes.txt"),
        "current_user": "u@e.co",
        "enable_graph": False,
        "knowledge_graph": None,
        "bytes_match_extension": lambda contents, suffix: True,
        "classify_sensitive_message": lambda message, index: {
            "preview": "hello", "sensitivity": "low", "labels": [],
        },
        "append_audit_event": lambda event, **fields: audit.append((event, fields)),
        "enforce_rate_limit": lambda user, action: None,
    }
    kwargs.update(overrides)
    kwargs["_audit"] = audit
    return kwargs


def _run(kwargs: Dict[str, Any]):
    audit = kwargs.pop("_audit")
    result = asyncio.run(upload_mod.process_uploaded_document(**kwargs))
    return result, audit


def _stub_read_document(monkeypatch, payload=None, error=None, delete_tmp=False):
    seen: list = []

    def fake_read_document(path):
        seen.append(path)
        if delete_tmp:
            Path(path).unlink()
        if error is not None:
            raise error
        return dict(payload or {"content": "hello", "chars": 5})

    monkeypatch.setattr(upload_mod, "read_document", fake_read_document)
    return seen


def test_unsupported_extension_is_refused():
    with pytest.raises(HTTPException) as excinfo:
        _run(_upload(file=FakeUpload("photo.heic")))

    assert excinfo.value.status_code == 400
    assert "지원하지 않는 형식" in excinfo.value.detail


def test_oversized_upload_is_refused():
    with pytest.raises(HTTPException) as excinfo:
        _run(_upload(file=FakeUpload("big.txt", content=b"x" * (10 * 1024 * 1024 + 1))))

    assert excinfo.value.status_code == 400
    assert "너무 큽니다" in excinfo.value.detail


def test_content_that_contradicts_the_extension_is_refused():
    with pytest.raises(HTTPException) as excinfo:
        _run(_upload(bytes_match_extension=lambda contents, suffix: False))

    assert excinfo.value.status_code == 400
    assert "일치하지 않습니다" in excinfo.value.detail


def test_a_pre_upload_hook_can_block_the_whole_pipeline():
    hooks = FakeHooks(pre_upload={"blocked": True, "block_reason": "policy says no"})

    with pytest.raises(HTTPException) as excinfo:
        _run(_upload(hooks=hooks))

    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == "policy says no"
    assert [name for name, _event, _payload in hooks.fired] == ["pre_upload"]


def test_disabled_graph_is_recorded_as_an_ingest_error_not_a_failure(monkeypatch):
    _stub_read_document(monkeypatch)
    hooks = FakeHooks()

    result, audit = _run(_upload(hooks=hooks))

    assert result["knowledge_graph"] == {"error": "graph disabled"}
    assert result["original_filename"] == "notes.txt"
    fired = [name for name, _event, _payload in hooks.fired]
    assert fired == ["pre_upload", "pre_index", "post_index", "post_upload"]
    assert audit[0][0] == "document_upload"
    assert audit[0][1]["graph_node"] is None


def test_a_non_ok_ingestion_result_is_surfaced_as_the_graph_error(monkeypatch):
    _stub_read_document(monkeypatch)
    pipeline = FakePipeline(FakeIngestResult(status="rejected", detail="duplicate content"))

    result, _audit = _run(
        _upload(enable_graph=True, knowledge_graph=object(), ingestion_pipeline=pipeline)
    )

    assert result["knowledge_graph"] == {"error": "duplicate content"}


def test_legacy_graph_path_records_the_node_and_hash(monkeypatch):
    _stub_read_document(monkeypatch)
    captured: Dict[str, Any] = {}

    class LegacyGraph:
        def ingest_document(self, path, **kwargs):
            captured.update(kwargs)
            return {"node_id": "kg-1", "sha256": "abc123"}

    result, audit = _run(
        _upload(
            enable_graph=True,
            knowledge_graph=LegacyGraph(),
            request=FakeRequest(
                headers={"X-Workspace-Id": " team "}, query={"conversation_id": "conv-9"}
            ),
        )
    )

    assert result["knowledge_graph"] == {"node_id": "kg-1", "sha256": "abc123"}
    assert captured["workspace_id"] == "team"
    assert captured["conversation_id"] == "conv-9"
    assert audit[0][1]["graph_node"] == "kg-1"


def test_a_tool_error_while_parsing_becomes_a_400(monkeypatch):
    _stub_read_document(monkeypatch, error=upload_mod.ToolError("unreadable document"))

    with pytest.raises(HTTPException) as excinfo:
        _run(_upload())

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "unreadable document"


def test_a_vanished_temp_file_does_not_break_the_upload(monkeypatch):
    _stub_read_document(monkeypatch, delete_tmp=True)

    result, _audit = _run(_upload())

    assert result["original_filename"] == "notes.txt"
    assert result["knowledge_graph"] == {"error": "graph disabled"}


def test_a_workspace_service_rejection_becomes_a_403():
    class Denying:
        def resolve_write_scope(self, requested, user):
            raise PermissionError(f"'{user}' cannot write to {requested!r}")

    with pytest.raises(HTTPException) as excinfo:
        _run(_upload(workspace_service=Denying()))

    assert excinfo.value.status_code == 403
    assert "cannot write to 'team'" in excinfo.value.detail


def test_pipeline_ingest_records_provenance(monkeypatch):
    _stub_read_document(monkeypatch)
    pipeline = FakePipeline()

    result, _audit = _run(
        _upload(enable_graph=True, knowledge_graph=object(), ingestion_pipeline=pipeline)
    )

    assert result["knowledge_graph"] == {
        "node_id": "node-1",
        "sha256": "sha-1",
        "provenance_id": "prov-1",
    }
    assert pipeline.items[0].source_type == "upload"
    assert pipeline.items[0].workspace_id == "team"
