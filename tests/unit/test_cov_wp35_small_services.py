"""wp35: coverage for the small latticeai/services leftovers.

Every service here takes its collaborators by construction or keyword, so the
tests bind fakes at that seam instead of reaching into module globals.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from latticeai.services import app_context as app_context_mod
from latticeai.services import architecture_readiness as arch_mod
from latticeai.services import cloud_egress_audit as egress_mod
from latticeai.services import cloud_token_guard as token_mod
from latticeai.services import funnel_metrics as funnel_mod
from latticeai.services import process_audit as process_mod
from latticeai.services import product_readiness as product_mod
from latticeai.services import setup_detection as setup_mod
from latticeai.services.chat_service import ChatService
from latticeai.services.evidence_actions import EvidenceActionService
from latticeai.services.permission_mode_service import PermissionModeService
from latticeai.services.workspace_service import WorkspaceService

# ── latticeai/services/ingestion.py + kg_portability.py (moved-module shims) ──


def test_moved_module_shims_alias_to_their_physical_modules():
    import lattice_brain.ingestion as ingestion_impl
    import lattice_brain.portability as portability_impl
    import latticeai.services.ingestion as ingestion_shim
    import latticeai.services.kg_portability as portability_shim

    assert ingestion_shim is ingestion_impl
    assert portability_shim is portability_impl


# ── setup_detection ───────────────────────────────────────────────────────────


def test_windows_controllers_skip_nameless_and_unparsable_ram():
    raw = json.dumps(
        [
            {"Name": "  ", "AdapterRAM": 1024},
            {"Name": "Fake GPU", "AdapterRAM": "not-a-number"},
        ]
    )

    controllers = setup_mod.parse_windows_video_controllers(raw)

    assert controllers == [{"name": "Fake GPU", "vram_mb": 0}]


def test_windows_controllers_text_fallback_handles_bad_adapter_ram():
    controllers = setup_mod.parse_windows_video_controllers(
        "Name=Fallback GPU\nAdapterRAM=not-an-int\n"
    )

    assert controllers == [{"name": "Fallback GPU", "vram_mb": 0}]


# ── app_context ───────────────────────────────────────────────────────────────


def test_app_context_require_names_unknown_and_missing_fields():
    ctx = app_context_mod.AppContext()

    with pytest.raises(AttributeError, match="no field 'nope'"):
        ctx.require("nope")
    with pytest.raises(RuntimeError, match="AppContext.chat_service is required"):
        ctx.require("chat_service")


# ── workspace_service ─────────────────────────────────────────────────────────


class _PermStore:
    def __init__(self, permissions, workspaces):
        self._permissions = permissions
        self._workspaces = workspaces

    def has_permission(self, workspace_id, user_id, permission):
        return (workspace_id, permission) in self._permissions

    def load_state(self):
        return {"workspaces": self._workspaces}


def test_workspace_service_can_read_can_write_and_readable_workspaces():
    store = _PermStore(
        permissions={("team", "read"), ("personal", "read"), ("personal", "write")},
        workspaces={"team": {}, "personal": {}, "locked": {}},
    )
    service = WorkspaceService(store)

    assert service.can_read("team", "user:a") is True
    assert service.can_write("team", "user:a") is False
    assert service.can_write("personal", "user:a") is True
    assert sorted(service.readable_workspaces("user:a")) == ["personal", "team"]


# ── permission_mode_service ───────────────────────────────────────────────────


def test_permission_mode_read_survives_corrupt_and_non_dict_state(tmp_path: Path):
    service = PermissionModeService(data_dir=tmp_path)
    path = tmp_path / "permission_mode.json"

    path.write_text("{not json", encoding="utf-8")
    assert service.resolve().value

    path.write_text("[]", encoding="utf-8")
    assert service.resolve().value == service._default.value


def test_permission_mode_set_default_scope_writes_default_key(tmp_path: Path):
    audited: list = []
    service = PermissionModeService(
        data_dir=tmp_path, audit=lambda event, **kw: audited.append((event, kw))
    )

    contract = service.set_mode("trusted")

    stored = json.loads((tmp_path / "permission_mode.json").read_text(encoding="utf-8"))
    assert stored["default"] == "trusted"
    assert contract["mode"] == "trusted"
    assert audited[0][0] == "permission_mode_changed"


# ── chat_service ──────────────────────────────────────────────────────────────


def test_chat_service_degrades_without_optional_writers():
    service = ChatService(store=None, get_history=lambda **kw: [])

    assert service.history_user("a@b.c", "nick") == {}
    assert service.search_history(
        "   ", scope={}, conversation_title=lambda item: "t"
    ) == []

    import asyncio

    with pytest.raises(RuntimeError, match="chat history writer is not configured"):
        asyncio.run(service.persist_entry("user", "hi"))


# ── voice_capture ─────────────────────────────────────────────────────────────


class _ExtensionsTrap(frozenset):
    """Flips a flag the moment the service checks the audio suffix.

    ``capture`` stats the file only after this membership test, so the trap is
    the deterministic seam for making that one ``stat`` fail without breaking
    the ``exists()``/``is_file()`` checks that ran before it.
    """

    def __new__(cls, values, flag):
        instance = super().__new__(cls, values)
        instance.flag = flag
        return instance

    def __contains__(self, item):
        self.flag["armed"] = True
        return super().__contains__(item)


def test_voice_capture_reports_unreadable_audio(tmp_path: Path, monkeypatch):
    from latticeai.services import voice_capture as voice_mod

    audio = tmp_path / "memo.m4a"
    audio.write_bytes(b"\x00" * 16)

    flag = {"armed": False}
    monkeypatch.setattr(
        voice_mod,
        "SUPPORTED_AUDIO_EXTENSIONS",
        _ExtensionsTrap(voice_mod.SUPPORTED_AUDIO_EXTENSIONS, flag),
    )
    real_stat = Path.stat

    def guarded_stat(self, *args, **kwargs):
        if flag["armed"] and str(self) == str(audio):
            raise OSError(13, "permission denied")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", guarded_stat)

    service = voice_mod.VoiceCaptureService(pipeline=object())
    result = service.capture(str(audio))

    assert result["status"] == "failed"
    assert result["error"] == "FILE_NOT_FOUND"
    assert "unreadable" in result["message"]


# ── evidence_actions ──────────────────────────────────────────────────────────


def test_evidence_reader_without_scope_support_that_also_fails():
    calls: list = []

    def reader(node_id, **kwargs):
        calls.append(kwargs)
        if kwargs:
            raise TypeError("reader takes no allowed_workspaces")
        raise RuntimeError("store offline")

    service = EvidenceActionService(node_reader=reader)

    resolved = service.resolve(["node-1"], allowed_workspaces={"personal"})

    assert resolved["missing"] == ["node-1"]
    assert resolved["sources"] == []
    assert calls == [{"allowed_workspaces": {"personal"}}, {}]


# ── funnel_metrics ────────────────────────────────────────────────────────────


def test_funnel_metrics_load_repairs_bad_counters_and_timestamps(tmp_path: Path):
    path = tmp_path / "funnel.json"
    path.write_text(
        json.dumps(
            {
                "file_requests": "not-a-number",
                "first_ingest_at": "2026-01-01T00:00:00",
                "first_value_at": "definitely-not-a-timestamp",
            }
        ),
        encoding="utf-8",
    )

    service = funnel_mod.FunnelMetricsService(path)

    assert service.snapshot()["counters"]["file_requests"] == 0
    # first_value_at is unparsable → no honest TTFV can be derived.
    assert service.ttfv_seconds() is None


def test_funnel_metrics_save_failure_is_swallowed_and_temp_file_kept(
    tmp_path: Path, monkeypatch
):
    path = tmp_path / "funnel.json"
    service = funnel_mod.FunnelMetricsService(path)

    real_replace = os.replace
    real_unlink = os.unlink

    def failing_replace(src, dst, *args, **kwargs):
        if str(dst) == str(path):
            raise OSError(28, "no space left on device")
        return real_replace(src, dst, *args, **kwargs)

    def failing_unlink(target, *args, **kwargs):
        if str(target).startswith(str(tmp_path)):
            raise OSError(1, "operation not permitted")
        return real_unlink(target, *args, **kwargs)

    monkeypatch.setattr(os, "replace", failing_replace)
    monkeypatch.setattr(os, "unlink", failing_unlink)

    service.increment("file_requests")

    # Counter still moved in memory; only persistence failed.
    assert service.snapshot()["counters"]["file_requests"] == 1
    assert not path.exists()


# ── architecture_readiness ────────────────────────────────────────────────────


def test_symbol_exists_classifies_non_symbols_and_bad_dotted_paths():
    assert arch_mod._symbol_exists("") is True
    assert arch_mod._symbol_exists("docs/RELEASE.md::heading") is True
    assert arch_mod._symbol_exists("nodotshere") is False
    assert arch_mod._symbol_exists("latticeai.services.wp35_not_a_module.Thing") is False


def test_forbidden_patterns_reports_missing_file(tmp_path: Path):
    assert arch_mod._forbidden_patterns(tmp_path, "nope.py", ["anything"]) == [
        "missing:nope.py"
    ]


def test_architecture_readiness_defaults_to_the_repository_root():
    report = arch_mod.architecture_readiness()

    assert report["status"] in {"complete", "incomplete"}
    assert report["gates"]


# ── product_readiness ─────────────────────────────────────────────────────────


def test_evidence_with_needle_missing_file_and_unreadable_file(
    tmp_path: Path, monkeypatch
):
    assert product_mod._evidence_resolves(tmp_path, "absent.py::needle") is False

    present = tmp_path / "present.py"
    present.write_text("needle", encoding="utf-8")
    real_read_text = Path.read_text

    def guarded_read_text(self, *args, **kwargs):
        if str(self) == str(present):
            raise OSError(5, "input/output error")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)
    assert product_mod._evidence_resolves(tmp_path, "present.py::needle") is False


def test_product_readiness_folds_incomplete_architecture_into_its_gate(tmp_path: Path):
    report = product_mod.product_readiness(tmp_path)

    architecture = next(
        gate for gate in report["gates"] if gate["id"] == "architecture-closed"
    )
    assert architecture["status"] == "incomplete"
    assert architecture["missing"]
    assert report["status"] == "incomplete"


def test_product_readiness_defaults_to_the_repository_root():
    report = product_mod.product_readiness()

    assert report["status"] in {"complete", "incomplete"}


# ── process_audit ─────────────────────────────────────────────────────────────


def test_redact_command_masks_secret_assignments_and_long_secrets():
    redacted = process_mod.redact_command(
        [
            "installer",
            "API_KEY=super-secret-value",
            "token" + "x" * 40,
            "PATH=/usr/bin",
        ]
    )

    assert redacted[1] == "API_KEY=[REDACTED]"
    assert redacted[2] == "[REDACTED]"
    assert redacted[3] == "PATH=/usr/bin"


def test_append_process_audit_event_never_raises_on_unwritable_path(tmp_path: Path):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a directory", encoding="utf-8")

    process_mod.append_process_audit_event(
        "process_execute",
        plan=process_mod.command_plan(["echo", "hi"], name="echo"),
        status="ok",
        audit_file=blocker / "nested" / "audit.jsonl",
    )

    assert blocker.read_text(encoding="utf-8") == "a file, not a directory"


# ── cloud_token_guard ─────────────────────────────────────────────────────────


def test_token_budget_reads_env_limits_and_falls_back_on_garbage(monkeypatch):
    monkeypatch.setenv("LATTICEAI_CLOUD_MAX_TOKENS_PER_TURN", "1234")
    monkeypatch.setenv("LATTICEAI_CLOUD_MAX_TOKENS_PER_SESSION", "not-an-int")

    budget = token_mod.TokenBudget()

    assert budget.max_tokens_per_turn == 1234
    assert budget.max_tokens_per_session == 50000
    assert budget.check_turn(2000) is not None


# ── cloud_egress_audit ────────────────────────────────────────────────────────


def test_cloud_egress_returns_the_event_when_no_sink_is_bound(monkeypatch):
    monkeypatch.setattr(egress_mod, "_AUDIT", None)

    event = egress_mod.record_cloud_egress(
        node_ids=["n1", "n2"],
        token_estimate=42,
        mode="cloud_allowed",
        provider="fake",
        detail="dry run",
    )

    assert event["event"] == "cloud_egress"
    assert event["node_count"] == 2
    assert event["detail"] == "dry run"
