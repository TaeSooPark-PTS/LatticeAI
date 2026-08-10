"""wp06: organization workspaces, activation, and membership roles.

Most of these handlers are a three-way error map (404 / 403 / 400) around one
service call, and none of the three arms had ever run. The tests drive the real
``WorkspaceService`` + ``WorkspaceOSStore`` so the role→permission table is what
decides, not a stub.

``get_workspace`` and ``workspace_summary`` are the exception: both check
``read`` permission *before* they look the workspace up, and an unknown id has
no members, so it can never grant read. Those two routes therefore answer 403
for an unknown workspace — the anti-enumeration design — and carry no 404 arm
at all.
"""

from __future__ import annotations

from pathlib import Path

from tests.unit.test_cov_wp06_workspace_router import (
    OWNER,
    STRANGER,
    VIEWER,
    WorkspaceHarness,
)

# ── registry, editions, activation ──────────────────────────────────────────

def test_registry_lists_only_workspaces_the_caller_belongs_to(tmp_path: Path):
    harness = WorkspaceHarness(tmp_path)
    workspace_id = harness.org(viewer=True)

    as_owner = harness.client.get("/workspace/registry").json()
    harness.user = STRANGER
    as_stranger = harness.client.get("/workspace/registry").json()

    assert [item["workspace_id"] for item in as_owner["workspaces"]] == ["personal", workspace_id]
    assert as_owner["roles"] == ["owner", "admin", "member", "viewer"]
    assert as_owner["permissions"]["viewer"] == ["read"]
    assert [item["workspace_id"] for item in as_stranger["workspaces"]] == ["personal"]


def test_editions_endpoint_reports_the_capability_registry(tmp_path: Path):
    harness = WorkspaceHarness(tmp_path)

    payload = harness.client.get("/workspace/editions").json()

    assert payload == {"edition": "community", "features": ["workspace", "graph"]}


def test_activation_switches_the_active_workspace_and_guards_membership(tmp_path: Path):
    harness = WorkspaceHarness(tmp_path)
    workspace_id = harness.org()

    activated = harness.client.post("/workspace/activate", json={"workspace_id": workspace_id})
    missing = harness.client.post("/workspace/activate", json={"workspace_id": "org-ghost"})
    harness.user = STRANGER
    forbidden = harness.client.post("/workspace/activate", json={"workspace_id": workspace_id})

    assert activated.status_code == 200
    assert activated.json()["workspace_id"] == workspace_id
    assert harness.store.load_state()["active_workspace"] == workspace_id
    assert missing.status_code == 404
    assert "Workspace not found" in missing.json()["detail"]
    assert forbidden.status_code == 403
    assert "is not a member" in forbidden.json()["detail"]


# ── create / read ───────────────────────────────────────────────────────────

def test_creating_an_organization_needs_a_name_and_is_audited(tmp_path: Path):
    harness = WorkspaceHarness(tmp_path)

    created = harness.client.post("/workspace/orgs", json={"name": "Acme", "settings": {"locale": "ko"}})
    unnamed = harness.client.post("/workspace/orgs", json={"name": "   "})

    workspace = created.json()["workspace"]
    assert created.status_code == 200
    assert workspace["workspace_id"] == "org-Acme"
    assert workspace["your_role"] == "owner"
    assert workspace["settings"] == {"locale": "ko"}
    assert unnamed.status_code == 400
    assert unnamed.json()["detail"] == "workspace name is required"
    assert harness.audit == [("workspace_created", {"user_email": OWNER, "workspace_id": "org-Acme"})]


def test_reading_one_organization_is_gated_and_hides_unknown_ids_behind_403(tmp_path: Path):
    harness = WorkspaceHarness(tmp_path)
    workspace_id = harness.org()

    visible = harness.client.get("/workspace/orgs/" + workspace_id)
    unknown = harness.client.get("/workspace/orgs/org-ghost")
    harness.user = STRANGER
    forbidden = harness.client.get("/workspace/orgs/" + workspace_id)

    assert visible.status_code == 200
    assert visible.json()["workspace"]["name"] == "Acme"
    assert forbidden.status_code == 403
    assert "lacks 'read'" in forbidden.json()["detail"]
    # A workspace that does not exist is indistinguishable from one the caller
    # simply cannot read — that is the anti-enumeration design, not an oversight.
    assert unknown.status_code == 403
    assert "lacks 'read'" in unknown.json()["detail"]


