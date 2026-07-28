import json
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from latticeai.api.admin import create_admin_router
from latticeai.api.invitations import create_invitations_router
from latticeai.core.invitations import InvitationStore
from latticeai.core.policy import (
    capabilities_for_role,
    policy_matrix,
    require_capability,
)
from latticeai.core.sessions import SessionStore
from latticeai.core.users import (
    load_users_file,
    migrate_knowledge_graph_identity,
    stable_user_id,
    user_id_for_email,
)
from latticeai.core.workspace_os import WorkspaceOSStore
from latticeai.services.workspace_service import WorkspaceService


def test_user_uuid_migration_rekeys_users_graph_workspaces_and_sessions(tmp_path: Path):
    users_path = tmp_path / "users.json"
    users_path.write_text(json.dumps({
        "Owner@Example.COM": {"password": "hashed", "name": "Owner", "nickname": "Owner", "role": "admin"},
        "Member@Example.COM": {"password": "hashed", "name": "Member", "nickname": "Member", "role": "user"},
    }), encoding="utf-8")

    store = WorkspaceOSStore(tmp_path)
    org = store.create_organization_workspace(name="Acme", owner_user_id="owner@example.com")
    store.add_member(org["id"], user_id="member@example.com", role="member", actor="owner@example.com")

    db_path = tmp_path / "knowledge_graph.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS nodes_v2(id TEXT PRIMARY KEY, owner_id TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS edges_v2(id TEXT PRIMARY KEY, created_by TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS ingestion_provenance(id TEXT PRIMARY KEY, owner TEXT)")
        conn.execute("INSERT INTO nodes_v2(id, owner_id) VALUES('n1', 'Owner@Example.COM')")
        conn.execute("INSERT INTO edges_v2(id, created_by) VALUES('e1', 'Member@Example.COM')")
        conn.execute("INSERT INTO ingestion_provenance(id, owner) VALUES('p1', 'OWNER@example.com')")

    users = load_users_file(users_path)
    owner_id = stable_user_id("owner@example.com")
    member_id = stable_user_id("member@example.com")
    assert sorted(users) == ["member@example.com", "owner@example.com"]
    assert users["owner@example.com"]["id"] == owner_id
    assert users["member@example.com"]["id"] == member_id
    assert list(tmp_path.glob("users.json.pre-user-uuid.*.json"))

    email_to_id = {email: user["id"] for email, user in users.items()}
    assert migrate_knowledge_graph_identity(db_path, email_to_id) == 3
    assert store.migrate_workspace_identities(email_to_id) == 3

    state = store.load_state()
    workspace = state["workspaces"][org["id"]]
    assert workspace["owner_user_id"] == owner_id
    assert {member["user_id"] for member in workspace["members"]} == {owner_id, member_id}
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT owner_id FROM nodes_v2 WHERE id='n1'").fetchone()[0] == owner_id
        assert conn.execute("SELECT created_by FROM edges_v2 WHERE id='e1'").fetchone()[0] == member_id
        assert conn.execute("SELECT owner FROM ingestion_provenance WHERE id='p1'").fetchone()[0] == owner_id

    sessions = SessionStore(tmp_path / "session-data")
    token = sessions.create(owner_id, email="owner@example.com")
    assert sessions.get_subject(token) == owner_id
    assert sessions.get_email(token) == "owner@example.com"
    assert user_id_for_email(users, owner_id) == owner_id


def test_policy_matrix_is_the_admin_roles_source():
    require_capability("admin", "admin:users")
    assert "workspace:read" in capabilities_for_role("viewer")

    users = {
        "admin@example.com": {"role": "admin"},
        "member@example.com": {"role": "member"},
    }

    def require_admin(_request: Request):
        return "admin@example.com", users

    app = FastAPI()
    app.include_router(create_admin_router(
        require_admin=require_admin,
        require_user=lambda _request: "admin@example.com",
        load_users=lambda: users,
        save_users=lambda _users: None,
        get_user_role=lambda email, all_users=None: (all_users or users).get(email, {}).get("role", "user"),
        get_history=lambda: [],
        get_audit_log=lambda: [],
        public_user=lambda email, user, _users: {"email": email, "role": user.get("role", "user")},
        load_vpc_config=lambda: {},
        save_vpc_config=lambda _config: None,
        build_admin_audit_report=lambda _users: {},
        build_sensitivity_report=lambda _history: {},
        append_audit_event=lambda *_args, **_kwargs: None,
        public_sso_config=lambda *_args, **_kwargs: {},
        save_sso_config=lambda _update: {},
        get_graph_stats=lambda: {},
        enable_graph=False,
        invite_code="invite",
        invite_gate_enabled=False,
        default_port=3000,
        policy_matrix=policy_matrix,
    ))

    roles = TestClient(app).get("/admin/roles").json()["roles"]
    by_role = {item["role"]: item for item in roles}
    assert "admin:users" in by_role["admin"]["caps"]
    assert "workspace:read" in by_role["viewer"]["caps"]
    assert by_role["member"]["members"] == 1


