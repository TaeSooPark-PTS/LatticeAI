"""Runtime namespace filtering for legacy server_app compatibility.

The app factory still constructs a broad local namespace for historical
``server_app`` attribute access. This module keeps that surface deliberate:
public runtime objects and known legacy helpers stay visible, while internal
assembly scratch values do not leak into ``server_app.__getattr__``.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any, Dict, Mapping


INTERNAL_RUNTIME_NAMES = {
    "config",
    "logging",
    "os",
    "threading",
    "Path",
    "mx",
    "uvicorn",
    "HTTPException",
    "Request",
    "BaseModel",
    "keyring",
    "datetime",
}

LEGACY_UNDERSCORE_EXPORTS = {
    "_LOCAL_WRITE_BLOCKED_PREFIXES",
    "_RATE_LIMIT_ENABLED",
    "_SESSION_TTL",
    "_TOOL_CATALOG_BRIEF",
    "_TOOL_GOVERNANCE_DEFAULT",
    "_agent_risk",
    "_allowed_workspaces_for",
    "_build_admin_audit_report",
    "_build_sensitivity_report",
    "_bytes_match_extension",
    "_check_ip_rate_limit",
    "_check_rate_limit",
    "_check_tool_role",
    "_classify_sensitive_message",
    "_client_ip",
    "_create_security_router",
    "_embedding_info",
    "_fetch_skills_marketplace",
    "_get_audit_log",
    "_get_sso_discovery",
    "_graph_stats_safe",
    "_host_is_loopback",
    "_list_compat_profiles",
    "_llm_generate_sync",
    "_product_hardening_status",
    "_recent_chat_context",
    "_redact_secret_text",
    "_require_graph",
    "_scoped_hybrid_search",
    "_security_audit_events_safe",
    "_security_list_uploaded_files",
    "_spawn",
    "_tool_response",
    "_user_id_for_email",
    "_workspace_graph",
    "_workspace_models_payload",
    "_workspace_scope_from_request",
    "_workspace_settings_payload",
}


def _is_internal_runtime_dict(name: str, value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if name in {"_RUNTIME_BUNDLE"}:
        return False
    return (
        name.endswith("_runtime")
        or name.endswith("_router_bundle")
        or name.endswith("_rt")
        or name
        in {
            "_mcp_state",
            "_garden_import",
            "_foundation_router_bundle",
            "_static_routes_bundle",
            "_vpc_runtime",
            "_sso_runtime",
            "_security_runtime",
            "_session_runtime",
            "_config_runtime",
            "_context_runtime",
            "_brain_runtime",
            "_hooks_runtime",
            "_history_query_runtime",
            "_persistence_runtime",
            "_platform_automation_runtime",
            "_user_key_runtime",
            "_web_runtime",
        }
    )


def build_runtime_namespace(
    local_namespace: Mapping[str, Any],
    *,
    runtime_bundle: Mapping[str, Any],
) -> Dict[str, Any]:
    """Return the compatibility namespace without assembly scratch values."""
    exported: Dict[str, Any] = {}
    for name, value in local_namespace.items():
        if name in INTERNAL_RUNTIME_NAMES:
            continue
        if isinstance(value, ModuleType):
            continue
        if _is_internal_runtime_dict(name, value):
            continue
        if name.startswith("_") and name not in LEGACY_UNDERSCORE_EXPORTS:
            continue
        exported[name] = value
    exported["_RUNTIME_BUNDLE"] = dict(runtime_bundle)
    return exported


__all__ = [
    "INTERNAL_RUNTIME_NAMES",
    "LEGACY_UNDERSCORE_EXPORTS",
    "build_runtime_namespace",
]
