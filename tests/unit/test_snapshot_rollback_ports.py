"""ToolDispatch snapshot/restore ports (L7, v9.9.5)."""

from __future__ import annotations

from pathlib import Path

import latticeai.services.tool_dispatch as td


def test_snapshot_and_restore_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(td, "AGENT_ROOT", tmp_path)
    service = td.ToolDispatchService()
    target = tmp_path / "notes.txt"
    target.write_text("before", encoding="utf-8")

    snap = service.snapshot_file("notes.txt")
    assert snap["existed"] is True
    assert snap["content"] == "before"
    assert snap["too_large"] is False

    target.write_text("after", encoding="utf-8")
    restored = service.restore_snapshot("notes.txt", snap["content"])
    assert restored["ok"] is True
    assert restored["action"] == "restored"
    assert target.read_text(encoding="utf-8") == "before"


def test_restore_none_deletes_created_file(tmp_path, monkeypatch):
    monkeypatch.setattr(td, "AGENT_ROOT", tmp_path)
    service = td.ToolDispatchService()
    (tmp_path / "new.py").write_text("x", encoding="utf-8")
    result = service.restore_snapshot("new.py", None)
    assert result["ok"] is True
    assert result["action"] == "deleted"
    assert not (tmp_path / "new.py").exists()


def test_snapshot_rejects_path_escape(tmp_path, monkeypatch):
    monkeypatch.setattr(td, "AGENT_ROOT", tmp_path)
    service = td.ToolDispatchService()
    outside = Path("/tmp/not-in-workspace.txt")
    snap = service.snapshot_file(str(outside))
    assert snap.get("error")


def test_telegram_resume_payload_prefers_token():
    from latticeai.integrations.telegram_bot import _resume_payload

    body = _resume_payload(
        {
            "run_id": "run-1",
            "approval_token": "tok",
            "context_id": "run-1",
            "legacy": True,
            "executing_model": "m1",
            "reviewing_model": "m2",
        },
        approved=True,
    )
    assert body["run_id"] == "run-1"
    assert body["approval_token"] == "tok"
    assert body["approved"] is True
