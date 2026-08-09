"""wp06: the workspace router, built from its factory over a real store.

``create_workspace_router`` had no direct test at all — 319 of its lines never
ran. This module carries the shared harness (real ``WorkspaceOSStore`` +
``WorkspaceService`` under ``tmp_path``, fake auth/graph/skills seams) and
covers the pages, onboarding, indexing, and snapshot surfaces. Sibling files
``test_cov_wp06_workspace_surfaces`` and ``test_cov_wp06_workspace_orgs``
import the harness from here.

The store is real on purpose: workspace_id scoping and the role→permission
gates are the branches that were missing, and faking them would test the fake.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from latticeai.api.workspace import create_workspace_router
from latticeai.core.users import stable_user_id, user_id_for_email
from latticeai.core.workspace_os import WorkspaceOSStore
from latticeai.services.app_context import AppContext
from latticeai.services.workspace_service import WorkspaceService

OWNER = "owner@example.com"
VIEWER = "viewer@example.com"
STRANGER = "stranger@example.com"


class FakeCapabilityRegistry:
    """Stands in for the edition/capability registry the router describes."""

    def describe(self) -> Dict[str, Any]:
        return {"edition": "community", "features": ["workspace", "graph"]}


class FakeGraph:
    """The handful of graph methods the workspace router actually reaches."""

    def __init__(self) -> None:
        self.nodes: List[Dict[str, Any]] = [
            {"id": "node-a", "type": "Decision", "title": "Use MLX"},
            {"id": "node-b", "type": "Entity", "title": "Runtime"},
        ]
        self.edges: List[Dict[str, Any]] = [
            {"from": "node-a", "to": "node-b", "type": "RELATES_TO"},
        ]
        self.sources: List[Dict[str, Any]] = [{
            "id": "src-1",
            "label": "Docs",
            "root_path": "/docs",
            "status": "ready",
            "watch_enabled": True,
            "include_ocr": False,
            "file_status": {"indexed": 3, "failed": 1, "skipped_empty_text": 2},
            "last_scanned_at": "2026-01-01T00:00:00",
        }]
        self.ingested: List[Dict[str, Any]] = []
        self.ingest_error: Optional[str] = None
        self.imports: List[Tuple[Dict[str, Any], str]] = []
        self.watch_calls: List[Tuple[str, bool]] = []
        self.removed: List[str] = []

    def graph(self, limit: int = 500) -> Dict[str, Any]:
        return {"nodes": list(self.nodes), "edges": list(self.edges), "limit": limit}

    def stats(self) -> Dict[str, Any]:
        return {
            "nodes": {"Decision": 1, "Entity": 1},
            "edges": {"RELATES_TO": 1},
            "local_sources": len(self.sources),
        }

    def local_sources(self) -> Dict[str, Any]:
        return {"sources": [dict(item) for item in self.sources]}

    def neighbors(self, node_id: str) -> Dict[str, Any]:
        return {"neighbors": [], "edges": []}

    def ingest_event(self, kind: str, title: str, **kwargs: Any) -> Dict[str, Any]:
        if self.ingest_error:
            raise RuntimeError(self.ingest_error)
        self.ingested.append({"kind": kind, "title": title, **kwargs})
        return {"node_id": "graph-node-%d" % len(self.ingested)}

    def import_graph(self, payload: Dict[str, Any], mode: str = "merge") -> Dict[str, Any]:
        self.imports.append((payload, mode))
        return {"nodes": len(payload.get("nodes") or [])}

    def set_local_source_watch(self, source_id: str, enabled: bool) -> Dict[str, Any]:
        self.watch_calls.append((source_id, enabled))
        for source in self.sources:
            if source["id"] == source_id:
                source["watch_enabled"] = enabled
                return dict(source)
        return {"id": source_id, "watch_enabled": enabled}

    def remove_local_source(self, source_id: str) -> Dict[str, Any]:
        self.removed.append(source_id)
        self.sources = [item for item in self.sources if item["id"] != source_id]
        return {"removed": True, "source_id": source_id}


class FakeWatcher:
    def __init__(self) -> None:
        self.stopped: List[str] = []
        self.started: List[str] = []

    def status(self) -> Dict[str, Any]:
        return {"available": True, "active": {"src-1": {"watching": True}}}

    def stop_source(self, source_id: str) -> Dict[str, Any]:
        self.stopped.append(source_id)
        return {"stopped": True, "source_id": source_id}

    def start_source(self, source: Dict[str, Any]) -> Dict[str, Any]:
        self.started.append(str(source.get("id")))
        return {"watching": True, "source_id": source.get("id")}


class WorkspaceHarness:
    """Builds the router from its factory with a real store and fake seams."""

    def __init__(self, tmp_path: Path, *, graph: bool = True, watcher: bool = True) -> None:
        self.users: Dict[str, Any] = {
            email: {"id": stable_user_id(email), "role": "admin" if email == OWNER else "user"}
            for email in (OWNER, VIEWER, STRANGER)
        }
        self.store = WorkspaceOSStore(tmp_path / "data")
        self.service = WorkspaceService(self.store, resolve_user_id=self.user_id)
        self.graph = FakeGraph() if graph else None
        self.watcher = FakeWatcher() if watcher else None
        self.skills_dir = tmp_path / "skills"
        self.skills_dir.mkdir(parents=True, exist_ok=True)

        # Mutable knobs so a single client can act as different callers.
        self.user: str = OWNER
        self.admin: str = OWNER
        self.audit: List[Tuple[str, Dict[str, Any]]] = []
        self.history: List[Dict[str, Any]] = [{"role": "user", "content": "hello"}]
        self.audit_log: List[Dict[str, Any]] = [
            {"event_type": "chat_completed", "user_email": OWNER, "timestamp": "2026-01-01T00:00:00", "model": "mlx-local"},
            {"event_type": "login", "user_email": VIEWER, "timestamp": "2026-01-02T00:00:00"},
        ]
        self.marketplace: List[Dict[str, Any]] = [{"skill": "summarize", "plugin": "core"}]
        self.marketplace_error: Optional[str] = None
        self.installed: List[Tuple[str, str]] = []
        self.removed_skills: List[str] = []
        self.sysinfo: Dict[str, Any] = {"cpu": "test-cpu", "ram_gb": 32}
        self.environment: Dict[str, Any] = {"os": "test", "ram_gb": 32}

        app = FastAPI()
        app.include_router(create_workspace_router(self.context()))
        self.client = TestClient(app, follow_redirects=False)

    # ── injected seams ───────────────────────────────────────────────────

    def user_id(self, email: Optional[str]) -> Optional[str]:
        return user_id_for_email(self.users, email)

    def _require_graph(self) -> None:
        if self.graph is None:
            raise HTTPException(status_code=503, detail="knowledge graph is disabled")

    async def _local_sysinfo(self, _request: Any) -> Dict[str, Any]:
        return dict(self.sysinfo)

    async def _fetch_marketplace(self) -> List[Dict[str, Any]]:
        if self.marketplace_error:
            raise RuntimeError(self.marketplace_error)
        return [dict(item) for item in self.marketplace]

    async def _install_skill(self, plugin: str, skill: str) -> Dict[str, Any]:
        self.installed.append((plugin, skill))
        return {"status": "installed", "plugin": plugin, "skill": skill}

    def _remove_skill_directory(self, skills_dir: Path, skill: str) -> Dict[str, Any]:
        self.removed_skills.append(skill)
        return {"removed": True, "skill": skill, "dir": str(skills_dir)}

    def context(self) -> AppContext:
        return AppContext(
            skills_dir=self.skills_dir,
            workspace_service=self.service,
            knowledge_graph=self.graph,
            local_kg_watcher=self.watcher,
            capability_registry=FakeCapabilityRegistry(),
            require_user=lambda _request: self.user,
            require_admin=lambda _request: (self.admin, self.users),
            get_current_user=lambda _request: self.user,
            load_users=lambda: dict(self.users),
            append_audit_event=lambda event_type, **payload: self.audit.append((event_type, payload)),
            get_audit_log=lambda: list(self.audit_log),
            get_history=lambda: list(self.history),
            require_graph=self._require_graph,
            workspace_graph=lambda: self.graph,
            graph_stats=lambda: {"nodes": 2, "edges": 1, "disabled": False},
            workspace_models=lambda: {"loaded_models": ["mlx-local"], "default": "mlx-local"},
            workspace_settings=lambda: {"theme": "dark"},
            scan_environment=lambda: dict(self.environment),
            local_sysinfo=self._local_sysinfo,
            get_recommendations=lambda env: [{"model": "qwen3-4b", "ram_gb": env.get("ram_gb")}],
            fetch_skills_marketplace=self._fetch_marketplace,
            install_skill=self._install_skill,
            remove_skill_directory=self._remove_skill_directory,
            redact_secret_text=lambda text: text.replace("SECRET", "[redacted]"),
            local_model="local-model",
            public_model="public-model",
        )

    # ── convenience ──────────────────────────────────────────────────────

    def org(self, name: str = "Acme", *, viewer: bool = False) -> str:
        record = self.service.create_organization_workspace(
            name=name, owner_user_id=OWNER, settings={}
        )
        workspace_id = str(record["workspace_id"])
        if viewer:
            self.service.add_member(workspace_id, user_id=VIEWER, role="viewer", actor=OWNER)
        return workspace_id

    def snapshot(self, *, workspace_id: Optional[str] = None, name: str = "Checkpoint") -> str:
        created = self.store.create_snapshot(
            name=name,
            graph=self.graph,
            history=self.history,
            settings={"theme": "dark"},
            models={"loaded_models": ["mlx-local"]},
            workspace_id=workspace_id,
        )
        return str(created["snapshot"]["id"])


# ── UI pages ────────────────────────────────────────────────────────────────

def test_legacy_workspace_pages_redirect_into_the_app_shell(tmp_path: Path):
    harness = WorkspaceHarness(tmp_path)

    workspace = harness.client.get("/workspace?tab=members")
    onboarding = harness.client.get("/onboarding")

    assert workspace.status_code == 308
    assert workspace.headers["location"] == "/app#/workspace-admin?tab=members"
    assert onboarding.status_code == 308
    assert onboarding.headers["location"] == "/app#/workspace-admin"


# ── summary + onboarding ────────────────────────────────────────────────────

def test_os_summary_merges_graph_models_and_edition(tmp_path: Path):
    harness = WorkspaceHarness(tmp_path)

    payload = harness.client.get("/workspace/os").json()

    assert payload["graph"] == {"nodes": 2, "edges": 1, "disabled": False}
    assert payload["models"]["default"] == "mlx-local"
    assert payload["edition"]["edition"] == "community"
    # WorkspaceService.summary adds the membership-filtered registry.
    assert [item["workspace_id"] for item in payload["workspace_registry"]["workspaces"]] == ["personal"]
    assert payload["shared_global_areas"] == ["graph", "skills"]


def test_onboarding_status_step_and_complete_advance_the_stored_flow(tmp_path: Path):
    harness = WorkspaceHarness(tmp_path)

    initial = harness.client.get("/workspace/onboarding/status").json()
    stepped = harness.client.post(
        "/workspace/onboarding/step",
        json={"step": "account", "status": "complete", "data": {"email": OWNER}},
    ).json()
    completed = harness.client.post(
        "/workspace/onboarding/complete", json={"data": {"source": "test"}}
    ).json()

    assert {item["status"] for item in initial["steps"]} == {"pending"}
    assert initial["has_account"] is True
    assert stepped["current_step"] == "admin"
    assert completed["completed"] is True
    assert {item["status"] for item in completed["steps"]} == {"complete"}
    assert harness.audit[0][0] == "onboarding_complete"


def test_onboarding_hardware_scan_is_recorded_as_a_completed_step(tmp_path: Path):
    harness = WorkspaceHarness(tmp_path)

    payload = harness.client.get("/workspace/onboarding/hardware").json()

    assert payload["environment"] == {"os": "test", "ram_gb": 32}
    assert payload["sysinfo"] == {"cpu": "test-cpu", "ram_gb": 32}
    assert payload["scanned_at"]
    stored = harness.store.load_state()["onboarding"]["steps"]["hardware"]
    assert stored["status"] == "complete"
    assert stored["data"]["environment"]["os"] == "test"
    assert stored["user_email"] == OWNER


def test_onboarding_model_recommendations_attach_the_tri_state_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    harness = WorkspaceHarness(tmp_path)

    class FakeProfile:
        def to_json(self) -> Dict[str, Any]:
            return {
                "os": "darwin",
                "arch": "arm64",
                "ram_mb": 32768,
                "gpu": {"vendor": "apple", "vram_mb": 32768},
            }

    monkeypatch.setattr("latticeai.setup.auto_setup.probe", lambda: FakeProfile())

    payload = harness.client.get("/workspace/onboarding/model-recommendations").json()

    assert payload["default_local_model"] == "local-model"
    assert payload["default_public_model"] == "public-model"
    assert payload["recommendations"] == [{"model": "qwen3-4b", "ram_gb": 32}]
    assert payload["catalog"]["engine"] == "local_mlx"
    assert payload["catalog"]["families"]
    stored = harness.store.load_state()["onboarding"]["steps"]["model_recommendation"]
    assert stored["status"] == "complete"


def test_model_recommendation_catalog_failure_leaves_the_step_usable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    harness = WorkspaceHarness(tmp_path)

    def _boom() -> Any:
        raise RuntimeError("probe unavailable")

    monkeypatch.setattr("latticeai.setup.auto_setup.probe", _boom)

    payload = harness.client.get("/workspace/onboarding/model-recommendations").json()

    assert payload["catalog"] is None
    assert payload["recommendations"] == [{"model": "qwen3-4b", "ram_gb": 32}]


# ── scope gates ─────────────────────────────────────────────────────────────

def test_read_gate_rejects_a_non_member_naming_a_workspace(tmp_path: Path):
    harness = WorkspaceHarness(tmp_path)
    workspace_id = harness.org()
    harness.user = STRANGER

    response = harness.client.get("/workspace/traces", headers={"X-Workspace-Id": workspace_id})

    assert response.status_code == 403
    assert "lacks 'read'" in response.json()["detail"]


def test_write_gate_rejects_a_viewer_who_may_still_read(tmp_path: Path):
    harness = WorkspaceHarness(tmp_path)
    workspace_id = harness.org(viewer=True)
    harness.user = VIEWER
    headers = {"X-Workspace-Id": workspace_id}

    readable = harness.client.get("/workspace/snapshots", headers=headers)
    writable = harness.client.post("/workspace/snapshots", json={"name": "nope"}, headers=headers)

    assert readable.status_code == 200
    assert readable.json() == {"snapshots": []}
    assert writable.status_code == 403
    assert "lacks 'write'" in writable.json()["detail"]


def test_traces_are_listed_within_the_resolved_scope(tmp_path: Path):
    harness = WorkspaceHarness(tmp_path)
    harness.store.record_trace(
        question="why?",
        response="because",
        conversation_id="conv-1",
        user_email=OWNER,
        trace={"nodes": []},
        workspace_id="personal",
    )
    harness.store.record_trace(
        question="elsewhere?",
        response="not here",
        conversation_id="conv-2",
        user_email=OWNER,
        trace={"nodes": []},
        workspace_id=harness.org(),
    )

    scoped = harness.client.get("/workspace/traces?limit=5").json()

    assert [item["question"] for item in scoped["traces"]] == ["why?"]


# ── indexing dashboard ──────────────────────────────────────────────────────

def test_indexing_dashboard_folds_graph_sources_into_watcher_state(tmp_path: Path):
    harness = WorkspaceHarness(tmp_path)

    payload = harness.client.get("/workspace/indexing").json()

    assert payload["watcher"]["available"] is True
    assert payload["totals"]["success"] == 3
    assert payload["totals"]["failed"] == 3
    source = payload["sources"][0]
    assert source["id"] == "src-1"
    assert source["watch_active"] is True


def test_indexing_dashboard_without_a_watcher_reports_no_active_sources(tmp_path: Path):
    harness = WorkspaceHarness(tmp_path, watcher=False)

    payload = harness.client.get("/workspace/indexing").json()

    assert payload["watcher"] == {"available": False, "active": {}}
    assert payload["sources"][0]["watch_active"] is False


def test_indexing_sources_can_be_paused_resumed_and_removed(tmp_path: Path):
    harness = WorkspaceHarness(tmp_path)

    paused = harness.client.post("/workspace/indexing/src-1/pause").json()
    resumed = harness.client.post("/workspace/indexing/src-1/resume").json()
    removed = harness.client.post("/workspace/indexing/src-1/remove").json()

    assert paused["source"]["watch_enabled"] is False
    assert paused["watch"] == {"stopped": True, "source_id": "src-1"}
    assert resumed["source"]["watch_enabled"] is True
    assert resumed["watch"] == {"watching": True, "source_id": "src-1"}
    assert removed == {"status": "ok", "removed": True, "source_id": "src-1"}
    assert harness.graph is not None
    assert harness.graph.removed == ["src-1"]
    assert [event["event_type"] for event in harness.store.load_state()["timeline"]] == [
        "indexing_paused", "indexing_resumed", "indexing_removed",
    ]


def test_indexing_controls_refuse_to_run_without_a_graph(tmp_path: Path):
    harness = WorkspaceHarness(tmp_path, graph=False)

    assert harness.client.post("/workspace/indexing/src-1/pause").status_code == 503
    assert harness.client.post("/workspace/indexing/src-1/resume").status_code == 503
    assert harness.client.post("/workspace/indexing/src-1/remove").status_code == 503


# ── snapshots ───────────────────────────────────────────────────────────────

def test_snapshot_create_and_list_stay_inside_the_write_scope(tmp_path: Path):
    harness = WorkspaceHarness(tmp_path)
    workspace_id = harness.org()
    headers = {"X-Workspace-Id": workspace_id}

    created = harness.client.post("/workspace/snapshots", json={"name": "Org checkpoint"}, headers=headers).json()
    org_list = harness.client.get("/workspace/snapshots", headers=headers).json()
    personal_list = harness.client.get("/workspace/snapshots").json()

    assert created["snapshot"]["workspace_id"] == workspace_id
    assert created["snapshot"]["node_count"] == 2
    assert [item["id"] for item in org_list["snapshots"]] == [created["snapshot"]["id"]]
    assert personal_list["snapshots"] == []
    assert harness.audit[-1][0] == "workspace_snapshot"


def test_snapshot_by_id_reports_missing_and_gates_on_its_own_workspace(tmp_path: Path):
    harness = WorkspaceHarness(tmp_path)
    workspace_id = harness.org()
    snapshot_id = harness.snapshot(workspace_id=workspace_id)

    owned = harness.client.get("/workspace/snapshots/" + snapshot_id)
    missing = harness.client.get("/workspace/snapshots/snapshot-ghost")
    harness.user = STRANGER
    # No header at all: authorization still follows the RECORD's workspace.
    forbidden = harness.client.get("/workspace/snapshots/" + snapshot_id)

    assert owned.status_code == 200
    assert owned.json()["workspace_id"] == workspace_id
    assert missing.status_code == 404
    assert "Snapshot not found" in missing.json()["detail"]
    assert forbidden.status_code == 403
    assert "lacks 'read'" in forbidden.json()["detail"]


def test_snapshot_area_view_returns_slices_and_maps_a_lost_file_to_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    harness = WorkspaceHarness(tmp_path)
    snapshot_id = harness.snapshot()

    graph_area = harness.client.get("/workspace/snapshots/%s/graph" % snapshot_id).json()

    def _vanished(*_args: Any, **_kwargs: Any) -> Dict[str, Any]:
        raise FileNotFoundError(snapshot_id)

    monkeypatch.setattr(harness.store, "snapshot_view", _vanished)
    gone = harness.client.get("/workspace/snapshots/%s/chat" % snapshot_id)

    assert [node["id"] for node in graph_area["graph"]["nodes"]] == ["node-a", "node-b"]
    assert gone.status_code == 404
    assert "Snapshot not found" in gone.json()["detail"]


def test_snapshot_export_writes_a_zip_and_maps_a_lost_file_to_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    harness = WorkspaceHarness(tmp_path)
    snapshot_id = harness.snapshot()

    exported = harness.client.post("/workspace/snapshots/%s/export" % snapshot_id).json()

    def _vanished(*_args: Any, **_kwargs: Any) -> Dict[str, Any]:
        raise FileNotFoundError(snapshot_id)

    monkeypatch.setattr(harness.store, "export_snapshot", _vanished)
    gone = harness.client.post("/workspace/snapshots/%s/export" % snapshot_id)

    assert Path(exported["export_path"]).exists()
    assert exported["bytes"] > 0
    assert harness.audit[-1][0] == "workspace_snapshot_export"
    assert gone.status_code == 404


def test_snapshot_compare_diffs_two_checkpoints_and_maps_a_lost_file_to_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    harness = WorkspaceHarness(tmp_path)
    before = harness.snapshot(name="Before")
    assert harness.graph is not None
    harness.graph.nodes.append({"id": "node-c", "type": "Decision", "title": "Ship it"})
    after = harness.snapshot(name="After")

    diff = harness.client.post(
        "/workspace/snapshots/compare", json={"before_id": before, "after_id": after}
    ).json()

    def _vanished(*_args: Any, **_kwargs: Any) -> Dict[str, Any]:
        raise FileNotFoundError(after)

    monkeypatch.setattr(harness.store, "compare_snapshots", _vanished)
    gone = harness.client.post(
        "/workspace/snapshots/compare", json={"before_id": before, "after_id": after}
    )

    assert [node["id"] for node in diff["nodes_added"]] == ["node-c"]
    assert diff["summary"]["decisions_changed"] == 1
    assert gone.status_code == 404


def test_snapshot_compare_authorizes_each_side_by_its_own_workspace(tmp_path: Path):
    harness = WorkspaceHarness(tmp_path)
    workspace_id = harness.org()
    personal = harness.snapshot(name="Personal")
    org_snapshot = harness.snapshot(workspace_id=workspace_id, name="Org")
    harness.user = STRANGER

    response = harness.client.post(
        "/workspace/snapshots/compare", json={"before_id": personal, "after_id": org_snapshot}
    )

    assert response.status_code == 403


def test_snapshot_restore_merges_the_graph_and_refuses_a_foreign_workspace(tmp_path: Path):
    harness = WorkspaceHarness(tmp_path)
    workspace_id = harness.org()
    personal = harness.snapshot(name="Personal")
    org_snapshot = harness.snapshot(workspace_id=workspace_id, name="Org")

    restored = harness.client.post("/workspace/snapshots/%s/restore" % personal).json()
    # The record belongs to the org workspace but the request resolves to
    # Personal — a by-id restore must not cross that line.
    mismatched = harness.client.post("/workspace/snapshots/%s/restore" % org_snapshot)

    assert restored["restored"] is True
    assert harness.graph is not None
    assert harness.graph.imports[0][1] == "merge"
    assert harness.audit[-1][0] == "workspace_snapshot_restore"
    assert mismatched.status_code == 403
    assert mismatched.json()["detail"] == "snapshot belongs to a different workspace"


def test_snapshot_restore_maps_missing_files_and_conflicts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    harness = WorkspaceHarness(tmp_path)
    snapshot_id = harness.snapshot()

    def _vanished(*_args: Any, **_kwargs: Any) -> Dict[str, Any]:
        raise FileNotFoundError(snapshot_id)

    monkeypatch.setattr(harness.store, "restore_snapshot", _vanished)
    gone = harness.client.post("/workspace/snapshots/%s/restore" % snapshot_id)

    def _conflict(*_args: Any, **_kwargs: Any) -> Dict[str, Any]:
        raise ValueError("a restore is already running")

    monkeypatch.setattr(harness.store, "restore_snapshot", _conflict)
    conflict = harness.client.post("/workspace/snapshots/%s/restore" % snapshot_id)

    assert gone.status_code == 404
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "a restore is already running"


# ── time machine ────────────────────────────────────────────────────────────

def test_time_machine_merges_audit_events_into_the_scoped_timeline(tmp_path: Path):
    harness = WorkspaceHarness(tmp_path)
    harness.snapshot(name="Checkpoint")

    payload = harness.client.get("/workspace/time-machine?limit=50").json()

    areas = {event["area"] for event in payload["events"]}
    assert "snapshot" in areas
    assert "audit" in areas
    assert any(event["event_type"] == "chat_completed" for event in payload["events"])


def test_time_machine_view_returns_an_area_and_maps_a_lost_file_to_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    harness = WorkspaceHarness(tmp_path)
    snapshot_id = harness.snapshot()

    chat = harness.client.get("/workspace/time-machine/%s/chat" % snapshot_id).json()

    def _vanished(*_args: Any, **_kwargs: Any) -> Dict[str, Any]:
        raise FileNotFoundError(snapshot_id)

    monkeypatch.setattr(harness.store, "snapshot_view", _vanished)
    gone = harness.client.get("/workspace/time-machine/%s/chat" % snapshot_id)

    assert chat["chat"] == [{"role": "user", "content": "hello"}]
    assert gone.status_code == 404
