from pathlib import Path

import pytest

from latticeai.core.workspace_os import WORKSPACE_OS_VERSION, WorkspaceOSStore


class FakeGraph:
    def __init__(self):
        self.nodes = [
            {"id": "node:a", "type": "Decision", "title": "Ship 1.0", "summary": "release", "metadata": {}},
            {"id": "node:b", "type": "Feature", "title": "Workspace OS", "summary": "foundation", "metadata": {"filename": "README.md"}},
        ]
        self.edges = [
            {"from": "node:a", "to": "node:b", "type": "depends_on", "weight": 1.0},
        ]
        self.events = []

    def graph(self, limit=2000):
        return {"nodes": list(self.nodes), "edges": list(self.edges)}

    def stats(self):
        return {"nodes": {"Decision": 1, "Feature": 1}, "edges": {"depends_on": 1}, "local_sources": 1}

    def local_sources(self):
        return {
            "sources": [{
                "id": "source:one",
                "label": "Repo",
                "root_path": "/tmp/repo",
                "status": "indexed",
                "watch_enabled": True,
                "last_scanned_at": "2026-05-31T00:00:00",
                "file_status": {"indexed": 2, "failed": 1},
            }]
        }

    def search(self, query, limit=8):
        return {"matches": list(self.nodes)[:limit]}

    def neighbors(self, node_id):
        return {"node_id": node_id, "neighbors": list(self.nodes), "edges": list(self.edges)}

    def set_local_source_watch(self, source_id, enabled):
        return {"source_id": source_id, "watch_enabled": enabled}

    def ingest_event(self, event_type, title, **kwargs):
        node_id = f"event:{len(self.events)}"
        self.events.append({"node_id": node_id, "event_type": event_type, "title": title, **kwargs})
        return {"node_id": node_id}


def test_onboarding_state_is_reentrant(tmp_path: Path):
    store = WorkspaceOSStore(tmp_path)

    first = store.update_onboarding_step("hardware", status="complete", data={"cpu": "apple"})
    second = WorkspaceOSStore(tmp_path).onboarding_status()

    assert first["steps"][2]["status"] == "complete"
    assert second["steps"][2]["data"]["cpu"] == "apple"
    assert second["current_step"] == "model_recommendation"


def test_graph_trace_includes_sources_edges_and_confidence(tmp_path: Path):
    trace = WorkspaceOSStore(tmp_path).build_graph_trace("Workspace OS", FakeGraph(), "context")

    assert trace["confidence"] > 0.5
    assert trace["source_files"][0]["source"] == "README.md"
    assert trace["graph_edges"][0]["type"] == "depends_on"
    assert trace["retrieval_metadata"]["matched_nodes"] == 2


def test_snapshot_compare_reports_knowledge_diff(tmp_path: Path):
    store = WorkspaceOSStore(tmp_path)
    graph = FakeGraph()
    first = store.create_snapshot(name="before", graph=graph, history=[], settings={}, models={})["snapshot"]["id"]
    graph.nodes.append({"id": "node:c", "type": "Decision", "title": "Add Time Machine", "summary": "done", "metadata": {}})
    graph.edges.append({"from": "node:c", "to": "node:b", "type": "relates_to", "weight": 0.7})
    second = store.create_snapshot(name="after", graph=graph, history=[], settings={}, models={})["snapshot"]["id"]

    diff = store.compare_snapshots(first, second)

    assert diff["summary"]["nodes_added"] == 1
    assert diff["summary"]["edges_added"] == 1
    assert diff["summary"]["decisions_changed"] == 1


def test_memory_requires_known_kind_and_links_graph(tmp_path: Path):
    graph = FakeGraph()
    store = WorkspaceOSStore(tmp_path)

    memory = store.upsert_memory(
        kind="preferences",
        content="Prefer autonomous execution",
        user_email="user@example.com",
        graph=graph,
    )

    assert memory["graph_node_id"].startswith("event:")
    assert store.search_memories("autonomous")["memories"][0]["id"] == memory["id"]
    with pytest.raises(ValueError):
        store.upsert_memory(kind="unknown", content="x", user_email=None)


def test_computer_memory_is_off_until_explicit_approval(tmp_path: Path):
    store = WorkspaceOSStore(tmp_path)

    assert store.record_computer_activity({"summary": "change"})["status"] == "ignored"
    with pytest.raises(PermissionError):
        store.configure_computer_memory(enabled=True, approved_by="user@example.com", consent={})

    config = store.configure_computer_memory(
        enabled=True,
        approved_by="user@example.com",
        consent={"approved": True},
    )
    assert config["enabled"] is True
    assert store.record_computer_activity({"summary": "change"})["status"] == "ok"


