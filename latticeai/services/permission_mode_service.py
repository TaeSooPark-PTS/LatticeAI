"""Persisted permission-mode preference (v9.9.8).

Stores the active mode under the data dir so every surface (web, VS Code,
Telegram, agent loop) shares one dial. Scope:

* global default (env ``LATTICEAI_PERMISSION_MODE`` or strict)
* per-user override
* per-workspace override (wins over user)

Changing to ``bypass`` requires ``acknowledge_risk=True`` once; the ack is
audited. Circuit breakers are enforced by ``latticeai.core.permission_mode``,
not by this store.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from latticeai.core.io_utils import atomic_write_json
from latticeai.core.permission_mode import (
    DEFAULT_MODE,
    PermissionMode,
    mode_catalog,
    mode_contract,
    normalize_mode,
)


class PermissionModeService:
    """Load / save the autonomy dial."""

    def __init__(
        self,
        *,
        data_dir: Path,
        default_mode: PermissionMode | str = DEFAULT_MODE,
        audit: Optional[Callable[..., None]] = None,
    ) -> None:
        self._path = Path(data_dir) / "permission_mode.json"
        self._default = normalize_mode(default_mode)
        self._audit = audit or (lambda *a, **kw: None)
        self._lock = threading.Lock()

    def _read(self) -> Dict[str, Any]:
        if not self._path.exists():
            return {"default": self._default.value, "users": {}, "workspaces": {}}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return {"default": self._default.value, "users": {}, "workspaces": {}}
        if not isinstance(data, dict):
            return {"default": self._default.value, "users": {}, "workspaces": {}}
        data.setdefault("default", self._default.value)
        data.setdefault("users", {})
        data.setdefault("workspaces", {})
        return data

    def _write(self, data: Dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self._path, data)

    def resolve(
        self,
        *,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> PermissionMode:
        with self._lock:
            data = self._read()
        if workspace_id:
            ws = (data.get("workspaces") or {}).get(str(workspace_id))
            if ws:
                return normalize_mode(ws)
        if user_email:
            user = (data.get("users") or {}).get(str(user_email).lower())
            if user:
                return normalize_mode(user)
        return normalize_mode(data.get("default") or self._default)

    def get(
        self,
        *,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        mode = self.resolve(user_email=user_email, workspace_id=workspace_id)
        contract = mode_contract(mode)
        contract["catalog"] = mode_catalog()
        contract["scope"] = {
            "user_email": user_email,
            "workspace_id": workspace_id,
        }
        return contract

    def set_mode(
        self,
        mode: PermissionMode | str,
        *,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
        acknowledge_risk: bool = False,
        source: str = "api",
    ) -> Dict[str, Any]:
        mode = normalize_mode(mode)
        if mode == PermissionMode.BYPASS and not acknowledge_risk:
            raise PermissionError(
                "bypass mode requires acknowledge_risk=true "
                "(YOLO inside the agent workspace; circuit breakers still apply)"
            )
        with self._lock:
            data = self._read()
            previous = self.resolve(user_email=user_email, workspace_id=workspace_id)
            if workspace_id:
                data.setdefault("workspaces", {})[str(workspace_id)] = mode.value
            elif user_email:
                data.setdefault("users", {})[str(user_email).lower()] = mode.value
            else:
                data["default"] = mode.value
            self._write(data)
        self._audit(
            "permission_mode_changed",
            user_email=user_email,
            workspace_id=workspace_id,
            previous=previous.value,
            mode=mode.value,
            source=source,
            acknowledge_risk=bool(acknowledge_risk),
        )
        return self.get(user_email=user_email, workspace_id=workspace_id)


__all__ = ["PermissionModeService"]
