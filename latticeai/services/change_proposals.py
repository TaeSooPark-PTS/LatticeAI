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
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from latticeai.core.quiet import quiet
from latticeai.core.security import sha256_hex
from latticeai.core.tool_governor import classify_tool_call
from latticeai.core.workspace_reorganization import (
    REORG_KIND,
    apply_reorganization,
    propose_reorganization,
)

LOGGER = logging.getLogger(__name__)

_MAX_STAGED_BYTES = 400_000
_MAX_DIFF_LINES = 400
_SMALL_TIER_DIFF_LINES = 40


class ProposalConflictError(ValueError):
    """The target file changed (or the proposal was already resolved) between
    staging and approval — applying now would silently destroy user edits.

    Subclasses :class:`ValueError` deliberately: API surfaces that only know
    ``except ValueError`` degrade to a 4xx instead of applying or crashing,
    while conflict-aware surfaces catch this class first and answer **409**
    with a rebase hint.
    """

    def __init__(
        self,
        *,
        reason: str,
        path: str,
        kind: str,
        base_sha256: str = "",
        current_sha256: str = "",
        rebase_hint: str = "",
    ) -> None:
        self.reason = reason
        self.path = path
        self.kind = kind
        self.base_sha256 = base_sha256
        self.current_sha256 = current_sha256
        self.rebase_hint = rebase_hint or (
            "제안 생성 이후 파일 상태가 바뀌었습니다. 이 제안을 거부하고 "
            "현재 파일 내용을 기준으로 제안을 다시 생성하세요."
        )
        super().__init__(f"change proposal conflict ({reason}): {path}")

    def to_detail(self) -> Dict[str, Any]:
        """409 response body — no staged content, just what the UI needs."""
        return {
            "error": "change_proposal_conflict",
            "conflict": True,
            "reason": self.reason,
            "path": self.path,
            "kind": self.kind,
            "base_sha256": self.base_sha256,
            "current_sha256": self.current_sha256,
            "rebase_hint": self.rebase_hint,
        }


