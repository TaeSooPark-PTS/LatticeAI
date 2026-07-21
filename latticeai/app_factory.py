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
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from latticeai.runtime.access_runtime import build_access_runtime
from latticeai.runtime.bootstrap import build_session_runtime
from latticeai.runtime.brain_runtime import build_brain_runtime
from latticeai.runtime.chat_wiring import (
    build_chat_agent_runtime_from_context,
    build_interaction_contexts,
    maybe_build_telegram_chat_mirror,
)
from latticeai.runtime.config_runtime import build_config_runtime
from latticeai.runtime.context_runtime import build_context_runtime
from latticeai.runtime.hooks_runtime import (
    bind_builtin_hook_runners,
    bind_trigger_hook_runner,
    build_hooks_runtime,
)
from latticeai.runtime.history_runtime import build_history_query_runtime
from latticeai.runtime.lifespan_runtime import build_lifespan_runtime
from latticeai.runtime.network_config_runtime import build_vpc_runtime
from latticeai.runtime.model_wiring import (
    configure_model_runtime_from_context,
    register_model_runtime_routers,
)
from latticeai.runtime.namespace_runtime import RuntimeBundle, build_runtime_namespace
from latticeai.runtime.platform_services_runtime import (
    build_brain_network,
)
from latticeai.runtime.platform_runtime_wiring import build_platform_automation_runtime
from latticeai.runtime.persistence_runtime import build_persistence_runtime
from latticeai.runtime.review_wiring import build_review_run_now_runner
from latticeai.runtime.sso_config_runtime import build_sso_config_runtime
from latticeai.runtime.audit_runtime import build_audit_runtime
from latticeai.runtime.router_registration import (
    build_auth_admin_security_router_bundle,
    build_router_bundle,
    build_static_routes_bundle,
    register_health_and_model_routers,
    register_foundation_routers,
    register_interaction_routers,
    register_platform_feature_routers,
    register_review_and_brain_tail_routers,
)
from latticeai.runtime.security_runtime import build_security_runtime
from latticeai.runtime.user_key_runtime import build_user_key_runtime
from latticeai.runtime.web_runtime import build_web_runtime

if TYPE_CHECKING:  # imports for annotations only — keep module import light
    from fastapi import FastAPI

    from latticeai.core.config import Config


