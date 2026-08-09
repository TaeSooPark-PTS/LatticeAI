"""Snapshots, indexing, skills, plugins, memory, and onboarding managers."""
from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

from latticeai.core.workspace_os import WorkspaceOSStore


class SnapshotGraph:
    """The read surface ``create_snapshot`` needs, plus an additive import."""

    def __init__(self) -> None:
        self.nodes = [
            {"id": "node:a", "type": "Decision", "title": "Ship it", "summary": "release"},
            {"id": "node:b", "type": "Concept", "title": "Workspace OS", "summary": "core"},
        ]
        self.edges = [{"from": "node:a", "to": "node:b", "type": "depends_on"}]
        self.imported_calls: List[Dict[str, Any]] = []

    def graph(self, limit: int = 2000) -> Dict[str, Any]:
        return {"nodes": list(self.nodes), "edges": list(self.edges)}

    def stats(self) -> Dict[str, Any]:
        return {"nodes": {"Decision": 1, "Concept": 1}, "edges": {"depends_on": 1}, "local_sources": 1}

    def local_sources(self) -> Dict[str, Any]:
        return {"sources": [{"id": "source:one", "label": "Repo"}]}

    def import_graph(self, data: Dict[str, Any], *, mode: str = "merge") -> Dict[str, Any]:
        self.imported_calls.append({"data": data, "mode": mode})
        return {"imported": True, "mode": mode, "nodes": len(data.get("nodes") or [])}


def _store(tmp_path: Path, name: str) -> WorkspaceOSStore:
    target = tmp_path / name
    target.mkdir()
    return WorkspaceOSStore(target)


def _snapshot(store: WorkspaceOSStore, graph: SnapshotGraph, name: str = "before") -> Dict[str, Any]:
    return store.create_snapshot(
        name=name,
        graph=graph,
        history=[{"role": "user", "content": "hello"}],
        settings={"theme": "dark"},
        models={"loaded_models": ["qwen"]},
    )["snapshot"]


def test_snapshots_are_listed_newest_first(tmp_path: Path):
    store = _store(tmp_path, "snap-list")
    graph = SnapshotGraph()
    _snapshot(store, graph, "first")
    _snapshot(store, graph, "second")

    listed = store.list_snapshots()

    assert [item["name"] for item in listed["snapshots"]] == ["second", "first"]
    assert store.list_snapshots(workspace_id="org-other")["snapshots"] == []


def test_a_moved_snapshot_file_is_found_through_its_recorded_path(tmp_path: Path):
    store = _store(tmp_path, "snap-moved")
    meta = _snapshot(store, SnapshotGraph())
    moved = tmp_path / "elsewhere"
    moved.mkdir()
    destination = moved / (meta["id"] + ".json")
    shutil.move(meta["path"], destination)
    state = store.load_state()
    for item in state["snapshots"]:
        if item["id"] == meta["id"]:
            item["path"] = str(destination)
    store.save_state(state)

    loaded = store.get_snapshot(meta["id"])

    assert loaded["id"] == meta["id"]
    assert loaded["chat"] == [{"role": "user", "content": "hello"}]
    with pytest.raises(FileNotFoundError):
        store.get_snapshot("snapshot-ghost")


def test_snapshot_views_slice_the_stored_document_by_area(tmp_path: Path):
    store = _store(tmp_path, "snap-view")
    snapshot_id = _snapshot(store, SnapshotGraph())["id"]

    graph_view = store.snapshot_view(snapshot_id, "graph")
    chat_view = store.snapshot_view(snapshot_id, "chat")
    decision_view = store.snapshot_view(snapshot_id, "decision")
    whole = store.snapshot_view(snapshot_id, "everything")

    assert len(graph_view["graph"]["nodes"]) == 2
    assert graph_view["graph_stats"]["local_sources"] == 1
    assert chat_view["chat"] == [{"role": "user", "content": "hello"}]
    assert [node["id"] for node in decision_view["decisions"]] == ["node:a"]
    assert whole["snapshot"]["settings"] == {"theme": "dark"}


