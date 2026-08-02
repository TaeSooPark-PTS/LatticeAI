"""The ordered phases that build the Lattice AI application.

Each phase reads what earlier phases published on the :class:`RuntimeContext`
and publishes its own results. The order below *is* the dependency order and is
fixed by ``tests/unit/test_runtime_context.py``:

1.  ``platform``   — MLX/GPU device selection (the only step that touches hardware)
2.  ``config``     — configuration, security settings, paths, filesystem layout
3.  ``identity``   — users, sessions, audit, access control, API keys, SSO/VPC config
4.  ``brain``      — embedder, knowledge graph, conversations, hooks, persistence, history
5.  ``domain``     — model router, garden, chat service (needed by the web phase)
6.  ``web``        — lifespan, the FastAPI app, model runtime, static + foundation routers
7.  ``services``   — retrieval/context, chat agent runtime, the typed AppContext
8.  ``foundation_routes`` — mount the foundation routers now that AppContext exists
9.  ``platform_features`` — workspace platform, automation, review, command centre
10. ``interaction`` — model/chat/search/tools routers and the brain tail routers

Every heavy import lives *inside* a phase, never at module scope: importing
this module must stay free of GPU init, singleton construction, and filesystem
writes (``tests/unit/test_app_factory.py`` enforces that).

Why closures still appear here: several handlers must resolve a dependency at
call time rather than at construction time, because the dependency is built by
a later phase. Those read through ``ctx``, which is exactly the late binding
the original single function got from Python's closure rules.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from latticeai.core.quiet import quiet
from latticeai.runtime.runtime_context import RuntimeContext


# ── phase 1: platform ────────────────────────────────────────────────────────
def phase_platform(ctx: RuntimeContext) -> None:
    """Select the MLX Metal device in the main thread.

    Kept first and alone: it is the one step that talks to hardware, and it
    must happen on the main thread before any worker touches MLX.
    """
    ctx.enter("platform")
    try:
        import mlx.core as mx

        mx.set_default_device(mx.gpu)  # type: ignore[arg-type]
        print("✅ MLX Metal context initialized in main thread.")
    except Exception as exc:
        print(f"⚠️ MLX Metal context unavailable: {exc}")
        mx = None  # type: ignore[assignment]
    ctx.set(mx=mx)


# ── phase 2: configuration and paths ─────────────────────────────────────────
def phase_config(ctx: RuntimeContext) -> None:
    """Parse configuration once and lay out the data directory."""
    ctx.enter("config")

    from latticeai.core.security import host_is_loopback as _host_is_loopback_impl
    from latticeai.core.workspace_os import WORKSPACE_OS_VERSION
    from latticeai.runtime.config_runtime import build_config_runtime
    from latticeai.runtime.security_runtime import build_security_runtime

    try:
        import keyring
    except Exception:
        keyring = None  # type: ignore[assignment]

    config_runtime = build_config_runtime(ctx.config_arg)
    ctx.set(config_runtime=config_runtime, keyring=keyring)
    ctx.adopt(
        config_runtime,
        "CONFIG",
        "APP_MODE",
        "IS_PUBLIC_MODE",
        "DEFAULT_HOST",
        "DEFAULT_PORT",
        "ENABLE_TELEGRAM",
        "ENABLE_GRAPH",
        "AUTOLOAD_MODELS",
        "MODEL_IDLE_UNLOAD_SECONDS",
        "ALLOW_MODEL_DOWNLOADS",
        "MODEL_DOWNLOAD_TIMEOUT",
        "ALLOW_LOCAL_MODELS",
        "REQUIRE_AUTH",
        "ALLOW_PLAINTEXT_API_KEYS",
        "CORS_ALLOW_NETWORK",
        "CORS_EXTRA_ORIGINS",
        "PUBLIC_MODEL",
        "LOCAL_MODEL",
        "LOCAL_DRAFT_MODEL",
    )
    ctx.set(
        APP_VERSION=WORKSPACE_OS_VERSION,
        OPEN_REGISTRATION=ctx.CONFIG.open_registration,
        _RATE_LIMIT_ENABLED=ctx.CONFIG.rate_limit_enabled,
        # Forwarded headers (X-Forwarded-For / CF-Connecting-IP) are honoured
        # for IP rate limiting only when the direct peer is a trusted proxy.
        _host_is_loopback=lambda host: _host_is_loopback_impl(host),
    )

    security_runtime = build_security_runtime(ctx.CONFIG)
    ctx.set(security_runtime=security_runtime)
    ctx.adopt(
        security_runtime,
        "SSO_DISCOVERY_URL",
        "SSO_CLIENT_ID",
        "SSO_CLIENT_SECRET",
        "SSO_REDIRECT_URI",
        "SSO_PROVIDER_NAME",
        "INVITE_CODE",
        "INVITE_COOKIE_SECRET",
        "INVITE_GATE_ENABLED",
        "SECURE_COOKIES",
    )

    from pathlib import Path

    base_dir = Path(__file__).resolve().parent.parent.parent
    data_dir = ctx.CONFIG.data_dir
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        data_dir.chmod(0o700)
    except OSError:
        quiet()

    ctx.set(
        BASE_DIR=base_dir,
        DATA_DIR=data_dir,
        STATIC_DIR=ctx.CONFIG.static_dir,
        USERS_FILE=data_dir / "users.json",
        HISTORY_FILE=data_dir / "chat_history.json",
        VPC_FILE=data_dir / "vpc_config.json",
        AUDIT_FILE=data_dir / "audit_log.json",
        SSO_FILE=data_dir / "sso_config.json",
    )


# ── phase 3: identity, audit, access ─────────────────────────────────────────
def phase_identity(ctx: RuntimeContext) -> None:
    """Users, sessions, audit, access control, API keys, SSO/VPC config."""
    ctx.enter("identity")

    from fastapi import HTTPException, Request

    from latticeai.core.mcp_registry import create_mcp_install_state
    from latticeai.core.security import (
        check_ip_rate_limit as _check_ip_rate_limit,
    )
    from latticeai.core.security import (
        client_ip as _client_ip_impl,
    )
    from latticeai.core.security import (
        enforce_rate_limit as _enforce_rate_limit,
    )
    from latticeai.core.security import hash_password, verify_password
    from latticeai.core.security import (
        redact_secret_text as _redact_secret_text,
    )
    from latticeai.core.users import (
        ensure_user_identity,
        load_users_file,
        migrate_knowledge_graph_identity,
        save_users_file,
    )
    from latticeai.core.users import user_id_for_email as _user_id_for_email
    from latticeai.runtime.access_runtime import build_access_runtime
    from latticeai.runtime.audit_runtime import build_audit_runtime
    from latticeai.runtime.bootstrap import build_session_runtime
    from latticeai.runtime.network_config_runtime import build_vpc_runtime
    from latticeai.runtime.sso_config_runtime import build_sso_config_runtime
    from latticeai.runtime.user_key_runtime import build_user_key_runtime

    # MCP install state (extracted to mcp_registry during the server decomp).
    mcp_state = create_mcp_install_state(ctx.DATA_DIR)
    ctx.adopt(
        mcp_state,
        "load_mcp_installs",
        "mcp_public_item",
        "recommend_mcps",
        "install_mcp",
    )

    def load_users() -> Dict[str, Any]:
        users = load_users_file(ctx.USERS_FILE)
        email_to_id = {
            email: str(user["id"])
            for email, user in users.items()
            if isinstance(user, dict) and user.get("id")
        }
        try:
            migrate_knowledge_graph_identity(
                ctx.DATA_DIR / "knowledge_graph.sqlite", email_to_id
            )
        except Exception as exc:
            logging.warning("knowledge graph identity migration skipped: %s", exc)
        try:
            # WORKSPACE_OS is built in the `brain` phase; resolving it here (at
            # call time) is why this stays a closure over ctx.
            ctx.WORKSPACE_OS.migrate_workspace_identities(email_to_id)
        except Exception as exc:
            logging.warning("workspace identity migration skipped: %s", exc)
        return users

    def save_users(users: Dict[str, Any]) -> None:
        save_users_file(ctx.USERS_FILE, users)

    def user_id_for_email(email: Optional[str]) -> Optional[str]:
        return _user_id_for_email(load_users(), email)

    def verify_and_migrate_password(
        email: str, plain: str, stored: str, users: Dict
    ) -> bool:
        """평문 비밀번호를 투명하게 해시로 마이그레이션. 마이그레이션 발생 시 audit log 남김."""
        if ":" in stored and len(stored) > 64:
            return verify_password(plain, stored)
        if plain == stored:
            users[email]["password"] = hash_password(plain)
            save_users(users)
            try:
                ctx.append_audit_event(
                    "password_migrated_from_plaintext", user_email=email
                )
            except Exception as exc:
                logging.warning("audit log failed on password migration: %s", exc)
            logging.info("Migrated plaintext password to scrypt hash for %s", email)
            return True
        return False

    def redact_secret_text(text: str) -> str:
        return _redact_secret_text(text)

    def _check_rate_limit(
        ip: str, action: str, max_calls: int, window_secs: float
    ) -> None:
        _check_ip_rate_limit(ip, action, max_calls=max_calls, window_secs=window_secs)

    def _client_ip(request: Request) -> str:
        return _client_ip_impl(request)

    def enforce_rate_limit(email: str, bucket_key: str) -> None:
        _enforce_rate_limit(email, bucket_key, enabled=ctx._RATE_LIMIT_ENABLED)

    ctx.set(
        load_users=load_users,
        save_users=save_users,
        user_id_for_email=user_id_for_email,
        verify_and_migrate_password=verify_and_migrate_password,
        redact_secret_text=redact_secret_text,
        _check_rate_limit=_check_rate_limit,
        _client_ip=_client_ip,
        enforce_rate_limit=enforce_rate_limit,
    )

    # Session token lifecycle; user_id_for_email is the injected subject resolver.
    session_runtime = build_session_runtime(user_id_resolver=user_id_for_email)
    ctx.adopt(
        session_runtime,
        "_SESSION_TTL",
        "_session_store",
        "create_session",
        "get_session_email",
        "invalidate_session",
    )

    # Audit is built after redaction is available, because every event is
    # redacted before it is written.
    audit_runtime = build_audit_runtime(
        audit_file=ctx.AUDIT_FILE,
        logging=logging,
        redact_fn=redact_secret_text,
    )
    ctx.adopt(audit_runtime, "get_audit_log", "append_audit_event")

    access_runtime = build_access_runtime(
        config=ctx.CONFIG,
        require_auth=ctx.REQUIRE_AUTH,
        http_exception=HTTPException,
        request_type=Request,
        load_users=load_users,
        get_session_email=ctx.get_session_email,
        user_id_for_email=_user_id_for_email,
    )
    ctx.adopt(
        access_runtime,
        "get_user_role",
        "_extract_bearer_token",
        "get_current_user",
        "require_user",
        "require_admin",
        "public_user",
    )

    user_key_runtime = build_user_key_runtime(
        load_users=load_users,
        save_users=save_users,
        ensure_user_identity=ensure_user_identity,
        keyring=ctx.keyring,
        allow_plaintext_api_keys=ctx.ALLOW_PLAINTEXT_API_KEYS,
        logging=logging,
        http_exception=HTTPException,
    )
    ctx.adopt(
        user_key_runtime, "get_history_user", "get_user_api_key", "set_user_api_key"
    )

    # SSO config + OIDC discovery share one closure, so saving the config
    # actually invalidates the discovery cache.
    sso_runtime = build_sso_config_runtime(
        sso_file=ctx.SSO_FILE,
        discovery_url=ctx.SSO_DISCOVERY_URL,
        client_id=ctx.SSO_CLIENT_ID,
        client_secret=ctx.SSO_CLIENT_SECRET,
        redirect_uri=ctx.SSO_REDIRECT_URI,
        provider_name=ctx.SSO_PROVIDER_NAME,
        logging=logging,
    )
    ctx.adopt(
        sso_runtime,
        "_sso_env_defaults",
        "get_sso_settings",
        "public_sso_config",
        "save_sso_config",
        "_get_sso_discovery",
        "_sso_states",
    )

    vpc_runtime = build_vpc_runtime(vpc_file=ctx.VPC_FILE, logging=logging)
    ctx.adopt(vpc_runtime, "load_vpc_config", "save_vpc_config")


# ── phase 4: brain and persistence ───────────────────────────────────────────
def phase_brain(ctx: RuntimeContext) -> None:
    """Embedder, knowledge graph, conversations, hooks, persistence, history."""
    ctx.enter("brain")

    import os

    from fastapi import HTTPException

    from lattice_brain.graph.schema import set_embed_dim
    from lattice_brain.ingestion import IngestionItem
    from lattice_brain.storage import storage_from_env
    from latticeai.core.audit import (
        build_admin_audit_report as _build_admin_audit_report,
    )
    from latticeai.core.audit import (
        build_sensitivity_report as _build_sensitivity_report,
    )
    from latticeai.core.audit import (
        classify_sensitive_message as _classify_sensitive_message,
    )
    from latticeai.core.embedding_providers import (
        resolve_embedder,
        resolve_embedding_profile,
    )
    from latticeai.core.security import (
        bytes_match_extension as _bytes_match_extension_impl,
    )
    from latticeai.models.router import normalize_branding
    from latticeai.runtime.brain_runtime import build_brain_runtime
    from latticeai.runtime.history_runtime import build_history_query_runtime
    from latticeai.runtime.history_writer import HistoryWriterDeps, write_chat_turn
    from latticeai.runtime.hooks_runtime import build_hooks_runtime
    from latticeai.runtime.persistence_runtime import build_persistence_runtime

    # Resolve the configured embedding provider once. Degrades to the offline
    # hash fallback when unavailable, recording requested-vs-active provider.
    try:
        embedding_profile = resolve_embedding_profile(ctx.CONFIG.embedding_profile)
    except ValueError as exc:
        logging.warning("Embedding profile ignored: %s", exc)
        embedding_profile = {}

    provider = ctx.CONFIG.embedding_provider
    model = ctx.CONFIG.embedding_model or str(embedding_profile.get("model") or "")
    dim = ctx.CONFIG.embedding_dim or int(embedding_profile.get("dimensions") or 0)
    if ctx.CONFIG.embedding_profile and provider in {"", "hash", "local", "fallback"}:
        provider = str(embedding_profile.get("provider") or provider)

    embedder = resolve_embedder(
        provider,
        model=model,
        base_url=ctx.CONFIG.embedding_base_url,
        api_key=ctx.CONFIG.embedding_api_key,
        dim=dim,
        timeout=ctx.CONFIG.embedding_timeout,
        extra={"target": ctx.CONFIG.embedding_custom_target},
        probe=provider not in {"", "hash", "local", "fallback"},
    )
    set_embed_dim(int(getattr(embedder, "dim", None) or dim or 384))
    if embedder.fell_back:
        logging.warning(
            "Embedding provider %s unavailable: %s", embedder.requested, embedder.detail
        )

    storage_engine = (
        storage_from_env(os.environ, data_dir=ctx.DATA_DIR) if ctx.ENABLE_GRAPH else None
    )
    ctx.set(
        EMBEDDING_PROFILE=embedding_profile,
        EMBEDDER=embedder,
        STORAGE_ENGINE=storage_engine,
    )

    brain_runtime = build_brain_runtime(
        data_dir=ctx.DATA_DIR,
        history_file=ctx.HISTORY_FILE,
        enable_graph=ctx.ENABLE_GRAPH,
        embedder=embedder,
        storage_engine=storage_engine,
    )
    ctx.set(brain_runtime=brain_runtime)
    # CONVERSATIONS is the v4 durable episodic store: unbounded history in the
    # same SQLite file as the graph, so backup/restore covers it for free.
    ctx.adopt(brain_runtime, "KNOWLEDGE_GRAPH", "CONVERSATIONS")

    def save_to_history(
        role: str,
        message: str,
        user_email: Optional[str] = None,
        user_nickname: Optional[str] = None,
        source: Optional[str] = None,
        conversation_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> None:
        """Persist one chat turn. Logic lives in runtime/history_writer.py.

        The dependency bundle is built per call because INGESTION_PIPELINE is
        published later in this same phase; resolving through ctx keeps the
        ordering contract in one place instead of reordering the phase.
        """
        write_chat_turn(
            role,
            message,
            user_email=user_email,
            user_nickname=user_nickname,
            source=source,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            deps=HistoryWriterDeps(
                conversations=ctx.CONVERSATIONS,
                append_audit_event=ctx.append_audit_event,
                classify_sensitive_message=ctx.classify_sensitive_message,
                redact_secret_text=ctx.redact_secret_text,
                normalize_branding=normalize_branding,
                ingestion_pipeline=ctx.INGESTION_PIPELINE,
                ingestion_item_factory=IngestionItem,
                enable_graph=ctx.ENABLE_GRAPH,
                knowledge_graph=ctx.KNOWLEDGE_GRAPH,
            ),
        )

    def classify_sensitive_message(item: Dict, index: int) -> Dict:
        return _classify_sensitive_message(item, index)

    def build_sensitivity_report(history: List[Dict]) -> Dict:
        return _build_sensitivity_report(history)

    def build_admin_audit_report(
        users: Dict, audit_events: Optional[List[Dict]] = None
    ) -> Dict:
        graph_stats = None
        try:
            if ctx.ENABLE_GRAPH and ctx.KNOWLEDGE_GRAPH:
                graph_stats = ctx.KNOWLEDGE_GRAPH.stats()
        except Exception:
            quiet()
        return _build_admin_audit_report(
            ctx.AUDIT_FILE,
            users,
            get_user_role=ctx.get_user_role,
            graph_stats=graph_stats,
            audit_events=audit_events,
        )

    def _bytes_match_extension(data: bytes, ext: str) -> bool:
        return _bytes_match_extension_impl(data, ext)

    def _require_graph() -> None:
        if not ctx.ENABLE_GRAPH or ctx.KNOWLEDGE_GRAPH is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    "지식 그래프가 비활성화되어 있습니다. "
                    "LATTICEAI_ENABLE_GRAPH=true 설정 후 다시 시도해 주세요."
                ),
            )

    def _workspace_graph() -> Any:
        return (
            ctx.KNOWLEDGE_GRAPH
            if (ctx.ENABLE_GRAPH and ctx.KNOWLEDGE_GRAPH)
            else None
        )

    ctx.set(
        save_to_history=save_to_history,
        classify_sensitive_message=classify_sensitive_message,
        build_sensitivity_report=build_sensitivity_report,
        build_admin_audit_report=build_admin_audit_report,
        _bytes_match_extension=_bytes_match_extension,
        _require_graph=_require_graph,
        _workspace_graph=_workspace_graph,
    )

    # The hooks registry is built ahead of the watcher so folder-watch
    # reindexes can fire the pre_index/post_index lifecycle hooks.
    hooks_runtime = build_hooks_runtime(
        data_dir=ctx.DATA_DIR,
        enable_graph=ctx.ENABLE_GRAPH,
        knowledge_graph_getter=lambda: ctx.KNOWLEDGE_GRAPH,
    )
    ctx.adopt(hooks_runtime, "HOOKS_REGISTRY", "LOCAL_KG_WATCHER")

    persistence_runtime = build_persistence_runtime(
        data_dir=ctx.DATA_DIR,
        base_dir=ctx.BASE_DIR,
        enable_graph=ctx.ENABLE_GRAPH,
        knowledge_graph=ctx.KNOWLEDGE_GRAPH,
        hooks_registry=ctx.HOOKS_REGISTRY,
        history_file=ctx.HISTORY_FILE,
        conversations=ctx.CONVERSATIONS,
        user_id_for_email=ctx.user_id_for_email,
        audit=lambda action, detail, user: ctx.append_audit_event(
            action, user_email=user, **detail
        ),
    )
    ctx.adopt(
        persistence_runtime,
        "REALTIME_BUS",
        "WORKSPACE_OS",
        "WORKSPACE_SERVICE",
        "INVITATION_STORE",
        "PLUGIN_REGISTRY",
        "TEMPLATE_CATALOG",
        "AGENT_REGISTRY",
        "MEMORY_SERVICE",
        "BRAIN_INTELLIGENCE",
        "AUTOMATION_INTELLIGENCE",
        "INGESTION_PIPELINE",
        "DEVICE_IDENTITY",
        "KG_PORTABILITY",
        "FUNNEL_METRICS",
    )

    # History reads/clears. The write path (save_to_history above) stays a
    # closure because it is bound to redaction/audit/ingestion.
    history_query_runtime = build_history_query_runtime(
        conversations=ctx.CONVERSATIONS,
        workspace_service=ctx.WORKSPACE_SERVICE,
        require_auth=ctx.REQUIRE_AUTH,
        logging=logging,
    )
    ctx.adopt(
        history_query_runtime,
        "_history_allowed_workspaces_for",
        "_history_include_legacy_global",
        "get_history",
        "conversation_title",
        "group_history_conversations",
        "get_conversation_messages",
        "clear_history",
        "clear_conversation",
    )


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


# ── phase 7: platform features ───────────────────────────────────────────────
def phase_platform_features(ctx: RuntimeContext) -> None:
    """Workspace platform, automation, review queue, command centre, proposals."""
    ctx.enter("platform_features")

    from latticeai.api.agents import create_agents_router
    from latticeai.api.automation_intelligence import (
        create_automation_intelligence_router,
    )
    from latticeai.api.change_proposals import create_change_proposals_router
    from latticeai.api.command_center import create_command_center_router
    from latticeai.api.evidence_actions import create_evidence_actions_router
    from latticeai.api.funnel_metrics import create_funnel_metrics_router
    from latticeai.api.marketplace import create_marketplace_router
    from latticeai.api.plugins import create_plugins_router
    from latticeai.api.project_sessions import create_project_sessions_router
    from latticeai.api.realtime import create_realtime_router
    from latticeai.api.voice_capture import create_voice_capture_router
    from latticeai.api.workflow_designer import create_workflow_designer_router
    from latticeai.api.workspace import _workspace_scope_from_request
    from latticeai.core.project_sessions import ProjectSessionStore
    from latticeai.runtime.hooks_runtime import (
        bind_builtin_hook_runners,
        bind_trigger_hook_runner,
    )
    from latticeai.runtime.platform_runtime_wiring import (
        build_platform_automation_runtime,
    )
    from latticeai.runtime.router_registration import (
        register_platform_feature_routers,
    )
    from latticeai.services.change_proposals import ChangeProposalService
    from latticeai.services.command_center import CommandCenterService
    from latticeai.services.evidence_actions import EvidenceActionService
    from latticeai.services.tool_dispatch import get_tool_permission
    from latticeai.services.voice_capture import VoiceCaptureService
    from latticeai.tools import resolve_workspace_path

    # v2 Agentic Workspace Platform: cross-system wiring.
    platform_automation_runtime = build_platform_automation_runtime(
        model_router=ctx.model_router,
        workspace_store=ctx.WORKSPACE_OS,
        workspace_service=ctx.WORKSPACE_SERVICE,
        plugin_registry=ctx.PLUGIN_REGISTRY,
        get_current_user=ctx.get_current_user,
        workspace_graph=ctx._workspace_graph,
        workspace_scope_from_request=_workspace_scope_from_request,
        get_tool_permission=get_tool_permission,
        hooks=ctx.HOOKS_REGISTRY,
        agent_registry=ctx.AGENT_REGISTRY,
        data_dir=ctx.DATA_DIR,
        append_audit_event=ctx.append_audit_event,
        memory_service=ctx.MEMORY_SERVICE,
        tz_name=getattr(ctx.CONFIG, "timezone", None),
    )
    ctx.adopt(
        platform_automation_runtime,
        "_llm_generate_sync",
        "PLATFORM",
        "_automation_runtime",
        "REVIEW_QUEUE",
        "TRIGGER_SERVICE",
        "AGENT_RUNTIME",
        "RUN_EXECUTOR",
    )
    bind_trigger_hook_runner(
        registry=ctx.HOOKS_REGISTRY, trigger_service=ctx.TRIGGER_SERVICE
    )
    ctx.app.state.run_executor = ctx.RUN_EXECUTOR
    ctx.app.state.run_reconciliation = ctx.RUN_EXECUTOR.reconcile_startup()
    ctx.TRIGGER_SERVICE.start()

    bind_builtin_hook_runners(
        registry=ctx.HOOKS_REGISTRY,
        append_audit_event=ctx.append_audit_event,
        get_tool_permission=get_tool_permission,
        classify_sensitive_message=ctx.classify_sensitive_message,
    )

    register_platform_feature_routers(
        ctx.app,
        create_plugins_router=create_plugins_router,
        plugin_registry=ctx.PLUGIN_REGISTRY,
        require_user=ctx.require_user,
        require_admin=ctx.require_admin,
        append_audit_event=ctx.append_audit_event,
        platform=ctx.PLATFORM,
        ui_file_response=ctx.ui_file_response,
        static_dir=ctx.STATIC_DIR,
        create_workflow_designer_router=create_workflow_designer_router,
        store=ctx.WORKSPACE_OS,
        get_current_user=ctx.get_current_user,
        workspace_graph=ctx._workspace_graph,
        hooks=ctx.HOOKS_REGISTRY,
        run_executor=ctx.RUN_EXECUTOR,
        trigger_service=ctx.TRIGGER_SERVICE,
        create_agents_router=create_agents_router,
        agent_runtime=ctx.AGENT_RUNTIME,
        create_marketplace_router=create_marketplace_router,
        template_catalog=ctx.TEMPLATE_CATALOG,
        create_realtime_router=create_realtime_router,
        realtime_bus=ctx.REALTIME_BUS,
    )

    ctx.app.include_router(
        create_automation_intelligence_router(
            service=ctx.AUTOMATION_INTELLIGENCE,
            store=ctx.WORKSPACE_OS,
            require_user=ctx.require_user,
            gate_read=ctx.PLATFORM.gate_read,
            gate_write=ctx.PLATFORM.gate_write,
            append_audit_event=ctx.append_audit_event,
            workspace_graph=ctx._workspace_graph,
            run_executor=ctx.RUN_EXECUTOR,
            review_queue=ctx.REVIEW_QUEUE,
        )
    )
    # UX funnel metrics (backlog #16): admin-only runtime counters.
    ctx.app.include_router(
        create_funnel_metrics_router(
            service=ctx.FUNNEL_METRICS,
            require_admin=ctx.require_admin,
        )
    )

    ctx.set(
        COMMAND_CENTER=CommandCenterService(
            conversation_store=ctx.CONVERSATIONS,
            knowledge_graph=ctx.KNOWLEDGE_GRAPH,
            store=ctx.WORKSPACE_OS,
            search_service=ctx.SEARCH_SERVICE,
            brain_intelligence=ctx.BRAIN_INTELLIGENCE,
            automation_intelligence=ctx.AUTOMATION_INTELLIGENCE,
            review_queue=ctx.REVIEW_QUEUE,
            enable_graph=ctx.ENABLE_GRAPH,
        )
    )
    ctx.app.include_router(
        create_command_center_router(
            service=ctx.COMMAND_CENTER,
            require_user=ctx.require_user,
            gate_read=ctx.PLATFORM.gate_read,
        )
    )

    ctx.set(
        CHANGE_PROPOSALS=ChangeProposalService(
            review_queue=ctx.REVIEW_QUEUE,
            resolve_path=resolve_workspace_path,
            audit=ctx.append_audit_event,
        )
    )
    # Proposal-first mutations: the agent loop consults the governor so
    # additive creates run with minimal friction while changes and deletions
    # of existing files are staged for review instead of applied.
    ctx.CHAT_AGENT_RUNTIME.deps.change_governor = ctx.CHANGE_PROPOSALS
    ctx.app.include_router(
        create_change_proposals_router(
            service=ctx.CHANGE_PROPOSALS,
            require_user=ctx.require_user,
            gate_read=ctx.PLATFORM.gate_read,
            gate_write=ctx.PLATFORM.gate_write,
        )
    )

    # Evidence → action (v9.9.6): an answer's citations become ready-to-send,
    # evidence-scoped follow-ups. Deterministic composition only — execution
    # stays on the chat/file-generation path.
    ctx.set(
        EVIDENCE_ACTIONS_SERVICE=EvidenceActionService(
            node_reader=getattr(ctx.KNOWLEDGE_GRAPH, "get_node", None)
        )
    )
    ctx.app.include_router(
        create_evidence_actions_router(
            service=ctx.EVIDENCE_ACTIONS_SERVICE,
            require_user=ctx.require_user,
            allowed_workspaces_for=ctx._allowed_workspaces_for,
        )
    )

    # Voice memo capture (v9.9.7): the shortest path from a thought to the
    # Brain. Transcription is an optional local port — absent, the memo is
    # still stored and the response says it is not searchable.
    ctx.set(
        VOICE_CAPTURE=VoiceCaptureService(
            pipeline=ctx.INGESTION_PIPELINE if ctx.ENABLE_GRAPH else None,
            transcriber=None,
        )
    )
    ctx.app.include_router(
        create_voice_capture_router(
            service=ctx.VOICE_CAPTURE,
            require_user=ctx.require_user,
            gate_write=ctx.PLATFORM.gate_write,
            append_audit_event=ctx.append_audit_event,
        )
    )

    # Multi-turn project loop (v9.9.6): work spanning several runs keeps its
    # files, open TODOs, and last honest verification in one project session.
    ctx.set(PROJECT_SESSIONS=ProjectSessionStore(ctx.DATA_DIR / "project_sessions"))
    ctx.app.include_router(
        create_project_sessions_router(
            store=ctx.PROJECT_SESSIONS,
            require_user=ctx.require_user,
            gate_read=ctx.PLATFORM.gate_read,
            gate_write=ctx.PLATFORM.gate_write,
        )
    )


# ── phase 8: interaction routers ─────────────────────────────────────────────
def phase_interaction(ctx: RuntimeContext) -> None:
    """Model, chat, search, tools, hooks, memory and brain tail routers."""
    ctx.enter("interaction")

    from fastapi import HTTPException

    from latticeai.api.agent_registry import create_agent_registry_router
    from latticeai.api.brain_intelligence import create_brain_intelligence_router
    from latticeai.api.browser import create_browser_router
    from latticeai.api.chat import create_chat_router
    from latticeai.api.garden import create_garden_router
    from latticeai.api.health import create_health_router
    from latticeai.api.hooks import create_hooks_router
    from latticeai.api.memory import create_memory_router
    from latticeai.api.models import create_models_router
    from latticeai.api.network import create_network_router
    from latticeai.api.portability import create_portability_router
    from latticeai.api.review_queue import create_review_queue_router
    from latticeai.api.search import create_search_router
    from latticeai.api.setup import create_setup_router
    from latticeai.api.tools import create_tools_router
    from latticeai.core.model_compat import (
        list_cached_profiles as _list_compat_profiles,
    )
    from latticeai.runtime.chat_wiring import build_interaction_contexts
    from latticeai.runtime.model_wiring import register_model_runtime_routers
    from latticeai.runtime.platform_services_runtime import build_brain_network
    from latticeai.runtime.review_wiring import build_review_run_now_runner
    from latticeai.runtime.router_registration import (
        register_health_and_model_routers,
        register_interaction_routers,
        register_review_and_brain_tail_routers,
    )
    from latticeai.services.model_runtime import (
        CLOUD_VERIFY_TTL_SECONDS,
        ENGINE_MODEL_CATALOG,
        MODEL_ENGINE_ALIASES,
        download_hf_model,
        ensure_ollama_server,
        filter_lower_family_versions,
        local_binary,
        normalize_local_model_request,
        sse_event,
    )

    service = ctx.model_runtime_service
    ctx.set(
        model_runtime=register_model_runtime_routers(
            app=ctx.app,
            create_health_router=create_health_router,
            create_models_router=create_models_router,
            register_health_and_model_routers=register_health_and_model_routers,
            model_router=ctx.model_router,
            runtime_service=service,
            runtime_features=service.runtime_features,
            is_public_mode=ctx.IS_PUBLIC_MODE,
            engine_status=service.engine_status,
            get_current_user=ctx.get_current_user,
            require_auth=ctx.REQUIRE_AUTH,
            app_version=ctx.APP_VERSION,
            app_mode=ctx.APP_MODE,
            require_user=ctx.require_user,
            require_admin=ctx.require_admin,
            load_users=ctx.load_users,
            get_user_role=ctx.get_user_role,
            install_engine=service.install_engine,
            verify_cloud_models=service.verify_cloud_models,
            normalize_local_model_request=normalize_local_model_request,
            download_hf_model=download_hf_model,
            prepare_and_load_model=service.prepare_and_load_model,
            prepare_and_load_model_stream=service.prepare_and_load_model_stream,
            sse_event=sse_event,
            ensure_ollama_server=ensure_ollama_server,
            local_binary=local_binary,
            filter_lower_family_versions=filter_lower_family_versions,
            list_compat_profiles=_list_compat_profiles,
            set_user_api_key=ctx.set_user_api_key,
            engine_model_catalog=ENGINE_MODEL_CATALOG,
            model_engine_aliases=MODEL_ENGINE_ALIASES,
            cloud_verify_ttl_seconds=CLOUD_VERIFY_TTL_SECONDS,
            allow_local_models=ctx.ALLOW_LOCAL_MODELS,
        )
    )

    _tool_router_context, interaction_router_context = build_interaction_contexts(
        config=ctx.CONFIG,
        ingestion_pipeline=ctx.INGESTION_PIPELINE,
        data_dir=ctx.DATA_DIR,
        static_dir=ctx.STATIC_DIR,
        model_router=ctx.model_router,
        require_user=ctx.require_user,
        require_admin=ctx.require_admin,
        get_current_user=ctx.get_current_user,
        clear_history=ctx.clear_history,
        append_audit_event=ctx.append_audit_event,
        enforce_rate_limit=ctx.enforce_rate_limit,
        bytes_match_extension=ctx._bytes_match_extension,
        classify_sensitive_message=ctx.classify_sensitive_message,
        save_to_history=ctx.save_to_history,
        enable_graph=ctx.ENABLE_GRAPH,
        knowledge_graph=ctx.KNOWLEDGE_GRAPH,
        require_graph=ctx._require_graph,
        local_kg_watcher=ctx.LOCAL_KG_WATCHER,
        load_mcp_installs=ctx.load_mcp_installs,
        recommend_mcps=ctx.recommend_mcps,
        install_mcp=ctx.install_mcp,
        mcp_public_item=ctx.mcp_public_item,
        hooks=ctx.HOOKS_REGISTRY,
        workspace_service=ctx.WORKSPACE_SERVICE,
        chat_context=ctx.app_context,
        search_service=ctx.SEARCH_SERVICE,
        allowed_workspaces_for=ctx._allowed_workspaces_for,
        embedding_info=ctx._embedding_info,
        agent_registry=ctx.AGENT_REGISTRY,
        memory_service=ctx.MEMORY_SERVICE,
        platform=ctx.PLATFORM,
        active_model_getter=lambda: ctx.model_router.current_model_id or "",
        brain_intelligence=ctx.BRAIN_INTELLIGENCE,
    )
    register_interaction_routers(
        ctx.app,
        interaction_context=interaction_router_context,
        create_chat_router=create_chat_router,
        create_search_router=create_search_router,
        create_tools_router=create_tools_router,
        create_hooks_router=create_hooks_router,
        create_agent_registry_router=create_agent_registry_router,
        create_memory_router=create_memory_router,
        create_brain_intelligence_router=create_brain_intelligence_router,
    )

    register_review_and_brain_tail_routers(
        ctx.app,
        create_review_queue_router=create_review_queue_router,
        review_queue=ctx.REVIEW_QUEUE,
        require_user=ctx.require_user,
        gate_read=ctx.PLATFORM.gate_read,
        gate_write=ctx.PLATFORM.gate_write,
        run_review_item=build_review_run_now_runner(ctx.PLATFORM, HTTPException),
        append_audit_event=ctx.append_audit_event,
        # Approving a change_proposal from the Review Center applies the
        # staged content through the same service the agent governor uses.
        change_proposals=ctx.CHANGE_PROPOSALS,
        create_browser_router=create_browser_router,
        ingestion_pipeline=ctx.INGESTION_PIPELINE,
        workspace_service=ctx.WORKSPACE_SERVICE,
        create_portability_router=create_portability_router,
        kg_portability=ctx.KG_PORTABILITY,
        require_admin=ctx.require_admin,
        build_brain_network=build_brain_network,
        device_identity=ctx.DEVICE_IDENTITY,
        data_dir=ctx.DATA_DIR,
        create_network_router=create_network_router,
        create_garden_router=create_garden_router,
        gardener=ctx.gardener,
        create_setup_router=create_setup_router,
        model_router=ctx.model_router,
        knowledge_graph=ctx.KNOWLEDGE_GRAPH,
    )


#: The build order. Exported so the ordering test reads the same list the
#: factory runs, rather than a copy that can drift.
BUILD_PHASES = (
    phase_platform,
    phase_config,
    phase_identity,
    phase_brain,
    phase_domain,
    phase_web,
    phase_services,
    phase_foundation_routes,
    phase_platform_features,
    phase_interaction,
)


__all__ = [
    "BUILD_PHASES",
    "phase_brain",
    "phase_domain",
    "phase_config",
    "phase_foundation_routes",
    "phase_identity",
    "phase_interaction",
    "phase_platform",
    "phase_platform_features",
    "phase_services",
    "phase_web",
]
