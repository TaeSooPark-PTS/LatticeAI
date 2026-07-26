"""Review-queue persistence extracted from WorkspaceOSStore (v9.9.6).

Workspace-scoped suggestion inbox: automation/trigger runs write drafts here
for the user to approve/dismiss/snooze. **Persistence only** — the transition
policy lives in :mod:`latticeai.services.review_queue`.

Behaviour-preserving move (review 2026-07-27 P2 #8 "대형 모듈 추가 분해 —
동작 보존 이동 우선"): the store delegates the same method names to this
collaborator, following the existing ``WorkspaceRuns`` / ``WorkspaceGraphTrace``
pattern.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .timeutil import now_iso as _now
from .workspace_os_utils import _json_hash, _listify


class WorkspaceReviewItems:
    def __init__(self, store: Any):
        self._store = store

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)

    def create_review_item(
        self,
        *,
        title: str,
        summary: str = "",
        source: str = "workflow_run",
        kind: str = "suggestion",
        payload: Optional[Dict[str, Any]] = None,
        provenance: Optional[Dict[str, Any]] = None,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not str(title or "").strip():
            raise ValueError("title is required")
        state = self.load_state()
        resolved_workspace = self._resolve_scope(workspace_id, state)
        now = _now()
        item = {
            "id": f"review-{_json_hash([title, source, kind, user_email, now])[:16]}",
            "status": "pending",
            "title": title,
            "summary": summary or "",
            "source": source or "workflow_run",
            "kind": kind or "suggestion",
            "payload": dict(payload or {}),
            "provenance": dict(provenance or {}),
            "snoozed_until": None,
            "user_email": user_email,
            "workspace_id": resolved_workspace,
            "created_at": now,
            "updated_at": now,
        }
        state.setdefault("review_items", []).append(item)
        self.save_state(state)
        self.record_timeline_event(
            "review", "review_item_created",
            {"item_id": item["id"], "source": item["source"], "kind": item["kind"]},
            workspace_id=resolved_workspace,
        )
        return item

    def list_review_items(
        self, *, workspace_id: Optional[str] = None, user_email: Optional[str] = None,
        source: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        items = self._scoped(_listify(self.load_state().get("review_items")), workspace_id)
        if user_email:
            items = [item for item in items if item.get("user_email") in {None, user_email}]
        if source:
            items = [item for item in items if item.get("source") == source]
        return list(reversed(items))

    def get_review_item(self, item_id: str, *, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        item = next(
            (it for it in _listify(self.load_state().get("review_items")) if it.get("id") == item_id),
            None,
        )
        if item is None or (workspace_id and self._record_workspace(item) != str(workspace_id)):
            raise FileNotFoundError(item_id)
        return item

    def update_review_item(
        self, item_id: str, *, workspace_id: Optional[str] = None, **fields: Any,
    ) -> Dict[str, Any]:
        state = self.load_state()
        item = next((it for it in _listify(state.get("review_items")) if it.get("id") == item_id), None)
        if item is None or (workspace_id and self._record_workspace(item) != str(workspace_id)):
            raise FileNotFoundError(item_id)
        for key, value in fields.items():
            item[key] = value
        item["updated_at"] = _now()
        self.save_state(state)
        self.record_timeline_event(
            "review", "review_item_updated",
            {"item_id": item_id, "status": item.get("status")},
            workspace_id=self._record_workspace(item),
        )
        return item


__all__ = ["WorkspaceReviewItems"]
