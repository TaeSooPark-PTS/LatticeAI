"""Skill registry persistence extracted from WorkspaceOSStore."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .timeutil import now_iso as _now


class WorkspaceSkills:
    def __init__(self, store: Any):
        self._store = store

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)

    def list_skill_registry(self, skills_dir: Path, marketplace: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        state = self.load_state()
        registry = state.setdefault("skill_registry", {})
        installed = []
        if skills_dir.exists():
            for skill_dir in sorted(skills_dir.iterdir()):
                if not skill_dir.is_dir():
                    continue
                skill_md = skill_dir / "SKILL.md"
                schema = skill_dir / "schema.json"
                if not skill_md.exists():
                    continue
                desc = ""
                try:
                    for line in skill_md.read_text(encoding="utf-8").splitlines():
                        if line.startswith("description:"):
                            desc = line.split(":", 1)[1].strip()
                            break
                except Exception:
                    desc = ""
                version = "local"
                if schema.exists():
                    try:
                        version = str((json.loads(schema.read_text(encoding="utf-8")) or {}).get("version") or "local")
                    except Exception:
                        version = "local"
                entry = registry.setdefault(skill_dir.name, {})
                entry.setdefault("enabled", True)
                entry.update({
                    "name": skill_dir.name,
                    "description": desc,
                    "version": version,
                    "installed": True,
                    "install_status": entry.get("install_status") or "ready",
                    "validation_status": "ready" if skill_md.exists() else "missing_manifest",
                    "source": entry.get("source") or "local",
                    "path": str(skill_dir),
                    "updated_at": entry.get("updated_at") or _now(),
                })
                installed.append(entry)
        available = []
        for item in marketplace or []:
            name = item.get("skill") or item.get("name")
            if not name:
                continue
            state_entry = registry.get(name, {})
            available.append({
                **item,
                "enabled": bool(state_entry.get("enabled", True)),
                "installed": bool(state_entry.get("installed")),
                "install_status": state_entry.get("install_status") or ("ready" if state_entry.get("installed") else "available"),
                "validation_status": state_entry.get("validation_status") or item.get("validation_status") or ("ready" if state_entry.get("installed") else "not_installed"),
                "source": state_entry.get("source") or item.get("source") or item.get("plugin") or "marketplace",
                "version": state_entry.get("version") or item.get("version") or "remote",
            })
        self.save_state(state)
        return {
            "installed": installed,
            "available": available,
            "registry": registry,
            "total_installed": len(installed),
            "total_available": len(available),
        }

    def set_skill_enabled(self, skill: str, enabled: bool) -> Dict[str, Any]:
        state = self.load_state()
        entry = state.setdefault("skill_registry", {}).setdefault(skill, {"name": skill})
        entry["enabled"] = bool(enabled)
        entry["updated_at"] = _now()
        self.save_state(state)
        self.record_timeline_event("skills", "skill_enabled" if enabled else "skill_disabled", {"skill": skill})
        return entry

    def mark_skill_installed(self, skill: str, *, version: str = "local", metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        state = self.load_state()
        entry = state.setdefault("skill_registry", {}).setdefault(skill, {"name": skill})
        entry.update({
            "installed": True,
            "enabled": entry.get("enabled", True),
            "version": version,
            "install_status": "ready",
            "validation_status": "ready",
            "source": (metadata or {}).get("source") or entry.get("source") or "marketplace",
            "metadata": metadata or entry.get("metadata") or {},
            "updated_at": _now(),
        })
        self.save_state(state)
        self.record_timeline_event("skills", "skill_installed", {"skill": skill, "version": version})
        return entry

    def mark_skill_uninstalled(self, skill: str) -> Dict[str, Any]:
        state = self.load_state()
        entry = state.setdefault("skill_registry", {}).setdefault(skill, {"name": skill})
        entry.update({"installed": False, "enabled": False, "updated_at": _now()})
        self.save_state(state)
        self.record_timeline_event("skills", "skill_uninstalled", {"skill": skill})
        return entry