def test_organization_summary_counts_scoped_records_for_members_only(tmp_path: Path):
    harness = WorkspaceHarness(tmp_path)
    workspace_id = harness.org(viewer=True)
    harness.store.upsert_memory(
        kind="workspace", content="Team note", user_email=OWNER, workspace_id=workspace_id
    )

    harness.user = VIEWER
    summary = harness.client.get("/workspace/orgs/%s/summary" % workspace_id)
    unknown = harness.client.get("/workspace/orgs/org-ghost/summary")
    harness.user = STRANGER
    forbidden = harness.client.get("/workspace/orgs/%s/summary" % workspace_id)

    assert summary.status_code == 200
    assert summary.json()["counts"]["memories"] == 1
    assert summary.json()["your_role"] == "viewer"
    assert forbidden.status_code == 403
    assert unknown.status_code == 403
    assert "lacks 'read'" in unknown.json()["detail"]


# ── update / archive ────────────────────────────────────────────────────────

def test_updating_an_organization_maps_missing_forbidden_and_invalid(tmp_path: Path):
    harness = WorkspaceHarness(tmp_path)
    workspace_id = harness.org(viewer=True)

    updated = harness.client.patch(
        "/workspace/orgs/" + workspace_id, json={"name": "Acme Renamed", "settings": {"locale": "ko"}}
    )
    missing = harness.client.patch("/workspace/orgs/org-ghost", json={"name": "x"})
    personal = harness.client.patch("/workspace/orgs/personal", json={"name": "x"})
    harness.user = VIEWER
    forbidden = harness.client.patch("/workspace/orgs/" + workspace_id, json={"name": "x"})

    assert updated.status_code == 200
    assert updated.json()["workspace"]["name"] == "Acme Renamed"
    assert missing.status_code == 404
    assert "Workspace not found" in missing.json()["detail"]
    assert personal.status_code == 400
    assert "organization workspaces" in personal.json()["detail"]
    assert forbidden.status_code == 403
    assert "lacks 'manage_workspace'" in forbidden.json()["detail"]
    assert harness.audit == [("workspace_updated", {"user_email": OWNER, "workspace_id": workspace_id})]


def test_archiving_soft_deletes_and_falls_back_to_the_personal_workspace(tmp_path: Path):
    harness = WorkspaceHarness(tmp_path)
    workspace_id = harness.org(viewer=True)
    harness.client.post("/workspace/activate", json={"workspace_id": workspace_id})

    missing = harness.client.post("/workspace/orgs/org-ghost/archive")
    personal = harness.client.post("/workspace/orgs/personal/archive")
    harness.user = VIEWER
    forbidden = harness.client.post("/workspace/orgs/%s/archive" % workspace_id)
    harness.user = OWNER
    archived = harness.client.post("/workspace/orgs/%s/archive" % workspace_id)

    assert missing.status_code == 404
    assert personal.status_code == 400
    assert forbidden.status_code == 403
    assert archived.status_code == 200
    assert archived.json()["workspace"]["status"] == "archived"
    assert harness.store.load_state()["active_workspace"] == "personal"
    assert harness.audit[-1] == ("workspace_archived", {"user_email": OWNER, "workspace_id": workspace_id})


# ── membership ──────────────────────────────────────────────────────────────