def _build(config: "Optional[Config]" = None) -> Dict[str, Any]:
    """The legacy ``server_app`` assembly, moved verbatim into function scope.

    Heavy imports (mlx, the LLM router, knowledge graph, MCP registry, …) are
    deliberately *inside* this function so that importing the module performs
    no GPU init, no singleton construction, and no filesystem writes.
    """
    import logging
    import os
    import threading
    from pathlib import Path

    try:
        import mlx.core as mx
        mx.set_default_device(mx.gpu)
        print("✅ MLX Metal context initialized in main thread.")
    except Exception as e:
        print(f"⚠️ MLX Metal context unavailable: {e}")
        mx = None
    import uvicorn
    from fastapi import HTTPException, Request
    from pydantic import BaseModel

    from latticeai.models.router import LLMRouter, normalize_branding
    from lattice_brain.graph.runtime import set_llm_router
    from lattice_brain.graph.schema import set_embed_dim
    from latticeai.core.security import (
        hash_password,
        verify_password,
        host_is_loopback as _host_is_loopback_impl,
        client_ip as _client_ip_impl,
        bytes_match_extension as _bytes_match_extension_impl,
        redact_secret_text as _redact_secret_text,
        check_ip_rate_limit as _check_ip_rate_limit,
        enforce_rate_limit as _enforce_rate_limit,
    )
    from latticeai.core.audit import (
        get_audit_log as _get_audit_log,  # noqa: F401 - explicit legacy server_app export
        classify_sensitive_message as _classify_sensitive_message,
        build_sensitivity_report as _build_sensitivity_report,
        build_admin_audit_report as _build_admin_audit_report,
    )
    from latticeai.api.auth import create_auth_router
    from latticeai.api.admin import create_admin_router
    from latticeai.api.security_dashboard import create_security_router as _create_security_router
    from latticeai.core.model_compat import list_cached_profiles as _list_compat_profiles
    from latticeai.core.workspace_os import (
        WORKSPACE_OS_VERSION,
        remove_skill_directory,
    )
    from latticeai.core.enterprise import (
        capability_registry,
    )
    from latticeai.core.policy import policy_matrix
    from latticeai.core.users import (
        ensure_user_identity,
        load_users_file,
        migrate_knowledge_graph_identity,
        save_users_file,
        user_id_for_email as _user_id_for_email,
    )
    from latticeai.services.chat_service import ChatService
    from latticeai.services.app_context import AppContext
    from latticeai.core.embedding_providers import resolve_embedder, resolve_embedding_profile
    from latticeai.services.model_runtime import (
        CLOUD_VERIFY_TTL_SECONDS,
        ENGINE_MODEL_CATALOG,
        LOCAL_SERVER_PROCESSES,
        MODEL_ENGINE_ALIASES,
        build_model_runtime,
        download_hf_model,
        filter_lower_family_versions,
        local_binary,
        normalize_local_model_request,
        sse_event,
        ensure_ollama_server,
    )
    from latticeai.api.workspace import create_workspace_router, _workspace_scope_from_request
    from latticeai.api.health import create_health_router
    # ── v2 Agentic Workspace Platform layers ─────────────────────────────────────
    from latticeai.api.plugins import create_plugins_router
    from latticeai.api.workflow_designer import create_workflow_designer_router
    from latticeai.api.agents import create_agents_router
    from latticeai.api.realtime import create_realtime_router
    from latticeai.api.invitations import create_invitations_router
    from latticeai.api.marketplace import create_marketplace_router
    from latticeai.api.models import create_models_router
    from latticeai.api.chat import build_recent_chat_context, create_chat_router
    from latticeai.api.search import create_search_router
    from latticeai.api.tools import create_tools_router
    from latticeai.api.static_routes import create_static_routes_router
    from latticeai.api.garden import create_garden_router
    from latticeai.api.setup import create_setup_router
    from latticeai.api.hooks import create_hooks_router
    from latticeai.core.product_hardening import build_product_hardening_status
    from latticeai.api.agent_registry import create_agent_registry_router
    from latticeai.api.automation_intelligence import create_automation_intelligence_router
    from latticeai.api.brain_intelligence import create_brain_intelligence_router
    from latticeai.api.command_center import create_command_center_router
    from latticeai.services.command_center import CommandCenterService
    from latticeai.api.change_proposals import create_change_proposals_router
    from latticeai.services.change_proposals import ChangeProposalService
    from latticeai.tools import resolve_workspace_path
    from latticeai.api.memory import create_memory_router
    from latticeai.api.browser import create_browser_router
    from latticeai.api.portability import create_portability_router
    from lattice_brain.ingestion import IngestionItem
    from lattice_brain.storage import storage_from_env
    from latticeai.api.network import create_network_router
    # The aliased names below form the explicit, allowlisted compatibility
    # surface consumed by historical ``server_app`` callers.
    from latticeai.services.tool_dispatch import (  # noqa: F401
        LOCAL_WRITE_BLOCKED_PREFIXES as _LOCAL_WRITE_BLOCKED_PREFIXES,
        TOOL_GOVERNANCE,
        TOOL_GOVERNANCE_DEFAULT as _TOOL_GOVERNANCE_DEFAULT,
        agent_risk as _agent_risk,
        check_tool_role as _check_tool_role,
        configure_tool_dispatch,
        get_tool_permission,
        list_tool_permissions,
        build_agent_runtime,
        tool_response as _tool_response,
    )
    from latticeai.core.tool_registry import TOOL_CATALOG_BRIEF as _TOOL_CATALOG_BRIEF  # noqa: F401
    from latticeai.core.mcp_registry import (
        _fetch_skills_marketplace,
        install_skill,
        SKILLS_DIR,
        create_mcp_install_state,
    )
    from latticeai.services.p_reinforce import PReinforceGardener
    from latticeai.setup.wizard import get_recommendations, scan_environment
    from latticeai.tools import ensure_agent_root, execute_tool, knowledge_save

    try:
        import keyring
    except Exception:
        keyring = None

    from datetime import datetime

    # ── App-level config — parsed once, in one place (latticeai.core.config) ──────
    # The module-level names below are kept as a compatibility surface for the rest
    # of server.py; all of them are now derived from a single CONFIG instance.
    _config_runtime = build_config_runtime(config)
    CONFIG = _config_runtime["CONFIG"]
    APP_VERSION = WORKSPACE_OS_VERSION

    # Forwarded headers (X-Forwarded-For / CF-Connecting-IP) are only honoured for
    # IP rate limiting when the direct peer is one of these trusted proxies. Empty by
    # default (local-first): the peer address is used and client-supplied headers are
    # ignored, so per-IP rate limits cannot be spoofed.
    APP_MODE = _config_runtime["APP_MODE"]
    IS_PUBLIC_MODE = _config_runtime["IS_PUBLIC_MODE"]
    DEFAULT_HOST = _config_runtime["DEFAULT_HOST"]
    DEFAULT_PORT = _config_runtime["DEFAULT_PORT"]
    def _host_is_loopback(host: str) -> bool:
        return _host_is_loopback_impl(host)

    ENABLE_TELEGRAM = _config_runtime["ENABLE_TELEGRAM"]
    ENABLE_GRAPH    = _config_runtime["ENABLE_GRAPH"]
    AUTOLOAD_MODELS = _config_runtime["AUTOLOAD_MODELS"]
    MODEL_IDLE_UNLOAD_SECONDS = _config_runtime["MODEL_IDLE_UNLOAD_SECONDS"]
    ALLOW_MODEL_DOWNLOADS = _config_runtime["ALLOW_MODEL_DOWNLOADS"]
    MODEL_DOWNLOAD_TIMEOUT = _config_runtime["MODEL_DOWNLOAD_TIMEOUT"]
    ALLOW_LOCAL_MODELS = _config_runtime["ALLOW_LOCAL_MODELS"]
    REQUIRE_AUTH = _config_runtime["REQUIRE_AUTH"]
    ALLOW_PLAINTEXT_API_KEYS = _config_runtime["ALLOW_PLAINTEXT_API_KEYS"]
    CORS_ALLOW_NETWORK = _config_runtime["CORS_ALLOW_NETWORK"]
    CORS_EXTRA_ORIGINS = _config_runtime["CORS_EXTRA_ORIGINS"]
    PUBLIC_MODEL = _config_runtime["PUBLIC_MODEL"]
    LOCAL_MODEL = _config_runtime["LOCAL_MODEL"]
    LOCAL_DRAFT_MODEL = _config_runtime["LOCAL_DRAFT_MODEL"]

    _security_runtime = build_security_runtime(CONFIG)

    # ── SSO / OIDC config ─────────────────────────────────────────────────────────
    SSO_DISCOVERY_URL = _security_runtime["SSO_DISCOVERY_URL"]
    SSO_CLIENT_ID = _security_runtime["SSO_CLIENT_ID"]
    SSO_CLIENT_SECRET = _security_runtime["SSO_CLIENT_SECRET"]
    SSO_REDIRECT_URI = _security_runtime["SSO_REDIRECT_URI"]
    SSO_PROVIDER_NAME = _security_runtime["SSO_PROVIDER_NAME"]
    INVITE_CODE = _security_runtime["INVITE_CODE"]
    INVITE_COOKIE_SECRET = _security_runtime["INVITE_COOKIE_SECRET"]
    INVITE_GATE_ENABLED = _security_runtime["INVITE_GATE_ENABLED"]
    SECURE_COOKIES = _security_runtime["SECURE_COOKIES"]

    # SSO config + discovery seam is built below, after SSO_FILE is known
    # (latticeai.runtime.sso_config_runtime).

    # ── Password hashing — used directly from latticeai.core.security ──────────────
    # (hash_password / verify_password are imported above; no local wrapper needed)
    def verify_and_migrate_password(email: str, plain: str, stored: str, users: Dict) -> bool:
        """평문 비밀번호를 투명하게 해시로 마이그레이션. 마이그레이션 발생 시 audit log 남김."""
        if ":" in stored and len(stored) > 64:
            return verify_password(plain, stored)
        if plain == stored:
            users[email]["password"] = hash_password(plain)
            save_users(users)
            try:
                append_audit_event("password_migrated_from_plaintext", user_email=email)
            except Exception as e:
                logging.warning("audit log failed on password migration: %s", e)
            logging.info("Migrated plaintext password to scrypt hash for %s", email)
            return True
        return False

    # ── Session store — delegated to latticeai.runtime.bootstrap ──────────────────
    def _check_rate_limit(ip: str, action: str, max_calls: int, window_secs: float) -> None:
        _check_ip_rate_limit(ip, action, max_calls=max_calls, window_secs=window_secs)

    def _client_ip(request: Request) -> str:
        return _client_ip_impl(request)

    def user_id_for_email(email: Optional[str]) -> Optional[str]:
        return _user_id_for_email(load_users(), email)

    # Session token lifecycle (store + create/get/invalidate closures) lives in
    # the bootstrap seam; user_id_for_email is injected as the subject resolver.
    _session_runtime = build_session_runtime(user_id_resolver=user_id_for_email)
    _SESSION_TTL = _session_runtime["_SESSION_TTL"]
    _session_store = _session_runtime["_session_store"]
    create_session = _session_runtime["create_session"]
    get_session_email = _session_runtime["get_session_email"]
    invalidate_session = _session_runtime["invalidate_session"]

    # ── User Management Logic ──────────────────────────────────────────────────
    BASE_DIR = Path(__file__).resolve().parent.parent
    DATA_DIR = CONFIG.data_dir
    DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        DATA_DIR.chmod(0o700)
    except OSError:
        pass
    STATIC_DIR = CONFIG.static_dir

    USERS_FILE = DATA_DIR / "users.json"
    HISTORY_FILE = DATA_DIR / "chat_history.json"
    VPC_FILE = DATA_DIR / "vpc_config.json"
    AUDIT_FILE = DATA_DIR / "audit_log.json"
    SSO_FILE = DATA_DIR / "sso_config.json"

    # MCP state extracted to mcp_registry.create_mcp_install_state (server decomp)
    _mcp_state = create_mcp_install_state(DATA_DIR)
    load_mcp_installs = _mcp_state["load_mcp_installs"]
    mcp_public_item = _mcp_state["mcp_public_item"]
    recommend_mcps = _mcp_state["recommend_mcps"]
    install_mcp = _mcp_state["install_mcp"]

    # Resolve the configured embedding provider once at startup. Degrades to the
    # offline hash fallback when the requested provider is unavailable, while
    # recording the requested-vs-active provider for the Embeddings status surface.
    try:
        EMBEDDING_PROFILE = resolve_embedding_profile(CONFIG.embedding_profile)
    except ValueError as exc:
        logging.warning("Embedding profile ignored: %s", exc)
        EMBEDDING_PROFILE = {}
    _embedding_provider = CONFIG.embedding_provider
    _embedding_model = CONFIG.embedding_model or str(EMBEDDING_PROFILE.get("model") or "")
    _embedding_dim = CONFIG.embedding_dim or int(EMBEDDING_PROFILE.get("dimensions") or 0)
    if CONFIG.embedding_profile and CONFIG.embedding_provider in {"", "hash", "local", "fallback"}:
        _embedding_provider = str(EMBEDDING_PROFILE.get("provider") or CONFIG.embedding_provider)

    EMBEDDER = resolve_embedder(
        _embedding_provider,
        model=_embedding_model,
        base_url=CONFIG.embedding_base_url,
        api_key=CONFIG.embedding_api_key,
        dim=_embedding_dim,
        timeout=CONFIG.embedding_timeout,
        extra={"target": CONFIG.embedding_custom_target},
        probe=_embedding_provider not in {"", "hash", "local", "fallback"},
    )
    set_embed_dim(int(getattr(EMBEDDER, "dim", None) or _embedding_dim or 384))
    if EMBEDDER.fell_back:
        logging.warning("Embedding provider %s unavailable: %s", EMBEDDER.requested, EMBEDDER.detail)
    STORAGE_ENGINE = storage_from_env(os.environ, data_dir=DATA_DIR) if ENABLE_GRAPH else None
    _brain_runtime = build_brain_runtime(
        data_dir=DATA_DIR,
        history_file=HISTORY_FILE,
        enable_graph=ENABLE_GRAPH,
        embedder=EMBEDDER,
        storage_engine=STORAGE_ENGINE,
    )
    KNOWLEDGE_GRAPH = _brain_runtime["KNOWLEDGE_GRAPH"]
    # ── v4 durable conversation store: unbounded episodic memory in the same
    # SQLite file as the graph (kg_portability backup/restore covers it for
    # free). Legacy chat_history.json is imported once, idempotently, and the
    # file is left untouched on disk as the import source.
    CONVERSATIONS = _brain_runtime["CONVERSATIONS"]
    # Hooks registry is constructed here (ahead of the watcher) so folder-watch
    # reindexes can fire the pre_index/post_index lifecycle hooks. The registry
    # + watcher pair is assembled behind the hooks_runtime seam.
    _hooks_runtime = build_hooks_runtime(
        data_dir=DATA_DIR,
        enable_graph=ENABLE_GRAPH,
        knowledge_graph_getter=lambda: KNOWLEDGE_GRAPH,
    )
    HOOKS_REGISTRY = _hooks_runtime["HOOKS_REGISTRY"]
    LOCAL_KG_WATCHER = _hooks_runtime["LOCAL_KG_WATCHER"]
    # ── Persistence/service graph: workspace store, realtime feed, plugin/memory
    # registries, ingestion pipeline, device identity, and portability services.
    _persistence_runtime = build_persistence_runtime(
        data_dir=DATA_DIR,
        base_dir=BASE_DIR,
        enable_graph=ENABLE_GRAPH,
        knowledge_graph=KNOWLEDGE_GRAPH,
        hooks_registry=HOOKS_REGISTRY,
        history_file=HISTORY_FILE,
        conversations=CONVERSATIONS,
        user_id_for_email=user_id_for_email,
        audit=lambda action, detail, user: append_audit_event(action, user_email=user, **detail),
    )
    REALTIME_BUS = _persistence_runtime["REALTIME_BUS"]
    WORKSPACE_OS = _persistence_runtime["WORKSPACE_OS"]
    WORKSPACE_SERVICE = _persistence_runtime["WORKSPACE_SERVICE"]
    INVITATION_STORE = _persistence_runtime["INVITATION_STORE"]
    PLUGIN_REGISTRY = _persistence_runtime["PLUGIN_REGISTRY"]
    TEMPLATE_CATALOG = _persistence_runtime["TEMPLATE_CATALOG"]
    AGENT_REGISTRY = _persistence_runtime["AGENT_REGISTRY"]
    MEMORY_SERVICE = _persistence_runtime["MEMORY_SERVICE"]
    BRAIN_INTELLIGENCE = _persistence_runtime["BRAIN_INTELLIGENCE"]
    AUTOMATION_INTELLIGENCE = _persistence_runtime["AUTOMATION_INTELLIGENCE"]
    INGESTION_PIPELINE = _persistence_runtime["INGESTION_PIPELINE"]
    DEVICE_IDENTITY = _persistence_runtime["DEVICE_IDENTITY"]
    KG_PORTABILITY = _persistence_runtime["KG_PORTABILITY"]
    FUNNEL_METRICS = _persistence_runtime["FUNNEL_METRICS"]

    def _require_graph():
        if not ENABLE_GRAPH or KNOWLEDGE_GRAPH is None:
            raise HTTPException(status_code=404, detail="지식 그래프가 비활성화되어 있습니다. LATTICEAI_ENABLE_GRAPH=true 설정 후 다시 시도해 주세요.")

    class UserRegister(BaseModel):
        email: str
        password: str
        name: str
        nickname: str

    class UserLogin(BaseModel):
        email: str
        password: str

    class AdminUserUpdate(BaseModel):
        role: Optional[str] = None
        disabled: Optional[bool] = None

    class VpcConfigUpdate(BaseModel):
        provider: Optional[str] = None
        region: Optional[str] = None
        cidr_block: Optional[str] = None
        private_subnets: Optional[List[str]] = None
        endpoint: Optional[str] = None
        vpn_status: Optional[str] = None
        peering_status: Optional[str] = None
        notes: Optional[str] = None

    class SsoConfigUpdate(BaseModel):
        enabled: Optional[bool] = None
        provider_name: Optional[str] = None
        discovery_url: Optional[str] = None
        client_id: Optional[str] = None
        client_secret: Optional[str] = None
        redirect_uri: Optional[str] = None
        scopes: Optional[str] = None

    # SSO config + OIDC discovery seam. One closure owns config load/save and the
    # discovery cache, so save_sso_config now actually invalidates discovery.
    _sso_runtime = build_sso_config_runtime(
        sso_file=SSO_FILE,
        discovery_url=SSO_DISCOVERY_URL,
        client_id=SSO_CLIENT_ID,
        client_secret=SSO_CLIENT_SECRET,
        redirect_uri=SSO_REDIRECT_URI,
        provider_name=SSO_PROVIDER_NAME,
        logging=logging,
    )
    _sso_env_defaults = _sso_runtime["_sso_env_defaults"]
    get_sso_settings = _sso_runtime["get_sso_settings"]
    public_sso_config = _sso_runtime["public_sso_config"]
    save_sso_config = _sso_runtime["save_sso_config"]
    _get_sso_discovery = _sso_runtime["_get_sso_discovery"]
    _sso_states = _sso_runtime["_sso_states"]

    # MCP/skill request models moved to latticeai.api.mcp (v1.3.0).
    # VPC network-profile config → latticeai.runtime.network_config_runtime.
    _vpc_runtime = build_vpc_runtime(vpc_file=VPC_FILE, logging=logging)
    load_vpc_config = _vpc_runtime["load_vpc_config"]
    save_vpc_config = _vpc_runtime["save_vpc_config"]


    def load_users():
        users = load_users_file(USERS_FILE)
        email_to_id = {
            email: user.get("id")
            for email, user in users.items()
            if isinstance(user, dict) and user.get("id")
        }
        try:
            migrate_knowledge_graph_identity(DATA_DIR / "knowledge_graph.sqlite", email_to_id)
        except Exception as exc:
            logging.warning("knowledge graph identity migration skipped: %s", exc)
        try:
            WORKSPACE_OS.migrate_workspace_identities(email_to_id)
        except Exception as exc:
            logging.warning("workspace identity migration skipped: %s", exc)
        return users

    def save_users(users):
        save_users_file(USERS_FILE, users)


    _history_lock = threading.Lock()

    # audit build moved after redact_secret_text is defined (see below)

    def save_to_history(
        role: str,
        message: str,
        user_email: Optional[str] = None,
        user_nickname: Optional[str] = None,
        source: Optional[str] = None,
        conversation_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ):
        try:
            message = redact_secret_text(message)
            if role == "assistant":
                message = normalize_branding(message)
            item = {"role": role, "content": message, "timestamp": datetime.now().isoformat()}
            if user_email:
                item["user_email"] = user_email
            if user_nickname:
                item["user_nickname"] = user_nickname
            if source:
                item["source"] = source
            if conversation_id:
                item["conversation_id"] = conversation_id
            if workspace_id:
                item["workspace_id"] = workspace_id
            sensitive = classify_sensitive_message(item, -1)
            append_audit_event(
                "chat_message",
                role=role,
                user_email=user_email,
                user_nickname=user_nickname,
                source=source,
                conversation_id=conversation_id,
                workspace_id=workspace_id,
                content_preview=sensitive.get("preview"),
                content_chars=len(message or ""),
                sensitivity=sensitive.get("sensitivity"),
                sensitive_labels=sensitive.get("labels") or [],
            )
            # v4: conversations are durable episodic memory — unbounded SQLite
            # store (the 50-message chat_history.json cap is dead).
            CONVERSATIONS.append(item)
            try:
                if ENABLE_GRAPH and KNOWLEDGE_GRAPH:
                    # v4: chat messages enter the brain through the unified
                    # ingestion pipeline (provenance + hook lifecycle), not by
                    # bypassing it with a direct store call.
                    INGESTION_PIPELINE.ingest(
                        IngestionItem(
                            source_type="chat_message",
                            text=message,
                            owner=user_email,
                            workspace_id=workspace_id,
                            conversation_id=conversation_id,
                            metadata={
                                "role": role,
                                "user_nickname": user_nickname,
                                "source": source,
                                "raw": item,
                            },
                        ),
                        user_email=user_email,
                    )
            except Exception as graph_error:
                logging.warning("knowledge graph message ingest failed: %s", graph_error)
        except Exception as e:
            logging.warning("save_to_history failed: %s", e)

    def redact_secret_text(text: str) -> str:
        return _redact_secret_text(text)

    # Build audit now that redact is available
    _audit_rt = build_audit_runtime(
        audit_file=AUDIT_FILE,
        logging=logging,
        redact_fn=redact_secret_text,
    )
    get_audit_log = _audit_rt["get_audit_log"]
    append_audit_event = _audit_rt["append_audit_event"]

    # History query/clear seam (scope resolution, get_history, grouping, clears)
    # → latticeai.runtime.history_runtime. save_to_history (the write path) stays
    # inline above because it is bound to redaction/audit/ingestion.
    _history_query_runtime = build_history_query_runtime(
        conversations=CONVERSATIONS,
        workspace_service=WORKSPACE_SERVICE,
        require_auth=REQUIRE_AUTH,
        logging=logging,
    )
    _history_allowed_workspaces_for = _history_query_runtime["_history_allowed_workspaces_for"]
    _history_include_legacy_global = _history_query_runtime["_history_include_legacy_global"]
    get_history = _history_query_runtime["get_history"]
    conversation_title = _history_query_runtime["conversation_title"]
    group_history_conversations = _history_query_runtime["group_history_conversations"]
    get_conversation_messages = _history_query_runtime["get_conversation_messages"]
    clear_history = _history_query_runtime["clear_history"]
    clear_conversation = _history_query_runtime["clear_conversation"]

    _access_runtime = build_access_runtime(
        config=CONFIG,
        require_auth=REQUIRE_AUTH,
        http_exception=HTTPException,
        request_type=Request,
        load_users=load_users,
        get_session_email=get_session_email,
        user_id_for_email=_user_id_for_email,
    )
    get_user_role = _access_runtime["get_user_role"]
    _extract_bearer_token = _access_runtime["_extract_bearer_token"]
    get_current_user = _access_runtime["get_current_user"]
    require_user = _access_runtime["require_user"]
    require_admin = _access_runtime["require_admin"]
    public_user = _access_runtime["public_user"]


    # ── Rate limiting & file validation — delegated to latticeai.core.security ────
    _RATE_LIMIT_ENABLED = CONFIG.rate_limit_enabled

    def enforce_rate_limit(email: str, bucket_key: str) -> None:
        _enforce_rate_limit(email, bucket_key, enabled=_RATE_LIMIT_ENABLED)

    def _bytes_match_extension(data: bytes, ext: str) -> bool:
        return _bytes_match_extension_impl(data, ext)

    # Local file-access approval lives entirely in
    # ``latticeai.api.permissions.PermissionGateway`` (token hash-at-rest,
    # persisted approval queue, Discord notifications). The historical inline
    # copy that used to sit here was app-dead — no route referenced it — so it
    # was removed to keep this factory a single wiring path.

    _user_key_runtime = build_user_key_runtime(
        load_users=load_users,
        save_users=save_users,
        ensure_user_identity=ensure_user_identity,
        keyring=keyring,
        allow_plaintext_api_keys=ALLOW_PLAINTEXT_API_KEYS,
        logging=logging,
        http_exception=HTTPException,
    )
    get_history_user = _user_key_runtime["get_history_user"]
    get_user_api_key = _user_key_runtime["get_user_api_key"]
    set_user_api_key = _user_key_runtime["set_user_api_key"]

    # Chat service owns persistence and trace behavior after its user-key
    # dependencies are available.
    CHAT_SERVICE = ChatService(
        store=WORKSPACE_OS,
        get_history=get_history,
        save_to_history=save_to_history,
        get_history_user=get_history_user,
    )

    # ── Sensitivity analysis — delegated to latticeai.core.audit ──────────────────
    def classify_sensitive_message(item: Dict, index: int) -> Dict:
        return _classify_sensitive_message(item, index)

    def build_sensitivity_report(history: List[Dict]) -> Dict:
        return _build_sensitivity_report(history)

    # ── Admin audit report — delegated to latticeai.core.audit ───────────────────
    def build_admin_audit_report(users: Dict, audit_events: Optional[List[Dict]] = None) -> Dict:
        graph_stats = None
        try:
            if ENABLE_GRAPH and KNOWLEDGE_GRAPH:
                graph_stats = KNOWLEDGE_GRAPH.stats()
        except Exception:
            pass
        return _build_admin_audit_report(
            AUDIT_FILE, users,
            get_user_role=get_user_role,
            graph_stats=graph_stats,
            audit_events=audit_events,
        )

    router = LLMRouter()
    set_llm_router(router)
    configure_tool_dispatch(load_users=load_users, get_user_role=get_user_role)
    # v4 garden absorption: the vault is the user-owned markdown mirror; the
    # brain is authoritative. Existing notes import idempotently at startup
    # (content-hash dedup — re-runs are no-ops), and garden context queries
    # the brain instead of rescanning the vault per chat message.
    gardener = PReinforceGardener(
        ingestion_pipeline=INGESTION_PIPELINE if ENABLE_GRAPH else None,
        knowledge_graph=KNOWLEDGE_GRAPH,
    )
    if ENABLE_GRAPH:
        try:
            _garden_import = gardener.import_vault()
            if _garden_import.get("failed"):
                logging.warning("garden vault import: %s notes failed to ingest", _garden_import["failed"])
        except Exception as exc:
            logging.warning("garden vault import skipped: %s", exc)

    _lifespan_runtime = build_lifespan_runtime(
        app_mode=APP_MODE,
        enable_telegram=ENABLE_TELEGRAM,
        autoload_models=AUTOLOAD_MODELS,
        is_public_mode=IS_PUBLIC_MODE,
        public_model=PUBLIC_MODEL,
        allow_local_models=ALLOW_LOCAL_MODELS,
        local_model=LOCAL_MODEL,
        local_draft_model=LOCAL_DRAFT_MODEL,
        model_idle_unload_seconds=MODEL_IDLE_UNLOAD_SECONDS,
        model_router=router,
        local_kg_watcher=LOCAL_KG_WATCHER,
        local_server_processes=LOCAL_SERVER_PROCESSES,
        logger=logging,
    )
    _spawn = _lifespan_runtime["_spawn"]
    lifespan = _lifespan_runtime["lifespan"]

    _web_runtime = build_web_runtime(
        app_mode=APP_MODE,
        app_version=APP_VERSION,
        lifespan=lifespan,
        default_host=DEFAULT_HOST,
        default_port=DEFAULT_PORT,
        cors_extra_origins=CORS_EXTRA_ORIGINS,
        cors_allow_network=CORS_ALLOW_NETWORK,
        static_dir=STATIC_DIR,
    )
    app = _web_runtime["app"]
    ensure_agent_root()

    OPEN_REGISTRATION = CONFIG.open_registration
    _model_runtime_service = configure_model_runtime_from_context(
        build_model_runtime=build_model_runtime,
        router=router,
        APP_MODE=APP_MODE,
        DEFAULT_HOST=DEFAULT_HOST,
        DEFAULT_PORT=DEFAULT_PORT,
        DATA_DIR=DATA_DIR,
        BASE_DIR=BASE_DIR,
        ENABLE_TELEGRAM=ENABLE_TELEGRAM,
        ENABLE_GRAPH=ENABLE_GRAPH,
        AUTOLOAD_MODELS=AUTOLOAD_MODELS,
        MODEL_IDLE_UNLOAD_SECONDS=MODEL_IDLE_UNLOAD_SECONDS,
        ALLOW_MODEL_DOWNLOADS=ALLOW_MODEL_DOWNLOADS,
        MODEL_DOWNLOAD_TIMEOUT=MODEL_DOWNLOAD_TIMEOUT,
        ALLOW_LOCAL_MODELS=ALLOW_LOCAL_MODELS,
        REQUIRE_AUTH=REQUIRE_AUTH,
        INVITE_GATE_ENABLED=INVITE_GATE_ENABLED,
        ALLOW_PLAINTEXT_API_KEYS=ALLOW_PLAINTEXT_API_KEYS,
        CORS_ALLOW_NETWORK=CORS_ALLOW_NETWORK,
        PUBLIC_MODEL=PUBLIC_MODEL,
        LOCAL_MODEL=LOCAL_MODEL,
        IS_PUBLIC_MODE=IS_PUBLIC_MODE,
        keyring=keyring,
        get_current_user=get_current_user,
        get_user_api_key=get_user_api_key,
    )
    _static_routes_bundle = build_static_routes_bundle(
        create_static_routes_router=create_static_routes_router,
        static_dir=STATIC_DIR,
        invite_gate_enabled=INVITE_GATE_ENABLED,
        invite_code=INVITE_CODE,
        invite_cookie_secret=INVITE_COOKIE_SECRET,
        secure_cookies=SECURE_COOKIES,
        app_mode=APP_MODE,
        model_router=router,
        require_user=require_user,
    )
    STATIC_ROUTES = _static_routes_bundle["STATIC_ROUTES"]
    ui_file_response = _static_routes_bundle["ui_file_response"]
    local_sysinfo = _static_routes_bundle["local_sysinfo"]
    invite_authorized = _static_routes_bundle["invite_authorized"]

    # ── Auth & Admin routers (latticeai.api) ─────────────────────────────────────
    _foundation_router_bundle = build_auth_admin_security_router_bundle(
        create_auth_router=create_auth_router,
        load_users=load_users,
        save_users=save_users,
        hash_password=hash_password,
        verify_and_migrate_password=verify_and_migrate_password,
        create_session=create_session,
        get_session_email=get_session_email,
        invalidate_session=invalidate_session,
        extract_bearer_token=_extract_bearer_token,
        get_user_role=get_user_role,
        require_user=require_user,
        check_ip_rate_limit=_check_rate_limit,
        client_ip=_client_ip,
        get_sso_settings=get_sso_settings,
        get_sso_discovery=_get_sso_discovery,
        public_sso_config=public_sso_config,
        open_registration=OPEN_REGISTRATION,
        session_ttl=_SESSION_TTL,
        require_auth=REQUIRE_AUTH,
        secure_cookies=SECURE_COOKIES,
        invite_authorized=invite_authorized,
        ensure_identity=ensure_user_identity,
        create_admin_router=create_admin_router,
        require_admin=require_admin,
        get_history=get_history,
        get_audit_log=get_audit_log,
        audit_file=AUDIT_FILE,
        public_user=public_user,
        load_vpc_config=load_vpc_config,
        save_vpc_config=save_vpc_config,
        build_admin_audit_report=build_admin_audit_report,
        build_sensitivity_report=build_sensitivity_report,
        append_audit_event=append_audit_event,
        save_sso_config=save_sso_config,
        knowledge_graph=KNOWLEDGE_GRAPH,
        enable_graph=ENABLE_GRAPH,
        logger=logging,
        invite_code=INVITE_CODE,
        invite_gate_enabled=INVITE_GATE_ENABLED,
        default_port=DEFAULT_PORT,
        policy_matrix=policy_matrix,
        build_product_hardening_status=build_product_hardening_status,
        config=CONFIG,
        kg_portability=KG_PORTABILITY,
        device_identity=DEVICE_IDENTITY,
        create_invitations_router=create_invitations_router,
        invitation_store=INVITATION_STORE,
        workspace_service=WORKSPACE_SERVICE,
        user_id_for_email=user_id_for_email,
        create_security_router=_create_security_router,
        classify_sensitive_message=classify_sensitive_message,
    )
    auth_router = _foundation_router_bundle["auth_router"]
    admin_router = _foundation_router_bundle["admin_router"]
    invitations_router = _foundation_router_bundle["invitations_router"]
    security_router = _foundation_router_bundle["security_router"]
    _graph_stats_safe = _foundation_router_bundle["_graph_stats_safe"]
    _product_hardening_status = _foundation_router_bundle["_product_hardening_status"]
    _security_audit_events_safe = _foundation_router_bundle["_security_audit_events_safe"]
    _security_list_uploaded_files = _foundation_router_bundle["_security_list_uploaded_files"]

    # ── Static UI/status routes moved to latticeai.api.static_routes ──

    # ── Request / Response Models ──────────────────────────────────────────────────

    # ── Workspace OS API ──────────────────────────────────────────────────────────

    def _workspace_settings_payload() -> Dict:
        return {
            "mode": APP_MODE,
            "host": DEFAULT_HOST,
            "port": DEFAULT_PORT,
            "require_auth": REQUIRE_AUTH,
            "enable_graph": ENABLE_GRAPH,
            "allow_local_models": ALLOW_LOCAL_MODELS,
            "static_dir": str(STATIC_DIR),
            "data_dir": str(DATA_DIR),
        }


    def _workspace_models_payload() -> Dict:
        return {
            "current_model": router.current_model_id,
            "loaded_models": router.loaded_model_ids,
            "public_model": PUBLIC_MODEL,
            "local_model": LOCAL_MODEL,
            "local_draft_model": LOCAL_DRAFT_MODEL,
        }


    def _workspace_graph():
        return KNOWLEDGE_GRAPH if (ENABLE_GRAPH and KNOWLEDGE_GRAPH) else None


    _context_runtime = build_context_runtime(
        graph_store=_workspace_graph(),
        ingestion_pipeline=INGESTION_PIPELINE,
        memory_service=MEMORY_SERVICE,
        gardener=gardener,
        require_auth=REQUIRE_AUTH,
        allowed_scopes_for_user=lambda user_email: PLATFORM.allowed_scopes(user_email),
    )
    SEARCH_SERVICE = _context_runtime["SEARCH_SERVICE"]
    BRAIN_MEMORY = _context_runtime["BRAIN_MEMORY"]
    CONTEXT_ASSEMBLER = _context_runtime["CONTEXT_ASSEMBLER"]
    _scoped_hybrid_search = _context_runtime["_scoped_hybrid_search"]


    # ── Telegram chat mirror: registered only when ENABLE_TELEGRAM is truthy.
    # latticeai.api.chat no longer imports telegram_bot (a 45KB module that
    # mutates os.environ at import); it calls this injected callback instead.
    on_chat_message = maybe_build_telegram_chat_mirror(
        enable_telegram=ENABLE_TELEGRAM,
        spawn=_spawn,
    )

    def _recent_chat_context(
        limit: int = 10,
        include_image_missing_replies: bool = True,
        user_email: Optional[str] = None,
        conversation_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> str:
        return build_recent_chat_context(
            get_history=get_history,
            limit=limit,
            include_image_missing_replies=include_image_missing_replies,
            user_email=user_email,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
        )

    CHAT_AGENT_RUNTIME = build_chat_agent_runtime_from_context(
        build_agent_runtime=build_agent_runtime,
        model_router=router,
        execute_tool=execute_tool,
        recent_chat_context=_recent_chat_context,
        clear_history=clear_history,
        knowledge_save=knowledge_save,
        audit=append_audit_event,
        hooks=HOOKS_REGISTRY,
        brain_memory=BRAIN_MEMORY,
    )

    # ── Typed dependency context (latticeai.services.app_context) ────────────────
    # One context object replaces the historical 25-30-kwarg router wiring.
    context = AppContext(
        config=CONFIG,
        data_dir=DATA_DIR,
        static_dir=STATIC_DIR,
        base_dir=BASE_DIR,
        skills_dir=SKILLS_DIR,
        model_router=router,
        workspace_store=WORKSPACE_OS,
        workspace_service=WORKSPACE_SERVICE,
        knowledge_graph=KNOWLEDGE_GRAPH,
        local_kg_watcher=LOCAL_KG_WATCHER,
        chat_service=CHAT_SERVICE,
        context_assembler=CONTEXT_ASSEMBLER,
        brain_memory=BRAIN_MEMORY,
        ingestion_pipeline=INGESTION_PIPELINE if ENABLE_GRAPH else None,
        chat_agent_runtime=CHAT_AGENT_RUNTIME,
        gardener=gardener,
        hooks=HOOKS_REGISTRY,
        realtime_bus=REALTIME_BUS,
        capability_registry=capability_registry,
        require_user=require_user,
        require_admin=require_admin,
        get_current_user=get_current_user,
        load_users=load_users,
        get_user_role=get_user_role,
        enforce_rate_limit=enforce_rate_limit,
        allowed_workspaces_for=_history_allowed_workspaces_for,
        append_audit_event=append_audit_event,
        get_audit_log=get_audit_log,
        get_history=get_history,
        get_history_user=get_history_user,
        save_to_history=save_to_history,
        clear_history=clear_history,
        clear_conversation=clear_conversation,
        group_history_conversations=group_history_conversations,
        get_conversation_messages=get_conversation_messages,
        conversation_title=conversation_title,
        enable_graph=ENABLE_GRAPH,
        require_graph=_require_graph,
        workspace_graph=_workspace_graph,
        graph_stats=_graph_stats_safe,
        workspace_models=_workspace_models_payload,
        workspace_settings=_workspace_settings_payload,
        scan_environment=scan_environment,
        local_sysinfo=local_sysinfo,
        get_recommendations=get_recommendations,
        fetch_skills_marketplace=_fetch_skills_marketplace,
        install_skill=install_skill,
        remove_skill_directory=remove_skill_directory,
        redact_secret_text=redact_secret_text,
        ui_file_response=ui_file_response,
        public_model=PUBLIC_MODEL,
        local_model=LOCAL_MODEL or "",
        on_chat_message=on_chat_message,
        funnel_metrics=FUNNEL_METRICS,
    )
    app.state.context = context

    register_foundation_routers(
        app,
        static_router=STATIC_ROUTES.router,
        auth_router=auth_router,
        admin_router=admin_router,
        invitations_router=invitations_router,
        security_router=security_router,
        create_workspace_router=create_workspace_router,
        context=context,
    )


    # ── v2 Agentic Workspace Platform: cross-system wiring ───────────────────────
    _platform_automation_runtime = build_platform_automation_runtime(
        model_router=router,
        workspace_store=WORKSPACE_OS,
        workspace_service=WORKSPACE_SERVICE,
        plugin_registry=PLUGIN_REGISTRY,
        get_current_user=get_current_user,
        workspace_graph=_workspace_graph,
        workspace_scope_from_request=_workspace_scope_from_request,
        get_tool_permission=get_tool_permission,
        hooks=HOOKS_REGISTRY,
        agent_registry=AGENT_REGISTRY,
        data_dir=DATA_DIR,
        append_audit_event=append_audit_event,
        memory_service=MEMORY_SERVICE,
        tz_name=getattr(CONFIG, "timezone", None),
    )
    _llm_generate_sync = _platform_automation_runtime["_llm_generate_sync"]
    PLATFORM = _platform_automation_runtime["PLATFORM"]
    _automation_runtime = _platform_automation_runtime["_automation_runtime"]
    REVIEW_QUEUE = _platform_automation_runtime["REVIEW_QUEUE"]
    TRIGGER_SERVICE = _platform_automation_runtime["TRIGGER_SERVICE"]
    AGENT_RUNTIME = _platform_automation_runtime["AGENT_RUNTIME"]
    RUN_EXECUTOR = _platform_automation_runtime["RUN_EXECUTOR"]
    bind_trigger_hook_runner(registry=HOOKS_REGISTRY, trigger_service=TRIGGER_SERVICE)
    app.state.run_executor = RUN_EXECUTOR
    app.state.run_reconciliation = RUN_EXECUTOR.reconcile_startup()
    TRIGGER_SERVICE.start()

    # ── Hooks dispatch: bind real built-in runners ───────────────────────────────
    bind_builtin_hook_runners(
        registry=HOOKS_REGISTRY,
        append_audit_event=append_audit_event,
        get_tool_permission=get_tool_permission,
        classify_sensitive_message=classify_sensitive_message,
    )

    register_platform_feature_routers(
        app,
        create_plugins_router=create_plugins_router,
        plugin_registry=PLUGIN_REGISTRY,
        require_user=require_user,
        require_admin=require_admin,
        append_audit_event=append_audit_event,
        platform=PLATFORM,
        ui_file_response=ui_file_response,
        static_dir=STATIC_DIR,
        create_workflow_designer_router=create_workflow_designer_router,
        store=WORKSPACE_OS,
        get_current_user=get_current_user,
        workspace_graph=_workspace_graph,
        hooks=HOOKS_REGISTRY,
        run_executor=RUN_EXECUTOR,
        trigger_service=TRIGGER_SERVICE,
        create_agents_router=create_agents_router,
        agent_runtime=AGENT_RUNTIME,
        create_marketplace_router=create_marketplace_router,
        template_catalog=TEMPLATE_CATALOG,
        create_realtime_router=create_realtime_router,
        realtime_bus=REALTIME_BUS,
    )
    app.include_router(
        create_automation_intelligence_router(
            service=AUTOMATION_INTELLIGENCE,
            store=WORKSPACE_OS,
            require_user=require_user,
            gate_read=PLATFORM.gate_read,
            gate_write=PLATFORM.gate_write,
            append_audit_event=append_audit_event,
            workspace_graph=_workspace_graph,
            run_executor=RUN_EXECUTOR,
            review_queue=REVIEW_QUEUE,
        )
    )
    # UX funnel metrics (backlog #16): admin-only runtime counters.
    from latticeai.api.funnel_metrics import create_funnel_metrics_router

    app.include_router(
        create_funnel_metrics_router(
            service=FUNNEL_METRICS,
            require_admin=require_admin,
        )
    )
    COMMAND_CENTER = CommandCenterService(
        conversation_store=CONVERSATIONS,
        knowledge_graph=KNOWLEDGE_GRAPH,
        store=WORKSPACE_OS,
        search_service=SEARCH_SERVICE,
        brain_intelligence=BRAIN_INTELLIGENCE,
        automation_intelligence=AUTOMATION_INTELLIGENCE,
        review_queue=REVIEW_QUEUE,
        enable_graph=ENABLE_GRAPH,
    )
    app.include_router(
        create_command_center_router(
            service=COMMAND_CENTER,
            require_user=require_user,
            gate_read=PLATFORM.gate_read,
        )
    )
    CHANGE_PROPOSALS = ChangeProposalService(
        review_queue=REVIEW_QUEUE,
        resolve_path=resolve_workspace_path,
        audit=append_audit_event,
    )
    # Proposal-first mutations: the agent loop consults the governor so
    # additive creates run with minimal friction while changes/deletions of
    # existing files are staged for review instead of applied.
    CHAT_AGENT_RUNTIME.deps.change_governor = CHANGE_PROPOSALS
    app.include_router(
        create_change_proposals_router(
            service=CHANGE_PROPOSALS,
            require_user=require_user,
            gate_read=PLATFORM.gate_read,
            gate_write=PLATFORM.gate_write,
        )
    )


    # ── Health & Info ──────────────────────────────────────────────────────────────

    # ── Model runtime/provider helpers moved to latticeai.services.model_runtime ──
    # ── Health / status / engine-summary router (latticeai.api.health, v1.2.0) ───
    # /health, /mode, /runtime_features, /engines(GET) now live in the health router.
    # Heavier engine mutation endpoints remain below in server_app.
    _model_runtime = register_model_runtime_routers(
        app=app,
        create_health_router=create_health_router,
        create_models_router=create_models_router,
        register_health_and_model_routers=register_health_and_model_routers,
        model_router=router,
        runtime_service=_model_runtime_service,
        runtime_features=_model_runtime_service.runtime_features,
        is_public_mode=IS_PUBLIC_MODE,
        engine_status=_model_runtime_service.engine_status,
        get_current_user=get_current_user,
        require_auth=REQUIRE_AUTH,
        app_version=APP_VERSION,
        app_mode=APP_MODE,
        require_user=require_user,
        require_admin=require_admin,
        load_users=load_users,
        get_user_role=get_user_role,
        install_engine=_model_runtime_service.install_engine,
        verify_cloud_models=_model_runtime_service.verify_cloud_models,
        normalize_local_model_request=normalize_local_model_request,
        download_hf_model=download_hf_model,
        prepare_and_load_model=_model_runtime_service.prepare_and_load_model,
        prepare_and_load_model_stream=_model_runtime_service.prepare_and_load_model_stream,
        sse_event=sse_event,
        ensure_ollama_server=ensure_ollama_server,
        local_binary=local_binary,
        filter_lower_family_versions=filter_lower_family_versions,
        list_compat_profiles=_list_compat_profiles,
        set_user_api_key=set_user_api_key,
        engine_model_catalog=ENGINE_MODEL_CATALOG,
        model_engine_aliases=MODEL_ENGINE_ALIASES,
        cloud_verify_ttl_seconds=CLOUD_VERIFY_TTL_SECONDS,
        allow_local_models=ALLOW_LOCAL_MODELS,
    )


    # ── Chat / Completion ──────────────────────────────────────────────────────────

    def _embedding_info() -> dict:
        from latticeai.core.embedding_providers import PROVIDER_TYPES, embedding_provider_profiles
        info = EMBEDDER.as_dict()
        info["available_providers"] = list(PROVIDER_TYPES)
        info["profile"] = CONFIG.embedding_profile or ""
        info["profiles"] = embedding_provider_profiles()
        return info


    def _allowed_workspaces_for(user):
        # No-auth local mode is single-user: no scoping. With auth, scope
        # reads to the caller's memberships (legacy-global rows require an
        # explicit maintenance-only compatibility opt-in).
        if not REQUIRE_AUTH or not user:
            return None
        return PLATFORM.allowed_scopes(user)

    tool_router_context, interaction_router_context = build_interaction_contexts(
        config=CONFIG,
        ingestion_pipeline=INGESTION_PIPELINE,
        data_dir=DATA_DIR,
        static_dir=STATIC_DIR,
        model_router=router,
        require_user=require_user,
        require_admin=require_admin,
        get_current_user=get_current_user,
        clear_history=clear_history,
        append_audit_event=append_audit_event,
        enforce_rate_limit=enforce_rate_limit,
        bytes_match_extension=_bytes_match_extension,
        classify_sensitive_message=classify_sensitive_message,
        save_to_history=save_to_history,
        enable_graph=ENABLE_GRAPH,
        knowledge_graph=KNOWLEDGE_GRAPH,
        require_graph=_require_graph,
        local_kg_watcher=LOCAL_KG_WATCHER,
        load_mcp_installs=load_mcp_installs,
        recommend_mcps=recommend_mcps,
        install_mcp=install_mcp,
        mcp_public_item=mcp_public_item,
        hooks=HOOKS_REGISTRY,
        workspace_service=WORKSPACE_SERVICE,
        chat_context=context,
        search_service=SEARCH_SERVICE,
        allowed_workspaces_for=_allowed_workspaces_for,
        embedding_info=_embedding_info,
        agent_registry=AGENT_REGISTRY,
        memory_service=MEMORY_SERVICE,
        platform=PLATFORM,
        active_model_getter=lambda: router.current_model_id or "",
        brain_intelligence=BRAIN_INTELLIGENCE,
    )
    register_interaction_routers(
        app,
        interaction_context=interaction_router_context,
        create_chat_router=create_chat_router,
        create_search_router=create_search_router,
        create_tools_router=create_tools_router,
        create_hooks_router=create_hooks_router,
        create_agent_registry_router=create_agent_registry_router,
        create_memory_router=create_memory_router,
        create_brain_intelligence_router=create_brain_intelligence_router,
    )

    from latticeai.api.review_queue import create_review_queue_router
    run_review_item = build_review_run_now_runner(PLATFORM, HTTPException)

    register_review_and_brain_tail_routers(
        app,
        create_review_queue_router=create_review_queue_router,
        review_queue=REVIEW_QUEUE,
        require_user=require_user,
        gate_read=PLATFORM.gate_read,
        gate_write=PLATFORM.gate_write,
        run_review_item=run_review_item,
        append_audit_event=append_audit_event,
        # Approving a change_proposal from the Review Center applies the
        # staged content through the same service the agent governor uses.
        change_proposals=CHANGE_PROPOSALS,
        create_browser_router=create_browser_router,
        ingestion_pipeline=INGESTION_PIPELINE,
        workspace_service=WORKSPACE_SERVICE,
        create_portability_router=create_portability_router,
        kg_portability=KG_PORTABILITY,
        require_admin=require_admin,
        build_brain_network=build_brain_network,
        device_identity=DEVICE_IDENTITY,
        data_dir=DATA_DIR,
        create_network_router=create_network_router,
        create_garden_router=create_garden_router,
        gardener=gardener,
        create_setup_router=create_setup_router,
        model_router=router,
        knowledge_graph=KNOWLEDGE_GRAPH,
    )

    # ── Entry Point ────────────────────────────────────────────────────────────────

    def main() -> None:
        print(f"🧠 Lattice AI Server starting in {APP_MODE} mode on http://{DEFAULT_HOST}:{DEFAULT_PORT}")
        uvicorn.run(app, host=DEFAULT_HOST, port=DEFAULT_PORT, log_level="info")

    # ── Constructed-namespace export (consumed by AppRuntime) ────────────────
    # The five stages are typed and the compatibility surface is enumerated;
    # assembly locals never escape the composition root.
    router_bundle = build_router_bundle(app, context)
    runtime_bundle = RuntimeBundle(
        app=app,
        CONFIG=CONFIG,
        KNOWLEDGE_GRAPH=KNOWLEDGE_GRAPH,
        INGESTION_PIPELINE=INGESTION_PIPELINE,
        AGENT_RUNTIME=AGENT_RUNTIME,
        HOOKS_REGISTRY=HOOKS_REGISTRY,
        REVIEW_QUEUE=REVIEW_QUEUE,
        AGENT_REGISTRY=AGENT_REGISTRY,
        model_router=router,
        build_runtime=build_runtime,
        get_shared_runtime=get_shared_runtime,
        create_app=create_app,
        config_runtime=_config_runtime,
        security_runtime=_security_runtime,
        brain_runtime=_brain_runtime,
        model_runtime=_model_runtime,
        router_bundle=router_bundle,
    )
    return build_runtime_namespace(
        runtime_bundle=runtime_bundle,
        legacy_exports={
            "ENGINE_MODEL_CATALOG": ENGINE_MODEL_CATALOG,
            "TOOL_GOVERNANCE": TOOL_GOVERNANCE,
            "enforce_rate_limit": enforce_rate_limit,
            "filter_lower_family_versions": filter_lower_family_versions,
            "hash_password": hash_password,
            "normalize_local_model_request": normalize_local_model_request,
            "verify_password": verify_password,
            "_LOCAL_WRITE_BLOCKED_PREFIXES": _LOCAL_WRITE_BLOCKED_PREFIXES,
            "_RATE_LIMIT_ENABLED": _RATE_LIMIT_ENABLED,
            "_SESSION_TTL": _SESSION_TTL,
            "_TOOL_CATALOG_BRIEF": _TOOL_CATALOG_BRIEF,
            "_TOOL_GOVERNANCE_DEFAULT": _TOOL_GOVERNANCE_DEFAULT,
            "_agent_risk": _agent_risk,
            "_allowed_workspaces_for": _allowed_workspaces_for,
            "_build_admin_audit_report": _build_admin_audit_report,
            "_build_sensitivity_report": _build_sensitivity_report,
            "_bytes_match_extension": _bytes_match_extension,
            "_check_ip_rate_limit": _check_ip_rate_limit,
            "_check_rate_limit": _check_rate_limit,
            "_check_tool_role": _check_tool_role,
            "_classify_sensitive_message": _classify_sensitive_message,
            "_client_ip": _client_ip,
            "_create_security_router": _create_security_router,
            "_embedding_info": _embedding_info,
            "_fetch_skills_marketplace": _fetch_skills_marketplace,
            "_get_audit_log": _get_audit_log,
            "_get_sso_discovery": _get_sso_discovery,
            "_graph_stats_safe": _graph_stats_safe,
            "_host_is_loopback": _host_is_loopback,
            "_list_compat_profiles": _list_compat_profiles,
            "_llm_generate_sync": _llm_generate_sync,
            "_product_hardening_status": _product_hardening_status,
            "_recent_chat_context": _recent_chat_context,
            "_redact_secret_text": _redact_secret_text,
            "_require_graph": _require_graph,
            "_scoped_hybrid_search": _scoped_hybrid_search,
            "_security_audit_events_safe": _security_audit_events_safe,
            "_security_list_uploaded_files": _security_list_uploaded_files,
            "_spawn": _spawn,
            "_tool_response": _tool_response,
            "_user_id_for_email": _user_id_for_email,
            "_workspace_graph": _workspace_graph,
            "_workspace_models_payload": _workspace_models_payload,
            "_workspace_scope_from_request": _workspace_scope_from_request,
            "_workspace_settings_payload": _workspace_settings_payload,
        },
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
    get_shared_runtime().main()
