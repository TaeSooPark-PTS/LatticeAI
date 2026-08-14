"""Tool registry, governance, and the authorization half of tool dispatch.

``ToolRegistry`` owns the catalog and governance facts; this service owns the
request-time authorization callbacks that must be configured during app
construction.

v11.6.0 shrank it to the three questions ``POST /agent/tool`` asks before it
runs anything — what is this call's policy, may this role make it, and does the
target already exist — plus the ``configure`` seam that binds the user table.
The rest of the module was the Python agent loop's: ``build_agent_runtime``
(the edge that kept ``latticeai.core.agent`` alive), the artifact collectors,
the snapshot/rollback file surface behind the Review Center, and
``enforce_policy``'s approval flow. Orchestration lives in ``lattice-agent``
now, and the mode gating is the kernel's decision, made with the run's approval
state — which HTTP does not have.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

from fastapi import HTTPException

from latticeai.core.policy import role_has_capability
from latticeai.core.tool_registry import ToolPermission, ToolPolicy
from latticeai.tools import AGENT_ROOT, DEFAULT_TOOL_REGISTRY, document_output_target


def _default_load_users() -> Dict[str, Any]:
    return {}


def _default_get_user_role(_email, _users=None) -> str:
    return "user"


TOOL_GOVERNANCE: Dict[str, ToolPolicy] = dict(DEFAULT_TOOL_REGISTRY.governance)
TOOL_GOVERNANCE_DEFAULT: ToolPolicy = DEFAULT_TOOL_REGISTRY.default_policy
RISK_LEVEL_MAP = DEFAULT_TOOL_REGISTRY.risk_level_map


@dataclass
class ToolDispatchService:
    """Runtime-facing tool policy and authorization boundary.

    Keeping the callbacks on an instance rather than in module globals is what
    lets a second application — or a test — bind its own user table without
    reaching into this one's.
    """

    registry: Any = field(default_factory=lambda: DEFAULT_TOOL_REGISTRY)
    load_users: Callable[[], Dict[str, Any]] = field(default=_default_load_users)
    get_user_role: Callable[..., str] = field(default=_default_get_user_role)

    @property
    def tool_governance(self) -> Mapping[str, ToolPolicy]:
        return self.registry.governance

    @property
    def risk_level_map(self) -> Mapping[str, str]:
        return self.registry.risk_level_map

    def configure(
        self,
        *,
        load_users: Callable[[], Dict[str, Any]],
        get_user_role: Callable[..., str],
    ) -> None:
        self.load_users = load_users
        self.get_user_role = get_user_role

    def policy_for(self, action_name: str, args: dict) -> ToolPolicy:
        return self.registry.policy_for(action_name, args)

    def risk_level(self, action_name: str, args: dict) -> str:
        return self.registry.risk_level(action_name, args)

    def permission(self, name: str, args: Optional[dict] = None) -> ToolPermission:
        return self.registry.permission(name, args or {})

    def permissions(self) -> list[ToolPermission]:
        return self.registry.permissions()

    def diagnostics(self) -> Dict[str, Any]:
        return self.registry.diagnostics()

    def manifest(self) -> Dict[str, Any]:
        return self.registry.manifest()

    def check_role(self, tool_name: str, current_user: str) -> None:
        admin_only = tool_name in self.registry.admin_only_tools
        capability = self.policy_for(tool_name, {}).get("capability")
        if not admin_only and not capability:
            return
        users = self.load_users()
        role = str(self.get_user_role(current_user, users) or "user")
        if admin_only and role not in {"admin", "owner"}:
            raise HTTPException(
                status_code=403,
                detail=f"'{tool_name}' 툴은 관리자 전용입니다.",
            )
        if capability and not role_has_capability(role, capability):
            raise HTTPException(
                status_code=403,
                detail=f"'{tool_name}' 툴에는 '{capability}' capability가 필요합니다.",
            )

    def _governed_path_exists(self, tool_name: str, path: str) -> bool:
        """Best-effort existence check for governance classification.

        The document creators rewrite ``filename`` into their own output
        directory, so the raw argument is resolved through
        :func:`document_output_target` first — checking the argument verbatim
        inspects a path nothing ever writes. Workspace-relative paths resolve
        under ``AGENT_ROOT``; absolute paths (home-sandbox writes) are honored
        as-is. Never raises — an unresolvable path is treated as non-existent
        (additive), which the fail-closed guard only escalates when the target
        actually exists.
        """
        try:
            candidate = Path(document_output_target(tool_name, path) or path)
            if not candidate.is_absolute():
                candidate = Path(AGENT_ROOT) / candidate
            return candidate.exists()
        except Exception:
            return False

    def user_role(self, current_user: str) -> str:
        users = self.load_users()
        try:
            return str(self.get_user_role(current_user, users) or "user")
        except Exception:
            return "user"


DEFAULT_TOOL_DISPATCH_SERVICE = ToolDispatchService()


def configure_tool_dispatch(
    *,
    load_users: Callable[[], Dict[str, Any]],
    get_user_role: Callable[..., str],
) -> None:
    DEFAULT_TOOL_DISPATCH_SERVICE.configure(
        load_users=load_users, get_user_role=get_user_role
    )


def get_tool_permission(name: str, args: Optional[dict] = None) -> ToolPermission:
    return DEFAULT_TOOL_DISPATCH_SERVICE.permission(name, args or {})


def check_tool_role(tool_name: str, current_user: str) -> None:
    DEFAULT_TOOL_DISPATCH_SERVICE.check_role(tool_name, current_user)


__all__ = [
    "DEFAULT_TOOL_DISPATCH_SERVICE",
    "RISK_LEVEL_MAP",
    "TOOL_GOVERNANCE",
    "TOOL_GOVERNANCE_DEFAULT",
    "ToolDispatchService",
    "check_tool_role",
    "configure_tool_dispatch",
    "get_tool_permission",
]
