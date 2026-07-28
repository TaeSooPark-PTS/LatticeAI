"""Auditable command plans for installer and local process execution."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

_SECRET_RE = re.compile(
    r"(api[_-]?key|access[_-]?token|auth[_-]?token|bearer|client[_-]?secret|password|secret|token)",
    re.IGNORECASE,
)
_SENSITIVE_FLAGS = {
    "--api-key",
    "--access-token",
    "--auth-token",
    "--client-secret",
    "--password",
    "--secret",
    "--token",
}


class CommandConfirmationError(ValueError):
    """Raised when a process command lacks the expected execution token."""


def _default_audit_file() -> Path:
    base = Path(os.environ.get("LATTICEAI_DATA_DIR") or Path.home() / ".ltcai")
    return base / "process_audit.jsonl"


def _normalize_command(command: Sequence[Any]) -> list[str]:
    return [str(part) for part in command]


def redact_command(command: Sequence[Any]) -> list[str]:
    redacted: list[str] = []
    redact_next = False
    for raw in _normalize_command(command):
        lower = raw.lower()
        if redact_next:
            redacted.append("[REDACTED]")
            redact_next = False
            continue
        if lower in _SENSITIVE_FLAGS:
            redacted.append(raw)
            redact_next = True
            continue
        if "=" in raw:
            key, _value = raw.split("=", 1)
            if _SECRET_RE.search(key):
                redacted.append(f"{key}=[REDACTED]")
                continue
        if _SECRET_RE.search(raw) and len(raw) > 32:
            redacted.append("[REDACTED]")
            continue
        redacted.append(raw)
    return redacted


def _digest_payload(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def command_hash(command: Sequence[Any], *, cwd: Optional[str] = None) -> str:
    return _digest_payload({"command": _normalize_command(command), "cwd": cwd or ""})


def confirmation_token(command: Sequence[Any], *, cwd: Optional[str] = None, purpose: str = "execute") -> str:
    digest = _digest_payload(
        {
            "purpose": purpose,
            "command": _normalize_command(command),
            "cwd": cwd or "",
        }
    )
    return digest[:16]


def command_plan(
    command: Sequence[Any],
    *,
    name: str,
    purpose: str = "installer",
    cwd: Optional[str] = None,
    requires_admin: bool = False,
    metadata: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "purpose": purpose,
        "command_preview": redact_command(command),
        "command_hash": command_hash(command, cwd=cwd),
        "confirmation_token": confirmation_token(command, cwd=cwd, purpose=purpose),
        "requires_admin": bool(requires_admin),
        "cwd": cwd,
        "metadata": dict(metadata or {}),
    }


def command_plan_for_commands(
    commands: Iterable[Sequence[Any]],
    *,
    name: str,
    purpose: str = "installer",
    cwd: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    normalized = [_normalize_command(command) for command in commands]
    digest = _digest_payload({"purpose": purpose, "commands": normalized, "cwd": cwd or ""})
    return {
        "name": name,
        "purpose": purpose,
        "command_count": len(normalized),
        "command_hash": digest,
        "confirmation_token": digest[:16],
        "cwd": cwd,
        "metadata": dict(metadata or {}),
    }


def verify_command_confirmation(
    command: Sequence[Any],
    provided_token: Optional[str],
    *,
    cwd: Optional[str] = None,
    purpose: str = "installer",
) -> bool:
    if not provided_token:
        return False
    return str(provided_token).strip() == confirmation_token(command, cwd=cwd, purpose=purpose)


def require_command_confirmation(
    command: Sequence[Any],
    provided_token: Optional[str],
    *,
    cwd: Optional[str] = None,
    purpose: str = "installer",
) -> None:
    if not verify_command_confirmation(command, provided_token, cwd=cwd, purpose=purpose):
        raise CommandConfirmationError("installer command requires a matching confirmation token")


def _preview_text(text: Optional[str], *, limit: int = 500) -> Optional[str]:
    if text is None:
        return None
    value = str(text)[-limit:]
    value = re.sub(r"(?i)(api[_-]?key|access[_-]?token|auth[_-]?token|password|secret|token)=\S+", r"\1=[REDACTED]", value)
    return value


def _text_hash(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    return hashlib.sha256(str(text).encode("utf-8", errors="replace")).hexdigest()


def append_process_audit_event(
    event_type: str,
    *,
    plan: Mapping[str, Any],
    status: str,
    user_email: Optional[str] = None,
    returncode: Optional[int] = None,
    stdout: Optional[str] = None,
    stderr: Optional[str] = None,
    error: Optional[str] = None,
    audit_file: Optional[Path] = None,
    extra: Optional[Mapping[str, Any]] = None,
) -> None:
    path = audit_file or _default_audit_file()
    entry = {
        "ts": time.time(),
        "event_type": event_type,
        "status": status,
        "user_email": user_email,
        "plan": {
            "name": plan.get("name"),
            "purpose": plan.get("purpose"),
            "command_preview": plan.get("command_preview"),
            "command_hash": plan.get("command_hash"),
            "requires_admin": plan.get("requires_admin"),
            "cwd": plan.get("cwd"),
            "metadata": plan.get("metadata") or {},
        },
        "returncode": returncode,
        "stdout_hash": _text_hash(stdout),
        "stderr_hash": _text_hash(stderr),
        "stdout_preview": _preview_text(stdout),
        "stderr_preview": _preview_text(stderr),
        "error": _preview_text(error),
        "extra": dict(extra or {}),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        # Auditing must never make setup recovery paths unusable.
        return
