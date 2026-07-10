from __future__ import annotations

from latticeai.services.platform_runtime import PlatformRuntime


class _BrokenWorkspaceService:
    def list_workspaces(self, _user):
        raise RuntimeError("workspace store unavailable")


class _WorkspaceService:
    def list_workspaces(self, _user):
        return {
            "workspaces": [
                {"workspace_id": "personal"},
                {"workspace_id": "org:acme"},
                {"name": "malformed"},
            ]
        }


def _runtime(service):
    runtime = PlatformRuntime.__new__(PlatformRuntime)
    runtime.svc = service
    return runtime


def test_allowed_scopes_returns_visible_workspace_ids():
    assert _runtime(_WorkspaceService()).allowed_scopes("member@example.com") == {
        "personal",
        "org:acme",
    }


def test_authenticated_scope_failure_fails_closed():
    assert _runtime(_BrokenWorkspaceService()).allowed_scopes("member@example.com") == set()


def test_explicit_no_auth_scope_failure_keeps_unscoped_local_mode():
    assert _runtime(_BrokenWorkspaceService()).allowed_scopes(None) is None