def test_admin_history_and_audit_are_workspace_scoped():
    users = {"admin@example.com": {"role": "admin"}}
    history = [
        {"role": "user", "content": "personal", "timestamp": "2026-06-01T00:00:00", "workspace_id": "personal"},
        {"role": "user", "content": "org", "timestamp": "2026-06-01T00:01:00", "workspace_id": "org-a"},
        {"role": "assistant", "content": "legacy", "timestamp": "2026-06-01T00:02:00"},
    ]
    audit = [
        {"event_type": "chat_message", "role": "user", "timestamp": "2026-06-01T00:00:00", "workspace_id": "personal"},
        {"event_type": "chat_message", "role": "user", "timestamp": "2026-06-01T00:01:00", "workspace_id": "org-a"},
    ]

    def require_admin(_request: Request):
        return "admin@example.com", users

    app = FastAPI()
    app.include_router(create_admin_router(
        require_admin=require_admin,
        require_user=lambda _request: "admin@example.com",
        load_users=lambda: users,
        save_users=lambda _users: None,
        get_user_role=lambda email, all_users=None: (all_users or users).get(email, {}).get("role", "user"),
        get_history=lambda: history,
        get_audit_log=lambda: audit,
        public_user=lambda email, user, _users: {"email": email, "role": user.get("role", "user")},
        load_vpc_config=lambda: {},
        save_vpc_config=lambda _config: None,
        build_admin_audit_report=lambda _users, events=None: {"recent_events": list(events or [])},
        build_sensitivity_report=lambda scoped_history: {"total": len(scoped_history), "items": scoped_history},
        append_audit_event=lambda *_args, **_kwargs: None,
        public_sso_config=lambda *_args, **_kwargs: {},
        save_sso_config=lambda _update: {},
        get_graph_stats=lambda: {},
        enable_graph=False,
        invite_code="invite",
        invite_gate_enabled=False,
        default_port=3000,
    ))

    client = TestClient(app)
    org_summary = client.get("/admin/summary", headers={"X-Workspace-Id": "org-a"}).json()
    personal_summary = client.get("/admin/summary", headers={"X-Workspace-Id": "personal"}).json()
    org_audit = client.get("/admin/audit", headers={"X-Workspace-Id": "org-a"}).json()

    assert org_summary["total_messages"] == 1
    assert personal_summary["total_messages"] == 2  # personal plus legacy-global compatibility
    assert org_audit["recent_events"] == [audit[1]]


def test_admin_audit_filters_and_retention_summary_are_scoped():
    users = {"admin@example.com": {"role": "admin"}}
    audit = [
        {
            "event_type": "chat_message",
            "user_email": "member@example.com",
            "timestamp": "2024-01-01T00:00:00",
            "workspace_id": "org-a",
            "severity": "informational",
        },
        {
            "event_type": "file_access_denied",
            "user_email": "admin@example.com",
            "timestamp": "2026-06-01T00:00:00",
            "workspace_id": "org-a",
            "severity": "warning",
            "target": "secrets/.env",
        },
        {
            "event_type": "file_access_denied",
            "user_email": "admin@example.com",
            "timestamp": "2026-06-01T00:00:00",
            "workspace_id": "org-b",
            "severity": "warning",
            "target": "other",
        },
    ]

    def require_admin(_request: Request):
        return "admin@example.com", users

    app = FastAPI()
    app.include_router(create_admin_router(
        require_admin=require_admin,
        require_user=lambda _request: "admin@example.com",
        load_users=lambda: users,
        save_users=lambda _users: None,
        get_user_role=lambda email, all_users=None: (all_users or users).get(email, {}).get("role", "user"),
        get_history=lambda: [],
        get_audit_log=lambda: audit,
        public_user=lambda email, user, _users: {"email": email, "role": user.get("role", "user")},
        load_vpc_config=lambda: {},
        save_vpc_config=lambda _config: None,
        build_admin_audit_report=lambda _users, events=None: {"recent_events": list(events or [])},
        build_sensitivity_report=lambda _history: {},
        append_audit_event=lambda *_args, **_kwargs: None,
        public_sso_config=lambda *_args, **_kwargs: {},
        save_sso_config=lambda _update: {},
        get_graph_stats=lambda: {},
        enable_graph=False,
        invite_code="invite",
        invite_gate_enabled=False,
        default_port=3000,
    ))

    client = TestClient(app)
    filtered = client.get(
        "/admin/audit?q=secrets&severity=warning",
        headers={"X-Workspace-Id": "org-a"},
    ).json()
    retention = client.get("/admin/log-retention", headers={"X-Workspace-Id": "org-a"}).json()

    assert filtered["recent_events"] == [audit[1]]
    assert filtered["filters"]["matched_events"] == 1
    assert filtered["filters"]["scoped_events"] == 2
    assert retention["total_events"] == 2
    assert retention["export_before_prune"] is True


