"""Runtime namespace filtering for legacy server_app compatibility.

The app factory still constructs a broad local namespace for historical
``server_app`` attribute access. This module keeps that surface deliberate:
public runtime objects and known legacy helpers stay visible, while internal
assembly scratch values do not leak into ``server_app.__getattr__``.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from types import ModuleType
from typing import Any, Dict, Mapping


@dataclass(frozen=True)
class RuntimeBundle:
    """Typed migration target for app-factory runtime dependencies."""

    app: Any
    CONFIG: Any
    KNOWLEDGE_GRAPH: Any
    INGESTION_PIPELINE: Any
    AGENT_RUNTIME: Any
    HOOKS_REGISTRY: Any
    REVIEW_QUEUE: Any
    AGENT_REGISTRY: Any
    model_router: Any
    build_runtime: Any
    get_shared_runtime: Any
    create_app: Any

    def as_legacy_dict(self) -> Dict[str, Any]:
        return {field.name: getattr(self, field.name) for field in fields(self)}


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
    "runtime_bundle",
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

LEGACY_PUBLIC_EXPORTS = {
    "ENGINE_MODEL_CATALOG",
    "TOOL_GOVERNANCE",
    "enforce_rate_limit",
    "filter_lower_family_versions",
    "hash_password",
    "normalize_local_model_request",
    "verify_password",
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
    runtime_bundle: RuntimeBundle | Mapping[str, Any],
) -> Dict[str, Any]:
    """Return the compatibility namespace without assembly scratch values."""
    legacy_bundle = (
        runtime_bundle.as_legacy_dict()
        if isinstance(runtime_bundle, RuntimeBundle)
        else dict(runtime_bundle)
    )
    exported: Dict[str, Any] = dict(legacy_bundle)
    allowed = set(legacy_bundle) | LEGACY_PUBLIC_EXPORTS | LEGACY_UNDERSCORE_EXPORTS
    for name in allowed:
        if name in exported:
            continue
        if name in INTERNAL_RUNTIME_NAMES:
            continue
        value = local_namespace.get(name)
        if value is None:
            continue
        if isinstance(value, ModuleType):
            continue
        if _is_internal_runtime_dict(name, value):
            continue
        exported[name] = value
    exported["RUNTIME_BUNDLE"] = runtime_bundle
    exported["_RUNTIME_BUNDLE"] = legacy_bundle
    return exported


__all__ = [
    "INTERNAL_RUNTIME_NAMES",
    "LEGACY_UNDERSCORE_EXPORTS",
    "RuntimeBundle",
    "build_runtime_namespace",
]
