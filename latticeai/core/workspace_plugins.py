"""Plugins, marketplace templates, and skill management extracted from WorkspaceOSStore.

Provides PluginManager for composition.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .workspace_os_utils import _now


class WorkspacePluginManager:
    """Composable plugin + marketplace manager."""

    def __init__(self, store: Any):
        self._store = store

    def list_plugin_registry(self) -> Dict[str, Any]:
        return dict(self._store.load_state().get("plugin_registry") or {})

    def set_plugin_enabled(self, plugin_id: str, enabled: bool) -> Dict[str, Any]:
        state = self._store.load_state()
        entry = state.setdefault("plugin_registry", {}).setdefault(plugin_id, {"id": plugin_id})
        entry["enabled"] = bool(enabled)
        entry["updated_at"] = _now()
        self._store.save_state(state)
        self._store.record_timeline_event("plugins", "plugin_toggled", {"plugin": plugin_id, "enabled": enabled})
        return entry

    def mark_plugin_installed(self, plugin_id: str, *, version: str = "0.0.0", metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        state = self._store.load_state()
        entry = state.setdefault("plugin_registry", {}).setdefault(plugin_id, {"id": plugin_id})
        entry.update({
            "id": plugin_id,
            "installed": True,
            "enabled": entry.get("enabled", True),
            "version": version,
            "install_status": "ready",
            "validation_status": "valid",
            "metadata": metadata or entry.get("metadata") or {},
            "updated_at": _now(),
        })
        self._store.save_state(state)
        self._store.record_timeline_event("plugins", "plugin_installed", {"plugin": plugin_id, "version": version})
        return entry

    def mark_plugin_uninstalled(self, plugin_id: str) -> Dict[str, Any]:
        state = self._store.load_state()
        entry = state.setdefault("plugin_registry", {}).setdefault(plugin_id, {"id": plugin_id})
        entry.update({"installed": False, "enabled": False, "updated_at": _now()})
        self._store.save_state(state)
        self._store.record_timeline_event("plugins", "plugin_uninstalled", {"plugin": plugin_id})
        return {"status": "ok", "plugin_id": plugin_id, "registry": entry}

    # Marketplace templates
    @staticmethod
    def _template_registry_key(kind: str, template_id: str, workspace_id: str) -> str:
        base = f"{kind}:{template_id}"
        return base if workspace_id == "personal" else f"{workspace_id}:{base}"

    def list_template_registry(self, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        state = self._store.load_state()
        registry = dict(state.get("template_registry") or {})
        if workspace_id is None:
            return registry
        scope = self._store._resolve_scope(workspace_id, state)
        return {
            key: value
            for key, value in registry.items()
            if self._store._record_workspace(value) == scope
        }

    def mark_template_installed(
        self,
        *,
        kind: str,
        template_id: str,
        version: str = "1.0.0",
        metadata: Optional[Dict[str, Any]] = None,
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        state = self._store.load_state()
        scope = self._store._resolve_scope(workspace_id, state)
        key = self._template_registry_key(kind, template_id, scope)
        entry = state.setdefault("template_registry", {}).setdefault(key, {"id": template_id, "kind": kind})
        entry.update({
            "id": template_id,
            "kind": kind,
            "version": version,
            "installed": True,
            "workspace_id": scope,
            "metadata": metadata or entry.get("metadata") or {},
            "updated_at": _now(),
        })
        self._store.save_state(state)
        self._store.record_timeline_event("marketplace", "template_installed", {"kind": kind, "template_id": template_id}, workspace_id=scope)
        return entry
