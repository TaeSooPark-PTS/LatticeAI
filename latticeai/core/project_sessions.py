"""Persistent project sessions — the multi-turn project loop (v9.9.6).

A single agent run is well modelled: PLAN → EXECUTE → VERIFY, with a durable
approval pause and a transcript. What was missing is the loop *above* it —
"만들고 → 고치고 → 검증하고 → 다시 고치는" work that spans several runs and
several days. Each run started from a blank workspace picture, so the second
turn could not see what the first produced, what still failed verification, or
what was left to do.

A project session is that missing state:

* ``files`` — every artifact the project's runs actually produced;
* ``todos`` — what is still open (explicit, user- or agent-authored);
* ``last_verification`` — the most recent honest outcome (never upgraded);
* ``runs`` — the run history that got there.

Storage mirrors :mod:`latticeai.core.run_store`: one JSON file per session
under the data dir, atomic replace on write, no new dependencies. Reads are
fail-open (a corrupt file is skipped, not fatal); writes never break a run —
a project session is context, not a gate.
"""

from __future__ import annotations

import logging
import re
import secrets
from pathlib import Path
from typing import Any, Dict, List, Optional

from latticeai.core.io_utils import atomic_write_json
from latticeai.core.timeutil import now_iso

LOGGER = logging.getLogger(__name__)

__all__ = ["ProjectSessionStore", "PROJECT_STATUSES"]

# Terminal-ish project states. "active" is the default; "archived" hides a
# project from the default listing without deleting its history.
PROJECT_STATUSES = ("active", "archived")

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_MAX_FILES = 200
_MAX_TODOS = 100
_MAX_RUNS = 100


