"""Coverage for the platform-side core modules: project sessions, the realtime
bus, the template marketplace, product-hardening status, the cloud network
boundary, the artifact ledger, document context rendering, the tool policy
factory and the moved-module compatibility shims.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
from pathlib import Path

import pytest

from latticeai.core import context_builder, tool_registry
from latticeai.core.artifact_ledger import ArtifactLedger
from latticeai.core.config import Config
from latticeai.core.document_generator import DocumentGenerationSession
from latticeai.core.marketplace import MarketplaceError, TemplateCatalog
from latticeai.core.network_boundary import is_node_blocked_for_cloud
from latticeai.core.product_hardening import (
    build_product_hardening_status,
    external_integration_status,
)
from latticeai.core.project_sessions import ProjectSessionStore
from latticeai.core.realtime import RealtimeBus, _Subscriber, sse_format

# ── project_sessions ───────────────────────────────────────────────────────


def test_corrupt_session_files_are_skipped_not_fatal(tmp_path):
    store = ProjectSessionStore(tmp_path)
    good = store.create(title="Real project", user_email="owner@example.com")
    (tmp_path / "corrupt-session-id.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "list-shaped.json").write_text("[]", encoding="utf-8")

    assert store.get("corrupt-session-id") is None
    listing = store.list(user_email="owner@example.com")
    assert [item["id"] for item in listing["projects"]] == [good["id"]]
    assert listing["count"] == 1


def test_session_reads_are_scoped_by_workspace(tmp_path):
    store = ProjectSessionStore(tmp_path)
    record = store.create(title="Scoped", user_email="owner@example.com", workspace_id="org:acme")

    assert store.get(record["id"], workspace_id="org:other") is None
    assert store.get(record["id"], workspace_id="org:acme")["title"] == "Scoped"
    assert store.list(workspace_id="org:other")["count"] == 0
    assert store.list(workspace_id="org:acme")["count"] == 1


def test_listing_an_uncreated_root_is_empty(tmp_path):
    store = ProjectSessionStore(tmp_path / "never-created")

    assert store.list() == {"projects": [], "count": 0}


def test_saving_a_record_without_a_valid_id_is_refused(tmp_path):
    store = ProjectSessionStore(tmp_path)

    with pytest.raises(ValueError, match="invalid project session id"):
        store._save({"id": "!!"})


def test_a_failed_write_never_breaks_the_run(tmp_path, monkeypatch):
    import latticeai.core.project_sessions as project_sessions

    def _no_write(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(project_sessions, "atomic_write_json", _no_write)
    store = ProjectSessionStore(tmp_path)

    record = store.create(title="Doomed")

    assert record["title"] == "Doomed"
    assert not list(tmp_path.glob("*.json"))


def test_update_rewrites_title_and_goal(tmp_path):
    store = ProjectSessionStore(tmp_path)
    record = store.create(title="Old", goal="old goal")

    updated = store.update(record["id"], title="New", goal="new goal", status="archived")

    assert (updated["title"], updated["goal"], updated["status"]) == ("New", "new goal", "archived")
    # A blank title keeps the previous one rather than emptying it.
    assert store.update(record["id"], title="   ")["title"] == "New"
    assert store.list(status="active")["count"] == 0
    assert store.list(status="all")["count"] == 1


def test_delete_reports_failure_instead_of_raising(tmp_path, monkeypatch):
    store = ProjectSessionStore(tmp_path)
    record = store.create(title="Doomed")

    def _no_unlink(self, *_args, **_kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "unlink", _no_unlink)
    assert store.delete(record["id"]) is False

    monkeypatch.undo()
    assert store.delete(record["id"]) is True
    assert store.get(record["id"]) is None


def test_recording_a_run_against_an_unknown_session_returns_none(tmp_path):
    store = ProjectSessionStore(tmp_path)

    assert store.record_run("unknown-session-id", run_id="r1") is None


# ── realtime ───────────────────────────────────────────────────────────────


def test_feed_is_a_bounded_ring_buffer():
    bus = RealtimeBus()
    for index in range(205):
        bus.publish({"area": "workspace", "event_type": "tick", "payload": {"i": index}})

    assert bus.stats()["feed_size"] == 200
    newest = bus.recent(limit=1)[0]
    assert newest["payload"]["i"] == 204
    assert newest["seq"] == 205


def test_enqueue_drops_the_event_when_the_queue_cannot_be_drained():
    class _WedgedQueue:
        def put_nowait(self, _event):
            raise asyncio.QueueFull

        def get_nowait(self):
            raise RuntimeError("queue is wedged")

    class _Sub:
        queue = _WedgedQueue()

    # Backpressure must never propagate to the publisher.
    assert RealtimeBus._enqueue(_Sub(), {"event_type": "tick"}) is None


def test_stream_stops_replaying_when_authorization_is_revoked_mid_tail():
    bus = RealtimeBus()
    bus.publish({"area": "workspace", "event_type": "tick", "workspace_id": None})
    calls = {"n": 0}

    def _refresh(_sub):
        calls["n"] += 1
        return calls["n"] < 2

    async def _drive():
        sub = bus.add_subscriber("s-replay")
        return [frame async for frame in bus.stream(sub, refresh_authorization=_refresh)]

    assert asyncio.run(_drive()) == []
    assert bus.stats()["subscribers"] == 0


def test_stream_yields_queued_events_and_stops_on_revocation():
    bus = RealtimeBus()

    async def _drive():
        sub = bus.add_subscriber("s-live")
        sub.queue.put_nowait({"event_type": "first", "workspace_id": None})
        sub.queue.put_nowait({"event_type": "second", "workspace_id": None})
        seen = []

        def _refresh(_sub):
            return len(seen) < 1

        async for frame in bus.stream(sub, heartbeat=0.05, refresh_authorization=_refresh):
            seen.append(frame)
        return seen

    frames = asyncio.run(_drive())

    assert frames == [sse_format({"event_type": "first", "workspace_id": None})]
    assert bus.stats()["subscribers"] == 0


def test_stream_stops_on_revocation_detected_at_heartbeat_time():
    bus = RealtimeBus()
    calls = {"n": 0}

    def _refresh(_sub):
        calls["n"] += 1
        return calls["n"] < 2

    async def _drive():
        sub = bus.add_subscriber("s-heartbeat")
        return [frame async for frame in bus.stream(sub, heartbeat=0.01, refresh_authorization=_refresh)]

    assert asyncio.run(_drive()) == []
    assert calls["n"] == 2
    assert bus.stats()["subscribers"] == 0


def test_presence_heartbeat_bumps_last_seen_for_known_clients():
    bus = RealtimeBus()
    joined = bus.join("client-1", user="owner@example.com", workspace_id="org:acme")

    beat = bus.heartbeat("client-1")

    assert beat["client_id"] == "client-1"
    assert beat["joined_at"] == joined["joined_at"]
    assert beat["last_seen"] >= joined["last_seen"]
    assert bus.heartbeat("client-unknown") is None


def test_subscriber_scope_gates_unscoped_events():
    scoped = _Subscriber("s", {"org:acme"}, "owner@example.com")

    assert scoped.accepts("org:acme") is True
    assert scoped.accepts(None) is False
    assert _Subscriber("open", None, None).accepts(None) is True


# ── marketplace ────────────────────────────────────────────────────────────


def test_catalog_rejects_unknown_kinds_and_missing_templates():
    catalog = TemplateCatalog()

    with pytest.raises(MarketplaceError, match="unknown template kind"):
        catalog.list_templates("spaceships")
    with pytest.raises(MarketplaceError, match="template not found"):
        catalog.get_template("workflow", "does-not-exist")


def test_import_template_validates_the_payload_shape():
    catalog = TemplateCatalog()

    with pytest.raises(MarketplaceError, match="must be an object"):
        catalog.import_template(["not", "an", "object"])
    with pytest.raises(MarketplaceError, match="missing id"):
        catalog.import_template({"kind": "workflow", "name": "No id"})
    with pytest.raises(MarketplaceError, match="missing name"):
        catalog.import_template({"kind": "workflow", "id": "no-name"})

    imported = catalog.import_template({"kind": "workflow", "id": "t1", "name": "T1"})
    assert imported["metadata"]["imported"] is True
    assert imported["version"] == "1.0.0"


# ── product_hardening ──────────────────────────────────────────────────────


def test_integration_status_reads_explicit_env_flags(tmp_path):
    config = Config.from_env({}, base_dir=tmp_path)

    enabled = external_integration_status(config, env={"LATTICEAI_ENABLE_UPDATES": "yes"})
    assert enabled["integrations"]["updates"]["enabled"] is True
    assert enabled["integrations"]["updates"]["automatic_egress"] is True

    disabled = external_integration_status(config, env={"LATTICEAI_ENABLE_UPDATES": "nope"})
    assert disabled["integrations"]["updates"]["enabled"] is False

    # Without an explicit mapping the process environment is the source.
    from_process_env = external_integration_status(config)
    assert from_process_env["integrations"]["telegram"]["enabled"] is False


def test_hardening_status_reports_portability_and_device_identity(tmp_path):
    config = Config.from_env({"LATTICEAI_DATA_DIR": str(tmp_path / "data")}, base_dir=tmp_path)

    class _Portability:
        def available(self):
            return True

        def storage_status(self):
            return {"available": True, "engine": "sqlite"}

        def backup_health(self):
            return {"available": True, "last_backup_at": "2026-01-01T00:00:00"}

    class _DeviceIdentity:
        def describe(self):
            return {"device_id": "device-1"}

    status = build_product_hardening_status(
        config=config, portability=_Portability(), device_identity=_DeviceIdentity()
    )

    assert status["storage"] == {"available": True, "engine": "sqlite"}
    assert status["backup"]["last_backup_at"] == "2026-01-01T00:00:00"
    assert status["device_identity"] == {"device_id": "device-1"}
    assert status["first_run"]["data_dir_exists"] is False

    bare = build_product_hardening_status(config=config)
    assert bare["storage"] == {"available": False}
    assert bare["device_identity"] == {}


# ── network_boundary ───────────────────────────────────────────────────────


def test_sensitive_node_types_and_malformed_metadata():
    assert is_node_blocked_for_cloud({"type": "Credential"}) == (
        "node type 'Credential' is blocked from cloud payloads"
    )
    assert is_node_blocked_for_cloud({"type": "Note", "metadata": {"sensitive": True}}) == (
        "node flagged 'sensitive' is blocked from cloud payloads"
    )
    # A non-mapping metadata blob carries no flags to honour.
    assert is_node_blocked_for_cloud({"type": "Note", "metadata": ["sensitive"]}) is None
    assert is_node_blocked_for_cloud({"type": "Note"}) is None


# ── artifact_ledger ────────────────────────────────────────────────────────


def test_ledger_ignores_empty_records_and_can_be_cleared():
    ledger = ArtifactLedger()

    assert ledger.record([], conversation_id="c1") == []
    assert ledger.record(["   ", {"path": ""}], conversation_id="c1") == []
    assert ledger.recent(conversation_id="c1") == []

    ledger.record(["notes/a.md"], conversation_id="c1")
    assert [item["path"] for item in ledger.recent(conversation_id="c1")] == ["notes/a.md"]

    ledger.clear()
    assert ledger.recent(conversation_id="c1") == []


# ── context_builder ────────────────────────────────────────────────────────


def test_context_trim_cuts_at_the_last_section_boundary():
    body = "x" * 20 + "\n### 관련 문서/파일\n" + "y" * 200

    trimmed, was_trimmed = context_builder._fit_to_budget(body, 10)

    assert was_trimmed is True
    assert trimmed == "x" * 20

    kept, untouched = context_builder._fit_to_budget("short", 100)
    assert (kept, untouched) == ("short", False)


def test_decision_and_task_results_get_their_own_section():
    sections = context_builder._build_context_sections(
        [
            {"id": "n1", "type": "Decision", "title": "Ship v11", "summary": "decided"},
            {"id": "n2", "type": "Task", "title": "Write notes", "summary": ""},
        ],
        {},
        [],
    )

    assert [section["title"] for section in sections] == ["관련 결정사항/작업"]
    assert [item["id"] for item in sections[0]["items"]] == ["n1", "n2"]


def test_render_markdown_skips_empty_sections():
    rendered = context_builder._render_markdown(
        "질문",
        [
            {"title": "빈 섹션", "items": [], "icon": "📄"},
            {
                "title": "관련 개념/기술",
                "icon": "🔗",
                "items": [{"id": "n1", "type": "Concept", "title": "MLX", "summary": "quantization"}],
            },
        ],
    )

    assert "빈 섹션" not in rendered
    assert rendered.startswith("### 🔗 관련 개념/기술")
    assert "- **[Concept] MLX**" in rendered


# ── document_generator ─────────────────────────────────────────────────────


def test_first_document_request_uses_the_plain_generation_prompt():
    session = DocumentGenerationSession()

    first = session.get_system_prompt("graph context here")
    assert session.has_previous is False
    assert "graph context here" in first

    session.update("graph context here", "<html>doc</html>", conversation_id="c1")
    followup = session.get_system_prompt("")
    assert session.has_previous is True
    assert "<html>doc</html>" in followup
    assert followup != first

    session.clear()
    assert session.get_system_prompt("graph context here") == first


# ── tool_registry ──────────────────────────────────────────────────────────


def test_auto_approved_write_policy_differs_only_in_the_approval_flag():
    auto = tool_registry._wa()
    gated = tool_registry._w()

    assert auto["auto_approve"] is True
    assert gated["auto_approve"] is False
    assert {k: v for k, v in auto.items() if k != "auto_approve"} == {
        k: v for k, v in gated.items() if k != "auto_approve"
    }
    assert tool_registry._wa(sandbox="system", rollback="git")["sandbox"] == "system"


# ── moved-module compatibility shims ───────────────────────────────────────


@pytest.mark.parametrize(
    ("shim", "target"),
    [
        ("latticeai.core.graph_curator", "lattice_brain.graph.curator"),
        ("latticeai.core.hooks", "lattice_brain.runtime.hooks"),
        ("latticeai.core.multi_agent", "lattice_brain.runtime.multi_agent"),
    ],
)
def test_legacy_import_paths_alias_the_physical_module(shim, target, monkeypatch):
    physical = importlib.import_module(target)
    monkeypatch.delitem(sys.modules, shim, raising=False)

    reloaded = importlib.import_module(shim)

    # Identity — not a copy — so module-level state and monkeypatching still work.
    assert reloaded is physical
    assert sys.modules[shim] is physical


# ── json round-trip guard for the SSE frame encoder ────────────────────────


def test_sse_format_encodes_non_serializable_values_as_text():
    frame = sse_format({"event_type": "tick", "at": Path("/tmp/x")})

    assert frame.startswith("data: ")
    assert frame.endswith("\n\n")
    assert json.loads(frame[len("data: ") :])["at"] == "/tmp/x"