def test_exporting_a_snapshot_writes_a_readable_archive(tmp_path: Path):
    store = _store(tmp_path, "snap-export")
    snapshot_id = _snapshot(store, SnapshotGraph())["id"]

    exported = store.export_snapshot(snapshot_id)

    archive = Path(exported["export_path"])
    assert archive.exists()
    assert exported["bytes"] == archive.stat().st_size
    with zipfile.ZipFile(archive) as zf:
        assert set(zf.namelist()) == {
            "snapshot.json", "graph.json", "chat.json",
            "settings.json", "indexed_folders.json", "models.json",
        }
        assert json.loads(zf.read("models.json"))["loaded_models"] == ["qwen"]


def test_restoring_a_snapshot_merges_instead_of_replacing(tmp_path: Path):
    store = _store(tmp_path, "snap-restore")
    graph = SnapshotGraph()
    snapshot_id = _snapshot(store, graph)["id"]

    restored = store.restore_snapshot(snapshot_id, graph=graph)

    assert restored["restored"] is True
    assert restored["imported"]["mode"] == "merge"
    assert restored["imported"]["nodes"] == 2
    assert graph.imported_calls[0]["mode"] == "merge"
    assert graph.imported["mode"] == "merge"


class IndexingGraph:
    def __init__(self) -> None:
        self.watch_calls: List[Any] = []
        self.removed: List[str] = []

    def stats(self) -> Dict[str, Any]:
        return {"nodes": {"Concept": 3}, "edges": {"relates_to": 2}}

    def local_sources(self) -> Dict[str, Any]:
        return {
            "sources": [
                {
                    "id": "source:one",
                    "label": "Repo",
                    "root_path": "/repo",
                    "status": "indexed",
                    "watch_enabled": True,
                    "include_ocr": False,
                    "last_scanned_at": "2026-01-01T00:00:00",
                    "file_status": {"indexed": 4, "failed": 1, "inaccessible": 2},
                },
                {"id": "source:two", "label": "Notes", "updated_at": "2026-01-02T00:00:00"},
            ]
        }

    def set_local_source_watch(self, source_id: str, enabled: bool) -> Dict[str, Any]:
        self.watch_calls.append((source_id, enabled))
        return {"source_id": source_id, "watch_enabled": enabled}


class RemovableIndexingGraph(IndexingGraph):
    def remove_local_source(self, source_id: str) -> Dict[str, Any]:
        self.removed.append(source_id)
        return {"removed": True, "source_id": source_id}


class FakeWatcher:
    def __init__(self) -> None:
        self.started: List[Dict[str, Any]] = []
        self.stopped: List[str] = []

    def start_source(self, source: Dict[str, Any]) -> Dict[str, Any]:
        self.started.append(source)
        return {"watching": True, "source_id": source.get("id")}

    def stop_source(self, source_id: str) -> Dict[str, Any]:
        self.stopped.append(source_id)
        return {"stopped": True, "source_id": source_id}


def test_indexing_dashboard_without_a_graph_reports_nothing_indexed(tmp_path: Path):
    dashboard = _store(tmp_path, "idx-none").build_indexing_dashboard(None)

    assert dashboard["sources"] == []
    assert dashboard["watcher"] == {"available": False, "active": {}}
    assert dashboard["totals"] == {"success": 0, "failed": 0, "nodes": 0, "edges": 0}