def test_relationship_explorer_finds_shortest_path(tmp_path: Path):
    result = WorkspaceOSStore(tmp_path).relationship_explorer(FakeGraph(), "node:a", target_id="node:b")

    assert result["outbound"][0]["to"] == "node:b"
    assert result["shortest_path"] == ["node:a", "node:b"]


def test_workspace_summary_exposes_health_timestamp(tmp_path: Path):
    store = WorkspaceOSStore(tmp_path)
    store.record_timeline_event("workspace", "health_check", {})

    summary = store.summary()

    assert summary["version"] == WORKSPACE_OS_VERSION
    assert summary["updated_at"]


def test_skill_registry_reports_install_and_validation_status(tmp_path: Path):
    skills_dir = tmp_path / "skills"
    skill_dir = skills_dir / "demo_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\ndescription: Demo skill\n---\n", encoding="utf-8")

    registry = WorkspaceOSStore(tmp_path).list_skill_registry(
        skills_dir,
        marketplace=[{"skill": "remote_skill", "description": "Remote", "version": "2.0.0", "source": "marketplace"}],
    )

    assert registry["installed"][0]["install_status"] == "ready"
    assert registry["installed"][0]["validation_status"] == "ready"
    assert registry["available"][0]["install_status"] == "available"
    assert registry["available"][0]["validation_status"] == "not_installed"


# ── Organization Workspace foundation (v1.1.0) ───────────────────────────────


def test_default_state_has_personal_workspace_only(tmp_path: Path):
    store = WorkspaceOSStore(tmp_path)
    registry = store.list_workspaces()

    assert registry["active_workspace"] == "personal"
    ids = [ws["workspace_id"] for ws in registry["workspaces"]]
    assert ids == ["personal"]
    personal = registry["workspaces"][0]
    assert personal["type"] == "personal"
    # Personal workspace always grants its local user owner rights.
    assert store.get_member_role("personal", "anyone@example.com") == "owner"


def test_legacy_workspace_state_migrates_non_destructively(tmp_path: Path):
    import json

    # Simulate a 1.0.x state file with the old minimal workspace shape.
    legacy = {
        "version": "1.0.0",
        "active_workspace": "organization",
        "workspaces": {
            "personal": {"id": "personal", "name": "Personal Workspace", "type": "personal"},
            "organization": {"id": "organization", "name": "Org", "type": "organization"},
        },
        "memories": [{"id": "memory-legacy", "kind": "preferences", "content": "old"}],
    }
    (tmp_path / "workspace_os.json").write_text(json.dumps(legacy), encoding="utf-8")

    store = WorkspaceOSStore(tmp_path)
    state = store.load_state()

    # Legacy workspaces upgraded to the full model, data preserved.
    org = state["workspaces"]["organization"]
    assert org["type"] == "organization"
    assert "members" in org and "roles" in org and org["status"] == "active"
    assert state["workspaces"]["personal"]["type"] == "personal"
    # Legacy memory with no workspace_id is treated as Personal.
    assert store.list_memories(workspace_id="personal")["memories"][0]["id"] == "memory-legacy"


def test_create_org_and_role_permissions(tmp_path: Path):
    store = WorkspaceOSStore(tmp_path)
    ws = store.create_organization_workspace(name="Acme", owner_user_id="owner@acme.com")
    wid = ws["workspace_id"]

    assert ws["type"] == "organization"
    assert store.get_member_role(wid, "owner@acme.com") == "owner"

    # Owner adds a viewer and a member.
    store.add_member(wid, user_id="viewer@acme.com", role="viewer", actor="owner@acme.com")
    store.add_member(wid, user_id="member@acme.com", role="member", actor="owner@acme.com")

    assert store.has_permission(wid, "member@acme.com", "write") is True
    assert store.has_permission(wid, "member@acme.com", "manage_members") is False
    assert store.has_permission(wid, "viewer@acme.com", "write") is False
    assert store.has_permission(wid, "viewer@acme.com", "read") is True

    # A viewer cannot manage members.
    with pytest.raises(PermissionError):
        store.add_member(wid, user_id="intruder@acme.com", role="admin", actor="viewer@acme.com")

    # Owner cannot be demoted or removed.
    with pytest.raises(ValueError):
        store.update_member_role(wid, user_id="owner@acme.com", role="member", actor="owner@acme.com")
    with pytest.raises(ValueError):
        store.remove_member(wid, user_id="owner@acme.com", actor="owner@acme.com")

    # Promote member to admin, who can then manage members.
    store.update_member_role(wid, user_id="member@acme.com", role="admin", actor="owner@acme.com")
    store.add_member(wid, user_id="late@acme.com", role="member", actor="member@acme.com")
    assert store.get_member_role(wid, "late@acme.com") == "member"


