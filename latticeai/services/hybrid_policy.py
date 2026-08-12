"""User-configurable hybrid policy (Phase 3).

Controls:

* which node types / metadata flags are blocked from leaving the machine
* whether cloud-derived KG expansion auto-commits or stages for review
* whether multimodal (video) cloud calls are allowed when cloud mode is on

Persisted under the data dir, scoped like NetworkBoundaryService
(workspace → user → default).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from latticeai.core.network_boundary import (
    HARD_BLOCK_METADATA_FLAGS,
    HARD_BLOCK_NODE_TYPES,
)
from latticeai.services.mode_store import JsonBackedModeService

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


class HybridPolicyService(JsonBackedModeService[Dict[str, Any]]):
    """Load / save hybrid cloud policy."""

    FILENAME = "hybrid_policy.json"

    def _default_entry(self) -> Any:
        return _default_policy()

    def _resolve_from(
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
            previous = self._resolve_from(
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
