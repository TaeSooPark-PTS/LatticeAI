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
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:  # imports for annotations only — keep module import light
    from fastapi import FastAPI

    from latticeai.core.config import Config


def _build(config: "Optional[Config]" = None) -> Dict[str, Any]:
    """The legacy ``server_app`` assembly, moved verbatim into function scope.

    Heavy imports (mlx, the LLM router, knowledge graph, MCP registry, …) are
    deliberately *inside* this function so that importing the module performs
    no GPU init, no singleton construction, and no filesystem writes.
    """
    import asyncio
    import hashlib
    import json
    import logging
    import os
    import re
    import secrets
    import threading
    import subprocess
    import sys
    import time
    from contextlib import asynccontextmanager
    from pathlib import Path

    try:
        import mlx.core as mx
        mx.set_default_device(mx.gpu)
        print("✅ MLX Metal context initialized in main thread.")
    except Exception as e:
        print(f"⚠️ MLX Metal context unavailable: {e}")
        mx = None
    from typing import List

    import uvicorn
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel

    from latticeai.models.router import LLMRouter, normalize_branding
    from knowledge_graph import KnowledgeGraphStore, set_llm_router
    from local_knowledge_api import LocalKnowledgeWatcher
    from latticeai.core.security import (
        hash_password,
        verify_password,
        host_is_loopback as _host_is_loopback_impl,
        client_ip as _client_ip_impl,
        configure_trusted_proxies as _configure_trusted_proxies,
        bytes_match_extension as _bytes_match_extension_impl,
        redact_secret_text as _redact_secret_text,
        check_ip_rate_limit as _check_ip_rate_limit,
        enforce_rate_limit as _enforce_rate_limit,
    )
    from latticeai.core.sessions import SessionStore as _SessionStore
    from latticeai.core.audit import (
        get_audit_log as _get_audit_log,
        append_audit_event as _append_audit_event,
        classify_sensitive_message as _classify_sensitive_message,
        build_sensitivity_report as _build_sensitivity_report,
        build_admin_audit_report as _build_admin_audit_report,
    )
    from latticeai.api.auth import create_auth_router
    from latticeai.api.admin import create_admin_router
    from latticeai.api.security_dashboard import create_security_router as _create_security_router
    from latticeai.core.model_compat import list_cached_profiles as _list_compat_profiles
    from latticeai.core.config import Config
    from latticeai.core.workspace_os import (
        WORKSPACE_OS_VERSION,
        WorkspaceOSStore,
        remove_skill_directory,
    )
    from latticeai.core.enterprise import (
        capability_registry,
    )
    from latticeai.services.app_context import AppContext
    from latticeai.services.workspace_service import WorkspaceService
    from latticeai.services.model_service import ModelService
    from latticeai.services.chat_service import ChatService
    from latticeai.services.search_service import SearchService
    from latticeai.core.embedding_providers import resolve_embedder, resolve_embedding_profile
    from latticeai.services.agent_runtime import AgentRuntime
    from latticeai.services.model_runtime import (
        CLOUD_VERIFY_TTL_SECONDS,
        ENGINE_MODEL_CATALOG,
        LOCAL_SERVER_PROCESSES,
        MODEL_ENGINE_ALIASES,
        configure_model_runtime,
        download_hf_model,
        engine_status,
        filter_lower_family_versions,
        install_engine,
        local_binary,
        normalize_local_model_request,
        prepare_and_load_model,
        prepare_and_load_model_stream,
        runtime_features,
        sse_event,
        verify_cloud_models,
        ensure_ollama_server,
    )
    from latticeai.api.workspace import create_workspace_router, _workspace_scope_from_request
    from latticeai.api.health import create_health_router
    # ── v2 Agentic Workspace Platform layers ─────────────────────────────────────
    from latticeai.core.plugins import PluginRegistry
    from latticeai.core.realtime import RealtimeBus
    from latticeai.core.marketplace import TemplateCatalog
    from latticeai.services.platform_runtime import PlatformRuntime
    from latticeai.api.plugins import create_plugins_router
    from latticeai.api.workflow_designer import create_workflow_designer_router
    from latticeai.api.agents import create_agents_router
    from latticeai.api.realtime import create_realtime_router
    from latticeai.api.marketplace import create_marketplace_router
    from latticeai.api.models import create_models_router
    from latticeai.api.chat import create_chat_router
    from latticeai.api.search import create_search_router
    from latticeai.api.tools import create_tools_router
    from latticeai.api.static_routes import create_static_routes_router
    from latticeai.api.garden import create_garden_router
    from latticeai.api.setup import create_setup_router
    from latticeai.api.hooks import create_hooks_router
    from latticeai.core.hooks import HooksRegistry
    from latticeai.core.builtin_hooks import register_builtin_hook_runners
    from latticeai.api.agent_registry import create_agent_registry_router
    from latticeai.core.agent_registry import AgentRegistry
    from latticeai.api.memory import create_memory_router
    from latticeai.api.browser import create_browser_router
    from latticeai.api.portability import create_portability_router
    from latticeai.services.memory_service import MemoryService
    from latticeai.services.ingestion import IngestionPipeline
    from latticeai.services.kg_portability import KGPortabilityService
    # The aliased names below look unused but are part of the legacy
    # ``server_app`` attribute surface: every local is exported via
    # ``dict(locals())`` and reached through ``server_app.__getattr__``
    # (tests import _agent_risk, _LOCAL_WRITE_BLOCKED_PREFIXES, …).
    from latticeai.services.tool_dispatch import (  # noqa: F401
        LOCAL_WRITE_BLOCKED_PREFIXES as _LOCAL_WRITE_BLOCKED_PREFIXES,
        TOOL_GOVERNANCE,
        TOOL_GOVERNANCE_DEFAULT as _TOOL_GOVERNANCE_DEFAULT,
        agent_risk as _agent_risk,
        check_tool_role as _check_tool_role,
        configure_tool_dispatch,
        get_tool_permission,
        list_tool_permissions,
        tool_response as _tool_response,
    )
    from latticeai.core.tool_registry import TOOL_CATALOG_BRIEF as _TOOL_CATALOG_BRIEF  # noqa: F401
    from latticeai.core.mcp_registry import (
        _get_combined_registry,
        _fetch_skills_marketplace, install_skill, SKILLS_DIR,
    )
    from p_reinforce import PReinforceGardener
    from setup_wizard import get_recommendations, scan_environment
    from tools import ensure_agent_root

    try:
        import keyring
    except Exception:
        keyring = None

    from datetime import datetime

    # ── App-level config — parsed once, in one place (latticeai.core.config) ──────
    # The module-level names below are kept as a compatibility surface for the rest
    # of server.py; all of them are now derived from a single CONFIG instance.
    CONFIG = config if config is not None else Config.from_env()
    APP_VERSION = WORKSPACE_OS_VERSION

    # Forwarded headers (X-Forwarded-For / CF-Connecting-IP) are only honoured for
    # IP rate limiting when the direct peer is one of these trusted proxies. Empty by
    # default (local-first): the peer address is used and client-supplied headers are
    # ignored, so per-IP rate limits cannot be spoofed.
    _configure_trusted_proxies(CONFIG.trusted_proxies)

    APP_MODE = CONFIG.app_mode
    IS_PUBLIC_MODE = CONFIG.is_public
    DEFAULT_HOST = CONFIG.host
    DEFAULT_PORT = CONFIG.port
    def _host_is_loopback(host: str) -> bool:
        return _host_is_loopback_impl(host)

    NETWORK_EXPOSED = CONFIG.network_exposed
    ENABLE_TELEGRAM = CONFIG.enable_telegram
    ENABLE_GRAPH    = CONFIG.enable_graph
    AUTOLOAD_MODELS = CONFIG.autoload_models
    MODEL_IDLE_UNLOAD_SECONDS = CONFIG.model_idle_unload_seconds
    ALLOW_LOCAL_MODELS = CONFIG.allow_local_models
    REQUIRE_AUTH = CONFIG.require_auth
    ALLOW_PLAINTEXT_API_KEYS = CONFIG.allow_plaintext_api_keys
    CORS_ALLOW_NETWORK = CONFIG.cors_allow_network
    CORS_EXTRA_ORIGINS = CONFIG.cors_extra_origins
    PUBLIC_MODEL = CONFIG.public_model
    LOCAL_MODEL = CONFIG.local_model
    LOCAL_DRAFT_MODEL = CONFIG.local_draft_model

    # ── SSO / OIDC config ─────────────────────────────────────────────────────────
    SSO_DISCOVERY_URL = CONFIG.sso_discovery_url
    SSO_CLIENT_ID = CONFIG.sso_client_id
    SSO_CLIENT_SECRET = CONFIG.sso_client_secret
    SSO_REDIRECT_URI = CONFIG.sso_redirect_uri
    SSO_PROVIDER_NAME = CONFIG.sso_provider_name
    _sso_discovery_cache: Optional[Dict] = None
    _sso_discovery_cache_url: str = ""
    _sso_states: Dict[str, float] = {}  # state → timestamp (CSRF protection)

    async def _get_sso_discovery() -> Optional[Dict]:
        nonlocal _sso_discovery_cache, _sso_discovery_cache_url
        settings = get_sso_settings()
        discovery_url = settings.get("discovery_url", "")
        if _sso_discovery_cache and _sso_discovery_cache_url == discovery_url:
            return _sso_discovery_cache
        if not discovery_url:
            return None
        try:
            import httpx as _httpx
            async with _httpx.AsyncClient() as c:
                r = await c.get(discovery_url, timeout=10)
                r.raise_for_status()
                _sso_discovery_cache = r.json()
                _sso_discovery_cache_url = discovery_url
        except Exception as e:
            logging.warning("SSO discovery failed: %s", e)
            return None
        return _sso_discovery_cache

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

    # ── Session store — delegated to latticeai.core.sessions ──────────────────────
    _SESSION_TTL = 60 * 60 * 24
    _session_store = _SessionStore()

    def _check_rate_limit(ip: str, action: str, max_calls: int, window_secs: float) -> None:
        _check_ip_rate_limit(ip, action, max_calls=max_calls, window_secs=window_secs)

    def _client_ip(request: Request) -> str:
        return _client_ip_impl(request)

    def create_session(email: str) -> str:
        return _session_store.create(email)

    def get_session_email(token: str) -> Optional[str]:
        return _session_store.get_email(token)

    def invalidate_session(token: str) -> None:
        _session_store.invalidate(token)

    # ── User Management Logic ──────────────────────────────────────────────────
    BASE_DIR = Path(__file__).resolve().parent.parent
    DATA_DIR = CONFIG.data_dir
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATIC_DIR = CONFIG.static_dir

    USERS_FILE = DATA_DIR / "users.json"
    HISTORY_FILE = DATA_DIR / "chat_history.json"
    VPC_FILE = DATA_DIR / "vpc_config.json"
    MCP_FILE = DATA_DIR / "mcp_installs.json"
    AUDIT_FILE = DATA_DIR / "audit_log.json"
    SSO_FILE = DATA_DIR / "sso_config.json"
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
    if EMBEDDER.fell_back:
        logging.warning("Embedding provider %s unavailable: %s", EMBEDDER.requested, EMBEDDER.detail)
    KNOWLEDGE_GRAPH = KnowledgeGraphStore(
        DATA_DIR / "knowledge_graph.sqlite",
        DATA_DIR / "knowledge_graph_blobs",
        embedder=EMBEDDER.provider,
    ) if ENABLE_GRAPH else None
    # Hooks registry is constructed here (ahead of the watcher) so folder-watch
    # reindexes can fire the pre_index/post_index lifecycle hooks.
    HOOKS_REGISTRY = HooksRegistry(DATA_DIR / "hooks.json")
    LOCAL_KG_WATCHER = LocalKnowledgeWatcher(lambda: KNOWLEDGE_GRAPH, hooks=HOOKS_REGISTRY) if ENABLE_GRAPH else None
    # ── v2 Realtime bus: constructed first so the store can fan every timeline
    # event into the realtime feed via a single additive sink (no per-call wiring).
    REALTIME_BUS = RealtimeBus()
    WORKSPACE_OS = WorkspaceOSStore(DATA_DIR, event_sink=REALTIME_BUS)
    # Service layer (latticeai.services) wraps the store with scope/permission
    # guardrails; routers and the app assembly share this single instance.
    WORKSPACE_SERVICE = WorkspaceService(WORKSPACE_OS)
    # ── v2 Plugin SDK registry (extends skills; discovers plugins/<id>/plugin.json)
    PLUGINS_DIR = Path(os.getenv("LATTICEAI_PLUGINS_DIR") or (BASE_DIR / "plugins"))
    PLUGIN_REGISTRY = PluginRegistry(PLUGINS_DIR, store=WORKSPACE_OS)
    TEMPLATE_CATALOG = TemplateCatalog()
    # ── v3.2 platform registries: lifecycle hooks + agent registry, persisted under
    # DATA_DIR so the /app Hooks and Agent Registry views read/write real state.
    # (HOOKS_REGISTRY is constructed earlier, before the local-knowledge watcher.)
    AGENT_REGISTRY = AgentRegistry(DATA_DIR / "agent_registry.json")
    # Unified long-term memory platform fronting workspace memories, agent
    # snapshots, conversation history, and the KG graph/vector index.
    MEMORY_SERVICE = MemoryService(
        store=WORKSPACE_OS,
        data_dir=DATA_DIR,
        knowledge_graph=KNOWLEDGE_GRAPH,
        enable_graph=ENABLE_GRAPH,
        history_file=HISTORY_FILE,
    )
    # ── v3.6.0 unified ingestion pipeline: the single write-side seam into the
    # Knowledge Graph. Every new source (web URL, browser tab, …) flows through this
    # so pre_tool/post_tool hooks fire on ingestion and provenance is captured
    # uniformly. Existing direct ingest callers keep working; new paths converge here.
    INGESTION_PIPELINE = IngestionPipeline(
        KNOWLEDGE_GRAPH,
        hooks=HOOKS_REGISTRY,
        enable_graph=ENABLE_GRAPH,
        audit=lambda action, detail, user: append_audit_event(action, user_email=user, **detail),
    )
    # ── v3.6.0 Knowledge Graph portability: local export / import / backup / restore.
    # The graph is the user's durable asset, so it must be portable with no cloud.
    KG_PORTABILITY = KGPortabilityService(
        knowledge_graph=KNOWLEDGE_GRAPH,
        data_dir=DATA_DIR,
        enable_graph=ENABLE_GRAPH,
    )

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

    def _sso_env_defaults() -> Dict[str, object]:
        return {
            "enabled": bool(SSO_DISCOVERY_URL and SSO_CLIENT_ID and SSO_CLIENT_SECRET),
            "provider_name": SSO_PROVIDER_NAME,
            "discovery_url": SSO_DISCOVERY_URL,
            "client_id": SSO_CLIENT_ID,
            "client_secret": SSO_CLIENT_SECRET,
            "redirect_uri": SSO_REDIRECT_URI,
            "scopes": "openid email profile",
        }

    def load_sso_config() -> Dict[str, object]:
        config = _sso_env_defaults()
        if SSO_FILE.exists():
            try:
                data = json.loads(SSO_FILE.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    config.update({k: v for k, v in data.items() if v is not None})
            except Exception as e:
                logging.warning("load_sso_config failed (using env/defaults): %s", e)
        config["provider_name"] = str(config.get("provider_name") or "SSO")
        config["discovery_url"] = str(config.get("discovery_url") or "")
        config["client_id"] = str(config.get("client_id") or "")
        config["client_secret"] = str(config.get("client_secret") or "")
        config["redirect_uri"] = str(config.get("redirect_uri") or SSO_REDIRECT_URI)
        config["scopes"] = str(config.get("scopes") or "openid email profile")
        config["enabled"] = bool(config.get("enabled")) and bool(
            config["discovery_url"] and config["client_id"] and config["client_secret"]
        )
        return config

    def get_sso_settings() -> Dict[str, object]:
        return load_sso_config()

    def public_sso_config(config: Optional[Dict[str, object]] = None) -> Dict[str, object]:
        cfg = config or get_sso_settings()
        return {
            "enabled": bool(cfg.get("enabled")),
            "provider_name": cfg.get("provider_name") or "",
            "discovery_url": cfg.get("discovery_url") or "",
            "client_id": cfg.get("client_id") or "",
            "redirect_uri": cfg.get("redirect_uri") or SSO_REDIRECT_URI,
            "scopes": cfg.get("scopes") or "openid email profile",
            "secret_configured": bool(cfg.get("client_secret")),
        }

    def save_sso_config(update: Dict[str, object]) -> Dict[str, object]:
        nonlocal _sso_discovery_cache, _sso_discovery_cache_url
        current = load_sso_config()
        if update.get("client_secret") == "":
            update.pop("client_secret", None)
        current.update({k: v for k, v in update.items() if v is not None})
        current["enabled"] = bool(current.get("enabled")) and bool(
            current.get("discovery_url") and current.get("client_id") and current.get("client_secret")
        )
        SSO_FILE.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        _sso_discovery_cache = None
        _sso_discovery_cache_url = ""
        return current

    # MCP/skill request models moved to latticeai.api.mcp (v1.3.0).
    DEFAULT_VPC_CONFIG = {
        "provider": "AWS",
        "region": "ap-northeast-2",
        "cidr_block": "10.42.0.0/16",
        "private_subnets": ["10.42.10.0/24", "10.42.20.0/24"],
        "endpoint": "ltcai-private.local",
        "vpn_status": "standby",
        "peering_status": "not_configured",
        "notes": "로컬 MLX 브릿지를 프라이빗 서브넷 또는 VPN 뒤에서 운영할 때 쓰는 네트워크 프로필입니다.",
        "updated_at": None,
    }


    def load_users():
        if not os.path.exists(USERS_FILE):
            return {}
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_users(users):
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)

    def load_vpc_config() -> Dict:
        if not os.path.exists(VPC_FILE):
            return DEFAULT_VPC_CONFIG.copy()
        try:
            with open(VPC_FILE, "r", encoding="utf-8") as f:
                stored = json.load(f)
            return {**DEFAULT_VPC_CONFIG, **stored}
        except Exception as e:
            logging.warning("load_vpc_config failed (using defaults): %s", e)
            return DEFAULT_VPC_CONFIG.copy()

    def save_vpc_config(config: Dict):
        config["updated_at"] = datetime.now().isoformat()
        with open(VPC_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

    def load_mcp_installs() -> Dict:
        if not os.path.exists(MCP_FILE):
            return {"installed": {}, "updated_at": None}
        try:
            with open(MCP_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "installed" not in data:
                data["installed"] = {}
            return data
        except Exception as e:
            logging.warning("load_mcp_installs failed: %s", e)
            return {"installed": {}, "updated_at": None}

    def save_mcp_installs(data: Dict):
        data["updated_at"] = datetime.now().isoformat()
        with open(MCP_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def mcp_public_item(item: Dict, installed_state: Dict) -> Dict:
        state = installed_state.get(item["id"]) or {}
        installed = item["install_mode"] in {"builtin", "bundled"} or bool(state.get("installed"))
        connector_pending = item["install_mode"] == "connector" and not state.get("authenticated")
        authenticated = item["install_mode"] != "connector" or bool(state.get("authenticated"))
        return {
            "id": item["id"],
            "name": item["name"],
            "category": item.get("category", ""),
            "install_mode": item["install_mode"],
            "description": item.get("description", ""),
            "capabilities": item.get("capabilities", []),
            "connector_url": item.get("connector_url"),
            "external_url": item.get("external_url"),
            "package": item.get("package"),
            "homepage": item.get("homepage"),
            "source": item.get("source", "local"),
            "installed": installed,
            "status": state.get("status") or ("active" if installed and not connector_pending else "needs_auth" if connector_pending else "available"),
            "authenticated": authenticated,
            "updated_at": state.get("updated_at"),
        }

    async def recommend_mcps(query: str, limit: int = 5) -> List[Dict]:
        text = (query or "").lower()
        installed = load_mcp_installs().get("installed", {})
        registry = await _get_combined_registry()
        scored = []
        for item in registry:
            score = 0
            hits = []
            for keyword in item.get("keywords", []):
                if keyword.lower() in text:
                    score += 3 if len(keyword) > 2 else 1
                    hits.append(keyword)
            # description 키워드 매칭 (remote 항목 보완)
            if not hits and text:
                desc_words = item.get("description", "").lower().split()
                for word in text.split():
                    if len(word) > 2 and word in desc_words:
                        score += 1
                        hits.append(word)
            if item["id"] == "filesystem" and any(word in text for word in ["만들", "구현", "build", "deploy", "코드", "앱"]):
                score += 2
            if score:
                public = mcp_public_item(item, installed)
                public["score"] = score
                public["matched_keywords"] = hits[:6]
                scored.append(public)
        if not scored:
            fallback_ids = ["filesystem", "browser", "documents"]
            scored = [
                {**mcp_public_item(item, installed), "score": 1, "matched_keywords": []}
                for item in registry
                if item["id"] in fallback_ids
            ]
        return sorted(scored, key=lambda item: item["score"], reverse=True)[: max(1, min(limit, 24))]

    async def install_mcp(mcp_id: str) -> Dict:
        registry = await _get_combined_registry()
        item = next((entry for entry in registry if entry["id"] == mcp_id), None)
        if not item:
            raise HTTPException(status_code=404, detail="MCP를 찾을 수 없습니다.")
        data = load_mcp_installs()
        state = data.setdefault("installed", {})
        status = "active"
        message = "MCP가 활성화되었습니다."
        if item["install_mode"] == "connector":
            status = "needs_auth"
            message = "커넥터 인증이 필요합니다. Codex 앱의 connector 설정에서 계정을 연결하면 바로 사용할 수 있습니다."
        elif item["install_mode"] == "pip":
            packages = item.get("pip_packages") or []
            for pkg in packages:
                completed = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "--upgrade", pkg],
                    capture_output=True, text=True, timeout=900, check=False,
                )
                if completed.returncode != 0:
                    raise HTTPException(status_code=500, detail=completed.stderr[-2000:] or f"{pkg} 설치 실패")
            message = f"필수 패키지 설치 완료: {', '.join(packages)}"
        elif item["install_mode"] == "pypi":
            pkg = item.get("package", "")
            version = item.get("package_version")
            pkg_str = f"{pkg}=={version}" if version else pkg
            completed = subprocess.run(
                [sys.executable, "-m", "pip", "install", pkg_str],
                capture_output=True, text=True, timeout=300, check=False,
            )
            if completed.returncode != 0:
                raise HTTPException(status_code=500, detail=completed.stderr[-2000:] or f"{pkg} 설치 실패")
            message = f"pip 패키지 설치 완료: {pkg_str}"
        elif item["install_mode"] == "npm":
            pkg = item.get("package", "")
            version = item.get("package_version")
            pkg_str = f"{pkg}@{version}" if version else pkg
            completed = subprocess.run(
                ["npm", "install", "-g", pkg_str],
                capture_output=True, text=True, timeout=300, check=False,
            )
            if completed.returncode != 0:
                raise HTTPException(status_code=500, detail=completed.stderr[-2000:] or f"{pkg} 설치 실패")
            message = f"npm 패키지 설치 완료: {pkg_str}"
        state[mcp_id] = {
            "installed": True,
            "status": status,
            "authenticated": item["install_mode"] != "connector",
            "updated_at": datetime.now().isoformat(),
        }
        save_mcp_installs(data)
        public = mcp_public_item(item, state)
        public["message"] = message
        return public

    _history_lock = threading.Lock()

    def get_audit_log() -> List[Dict]:
        return _get_audit_log(AUDIT_FILE)

    def append_audit_event(event_type: str, **payload) -> None:
        _append_audit_event(AUDIT_FILE, event_type, **payload)

    def save_to_history(
        role: str,
        message: str,
        user_email: Optional[str] = None,
        user_nickname: Optional[str] = None,
        source: Optional[str] = None,
        conversation_id: Optional[str] = None,
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
            sensitive = classify_sensitive_message(item, -1)
            append_audit_event(
                "chat_message",
                role=role,
                user_email=user_email,
                user_nickname=user_nickname,
                source=source,
                conversation_id=conversation_id,
                content_preview=sensitive.get("preview"),
                content_chars=len(message or ""),
                sensitivity=sensitive.get("sensitivity"),
                sensitive_labels=sensitive.get("labels") or [],
            )
            with _history_lock:
                history = []
                if os.path.exists(HISTORY_FILE):
                    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                        history = json.load(f)
                history.append(item)
                if len(history) > 50:
                    history = history[-50:]
                tmp_path = str(HISTORY_FILE) + ".tmp"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(history, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, HISTORY_FILE)
            try:
                if ENABLE_GRAPH and KNOWLEDGE_GRAPH:
                    KNOWLEDGE_GRAPH.ingest_message(
                        role,
                        message,
                        user_email=user_email,
                        user_nickname=user_nickname,
                        source=source,
                        conversation_id=conversation_id,
                        raw=item,
                    )
            except Exception as graph_error:
                logging.warning("knowledge graph message ingest failed: %s", graph_error)
        except Exception as e:
            logging.warning("save_to_history failed: %s", e)

    def redact_secret_text(text: str) -> str:
        return _redact_secret_text(text)

    def get_history():
        if not os.path.exists(HISTORY_FILE):
            return []
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logging.warning("get_history failed: %s", e)
            return []

    # Chat service seam: behaviour-preserving façade for history access and
    # Workspace-OS answer-trace recording used by the (unchanged) streaming chat path.
    CHAT_SERVICE = ChatService(store=WORKSPACE_OS, get_history=get_history)

    def conversation_title(item: Dict) -> str:
        content = str(item.get("content") or "").strip()
        content = re.sub(r"\s+", " ", content)
        return content[:48] or "새 대화"

    def group_history_conversations(history: Optional[List[Dict]] = None) -> List[Dict]:
        history = history if history is not None else get_history()
        conversations: Dict[str, Dict] = {}
        order: List[str] = []

        for index, item in enumerate(history):
            conv_id = item.get("conversation_id")
            if not conv_id:
                conv_id = "legacy-previous-history"

            if conv_id not in conversations:
                conversations[conv_id] = {
                    "id": conv_id,
                    "title": "이전 대화 기록" if conv_id == "legacy-previous-history" else conversation_title(item),
                    "created_at": item.get("timestamp"),
                    "updated_at": item.get("timestamp"),
                    "message_count": 0,
                    "last_message": "",
                    "source": item.get("source"),
                }
                order.append(conv_id)

            conv = conversations[conv_id]
            conv["message_count"] += 1
            conv["updated_at"] = item.get("timestamp") or conv.get("updated_at")
            conv["last_message"] = conversation_title(item)
            if conv_id != "legacy-previous-history" and item.get("role") == "user" and (not conv.get("title") or conv["title"] == "새 대화"):
                conv["title"] = conversation_title(item)

        return sorted((conversations[key] for key in order), key=lambda item: item.get("updated_at") or "", reverse=True)

    def get_conversation_messages(conversation_id: str) -> List[Dict]:
        history = get_history()
        if conversation_id == "legacy-previous-history":
            return [item for item in history if not item.get("conversation_id")]
        return [item for item in history if item.get("conversation_id") == conversation_id]

    def clear_history(keep_last: int = 0) -> Dict:
        keep_last = max(0, min(int(keep_last or 0), 20))
        previous = get_history()
        kept = previous[-keep_last:] if keep_last else []
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(kept, f, ensure_ascii=False, indent=2)
        return {"status": "cleared", "removed": max(0, len(previous) - len(kept)), "kept": len(kept)}

    def clear_conversation(conversation_id: str, started_at: Optional[str] = None) -> Dict:
        previous = get_history()
        kept = []
        removed = 0
        for item in previous:
            item_conversation_id = item.get("conversation_id")
            should_remove = item_conversation_id == conversation_id
            if conversation_id == "legacy-previous-history":
                should_remove = not item_conversation_id
            elif started_at and not item_conversation_id:
                should_remove = str(item.get("timestamp") or "") >= started_at

            if should_remove:
                removed += 1
            else:
                kept.append(item)

        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(kept, f, ensure_ascii=False, indent=2)
        return {"status": "cleared", "conversation_id": conversation_id, "removed": removed, "kept": len(kept)}

    def get_user_role(email: str, users: Optional[Dict] = None) -> str:
        users = users or load_users()
        user = users.get(email) or {}
        if user.get("role") in {"admin", "user"}:
            return user["role"]
        admin_emails = set(CONFIG.admin_emails)
        if email.lower() in admin_emails:
            return "admin"
        first_email = next(iter(users), None)
        return "admin" if first_email == email else "user"

    def _extract_bearer_token(request: Request) -> Optional[str]:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:].strip()
        return request.cookies.get("session_token")

    def get_current_user(request: Request) -> Optional[str]:
        token = _extract_bearer_token(request)
        if token:
            return get_session_email(token)
        return None

    def require_user(request: Request) -> str:
        email = get_current_user(request)
        if REQUIRE_AUTH and not email:
            raise HTTPException(status_code=401, detail="인증이 필요합니다.")
        return email or ""


    # ── Rate limiting & file validation — delegated to latticeai.core.security ────
    _RATE_LIMIT_ENABLED = CONFIG.rate_limit_enabled

    def enforce_rate_limit(email: str, bucket_key: str) -> None:
        _enforce_rate_limit(email, bucket_key, enabled=_RATE_LIMIT_ENABLED)

    def _bytes_match_extension(data: bytes, ext: str) -> bool:
        return _bytes_match_extension_impl(data, ext)

    _LOCAL_APPROVAL_TTL_SECONDS = 5 * 60
    _local_approvals: Dict[str, Dict[str, object]] = {}


    def _normalize_local_path_for_approval(path: str) -> str:
        return str(Path(path).expanduser().resolve())


    def _content_fingerprint(content: str = "") -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()


    def _local_permission_response(path: str, action: str, user_email: str, content: str = "") -> dict:
        normalized = _normalize_local_path_for_approval(path)
        token = secrets.token_urlsafe(24)
        record: Dict[str, object] = {
            "path": normalized,
            "action": action,
            "user_email": user_email,
            "expires_at": time.time() + _LOCAL_APPROVAL_TTL_SECONDS,
            "approved": False,
        }
        if action == "write":
            record["content_hash"] = _content_fingerprint(content)
        _local_approvals[token] = record
        return {
            "permission_required": True,
            "path": path,
            "action": action,
            "approval_token": token,
            "expires_in": _LOCAL_APPROVAL_TTL_SECONDS,
        }


    def _require_local_approval(
        *,
        token: Optional[str],
        path: str,
        action: str,
        user_email: str,
        content: str = "",
    ) -> None:
        if not token:
            raise HTTPException(status_code=403, detail="파일 접근 승인 토큰이 필요합니다.")
        record = _local_approvals.get(token)
        if not record or float(record.get("expires_at", 0)) < time.time():
            raise HTTPException(status_code=403, detail="파일 접근 승인이 만료되었거나 유효하지 않습니다.")
        if not record.get("approved"):
            raise HTTPException(status_code=403, detail="파일 접근이 아직 승인되지 않았습니다.")
        if record.get("user_email") != user_email:
            raise HTTPException(status_code=403, detail="다른 사용자의 파일 접근 승인은 사용할 수 없습니다.")
        if record.get("path") != _normalize_local_path_for_approval(path) or record.get("action") != action:
            raise HTTPException(status_code=403, detail="파일 접근 승인 범위가 일치하지 않습니다.")
        if action == "write" and record.get("content_hash") != _content_fingerprint(content):
            raise HTTPException(status_code=403, detail="승인된 파일 내용과 요청 내용이 다릅니다.")


    def require_admin(request: Request) -> tuple[str, Dict]:
        users = load_users()
        if not REQUIRE_AUTH:
            return "", users
        token = _extract_bearer_token(request)
        if token:
            email = get_session_email(token)
            if email:
                if get_user_role(email, users) == "admin":
                    return email, users
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")

    def public_user(email: str, user: Dict, users: Dict) -> Dict:
        return {
            "email": email,
            "name": user.get("name", ""),
            "nickname": user.get("nickname", ""),
            "role": get_user_role(email, users),
            "disabled": bool(user.get("disabled", False)),
        }

    def get_history_user(email: Optional[str], nickname: Optional[str] = None) -> Dict:
        if not email:
            return {"user_email": None, "user_nickname": nickname or None}
        users = load_users()
        user = users.get(email, {})
        return {
            "user_email": email,
            "user_nickname": nickname or user.get("nickname") or user.get("name") or email,
        }

    def get_user_api_key(email: Optional[str], provider: str) -> Optional[str]:
        if not email:
            return None
        keyring_key = f"{email}:{provider}"
        if keyring is not None:
            try:
                key = keyring.get_password("LatticeAI", keyring_key)
                if key:
                    return key.strip()
            except Exception as exc:
                logging.warning("keyring read failed for %s: %s", provider, exc)
        users = load_users()
        user = users.get(email) or {}
        api_keys = user.get("api_keys") or {}
        key = api_keys.get(provider)
        if isinstance(key, str) and key.strip() and ALLOW_PLAINTEXT_API_KEYS:
            return key.strip()
        return None

    def set_user_api_key(email: str, provider: str, key: str) -> None:
        keyring_key = f"{email}:{provider}"
        if keyring is not None:
            try:
                keyring.set_password("LatticeAI", keyring_key, key)
                users = load_users()
                user = users.get(email)
                if user and "api_keys" in user:
                    user["api_keys"].pop(provider, None)
                    if not user["api_keys"]:
                        user.pop("api_keys", None)
                    save_users(users)
                return
            except Exception as exc:
                logging.warning("keyring write failed for %s: %s", provider, exc)
                if not ALLOW_PLAINTEXT_API_KEYS:
                    raise HTTPException(
                        status_code=500,
                        detail="OS keyring에 API 키를 저장하지 못했습니다. keyring 설정을 확인하거나 LATTICEAI_ALLOW_PLAINTEXT_API_KEYS=true를 명시적으로 설정하세요.",
                    )

        if not ALLOW_PLAINTEXT_API_KEYS:
            raise HTTPException(
                status_code=500,
                detail="keyring 패키지를 사용할 수 없어 API 키를 안전하게 저장할 수 없습니다.",
            )

        users = load_users()
        user = users.get(email)
        if not user:
            user = {
                "password_hash": "",
                "salt": "",
                "name": email,
                "nickname": email,
                "role": "user",
                "disabled": False,
            }
        api_keys = user.get("api_keys") or {}
        api_keys[provider] = key
        user["api_keys"] = api_keys
        users[email] = user
        save_users(users)

    # ── Sensitivity analysis — delegated to latticeai.core.audit ──────────────────
    def classify_sensitive_message(item: Dict, index: int) -> Dict:
        return _classify_sensitive_message(item, index)

    def build_sensitivity_report(history: List[Dict]) -> Dict:
        return _build_sensitivity_report(history)

    # ── Admin audit report — delegated to latticeai.core.audit ───────────────────
    def build_admin_audit_report(users: Dict) -> Dict:
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
        )

    router = LLMRouter()
    set_llm_router(router)
    configure_tool_dispatch(load_users=load_users, get_user_role=get_user_role)
    gardener = PReinforceGardener()

    async def autoload_default_model() -> None:
        if not AUTOLOAD_MODELS:
            print("⏭️ Model autoload disabled by LATTICEAI_AUTOLOAD_MODELS=false.")
            return

        if IS_PUBLIC_MODE:
            model_id = PUBLIC_MODEL
            provider = model_id.split(":", 1)[0] if ":" in model_id else "openai"
            env_by_provider = {
                "openai": "OPENAI_API_KEY",
                "openrouter": "OPENROUTER_API_KEY",
                "groq": "GROQ_API_KEY",
                "together": "TOGETHER_API_KEY",
                "ollama": "OLLAMA_API_KEY",
            }
            required_env = env_by_provider.get(provider)
            if required_env and not os.getenv(required_env) and provider != "ollama":
                print(f"🌐 Public mode ready. Set {required_env} to autoload {model_id}.")
                return
            print(f"🌐 Public mode autoload: {model_id}")
            try:
                msg = await router.load_model(model_id)
                print(f"✅ {msg}")
            except Exception as e:
                print(f"⚠️ Public model autoload failed: {e}")
            return

        if not ALLOW_LOCAL_MODELS:
            print("⏭️ Local model autoload skipped because LATTICEAI_ALLOW_LOCAL_MODELS=false.")
            return

        print("⏳ Auto-loading local model stack:")
        print(f"   - Target: {LOCAL_MODEL}")
        if LOCAL_DRAFT_MODEL:
            print(f"   - Draft:  {LOCAL_DRAFT_MODEL}")
        else:
            print("   - Draft:  disabled (set LATTICEAI_LOCAL_DRAFT_MODEL to enable)")
        try:
            await router.load_model(LOCAL_MODEL, draft_model_id=LOCAL_DRAFT_MODEL or None)
        except Exception as e:
            print(f"⚠️ Local model autoload failed: {e}")

    async def unload_idle_models_loop() -> None:
        if MODEL_IDLE_UNLOAD_SECONDS <= 0:
            print("⏭️ Model idle unload disabled.")
            return
        while True:
            await asyncio.sleep(min(60, MODEL_IDLE_UNLOAD_SECONDS))
            try:
                unloaded = router.unload_idle_models(MODEL_IDLE_UNLOAD_SECONDS)
                if unloaded:
                    print(f"🧹 Idle model unload: {', '.join(unloaded)}")
            except Exception as e:
                logging.warning("Idle model unload failed: %s", e)

    def _spawn(coro, *, name: str):
        """Fire-and-forget asyncio task that logs exceptions instead of swallowing them."""
        task = asyncio.create_task(coro, name=name)
        def _on_done(t: asyncio.Task) -> None:
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                logging.warning("background task '%s' failed: %s", name, exc)
        task.add_done_callback(_on_done)
        return task


    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            print(f"🧭 Lattice AI mode: {APP_MODE}")
            if ENABLE_TELEGRAM:
                from telegram_bot import run_bot
                _spawn(run_bot(), name="telegram_bot")
                print("🚀 Telegram Bot Bridge activated!")
            else:
                print("⏭️ Telegram Bot Bridge disabled for this mode.")
            _spawn(unload_idle_models_loop(), name="unload_idle_models")
            _spawn(autoload_default_model(), name="autoload_default_model")
            if LOCAL_KG_WATCHER:
                restored = LOCAL_KG_WATCHER.restore_enabled_sources()
                if restored.get("restored"):
                    print(f"🕸️ Local knowledge watchers restored: {restored['restored']}")
        except Exception as e:
            print(f"⚠️ Startup sequence failed: {e}")
        try:
            yield
        finally:
            if LOCAL_KG_WATCHER:
                LOCAL_KG_WATCHER.stop_all()
            router.unload_all()
            for proc in LOCAL_SERVER_PROCESSES.values():
                try:
                    if proc.poll() is None:
                        proc.terminate()
                        proc.wait(timeout=5)
                except Exception:
                    pass

    app = FastAPI(title=f"Lattice AI Server ({APP_MODE})", version=APP_VERSION, lifespan=lifespan)

    CORS_ALLOWED_ORIGINS = [
        f"http://localhost:{DEFAULT_PORT}",
        f"http://127.0.0.1:{DEFAULT_PORT}",
        *CORS_EXTRA_ORIGINS,
    ]
    if CORS_ALLOW_NETWORK:
        CORS_ALLOWED_ORIGINS = CORS_ALLOWED_ORIGINS + [
            f"http://{DEFAULT_HOST}:{DEFAULT_PORT}",
            f"https://{DEFAULT_HOST}:{DEFAULT_PORT}",
        ]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ALLOWED_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
    )

    # UI 파일이 담길 static 폴더 연결
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    # PWA icons served at /icons/*
    _ICONS_DIR = STATIC_DIR / "icons"
    if _ICONS_DIR.exists():
        app.mount("/icons", StaticFiles(directory=str(_ICONS_DIR)), name="icons")
    ensure_agent_root()

    OPEN_REGISTRATION = CONFIG.open_registration
    INVITE_CODE = CONFIG.invite_code
    INVITE_GATE_ENABLED = CONFIG.invite_gate_enabled
    configure_model_runtime(
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
    STATIC_ROUTES = create_static_routes_router(
        static_dir=STATIC_DIR,
        invite_gate_enabled=INVITE_GATE_ENABLED,
        invite_code=INVITE_CODE,
        app_mode=APP_MODE,
        model_router=router,
        require_user=require_user,
    )
    ui_file_response = STATIC_ROUTES.ui_file_response
    local_sysinfo = STATIC_ROUTES.local_sysinfo
    app.include_router(STATIC_ROUTES.router)

    # ── Auth & Admin routers (latticeai.api) ─────────────────────────────────────
    app.include_router(create_auth_router(
        load_users=load_users, save_users=save_users,
        hash_password=hash_password, verify_and_migrate=verify_and_migrate_password,
        create_session=create_session, get_session_email=get_session_email,
        invalidate_session=invalidate_session, extract_bearer_token=_extract_bearer_token,
        get_user_role=get_user_role, require_user=require_user,
        check_ip_rate_limit=_check_rate_limit, client_ip=_client_ip,
        get_sso_settings=get_sso_settings, get_sso_discovery=_get_sso_discovery,
        public_sso_config=public_sso_config,
        open_registration=OPEN_REGISTRATION, session_ttl=_SESSION_TTL,
        require_auth=REQUIRE_AUTH,
    ))

    def _graph_stats_safe():
        try:
            return KNOWLEDGE_GRAPH.stats() if (ENABLE_GRAPH and KNOWLEDGE_GRAPH) else {"disabled": True}
        except Exception as e:
            return {"error": str(e)}

    app.include_router(create_admin_router(
        require_admin=require_admin, require_user=require_user,
        load_users=load_users, save_users=save_users,
        get_user_role=get_user_role, get_history=get_history,
        public_user=public_user, load_vpc_config=load_vpc_config,
        save_vpc_config=save_vpc_config,
        build_admin_audit_report=build_admin_audit_report,
        build_sensitivity_report=build_sensitivity_report,
        append_audit_event=append_audit_event,
        public_sso_config=public_sso_config, save_sso_config=save_sso_config,
        get_graph_stats=_graph_stats_safe, enable_graph=ENABLE_GRAPH,
        invite_code=INVITE_CODE, invite_gate_enabled=INVITE_GATE_ENABLED,
        default_port=DEFAULT_PORT,
    ))

    # ── Security & Audit Command Center (피드백 #5) ──────────────────────────────
    def _security_audit_events_safe() -> List[Dict]:
        try:
            return _get_audit_log(AUDIT_FILE)
        except Exception as e:
            logging.warning("security audit events load failed: %s", e)
            return []

    def _security_list_uploaded_files() -> List[Dict]:
        """Audit log에서 document_upload 이벤트를 가공해서 file 목록으로 노출."""
        files: List[Dict] = []
        for idx, e in enumerate(_security_audit_events_safe()):
            if e.get("event_type") != "document_upload":
                continue
            files.append({
                "file_id": str(e.get("filename") or idx),
                "filename": e.get("filename"),
                "user_email": e.get("user_email"),
                "user_nickname": e.get("user_nickname"),
                "uploaded_at": e.get("timestamp"),
                "ext": e.get("ext"),
                "bytes": e.get("bytes"),
                "sensitivity": e.get("sensitivity") or "none",
                "sensitive_labels": e.get("sensitive_labels") or [],
                "content_preview": e.get("content_preview"),
            })
        return files

    app.include_router(_create_security_router(
        require_admin=require_admin,
        get_history=get_history,
        get_audit_events=_security_audit_events_safe,
        classify_sensitive_message=classify_sensitive_message,
        build_sensitivity_report=build_sensitivity_report,
        list_uploaded_files=_security_list_uploaded_files,
        append_audit_event=append_audit_event,
    ))

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


    SEARCH_SERVICE = SearchService(graph_store=_workspace_graph())


    # ── Telegram chat mirror: registered only when ENABLE_TELEGRAM is truthy.
    # latticeai.api.chat no longer imports telegram_bot (a 45KB module that
    # mutates os.environ at import); it calls this injected callback instead.
    on_chat_message = None
    if ENABLE_TELEGRAM:
        def _telegram_chat_mirror(role: str, text: str, source: Optional[str] = None) -> None:
            from telegram_bot import broadcast_web_chat
            _spawn(broadcast_web_chat(role, text), name="telegram_broadcast")
        on_chat_message = _telegram_chat_mirror

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
    )
    app.state.context = context

    # ── Workspace OS + Organization router (latticeai.api.workspace, v1.2.0) ──────
    app.include_router(create_workspace_router(context))


    # ── v2 Agentic Workspace Platform: cross-system wiring ───────────────────────
    # All cross-subsystem closures live in latticeai.services.platform_runtime to
    # keep this assembly file lean; server_app only constructs it and mounts routers.
    PLATFORM = PlatformRuntime(
        store=WORKSPACE_OS,
        workspace_service=WORKSPACE_SERVICE,
        plugin_registry=PLUGIN_REGISTRY,
        get_current_user=get_current_user,
        workspace_graph=_workspace_graph,
        workspace_scope_from_request=_workspace_scope_from_request,
        get_tool_permission=get_tool_permission,
        hooks=HOOKS_REGISTRY,
    )

    # Single AgentRuntime boundary over the orchestrator + run store.
    AGENT_RUNTIME = AgentRuntime(
        store=WORKSPACE_OS,
        orchestrator_factory=PLATFORM.build_orchestrator,
        workspace_graph=_workspace_graph,
        append_audit_event=append_audit_event,
        hooks=HOOKS_REGISTRY,
    )

    # ── Hooks dispatch: bind real built-in runners ───────────────────────────────
    # The registry lists built-in hooks; binding a runner here makes them *execute*
    # real platform behaviour when fired (not a placeholder). Runners take a
    # HookContext and may mutate its payload, return a status dict, or block.
    # Bind a real runner to every built-in hook so none is a silent no-op.
    register_builtin_hook_runners(
        HOOKS_REGISTRY,
        append_audit_event=append_audit_event,
        get_tool_permission=get_tool_permission,
        classify_sensitive_message=classify_sensitive_message,
    )

    app.include_router(create_plugins_router(
        registry=PLUGIN_REGISTRY,
        require_user=require_user,
        require_admin=require_admin,
        append_audit_event=append_audit_event,
        register_skill=PLATFORM.register_plugin_skill,
        plugin_runners_factory=lambda: PLATFORM.plugin_capability_runners(None, None),
        ui_file_response=ui_file_response,
        static_dir=STATIC_DIR,
    ))

    app.include_router(create_workflow_designer_router(
        store=WORKSPACE_OS,
        require_user=require_user,
        get_current_user=get_current_user,
        gate_read=PLATFORM.gate_read,
        gate_write=PLATFORM.gate_write,
        workspace_graph=_workspace_graph,
        build_runners=PLATFORM.build_workflow_runners,
        append_audit_event=append_audit_event,
        ui_file_response=ui_file_response,
        static_dir=STATIC_DIR,
        hooks=HOOKS_REGISTRY,
    ))

    app.include_router(create_agents_router(
        store=WORKSPACE_OS,
        orchestrator_factory=PLATFORM.build_orchestrator,
        require_user=require_user,
        get_current_user=get_current_user,
        gate_read=PLATFORM.gate_read,
        gate_write=PLATFORM.gate_write,
        workspace_graph=_workspace_graph,
        append_audit_event=append_audit_event,
        ui_file_response=ui_file_response,
        static_dir=STATIC_DIR,
        agent_runtime=AGENT_RUNTIME,
    ))

    app.include_router(create_marketplace_router(
        store=WORKSPACE_OS,
        catalog=TEMPLATE_CATALOG,
        require_user=require_user,
        gate_read=PLATFORM.gate_read,
        gate_write=PLATFORM.gate_write,
        workspace_graph=_workspace_graph,
    ))

    app.include_router(create_realtime_router(
        bus=REALTIME_BUS,
        require_user=require_user,
        get_current_user=get_current_user,
        allowed_scopes=PLATFORM.allowed_scopes,
        ui_file_response=ui_file_response,
        static_dir=STATIC_DIR,
    ))


    # ── Health & Info ──────────────────────────────────────────────────────────────

    # ── Model runtime/provider helpers moved to latticeai.services.model_runtime ──
    # ── Health / status / engine-summary router (latticeai.api.health, v1.2.0) ───
    # /health, /mode, /runtime_features, /engines(GET) now live in the health router.
    # Heavier engine mutation endpoints remain below in server_app.
    MODEL_SERVICE = ModelService(
        model_router=router,
        runtime_features=runtime_features,
        is_public=IS_PUBLIC_MODE,
    )
    app.include_router(create_health_router(
        model_service=MODEL_SERVICE,
        engine_status=engine_status,
        get_current_user=get_current_user,
        require_auth=REQUIRE_AUTH,
        app_version=APP_VERSION,
        app_mode=APP_MODE,
    ))


    # ── Model / Engine router (latticeai.api.models, v1.3.0) ─────────────────────
    app.include_router(create_models_router(
        model_router=router,
        require_user=require_user,
        get_current_user=get_current_user,
        load_users=load_users,
        get_user_role=get_user_role,
        install_engine=install_engine,
        verify_cloud_models=verify_cloud_models,
        normalize_local_model_request=normalize_local_model_request,
        download_hf_model=download_hf_model,
        prepare_and_load_model=prepare_and_load_model,
        prepare_and_load_model_stream=prepare_and_load_model_stream,
        sse_event=sse_event,
        ensure_ollama_server=ensure_ollama_server,
        local_binary=local_binary,
        engine_status=engine_status,
        filter_lower_family_versions=filter_lower_family_versions,
        list_compat_profiles=_list_compat_profiles,
        set_user_api_key=set_user_api_key,
        engine_model_catalog=ENGINE_MODEL_CATALOG,
        model_engine_aliases=MODEL_ENGINE_ALIASES,
        cloud_verify_ttl_seconds=CLOUD_VERIFY_TTL_SECONDS,
        is_public_mode=IS_PUBLIC_MODE,
        allow_local_models=ALLOW_LOCAL_MODELS,
        require_auth=REQUIRE_AUTH,
    ))


    # ── Chat / Completion ──────────────────────────────────────────────────────────

    app.include_router(create_chat_router(context))

    def _embedding_info() -> dict:
        from latticeai.core.embedding_providers import PROVIDER_TYPES, embedding_provider_profiles
        info = EMBEDDER.as_dict()
        info["available_providers"] = list(PROVIDER_TYPES)
        info["profile"] = CONFIG.embedding_profile or ""
        info["profiles"] = embedding_provider_profiles()
        return info


    app.include_router(create_search_router(
        service=SEARCH_SERVICE,
        require_user=require_user,
        embedding_info=_embedding_info,
    ))

    app.include_router(create_tools_router(
        config=CONFIG,
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
    ))

    app.include_router(create_hooks_router(
        registry=HOOKS_REGISTRY,
        require_user=require_user,
        append_audit_event=append_audit_event,
    ))

    app.include_router(create_agent_registry_router(
        registry=AGENT_REGISTRY,
        require_user=require_user,
        append_audit_event=append_audit_event,
    ))

    app.include_router(create_memory_router(
        service=MEMORY_SERVICE,
        require_user=require_user,
        get_current_user=get_current_user,
        gate_read=PLATFORM.gate_read,
        gate_write=PLATFORM.gate_write,
        append_audit_event=append_audit_event,
    ))

    app.include_router(create_browser_router(
        pipeline=INGESTION_PIPELINE,
        require_user=require_user,
    ))

    app.include_router(create_portability_router(
        service=KG_PORTABILITY,
        require_user=require_user,
        require_admin=require_admin,
    ))

    app.include_router(create_garden_router(gardener=gardener, require_user=require_user))
    app.include_router(create_setup_router(model_router=router, require_user=require_user))

    # ── Entry Point ────────────────────────────────────────────────────────────────

    def main() -> None:
        print(f"🧠 Lattice AI Server starting in {APP_MODE} mode on http://{DEFAULT_HOST}:{DEFAULT_PORT}")
        uvicorn.run(app, host=DEFAULT_HOST, port=DEFAULT_PORT, log_level="info")

    # ── Constructed-namespace export (consumed by AppRuntime) ────────────────
    # Every local — singletons, helper functions, request models — becomes an
    # attribute of the runtime so the legacy ``server_app`` surface survives.
    return dict(locals())


class AppRuntime:
    """The constructed application namespace.

    Exposes every name the legacy import-time ``server_app`` module defined
    (``app``, ``KNOWLEDGE_GRAPH``, ``load_users``, …) as attributes.
    """

    def __init__(self, namespace: Dict[str, Any]) -> None:
        self.__dict__.update(namespace)


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
