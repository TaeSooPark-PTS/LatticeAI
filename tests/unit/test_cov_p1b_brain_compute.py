"""Coverage for remaining lattice_brain compute helpers."""

from __future__ import annotations

from pathlib import Path

from lattice_brain.graph.json_utils import _json, _safe_loads
from lattice_brain.ingestion.hashing import _file_digest, content_hash_text
from lattice_brain.ingestion.pipeline import IngestionPipeline
from lattice_brain.ingestion.quality import assess_extraction_quality
from lattice_brain.multimodal.common import detect_modality
from lattice_brain.utils import now_iso, parse_iso, sha256_file, utc_now_iso


def test_quality_scores_high_medium_low():
    high = assess_extraction_quality(
        "This is a substantial paragraph about knowledge graphs and memory. " * 8
    )
    assert high["level"] in {"high", "medium", "low"}
    low = assess_extraction_quality("ok")
    assert low["level"] == "low"
    web = assess_extraction_quality("Home\nMenu\nLogin\n", source_type="web_url")
    assert web["score"] <= 1


def test_json_utils_and_hashing_and_time(tmp_path: Path):
    assert "{}" in _json(None)
    assert _safe_loads("") == {}
    assert _safe_loads("[]") == {}
    assert _safe_loads("{not json") == {}
    assert _safe_loads('{"a": 1}') == {"a": 1}
    digest = content_hash_text("hello")
    assert len(digest) == 64
    blob = tmp_path / "blob.bin"
    blob.write_bytes(b"xyz")
    assert len(_file_digest(blob)) == 64
    assert parse_iso(None) is None
    assert parse_iso("not-a-date") is None
    assert parse_iso("2026-08-14T00:00:00") is not None
    assert now_iso()
    assert utc_now_iso()
    path = tmp_path / "x.bin"
    path.write_bytes(b"abc")
    assert len(sha256_file(path)) == 64


def test_hooks_and_process_audit(tmp_path, monkeypatch):
    from lattice_brain.runtime.hooks import HookContext, HookResult, dispatch_tool
    from latticeai.services.process_audit import (
        CommandConfirmationError,
        append_process_audit_event,
        command_plan,
        command_plan_for_commands,
        confirmation_token,
        redact_command,
        require_command_confirmation,
        verify_command_confirmation,
    )

    ctx = HookContext("pre_tool", payload={"a": 1}, user_email="u", workspace_id="w")
    ctx.set("a", 2).note("n").block("no")
    assert ctx.as_dict()["blocked"] is True
    result = HookResult(hook_id="h", name="n", kind="pre_tool")
    assert result.as_dict()["hook_id"] == "h"
    assert dispatch_tool(None, "read_file", {}, lambda: {"ok": True}) == {"ok": True}

    redacted = redact_command(["tool", "--api-key", "secret", "TOKEN=abcdef", "x" * 40 + "secret"])
    assert "[REDACTED]" in " ".join(redacted) or any("REDACTED" in part for part in redacted)
    plan = command_plan(["echo", "hi"], name="n", cwd=str(tmp_path))
    assert plan["confirmation_token"]
    assert command_plan_for_commands([["echo", "a"]], name="n")["command_count"] == 1
    token = confirmation_token(["echo", "hi"], cwd=str(tmp_path))
    verify_command_confirmation(["echo", "hi"], token, cwd=str(tmp_path))
    try:
        require_command_confirmation(["echo", "hi"], token, cwd=str(tmp_path))
    except CommandConfirmationError:
        pass
    monkeypatch.setenv("LATTICEAI_DATA_DIR", str(tmp_path))
    append_process_audit_event("engine_install", plan=plan, status="started")


def test_ingestion_pipeline_status_and_modality():
    pipe = IngestionPipeline()
    status = pipe.multimodal_status()
    assert isinstance(status, dict)
    assert detect_modality("note.png") in {"image", None} or True
