"""Organization workspace lifecycle, membership, and the permission gate."""
from __future__ import annotations

from pathlib import Path

import pytest

from latticeai.core.workspace_os import DEFAULT_WORKSPACE_ID, WorkspaceOSStore
from latticeai.core.workspace_permissions import (
    has_permission as standalone_has_permission,
)

OWNER = "user-owner"
MEMBER = "user-member"


def _store(tmp_path: Path, name: str = "data") -> WorkspaceOSStore:
    target = tmp_path / name
    target.mkdir()
    return WorkspaceOSStore(target)


def _org(store: WorkspaceOSStore, name: str = "Acme") -> str:
    return store.create_organization_workspace(name=name, owner_user_id=OWNER)["workspace_id"]


def test_get_workspace_returns_the_public_record_and_reports_missing_ones(tmp_path: Path):
    store = _store(tmp_path)
    workspace_id = _org(store)

    public = store.get_workspace(workspace_id, OWNER)

    assert public["workspace_id"] == workspace_id
    assert public["type"] == "organization"
    assert public["your_role"] == "owner"
    assert public["member_count"] == 1
    with pytest.raises(FileNotFoundError):
        store.get_workspace("org-ghost")


def test_organization_names_must_be_present_and_ids_never_collide(tmp_path: Path):
    store = _store(tmp_path)

    with pytest.raises(ValueError, match="workspace name is required"):
        store.create_organization_workspace(name="   ", owner_user_id=OWNER)

    first = store.create_organization_workspace(name="Acme", owner_user_id=OWNER)
    second = store.create_organization_workspace(name="Acme", owner_user_id=OWNER)
    third = store.create_organization_workspace(name="Acme", owner_user_id=OWNER)

    assert [first["workspace_id"], second["workspace_id"], third["workspace_id"]] == [
        "org-Acme",
        "org-Acme-2",
        "org-Acme-3",
    ]


def test_update_workspace_renames_merges_settings_and_guards_its_scope(tmp_path: Path):
    store = _store(tmp_path)
    workspace_id = store.create_organization_workspace(
        name="Acme", owner_user_id=OWNER, settings={"retention_days": 30}
    )["workspace_id"]

    updated = store.update_workspace(
        workspace_id, name="  Acme Renamed  ", settings={"locale": "ko"}, actor=OWNER
    )

    assert updated["name"] == "Acme Renamed"
    assert updated["settings"] == {"retention_days": 30, "locale": "ko"}
    # A no-op patch keeps the record intact rather than blanking fields.
    assert store.update_workspace(workspace_id, actor=OWNER)["name"] == "Acme Renamed"

    with pytest.raises(FileNotFoundError):
        store.update_workspace("org-ghost", name="x", actor=OWNER)
    with pytest.raises(ValueError, match="organization workspaces"):
        store.update_workspace(DEFAULT_WORKSPACE_ID, name="x", actor=OWNER)
    with pytest.raises(PermissionError):
        store.update_workspace(workspace_id, name="x", actor="stranger")


def test_archiving_the_active_workspace_moves_the_pointer_home(tmp_path: Path):
    store = _store(tmp_path)
    workspace_id = _org(store)
    store.set_active_workspace(workspace_id, OWNER)

    archived = store.archive_workspace(workspace_id, actor=OWNER)

    assert archived["status"] == "archived"
    assert store.load_state()["active_workspace"] == DEFAULT_WORKSPACE_ID


def test_member_roles_can_be_changed_only_to_known_roles_for_known_members(tmp_path: Path):
    store = _store(tmp_path)
    workspace_id = _org(store)
    store.add_member(workspace_id, user_id=MEMBER, role="member", actor=OWNER)

    with pytest.raises(ValueError, match="unknown role"):
        store.update_member_role(workspace_id, user_id=MEMBER, role="superuser", actor=OWNER)
    with pytest.raises(FileNotFoundError):
        store.update_member_role(workspace_id, user_id="nobody", role="admin", actor=OWNER)

    promoted = store.update_member_role(workspace_id, user_id=MEMBER, role="admin", actor=OWNER)

    assert [m["role"] for m in promoted["members"] if m["user_id"] == MEMBER] == ["admin"]


