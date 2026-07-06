"""Local permission request and approval routes."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import threading
import time
import urllib.request
from pathlib import Path
from typing import Dict, Optional, Tuple

from fastapi import APIRouter, HTTPException, Request


_PERMISSION_ACTION_LABELS = {
    "list": "폴더 목록 보기",
    "read": "파일 읽기",
    "write": "파일 쓰기",
}


class PermissionGateway:
    """Shared permission state used by local-file and knowledge routers."""

    def __init__(self, *, config, data_dir: Path, require_admin, get_current_user) -> None:
        self.require_admin = require_admin
        self.get_current_user = get_current_user
        self.local_approval_ttl_seconds = 5 * 60
        self.local_approval_lock = threading.Lock()
        self.perm_queue_lock = threading.Lock()
        self.local_approvals: Dict[str, Dict[str, object]] = {}
        self.discord_permission_webhook_url = config.discord_permission_webhook
        self.discord_bot_token = config.discord_bot_token
        self.discord_permission_channel = config.discord_permission_channel
        self.permission_monitor_secret = config.permission_monitor_secret
        self.perm_queue_file = data_dir / "permission_queue.json"

    @staticmethod
    def token_hash(token: str) -> str:
        return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()

    @staticmethod
    def token_hint(token: str) -> str:
        value = str(token or "")
        return value[:8] if value else ""

    def _read_queue(self) -> Dict:
        if not self.perm_queue_file.exists():
            return {}
        try:
            return json.loads(self.perm_queue_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write_queue(self, queue: Dict) -> None:
        self.perm_queue_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.perm_queue_file.with_suffix(self.perm_queue_file.suffix + ".tmp")
        tmp.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.perm_queue_file)

    def _perm_queue_write(self, token: str, record: Dict[str, object]) -> None:
        try:
            key = self.token_hash(token)
            with self.perm_queue_lock:
                queue = self._read_queue()
                queue[key] = {**record, "token_hint": self.token_hint(token), "notified": False}
                self._write_queue(queue)
        except Exception as exc:
            logging.warning("perm_queue_write failed: %s", exc)

    def _perm_queue_remove(self, token: str) -> None:
        try:
            key = self.token_hash(token)
            with self.perm_queue_lock:
                queue = self._read_queue()
                queue.pop(key, None)
                self._write_queue(queue)
        except Exception as exc:
            logging.warning("perm_queue_remove failed: %s", exc)

    @staticmethod
    def normalize_local_path_for_approval(path: str) -> str:
        return str(Path(path).expanduser().resolve())

    @staticmethod
    def content_fingerprint(content: str = "") -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _notify_discord_permission_sync(self, token: str, path: str, action: str, user_email: str) -> None:
        sent = False
        if self.discord_bot_token and self.discord_permission_channel:
            action_label = _PERMISSION_ACTION_LABELS.get(action, action)
            expires_at_iso = time.strftime(
                "%Y-%m-%d %H:%M:%S UTC",
                time.gmtime(time.time() + self.local_approval_ttl_seconds),
            )
            msg = (
                f"🔐 **파일 접근 권한 요청**\n"
                f"**경로:** `{path}`\n"
                f"**작업:** {action_label}\n"
                f"**요청자:** {user_email}\n"
                f"**토큰:** `{token}`\n"
                f"**만료:** {expires_at_iso}\n\n"
                f"승인하려면 `승인 {token[:8]}` / 거부하려면 `거부 {token[:8]}` 라고 답장하세요."
            )
            payload = json.dumps({"content": msg}, ensure_ascii=False).encode("utf-8")
            try:
                req = urllib.request.Request(
                    f"https://discord.com/api/v10/channels/{self.discord_permission_channel}/messages",
                    data=payload,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bot {self.discord_bot_token}",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=5):
                    pass
                sent = True
            except Exception as exc:
                logging.warning("Discord bot permission notify failed: %s", exc)

        if not sent and self.discord_permission_webhook_url:
            action_label = _PERMISSION_ACTION_LABELS.get(action, action)
            expires_at_iso = time.strftime(
                "%Y-%m-%d %H:%M:%S UTC",
                time.gmtime(time.time() + self.local_approval_ttl_seconds),
            )
            payload = json.dumps(
                {
                    "embeds": [
                        {
                            "title": "🔐 파일 접근 권한 요청",
                            "color": 0xFF9900,
                            "fields": [
                                {"name": "경로", "value": f"`{path}`", "inline": False},
                                {"name": "작업", "value": action_label, "inline": True},
                                {"name": "요청자", "value": user_email, "inline": True},
                                {"name": "토큰", "value": f"`{token}`", "inline": False},
                                {"name": "만료", "value": expires_at_iso, "inline": True},
                            ],
                            "footer": {
                                "text": (
                                    "승인: POST /permissions/approve/{token}  |  "
                                    "거부: POST /permissions/deny/{token}  |  "
                                    "목록: GET /permissions/pending"
                                )
                            },
                        }
                    ]
                },
                ensure_ascii=False,
            ).encode("utf-8")
            try:
                req = urllib.request.Request(
                    self.discord_permission_webhook_url,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=5):
                    pass
            except Exception as exc:
                logging.warning("Discord permission webhook failed: %s", exc)

    def local_permission_response(self, path: str, action: str, user_email: str, content: str = "") -> dict:
        normalized = self.normalize_local_path_for_approval(path)
        token = secrets.token_urlsafe(24)
        record: Dict[str, object] = {
            "path": normalized,
            "action": action,
            "user_email": user_email,
            "expires_at": time.time() + self.local_approval_ttl_seconds,
            "approved": False,
        }
        if action == "write":
            self.ensure_path_allowed(path, action="write")
            record["content_hash"] = self.content_fingerprint(content)
        with self.local_approval_lock:
            self.local_approvals[self.token_hash(token)] = {**record, "token_hint": self.token_hint(token)}
        self._perm_queue_write(token, record)
        action_label = _PERMISSION_ACTION_LABELS.get(action, action)
        return {
            "permission_required": True,
            "path": path,
            "action": action,
            "action_label": action_label,
            "approval_token": token,
            "expires_in": self.local_approval_ttl_seconds,
            "message": f"AI가 '{path}' 에 대한 {action_label} 권한을 요청합니다.",
            "check_status_url": f"/permissions/status/{token}",
        }

    def require_local_user(self, request: Request) -> str:
        email = self.get_current_user(request)
        if not email:
            raise HTTPException(status_code=401, detail="로컬 파일 접근은 로그인 세션이 필요합니다.")
        return email

    def require_local_approval(
        self,
        *,
        token: Optional[str],
        path: str,
        action: str,
        user_email: str,
        content: str = "",
    ) -> None:
        if not token:
            raise HTTPException(status_code=403, detail="파일 접근 승인 토큰이 필요합니다.")
        normalized = self.normalize_local_path_for_approval(path)
        self.ensure_path_allowed(normalized, action=action)
        now = time.time()
        key = self.token_hash(token)
        with self.local_approval_lock:
            expired = [key for key, value in self.local_approvals.items() if float(value.get("expires_at", 0)) < now]
            for key in expired:
                self.local_approvals.pop(key, None)
            record = self.local_approvals.get(key)
        if not record:
            raise HTTPException(status_code=403, detail="파일 접근 승인이 만료되었거나 유효하지 않습니다.")
        if not record.get("approved"):
            raise HTTPException(status_code=403, detail="파일 접근이 아직 승인되지 않았습니다. Discord 또는 UI에서 승인해주세요.")
        if record.get("user_email") != user_email:
            raise HTTPException(status_code=403, detail="다른 사용자의 파일 접근 승인은 사용할 수 없습니다.")
        if record.get("path") != normalized or record.get("action") != action:
            raise HTTPException(status_code=403, detail="파일 접근 승인 범위가 일치하지 않습니다.")
        if action == "write" and record.get("content_hash") != self.content_fingerprint(content):
            raise HTTPException(status_code=403, detail="승인된 파일 내용과 요청 내용이 다릅니다.")

    def check_permission_auth(self, request: Request, token: Optional[str] = None) -> None:
        if self.permission_monitor_secret:
            auth_header = request.headers.get("Authorization", "")
            if auth_header == f"Bearer {self.permission_monitor_secret}":
                return
        if token:
            key = self.token_hash(token)
            current_user = self.get_current_user(request)
            with self.local_approval_lock:
                record = self.local_approvals.get(key)
            if current_user and record and record.get("user_email") == current_user:
                return
        self.require_admin(request)

    def ensure_path_allowed(self, path: str, *, action: str) -> None:
        if action != "write":
            return
        normalized = self.normalize_local_path_for_approval(path)
        from latticeai.services.tool_dispatch import LOCAL_WRITE_BLOCKED_PREFIXES

        for prefix in LOCAL_WRITE_BLOCKED_PREFIXES:
            normalized_prefix = str(Path(prefix).expanduser()).rstrip("/")
            if normalized == normalized_prefix or normalized.startswith(normalized_prefix + "/"):
                raise HTTPException(status_code=403, detail=f"쓰기 금지 경로입니다: {prefix}")


def create_permissions_router(
    *,
    config,
    data_dir: Path,
    require_user,
    require_admin,
    get_current_user,
) -> Tuple[APIRouter, PermissionGateway]:
    router = APIRouter()
    gateway = PermissionGateway(
        config=config,
        data_dir=data_dir,
        require_admin=require_admin,
        get_current_user=get_current_user,
    )

    @router.get("/permissions/pending")
    async def permissions_pending(request: Request):
        require_admin(request)
        now = time.time()
        with gateway.local_approval_lock:
            result = {}
            for token_hash, rec in list(gateway.local_approvals.items()):
                expires_at = float(rec.get("expires_at", 0))
                if expires_at < now:
                    continue
                result[str(rec.get("token_hint") or token_hash[:8])] = {
                    "path": rec.get("path"),
                    "action": rec.get("action"),
                    "action_label": _PERMISSION_ACTION_LABELS.get(str(rec.get("action", "")), str(rec.get("action", ""))),
                    "user_email": rec.get("user_email"),
                    "approved": bool(rec.get("approved")),
                    "expires_in": round(expires_at - now),
                }
        return {"pending": result, "count": len(result)}

    @router.post("/permissions/approve/{token}")
    async def permissions_approve(token: str, request: Request):
        gateway.check_permission_auth(request, token)
        key = gateway.token_hash(token)
        with gateway.local_approval_lock:
            record = gateway.local_approvals.get(key)
            if not record:
                raise HTTPException(status_code=404, detail="토큰이 없거나 만료되었습니다.")
            if float(record.get("expires_at", 0)) < time.time():
                gateway.local_approvals.pop(key, None)
                raise HTTPException(status_code=410, detail="토큰이 만료되었습니다.")
            record["approved"] = True
        gateway._perm_queue_remove(token)
        logging.info(
            "Permission approved: token=%s path=%s action=%s user=%s",
            gateway.token_hint(token),
            record.get("path"),
            record.get("action"),
            record.get("user_email"),
        )
        return {
            "ok": True,
            "token_hint": gateway.token_hint(token),
            "path": record.get("path"),
            "action": record.get("action"),
            "user_email": record.get("user_email"),
        }

    @router.post("/permissions/deny/{token}")
    async def permissions_deny(token: str, request: Request):
        gateway.check_permission_auth(request, token)
        key = gateway.token_hash(token)
        with gateway.local_approval_lock:
            record = gateway.local_approvals.pop(key, None)
        gateway._perm_queue_remove(token)
        if not record:
            raise HTTPException(status_code=404, detail="토큰이 없거나 이미 처리되었습니다.")
        logging.info(
            "Permission denied: token=%s path=%s action=%s user=%s",
            gateway.token_hint(token),
            record.get("path"),
            record.get("action"),
            record.get("user_email"),
        )
        return {
            "ok": True,
            "denied": True,
            "token_hint": gateway.token_hint(token),
            "path": record.get("path"),
            "action": record.get("action"),
        }

    @router.get("/permissions/status/{token}")
    async def permissions_status(token: str, request: Request):
        require_user(request)
        now = time.time()
        key = gateway.token_hash(token)
        with gateway.local_approval_lock:
            record = gateway.local_approvals.get(key)
        if not record:
            return {"status": "denied_or_expired", "token_hint": gateway.token_hint(token)}
        if float(record.get("expires_at", 0)) < now:
            return {"status": "expired", "token_hint": gateway.token_hint(token)}
        if record.get("approved"):
            return {"status": "approved", "token_hint": gateway.token_hint(token)}
        return {
            "status": "pending",
            "token_hint": gateway.token_hint(token),
            "expires_in": round(float(record.get("expires_at", 0)) - now),
        }

    return router, gateway


__all__ = ["PermissionGateway", "create_permissions_router"]