def test_invitation_create_accept_and_expire(tmp_path: Path):
    owner_email = "owner@example.com"
    member_email = "member@example.com"
    owner_id = stable_user_id(owner_email)
    member_id = stable_user_id(member_email)
    users = {
        owner_email: {"id": owner_id, "role": "admin"},
        member_email: {"id": member_id, "role": "user"},
    }
    user_lookup = lambda email: user_id_for_email(users, email)

    store = WorkspaceOSStore(tmp_path)
    service = WorkspaceService(store, resolve_user_id=user_lookup)
    org = service.create_organization_workspace(name="Acme", owner_user_id=owner_email)
    invitations = InvitationStore(tmp_path / "invitations.json")
    audit_events = []

    app = FastAPI()
    app.include_router(create_invitations_router(
        invitation_store=invitations,
        workspace_service=service,
        require_admin=lambda _request: (owner_email, users),
        require_user=lambda _request: member_email,
        user_id_for_email=user_lookup,
        append_audit_event=lambda event_type, **payload: audit_events.append((event_type, payload)),
    ))
    client = TestClient(app)

    created = client.post("/invitations", json={
        "email": member_email,
        "workspace_id": org["id"],
        "role": "member",
    })
    assert created.status_code == 200
    token = created.json()["invitation"]["token"]

    accepted = client.post(f"/invitations/{token}/accept")
    assert accepted.status_code == 200
    assert store.has_permission(org["id"], member_id, "read") is True
    assert [event[0] for event in audit_events] == ["invitation_created", "invitation_accepted"]

    expiring = invitations.create(email=None, workspace_id=None, role="viewer", created_by=owner_id)
    raw = json.loads((tmp_path / "invitations.json").read_text(encoding="utf-8"))
    for invitation in raw["invitations"]:
        if invitation["id"] == expiring["id"]:
            invitation["expires_at"] = "2000-01-01T00:00:00"
    (tmp_path / "invitations.json").write_text(json.dumps(raw), encoding="utf-8")
    assert next(item for item in invitations.list() if item["id"] == expiring["id"])["status"] == "expired"
    with pytest.raises(PermissionError):
        invitations.accept(expiring["token"], accepted_by=member_id, email=member_email)


def test_workspace_state_imports_to_sqlite_and_keeps_full_history(tmp_path: Path):
    legacy_state = {
        "identity": "Legacy Workspace",
        "agent_runs": [{
            "id": "legacy-run",
            "workspace_id": "personal",
            "agent_id": "agent:legacy",
            "status": "ok",
            "created_at": "2026-01-01T00:00:00",
        }],
    }
    (tmp_path / "workspace_os.json").write_text(json.dumps(legacy_state), encoding="utf-8")

    store = WorkspaceOSStore(tmp_path)
    imported = store.load_state()
    assert imported["identity"] == "Legacy Workspace"
    assert imported["agent_runs"][0]["id"] == "legacy-run"
    assert list(tmp_path.glob("workspace_os.json.pre-sqlite.*.json"))

    for index in range(320):
        store.record_agent_run(
            agent_id="agent:test",
            status="ok",
            input_text=f"input {index}",
            output_text="done",
            user_email="user@example.com",
            mode="real",
        )

    state = store.load_state()
    assert len(state["agent_runs"]) == 321
    with sqlite3.connect(tmp_path / "knowledge_graph.sqlite") as conn:
        payload = conn.execute(
            "SELECT state_json FROM workspace_os_state WHERE id='current'"
        ).fetchone()[0]
    sqlite_state = json.loads(payload)
    assert len(sqlite_state["agent_runs"]) == 321
    assert json.loads((tmp_path / "workspace_os.json").read_text(encoding="utf-8"))["agent_runs"][-1]["input"] == "input 319"
