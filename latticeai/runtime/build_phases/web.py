"""Phases 5-8: domain singletons, the web app, services, foundation routes.

The middle of the build order. ``phase_domain`` runs before ``phase_web``
because the lifespan, the static status routes and the model runtime service
are all wired against the router it builds; ``phase_services`` runs after
``phase_web`` because the AppContext carries handles the web phase produces.
Split out of the single ``build_phases`` module in v11.3.0.

Every heavy import lives *inside* a phase, never at module scope.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from latticeai.runtime.runtime_context import RuntimeContext


# ── phase 5: domain singletons (must precede the web app) ────────────────────
def phase_domain(ctx: RuntimeContext) -> None:
    """The model router, the garden, and the chat service.

    Separated from :func:`phase_services` because ``phase_web`` needs the model
    router: the lifespan hooks, the static status routes, and the model runtime
    service are all wired against it.
    """
    ctx.enter("domain")

    from lattice_brain.graph.runtime import set_llm_router
    from latticeai.models.router import LLMRouter
    from latticeai.services.chat_service import ChatService
    from latticeai.services.p_reinforce import PReinforceGardener
    from latticeai.services.tool_dispatch import configure_tool_dispatch

    model_router = LLMRouter()
    set_llm_router(model_router)
    configure_tool_dispatch(load_users=ctx.load_users, get_user_role=ctx.get_user_role)
    ctx.set(model_router=model_router)

    # v4 garden absorption: the vault is the user-owned markdown mirror and the
    # brain is authoritative. Existing notes import idempotently (content-hash
    # dedup), and garden context queries the brain rather than rescanning.
    gardener = PReinforceGardener(
        ingestion_pipeline=ctx.INGESTION_PIPELINE if ctx.ENABLE_GRAPH else None,
        knowledge_graph=ctx.KNOWLEDGE_GRAPH,
    )
    if ctx.ENABLE_GRAPH:
        try:
            garden_import = gardener.import_vault()
            if garden_import.get("failed"):
                logging.warning(
                    "garden vault import: %s notes failed to ingest",
                    garden_import["failed"],
                )
        except Exception as exc:
            logging.warning("garden vault import skipped: %s", exc)
    ctx.set(gardener=gardener)

    # The chat service owns persistence and trace behaviour once its user-key
    # dependencies exist.
    ctx.set(
        CHAT_SERVICE=ChatService(
            store=ctx.WORKSPACE_OS,
            get_history=ctx.get_history,
            save_to_history=ctx.save_to_history,
            get_history_user=ctx.get_history_user,
        )
    )


def self_model_port(workspace_graph: Any) -> Any:
    """The agent loop's Self-Model port (v11.2.0).

    11.1.0 built ``executor_prompt_for(self_model_summary=…)`` and had nothing
    to pass it; this is what the composition root passes. ``summary_for_prompt``
    never raises and answers ``""`` for a Brain that knows nothing about its
    owner — which produces exactly the pre-11.2.0 prompt bytes.

    A module-level factory rather than a closure inside ``phase_services``
    because a port that cannot be called on its own cannot be tested on its own.
    """

    def _summary(*, user_email: Any = None, workspace_id: Any = None) -> str:
        from lattice_brain.self_model import summary_for_prompt

        graph = workspace_graph()
        if graph is None:
            return ""
        return summary_for_prompt(graph, workspace_id=workspace_id)

    return _summary


# ── phase 6b: retrieval, agent runtime, and the typed AppContext ─────────────
def phase_services(ctx: RuntimeContext) -> None:
    """Retrieval/context assembly, the chat agent runtime, and the AppContext.

    Runs after ``phase_web`` because the AppContext carries handles the web
    phase produces (``ui_file_response``, ``local_sysinfo``, ``graph_stats``)
    and the telegram mirror needs the lifespan's ``_spawn``.
    """
    ctx.enter("services")

    from latticeai.api.chat import build_recent_chat_context
    from latticeai.core.enterprise import capability_registry
    from latticeai.core.mcp_registry import (
        SKILLS_DIR,
        _fetch_skills_marketplace,
        install_skill,
    )
    from latticeai.core.workspace_os import remove_skill_directory
    from latticeai.runtime.chat_wiring import (
        build_chat_agent_runtime_from_context,
        maybe_build_telegram_chat_mirror,
    )
    from latticeai.runtime.context_runtime import build_context_runtime
    from latticeai.services.app_context import AppContext
    from latticeai.services.tool_dispatch import build_agent_runtime
    from latticeai.setup.wizard import get_recommendations, scan_environment
    from latticeai.tools import execute_tool, knowledge_save

    gardener = ctx.gardener
    model_router = ctx.model_router

    def _workspace_settings_payload() -> Dict:
        return {
            "mode": ctx.APP_MODE,
            "host": ctx.DEFAULT_HOST,
            "port": ctx.DEFAULT_PORT,
            "require_auth": ctx.REQUIRE_AUTH,
            "enable_graph": ctx.ENABLE_GRAPH,
            "allow_local_models": ctx.ALLOW_LOCAL_MODELS,
            "static_dir": str(ctx.STATIC_DIR),
            "data_dir": str(ctx.DATA_DIR),
        }

    def _workspace_models_payload() -> Dict:
        return {
            "current_model": ctx.model_router.current_model_id,
            "loaded_models": ctx.model_router.loaded_model_ids,
            "public_model": ctx.PUBLIC_MODEL,
            "local_model": ctx.LOCAL_MODEL,
            "local_draft_model": ctx.LOCAL_DRAFT_MODEL,
        }

    def _embedding_info() -> dict:
        from latticeai.core.embedding_providers import (
            PROVIDER_TYPES,
            embedding_provider_profiles,
        )

        info = ctx.EMBEDDER.as_dict()
        info["available_providers"] = list(PROVIDER_TYPES)
        info["profile"] = ctx.CONFIG.embedding_profile or ""
        info["profiles"] = embedding_provider_profiles()
        return info

    def _allowed_workspaces_for(user: Any) -> Any:
        # No-auth local mode is single-user: no scoping. With auth, reads are
        # scoped to the caller's memberships (legacy-global rows need an
        # explicit maintenance-only opt-in).
        if not ctx.REQUIRE_AUTH or not user:
            return None
        return ctx.PLATFORM.allowed_scopes(user)

    def _recent_chat_context(
        limit: int = 10,
        include_image_missing_replies: bool = True,
        user_email: Optional[str] = None,
        conversation_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> str:
        return build_recent_chat_context(
            get_history=ctx.get_history,
            limit=limit,
            include_image_missing_replies=include_image_missing_replies,
            user_email=user_email,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
        )

    ctx.set(
        _workspace_settings_payload=_workspace_settings_payload,
        _workspace_models_payload=_workspace_models_payload,
        _embedding_info=_embedding_info,
        _allowed_workspaces_for=_allowed_workspaces_for,
        _recent_chat_context=_recent_chat_context,
    )

    context_runtime = build_context_runtime(
        graph_store=ctx._workspace_graph(),
        ingestion_pipeline=ctx.INGESTION_PIPELINE,
        memory_service=ctx.MEMORY_SERVICE,
        gardener=gardener,
        require_auth=ctx.REQUIRE_AUTH,
        allowed_scopes_for_user=lambda user_email: ctx.PLATFORM.allowed_scopes(
            user_email
        ),
    )
    ctx.adopt(
        context_runtime,
        "SEARCH_SERVICE",
        "BRAIN_MEMORY",
        "CONTEXT_ASSEMBLER",
        "ARTIFACT_LEDGER",
        "_scoped_hybrid_search",
    )

    # Telegram chat mirror: registered only when ENABLE_TELEGRAM is truthy, so
    # latticeai.api.chat never imports the 45KB telegram module.
    ctx.set(
        on_chat_message=maybe_build_telegram_chat_mirror(
            enable_telegram=ctx.ENABLE_TELEGRAM,
            spawn=ctx._spawn,
        )
    )

    ctx.set(
        CHAT_AGENT_RUNTIME=build_chat_agent_runtime_from_context(
            build_agent_runtime=build_agent_runtime,
            model_router=model_router,
            execute_tool=execute_tool,
            recent_chat_context=_recent_chat_context,
            clear_history=ctx.clear_history,
            knowledge_save=knowledge_save,
            audit=ctx.append_audit_event,
            hooks=ctx.HOOKS_REGISTRY,
            brain_memory=ctx.BRAIN_MEMORY,
            self_model_summary=self_model_port(
                lambda: ctx.KNOWLEDGE_GRAPH if ctx.ENABLE_GRAPH else None
            ),
        )
    )

    # One typed context object replaces the historical 25-30-kwarg wiring.
    ctx.set(
        app_context=AppContext(
            config=ctx.CONFIG,
            data_dir=ctx.DATA_DIR,
            static_dir=ctx.STATIC_DIR,
            base_dir=ctx.BASE_DIR,
            skills_dir=SKILLS_DIR,
            model_router=model_router,
            workspace_store=ctx.WORKSPACE_OS,
            workspace_service=ctx.WORKSPACE_SERVICE,
            knowledge_graph=ctx.KNOWLEDGE_GRAPH,
            local_kg_watcher=ctx.LOCAL_KG_WATCHER,
            chat_service=ctx.CHAT_SERVICE,
            context_assembler=ctx.CONTEXT_ASSEMBLER,
            artifact_ledger=ctx.ARTIFACT_LEDGER,
            brain_memory=ctx.BRAIN_MEMORY,
            ingestion_pipeline=ctx.INGESTION_PIPELINE if ctx.ENABLE_GRAPH else None,
            chat_agent_runtime=ctx.CHAT_AGENT_RUNTIME,
            gardener=gardener,
            hooks=ctx.HOOKS_REGISTRY,
            realtime_bus=ctx.REALTIME_BUS,
            capability_registry=capability_registry,
            require_user=ctx.require_user,
            require_admin=ctx.require_admin,
            get_current_user=ctx.get_current_user,
            load_users=ctx.load_users,
            get_user_role=ctx.get_user_role,
            enforce_rate_limit=ctx.enforce_rate_limit,
            allowed_workspaces_for=ctx._history_allowed_workspaces_for,
            append_audit_event=ctx.append_audit_event,
            get_audit_log=ctx.get_audit_log,
            get_history=ctx.get_history,
            get_history_user=ctx.get_history_user,
            save_to_history=ctx.save_to_history,
            clear_history=ctx.clear_history,
            clear_conversation=ctx.clear_conversation,
            group_history_conversations=ctx.group_history_conversations,
            get_conversation_messages=ctx.get_conversation_messages,
            conversation_title=ctx.conversation_title,
            enable_graph=ctx.ENABLE_GRAPH,
            require_graph=ctx._require_graph,
            workspace_graph=ctx._workspace_graph,
            graph_stats=ctx._graph_stats_safe,
            # Provider, not a value: REVIEW_QUEUE lands two phases later.
            review_queue=lambda: ctx.REVIEW_QUEUE,
            workspace_models=_workspace_models_payload,
            workspace_settings=_workspace_settings_payload,
            scan_environment=scan_environment,
            local_sysinfo=ctx.local_sysinfo,
            get_recommendations=get_recommendations,
            fetch_skills_marketplace=_fetch_skills_marketplace,
            install_skill=install_skill,
            remove_skill_directory=remove_skill_directory,
            redact_secret_text=ctx.redact_secret_text,
            ui_file_response=ctx.ui_file_response,
            public_model=ctx.PUBLIC_MODEL,
            local_model=ctx.LOCAL_MODEL or "",
            on_chat_message=ctx.on_chat_message,
            funnel_metrics=ctx.FUNNEL_METRICS,
        )
    )
    ctx.app.state.context = ctx.app_context


# ── phase 6: web app and foundation routers ──────────────────────────────────
def phase_web(ctx: RuntimeContext) -> None:
    """Lifespan, the FastAPI app, model runtime service, foundation routers."""
    ctx.enter("web")

    from latticeai.api.admin import create_admin_router
    from latticeai.api.auth import create_auth_router
    from latticeai.api.invitations import create_invitations_router
    from latticeai.api.security_dashboard import (
        create_security_router as _create_security_router,
    )
    from latticeai.api.static_routes import create_static_routes_router
    from latticeai.core.config import default_sso_redirect_uri
    from latticeai.core.policy import policy_matrix
    from latticeai.core.product_hardening import build_product_hardening_status
    from latticeai.core.security import hash_password
    from latticeai.core.users import ensure_user_identity
    from latticeai.runtime.lifespan_runtime import build_lifespan_runtime
    from latticeai.runtime.model_wiring import configure_model_runtime_from_context
    from latticeai.runtime.router_registration import (
        build_auth_admin_security_router_bundle,
        build_static_routes_bundle,
    )
    from latticeai.runtime.web_runtime import build_web_runtime
    from latticeai.services.model_runtime import (
        LOCAL_SERVER_PROCESSES,
        build_model_runtime,
    )
    from latticeai.tools import ensure_agent_root

    lifespan_runtime = build_lifespan_runtime(
        app_mode=ctx.APP_MODE,
        enable_telegram=ctx.ENABLE_TELEGRAM,
        autoload_models=ctx.AUTOLOAD_MODELS,
        is_public_mode=ctx.IS_PUBLIC_MODE,
        public_model=ctx.PUBLIC_MODEL,
        allow_local_models=ctx.ALLOW_LOCAL_MODELS,
        local_model=ctx.LOCAL_MODEL,
        local_draft_model=ctx.LOCAL_DRAFT_MODEL,
        model_idle_unload_seconds=ctx.MODEL_IDLE_UNLOAD_SECONDS,
        model_router=ctx.model_router,
        local_kg_watcher=ctx.LOCAL_KG_WATCHER,
        local_server_processes=LOCAL_SERVER_PROCESSES,
        logger=logging,
    )
    ctx.adopt(lifespan_runtime, "_spawn", "lifespan")

    web_runtime = build_web_runtime(
        app_mode=ctx.APP_MODE,
        app_version=ctx.APP_VERSION,
        lifespan=ctx.lifespan,
        default_host=ctx.DEFAULT_HOST,
        default_port=ctx.DEFAULT_PORT,
        cors_extra_origins=ctx.CORS_EXTRA_ORIGINS,
        cors_allow_network=ctx.CORS_ALLOW_NETWORK,
        static_dir=ctx.STATIC_DIR,
        csrf_trusted_origins=ctx.CSRF_TRUSTED_ORIGINS,
    )
    ctx.adopt(web_runtime, "app")
    ensure_agent_root()

    ctx.set(
        model_runtime_service=configure_model_runtime_from_context(
            build_model_runtime=build_model_runtime,
            router=ctx.model_router,
            APP_MODE=ctx.APP_MODE,
            DEFAULT_HOST=ctx.DEFAULT_HOST,
            DEFAULT_PORT=ctx.DEFAULT_PORT,
            DATA_DIR=ctx.DATA_DIR,
            BASE_DIR=ctx.BASE_DIR,
            ENABLE_TELEGRAM=ctx.ENABLE_TELEGRAM,
            ENABLE_GRAPH=ctx.ENABLE_GRAPH,
            AUTOLOAD_MODELS=ctx.AUTOLOAD_MODELS,
            MODEL_IDLE_UNLOAD_SECONDS=ctx.MODEL_IDLE_UNLOAD_SECONDS,
            ALLOW_MODEL_DOWNLOADS=ctx.ALLOW_MODEL_DOWNLOADS,
            MODEL_DOWNLOAD_TIMEOUT=ctx.MODEL_DOWNLOAD_TIMEOUT,
            ALLOW_LOCAL_MODELS=ctx.ALLOW_LOCAL_MODELS,
            REQUIRE_AUTH=ctx.REQUIRE_AUTH,
            INVITE_GATE_ENABLED=ctx.INVITE_GATE_ENABLED,
            ALLOW_PLAINTEXT_API_KEYS=ctx.ALLOW_PLAINTEXT_API_KEYS,
            CORS_ALLOW_NETWORK=ctx.CORS_ALLOW_NETWORK,
            PUBLIC_MODEL=ctx.PUBLIC_MODEL,
            LOCAL_MODEL=ctx.LOCAL_MODEL,
            IS_PUBLIC_MODE=ctx.IS_PUBLIC_MODE,
            keyring=ctx.keyring,
            get_current_user=ctx.get_current_user,
            get_user_api_key=ctx.get_user_api_key,
        )
    )

    static_routes_bundle = build_static_routes_bundle(
        create_static_routes_router=create_static_routes_router,
        static_dir=ctx.STATIC_DIR,
        invite_gate_enabled=ctx.INVITE_GATE_ENABLED,
        invite_code=ctx.INVITE_CODE,
        invite_cookie_secret=ctx.INVITE_COOKIE_SECRET,
        secure_cookies=ctx.SECURE_COOKIES,
        app_mode=ctx.APP_MODE,
        model_router=ctx.model_router,
        require_user=ctx.require_user,
    )
    ctx.adopt(
        static_routes_bundle,
        "STATIC_ROUTES",
        "ui_file_response",
        "local_sysinfo",
        "invite_authorized",
    )

    foundation_router_bundle = build_auth_admin_security_router_bundle(
        create_auth_router=create_auth_router,
        sso_default_redirect_uri=default_sso_redirect_uri(ctx.DEFAULT_PORT),
        load_users=ctx.load_users,
        save_users=ctx.save_users,
        hash_password=hash_password,
        verify_and_migrate_password=ctx.verify_and_migrate_password,
        create_session=ctx.create_session,
        get_session_email=ctx.get_session_email,
        invalidate_session=ctx.invalidate_session,
        extract_bearer_token=ctx._extract_bearer_token,
        get_user_role=ctx.get_user_role,
        require_user=ctx.require_user,
        check_ip_rate_limit=ctx._check_rate_limit,
        client_ip=ctx._client_ip,
        get_sso_settings=ctx.get_sso_settings,
        get_sso_discovery=ctx._get_sso_discovery,
        public_sso_config=ctx.public_sso_config,
        open_registration=ctx.OPEN_REGISTRATION,
        session_ttl=ctx._SESSION_TTL,
        require_auth=ctx.REQUIRE_AUTH,
        secure_cookies=ctx.SECURE_COOKIES,
        invite_authorized=ctx.invite_authorized,
        ensure_identity=ensure_user_identity,
        create_admin_router=create_admin_router,
        require_admin=ctx.require_admin,
        get_history=ctx.get_history,
        get_audit_log=ctx.get_audit_log,
        audit_file=ctx.AUDIT_FILE,
        public_user=ctx.public_user,
        load_vpc_config=ctx.load_vpc_config,
        save_vpc_config=ctx.save_vpc_config,
        build_admin_audit_report=ctx.build_admin_audit_report,
        build_sensitivity_report=ctx.build_sensitivity_report,
        append_audit_event=ctx.append_audit_event,
        save_sso_config=ctx.save_sso_config,
        knowledge_graph=ctx.KNOWLEDGE_GRAPH,
        enable_graph=ctx.ENABLE_GRAPH,
        logger=logging,
        invite_code=ctx.INVITE_CODE,
        invite_gate_enabled=ctx.INVITE_GATE_ENABLED,
        default_port=ctx.DEFAULT_PORT,
        policy_matrix=policy_matrix,
        build_product_hardening_status=build_product_hardening_status,
        config=ctx.CONFIG,
        kg_portability=ctx.KG_PORTABILITY,
        device_identity=ctx.DEVICE_IDENTITY,
        create_invitations_router=create_invitations_router,
        invitation_store=ctx.INVITATION_STORE,
        workspace_service=ctx.WORKSPACE_SERVICE,
        user_id_for_email=ctx.user_id_for_email,
        create_security_router=_create_security_router,
        classify_sensitive_message=ctx.classify_sensitive_message,
    )
    ctx.adopt(
        foundation_router_bundle,
        "auth_router",
        "admin_router",
        "invitations_router",
        "security_router",
        "_graph_stats_safe",
        "_product_hardening_status",
        "_security_audit_events_safe",
        "_security_list_uploaded_files",
    )


def phase_foundation_routes(ctx: RuntimeContext) -> None:
    """Mount the foundation routers once the AppContext exists."""
    ctx.enter("foundation_routes")

    from latticeai.api.workspace import create_workspace_router
    from latticeai.runtime.router_registration import register_foundation_routers

    register_foundation_routers(
        ctx.app,
        static_router=ctx.STATIC_ROUTES.router,
        auth_router=ctx.auth_router,
        admin_router=ctx.admin_router,
        invitations_router=ctx.invitations_router,
        security_router=ctx.security_router,
        create_workspace_router=create_workspace_router,
        context=ctx.app_context,
    )