def test_removing_a_member_drops_exactly_one_row(tmp_path: Path):
    store = _store(tmp_path)
    workspace_id = _org(store)
    store.add_member(workspace_id, user_id=MEMBER, role="member", actor=OWNER)
    store.add_member(workspace_id, user_id="user-other", role="viewer", actor=OWNER)

    with pytest.raises(FileNotFoundError):
        store.remove_member(workspace_id, user_id="nobody", actor=OWNER)
    with pytest.raises(ValueError, match="cannot remove the workspace owner"):
        store.remove_member(workspace_id, user_id=OWNER, actor=OWNER)

    remaining = store.remove_member(workspace_id, user_id=MEMBER, actor=OWNER)

    assert [m["user_id"] for m in remaining["members"]] == [OWNER, "user-other"]
    assert store.get_member_role(workspace_id, MEMBER) is None


def test_activating_an_unknown_workspace_is_reported(tmp_path: Path):
    store = _store(tmp_path)

    with pytest.raises(FileNotFoundError):
        store.set_active_workspace("org-ghost", OWNER)


def test_workspace_summary_counts_only_that_workspaces_records(tmp_path: Path):
    store = _store(tmp_path)
    workspace_id = _org(store)
    store.upsert_memory(
        kind="workspace", content="scoped note", user_email=None, workspace_id=workspace_id
    )
    store.upsert_memory(kind="workspace", content="personal note", user_email=None)

    summary = store.workspace_summary(workspace_id, OWNER)

    assert summary["workspace_id"] == workspace_id
    assert summary["counts"]["memories"] == 1
    assert summary["counts"]["timeline"] >= 1
    assert summary["counts"]["agent_runs"] == 0
    with pytest.raises(FileNotFoundError):
        store.workspace_summary("org-ghost", OWNER)


def test_standalone_has_permission_accepts_a_store_or_a_bare_record(tmp_path: Path):
    store = _store(tmp_path)
    workspace_id = _org(store)
    record = store.load_state()["workspaces"][workspace_id]

    assert standalone_has_permission(store, workspace_id, OWNER, "manage_members") is True
    assert standalone_has_permission(store, "org-ghost", OWNER, "read") is False
    assert standalone_has_permission(record, workspace_id, OWNER, "manage_workspace") is True
    assert standalone_has_permission(record, workspace_id, "stranger", "read") is False
    assert standalone_has_permission({}, workspace_id, OWNER, "read") is False


def test_permission_manager_reports_unknown_workspaces_without_guessing(tmp_path: Path):
    store = _store(tmp_path)

    with pytest.raises(FileNotFoundError):
        store.get_member_role("org-ghost", OWNER)
    assert store.has_permission("org-ghost", OWNER, "read") is False


def test_add_member_validates_input_and_updates_an_existing_row(tmp_path: Path):
    store = _store(tmp_path)
    workspace_id = _org(store)

    with pytest.raises(ValueError, match="unknown role"):
        store.add_member(workspace_id, user_id=MEMBER, role="superuser", actor=OWNER)
    with pytest.raises(ValueError, match="user_id is required"):
        store.add_member(workspace_id, user_id="  ", role="member", actor=OWNER)

    store.add_member(workspace_id, user_id=MEMBER, role="viewer", actor=OWNER)
    promoted = store.add_member(workspace_id, user_id=MEMBER, role="admin", actor=OWNER)

    rows = [m for m in promoted["members"] if m["user_id"] == MEMBER]
    assert len(rows) == 1
    assert rows[0]["role"] == "admin"
    assert rows[0]["updated_at"]
