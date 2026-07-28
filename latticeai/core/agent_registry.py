"""Agent Registry — registration, discovery, metadata, versioning, capabilities.

Part 2 of the v3.2.0 platform. Before this module the agent roster was derived
ad-hoc from the hardcoded ``AGENT_ROLES`` tuple wherever it was needed. The
registry makes every agent — built-in role and user-registered custom agent — a
first-class entry with stable metadata: ``id``, ``type``, ``version``,
``capabilities``, ``description`` and a mutable ``config``. The HTTP surface and
the /app views read this registry instead of any hardcoded list.

Built-in role agents are projected from the single source of truth in
:mod:`lattice_brain.runtime.multi_agent` (so adding a role there flows through here).
Custom agents and config overrides are persisted to
``data_dir/agent_registry.json``.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from lattice_brain.runtime.multi_agent import (
    AGENT_ROLES,
    CORE_PIPELINE,
    MULTI_AGENT_VERSION,
    ROLE_AGENT_IDS,
)
from latticeai.core.quiet import quiet

from .timeutil import now_iso as _now

AGENT_TYPES = ("planner", "researcher", "executor", "reviewer", "release", "custom")


# Capabilities + descriptions for the built-in role agents. Kept here as the
# registry's metadata projection of the roles defined in multi_agent.py.
ROLE_META: Dict[str, Dict[str, Any]] = {
    "researcher": {
        "description": "Gathers workspace context, memory, and graph signal for the goal.",
        "capabilities": ["context-retrieval", "memory-recall", "graph-read", "hybrid-search"],
    },
    "planner": {
        "description": "Decomposes the goal into an ordered, bounded, reviewable plan.",
        "capabilities": ["task-decomposition", "plan-review", "delegation"],
    },
    "executor": {
        "description": "Executes each planned step, invoking tools, workflows, and plugins.",
        "capabilities": ["tool-use", "workflow-run", "plugin-run", "file-write"],
    },
    "reviewer": {
        "description": "Reviews executed work and approves, rejects, or requests a retry.",
        "capabilities": ["verification", "retry-control", "approval"],
    },
    "release": {
        "description": "Finalizes and summarizes the approved outcome.",
        "capabilities": ["summarize", "finalize"],
    },
}


class AgentRegistry:
    """Persisted registry of built-in role agents + user-registered agents."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._state: Dict[str, Any] = self._load()

    # ── persistence ───────────────────────────────────────────────────────
    def _load(self) -> Dict[str, Any]:
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    data.setdefault("custom", [])
                    data.setdefault("config_overrides", {})
                    return data
            except Exception:
                quiet()
        return {"custom": [], "config_overrides": {}}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._state, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    # ── projection ────────────────────────────────────────────────────────
    def _builtin_agents(self) -> List[Dict[str, Any]]:
        overrides = self._state.get("config_overrides", {})
        agents: List[Dict[str, Any]] = []
        for role in AGENT_ROLES:
            meta = ROLE_META.get(role, {"description": "", "capabilities": []})
            agent_id = ROLE_AGENT_IDS.get(role, f"agent:{role}")
            handoffs: List[str] = []
            if role == "planner":
                handoffs = [ROLE_AGENT_IDS["executor"]]
            elif role == "executor":
                handoffs = [ROLE_AGENT_IDS["reviewer"]]
            ov = overrides.get(agent_id, {})
            agents.append({
                "id": agent_id,
                "name": role.capitalize(),
                "type": role,
                "version": MULTI_AGENT_VERSION,
                "description": meta["description"],
                "capabilities": list(meta["capabilities"]),
                "handoffs": handoffs,
                "in_default_pipeline": role in CORE_PIPELINE,
                "source": "builtin",
                "removable": False,
                "enabled": bool(ov.get("enabled", True)),
                "config": ov.get("config", {}),
            })
        return agents

    def _custom_agents(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for entry in self._state.get("custom", []):
            agent = dict(entry)
            agent["source"] = "user"
            agent["removable"] = True
            agent.setdefault("enabled", True)
            agent.setdefault("handoffs", [])
            out.append(agent)
        return out

    def all(self) -> List[Dict[str, Any]]:
        return self._builtin_agents() + self._custom_agents()

    # ── reads ─────────────────────────────────────────────────────────────
    def list(self, agent_type: Optional[str] = None) -> Dict[str, Any]:
        agents = self.all()
        if agent_type:
            agents = [a for a in agents if a["type"] == agent_type]
        counts: Dict[str, int] = {}
        for a in self.all():
            counts[a["type"]] = counts.get(a["type"], 0) + 1
        return {
            "agents": agents,
            "types": list(AGENT_TYPES),
            "counts": counts,
            "total": len(agents),
            "version": MULTI_AGENT_VERSION,
            "default_pipeline": list(CORE_PIPELINE),
            "generated_at": _now(),
        }

    def get(self, agent_id: str) -> Optional[Dict[str, Any]]:
        return next((a for a in self.all() if a["id"] == agent_id), None)

    def capabilities(self) -> Dict[str, List[str]]:
        """Inverted index: capability -> [agent_id, …]."""
        index: Dict[str, List[str]] = {}
        for a in self.all():
            for cap in a.get("capabilities", []):
                index.setdefault(cap, []).append(a["id"])
        return index

    def discover(self, capability: str) -> List[Dict[str, Any]]:
        cap = str(capability or "").lower().strip()
        return [a for a in self.all() if any(cap == c.lower() for c in a.get("capabilities", []))]

    # ── mutations ─────────────────────────────────────────────────────────
    def register(
        self,
        *,
        name: str,
        agent_type: str = "custom",
        description: str = "",
        capabilities: Optional[List[str]] = None,
        config: Optional[Dict[str, Any]] = None,
        version: str = "1.0.0",
    ) -> Dict[str, Any]:
        if not str(name).strip():
            raise ValueError("name is required")
        if agent_type not in AGENT_TYPES:
            raise ValueError(f"type must be one of {', '.join(AGENT_TYPES)}")
        slug = str(name).strip().lower().replace(" ", "-")
        agent_id = f"agent:custom:{slug}"
        existing = {c["id"] for c in self._state.get("custom", [])}
        if agent_id in existing:
            agent_id = f"agent:custom:{slug}-{len(existing) + 1}"
        entry = {
            "id": agent_id,
            "name": str(name).strip(),
            "type": agent_type,
            "version": str(version or "1.0.0"),
            "description": str(description or "").strip(),
            "capabilities": list(capabilities or []),
            "config": dict(config or {}),
            "enabled": True,
            "created_at": _now(),
        }
        self._state.setdefault("custom", []).append(entry)
        self._save()
        return entry

    def update_config(self, agent_id: str, config: Dict[str, Any], *, enabled: Optional[bool] = None) -> Dict[str, Any]:
        if self.get(agent_id) is None:
            raise KeyError(agent_id)
        if agent_id.startswith("agent:custom:"):
            for entry in self._state.get("custom", []):
                if entry["id"] == agent_id:
                    entry["config"] = dict(config or {})
                    if enabled is not None:
                        entry["enabled"] = bool(enabled)
                    entry["updated_at"] = _now()
        else:
            ov = self._state.setdefault("config_overrides", {}).setdefault(agent_id, {})
            ov["config"] = dict(config or {})
            if enabled is not None:
                ov["enabled"] = bool(enabled)
        self._save()
        return self.get(agent_id)  # type: ignore[return-value]

    def remove(self, agent_id: str) -> Dict[str, Any]:
        if not agent_id.startswith("agent:custom:"):
            raise ValueError("Built-in role agents cannot be removed; disable them via config instead.")
        before = len(self._state.get("custom", []))
        self._state["custom"] = [c for c in self._state.get("custom", []) if c["id"] != agent_id]
        if len(self._state["custom"]) == before:
            raise KeyError(agent_id)
        self._save()
        return {"removed": agent_id}
