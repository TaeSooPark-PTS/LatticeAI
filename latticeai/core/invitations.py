"""Local-first invitation tokens for workspace membership."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from .timeutil import local_now as _now

def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _atomic_write(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


class InvitationStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "invitations": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("invitations", [])
                return data
        except Exception:
            pass
        return {"version": 1, "invitations": []}

    def _save(self, data: Dict[str, Any]) -> None:
        data["version"] = 1
        _atomic_write(self.path, data)

    def create(
        self,
        *,
        email: Optional[str],
        workspace_id: Optional[str],
        role: str,
        created_by: Optional[str],
        expires_hours: int = 168,
    ) -> Dict[str, Any]:
        token = secrets.token_urlsafe(32)
        now = _now()
        record = {
            "id": f"invite-{secrets.token_hex(8)}",
            "token_hash": _hash_token(token),
            "email": (email or "").strip().lower() or None,
            "workspace_id": workspace_id,
            "role": role,
            "created_by": created_by,
            "created_at": _iso(now),
            "expires_at": _iso(now + timedelta(hours=max(1, min(int(expires_hours or 168), 24 * 30)))),
            "status": "pending",
            "accepted_by": None,
            "accepted_at": None,
        }
        data = self._load()
        data.setdefault("invitations", []).append(record)
        self._save(data)
        public = self.public(record)
        public["token"] = token
        return public

    def list(self) -> List[Dict[str, Any]]:
        data = self._load()
        changed = False
        records = []
        for record in data.get("invitations") or []:
            if self._expire_if_needed(record):
                changed = True
            records.append(self.public(record))
        if changed:
            self._save(data)
        return records

    def accept(self, token: str, *, accepted_by: str, email: Optional[str]) -> Dict[str, Any]:
        data = self._load()
        token_hash = _hash_token(token)
        record = next((item for item in data.get("invitations") or [] if item.get("token_hash") == token_hash), None)
        if record is None:
            raise FileNotFoundError("invitation not found")
        if self._expire_if_needed(record):
            self._save(data)
            raise PermissionError("invitation expired")
        if record.get("status") != "pending":
            raise PermissionError(f"invitation is {record.get('status')}")
        invited_email = (record.get("email") or "").lower()
        if invited_email and (not email or invited_email != email.lower()):
            raise PermissionError("invitation was issued for a different email")
        record["status"] = "accepted"
        record["accepted_by"] = accepted_by
        record["accepted_at"] = _iso(_now())
        self._save(data)
        return self.public(record)

    @staticmethod
    def _expire_if_needed(record: Dict[str, Any]) -> bool:
        if record.get("status") != "pending":
            return False
        try:
            expires_at = datetime.fromisoformat(str(record.get("expires_at")))
        except Exception:
            expires_at = _now() - timedelta(seconds=1)
        if expires_at >= _now():
            return False
        record["status"] = "expired"
        record["expired_at"] = _iso(_now())
        return True

    @staticmethod
    def public(record: Dict[str, Any]) -> Dict[str, Any]:
        return {k: v for k, v in record.items() if k != "token_hash"}
