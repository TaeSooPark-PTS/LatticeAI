"""Phases 1-4: platform, configuration, identity, brain.

The first half of the build order — everything that must exist before a
FastAPI application object is worth creating. Split out of the single
``build_phases`` module in v11.3.0; the package ``__init__`` still owns the
:data:`~latticeai.runtime.build_phases.BUILD_PHASES` order and re-exports every
phase under its historical name.

Every heavy import lives *inside* a phase, never at module scope
(``tests/unit/test_runtime_context.py`` enforces that for each submodule).
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
        "CSRF_TRUSTED_ORIGINS",
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
        "MULTIMODAL_PORTS",
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

