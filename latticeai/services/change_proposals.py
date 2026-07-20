"""Change proposals — 수정/삭제는 제안으로, 생성은 바로 (v9.6.0).

The consent model most users actually want is git's: creating something new
is cheap and reversible, but changing or deleting what already exists
deserves a review. This service implements that model for the agent
workspace:

* **additive** operations (new files, new notes) run with minimal friction;
* **mutation/destructive** operations (overwrite, in-place edit, delete of
  an existing file) are *staged as proposals* instead of applied: a review
  item (source ``change_proposal``) carrying the target path, a unified
  diff, the exact resulting content, and a small/large tier. Nothing changes
  on disk until the user approves; rejecting discards the staged change.

Approving applies the staged content exactly as reviewed (never recomputed),
then marks the review item approved — so the review timeline doubles as the
change audit log. The classification itself lives in
:mod:`latticeai.core.tool_governor` so every entry point shares one policy.
"""

from __future__ import annotations

import difflib
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from latticeai.core.tool_governor import classify_tool_call

LOGGER = logging.getLogger(__name__)

_MAX_STAGED_BYTES = 400_000
_MAX_DIFF_LINES = 400
_SMALL_TIER_DIFF_LINES = 40


def _unified_diff(before: str, after: str, path: str) -> List[str]:
    lines = list(
        difflib.unified_diff(
            before.splitlines(), after.splitlines(),
            fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="",
        )
    )
    return lines[:_MAX_DIFF_LINES]


