"""Durable conversation store — kills the 50-message chat_history.json cap.

Conversations are episodic memory, the most valuable raw input a Digital
Brain has; truncating them contradicted "knowledge is durable". This store
keeps every message in SQLite — by default in the same database file as the
knowledge graph, so the existing kg_portability backup/restore covers
conversations with no manifest changes.

The public item shape is exactly the legacy chat_history.json entry
(role/content/timestamp + optional user_email/user_nickname/source/
conversation_id), so every existing consumer of ``get_history()`` keeps
working. Legacy history is imported once, idempotently (content-hash dedup).
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional


def _message_hash(item: Dict[str, Any]) -> str:
    basis = "|".join(
        str(item.get(key) or "")
        for key in ("role", "content", "timestamp", "user_email", "conversation_id", "source")
    )
    return hashlib.sha256(basis.encode("utf-8", "ignore")).hexdigest()


class ConversationStore:
    """Unbounded, per-conversation chat history in SQLite."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Transactional connection that is closed when the block exits.

        ``with sqlite3.connect(...)`` commits but never closes; see
        :meth:`lattice_brain.storage.base.StorageEngine.session`.
        """
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversation_messages (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  message_hash TEXT NOT NULL UNIQUE,
                  conversation_id TEXT,
                  role TEXT NOT NULL,
                  content TEXT NOT NULL,
                  user_email TEXT,
                  user_nickname TEXT,
                  source TEXT,
                  timestamp TEXT NOT NULL,
                  metadata_json TEXT NOT NULL DEFAULT '{}',
                  workspace_id TEXT,
                  organization_id TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_conv_messages_conv
                  ON conversation_messages(conversation_id);
                CREATE INDEX IF NOT EXISTS idx_conv_messages_time
                  ON conversation_messages(timestamp);
                CREATE INDEX IF NOT EXISTS idx_conv_messages_user
                  ON conversation_messages(user_email);
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(conversation_messages)").fetchall()}
            if "workspace_id" not in columns:
                conn.execute("ALTER TABLE conversation_messages ADD COLUMN workspace_id TEXT")
            if "organization_id" not in columns:
                conn.execute("ALTER TABLE conversation_messages ADD COLUMN organization_id TEXT")
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_conv_messages_workspace
                  ON conversation_messages(workspace_id)
                """
            )
            rows = conn.execute(
                """
                SELECT id, metadata_json FROM conversation_messages
                WHERE workspace_id IS NULL OR organization_id IS NULL
                """
            ).fetchall()
            for row in rows:
                try:
                    meta = json.loads(row["metadata_json"] or "{}")
                except Exception:
                    meta = {}
                workspace_id = meta.get("workspace_id")
                organization_id = meta.get("organization_id")
                if workspace_id or organization_id:
                    conn.execute(
                        """
                        UPDATE conversation_messages
                        SET workspace_id=COALESCE(workspace_id, ?),
                            organization_id=COALESCE(organization_id, ?)
                        WHERE id=?
                        """,
                        (workspace_id, organization_id, row["id"]),
                    )

    # ── writes ────────────────────────────────────────────────────────────
    def append(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Persist one chat item (the legacy chat_history.json entry shape)."""
        known = {
            "role",
            "content",
            "timestamp",
            "user_email",
            "user_nickname",
            "source",
            "conversation_id",
            "workspace_id",
            "organization_id",
        }
        extra = {k: v for k, v in item.items() if k not in known}
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO conversation_messages
                  (message_hash, conversation_id, role, content, user_email,
                   user_nickname, source, timestamp, metadata_json, workspace_id,
                   organization_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _message_hash(item),
                    item.get("conversation_id"),
                    str(item.get("role") or "user"),
                    str(item.get("content") or ""),
                    item.get("user_email"),
                    item.get("user_nickname"),
                    item.get("source"),
                    str(item.get("timestamp") or ""),
                    json.dumps(extra, ensure_ascii=False) if extra else "{}",
                    item.get("workspace_id"),
                    item.get("organization_id"),
                ),
            )
        return item

    def import_legacy_json(self, history_file: Path) -> int:
        """One-time, idempotent import of a chat_history.json file.

        Re-running never duplicates (message_hash UNIQUE + INSERT OR IGNORE);
        the source file is left untouched on disk.
        """
        path = Path(history_file)
        if not path.exists():
            return 0
        try:
            with open(path, "r", encoding="utf-8") as fh:
                items = json.load(fh)
        except Exception as exc:
            logging.warning("conversation store: legacy import failed to read %s: %s", path, exc)
            return 0
        if not isinstance(items, list):
            return 0
        imported = 0
        with self._lock, self._connect() as conn:
            for item in items:
                if not isinstance(item, dict):
                    continue
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO conversation_messages
                      (message_hash, conversation_id, role, content, user_email,
                       user_nickname, source, timestamp, metadata_json, workspace_id,
                       organization_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?)
                    """,
                    (
                        _message_hash(item),
                        item.get("conversation_id"),
                        str(item.get("role") or "user"),
                        str(item.get("content") or ""),
                        item.get("user_email"),
                        item.get("user_nickname"),
                        item.get("source"),
                        str(item.get("timestamp") or ""),
                        item.get("workspace_id"),
                        item.get("organization_id"),
                    ),
                )
                imported += cur.rowcount if cur.rowcount > 0 else 0
        if imported:
            logging.info("conversation store: imported %d legacy chat messages from %s", imported, path)
        return imported

    # ── reads (legacy item shape) ─────────────────────────────────────────
    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> Dict[str, Any]:
        item: Dict[str, Any] = {
            "role": row["role"],
            "content": row["content"],
            "timestamp": row["timestamp"],
        }
        for key in ("user_email", "user_nickname", "source", "conversation_id", "workspace_id", "organization_id"):
            if row[key]:
                item[key] = row[key]
        try:
            extra = json.loads(row["metadata_json"] or "{}")
        except Exception:
            extra = {}
        item.update(extra)
        return item

    @staticmethod
    def _scope_sql(
        *,
        user_email: Optional[str] = None,
        allowed_workspaces: Optional[Any] = None,
        include_legacy_global: bool = True,
    ) -> tuple[str, List[Any]]:
        clauses: List[str] = []
        params: List[Any] = []
        if user_email:
            if include_legacy_global:
                clauses.append("(user_email = ? OR user_email IS NULL OR user_email = '')")
            else:
                clauses.append("user_email = ?")
            params.append(user_email)
        if allowed_workspaces is not None:
            allowed = [str(item) for item in allowed_workspaces if item]
            if allowed:
                placeholders = ",".join("?" for _ in allowed)
                if include_legacy_global:
                    clauses.append(f"(workspace_id IN ({placeholders}) OR workspace_id IS NULL OR workspace_id = '')")
                else:
                    clauses.append(f"workspace_id IN ({placeholders})")
                params.extend(allowed)
            elif include_legacy_global:
                clauses.append("(workspace_id IS NULL OR workspace_id = '')")
            else:
                clauses.append("1=0")
        return " AND ".join(clauses), params

    def history(
        self,
        *,
        conversation_id: Optional[str] = None,
        limit: Optional[int] = None,
        user_email: Optional[str] = None,
        allowed_workspaces: Optional[Any] = None,
        include_legacy_global: bool = True,
    ) -> List[Dict[str, Any]]:
        """Chronological items; the unbounded successor of get_history()."""
        query = "SELECT * FROM conversation_messages"
        params: List[Any] = []
        where: List[str] = []
        if conversation_id is not None:
            where.append("conversation_id IS ?" if conversation_id == "" else "conversation_id = ?")
            params.append(None if conversation_id == "" else conversation_id)
        scope_sql, scope_params = self._scope_sql(
            user_email=user_email,
            allowed_workspaces=allowed_workspaces,
            include_legacy_global=include_legacy_global,
        )
        if scope_sql:
            where.append(scope_sql)
            params.extend(scope_params)
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY id ASC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(max(1, int(limit)))
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_item(row) for row in rows]

    def count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM conversation_messages").fetchone()[0]

    def size_bytes(self) -> int:
        try:
            return self.db_path.stat().st_size if self.db_path.exists() else 0
        except OSError:
            return 0

    # ── clears (legacy semantics preserved) ───────────────────────────────
    def clear_all(
        self,
        keep_last: int = 0,
        *,
        user_email: Optional[str] = None,
        allowed_workspaces: Optional[Any] = None,
        include_legacy_global: bool = True,
    ) -> Dict[str, Any]:
        keep_last = max(0, min(int(keep_last or 0), 20))
        with self._lock, self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM conversation_messages").fetchone()[0]
            scope_sql, scope_params = self._scope_sql(
                user_email=user_email,
                allowed_workspaces=allowed_workspaces,
                include_legacy_global=include_legacy_global,
            )
            scope_where = f" WHERE {scope_sql}" if scope_sql else ""
            if keep_last:
                conn.execute(
                    f"""
                    DELETE FROM conversation_messages
                    WHERE id IN (
                      SELECT id FROM conversation_messages{scope_where}
                    )
                    AND id NOT IN (
                      SELECT id FROM conversation_messages{scope_where}
                      ORDER BY id DESC LIMIT ?
                    )
                    """,
                    (*scope_params, *scope_params, keep_last),
                )
            else:
                conn.execute(f"DELETE FROM conversation_messages{scope_where}", scope_params)
            kept = conn.execute("SELECT COUNT(*) FROM conversation_messages").fetchone()[0]
        return {"status": "cleared", "removed": max(0, total - kept), "kept": kept}

    def clear_conversation(
        self,
        conversation_id: str,
        started_at: Optional[str] = None,
        *,
        user_email: Optional[str] = None,
        allowed_workspaces: Optional[Any] = None,
        include_legacy_global: bool = True,
    ) -> Dict[str, Any]:
        """Remove one conversation.

        ``legacy-previous-history`` targets unattributed messages; when
        ``started_at`` is given, unattributed messages from that point on are
        removed too (mirrors the pre-v4 JSON behaviour exactly).
        """
        with self._lock, self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM conversation_messages").fetchone()[0]
            scope_sql, scope_params = self._scope_sql(
                user_email=user_email,
                allowed_workspaces=allowed_workspaces,
                include_legacy_global=include_legacy_global,
            )
            scoped = f" AND {scope_sql}" if scope_sql else ""
            if conversation_id == "legacy-previous-history":
                conn.execute(f"DELETE FROM conversation_messages WHERE conversation_id IS NULL{scoped}", scope_params)
            else:
                conn.execute(
                    f"DELETE FROM conversation_messages WHERE conversation_id = ?{scoped}",
                    (conversation_id, *scope_params),
                )
                if started_at:
                    conn.execute(
                        f"DELETE FROM conversation_messages WHERE conversation_id IS NULL AND timestamp >= ?{scoped}",
                        (str(started_at), *scope_params),
                    )
            kept = conn.execute("SELECT COUNT(*) FROM conversation_messages").fetchone()[0]
        return {
            "status": "cleared",
            "conversation_id": conversation_id,
            "removed": max(0, total - kept),
            "kept": kept,
        }


__all__ = ["ConversationStore"]
