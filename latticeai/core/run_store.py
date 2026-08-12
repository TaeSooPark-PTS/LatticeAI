"""Durable persistence for paused agent runs (review 2026-07-25 Wave 0.1).

The ``awaiting_approval`` loop parked runs in an in-process dict, so a server
restart between pause and resume turned every outstanding approval into a 404
("승인하려다 사라짐"). This store writes each paused run to one JSON file under
the data dir, so a valid, unexpired token can resume its run across restarts
and across worker processes.

Design constraints (deliberate, matching :mod:`latticeai.services.funnel_metrics`):

* **One JSON file per run**, atomic replace on write — no new dependencies.
* **Fail-open on save, fail-closed on resume.** A persistence error never
  breaks the pause response (the in-memory path still works); a load error on
  resume is treated as "run not found".
* **Tokens are never stored in plaintext.** Only the SHA-256 hex digest is
  persisted; resume hashes the presented token and compares digests.
* **Wall-clock expiry.** The in-memory dict keeps its monotonic deadline, but
  monotonic time does not survive a restart — the durable record carries an
  absolute UTC epoch and files past it are swept, never resumed.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from latticeai.core.agent import AgentRunContext, AgentState
from latticeai.core.agent_trace import LoopTrace
from latticeai.core.quiet import quiet
from latticeai.core.security import sha256_hex

LOGGER = logging.getLogger(__name__)

# run_id comes from ``secrets.token_urlsafe`` — validate before any path use
# so a crafted id can never traverse out of the store directory.
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def hash_approval_token(token: str) -> str:
    """Stable digest used both at save and at resume comparison time."""
    return sha256_hex(str(token or ""))


def serialize_run_context(ctx: AgentRunContext) -> Dict[str, Any]:
    """Reduce an :class:`AgentRunContext` to plain JSON-safe data."""
    return {
        "state": ctx.state.value,
        "plan": ctx.plan,
        "transcript": ctx.transcript,
        "retry_count": ctx.retry_count,
        "state_history": ctx.state_history,
        "corrections": ctx.corrections,
        "final_message": ctx.final_message,
        "rollback_log": ctx.rollback_log,
        "executing_model": ctx.executing_model,
        "reviewing_model": ctx.reviewing_model,
        "approved_by_human": ctx.approved_by_human,
        # A paused run resumes under the dial it was planned and approved with,
        # not whatever the preference happens to be at resume time.
        "permission_mode": ctx.permission_mode,
        "trace": {"events": ctx.trace.events, "truncated": ctx.trace.truncated},
    }


def restore_run_context(payload: Dict[str, Any]) -> AgentRunContext:
    """Rebuild an :class:`AgentRunContext` from :func:`serialize_run_context`."""
    ctx = AgentRunContext()
    try:
        ctx.state = AgentState(str(payload.get("state") or AgentState.WAITING_APPROVAL.value))
    except ValueError:
        ctx.state = AgentState.WAITING_APPROVAL
    ctx.plan = dict(payload.get("plan") or {})
    ctx.transcript = list(payload.get("transcript") or [])
    ctx.retry_count = int(payload.get("retry_count") or 0)
    ctx.state_history = list(payload.get("state_history") or [])
    ctx.corrections = list(payload.get("corrections") or [])
    ctx.final_message = str(payload.get("final_message") or "")
    ctx.rollback_log = list(payload.get("rollback_log") or [])
    ctx.executing_model = payload.get("executing_model")
    ctx.reviewing_model = payload.get("reviewing_model")
    ctx.approved_by_human = bool(payload.get("approved_by_human"))
    stored_mode = payload.get("permission_mode")
    ctx.permission_mode = str(stored_mode) if stored_mode else None
    trace_payload = payload.get("trace") or {}
    trace = LoopTrace()
    trace.events = list(trace_payload.get("events") or [])
    trace.truncated = int(trace_payload.get("truncated") or 0)
    ctx.trace = trace
    return ctx


class AgentRunStore:
    """One-JSON-file-per-run persistence for paused approval runs."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def _path_for(self, run_id: str) -> Optional[Path]:
        if not _RUN_ID_RE.fullmatch(str(run_id or "")):
            return None
        return self._root / f"{run_id}.json"

    # ── writes ───────────────────────────────────────────────────────────

    def save(
        self,
        run_id: str,
        *,
        ctx: AgentRunContext,
        req_payload: Dict[str, Any],
        language_hint: str,
        user: str,
        token: str,
        expires_epoch: float,
        expires_at: str,
        legacy_context: bool = False,
    ) -> bool:
        """Persist a paused run. Best-effort: returns False on any failure."""
        path = self._path_for(run_id)
        if path is None:
            return False
        record = {
            "version": 1,
            "run_id": run_id,
            "user": user,
            "language_hint": language_hint,
            "token_hash": hash_approval_token(token),
            "expires_epoch": float(expires_epoch),
            "expires_at": expires_at,
            "legacy_context": bool(legacy_context),
            "req": req_payload,
            "ctx": serialize_run_context(ctx),
        }
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(prefix=path.name, dir=str(self._root))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(record, handle, ensure_ascii=False)
                os.replace(tmp_name, path)
            finally:
                if os.path.exists(tmp_name):
                    try:
                        os.unlink(tmp_name)
                    except OSError:
                        quiet()
            return True
        except Exception as exc:  # noqa: BLE001 — pause must still answer without disk
            LOGGER.warning("agent run store save failed for %s: %s", run_id, exc)
            return False

    def delete(self, run_id: str) -> None:
        path = self._path_for(run_id)
        if path is None:
            return
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            LOGGER.warning("agent run store delete failed for %s: %s", run_id, exc)

    # ── reads ────────────────────────────────────────────────────────────

    def load(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Raw persisted record, or None when missing/corrupt/invalid id."""
        path = self._path_for(run_id)
        if path is None:
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except Exception as exc:  # noqa: BLE001 — corrupt record == not found
            LOGGER.warning("agent run store load failed for %s: %s", run_id, exc)
            return None
        if not isinstance(record, dict) or record.get("run_id") != run_id:
            return None
        return record

    def pending_summaries(self, user: Optional[str] = None) -> List[Dict[str, Any]]:
        """Unexpired pending runs (optionally per user) for surfacing in UI."""
        summaries: List[Dict[str, Any]] = []
        now = time.time()
        try:
            paths = sorted(self._root.glob("*.json"))
        except OSError:
            return summaries
        for path in paths:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                quiet()
                continue
            if not isinstance(record, dict):
                continue
            if float(record.get("expires_epoch") or 0) <= now:
                continue
            if user is not None and record.get("user") != user:
                continue
            plan = (record.get("ctx") or {}).get("plan") or {}
            summaries.append({
                "run_id": record.get("run_id"),
                "user": record.get("user"),
                "goal": str(plan.get("goal") or "")[:200],
                "expires_at": record.get("expires_at"),
            })
        return summaries

    def sweep_expired(
        self,
        now_epoch: Optional[float] = None,
        *,
        retention_seconds: float = 86400.0,
    ) -> int:
        """Remove long-expired run files; returns how many were removed.

        Recently expired records are *kept* for ``retention_seconds`` so that
        a resume attempt after expiry can still answer 410 with a one-click
        replan hint (the original request message) instead of a bare 404.
        """
        removed = 0
        now = time.time() if now_epoch is None else float(now_epoch)
        try:
            paths = list(self._root.glob("*.json"))
        except OSError:
            return removed
        for path in paths:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                expires = float(record.get("expires_epoch") or 0)
            except Exception:  # noqa: BLE001 — unreadable == expired garbage
                expires = -float(retention_seconds)
            if expires + float(retention_seconds) <= now:
                try:
                    path.unlink(missing_ok=True)
                    removed += 1
                except OSError:
                    quiet()
        return removed


__all__ = [
    "AgentRunStore",
    "hash_approval_token",
    "restore_run_context",
    "serialize_run_context",
]