class ChangeProposalService:
    """Stages mutation/destructive file changes as reviewable proposals."""

    #: Tools whose plan-time approval is delegated to per-call governance.
    governed_tools = frozenset({"write_file", "edit_file"})

    def __init__(
        self,
        *,
        review_queue: Any,
        resolve_path: Callable[[str], Path],
        audit: Optional[Callable[..., None]] = None,
    ) -> None:
        self._review_queue = review_queue
        self._resolve_path = resolve_path
        self._audit = audit or (lambda *a, **kw: None)

    # ── classification / staging (agent-loop governor port) ─────────────

    def review(
        self,
        name: str,
        args: Dict[str, Any],
        *,
        policy: Optional[Dict[str, Any]] = None,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Governor port for the agent loop.

        Returns ``None`` to fall through to the existing gates, or a verdict:
        ``{"decision": "allow_additive"}`` (execute without extra approval) /
        ``{"decision": "proposed", "proposal": {...}}`` (staged for review).
        """
        if name not in {"write_file", "edit_file"}:
            return None
        verdict = classify_tool_call(
            name, args, policy=policy, path_exists=self._path_exists
        )
        if verdict["change_class"] == "additive":
            return {"decision": "allow_additive", "classification": verdict}
        if not verdict["proposal_required"]:
            return None

        path = str(args.get("path") or "")
        after = self._staged_content(name, args)
        if after is None:
            # The edit cannot be computed deterministically (e.g. old_string
            # not found) — let the normal tool path surface the real error.
            return None
        try:
            proposal = self.propose_file_update(
                path=path,
                new_content=after,
                proposed_by="agent",
                reason=verdict["reason"],
                user_email=user_email,
                workspace_id=workspace_id,
                context={
                    # Full provenance for the Review Center: which tool asked,
                    # how the governor classified it, and the policy risk.
                    "tool": name,
                    "change_class": verdict.get("change_class"),
                    "risk": (policy or {}).get("risk"),
                    "conversation_id": conversation_id,
                    "source_detail": "agent change governor",
                },
            )
        except Exception:
            LOGGER.exception("change proposal staging failed")
            return None
        return {"decision": "proposed", "classification": verdict, "proposal": proposal}

    def _path_exists(self, path: str) -> bool:
        try:
            return self._resolve_path(path).is_file()
        except Exception:
            return False

    def _read_before(self, path: str) -> str:
        try:
            target = self._resolve_path(path)
            if not target.is_file():
                return ""
            raw = target.read_bytes()[:_MAX_STAGED_BYTES]
            return raw.decode("utf-8", errors="replace")
        except Exception:
            LOGGER.exception("change proposal read failed")
            return ""

    def _staged_content(self, name: str, args: Dict[str, Any]) -> Optional[str]:
        if name == "write_file":
            return str(args.get("content") or "")[:_MAX_STAGED_BYTES]
        if name == "edit_file":
            before = self._read_before(str(args.get("path") or ""))
            old = str(args.get("old_string") or "")
            new = str(args.get("new_string") or "")
            if not old or old not in before:
                return None
            if args.get("replace_all"):
                return before.replace(old, new)[:_MAX_STAGED_BYTES]
            if before.count(old) != 1:
                return None
            return before.replace(old, new, 1)[:_MAX_STAGED_BYTES]
        return None

    # ── proposal creation ────────────────────────────────────────────────

    def propose_file_update(
        self,
        *,
        path: str,
        new_content: str,
        proposed_by: str = "agent",
        reason: str = "",
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        before = self._read_before(path)
        diff = _unified_diff(before, new_content, path)
        tier = "small" if len(diff) <= _SMALL_TIER_DIFF_LINES else "large"
        provenance: Dict[str, Any] = {"proposed_by": proposed_by, "reason": reason}
        for key, value in (context or {}).items():
            if value is not None and key not in provenance:
                provenance[key] = value
        item = self._review_queue.create(
            title=f"파일 수정 제안: {path}",
            summary=reason or "기존 파일을 변경하는 작업이라 검토 후 적용됩니다.",
            source="change_proposal",
            kind="file_update",
            payload={
                "path": path,
                "diff": diff,
                "new_content": new_content[:_MAX_STAGED_BYTES],
                "tier": tier,
                "before_bytes": len(before.encode("utf-8")),
                "after_bytes": len(new_content.encode("utf-8")),
            },
            provenance=provenance,
            user_email=user_email,
            workspace_id=workspace_id,
        )
        self._audit(
            "change_proposal_created", user_email=user_email,
            proposal_id=item.get("id"), path=path, kind="file_update", tier=tier,
        )
        return item

    def propose_file_delete(
        self,
        *,
        path: str,
        proposed_by: str = "agent",
        reason: str = "",
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        before = self._read_before(path)
        item = self._review_queue.create(
            title=f"파일 삭제 제안: {path}",
            summary=reason or "기존 파일을 삭제하는 작업이라 검토 후 적용됩니다.",
            source="change_proposal",
            kind="file_delete",
            payload={
                "path": path,
                "diff": _unified_diff(before, "", path),
                "tier": "large",
                "before_bytes": len(before.encode("utf-8")),
                "after_bytes": 0,
            },
            provenance={"proposed_by": proposed_by, "reason": reason},
            user_email=user_email,
            workspace_id=workspace_id,
        )
        self._audit(
            "change_proposal_created", user_email=user_email,
            proposal_id=item.get("id"), path=path, kind="file_delete", tier="large",
        )
        return item

    # ── listing / apply / reject ─────────────────────────────────────────

    def pending(
        self, *, user_email: Optional[str] = None, workspace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        listed = self._review_queue.list(
            workspace_id=workspace_id, user_email=user_email,
            status="pending", source="change_proposal",
        )
        items = listed.get("items") or []
        return {
            "items": items,
            "count": len(items),
            "contract": {
                "additive_writes": "auto",
                "mutations": "proposal",
                "deletions": "proposal",
                "applied_content": "exactly_as_reviewed",
            },
        }

    def counts(
        self, *, user_email: Optional[str] = None, workspace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Pending proposal count for the Review Center badge."""
        listed = self._review_queue.list(
            workspace_id=workspace_id, user_email=user_email,
            status="pending", source="change_proposal",
        )
        return {"pending": len(listed.get("items") or [])}

    def get_proposal(
        self, item_id: str, *, workspace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Full proposal detail (diff + staged content) for the preview UI.

        Raises ``KeyError`` when the id exists but is not a change proposal,
        so the API surface cannot leak arbitrary review items.
        """
        item = self._review_queue.get(item_id, workspace_id=workspace_id)
        if item.get("source") != "change_proposal":
            raise KeyError(f"not a change proposal: {item_id}")
        return item

    def approve_and_apply(
        self, item_id: str, *, user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        item = self._review_queue.get(item_id, workspace_id=workspace_id)
        if item.get("source") != "change_proposal":
            raise KeyError(f"not a change proposal: {item_id}")
        payload = item.get("payload") or {}
        kind = item.get("kind")
        path = str(payload.get("path") or "")
        target = self._resolve_path(path)
        if kind == "file_update":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(payload.get("new_content") or ""), encoding="utf-8")
        elif kind == "file_delete":
            if target.is_file():
                target.unlink()
        else:
            raise ValueError(f"unknown change proposal kind: {kind}")
        approved = self._review_queue.approve(item_id, workspace_id=workspace_id)
        self._audit(
            "change_proposal_applied", user_email=user_email,
            proposal_id=item_id, path=path, kind=kind,
        )
        return {"item": approved, "applied": True, "path": path, "kind": kind}

    def reject(
        self, item_id: str, *, user_email: Optional[str] = None,
        workspace_id: Optional[str] = None, reason: str = "",
    ) -> Dict[str, Any]:
        self.get_proposal(item_id, workspace_id=workspace_id)  # source guard
        reason = str(reason or "").strip()[:500]
        if reason:
            try:
                dismissed = self._review_queue.dismiss(
                    item_id, workspace_id=workspace_id, reason=reason
                )
            except TypeError:
                # Older/fake queues without reason support still dismiss.
                dismissed = self._review_queue.dismiss(item_id, workspace_id=workspace_id)
        else:
            dismissed = self._review_queue.dismiss(item_id, workspace_id=workspace_id)
        self._audit(
            "change_proposal_rejected", user_email=user_email, proposal_id=item_id,
            reason=reason or None,
        )
        return {"item": dismissed, "applied": False, "reason": reason}


__all__ = ["ChangeProposalService"]
