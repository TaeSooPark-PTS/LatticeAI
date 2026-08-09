"""wp06: workspace router surfaces — memory, agents, workflows, skills, VS Code.

Uses the harness from ``test_cov_wp06_workspace_router`` (real store under
``tmp_path``). The interesting lines here are the authorization forks: a
memory delete gated on the record's own workspace, skill install/update
splitting on whether a plugin was named, and the VS Code bridge continuing
after the graph refuses an event.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest

from latticeai.api import workspace as workspace_api
from tests.unit.test_cov_wp06_workspace_router import (
    OWNER,
    STRANGER,
    VIEWER,
    WorkspaceHarness,
)


def _reset_vscode_status(monkeypatch: pytest.MonkeyPatch) -> Dict[str, object]:
    """Isolate the module-level VS Code presence dict for one test."""
    status: Dict[str, object] = {
        "connected": False,
        "status": "offline",
        "index_status": "unknown",
        "last_seen_ms": 0,
    }
    monkeypatch.setattr(workspace_api, "_VSCODE_STATUS", status)
    return status


# ── personal memory ─────────────────────────────────────────────────────────

def test_memories_are_listed_searched_and_filtered_by_kind(tmp_path: Path):
    harness = WorkspaceHarness(tmp_path)

    harness.client.post("/workspace/memories", json={
        "kind": "preferences", "content": "Prefers dark mode", "tags": ["ui"],
    })
    harness.client.post("/workspace/memories", json={
        "kind": "decisions", "content": "Ship on Friday",
    })

    everything = harness.client.get("/workspace/memories").json()
    by_kind = harness.client.get("/workspace/memories?kind=decisions").json()
    found = harness.client.get("/workspace/memories/search?q=dark&limit=5").json()

    assert {item["content"] for item in everything["memories"]} == {"Prefers dark mode", "Ship on Friday"}
    assert [item["content"] for item in by_kind["memories"]] == ["Ship on Friday"]
    assert found["query"] == "dark"
    assert [item["content"] for item in found["memories"]] == ["Prefers dark mode"]


def test_memory_upsert_records_the_scope_and_rejects_an_unknown_kind(tmp_path: Path):
    harness = WorkspaceHarness(tmp_path)
    workspace_id = harness.org()

    created = harness.client.post(
        "/workspace/memories",
        json={"kind": "workspace", "content": "Team standup is 10am", "metadata": {"src": "test"}},
        headers={"X-Workspace-Id": workspace_id},
    )
    rejected = harness.client.post(
        "/workspace/memories", json={"kind": "telepathy", "content": "nope"}
    )

    assert created.status_code == 200
    record = created.json()["memory"]
    assert record["workspace_id"] == workspace_id
    assert record["user_email"] == OWNER
    assert record["graph_node_id"] == "graph-node-1"
    assert record["metadata"]["memory_scope"] == "workspace"
    assert rejected.status_code == 400
    assert rejected.json()["detail"] == "unknown memory kind: telepathy"


def test_memory_delete_needs_ownership_or_write_on_the_records_workspace(tmp_path: Path):
    harness = WorkspaceHarness(tmp_path)
    workspace_id = harness.org()
    owned = harness.store.upsert_memory(
        kind="decisions", content="Org decision", user_email=OWNER, workspace_id=workspace_id
    )

    harness.user = STRANGER
    forbidden = harness.client.delete("/workspace/memories/" + owned["id"])
    missing = harness.client.delete("/workspace/memories/memory-ghost")
    harness.user = OWNER
    deleted = harness.client.delete("/workspace/memories/" + owned["id"])

    assert forbidden.status_code == 403
    assert "lacks 'write'" in forbidden.json()["detail"]
    assert missing.status_code == 404
    assert "Memory not found" in missing.json()["detail"]
    assert deleted.status_code == 200
    assert deleted.json() == {"status": "ok", "memory_id": owned["id"]}
    assert harness.store.load_state()["memories"] == []


def test_memory_delete_maps_a_record_lost_mid_flight_to_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    harness = WorkspaceHarness(tmp_path)
    record = harness.store.upsert_memory(kind="short_term", content="ephemeral", user_email=OWNER)

    def _vanished(*_args: Any, **_kwargs: Any) -> Dict[str, Any]:
        raise FileNotFoundError(record["id"])

    monkeypatch.setattr(harness.store, "delete_memory", _vanished)
    response = harness.client.delete("/workspace/memories/" + record["id"])

    assert response.status_code == 404
    assert "Memory not found" in response.json()["detail"]


# ── agents, runs, relationships ─────────────────────────────────────────────

def test_agent_runs_are_recorded_in_the_write_scope_and_listed_back(tmp_path: Path):
    harness = WorkspaceHarness(tmp_path)
    workspace_id = harness.org()
    headers = {"X-Workspace-Id": workspace_id}

    run = harness.client.post("/workspace/agents/runs", json={
        "agent_id": "agent:planner",
        "status": "ok",
        "input": "plan the release",
        "output": "three steps",
        "timeline": [{"event": "agent_started"}],
        "relationships": ["agent:executor"],
    }, headers=headers).json()["run"]

    scoped = harness.client.get("/workspace/agents", headers=headers).json()
    personal = harness.client.get("/workspace/agents").json()

    assert run["workspace_id"] == workspace_id
    assert run["agent_id"] == "agent:planner"
    assert [item["id"] for item in scoped["runs"]] == [run["id"]]
    assert personal["runs"] == []
    assert [agent["id"] for agent in scoped["agents"]][:1] == ["agent:planner"]


def test_relationship_explorer_walks_the_graph_and_finds_a_path(tmp_path: Path):
    harness = WorkspaceHarness(tmp_path)

    payload = harness.client.get("/workspace/relationships/node-a?target_id=node-b").json()

    assert payload["node"]["title"] == "Use MLX"
    assert [edge["to"] for edge in payload["outbound"]] == ["node-b"]
    assert payload["shortest_path"] == ["node-a", "node-b"]


def test_relationship_explorer_needs_a_graph(tmp_path: Path):
    harness = WorkspaceHarness(tmp_path, graph=False)

    assert harness.client.get("/workspace/relationships/node-a").status_code == 503


# ── local computer memory ───────────────────────────────────────────────────

def test_computer_memory_requires_consent_before_it_records_activity(tmp_path: Path):
    harness = WorkspaceHarness(tmp_path)

    ignored = harness.client.post(
        "/workspace/computer-memory/activity", json={"activity": {"summary": "opened a file"}}
    ).json()
    refused = harness.client.post("/workspace/computer-memory", json={"enabled": True})
    approved = harness.client.post("/workspace/computer-memory", json={
        "enabled": True, "consent": {"approved": True}, "scopes": ["Downloads"],
    })
    recorded = harness.client.post(
        "/workspace/computer-memory/activity", json={"activity": {"summary": "opened a file"}}
    ).json()
    current = harness.client.get("/workspace/computer-memory").json()

    assert ignored["status"] == "ignored"
    assert refused.status_code == 403
    assert "explicit approval" in refused.json()["detail"]
    assert approved.status_code == 200
    assert approved.json()["computer_memory"]["scopes"] == ["Downloads"]
    assert approved.json()["computer_memory"]["approved_by"] == OWNER
    assert recorded["status"] == "ok"
    assert current["enabled"] is True
    assert harness.audit[-1] == ("computer_memory_config", {"user_email": OWNER, "enabled": True})


# ── workflows ───────────────────────────────────────────────────────────────

def test_workflows_are_created_searched_and_appended_to(tmp_path: Path):
    harness = WorkspaceHarness(tmp_path)

    workflow = harness.client.post("/workspace/workflows", json={
        "name": "Nightly index", "steps": [{"action": "scan"}], "metadata": {"cron": "0 2 * * *"},
    }).json()["workflow"]

    matched = harness.client.get("/workspace/workflows?q=nightly").json()
    unmatched = harness.client.get("/workspace/workflows?q=weekly").json()
    evented = harness.client.post(
        "/workspace/workflows/%s/events" % workflow["id"],
        json={"event_type": "started", "payload": {"run": 1}},
    ).json()["workflow"]
    missing = harness.client.post(
        "/workspace/workflows/workflow-ghost/events", json={"event_type": "started"}
    )

    assert workflow["workspace_id"] == "personal"
    assert workflow["graph_node_id"] == "graph-node-1"
    assert [item["id"] for item in matched["workflows"]] == [workflow["id"]]
    assert unmatched["workflows"] == []
    assert [event["type"] for event in evented["events"]] == ["created", "started"]
    assert missing.status_code == 404
    assert "Workflow not found" in missing.json()["detail"]


# ── skills ──────────────────────────────────────────────────────────────────

def test_skill_registry_merges_the_marketplace_and_survives_it_being_down(tmp_path: Path):
    harness = WorkspaceHarness(tmp_path)
    skill_dir = harness.skills_dir / "summarize"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("description: Summarize text\n", encoding="utf-8")

    online = harness.client.get("/workspace/skills").json()
    harness.marketplace_error = "marketplace unreachable"
    offline = harness.client.get("/workspace/skills").json()

    assert [item["name"] for item in online["installed"]] == ["summarize"]
    assert online["installed"][0]["description"] == "Summarize text"
    assert [item["skill"] for item in online["available"]] == ["summarize"]
    assert offline["available"] == []
    assert offline["total_installed"] == 1


def test_skill_install_and_update_split_on_whether_a_plugin_was_named(tmp_path: Path):
    harness = WorkspaceHarness(tmp_path)

    from_plugin = harness.client.post("/workspace/skills/install", json={
        "skill": "summarize", "plugin": "core", "version": "1.0.0",
    }).json()
    recorded_only = harness.client.post("/workspace/skills/install", json={"skill": "local-only"}).json()
    updated = harness.client.post("/workspace/skills/update", json={
        "skill": "summarize", "plugin": "core", "version": "1.1.0",
    }).json()
    version_bump = harness.client.post("/workspace/skills/update", json={"skill": "local-only"}).json()

    assert from_plugin["install"] == {"status": "installed", "plugin": "core", "skill": "summarize"}
    assert from_plugin["skill"]["version"] == "1.0.0"
    assert recorded_only["install"] == {"status": "recorded", "skill": "local-only"}
    assert updated["skill"]["version"] == "1.1.0"
    assert version_bump["update"] == {"status": "version_recorded", "skill": "local-only"}
    assert version_bump["skill"]["version"] == "latest"
    assert harness.installed == [("core", "summarize"), ("core", "summarize")]
    assert [event[0] for event in harness.audit] == [
        "skill_install", "skill_install", "skill_update", "skill_update",
    ]


def test_skills_can_be_disabled_re_enabled_and_uninstalled(tmp_path: Path):
    harness = WorkspaceHarness(tmp_path)
    harness.client.post("/workspace/skills/install", json={"skill": "summarize"})

    disabled = harness.client.post("/workspace/skills/disable", json={"skill": "summarize"}).json()
    enabled = harness.client.post("/workspace/skills/enable", json={"skill": "summarize"}).json()
    removed = harness.client.post("/workspace/skills/uninstall", json={"skill": "summarize"}).json()

    assert disabled["skill"]["enabled"] is False
    assert enabled["skill"]["enabled"] is True
    assert removed["removal"] == {"removed": True, "skill": "summarize", "dir": str(harness.skills_dir)}
    assert removed["skill"]["installed"] is False
    assert harness.removed_skills == ["summarize"]
    assert harness.audit[-1][0] == "skill_uninstall"


# ── audit timeline ──────────────────────────────────────────────────────────

def test_audit_timeline_filters_are_applied_for_admins(tmp_path: Path):
    harness = WorkspaceHarness(tmp_path)

    everything = harness.client.get("/workspace/audit-timeline?limit=10").json()
    by_user = harness.client.get("/workspace/audit-timeline?user=" + VIEWER).json()
    windowed = harness.client.get(
        "/workspace/audit-timeline?event_type=chat&since=2025-12-31T00:00:00&until=2026-01-01T12:00:00&model=mlx-local"
    ).json()

    assert everything["total"] == 2
    assert {event["category"] for event in everything["events"]} == {"model_usage", "security_event"}
    assert [event["user_email"] for event in by_user["events"]] == [VIEWER]
    assert [event["event_type"] for event in windowed["events"]] == ["chat_completed"]


# ── VS Code bridge ──────────────────────────────────────────────────────────

def test_vscode_presence_expires_when_the_extension_stops_reporting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    harness = WorkspaceHarness(tmp_path)
    status = _reset_vscode_status(monkeypatch)

    offline = harness.client.get("/workspace/vscode/status").json()
    posted = harness.client.post("/workspace/vscode/status", json={
        "status": "connected", "index_status": "ready", "workspace_folder": "/repo",
        "extension_version": "1.2.3", "active_file": "main.py", "detail": "ok",
    }).json()
    online = harness.client.get("/workspace/vscode/status").json()

    status["last_seen_ms"] = 1
    stale = harness.client.get("/workspace/vscode/status").json()

    assert offline["connected"] is False
    assert offline["status"] == "offline"
    # The handler returns ``{"status": "ok", **_VSCODE_STATUS}`` — the spread
    # wins, so the reported extension status is what comes back, not "ok".
    assert posted["status"] == "connected"
    assert posted["connected"] is True
    assert posted["user_email"] == OWNER
    assert online["connected"] is True
    assert online["status"] == "connected"
    assert online["index_status"] == "ready"
    assert stale["connected"] is False
    assert stale["status"] == "offline"


def test_vscode_send_creates_a_workflow_and_redacts_the_preview(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    harness = WorkspaceHarness(tmp_path)
    _reset_vscode_status(monkeypatch)

    payload = harness.client.post("/workspace/vscode/send", json={
        "action": "explain",
        "file_path": "app/main.py",
        "language": "python",
        "selection": "token = SECRET",
        "workspace_folder": "/repo",
        "extension_version": "1.2.3",
    }).json()

    workflow = payload["workflow"]
    assert payload["status"] == "ok"
    assert workflow["name"] == "VS Code: explain"
    assert workflow["metadata"]["content_preview"] == "token = [redacted]"
    assert [step["action"] for step in workflow["steps"]] == ["explain", "send_to_lattice"]
    assert harness.graph is not None
    assert harness.graph.ingested[-1]["kind"] == "VSCodeWorkflow"
    assert harness.graph.ingested[-1]["metadata"]["workflow_id"] == workflow["id"]
    assert workspace_api._VSCODE_STATUS["active_file"] == "app/main.py"


def test_vscode_send_keeps_the_workflow_when_the_graph_refuses_the_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    harness = WorkspaceHarness(tmp_path)
    _reset_vscode_status(monkeypatch)
    assert harness.graph is not None
    harness.graph.ingest_error = "graph is read-only"

    payload = harness.client.post("/workspace/vscode/send", json={
        "action": "review", "content": "print(1)", "language": "python",
    }).json()

    workflow = payload["workflow"]
    assert payload["status"] == "ok"
    assert workflow["graph_error"] == "graph is read-only"
    assert harness.graph.ingested == []


def test_vscode_send_without_content_skips_the_graph_entirely(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    harness = WorkspaceHarness(tmp_path)
    _reset_vscode_status(monkeypatch)

    payload = harness.client.post("/workspace/vscode/send", json={"action": "ping"}).json()

    assert payload["workflow"]["steps"][1]["chars"] == 0
    assert harness.graph is not None
    assert [item["kind"] for item in harness.graph.ingested] == ["Workflow"]
