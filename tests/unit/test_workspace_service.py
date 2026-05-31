"""Guardrail tests for the WorkspaceService scope/permission layer (v1.2.0)."""

import pytest

from latticeai.core.workspace_os import WorkspaceOSStore
from latticeai.services.workspace_service import WorkspaceService


def _service(tmp_path):
    return WorkspaceService(WorkspaceOSStore(tmp_path))


def test_personal_scope_is_backward_compatible(tmp_path):
    svc = _service(tmp_path)
    # No workspace named, no auth identity -> Personal, read+write allowed.
    assert svc.resolve_read_scope(None, None) == "personal"
    assert svc.resolve_write_scope(None, None) == "personal"


def test_non_member_cannot_read_or_write_org(tmp_path):
    svc = _service(tmp_path)
    org = svc.create_organization_workspace(name="Acme", owner_user_id="owner@acme.com")
    wid = org["workspace_id"]

    with pytest.raises(PermissionError):
        svc.resolve_read_scope(wid, "stranger@acme.com")
    with pytest.raises(PermissionError):
        svc.resolve_write_scope(wid, "stranger@acme.com")
    with pytest.raises(PermissionError):
        svc.workspace_summary(wid, "stranger@acme.com")


def test_viewer_can_read_but_not_write(tmp_path):
    svc = _service(tmp_path)
    org = svc.create_organization_workspace(name="Beta", owner_user_id="owner@beta.com")
    wid = org["workspace_id"]
    svc.add_member(wid, user_id="viewer@beta.com", role="viewer", actor="owner@beta.com")

    assert svc.resolve_read_scope(wid, "viewer@beta.com") == wid
    with pytest.raises(PermissionError):
        svc.resolve_write_scope(wid, "viewer@beta.com")


def test_member_can_write(tmp_path):
    svc = _service(tmp_path)
    org = svc.create_organization_workspace(name="Gamma", owner_user_id="owner@gamma.com")
    wid = org["workspace_id"]
    svc.add_member(wid, user_id="member@gamma.com", role="member", actor="owner@gamma.com")

    assert svc.resolve_write_scope(wid, "member@gamma.com") == wid
    assert svc.resolve_read_scope(wid, "member@gamma.com") == wid


def test_only_owner_admin_manage_members(tmp_path):
    svc = _service(tmp_path)
    org = svc.create_organization_workspace(name="Delta", owner_user_id="owner@d.com")
    wid = org["workspace_id"]
    svc.add_member(wid, user_id="member@d.com", role="member", actor="owner@d.com")
    svc.add_member(wid, user_id="viewer@d.com", role="viewer", actor="owner@d.com")

    # member/viewer cannot manage members
    with pytest.raises(PermissionError):
        svc.add_member(wid, user_id="x@d.com", role="member", actor="member@d.com")
    with pytest.raises(PermissionError):
        svc.add_member(wid, user_id="y@d.com", role="member", actor="viewer@d.com")

    # promote member to admin -> can now manage
    svc.update_member_role(wid, user_id="member@d.com", role="admin", actor="owner@d.com")
    svc.add_member(wid, user_id="z@d.com", role="member", actor="member@d.com")
    assert svc.store.get_member_role(wid, "z@d.com") == "member"


def test_owner_cannot_be_removed_or_demoted(tmp_path):
    svc = _service(tmp_path)
    org = svc.create_organization_workspace(name="Eps", owner_user_id="owner@e.com")
    wid = org["workspace_id"]

    with pytest.raises(ValueError):
        svc.remove_member(wid, user_id="owner@e.com", actor="owner@e.com")
    with pytest.raises(ValueError):
        svc.update_member_role(wid, user_id="owner@e.com", role="member", actor="owner@e.com")


def test_named_stranger_does_not_bypass_in_no_auth_mode(tmp_path):
    # Ownerless org (no-auth creator) is owned by the anonymous local user, but a
    # named stranger must NOT inherit access.
    svc = _service(tmp_path)
    org = svc.create_organization_workspace(name="Local", owner_user_id=None)
    wid = org["workspace_id"]

    assert svc.resolve_write_scope(wid, None) == wid  # local user owns it
    with pytest.raises(PermissionError):
        svc.resolve_read_scope(wid, "stranger@local")


def test_shared_global_areas_documented(tmp_path):
    svc = _service(tmp_path)
    summary = svc.summary(None)
    assert "graph" in summary["shared_global_areas"]
    assert "skills" in summary["shared_global_areas"]
    assert summary["workspace_registry"]["active_workspace"] == "personal"
