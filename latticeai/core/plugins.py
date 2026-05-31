"""Plugin SDK — manifest, registry, lifecycle, permissions, validation, and a
safe execution boundary.

The Plugin SDK is the v2.0.0 extension layer. It is intentionally additive:
a plugin is a directory under the configured ``plugins`` root that ships a
``plugin.json`` manifest and *extends* the existing Skill / Tool / Workflow
surfaces rather than replacing them. Installed standalone skills keep working
untouched; a plugin can simply *bundle* skills (and declare tools / workflow
templates) under one versioned, permissioned unit.

Design rules (mirrors :mod:`latticeai.core.tool_registry` and the workspace
skill registry):

* **No import-time I/O.** The registry only touches the filesystem when asked
  to ``discover``.
* **No FastAPI, no globals.** Lifecycle state lives in the Workspace OS store
  (passed in), so personal/organization scoping and the local-first JSON store
  are reused, not duplicated.
* **Permissions are an allow-list.** A manifest may only request permissions in
  :data:`PLUGIN_PERMISSIONS`; the execution boundary refuses anything a plugin
  did not declare *and* was not granted.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


PLUGIN_SDK_VERSION = "2.0.0"

# Capability-style permissions a plugin can request. Kept deliberately small so
# the Enterprise seam can layer finer-grained policy on top without changing the
# community contract. Every permission maps to a concrete thing the execution
# boundary will or will not allow.
PLUGIN_PERMISSIONS = (
    "read_workspace",
    "write_workspace",
    "read_graph",
    "write_graph",
    "run_tools",
    "run_skills",
    "run_workflows",
    "run_agents",
    "network",
    "manage_memory",
)

# What a plugin may contribute to the platform. These extend existing systems.
PLUGIN_PROVIDES = ("skills", "tools", "workflows", "actions")

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+([.-][0-9A-Za-z.]+)?$")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")


class PluginError(Exception):
    """Raised for plugin validation / lifecycle / execution failures."""


def _version_tuple(version: str) -> Tuple[int, int, int]:
    parts = re.split(r"[.-]", str(version or "0"))
    nums: List[int] = []
    for part in parts[:3]:
        try:
            nums.append(int(part))
        except ValueError:
            nums.append(0)
    while len(nums) < 3:
        nums.append(0)
    return nums[0], nums[1], nums[2]


def is_compatible(required: str, current: str = PLUGIN_SDK_VERSION) -> bool:
    """True if a plugin requiring ``required`` runs on ``current`` host version.

    ``required`` is a minimum (major must match, host must be >= required).
    Empty / missing requirement is treated as "any host".
    """
    required = str(required or "").strip().lstrip(">=").strip()
    if not required:
        return True
    req = _version_tuple(required)
    cur = _version_tuple(current)
    if req[0] != cur[0]:
        return False
    return cur >= req


@dataclass(frozen=True)
class PluginManifest:
    """A parsed, validated ``plugin.json``."""

    id: str
    name: str
    version: str
    description: str = ""
    author: str = ""
    lattice_version: str = ""
    permissions: Tuple[str, ...] = ()
    provides: Dict[str, List[str]] = field(default_factory=dict)
    entrypoint: str = ""
    homepage: str = ""
    path: str = ""

    def public(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "lattice_version": self.lattice_version,
            "permissions": list(self.permissions),
            "provides": {key: list(value) for key, value in self.provides.items()},
            "entrypoint": self.entrypoint,
            "homepage": self.homepage,
            "path": self.path,
            "compatible": is_compatible(self.lattice_version),
        }


def validate_manifest(data: Dict[str, Any], *, path: str = "") -> Tuple[Optional[PluginManifest], List[str]]:
    """Validate a manifest dict. Returns ``(manifest_or_None, errors)``.

    A manifest with errors still returns ``None`` so callers never accidentally
    treat an invalid plugin as runnable.
    """
    errors: List[str] = []
    if not isinstance(data, dict):
        return None, ["manifest is not a JSON object"]

    plugin_id = str(data.get("id") or "").strip()
    if not plugin_id:
        errors.append("missing required field: id")
    elif not _ID_RE.match(plugin_id):
        errors.append("id must be lowercase alphanumeric with - or _ (2-64 chars)")

    name = str(data.get("name") or plugin_id or "").strip()
    if not name:
        errors.append("missing required field: name")

    version = str(data.get("version") or "").strip()
    if not version:
        errors.append("missing required field: version")
    elif not _SEMVER_RE.match(version):
        errors.append(f"version '{version}' is not a valid semantic version")

    raw_perms = data.get("permissions") or []
    if not isinstance(raw_perms, list):
        errors.append("permissions must be a list")
        raw_perms = []
    perms: List[str] = []
    for perm in raw_perms:
        if perm not in PLUGIN_PERMISSIONS:
            errors.append(f"unknown permission: {perm}")
        else:
            perms.append(perm)

    raw_provides = data.get("provides") or {}
    provides: Dict[str, List[str]] = {}
    if not isinstance(raw_provides, dict):
        errors.append("provides must be an object")
    else:
        for key, value in raw_provides.items():
            if key not in PLUGIN_PROVIDES:
                errors.append(f"unknown provides key: {key}")
                continue
            if not isinstance(value, list):
                errors.append(f"provides.{key} must be a list")
                continue
            provides[key] = [str(item) for item in value]

    lattice_version = str(data.get("lattice_version") or "").strip()
    if lattice_version and not is_compatible(lattice_version):
        errors.append(
            f"requires Lattice {lattice_version} but host is {PLUGIN_SDK_VERSION}"
        )

    if errors:
        return None, errors

    manifest = PluginManifest(
        id=plugin_id,
        name=name,
        version=version,
        description=str(data.get("description") or ""),
        author=str(data.get("author") or ""),
        lattice_version=lattice_version,
        permissions=tuple(perms),
        provides=provides,
        entrypoint=str(data.get("entrypoint") or ""),
        homepage=str(data.get("homepage") or ""),
        path=path,
    )
    return manifest, []


@dataclass
class PluginExecutionResult:
    plugin_id: str
    action: str
    status: str  # "ok" | "blocked" | "error" | "skipped"
    output: Any = None
    reason: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "action": self.action,
            "status": self.status,
            "output": self.output,
            "reason": self.reason,
        }


class PluginRegistry:
    """Discovery + validation + a permissioned execution boundary for plugins.

    Lifecycle *state* (installed / enabled / validation status) is delegated to
    the Workspace OS store via the small ``store`` port so plugins reuse the same
    local-first JSON persistence, workspace scoping, and timeline events as
    skills. The registry itself owns only manifest parsing and the execution
    boundary.
    """

    def __init__(self, plugins_dir: Path | str, *, store: Any = None):
        self.plugins_dir = Path(plugins_dir).expanduser()
        self.store = store

    # ── discovery / validation ───────────────────────────────────────────

    def discover(self) -> Dict[str, Any]:
        """Scan ``plugins_dir`` for ``<id>/plugin.json`` manifests."""
        valid: List[PluginManifest] = []
        invalid: List[Dict[str, Any]] = []
        if self.plugins_dir.exists():
            for entry in sorted(self.plugins_dir.iterdir()):
                if not entry.is_dir():
                    continue
                manifest_path = entry / "plugin.json"
                if not manifest_path.exists():
                    continue
                try:
                    data = json.loads(manifest_path.read_text(encoding="utf-8"))
                except Exception as exc:
                    invalid.append({"path": str(entry), "errors": [f"invalid JSON: {exc}"]})
                    continue
                manifest, errors = validate_manifest(data, path=str(entry))
                if manifest is None:
                    invalid.append({"path": str(entry), "id": data.get("id"), "errors": errors})
                else:
                    valid.append(manifest)
        return {"valid": valid, "invalid": invalid}

    def get_manifest(self, plugin_id: str) -> Optional[PluginManifest]:
        for manifest in self.discover()["valid"]:
            if manifest.id == plugin_id:
                return manifest
        return None

    def catalog(self) -> Dict[str, Any]:
        """Merge discovered manifests with persisted lifecycle state for the UI."""
        discovered = self.discover()
        registry_state = self.store.list_plugin_registry() if self.store else {}
        plugins = []
        for manifest in discovered["valid"]:
            state = registry_state.get(manifest.id, {})
            public = manifest.public()
            public.update({
                "installed": bool(state.get("installed")),
                "enabled": bool(state.get("enabled", state.get("installed"))),
                "install_status": state.get("install_status") or ("ready" if state.get("installed") else "available"),
                "validation_status": "valid",
                "updated_at": state.get("updated_at"),
            })
            plugins.append(public)
        return {
            "sdk_version": PLUGIN_SDK_VERSION,
            "permissions": list(PLUGIN_PERMISSIONS),
            "provides": list(PLUGIN_PROVIDES),
            "plugins": plugins,
            "invalid": discovered["invalid"],
            "plugins_dir": str(self.plugins_dir),
            "total": len(plugins),
        }

    # ── lifecycle ─────────────────────────────────────────────────────────

    def install(self, plugin_id: str, *, register_skill: Optional[Callable[[str, str], Any]] = None) -> Dict[str, Any]:
        """Install (activate) a discovered plugin and register the skills it bundles.

        ``register_skill(skill_name, plugin_id)`` is injected so plugins *extend*
        the existing skill registry instead of owning a parallel one.
        """
        manifest = self.get_manifest(plugin_id)
        if manifest is None:
            raise PluginError(f"plugin not found or invalid: {plugin_id}")
        if not is_compatible(manifest.lattice_version):
            raise PluginError(
                f"plugin '{plugin_id}' requires Lattice {manifest.lattice_version}, host is {PLUGIN_SDK_VERSION}"
            )
        registered_skills = []
        if register_skill is not None:
            for skill_name in manifest.provides.get("skills", []):
                try:
                    register_skill(skill_name, plugin_id)
                    registered_skills.append(skill_name)
                except Exception:  # pragma: no cover - skill registration is best-effort
                    pass
        entry = {}
        if self.store is not None:
            entry = self.store.mark_plugin_installed(
                plugin_id,
                version=manifest.version,
                metadata={
                    "permissions": list(manifest.permissions),
                    "provides": {k: list(v) for k, v in manifest.provides.items()},
                    "registered_skills": registered_skills,
                },
            )
        return {"plugin": manifest.public(), "registry": entry, "registered_skills": registered_skills}

    def uninstall(self, plugin_id: str) -> Dict[str, Any]:
        if self.store is None:
            return {"status": "ok", "plugin_id": plugin_id}
        return self.store.mark_plugin_uninstalled(plugin_id)

    def set_enabled(self, plugin_id: str, enabled: bool) -> Dict[str, Any]:
        if self.store is None:
            return {"plugin_id": plugin_id, "enabled": enabled}
        return self.store.set_plugin_enabled(plugin_id, enabled)

    # ── execution boundary ────────────────────────────────────────────────

    def _granted_permissions(self, plugin_id: str) -> List[str]:
        if self.store is None:
            return []
        state = self.store.list_plugin_registry().get(plugin_id, {})
        return list((state.get("metadata") or {}).get("permissions") or [])

    def execute_action(
        self,
        plugin_id: str,
        action: str,
        args: Optional[Dict[str, Any]] = None,
        *,
        runners: Optional[Dict[str, Callable[..., Any]]] = None,
    ) -> PluginExecutionResult:
        """Run a plugin-provided action through the permission boundary.

        ``runners`` maps a capability ("tools" / "skills" / "workflows" /
        "agents") to a callable the host injects. The boundary refuses any
        capability the plugin did not *declare in its manifest*; without a
        matching runner the action is reported ``skipped`` (never crashes the
        caller). This keeps v2.0.0 plugins safe-by-default.
        """
        args = args or {}
        runners = runners or {}
        manifest = self.get_manifest(plugin_id)
        if manifest is None:
            return PluginExecutionResult(plugin_id, action, "error", reason="plugin not found or invalid")

        registry_state = self.store.list_plugin_registry().get(plugin_id, {}) if self.store else {}
        if self.store is not None and not registry_state.get("enabled", registry_state.get("installed")):
            return PluginExecutionResult(plugin_id, action, "blocked", reason="plugin is not enabled")

        # Map an action to the capability + permission it needs.
        capability_for: Dict[str, Tuple[str, str]] = {
            "run_tool": ("tools", "run_tools"),
            "run_skill": ("skills", "run_skills"),
            "run_workflow": ("workflows", "run_workflows"),
            "run_agent": ("agents", "run_agents"),
        }
        capability, permission = capability_for.get(action, ("actions", ""))

        if permission and permission not in manifest.permissions:
            return PluginExecutionResult(
                plugin_id, action, "blocked",
                reason=f"plugin did not declare required permission '{permission}'",
            )
        if permission and self.store is not None and permission not in self._granted_permissions(plugin_id):
            return PluginExecutionResult(
                plugin_id, action, "blocked",
                reason=f"permission '{permission}' not granted at install time",
            )

        runner = runners.get(capability)
        if runner is None:
            return PluginExecutionResult(
                plugin_id, action, "skipped",
                reason=f"no host runner for capability '{capability}'",
            )
        try:
            output = runner(plugin_id=plugin_id, action=action, args=args, manifest=manifest)
            return PluginExecutionResult(plugin_id, action, "ok", output=output)
        except Exception as exc:
            return PluginExecutionResult(plugin_id, action, "error", reason=str(exc))