def test_indexing_dashboard_totals_successes_failures_and_watch_state(tmp_path: Path):
    dashboard = _store(tmp_path, "idx-full").build_indexing_dashboard(
        IndexingGraph(),
        {"available": True, "active": {"source:one": {"watching": True}}},
    )

    first, second = dashboard["sources"]
    assert first["success_count"] == 4
    assert first["failure_count"] == 3
    assert first["watch_active"] is True
    assert first["watch_status"] == {"watching": True}
    assert first["last_run_at"] == "2026-01-01T00:00:00"
    assert second["watch_active"] is False
    assert second["last_run_at"] == "2026-01-02T00:00:00"
    assert dashboard["totals"] == {
        "success": 4, "failed": 3, "nodes": 3, "edges": 2, "local_sources": 2,
    }
    assert dashboard["graph_stats"]["nodes"] == {"Concept": 3}


def test_pausing_a_source_stops_its_watch(tmp_path: Path):
    store = _store(tmp_path, "idx-pause")
    graph = IndexingGraph()
    watcher = FakeWatcher()

    watched = store.pause_indexing(graph, "source:one", watcher)
    unwatched = store.pause_indexing(graph, "source:two")

    assert watched["source"]["watch_enabled"] is False
    assert watched["watch"] == {"stopped": True, "source_id": "source:one"}
    assert watcher.stopped == ["source:one"]
    assert unwatched["watch"] == {"stopped": False, "source_id": "source:two"}
    assert "indexing_paused" in [e["event_type"] for e in store.load_state()["timeline"]]


def test_resuming_a_source_starts_its_watch_when_the_source_still_exists(tmp_path: Path):
    store = _store(tmp_path, "idx-resume")
    graph = IndexingGraph()
    watcher = FakeWatcher()

    resumed = store.resume_indexing(graph, "source:one", watcher)
    unknown = store.resume_indexing(graph, "source:missing", watcher)
    without_watcher = store.resume_indexing(graph, "source:one")

    assert resumed["watch"] == {"watching": True, "source_id": "source:one"}
    assert [source["id"] for source in watcher.started] == ["source:one"]
    assert unknown["watch"] == {"watching": False, "source_id": "source:missing"}
    assert without_watcher["watch"] == {"watching": False, "source_id": "source:one"}
    assert graph.watch_calls == [("source:one", True), ("source:missing", True), ("source:one", True)]


def test_removing_a_source_requires_graph_support(tmp_path: Path):
    store = _store(tmp_path, "idx-remove")
    watcher = FakeWatcher()

    with pytest.raises(ValueError, match="does not support removing"):
        store.remove_index_source(IndexingGraph(), "source:one", watcher)

    graph = RemovableIndexingGraph()
    removed = store.remove_index_source(graph, "source:one", watcher)

    assert removed == {"status": "ok", "removed": True, "source_id": "source:one"}
    assert graph.removed == ["source:one"]
    assert watcher.stopped == ["source:one", "source:one"]
    assert "indexing_removed" in [e["event_type"] for e in store.load_state()["timeline"]]


def _skills_tree(tmp_path: Path) -> Path:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "loose-file.md").write_text("not a skill", encoding="utf-8")
    (skills_dir / "no-manifest").mkdir()
    unreadable = skills_dir / "unreadable"
    unreadable.mkdir()
    (unreadable / "SKILL.md").write_bytes(b"\xff\xfe\xfd binary")
    versioned = skills_dir / "versioned"
    versioned.mkdir()
    (versioned / "SKILL.md").write_text("name: versioned\ndescription: does a thing\n", encoding="utf-8")
    (versioned / "schema.json").write_text(json.dumps({"version": "2.1.0"}), encoding="utf-8")
    broken_schema = skills_dir / "broken-schema"
    broken_schema.mkdir()
    (broken_schema / "SKILL.md").write_text("description: still usable\n", encoding="utf-8")
    (broken_schema / "schema.json").write_text("{not json", encoding="utf-8")
    return skills_dir


