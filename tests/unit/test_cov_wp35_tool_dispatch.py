"""wp35: ToolDispatchService policy gates and the module-level façade.

``AGENT_ROOT`` is rebound to ``tmp_path`` for every filesystem test (idiom:
tests/unit/test_snapshot_rollback_ports.py), so nothing here writes into the
real agent workspace.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

import latticeai.services.tool_dispatch as td


def _service(role: str = "user") -> td.ToolDispatchService:
    service = td.ToolDispatchService()
    service.configure(
        load_users=lambda: {"u@e.co": {"role": role}},
        get_user_role=lambda email, users=None: role,
    )
    return service


# ── module defaults + registry passthrough ───────────────────────────────────


def test_module_default_callables_are_permissive_but_explicit():
    assert td._default_load_users() == {}
    assert td._default_get_user_role("anyone") == "user"


def test_registry_facts_are_exposed_through_the_service():
    service = td.ToolDispatchService()

    assert service.risk_level_map["read"] == service.registry.risk_level_map["read"]
    assert service.risk_level("read_file", {}) == "low"
    assert service.policy_for("read_file", {})["risk"] == "read"
    assert service.permission("read_file", {})["risk"] == "low"


def test_configure_replaces_the_user_table():
    service = td.ToolDispatchService()

    service.configure(
        load_users=lambda: {"a@e.co": {"role": "admin"}},
        get_user_role=lambda email, users=None: users[email]["role"],
    )

    assert service.user_role("a@e.co") == "admin"
    assert service.load_users()["a@e.co"]["role"] == "admin"


def test_user_role_falls_back_to_user_when_the_lookup_raises():
    service = td.ToolDispatchService()

    def boom(email, users=None):
        raise RuntimeError("user store offline")

    service.configure(load_users=dict, get_user_role=boom)

    assert service.user_role("u@e.co") == "user"


def test_diagnostics_and_manifest_are_registry_passthroughs():
    service = td.ToolDispatchService()

    diagnostics = service.diagnostics()
    manifest = service.manifest()

    assert diagnostics == service.registry.diagnostics()
    assert manifest["schema_version"] == "tool-registry-contract/v1"
    assert manifest["diagnostics"] == diagnostics


# ── role / capability gates ──────────────────────────────────────────────────


def test_capability_gate_rejects_a_role_without_the_capability():
    service = _service(role="viewer")

    with pytest.raises(HTTPException) as excinfo:
        service.check_role("knowledge_save", "viewer@e.co")

    assert excinfo.value.status_code == 403
    assert "workspace:write" in excinfo.value.detail


def test_module_level_facade_delegates_to_the_default_service():
    assert td.get_tool_permission("read_file", {})["risk"] == "low"
    # list_dir carries no capability and is not admin-only: the gate is a no-op.
    assert td.check_tool_role("list_dir", "u@e.co") is None


def test_governed_path_existence_never_raises(monkeypatch):
    def boom(tool_name, path):
        raise RuntimeError("output target resolution broke")

    monkeypatch.setattr(td, "document_output_target", boom)

    assert td.ToolDispatchService()._governed_path_exists("create_docx", "a.docx") is False
