"""Persisted network-boundary preference for hybrid local + cloud turns.

Mirrors PermissionModeService:

* process default (env ``LATTICEAI_NETWORK_MODE`` or local_only)
* per-user override
* per-workspace override (wins over user)

Switching to ``cloud_allowed`` requires ``acknowledge_risk=True`` once; the
ack is audited. Hard node filters remain in ``latticeai.core.network_boundary``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Optional

from latticeai.core.network_boundary import (
    DEFAULT_NETWORK_MODE,
    NetworkBoundaryMode,
    network_mode_catalog,
    network_mode_contract,
    normalize_network_mode,
)
from latticeai.services.mode_store import JsonBackedModeService


class NetworkBoundaryService(JsonBackedModeService[NetworkBoundaryMode]):
    """Load / save the network boundary dial."""

    FILENAME = "network_boundary.json"

    def __init__(
        self,
        *,
        data_dir: Path,
        default_mode: NetworkBoundaryMode | str = DEFAULT_NETWORK_MODE,
        audit: Optional[Callable[..., None]] = None,
    ) -> None:
        super().__init__(data_dir=data_dir, audit=audit)
        self._default = normalize_network_mode(default_mode)

    def _default_entry(self) -> Any:
        return self._default.value

    def _resolve_from(
        self,
        data: Dict[str, Any],
        *,
        user_email: Optional[str],
        workspace_id: Optional[str],
    ) -> NetworkBoundaryMode:
        if workspace_id:
            ws = (data.get("workspaces") or {}).get(str(workspace_id))
            if ws:
                return normalize_network_mode(ws)
        if user_email:
            user = (data.get("users") or {}).get(str(user_email).lower())
            if user:
                return normalize_network_mode(user)
        return normalize_network_mode(data.get("default") or self._default)

    def get(
        self,
        *,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        mode = self.resolve(user_email=user_email, workspace_id=workspace_id)
        contract = network_mode_contract(mode)
        contract["catalog"] = network_mode_catalog()
        contract["scope"] = {
            "user_email": user_email,
            "workspace_id": workspace_id,
        }
        return contract

    def set_mode(
        self,
        mode: NetworkBoundaryMode | str,
        *,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
        acknowledge_risk: bool = False,
        source: str = "api",
    ) -> Dict[str, Any]:
        mode = normalize_network_mode(mode)
        if mode == NetworkBoundaryMode.CLOUD_ALLOWED and not acknowledge_risk:
            raise PermissionError(
                "cloud_allowed mode requires acknowledge_risk=true "
                "(minimal related Knowledge Graph nodes may leave this machine)"
            )
        with self._lock:
            data = self._read()
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
            "network_boundary_changed",
            user_email=user_email,
            workspace_id=workspace_id,
            previous=previous.value,
            mode=mode.value,
            source=source,
            acknowledge_risk=bool(acknowledge_risk),
        )
        return self.get(user_email=user_email, workspace_id=workspace_id)


__all__ = ["NetworkBoundaryService"]
