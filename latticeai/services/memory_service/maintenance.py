"""The mutating half: prune, compact, rebuild, clear.

Every deletion path goes through :meth:`prune`, and :meth:`prune` intersects
its targets with the caller's *own* memories first. That ownership guard is the
point: a forged id belonging to another user is refused and reported as
``skipped``, never silently deleted. :meth:`clear` is a thin scope router over
the same guard, and refuses ``graph`` outright because the graph is not
workspace-scoped.

Partial failure is reported, not swallowed: ``status`` becomes ``partial`` when
some deletions landed and ``error`` when none did.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ._contract import MemoryCore as _Core
from .constants import LOGGER, WORKSPACE_KINDS


class MemoryMaintenanceMixin(_Core):
    """Prune / compact / rebuild / clear. Mixed into ``MemoryService``."""

    # ── mutating operations ───────────────────────────────────────────────
    def prune(
        self,
        *,
        ids: Optional[List[str]] = None,
        kind: Optional[str] = None,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        # Ownership guard: a caller may only prune memories they own. Both the
        # explicit-id and kind paths are intersected with the caller's own
        # memories, so a forged id for another user's memory is refused, not
        # silently deleted.
        owned_ids = {
            m["id"]
            for m in self._workspace_memories(user_email=user_email, workspace_id=workspace_id)
            if m.get("id")
        }
        removed: List[str] = []
        failed: List[Dict[str, str]] = []
        skipped: List[str] = []
        target_ids: List[str] = []
        seen: set = set()
        for mid in (ids or []):
            if mid in seen:
                continue
            seen.add(mid)
            if mid in owned_ids:
                target_ids.append(mid)
            else:
                skipped.append(mid)
        if kind:
            for m in self._workspace_memories(user_email=user_email, workspace_id=workspace_id):
                if m.get("kind") == kind and m.get("id") and m["id"] not in seen:
                    seen.add(m["id"])
                    target_ids.append(m["id"])
        for mid in target_ids:
            try:
                self._store.delete_memory(mid)
                removed.append(mid)
            except Exception as exc:
                LOGGER.exception("memory deletion failed for %s", mid)
                failed.append({"id": mid, "detail": str(exc)})
        result: Dict[str, Any] = {"removed": removed, "count": len(removed)}
        if skipped:
            result["skipped"] = skipped
        if failed:
            result["failed"] = failed
            result["status"] = "partial" if removed else "error"
        return result

    def compact(
        self,
        *,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Dedupe workspace memories with identical (kind, content)."""
        seen: set = set()
        removed: List[str] = []
        failed: List[Dict[str, str]] = []
        # Oldest first so the first occurrence (oldest) is kept.
        memories = list(reversed(self._workspace_memories(user_email=user_email, workspace_id=workspace_id)))
        for m in memories:
            key = (m.get("kind"), str(m.get("content") or "").strip())
            if key in seen:
                if m.get("id"):
                    try:
                        self._store.delete_memory(m["id"])
                        removed.append(m["id"])
                    except Exception as exc:
                        LOGGER.exception("memory compaction deletion failed for %s", m["id"])
                        failed.append({"id": m["id"], "detail": str(exc)})
            else:
                seen.add(key)
        return {
            "compacted": len(removed),
            "removed": removed,
            "remaining": len(seen),
            "failed": failed,
            "status": "partial" if failed and removed else "error" if failed else "ok",
        }

    def rebuild(self, target: str = "vector") -> Dict[str, Any]:
        if target in {"vector", "index", "vector_index"}:
            if not self._enable_graph:
                return {"status": "unavailable", "detail": "Knowledge graph / vector index disabled."}
            try:
                result = self._kg.rebuild_vector_index()
                return {"status": "ok", "target": "vector_index", "result": result}
            except Exception as exc:
                return {"status": "error", "detail": str(exc)}
        return {"status": "error", "detail": f"Unknown rebuild target: {target}"}

    def clear(
        self,
        *,
        scope: str,
        confirm: bool = False,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not confirm:
            raise ValueError("clear requires confirm=true")
        if scope in WORKSPACE_KINDS:
            result = self.prune(kind=scope, user_email=user_email, workspace_id=workspace_id)
            return {"cleared": scope, **result}
        if scope == "workspace":  # pragma: no cover — shadowed: "workspace" is itself a WORKSPACE_KINDS member, so the by-kind branch above always wins; reordering would widen what clear() deletes, which is an owner decision
            ids = [m["id"] for m in self._workspace_memories(user_email=user_email, workspace_id=workspace_id) if m.get("id")]
            result = self.prune(ids=ids, user_email=user_email, workspace_id=workspace_id)
            return {"cleared": "workspace", **result}
        if scope == "graph":
            raise ValueError("graph clear is disabled from Memory Manager because it is not workspace-scoped")
        raise ValueError(f"unsupported clear scope: {scope}")
