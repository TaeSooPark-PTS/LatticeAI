"""Explicit runtime exports for the legacy :mod:`server_app` facade.

The composition root must never export ``locals()``. Every compatibility name
is selected here and every typed assembly stage remains available through the
``RuntimeBundle`` without leaking construction scratch state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping


@dataclass(frozen=True)
class RuntimeBundle:
    """Typed application assembly result and its five explicit stages."""

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
    config_runtime: Any
    security_runtime: Any
    brain_runtime: Any
    model_runtime: Any
    router_bundle: Any

    def as_legacy_dict(self) -> Dict[str, Any]:
        return {
            name: getattr(self, name)
            for name in RUNTIME_BUNDLE_EXPORTS
        }

    @property
    def stages(self) -> Dict[str, Any]:
        return {
            "config": self.config_runtime,
            "security": self.security_runtime,
            "brain": self.brain_runtime,
            "models": self.model_runtime,
            "routers": self.router_bundle,
        }


RUNTIME_BUNDLE_EXPORTS = frozenset(
    {
        "app",
        "CONFIG",
        "KNOWLEDGE_GRAPH",
        "INGESTION_PIPELINE",
        "AGENT_RUNTIME",
        "HOOKS_REGISTRY",
        "REVIEW_QUEUE",
        "AGENT_REGISTRY",
        "model_router",
        "build_runtime",
        "get_shared_runtime",
        "create_app",
    }
)

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


def build_runtime_namespace(
    *, runtime_bundle: RuntimeBundle | Mapping[str, Any], legacy_exports: Mapping[str, Any]
) -> Dict[str, Any]:
    """Return only explicit compatibility exports and typed bundle handles."""
    legacy_bundle = (
        runtime_bundle.as_legacy_dict()
        if isinstance(runtime_bundle, RuntimeBundle)
        else dict(runtime_bundle)
    )
    exported: Dict[str, Any] = dict(legacy_bundle)
    allowed = LEGACY_PUBLIC_EXPORTS | LEGACY_UNDERSCORE_EXPORTS
    unexpected = set(legacy_exports) - allowed
    if unexpected:
        raise ValueError(f"unapproved runtime exports: {sorted(unexpected)}")
    for name in sorted(allowed):
        if name in exported:
            continue
        value = legacy_exports.get(name)
        if value is None:
            continue
        exported[name] = value
    exported["RUNTIME_BUNDLE"] = runtime_bundle
    exported["_RUNTIME_BUNDLE"] = legacy_bundle
    return exported


SERVER_APP_EXPORTS = frozenset(
    RUNTIME_BUNDLE_EXPORTS
    | LEGACY_PUBLIC_EXPORTS
    | LEGACY_UNDERSCORE_EXPORTS
    | {"RUNTIME_BUNDLE", "_RUNTIME_BUNDLE"}
)


__all__ = [
    "LEGACY_UNDERSCORE_EXPORTS",
    "LEGACY_PUBLIC_EXPORTS",
    "RUNTIME_BUNDLE_EXPORTS",
    "RuntimeBundle",
    "SERVER_APP_EXPORTS",
    "build_runtime_namespace",
]
