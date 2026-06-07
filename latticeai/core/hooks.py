"""Hooks platform — a persisted registry of lifecycle extension points.

Lattice AI runs several behaviours at well-defined points in the agent / tool /
workflow lifecycle (audit logging, secret redaction, sensitive-data
classification, tool-permission gating, memory snapshots, workflow replay
logging). v3.2.0 makes those points *first-class and inspectable*: every hook is
listed, ordered, and individually enable/disable-able, and users can register
their own custom hooks alongside the built-ins.

The registry owns metadata, ordering and the enabled flag (persisted to
``data_dir/hooks.json``). Built-in hooks carry ``source="builtin"`` and map onto
behaviour the platform already performs; ``managed`` records whether the
behaviour is enforced by the platform (``platform``) or is an advisory hook the
user has registered (``user``). The registry never silently drops a hook: the
full set is always returned so the UI can show exactly what runs and in which
order.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


HOOK_KINDS = (
    "pre_run",
    "post_run",
    "pre_tool",
    "post_tool",
    "agent",
    "pipeline",
    "workflow",
)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# Built-in hooks describe lifecycle points the platform already exercises. They
# are honest reflections of existing behaviour (see the `binding` field), made
# visible and orderable here. Disabling a `managed="platform"` hook is recorded
# and surfaced, but core safety behaviours remain enforced by their owning
# subsystem — the UI states this explicitly so nothing is misrepresented.
BUILTIN_HOOKS: List[Dict[str, Any]] = [
    {
        "id": "builtin:redact-secrets",
        "name": "Redact secrets",
        "kind": "pre_run",
        "order": 10,
        "description": "Strip secret-like fields (token, password, api_key…) from agent context packets before a run.",
        "binding": "latticeai.core.multi_agent._redact",
        "managed": "platform",
    },
    {
        "id": "builtin:research-memory-snapshot",
        "name": "Research memory snapshot",
        "kind": "agent",
        "order": 20,
        "description": "Capture a short-term memory snapshot after the researcher stage gathers context.",
        "binding": "latticeai.core.multi_agent.default_role_runner",
        "managed": "platform",
    },
    {
        "id": "builtin:tool-permission-gate",
        "name": "Tool permission gate",
        "kind": "pre_tool",
        "order": 10,
        "description": "Require explicit approval for tools whose governance policy is not auto-approve.",
        "binding": "latticeai.core.tool_registry.ToolRegistry.permission",
        "managed": "platform",
    },
    {
        "id": "builtin:sensitive-data-guard",
        "name": "Sensitive-data guard",
        "kind": "pre_tool",
        "order": 20,
        "description": "Classify outgoing content for sensitive data before tool execution.",
        "binding": "server_app.classify_sensitive_message",
        "managed": "platform",
    },
    {
        "id": "builtin:audit-agent-run",
        "name": "Audit agent run",
        "kind": "post_run",
        "order": 10,
        "description": "Append every completed agent run to the workspace audit log.",
        "binding": "latticeai.services.agent_runtime.AgentRuntime.start",
        "managed": "platform",
    },
    {
        "id": "builtin:workflow-replay-log",
        "name": "Workflow replay log",
        "kind": "workflow",
        "order": 10,
        "description": "Record each workflow run's timeline so it can be replayed step by step.",
        "binding": "latticeai.api.workflow_designer",
        "managed": "platform",
    },
    {
        "id": "builtin:pipeline-index-status",
        "name": "Pipeline index status",
        "kind": "pipeline",
        "order": 10,
        "description": "Publish ingest / embed / graph-build pipeline state to the retrieval index status.",
        "binding": "latticeai.api.search",
        "managed": "platform",
    },
]


class HooksRegistry:
    """Persisted registry of lifecycle hooks (built-in + user-registered)."""

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
                    data.setdefault("overrides", {})
                    return data
            except Exception:
                pass
        return {"custom": [], "overrides": {}}

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

    # ── views ─────────────────────────────────────────────────────────────
    def _materialize(self) -> List[Dict[str, Any]]:
        overrides: Dict[str, Any] = self._state.get("overrides", {})
        hooks: List[Dict[str, Any]] = []
        for base in BUILTIN_HOOKS:
            ov = overrides.get(base["id"], {})
            hook = dict(base)
            hook["source"] = "builtin"
            hook["enabled"] = bool(ov.get("enabled", True))
            if "order" in ov:
                hook["order"] = ov["order"]
            hook["removable"] = False
            hooks.append(hook)
        for custom in self._state.get("custom", []):
            hook = dict(custom)
            hook["source"] = "user"
            hook.setdefault("managed", "user")
            hook.setdefault("binding", "advisory")
            hook["enabled"] = bool(custom.get("enabled", True))
            hook["removable"] = True
            hooks.append(hook)
        hooks.sort(key=lambda h: (HOOK_KINDS.index(h["kind"]) if h["kind"] in HOOK_KINDS else 99, h.get("order", 100), h["id"]))
        return hooks

    def list(self, kind: Optional[str] = None) -> Dict[str, Any]:
        hooks = self._materialize()
        if kind:
            hooks = [h for h in hooks if h["kind"] == kind]
        counts: Dict[str, Dict[str, int]] = {}
        for h in self._materialize():
            bucket = counts.setdefault(h["kind"], {"total": 0, "enabled": 0})
            bucket["total"] += 1
            if h["enabled"]:
                bucket["enabled"] += 1
        return {
            "hooks": hooks,
            "kinds": list(HOOK_KINDS),
            "counts": counts,
            "total": len(hooks),
            "enabled": sum(1 for h in hooks if h["enabled"]),
            "generated_at": _now(),
        }

    def get(self, hook_id: str) -> Optional[Dict[str, Any]]:
        return next((h for h in self._materialize() if h["id"] == hook_id), None)

    def inspect(self, hook_id: str) -> Dict[str, Any]:
        hook = self.get(hook_id)
        if hook is None:
            raise KeyError(hook_id)
        detail = dict(hook)
        detail["advisory"] = hook.get("managed") != "platform"
        detail["note"] = (
            "Enforced by its owning subsystem; the registry controls visibility and ordering."
            if hook.get("managed") == "platform"
            else "User-registered hook: listed, ordered and inspectable; runs advisory in this build."
        )
        return detail

    # ── mutations ─────────────────────────────────────────────────────────
    def set_enabled(self, hook_id: str, enabled: bool) -> Dict[str, Any]:
        if self.get(hook_id) is None:
            raise KeyError(hook_id)
        if hook_id.startswith("builtin:"):
            self._state.setdefault("overrides", {}).setdefault(hook_id, {})["enabled"] = bool(enabled)
        else:
            for custom in self._state.get("custom", []):
                if custom["id"] == hook_id:
                    custom["enabled"] = bool(enabled)
        self._save()
        return self.get(hook_id)  # type: ignore[return-value]

    def set_order(self, hook_id: str, order: int) -> Dict[str, Any]:
        if self.get(hook_id) is None:
            raise KeyError(hook_id)
        order = int(order)
        if hook_id.startswith("builtin:"):
            self._state.setdefault("overrides", {}).setdefault(hook_id, {})["order"] = order
        else:
            for custom in self._state.get("custom", []):
                if custom["id"] == hook_id:
                    custom["order"] = order
        self._save()
        return self.get(hook_id)  # type: ignore[return-value]

    def reorder(self, kind: str, ordered_ids: List[str]) -> Dict[str, Any]:
        for idx, hook_id in enumerate(ordered_ids):
            try:
                self.set_order(hook_id, (idx + 1) * 10)
            except KeyError:
                continue
        return self.list(kind=kind)

    def register(
        self,
        *,
        name: str,
        kind: str,
        description: str = "",
        command: str = "",
        order: Optional[int] = None,
        enabled: bool = True,
    ) -> Dict[str, Any]:
        if not str(name).strip():
            raise ValueError("name is required")
        if kind not in HOOK_KINDS:
            raise ValueError(f"kind must be one of {', '.join(HOOK_KINDS)}")
        slug = str(name).strip().lower().replace(" ", "-")
        hook_id = f"user:{slug}"
        existing = {c["id"] for c in self._state.get("custom", [])}
        if hook_id in existing:
            hook_id = f"user:{slug}-{len(existing) + 1}"
        entry = {
            "id": hook_id,
            "name": str(name).strip(),
            "kind": kind,
            "description": str(description or "").strip(),
            "command": str(command or "").strip(),
            "order": int(order) if order is not None else 100,
            "enabled": bool(enabled),
            "managed": "user",
            "binding": "advisory",
            "created_at": _now(),
        }
        self._state.setdefault("custom", []).append(entry)
        self._save()
        return entry

    def remove(self, hook_id: str) -> Dict[str, Any]:
        if hook_id.startswith("builtin:"):
            raise ValueError("Built-in hooks cannot be removed; disable them instead.")
        before = len(self._state.get("custom", []))
        self._state["custom"] = [c for c in self._state.get("custom", []) if c["id"] != hook_id]
        if len(self._state["custom"]) == before:
            raise KeyError(hook_id)
        self._save()
        return {"removed": hook_id}
