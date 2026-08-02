"""Lattice AI application factory.

``create_app`` performs *all* construction that ``latticeai.server_app``
historically ran at import time: MLX/GPU device init, config parsing,
singleton construction (knowledge graph, workspace OS, registries, pipelines,
gardener) and router assembly. Importing this module — like importing
``latticeai.server_app`` — has **no side effects**: nothing heavy is imported
and no file is created until ``create_app``/``build_runtime`` is called.

``build_runtime`` returns the full constructed namespace (every name the
legacy module-level assembly exposed); ``latticeai.server_app`` proxies it
lazily via module ``__getattr__`` for backwards compatibility.

The assembly itself lives in :mod:`latticeai.runtime.build_phases` as ten
ordered phases sharing a :class:`~latticeai.runtime.runtime_context.RuntimeContext`.
This module is now only the *orchestrator*: run the phases, then select the
explicit compatibility surface. It used to be a single 1,300-line ``_build``
closure, which worked because closures resolve free variables at call time —
the RuntimeContext preserves exactly that property while naming the state.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Optional

from latticeai.runtime.build_phases import BUILD_PHASES
from latticeai.runtime.namespace_runtime import RuntimeBundle, build_runtime_namespace
from latticeai.runtime.router_registration import build_router_bundle
from latticeai.runtime.runtime_context import RuntimeContext

if TYPE_CHECKING:  # imports for annotations only — keep module import light
    from fastapi import FastAPI

    from latticeai.core.config import Config


def _legacy_exports(ctx: RuntimeContext) -> Dict[str, Any]:
    """The explicit, allowlisted compatibility surface for ``server_app``.

    Two kinds of name appear here: values the phases produced (read off the
    context) and pass-through re-exports of module functions that historical
    callers reached through ``server_app``. Keeping them in one function makes
    the surface countable — ``namespace_runtime`` rejects anything not on its
    allowlist.
    """
    from latticeai.api.security_dashboard import (
        create_security_router as _create_security_router,
    )
    from latticeai.api.workspace import _workspace_scope_from_request
    from latticeai.core.audit import (
        build_admin_audit_report as _build_admin_audit_report,
    )
    from latticeai.core.audit import (
        build_sensitivity_report as _build_sensitivity_report,
    )
    from latticeai.core.audit import (
        classify_sensitive_message as _classify_sensitive_message,
    )
    from latticeai.core.audit import get_audit_log as _get_audit_log
    from latticeai.core.mcp_registry import _fetch_skills_marketplace
    from latticeai.core.model_compat import (
        list_cached_profiles as _list_compat_profiles,
    )
    from latticeai.core.security import (
        check_ip_rate_limit as _check_ip_rate_limit,
    )
    from latticeai.core.security import hash_password, verify_password
    from latticeai.core.security import (
        redact_secret_text as _redact_secret_text,
    )
    from latticeai.core.tool_registry import TOOL_CATALOG_BRIEF as _TOOL_CATALOG_BRIEF
    from latticeai.core.users import user_id_for_email as _user_id_for_email
    from latticeai.services.model_runtime import (
        ENGINE_MODEL_CATALOG,
        filter_lower_family_versions,
        normalize_local_model_request,
    )
    from latticeai.services.tool_dispatch import (
        LOCAL_WRITE_BLOCKED_PREFIXES as _LOCAL_WRITE_BLOCKED_PREFIXES,
    )
    from latticeai.services.tool_dispatch import TOOL_GOVERNANCE
    from latticeai.services.tool_dispatch import (
        TOOL_GOVERNANCE_DEFAULT as _TOOL_GOVERNANCE_DEFAULT,
    )
    from latticeai.services.tool_dispatch import agent_risk as _agent_risk
    from latticeai.services.tool_dispatch import check_tool_role as _check_tool_role
    from latticeai.services.tool_dispatch import tool_response as _tool_response

    return {
        # ── pass-through re-exports ──────────────────────────────────────
        "ENGINE_MODEL_CATALOG": ENGINE_MODEL_CATALOG,
        "TOOL_GOVERNANCE": TOOL_GOVERNANCE,
        "filter_lower_family_versions": filter_lower_family_versions,
        "hash_password": hash_password,
        "normalize_local_model_request": normalize_local_model_request,
        "verify_password": verify_password,
        "_LOCAL_WRITE_BLOCKED_PREFIXES": _LOCAL_WRITE_BLOCKED_PREFIXES,
        "_TOOL_CATALOG_BRIEF": _TOOL_CATALOG_BRIEF,
        "_TOOL_GOVERNANCE_DEFAULT": _TOOL_GOVERNANCE_DEFAULT,
        "_agent_risk": _agent_risk,
        "_build_admin_audit_report": _build_admin_audit_report,
        "_build_sensitivity_report": _build_sensitivity_report,
        "_check_ip_rate_limit": _check_ip_rate_limit,
        "_check_tool_role": _check_tool_role,
        "_classify_sensitive_message": _classify_sensitive_message,
        "_create_security_router": _create_security_router,
        "_fetch_skills_marketplace": _fetch_skills_marketplace,
        "_get_audit_log": _get_audit_log,
        "_list_compat_profiles": _list_compat_profiles,
        "_redact_secret_text": _redact_secret_text,
        "_tool_response": _tool_response,
        "_user_id_for_email": _user_id_for_email,
        "_workspace_scope_from_request": _workspace_scope_from_request,
        # ── values the phases produced ───────────────────────────────────
        "enforce_rate_limit": ctx.enforce_rate_limit,
        "_RATE_LIMIT_ENABLED": ctx._RATE_LIMIT_ENABLED,
        "_SESSION_TTL": ctx._SESSION_TTL,
        "_allowed_workspaces_for": ctx._allowed_workspaces_for,
        "_bytes_match_extension": ctx._bytes_match_extension,
        "_check_rate_limit": ctx._check_rate_limit,
        "_client_ip": ctx._client_ip,
        "_embedding_info": ctx._embedding_info,
        "_get_sso_discovery": ctx._get_sso_discovery,
        "_graph_stats_safe": ctx._graph_stats_safe,
        "_host_is_loopback": ctx._host_is_loopback,
        "_llm_generate_sync": ctx._llm_generate_sync,
        "_product_hardening_status": ctx._product_hardening_status,
        "_recent_chat_context": ctx._recent_chat_context,
        "_require_graph": ctx._require_graph,
        "_scoped_hybrid_search": ctx._scoped_hybrid_search,
        "_security_audit_events_safe": ctx._security_audit_events_safe,
        "_security_list_uploaded_files": ctx._security_list_uploaded_files,
        "_spawn": ctx._spawn,
        "_workspace_graph": ctx._workspace_graph,
        "_workspace_models_payload": ctx._workspace_models_payload,
        "_workspace_settings_payload": ctx._workspace_settings_payload,
    }


def build_context(config: "Optional[Config]" = None) -> RuntimeContext:
    """Run every build phase in order and return the populated context.

    Exposed separately from :func:`build_runtime` so tests can inspect the
    assembly (which phase produced what) without going through the legacy
    namespace adapter.
    """
    ctx = RuntimeContext(config)
    for phase in BUILD_PHASES:
        phase(ctx)
    return ctx


def _build(config: "Optional[Config]" = None) -> Dict[str, Any]:
    """Assemble the application and return its explicit export namespace."""
    ctx = build_context(config)
    runtime_bundle = RuntimeBundle(
        app=ctx.app,
        CONFIG=ctx.CONFIG,
        KNOWLEDGE_GRAPH=ctx.KNOWLEDGE_GRAPH,
        INGESTION_PIPELINE=ctx.INGESTION_PIPELINE,
        AGENT_RUNTIME=ctx.AGENT_RUNTIME,
        HOOKS_REGISTRY=ctx.HOOKS_REGISTRY,
        REVIEW_QUEUE=ctx.REVIEW_QUEUE,
        AGENT_REGISTRY=ctx.AGENT_REGISTRY,
        model_router=ctx.model_router,
        build_runtime=build_runtime,
        get_shared_runtime=get_shared_runtime,
        create_app=create_app,
        config_runtime=ctx.config_runtime,
        security_runtime=ctx.security_runtime,
        brain_runtime=ctx.brain_runtime,
        model_runtime=ctx.model_runtime,
        router_bundle=build_router_bundle(ctx.app, ctx.app_context),
    )
    return build_runtime_namespace(
        runtime_bundle=runtime_bundle,
        legacy_exports=_legacy_exports(ctx),
    )


@dataclass(frozen=True)
class LegacyRuntimeNamespace:
    """Compatibility adapter for the historical module-level runtime surface."""

    namespace: Dict[str, Any]

    def bind(self, runtime: "AppRuntime") -> None:
        runtime.__dict__.update(self.namespace)


class AppRuntime:
    """The constructed application namespace.

    Exposes every name the legacy import-time ``server_app`` module defined
    (``app``, ``KNOWLEDGE_GRAPH``, ``load_users``, …) as attributes.
    """

    # Declared because they are bound dynamically from the namespace dict;
    # these two are the ones this module itself reads back.
    app: Any
    CONFIG: Any

    def __init__(self, namespace: Dict[str, Any]) -> None:
        self._legacy_namespace = LegacyRuntimeNamespace(namespace)
        self._legacy_namespace.bind(self)


_runtime_lock = threading.RLock()
_shared_runtime: "Optional[AppRuntime]" = None


def build_runtime(config: "Optional[Config]" = None) -> AppRuntime:
    """Construct a fresh runtime (all singletons + FastAPI app)."""
    return AppRuntime(_build(config))


def get_shared_runtime() -> AppRuntime:
    """The process-wide runtime backing ``latticeai.server_app`` / ``server``.

    Built once, on first access — never at import time.
    """
    global _shared_runtime
    if _shared_runtime is None:
        with _runtime_lock:
            if _shared_runtime is None:
                _shared_runtime = build_runtime()
    return _shared_runtime


def create_app(config: "Optional[Config]" = None) -> "FastAPI":
    """Build and return the FastAPI application (the factory entrypoint)."""
    return build_runtime(config).app


def main() -> None:
    """Serve the shared runtime (``python -m latticeai.server_app``).

    This used to call ``get_shared_runtime().main()``, but ``main`` was a local
    inside the old ``_build`` closure and was never on the export allowlist, so
    the call raised ``AttributeError`` — the module entrypoint was broken.
    Serving from here needs no export at all.
    """
    import uvicorn

    runtime = get_shared_runtime()
    config = runtime.CONFIG
    host, port = config.host, config.port
    print(
        f"🧠 Lattice AI Server starting in {config.app_mode} mode "
        f"on http://{host}:{port}"
    )
    uvicorn.run(runtime.app, host=host, port=port, log_level="info")