def _sha256_text(content: str) -> str:
    return sha256_hex(content)


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
        # One protected section for status-check → conflict-check → apply →
        # transition. In-process serialization is the right weight here: the
        # review queue lives in this process and the write itself is atomic
        # (temp file + os.replace), so a cross-process file lock adds nothing.
        self._apply_lock = threading.Lock()

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
        return self._snapshot(path)[1]

    def _snapshot(self, path: str) -> Tuple[bool, str]:
        """(exists, content) of the target *as the proposal pipeline sees it*.

        Both staging and the approve-time conflict check go through this one
        normalization (truncate → utf-8 with replacement), so an unchanged
        file always hashes identically at both points in time.
        """
        try:
            target = self._resolve_path(path)
            if not target.is_file():
                return False, ""
            raw = target.read_bytes()[:_MAX_STAGED_BYTES]
            return True, raw.decode("utf-8", errors="replace")
        except Exception:
            LOGGER.exception("change proposal read failed")
            return False, ""

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
        base_exists, before = self._snapshot(path)
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
                # Base snapshot for the approve-time conflict check: an empty
                # base_sha256 with base_exists=False means "proposed against a
                # missing file", never "hash of the empty string".
                "base_exists": base_exists,
                "base_sha256": _sha256_text(before) if base_exists else "",
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

    def propose_reorganization(
        self,
        *,
        root: str = "",
        graph: Any = None,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
        max_moves: int = 20,
    ) -> Dict[str, Any]:
        """Stage a whole-folder reorganization as one reviewable proposal.

        Moves only — see :mod:`latticeai.core.workspace_reorganization`. It
        goes through this service (rather than the review queue directly) so
        approving it from the Review Center applies it the same way every
        other staged change is applied.
        """
        return propose_reorganization(
            root=root,
            resolve_path=self._resolve_path,
            review_queue=self._review_queue,
            graph=graph,
            user_email=user_email,
            workspace_id=workspace_id,
            max_moves=max_moves,
            audit=self._audit,
        )

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
        """Apply the staged change exactly as reviewed — but only if the file
        on disk still matches the base snapshot the reviewer looked at.

        Raises :class:`ProposalConflictError` (→ HTTP 409) when the target
        drifted since staging or the proposal was already resolved; nothing
        touches disk in that case, so out-of-band user edits are preserved.
        """
        with self._apply_lock:
            item = self._review_queue.get(item_id, workspace_id=workspace_id)
            if item.get("source") != "change_proposal":
                raise KeyError(f"not a change proposal: {item_id}")
            payload = item.get("payload") or {}
            kind = str(item.get("kind") or "")
            path = str(payload.get("path") or payload.get("root") or "")
            if kind not in ("file_update", "file_delete", REORG_KIND):
                raise ValueError(f"unknown change proposal kind: {kind}")

            # Duplicate/concurrent approval guard: a resolved proposal must
            # never re-apply (the disk may have moved on since it was applied).
            status = str(item.get("status") or "pending")
            if status not in ("pending", "snoozed"):
                raise ProposalConflictError(
                    reason=f"already_{status}", path=path, kind=kind,
                    rebase_hint="이미 처리된 제안입니다. 다시 적용할 수 없습니다.",
                )

            moves: Optional[Dict[str, Any]] = None
            if kind == REORG_KIND:
                # A reorganization has no single base file to hash: each move
                # is re-checked at apply time and a drifted one is skipped,
                # never forced (see workspace_reorganization.apply_*).
                moves = apply_reorganization(payload, resolve_path=self._resolve_path)
            else:
                self._check_base_unchanged(payload, path=path, kind=kind)
                target = self._resolve_path(path)
                if kind == "file_update":
                    self._atomic_write(target, str(payload.get("new_content") or ""))
                elif target.is_file():  # file_delete — re-verified inside the lock
                    target.unlink()
            approved = self._review_queue.approve(item_id, workspace_id=workspace_id)
        self._audit(
            "change_proposal_applied", user_email=user_email,
            proposal_id=item_id, path=path, kind=kind,
        )
        result = {"item": approved, "applied": True, "path": path, "kind": kind}
        if moves is not None:
            result["moves"] = moves
        return result

    def _check_base_unchanged(
        self, payload: Dict[str, Any], *, path: str, kind: str
    ) -> None:
        """Compare the disk state *now* against the staged base snapshot."""
        if "base_sha256" not in payload or "base_exists" not in payload:
            # Legacy proposal staged before base snapshots existed — keep the
            # historical apply-as-reviewed behavior rather than rejecting it.
            return
        base_exists = bool(payload.get("base_exists"))
        base_sha256 = str(payload.get("base_sha256") or "")
        current_exists, current_content = self._snapshot(path)
        current_sha256 = _sha256_text(current_content) if current_exists else ""
        if not base_exists:
            if current_exists:
                raise ProposalConflictError(
                    reason="file_created_since_proposal", path=path, kind=kind,
                    current_sha256=current_sha256,
                )
            return
        if not current_exists:
            raise ProposalConflictError(
                reason="file_deleted_since_proposal", path=path, kind=kind,
                base_sha256=base_sha256,
            )
        if current_sha256 != base_sha256:
            raise ProposalConflictError(
                reason="file_modified_since_proposal", path=path, kind=kind,
                base_sha256=base_sha256, current_sha256=current_sha256,
            )

    @staticmethod
    def _atomic_write(target: Path, content: str) -> None:
        """Write via a same-directory temp file + ``os.replace`` so readers
        never observe a partially written proposal."""
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(target.parent), prefix=f".{target.name}.", suffix=".staged"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
            os.replace(tmp_name, target)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                quiet()
            raise

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


__all__ = ["ChangeProposalService", "ProposalConflictError"]
