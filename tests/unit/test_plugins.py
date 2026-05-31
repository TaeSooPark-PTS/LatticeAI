"""Unit tests for the v2.0 Plugin SDK (manifest, registry, permission boundary)."""

import json
from pathlib import Path

import pytest

from latticeai.core.plugins import (
    PLUGIN_SDK_VERSION,
    PluginRegistry,
    is_compatible,
    validate_manifest,
)
from latticeai.core.workspace_os import WorkspaceOSStore


def _write_plugin(plugins_dir: Path, plugin_id: str, **overrides):
    body = {
        "id": plugin_id,
        "name": overrides.get("name", plugin_id.title()),
        "version": overrides.get("version", "1.0.0"),
        "description": "test plugin",
        "lattice_version": overrides.get("lattice_version", "2.0.0"),
        "permissions": overrides.get("permissions", ["read_workspace"]),
        "provides": overrides.get("provides", {"skills": ["demo_skill"]}),
    }
    pdir = plugins_dir / plugin_id
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "plugin.json").write_text(json.dumps(body), encoding="utf-8")
    return body


# ── manifest validation ──────────────────────────────────────────────────────

def test_valid_manifest_parses():
    manifest, errors = validate_manifest({
        "id": "good-plugin", "name": "Good", "version": "1.2.3",
        "permissions": ["read_workspace", "run_tools"], "provides": {"tools": ["x"]},
    })
    assert errors == []
    assert manifest.id == "good-plugin"
    assert "run_tools" in manifest.permissions


def test_invalid_manifest_collects_errors():
    manifest, errors = validate_manifest({"id": "Bad ID!", "version": "nope", "permissions": ["wat"]})
    assert manifest is None
    assert any("id" in e for e in errors)
    assert any("semantic version" in e for e in errors)
    assert any("unknown permission" in e for e in errors)


def test_unknown_provides_key_rejected():
    _, errors = validate_manifest({"id": "p", "name": "P", "version": "1.0.0", "provides": {"bogus": []}})
    assert any("provides" in e for e in errors)


def test_is_compatible_major_and_minimum():
    assert is_compatible("2.0.0") is True
    assert is_compatible(">=2.0.0") is True
    assert is_compatible("1.0.0") is False        # different major
    assert is_compatible("2.5.0") is False         # host below required minor
    assert is_compatible("") is True               # no requirement


def test_incompatible_version_is_an_error():
    _, errors = validate_manifest({"id": "p", "name": "P", "version": "1.0.0", "lattice_version": "9.0.0"})
    assert any("requires Lattice" in e for e in errors)


# ── discovery + lifecycle ─────────────────────────────────────────────────────

def test_discover_and_catalog(tmp_path):
    plugins = tmp_path / "plugins"
    _write_plugin(plugins, "alpha")
    (plugins / "broken").mkdir(parents=True)
    (plugins / "broken" / "plugin.json").write_text("{not json", encoding="utf-8")
    store = WorkspaceOSStore(tmp_path / "data")
    registry = PluginRegistry(plugins, store=store)

    catalog = registry.catalog()
    assert catalog["sdk_version"] == PLUGIN_SDK_VERSION
    ids = [p["id"] for p in catalog["plugins"]]
    assert "alpha" in ids
    assert any("broken" in inv["path"] for inv in catalog["invalid"])


def test_install_registers_bundled_skill(tmp_path):
    plugins = tmp_path / "plugins"
    _write_plugin(plugins, "alpha", provides={"skills": ["demo_skill"]})
    store = WorkspaceOSStore(tmp_path / "data")
    registry = PluginRegistry(plugins, store=store)

    registered = []
    result = registry.install("alpha", register_skill=lambda s, p: registered.append((s, p)))
    assert result["registered_skills"] == ["demo_skill"]
    assert registered == [("demo_skill", "alpha")]
    assert store.list_plugin_registry()["alpha"]["installed"] is True


def test_install_unknown_plugin_raises(tmp_path):
    registry = PluginRegistry(tmp_path / "plugins", store=WorkspaceOSStore(tmp_path / "data"))
    with pytest.raises(Exception):
        registry.install("does-not-exist")


# ── execution boundary ────────────────────────────────────────────────────────

def test_execute_blocked_without_declared_permission(tmp_path):
    plugins = tmp_path / "plugins"
    _write_plugin(plugins, "alpha", permissions=["read_workspace"])  # no run_tools
    store = WorkspaceOSStore(tmp_path / "data")
    registry = PluginRegistry(plugins, store=store)
    registry.install("alpha")

    result = registry.execute_action("alpha", "run_tool", {}, runners={"tools": lambda **k: "ran"})
    assert result.status == "blocked"
    assert "run_tools" in result.reason


def test_execute_ok_with_declared_permission_and_runner(tmp_path):
    plugins = tmp_path / "plugins"
    _write_plugin(plugins, "alpha", permissions=["run_tools"])
    store = WorkspaceOSStore(tmp_path / "data")
    registry = PluginRegistry(plugins, store=store)
    registry.install("alpha")

    result = registry.execute_action("alpha", "run_tool", {"tool": "x"}, runners={"tools": lambda **k: {"ran": True}})
    assert result.status == "ok"
    assert result.output == {"ran": True}


def test_execute_skipped_without_runner(tmp_path):
    plugins = tmp_path / "plugins"
    _write_plugin(plugins, "alpha", permissions=["run_skills"], provides={"skills": ["s"]})
    store = WorkspaceOSStore(tmp_path / "data")
    registry = PluginRegistry(plugins, store=store)
    registry.install("alpha")

    result = registry.execute_action("alpha", "run_skill", {}, runners={})
    assert result.status == "skipped"


def test_execute_blocked_when_disabled(tmp_path):
    plugins = tmp_path / "plugins"
    _write_plugin(plugins, "alpha", permissions=["run_skills"], provides={"skills": ["s"]})
    store = WorkspaceOSStore(tmp_path / "data")
    registry = PluginRegistry(plugins, store=store)
    registry.install("alpha")
    registry.set_enabled("alpha", False)

    result = registry.execute_action("alpha", "run_skill", {}, runners={"skills": lambda **k: "x"})
    assert result.status == "blocked"
    assert "enabled" in result.reason
