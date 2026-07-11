"""User identity store and v4 UUID migration helpers."""

from __future__ import annotations

import json
import shutil
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from .io_utils import atomic_write_json
from .timeutil import now_iso as _now


USER_NAMESPACE = uuid.UUID("5d6d4480-cf79-49c3-a6d0-4c6eec3224d6")


def normalize_email(email: str) -> str:
    return str(email or "").strip().lower()


def stable_user_id(email: str) -> str:
    return f"user:{uuid.uuid5(USER_NAMESPACE, normalize_email(email))}"


def ensure_user_identity(email: str, user: Dict[str, Any]) -> bool:
    changed = False
    normalized = normalize_email(email or user.get("email") or "")
    if not user.get("id"):
        user["id"] = stable_user_id(normalized)
        changed = True
    if user.get("email") != normalized:
        user["email"] = normalized
        changed = True
    return changed


def migrate_users(users: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, str], bool]:
    migrated: Dict[str, Any] = {}
    email_to_id: Dict[str, str] = {}
    changed = False
    for raw_email, raw_user in (users or {}).items():
        if not isinstance(raw_user, dict):
            continue
        email = normalize_email(raw_user.get("email") or raw_email)
        user = dict(raw_user)
        changed = ensure_user_identity(email, user) or changed
        if raw_email != email:
            changed = True
        if email in migrated:
            existing = migrated[email]
            merged = {**existing, **user}
            merged["id"] = existing.get("id") or user.get("id") or stable_user_id(email)
            if isinstance(existing.get("api_keys"), dict) or isinstance(user.get("api_keys"), dict):
                merged["api_keys"] = {**(existing.get("api_keys") or {}), **(user.get("api_keys") or {})}
            user = merged
            changed = True
        migrated[email] = user
        email_to_id[email] = user["id"]
    return migrated, email_to_id, changed


def load_users_file(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            loaded = {}
    except Exception:
        loaded = {}
    migrated, _, changed = migrate_users(loaded)
    if changed:
        backup = path.with_name(f"{path.name}.pre-user-uuid.{_now().replace(':', '-')}.json")
        try:
            shutil.copy2(path, backup)
        except Exception:
            pass
        atomic_write_json(path, migrated)
    return migrated


def save_users_file(path: Path, users: Dict[str, Any]) -> None:
    migrated, _, _ = migrate_users(users)
    atomic_write_json(path, migrated)


def user_id_for_email(users: Dict[str, Any], email: Optional[str]) -> Optional[str]:
    if not email:
        return None
    if str(email).startswith("user:"):
        return str(email)
    normalized = normalize_email(email)
    user = (users or {}).get(normalized)
    if isinstance(user, dict):
        return user.get("id") or stable_user_id(normalized)
    return stable_user_id(normalized)


def email_for_user_id(users: Dict[str, Any], user_id: Optional[str]) -> Optional[str]:
    if not user_id:
        return None
    for email, user in (users or {}).items():
        if isinstance(user, dict) and user.get("id") == user_id:
            return email
    return None


def migrate_knowledge_graph_identity(db_path: Path, email_to_id: Dict[str, str]) -> int:
    """Rewrite KG owner/creator identity columns from email to stable UUIDs."""
    if not db_path.exists() or not email_to_id:
        return 0
    changed = 0
    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for email, user_id in email_to_id.items():
            normalized = normalize_email(email)
            if "nodes_v2" in tables:
                cur = conn.execute("UPDATE nodes_v2 SET owner_id=? WHERE LOWER(owner_id)=?", (user_id, normalized))
                changed += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
            if "edges_v2" in tables:
                cur = conn.execute("UPDATE edges_v2 SET created_by=? WHERE LOWER(created_by)=?", (user_id, normalized))
                changed += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
            if "ingestion_provenance" in tables:
                cur = conn.execute("UPDATE ingestion_provenance SET owner=? WHERE LOWER(owner)=?", (user_id, normalized))
                changed += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        if changed:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS kg_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT OR REPLACE INTO kg_meta(key, value) VALUES('identity_uuid_migrated_at', ?)",
                (_now(),),
            )
    return changed
