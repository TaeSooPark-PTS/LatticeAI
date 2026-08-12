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

from pathlib import Path
from typing import Any, Callable, Dict, Optional

from latticeai.core.permission_mode import (
    DEFAULT_MODE,
    PermissionMode,
    mode_catalog,
    mode_contract,
    normalize_mode,
)
from latticeai.services.mode_store import JsonBackedModeService


class PermissionModeService(JsonBackedModeService[PermissionMode]):
    """Load / save the autonomy dial."""

    FILENAME = "permission_mode.json"

    def __init__(
        self,
        *,
        data_dir: Path,
        default_mode: PermissionMode | str = DEFAULT_MODE,
        audit: Optional[Callable[..., None]] = None,
    ) -> None:
        super().__init__(data_dir=data_dir, audit=audit)
        self._default = normalize_mode(default_mode)

    def _default_entry(self) -> Any:
        return self._default.value

    def _resolve_from(
        self,
        data: Dict[str, Any],
        *,
        user_email: Optional[str],
        workspace_id: Optional[str],
    ) -> PermissionMode:
        """Scope precedence: workspace → user → process default."""
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
            # Lock-free helper on purpose: calling ``resolve`` here would
            # re-enter ``_lock`` and deadlock every mode change.
            previous = self._resolve_from(
                data, user_email=user_email, workspace_id=workspace_id,
            )
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