def test_workspace_scoping_isolates_records(tmp_path: Path):
    store = WorkspaceOSStore(tmp_path)
    org = store.create_organization_workspace(name="Beta", owner_user_id="owner@beta.com")
    wid = org["workspace_id"]

    store.upsert_memory(kind="preferences", content="personal-only", user_email="u@x.com", workspace_id="personal")
    store.upsert_memory(kind="preferences", content="org-only", user_email="owner@beta.com", workspace_id=wid)

    personal = [m["content"] for m in store.list_memories(workspace_id="personal")["memories"]]
    org_memories = [m["content"] for m in store.list_memories(workspace_id=wid)["memories"]]
    everything = [m["content"] for m in store.list_memories()["memories"]]

    assert personal == ["personal-only"]
    assert org_memories == ["org-only"]
    assert set(everything) == {"personal-only", "org-only"}

    # Search respects scope too.
    assert store.search_memories("org-only", workspace_id="personal")["memories"] == []
    assert store.search_memories("org-only", workspace_id=wid)["memories"][0]["content"] == "org-only"


def test_workflow_detail_and_edit_respect_workspace_scope(tmp_path: Path):
    store = WorkspaceOSStore(tmp_path)
    org = store.create_organization_workspace(name="Workflows", owner_user_id="owner@wf.com")
    wid = org["workspace_id"]

    personal = store.create_workflow(name="Personal flow", steps=[], user_email="u@x.com", workspace_id="personal")
    org_flow = store.create_workflow(name="Org flow", steps=[], user_email="owner@wf.com", workspace_id=wid)

    assert store.get_workflow(personal["id"], workspace_id="personal")["name"] == "Personal flow"
    assert store.get_workflow(org_flow["id"], workspace_id=wid)["name"] == "Org flow"
    with pytest.raises(FileNotFoundError):
        store.get_workflow(org_flow["id"], workspace_id="personal")
    with pytest.raises(FileNotFoundError):
        store.update_workflow_definition(org_flow["id"], name="Leaked edit", workspace_id="personal")

    updated = store.update_workflow_definition(org_flow["id"], name="Scoped edit", workspace_id=wid)
    assert updated["name"] == "Scoped edit"


def test_archive_is_soft_and_non_destructive(tmp_path: Path):
    store = WorkspaceOSStore(tmp_path)
    org = store.create_organization_workspace(name="Gamma", owner_user_id="owner@gamma.com")
    wid = org["workspace_id"]
    store.upsert_memory(kind="decisions", content="keep me", user_email="owner@gamma.com", workspace_id=wid)

    archived = store.archive_workspace(wid, actor="owner@gamma.com")
    assert archived["status"] == "archived"
    # Active workspace falls back to personal; the org and its data remain.
    assert store._active_workspace_id() == "personal"
    assert store.list_memories(workspace_id=wid)["memories"][0]["content"] == "keep me"


def test_ownerless_org_is_managed_by_local_user(tmp_path: Path):
    # No-auth local mode: creator has no identity (owner_user_id=None).
    store = WorkspaceOSStore(tmp_path)
    org = store.create_organization_workspace(name="Local", owner_user_id=None)
    wid = org["workspace_id"]

    # The anonymous local user may manage the workspace they created.
    assert store.has_permission(wid, None, "manage_members") is True
    store.add_member(wid, user_id="teammate@local", role="member", actor=None)
    assert store.get_member_role(wid, "teammate@local") == "member"
    # A *named* stranger still gets no implicit role on an ownerless org.
    assert store.get_member_role(wid, "stranger@local") is None


def test_set_active_workspace_requires_membership(tmp_path: Path):
    store = WorkspaceOSStore(tmp_path)
    org = store.create_organization_workspace(name="Delta", owner_user_id="owner@delta.com")
    wid = org["workspace_id"]

    with pytest.raises(PermissionError):
        store.set_active_workspace(wid, user_id="stranger@delta.com")

    activated = store.set_active_workspace(wid, user_id="owner@delta.com")
    assert activated["workspace_id"] == wid
    assert store._active_workspace_id() == wid