def test_skill_registry_reads_every_kind_of_installed_directory(tmp_path: Path):
    store = _store(tmp_path, "skills-store")
    skills_dir = _skills_tree(tmp_path)

    registry = store.list_skill_registry(
        skills_dir,
        [
            {"skill": "remote-one", "version": "9.9.9"},
            {"name": "remote-two"},
            {"description": "nameless entries are skipped"},
        ],
    )

    installed = {item["name"]: item for item in registry["installed"]}
    assert set(installed) == {"broken-schema", "unreadable", "versioned"}
    assert installed["versioned"]["version"] == "2.1.0"
    assert installed["versioned"]["description"] == "does a thing"
    assert installed["broken-schema"]["version"] == "local"
    assert installed["unreadable"]["description"] == ""
    assert registry["total_installed"] == 3
    assert [item.get("skill") or item.get("name") for item in registry["available"]] == [
        "remote-one", "remote-two",
    ]
    assert registry["available"][0]["install_status"] == "available"


def test_skills_can_be_toggled_installed_and_uninstalled(tmp_path: Path):
    store = _store(tmp_path, "skills-state")

    disabled = store.set_skill_enabled("demo", False)
    installed = store.mark_skill_installed("demo", version="1.2.0", metadata={"source": "marketplace"})
    uninstalled = store.mark_skill_uninstalled("demo")

    assert disabled["enabled"] is False
    assert installed["installed"] is True
    assert installed["version"] == "1.2.0"
    assert installed["validation_status"] == "ready"
    assert installed["enabled"] is False  # a disabled skill stays disabled after reinstall
    assert uninstalled["installed"] is False
    emitted = [event["event_type"] for event in store.load_state()["timeline"]]
    assert emitted == ["skill_disabled", "skill_installed", "skill_uninstalled"]


def test_uninstalling_a_plugin_disables_it_and_keeps_the_row(tmp_path: Path):
    store = _store(tmp_path, "plugins")
    store.mark_plugin_installed("plugin-a", version="1.0.0")

    result = store.mark_plugin_uninstalled("plugin-a")

    assert result["status"] == "ok"
    assert result["plugin_id"] == "plugin-a"
    assert result["registry"]["installed"] is False
    assert result["registry"]["enabled"] is False
    assert store.list_plugin_registry()["plugin-a"]["installed"] is False


class MemoryGraph:
    def __init__(self, *, refuse: bool = False) -> None:
        self.refuse = refuse
        self.events: List[Dict[str, Any]] = []

    def ingest_event(self, event_type: str, title: str, **kwargs: Any) -> Dict[str, Any]:
        if self.refuse:
            raise RuntimeError("graph refused the event")
        self.events.append({"event_type": event_type, "title": title, **kwargs})
        return {"node_id": "node-" + str(len(self.events))}


def test_memories_require_content_and_a_known_kind(tmp_path: Path):
    store = _store(tmp_path, "memory-guard")

    with pytest.raises(ValueError, match="unknown memory kind"):
        store.upsert_memory(kind="daydream", content="x", user_email=None)
    with pytest.raises(ValueError, match="content is required"):
        store.upsert_memory(kind="workspace", content="   ", user_email=None)


def test_memory_keeps_the_reason_the_graph_refused_it(tmp_path: Path):
    store = _store(tmp_path, "memory-graph")

    record = store.upsert_memory(
        kind="decisions", content="ship on friday", user_email=None, graph=MemoryGraph(refuse=True)
    )

    assert record["graph_error"] == "graph refused the event"
    assert "graph_node_id" not in record
    assert store.get_memory(record["id"])["content"] == "ship on friday"


def test_memories_can_be_filtered_by_person_and_kind(tmp_path: Path):
    store = _store(tmp_path, "memory-filter")
    store.upsert_memory(kind="preferences", content="dark mode", user_email="alice@example.com")
    store.upsert_memory(kind="decisions", content="ship friday", user_email="alice@example.com")
    store.upsert_memory(kind="preferences", content="light mode", user_email="bob@example.com")
    store.upsert_memory(kind="preferences", content="shared default", user_email=None)

    mine = store.list_memories(user_email="alice@example.com")
    prefs = store.list_memories(kind="preferences")

    assert {item["content"] for item in mine["memories"]} == {"dark mode", "ship friday", "shared default"}
    assert len(prefs["memories"]) == 3
    assert store.search_memories("dark")["memories"][0]["content"] == "dark mode"