def _clean(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


class ProjectSessionStore:
    """JSON-file store for long-running project sessions."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    # ── paths ────────────────────────────────────────────────────────────
    def _path(self, session_id: str) -> Optional[Path]:
        if not _SESSION_ID_RE.match(str(session_id or "")):
            return None
        return self._root / f"{session_id}.json"

    def _ensure_root(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)

    # ── read ─────────────────────────────────────────────────────────────
    def _load(self, session_id: str) -> Optional[Dict[str, Any]]:
        path = self._path(session_id)
        if path is None or not path.exists():
            return None
        try:
            import json

            with path.open(encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else None
        except Exception as exc:  # noqa: BLE001 — a bad file is skipped, not fatal
            LOGGER.warning("project session load failed for %s: %s", session_id, exc)
            return None

    def get(
        self,
        session_id: str,
        *,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """One session, scoped. Returns None when it does not exist or is
        outside the caller's scope — never a partial record."""
        record = self._load(session_id)
        if record is None:
            return None
        if user_email is not None and record.get("user_email") != user_email:
            return None
        if workspace_id is not None and record.get("workspace_id") != workspace_id:
            return None
        return record

    def list(
        self,
        *,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
        status: str = "active",
    ) -> Dict[str, Any]:
        """Sessions for a caller, newest first. ``status="all"`` includes archived."""
        if not self._root.exists():
            return {"projects": [], "count": 0}
        sessions: List[Dict[str, Any]] = []
        for path in self._root.glob("*.json"):
            record = self._load(path.stem)
            if record is None:
                continue
            if user_email is not None and record.get("user_email") != user_email:
                continue
            if workspace_id is not None and record.get("workspace_id") != workspace_id:
                continue
            if status != "all" and record.get("status") != status:
                continue
            sessions.append(record)
        sessions.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return {"projects": sessions, "count": len(sessions)}

    # ── write ────────────────────────────────────────────────────────────
    def _save(self, record: Dict[str, Any]) -> Dict[str, Any]:
        record["updated_at"] = now_iso()
        path = self._path(str(record.get("id") or ""))
        if path is None:
            raise ValueError("invalid project session id")
        try:
            self._ensure_root()
            atomic_write_json(path, record)
        except Exception as exc:  # noqa: BLE001 — persistence never breaks a run
            LOGGER.warning("project session save failed for %s: %s", record.get("id"), exc)
        return record

    def create(
        self,
        *,
        title: str,
        goal: str = "",
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        session_id = secrets.token_urlsafe(12)
        record = {
            "id": session_id,
            "title": _clean(title, 200) or "프로젝트",
            "goal": _clean(goal, 2000),
            "status": "active",
            "user_email": user_email,
            "workspace_id": workspace_id,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "files": [],
            "todos": [],
            "runs": [],
            "last_verification": None,
        }
        return self._save(record)

    def update(
        self,
        session_id: str,
        *,
        title: Optional[str] = None,
        goal: Optional[str] = None,
        status: Optional[str] = None,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        record = self.get(session_id, user_email=user_email, workspace_id=workspace_id)
        if record is None:
            return None
        if title is not None:
            record["title"] = _clean(title, 200) or record["title"]
        if goal is not None:
            record["goal"] = _clean(goal, 2000)
        if status is not None and status in PROJECT_STATUSES:
            record["status"] = status
        return self._save(record)

    def delete(
        self,
        session_id: str,
        *,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> bool:
        record = self.get(session_id, user_email=user_email, workspace_id=workspace_id)
        if record is None:
            return False
        path = self._path(session_id)
        try:
            if path is not None and path.exists():
                path.unlink()
            return True
        except OSError as exc:
            LOGGER.warning("project session delete failed for %s: %s", session_id, exc)
            return False

    def set_todos(
        self,
        session_id: str,
        todos: List[Any],
        *,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Replace the open-work list. Accepts strings or ``{text, done}`` dicts."""
        record = self.get(session_id, user_email=user_email, workspace_id=workspace_id)
        if record is None:
            return None
        normalized: List[Dict[str, Any]] = []
        for raw in list(todos or [])[:_MAX_TODOS]:
            if isinstance(raw, dict):
                text = _clean(raw.get("text"), 300)
                done = bool(raw.get("done"))
            else:
                text = _clean(raw, 300)
                done = False
            if text:
                normalized.append({"text": text, "done": done})
        record["todos"] = normalized
        return self._save(record)

    def record_run(
        self,
        session_id: str,
        *,
        run_id: str = "",
        goal: str = "",
        status: str = "",
        final_state: str = "",
        files: Optional[List[Any]] = None,
        explanation: Optional[Dict[str, Any]] = None,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Fold one finished agent run into the project's state.

        The verification result is recorded exactly as the run reported it —
        a NEEDS_REVIEW run never becomes a project's "done".
        """
        record = self.get(session_id, user_email=user_email, workspace_id=workspace_id)
        if record is None:
            return None
        paths: List[str] = []
        for entry in list(files or []):
            path = entry if isinstance(entry, str) else (entry or {}).get("path")
            path = _clean(path, 400)
            if path and path not in paths:
                paths.append(path)
        known = list(record.get("files") or [])
        for path in paths:
            if path not in known:
                known.append(path)
        record["files"] = known[-_MAX_FILES:]
        headline = ""
        next_step = ""
        if isinstance(explanation, dict):
            headline = _clean((explanation.get("headline") or {}).get("ko"), 400)
            next_step = _clean((explanation.get("next_step") or {}).get("ko"), 400)
        entry = {
            "run_id": _clean(run_id, 120),
            "goal": _clean(goal, 400),
            "status": _clean(status, 40),
            "final_state": _clean(final_state, 40),
            "files": paths,
            "at": now_iso(),
            **({"headline": headline} if headline else {}),
            **({"next_step": next_step} if next_step else {}),
        }
        runs = list(record.get("runs") or [])
        runs.append(entry)
        record["runs"] = runs[-_MAX_RUNS:]
        record["last_verification"] = {
            "final_state": entry["final_state"],
            "ok": bool(isinstance(explanation, dict) and explanation.get("ok")),
            "at": entry["at"],
            **({"headline": headline} if headline else {}),
            # Failure-learning loop (review 루프 §3): what the *last* attempt
            # says to do differently travels into the next run's plan context
            # instead of dying with the run.
            **({"next_step": next_step} if next_step else {}),
        }
        return self._save(record)

    # ── prompt context ───────────────────────────────────────────────────
    def summary(
        self,
        session_id: str,
        *,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
        max_files: int = 15,
        max_todos: int = 10,
    ) -> str:
        """Prompt block describing where this project stands.

        Injected into the planner/executor context so a later run sees the
        files that already exist, what is still open, and whether the last
        attempt actually passed. Empty string when there is nothing to say —
        an empty project must not pad the prompt.
        """
        record = self.get(session_id, user_email=user_email, workspace_id=workspace_id)
        if record is None:
            return ""
        lines: List[str] = []
        title = record.get("title") or ""
        goal = record.get("goal") or ""
        lines.append(f"Project: {title}" + (f" — {goal}" if goal else ""))
        files = list(record.get("files") or [])[-max_files:]
        if files:
            lines.append("Files this project has already produced (they exist now):")
            lines.extend(f"- {path}" for path in files)
        open_todos = [
            todo for todo in (record.get("todos") or []) if not todo.get("done")
        ][:max_todos]
        if open_todos:
            lines.append("Still open:")
            lines.extend(f"- {todo['text']}" for todo in open_todos)
        verification = record.get("last_verification")
        if isinstance(verification, dict) and verification.get("final_state"):
            verdict = verification["final_state"]
            headline = verification.get("headline") or ""
            lines.append(
                f"Last verification: {verdict}" + (f" — {headline}" if headline else "")
            )
            next_step = verification.get("next_step") or ""
            if next_step and not verification.get("ok"):
                # The previous attempt's own diagnosis, so this plan does not
                # repeat the same failure.
                lines.append(f"What the last attempt said to do differently: {next_step}")
        # Only the header line means nothing worth injecting.
        return "\n".join(lines) if len(lines) > 1 else ""
