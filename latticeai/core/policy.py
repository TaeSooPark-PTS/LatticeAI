"""Enforced community RBAC policy for Lattice AI."""

from __future__ import annotations

from typing import Dict, Iterable, List, Set

ROLE_CAPABILITIES: Dict[str, Set[str]] = {
    "owner": {"all"},
    "admin": {
        "admin:users",
        "admin:roles",
        "admin:policies",
        "admin:audit",
        "admin:security",
        "workspace:read",
        "workspace:write",
        "workspace:manage",
        "workspace:members",
        "chat",
        "search",
        "files",
        "pipeline",
        "desktop:control",
    },
    "member": {"workspace:read", "workspace:write", "chat", "search", "files", "pipeline"},
    "user": {"workspace:read", "workspace:write", "chat", "search", "files", "pipeline"},
    "viewer": {"workspace:read", "chat", "search"},
}


def normalize_role(role: str) -> str:
    role = str(role or "user").lower()
    return role if role in ROLE_CAPABILITIES else "user"


def capabilities_for_role(role: str) -> List[str]:
    caps = ROLE_CAPABILITIES.get(normalize_role(role), ROLE_CAPABILITIES["user"])
    return sorted(caps)


def role_has_capability(role: str, capability: str) -> bool:
    caps = ROLE_CAPABILITIES.get(normalize_role(role), ROLE_CAPABILITIES["user"])
    return "all" in caps or capability in caps


def require_capability(role: str, capability: str) -> None:
    if not role_has_capability(role, capability):
        raise PermissionError(f"role '{normalize_role(role)}' lacks capability '{capability}'")


def policy_matrix(roles: Iterable[str] | None = None) -> List[Dict[str, object]]:
    selected = list(roles or ROLE_CAPABILITIES.keys())
    return [{"role": normalize_role(role), "caps": capabilities_for_role(role)} for role in selected]