def test_adding_a_member_maps_missing_forbidden_and_unknown_roles(tmp_path: Path):
    harness = WorkspaceHarness(tmp_path)
    workspace_id = harness.org()

    added = harness.client.post(
        "/workspace/orgs/%s/members" % workspace_id, json={"user_id": VIEWER, "role": "member"}
    )
    missing = harness.client.post(
        "/workspace/orgs/org-ghost/members", json={"user_id": VIEWER}
    )
    bad_role = harness.client.post(
        "/workspace/orgs/%s/members" % workspace_id, json={"user_id": VIEWER, "role": "superuser"}
    )
    harness.user = VIEWER
    forbidden = harness.client.post(
        "/workspace/orgs/%s/members" % workspace_id, json={"user_id": STRANGER}
    )

    assert added.status_code == 200
    # The owner is already a member of their own workspace; the invitee is #2.
    assert added.json()["workspace"]["member_count"] == 2
    assert {member["user_id"]: member["role"] for member in added.json()["workspace"]["members"]} == {
        str(harness.user_id(OWNER)): "owner",
        str(harness.user_id(VIEWER)): "member",
    }
    assert missing.status_code == 404
    assert bad_role.status_code == 400
    assert bad_role.json()["detail"] == "unknown role: superuser"
    assert forbidden.status_code == 403
    assert "lacks 'manage_members'" in forbidden.json()["detail"]
    assert harness.audit[-1] == (
        "workspace_member_added",
        {"user_email": OWNER, "workspace_id": workspace_id, "member": VIEWER, "role": "member"},
    )


def test_changing_a_role_refuses_unknown_members_owners_and_outsiders(tmp_path: Path):
    harness = WorkspaceHarness(tmp_path)
    workspace_id = harness.org(viewer=True)
    owner_id = str(harness.user_id(OWNER))

    promoted = harness.client.patch(
        "/workspace/orgs/%s/members/%s" % (workspace_id, VIEWER), json={"role": "admin"}
    )
    unknown_member = harness.client.patch(
        "/workspace/orgs/%s/members/%s" % (workspace_id, STRANGER), json={"role": "member"}
    )
    demote_owner = harness.client.patch(
        "/workspace/orgs/%s/members/%s" % (workspace_id, owner_id), json={"role": "member"}
    )
    harness.user = STRANGER
    forbidden = harness.client.patch(
        "/workspace/orgs/%s/members/%s" % (workspace_id, VIEWER), json={"role": "member"}
    )

    assert promoted.status_code == 200
    assert {member["user_id"]: member["role"] for member in promoted.json()["workspace"]["members"]} == {
        owner_id: "owner",
        str(harness.user_id(VIEWER)): "admin",
    }
    assert unknown_member.status_code == 404
    assert "Not found" in unknown_member.json()["detail"]
    assert demote_owner.status_code == 400
    assert demote_owner.json()["detail"] == "cannot demote the workspace owner"
    assert forbidden.status_code == 403
    assert harness.audit[-1] == (
        "workspace_member_role_updated",
        {"user_email": OWNER, "workspace_id": workspace_id, "member": VIEWER, "role": "admin"},
    )


def test_removing_a_member_refuses_unknown_members_and_the_owner(tmp_path: Path):
    harness = WorkspaceHarness(tmp_path)
    workspace_id = harness.org(viewer=True)
    owner_id = str(harness.user_id(OWNER))

    unknown_member = harness.client.delete(
        "/workspace/orgs/%s/members/%s" % (workspace_id, STRANGER)
    )
    remove_owner = harness.client.delete(
        "/workspace/orgs/%s/members/%s" % (workspace_id, owner_id)
    )
    harness.user = VIEWER
    forbidden = harness.client.delete(
        "/workspace/orgs/%s/members/%s" % (workspace_id, VIEWER)
    )
    harness.user = OWNER
    removed = harness.client.delete(
        "/workspace/orgs/%s/members/%s" % (workspace_id, VIEWER)
    )

    assert unknown_member.status_code == 404
    assert "Not found" in unknown_member.json()["detail"]
    assert remove_owner.status_code == 400
    assert remove_owner.json()["detail"] == "cannot remove the workspace owner"
    assert forbidden.status_code == 403
    assert removed.status_code == 200
    assert [member["user_id"] for member in removed.json()["workspace"]["members"]] == [owner_id]
    assert harness.audit[-1] == (
        "workspace_member_removed",
        {"user_email": OWNER, "workspace_id": workspace_id, "member": VIEWER},
    )
