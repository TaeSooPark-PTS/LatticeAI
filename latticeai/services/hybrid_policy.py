"""User-configurable hybrid policy (Phase 3).

Controls:

* which node types / metadata flags are blocked from leaving the machine
* whether cloud-derived KG expansion auto-commits or stages for review
* whether multimodal (video) cloud calls are allowed when cloud mode is on

Persisted under the data dir, scoped like NetworkBoundaryService
(workspace → user → default).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from latticeai.core.io_utils import atomic_write_json
from latticeai.core.network_boundary import HARD_BLOCK_METADATA_FLAGS, HARD_BLOCK_NODE_TYPES

DEFAULT_BLOCKED_TYPES: List[str] = []
DEFAULT_BLOCKED_FLAGS: List[str] = sorted(HARD_BLOCK_METADATA_FLAGS)
DEFAULT_AUTO_COMMIT = False
DEFAULT_ALLOW_MULTIMODAL = False


def _default_policy() -> Dict[str, Any]:
    return {
        "blocked_node_types": list(DEFAULT_BLOCKED_TYPES),
        "blocked_metadata_flags": list(DEFAULT_BLOCKED_FLAGS),
        "auto_commit": DEFAULT_AUTO_COMMIT,
        "allow_multimodal": DEFAULT_ALLOW_MULTIMODAL,
        "min_extraction_confidence": 0.55,
    }


class HybridPolicyService:
    """Load / save hybrid cloud policy."""

    def __init__(
        self,
        *,
        data_dir: Path,
        audit: Optional[Callable[..., None]] = None,
    ) -> None:
        self._path = Path(data_dir) / "hybrid_policy.json"
        self._audit = audit or (lambda *a, **kw: None)
        self._lock = threading.Lock()

    def rebind_data_dir(self, data_dir: Path) -> None:
        with self._lock:
            self._path = Path(data_dir) / "hybrid_policy.json"

    def rebind_audit(self, audit: Callable[..., None]) -> None:
        with self._lock:
            self._audit = audit

    def _read(self) -> Dict[str, Any]:
        base = {
            "default": _default_policy(),
            "users": {},
            "workspaces": {},
        }
        if not self._path.exists():
            return base
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return base
        if not isinstance(data, dict):
            return base
        data.setdefault("default", _default_policy())
        data.setdefault("users", {})
        data.setdefault("workspaces", {})
        return data

    def _write(self, data: Dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self._path, data)

    def _resolve_raw(
        self,
        data: Dict[str, Any],
        *,
        user_email: Optional[str],
        workspace_id: Optional[str],
    ) -> Dict[str, Any]:
        policy = dict(data.get("default") or _default_policy())
        if user_email:
            user = (data.get("users") or {}).get(str(user_email).lower())
            if isinstance(user, dict):
                policy.update(user)
        if workspace_id:
            ws = (data.get("workspaces") or {}).get(str(workspace_id))
            if isinstance(ws, dict):
                policy.update(ws)
        # Always union hard-coded circuit breakers.
        blocked_types: Set[str] = set(policy.get("blocked_node_types") or [])
        blocked_types |= set(HARD_BLOCK_NODE_TYPES)
        blocked_flags: Set[str] = set(policy.get("blocked_metadata_flags") or [])
        blocked_flags |= set(HARD_BLOCK_METADATA_FLAGS)
        policy["blocked_node_types"] = sorted(blocked_types)
        policy["blocked_metadata_flags"] = sorted(blocked_flags)
        policy["auto_commit"] = bool(policy.get("auto_commit", False))
        policy["allow_multimodal"] = bool(policy.get("allow_multimodal", False))
        try:
            policy["min_extraction_confidence"] = float(
                policy.get("min_extraction_confidence", 0.55)
            )
        except (TypeError, ValueError):
            policy["min_extraction_confidence"] = 0.55
        return policy

    def resolve(
        self,
        *,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            data = self._read()
        return self._resolve_raw(data, user_email=user_email, workspace_id=workspace_id)

    def set_policy(
        self,
        patch: Dict[str, Any],
        *,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
        source: str = "api",
    ) -> Dict[str, Any]:
        allowed = {
            "blocked_node_types",
            "blocked_metadata_flags",
            "auto_commit",
            "allow_multimodal",
            "min_extraction_confidence",
        }
        clean = {k: v for k, v in (patch or {}).items() if k in allowed}
        with self._lock:
            data = self._read()
            previous = self._resolve_raw(
                data, user_email=user_email, workspace_id=workspace_id
            )
            if workspace_id:
                bucket = data.setdefault("workspaces", {})
                current = dict(bucket.get(str(workspace_id)) or {})
                current.update(clean)
                bucket[str(workspace_id)] = current
            elif user_email:
                bucket = data.setdefault("users", {})
                key = str(user_email).lower()
                current = dict(bucket.get(key) or {})
                current.update(clean)
                bucket[key] = current
            else:
                current = dict(data.get("default") or _default_policy())
                current.update(clean)
                data["default"] = current
            self._write(data)
        resolved = self.resolve(user_email=user_email, workspace_id=workspace_id)
        self._audit(
            "hybrid_policy_changed",
            user_email=user_email,
            workspace_id=workspace_id,
            previous=previous,
            policy=resolved,
            source=source,
        )
        return resolved


__all__ = ["HybridPolicyService", "DEFAULT_AUTO_COMMIT", "DEFAULT_ALLOW_MULTIMODAL"]