def test_deleting_an_unknown_memory_is_reported(tmp_path: Path):
    store = _store(tmp_path, "memory-delete")
    record = store.upsert_memory(kind="workspace", content="temporary", user_email=None)

    assert store.delete_memory(record["id"]) == {"status": "ok", "memory_id": record["id"]}
    with pytest.raises(FileNotFoundError):
        store.delete_memory("memory-ghost")
    with pytest.raises(FileNotFoundError):
        store.get_memory(record["id"])


def test_memory_snapshots_can_be_narrowed_to_named_memories(tmp_path: Path):
    store = _store(tmp_path, "memory-snapshot")
    keep = store.upsert_memory(kind="workspace", content="keep me", user_email="alice@example.com")
    store.upsert_memory(kind="workspace", content="leave me", user_email="alice@example.com")

    snapshot = store.create_memory_snapshot(
        label="picked", user_email="alice@example.com", memory_ids=[keep["id"]], reason="test"
    )

    assert snapshot["memory_count"] == 1
    assert [item["content"] for item in snapshot["memories"]] == ["keep me"]
    assert store.list_memory_snapshots()["snapshots"][0]["id"] == snapshot["id"]


def test_computer_activity_records_the_graph_outcome_either_way(tmp_path: Path):
    store = _store(tmp_path, "computer-memory")
    store.configure_computer_memory(
        enabled=True, approved_by="alice@example.com", consent={"approved": True}
    )
    graph = MemoryGraph()

    accepted = store.record_computer_activity({"summary": "opened report.pdf"}, graph)
    refused = store.record_computer_activity({"path": "/tmp/notes.md"}, MemoryGraph(refuse=True))

    assert accepted["status"] == "ok"
    assert "graph_error" not in accepted["activity"]
    assert graph.events[0]["title"] == "opened report.pdf"
    assert refused["activity"]["graph_error"] == "graph refused the event"
    stored = store.load_state()["computer_memory"]["activities"]
    assert len(stored) == 2


def test_onboarding_steps_and_statuses_are_validated(tmp_path: Path):
    store = _store(tmp_path, "onboarding-guard")

    with pytest.raises(ValueError, match="unknown onboarding step"):
        store.update_onboarding_step("teleport")
    with pytest.raises(ValueError, match="unknown onboarding status"):
        store.update_onboarding_step("account", status="halfway")


def test_a_failed_step_keeps_the_person_on_that_step(tmp_path: Path):
    store = _store(tmp_path, "onboarding-failed")

    status = store.update_onboarding_step("hardware", status="failed", error="no GPU found")

    assert status["current_step"] == "hardware"
    assert [step for step in status["steps"] if step["id"] == "hardware"][0]["error"] == "no GPU found"


def test_finishing_onboarding_marks_every_step_and_the_run_complete(tmp_path: Path):
    store = _store(tmp_path, "onboarding-complete")

    status = store.complete_onboarding(data={"finished": True}, user_email="alice@example.com")

    assert status["completed"] is True
    assert status["completed_at"]
    assert status["current_step"] == "complete"
    assert {step["status"] for step in status["steps"]} == {"complete"}
    assert status["steps"][-1]["data"] == {"finished": True}


def test_updating_an_unknown_review_item_is_reported(tmp_path: Path):
    store = _store(tmp_path, "review-items")
    item = store.create_review_item(title="Check the digest", source="workflow_run")

    updated = store.update_review_item(item["id"], status="approved")

    assert updated["status"] == "approved"
    with pytest.raises(FileNotFoundError):
        store.update_review_item("review-ghost", status="approved")
    with pytest.raises(FileNotFoundError):
        store.update_review_item(item["id"], workspace_id="org-other", status="approved")
