"""
Lattice AI MLX — Local LLM Bridge Server
Apple Silicon (M1-M5) 전용 | mlx-lm 기반
"""

import asyncio
import base64
import hashlib
import importlib.util
import io
import json
import logging
import os
import platform
import queue
import re
import secrets
import threading
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import ipaddress
from contextlib import asynccontextmanager
from pathlib import Path

try:
    import mlx.core as mx
    mx.set_default_device(mx.gpu)
    print("✅ MLX Metal context initialized in main thread.")
except Exception as e:
    print(f"⚠️ MLX Metal context unavailable: {e}")
    mx = None
from typing import AsyncIterator, Optional, List, Dict

import uvicorn
from fastapi import FastAPI, File, HTTPException, Request, Cookie, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from PIL import Image

from llm_router import AsyncOpenAI, LLMRouter, OPENAI_COMPATIBLE_PROVIDERS, HF_MODELS_ROOT, ensure_mlx_runtime, hf_model_dir, parse_model_ref, mx, normalize_branding
from knowledge_graph import KnowledgeGraphStore, set_llm_router
from knowledge_graph_api import create_knowledge_graph_router
from latticeai.core.context_builder import retrieve_context_for_generation, format_sources_footnote
from latticeai.core.document_generator import detect_document_intent, DocumentGenerationSession
from local_knowledge_api import LocalKnowledgeWatcher, create_local_knowledge_router
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
from latticeai.core.sessions import SessionStore as _SessionStore
from latticeai.core.audit import (
    get_audit_log as _get_audit_log,
    append_audit_event as _append_audit_event,
    classify_sensitive_message as _classify_sensitive_message,
    mask_sensitive_text as _mask_sensitive_text,
    build_sensitivity_report as _build_sensitivity_report,
    build_admin_audit_report as _build_admin_audit_report,
)
from latticeai.api.auth import create_auth_router
from latticeai.api.admin import create_admin_router
from latticeai.api.security_dashboard import create_security_router as _create_security_router
from latticeai.core.model_compat import (
    ensure_profile as _ensure_compat_profile,
    record_smoke_result as _record_smoke_result,
    fast_postprocess as _compat_fast_postprocess,
    validate_smoke_response as _validate_smoke_response,
    classify_smoke_response as _classify_smoke_response,
    list_cached_profiles as _list_compat_profiles,
    SMOKE_PROMPT as _SMOKE_PROMPT,
)
from latticeai.core.model_resolution import (
    ModelResolution as _ModelResolution,
    PrepareState as _PrepareState,
    PrepareReport as _PrepareReport,
)
from latticeai.core.graph_curator import (
    auto_build_graph_overlay as _auto_build_graph_overlay,
    mask_secrets as _curator_mask_secrets,
)
from latticeai.core.config import Config
from latticeai.core.agent import (
    AgentState,
    AgentRunContext,
    AGENT_TERMINAL_STATES,
    AgentDeps,
    AgentRuntime,
    extract_action as _extract_agent_action,
)
from latticeai.core.agent_prompts import (
    AGENT_SYSTEM_PROMPT,
    CRITIC_PROMPT,
    EXECUTOR_PROMPT,
    MEMORY_UPDATER_PROMPT,
    PLANNER_PROMPT,
)
from latticeai.core.tool_registry import (
    MCP_TOOL_DESCRIPTIONS,
    ToolPermission,
    ToolPolicy,
    TOOL_CATALOG_BRIEF as _TOOL_CATALOG_BRIEF,
)
import mcp_registry
from mcp_registry import (
    MCP_REGISTRY, _THIRD_PARTY_SKILL_SOURCES, _KNOWN_REPO_LICENSES,
    _MARKETPLACE_RAW, _MARKETPLACE_API,
    _fetch_remote_mcp_registry, _get_combined_registry,
    _extract_skill_desc, _fetch_plugin_skills,
    _fetch_skills_marketplace, _fetch_plugin_directory,
    _OPEN_LICENSES, install_skill, SKILLS_DIR,
)
from p_reinforce import BRAIN_DIR, PReinforceGardener
from setup import get_recommendations, install_stream, open_url, scan_environment
from auto_setup import (
    plan as auto_setup_plan,
    preset as auto_setup_preset,
    probe as auto_setup_probe,
    recommend as auto_setup_recommend,
    verify as auto_setup_verify,
)
from telegram_bot import broadcast_web_chat
from tools import (
    AGENT_ROOT,
    DEFAULT_TOOL_REGISTRY,
    ToolError,
    build_project,
    computer_click,
    computer_drag,
    computer_key,
    computer_move,
    computer_open_app,
    computer_open_url,
    computer_screenshot,
    computer_scroll,
    computer_status,
    computer_type,
    create_docx,
    create_pdf,
    create_pptx,
    create_xlsx,
    read_document,
    deploy_project,
    desktop_bridge_status,
    edit_file,
    ensure_agent_root,
    execute_tool,
    git_diff,
    git_log,
    git_show,
    git_status,
    grep,
    inspect_html,
    knowledge_save,
    knowledge_search,
    knowledge_tree,
    list_dir,
    local_list,
    local_read,
    local_write,
    network_status,
    obsidian_save,
    obsidian_search,
    obsidian_tree,
    preview_url,
    read_file,
    run_command,
    search_files,
    todo_read,
    todo_write,
    workspace_tree,
    write_file,
)

try:
    import keyring
except Exception:
    keyring = None

from datetime import datetime, timedelta
import httpx

def detect_language(text: str) -> str:
    """Detect language: 'ko' (Korean) or 'en' (English)."""
    total = max(len(text), 1)
    ko = sum(1 for c in text if '가' <= c <= '힣')
    if ko / total > 0.05:
        return "ko"
    return "en"

_LANG_HINT = {
    "ko": "Respond in Korean (한국어로 답변하세요).",
    "en": "Respond in English.",
}

def is_network_status_request(text: str) -> bool:
    """사용자가 현재 IP/네트워크 정보를 물었는지 감지합니다."""
    t = (text or "").lower()
    has_ip = bool(re.search(r"((?<![a-z0-9])ip(?![a-z0-9])|아이피|ip\s*주소|아이피\s*주소|ipconfig|ifconfig|네트워크)", t))
    asks_current = any(word in t for word in ["내", "현재", "지금", "local", "로컬", "주소", "address", "뭐", "알려", "확인", "상태"])
    return has_ip and asks_current

def is_current_url_request(text: str) -> bool:
    t = (text or "").lower()
    has_url = any(word in t for word in ["url", "주소", "링크", "address"])
    asks_current = any(word in t for word in ["현재", "지금", "여기", "접속", "페이지", "브라우저", "알려", "뭐"])
    return has_url and asks_current

def is_clear_command(text: str) -> bool:
    return (text or "").strip().lower() in {"/clear", "/clear_all"}

def format_network_status(info: Dict) -> str:
    lines = [
        f"내부 IP: {info.get('local_ip') or '확인 안 됨'}",
        f"외부 IP: {info.get('public_ip') or '확인 안 됨'}",
        f"호스트명: {info.get('hostname') or '확인 안 됨'}",
    ]
    local_ips = info.get("local_ips") or {}
    if local_ips:
        lines.extend(["", "인터페이스:"])
        lines.extend(f"- {name}: {ip}" for name, ip in local_ips.items())
    note = info.get("note")
    if note:
        lines.extend(["", note])
    return "\n".join(lines)

async def single_text_stream(text: str, model: str = "system") -> AsyncIterator[str]:
    yield f"data: {json.dumps({'chunk': text, 'model': model}, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"

# ── App-level config — parsed once, in one place (latticeai.core.config) ──────
# The module-level names below are kept as a compatibility surface for the rest
# of server.py; all of them are now derived from a single CONFIG instance.
CONFIG = Config.from_env()

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
    global _sso_discovery_cache, _sso_discovery_cache_url
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
KNOWLEDGE_GRAPH = KnowledgeGraphStore(DATA_DIR / "knowledge_graph.sqlite", DATA_DIR / "knowledge_graph_blobs") if ENABLE_GRAPH else None
LOCAL_KG_WATCHER = LocalKnowledgeWatcher(lambda: KNOWLEDGE_GRAPH) if ENABLE_GRAPH else None

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
    global _sso_discovery_cache, _sso_discovery_cache_url
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

class McpRecommendRequest(BaseModel):
    query: str
    limit: int = 5

class McpInstallRequest(BaseModel):
    mcp_id: str

class McpCustomRequest(BaseModel):
    name: str
    package: str
    description: str = ""
    category: str = "custom"
    icon: str = "🔌"
    env_vars: List[Dict] = []

class SkillInstallRequest(BaseModel):
    plugin: str
    skill: str

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

def build_recent_chat_context(
    limit: int = 10,
    include_image_missing_replies: bool = True,
    user_email: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> str:
    history = get_history()
    if conversation_id:
        history = [item for item in history if item.get("conversation_id") == conversation_id]
    if user_email:
        history = [item for item in history if item.get("user_email") == user_email or item.get("role") == "assistant"]
    history = history[-limit:]
    lines = []
    for item in history:
        role = item.get("role", "user")
        content = item.get("content", "")
        if not include_image_missing_replies and role == "assistant":
            if "이미지" in content and any(word in content for word in ["업로드", "제공", "올려"]):
                continue
        source = item.get("source")
        label = role
        if source:
            label = f"{role} ({source})"
        lines.append(f"{label}: {content}")
    return "\n".join(lines)

def extract_screenshot_context(image_data: Optional[str]) -> str:
    if not image_data:
        return ""

    lines = ["[SCREENSHOT INGESTION]"]
    image_bytes = b""
    try:
        image_bytes = base64.b64decode(image_data)
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        lines.append(f"- image_size: {image.width}x{image.height}")
        lines.append(f"- image_mode: {image.mode}")
    except Exception as e:
        lines.append(f"- image_decode_error: {e}")
        return "\n".join(lines)

    tesseract_path = shutil.which("tesseract")
    if not tesseract_path:
        lines.append("- ocr: unavailable; install `tesseract` to enable OCR text extraction.")
        return "\n".join(lines)

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(prefix="ltcai-screenshot-", suffix=".png", delete=False) as temp:
            temp.write(image_bytes)
            temp_path = temp.name

        ocr_text = ""
        for lang in ("kor+eng", "eng"):
            completed = subprocess.run(
                [tesseract_path, temp_path, "stdout", "-l", lang, "--psm", "6"],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            if completed.returncode == 0 and completed.stdout.strip():
                ocr_text = completed.stdout.strip()
                lines.append(f"- ocr_language: {lang}")
                break

        if ocr_text:
            lines.append("- ocr_text:")
            lines.append(ocr_text[:4000])
        else:
            lines.append("- ocr: no text extracted.")
    except Exception as e:
        lines.append(f"- ocr_error: {e}")
    finally:
        if temp_path:
            try:
                Path(temp_path).unlink()
            except OSError:
                pass

    return "\n".join(lines)

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

def require_admin(request: Request) -> tuple[str, Dict]:
    users = load_users()
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
gardener = PReinforceGardener()
_doc_gen_sessions: dict = {}  # conversation_id → DocumentGenerationSession

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

app = FastAPI(title=f"Lattice AI Server ({APP_MODE})", version="0.5.1", lifespan=lifespan)

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

def ui_file_response(path: Path) -> FileResponse:
    response = FileResponse(path)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

@app.get("/")
async def root(request: Request, code: Optional[str] = None, authorized: Optional[str] = Cookie(None)):
    """로그인/회원가입 페이지. 초대 게이트 활성화 시 코드 검증 후 진입."""
    if not INVITE_GATE_ENABLED:
        return ui_file_response(STATIC_DIR / "account.html")

    # 1. 이미 쿠키로 인증된 경우
    if authorized == "true":
        return ui_file_response(STATIC_DIR / "account.html")

    # 2. 초대 코드가 일치하는 경우 (최초 진입)
    if code == INVITE_CODE:
        response = ui_file_response(STATIC_DIR / "account.html")
        response.set_cookie(key="authorized", value="true", httponly=True, samesite="lax", max_age=60*60*24*7)
        return response

    # 3. 인증 실패 시 차단 화면
    return HTMLResponse(content=f"""
        <body style="background:#0f1115; color:white; display:flex; flex-direction:column; align-items:center; justify-content:center; height:100vh; font-family:sans-serif;">
            <div style="background:#16191f; padding:40px; border-radius:24px; border:1px solid rgba(255,255,255,0.1); text-align:center; box-shadow: 0 20px 40px rgba(0,0,0,0.5);">
                <div style="font-size:48px; margin-bottom:20px;">🔒</div>
                <h1 style="color:#378ADD; margin:0; font-size:24px;">Invitation Required</h1>
                <p style="color:#94a3b8; margin:20px 0; line-height:1.6;">이 서비스는 비공개로 운영되고 있습니다.<br>선생님께 받은 <b>초대용 전용 링크</b>를 통해 접속해 주세요.</p>
                <div style="margin-top:30px; padding-top:20px; border-top:1px solid rgba(255,255,255,0.05); font-size:11px; color:rgba(255,255,255,0.2); letter-spacing:1px;">LATTICE AI</div>
            </div>
        </body>
    """, status_code=403)


@app.get("/account")
async def account_page():
    """Direct login/register page route used by logout and manual navigation."""
    return ui_file_response(STATIC_DIR / "account.html")


@app.get("/manifest.json")
async def manifest():
    p = STATIC_DIR / "manifest.json"
    if not p.exists():
        raise HTTPException(status_code=404)
    return FileResponse(str(p), media_type="application/manifest+json")


@app.get("/sw.js")
async def service_worker():
    p = STATIC_DIR / "sw.js"
    if not p.exists():
        raise HTTPException(status_code=404)
    resp = FileResponse(str(p), media_type="application/javascript")
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@app.get("/chat")
async def chat_page(request: Request):
    return ui_file_response(STATIC_DIR / "chat.html")


@app.get("/admin")
async def admin_page():
    admin_path = STATIC_DIR / "admin.html"
    if not admin_path.exists():
        raise HTTPException(status_code=404, detail="Admin UI not found.")
    response = FileResponse(admin_path)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response

@app.get("/status")
async def status():
    """서버 상태 및 현재 로드된 모델 정보를 반환합니다."""
    return {
        "message": "🧠 Lattice AI MLX Server is running!",
        "status": "online",
        "mode": APP_MODE,
        "loaded_model": router._current or "None"
    }


@app.get("/local/sysinfo")
async def local_sysinfo(request: Request):
    """CPU / RAM / GPU(MLX) 사용량을 반환합니다."""
    require_user(request)
    import subprocess, re as _re
    result = {"cpu_pct": 0.0, "ram_pct": 0.0, "gpu_mem_pct": 0.0, "gpu_mem_gb": 0.0}
    try:
        # CPU
        top_out = subprocess.run(["top", "-l", "1", "-n", "0"], capture_output=True, text=True, timeout=4).stdout
        for line in top_out.splitlines():
            if "CPU usage" in line:
                m = _re.search(r"([\d.]+)% user.*?([\d.]+)% sys", line)
                if m:
                    result["cpu_pct"] = round(float(m.group(1)) + float(m.group(2)), 1)
        # RAM
        vm_out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=4).stdout
        page_size = 16384
        pages: dict = {}
        for line in vm_out.splitlines():
            for key in ["Pages free", "Pages active", "Pages inactive", "Pages wired down", "Pages occupied by compressor"]:
                if line.startswith(key):
                    m = _re.search(r"(\d+)", line)
                    if m:
                        pages[key] = int(m.group(1))
        total = sum(pages.values())
        used  = total - pages.get("Pages free", 0)
        result["ram_pct"] = round(used / total * 100, 1) if total else 0.0
        # GPU (MLX / Apple Silicon unified memory)
        try:
            import mlx.core as _mx
            hw_out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=2).stdout
            total_bytes = int(hw_out.strip())
            gpu_bytes = _mx.get_active_memory() + _mx.get_cache_memory()
            result["gpu_mem_gb"]  = round(gpu_bytes / (1024 ** 3), 2)
            result["gpu_mem_pct"] = round(gpu_bytes / total_bytes * 100, 1) if total_bytes else 0.0
        except Exception:
            pass
    except Exception as e:
        result["error"] = str(e)
    return result




# ── Request / Response Models ──────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    client_url: Optional[str] = None
    model: Optional[str] = None
    max_tokens: int = 2048
    temperature: float = 0.2
    stream: bool = True
    context: Optional[str] = None
    source: Optional[str] = None
    user_email: Optional[str] = None
    user_nickname: Optional[str] = None
    image_data: Optional[str] = None     # Base64 이미지 데이터 (VLM용)


class LoadModelRequest(BaseModel):
    model_id: str                         # HuggingFace repo id 또는 로컬 경로
    adapter_path: Optional[str] = None   # LoRA adapter (선택)
    draft_model_id: Optional[str] = None # Speculative decoding draft model (선택)
    engine: Optional[str] = None
    user_email: Optional[str] = None


class InstallEngineRequest(BaseModel):
    engine: str


class SetApiKeyRequest(BaseModel):
    provider: str
    key: str
    user_email: Optional[str] = None


class PullModelRequest(BaseModel):
    model: str

class PrepareModelRequest(BaseModel):
    model: str
    engine: Optional[str] = None
    user_email: Optional[str] = None

class VerifyCloudRequest(BaseModel):
    force: bool = False
    provider: Optional[str] = None


class GardenRequest(BaseModel):
    raw_data: str
    category: Optional[str] = None       # 10_Wiki / 00_Raw / Skills


class AgentRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    source: Optional[str] = None
    max_steps: int = 25
    temperature: float = 0.1
    user_email: Optional[str] = None
    user_nickname: Optional[str] = None
    # Multi-LLM pipeline: per-phase model override (None = use current loaded model)
    planning_model: Optional[str] = None
    executing_model: Optional[str] = None
    reviewing_model: Optional[str] = None
    # When True: pause after planning and wait for /agent/resume
    human_in_loop: bool = False


class AgentResumeRequest(BaseModel):
    context_id: str
    approved: bool = True
    modified_plan: Optional[dict] = None
    executing_model: Optional[str] = None
    reviewing_model: Optional[str] = None


class AgentEvalRequest(BaseModel):
    skill: str
    case_id: Optional[str] = None


# AgentState / AgentRunContext / AGENT_TERMINAL_STATES are defined in
# latticeai.core.agent and imported at the top of this module.

# Pending agent contexts waiting for human approval: context_id → (ctx, req, lang_hint, current_user)
_pending_agents: dict[str, tuple] = {}
_pending_agents_lock = threading.Lock()


class ToolPathRequest(BaseModel):
    path: str = "."
    approval_token: Optional[str] = None


class ToolWriteFileRequest(BaseModel):
    path: str
    content: str


class ToolRunCommandRequest(BaseModel):
    command: str
    cwd: Optional[str] = "."


class ToolScriptRequest(BaseModel):
    cwd: Optional[str] = "."
    script: str = "build"


class ToolSearchFilesRequest(BaseModel):
    query: str
    path: str = "."
    max_results: int = 20


class ToolReadFileRequest(BaseModel):
    path: str
    offset: int = 0
    limit: int = 0
    line_numbers: bool = True


class ToolEditFileRequest(BaseModel):
    path: str
    old_string: str
    new_string: str
    replace_all: bool = False


class ToolGrepRequest(BaseModel):
    pattern: str
    path: str = "."
    glob: Optional[str] = None
    max_results: int = 50
    case_insensitive: bool = False
    context_lines: int = 0


class ToolTodoWriteRequest(BaseModel):
    todos: List[Dict] = []


class ToolWorkspaceTreeRequest(BaseModel):
    path: str = "."
    max_depth: int = 3


class ToolClearHistoryRequest(BaseModel):
    keep_last: int = 0


class ToolKnowledgeSaveRequest(BaseModel):
    content: str
    folder: str = "00_Raw"
    title: Optional[str] = None


class ToolKnowledgeSearchRequest(BaseModel):
    query: str
    max_results: int = 5


class ToolDocxRequest(BaseModel):
    title: str = ""
    body: str = ""
    filename: str = "document.docx"


class ToolXlsxRequest(BaseModel):
    rows: List[List] = []
    filename: str = "spreadsheet.xlsx"
    sheet_name: str = "Sheet1"


class ToolPptxRequest(BaseModel):
    title: str = ""
    slides: List[Dict] = []
    filename: str = "presentation.pptx"


class ToolPdfRequest(BaseModel):
    title: str = ""
    body: str = ""
    filename: str = "document.pdf"


class LocalAccessRequest(BaseModel):
    path: str
    approved: bool = False
    approval_token: Optional[str] = None


class LocalWriteRequest(BaseModel):
    path: str
    content: str
    approved: bool = False
    approval_token: Optional[str] = None


class McpCallRequest(BaseModel):
    action: str
    args: Dict = {}


class ToolGitDiffRequest(BaseModel):
    path: Optional[str] = None
    cwd: Optional[str] = "."


class ToolGitLogRequest(BaseModel):
    max_count: int = 5
    cwd: Optional[str] = "."


class ToolGitShowRequest(BaseModel):
    revision: str = "HEAD"
    cwd: Optional[str] = "."


# ── Health & Info ──────────────────────────────────────────────────────────────

ENGINE_INSTALLERS = {
    "local_mlx": {
        "command": [sys.executable, "-m", "pip", "install", "--upgrade", "mlx-lm", "mlx-vlm", "huggingface_hub[cli]"],
        "label": "Install MLX runtime",
    },
    "openai": {
        "command": [sys.executable, "-m", "pip", "install", "openai"],
        "label": "Install OpenAI-compatible SDK",
    },
    "openrouter": {
        "command": [sys.executable, "-m", "pip", "install", "openai"],
        "label": "Install OpenAI-compatible SDK",
    },
    "groq": {
        "command": [sys.executable, "-m", "pip", "install", "openai"],
        "label": "Install OpenAI-compatible SDK",
    },
    "together": {
        "command": [sys.executable, "-m", "pip", "install", "openai"],
        "label": "Install OpenAI-compatible SDK",
    },
    "xai": {
        "command": [sys.executable, "-m", "pip", "install", "openai"],
        "label": "Install OpenAI-compatible SDK",
    },
    "ollama": {
        "command": ["brew", "install", "ollama"],
        "label": "Install Ollama",
        "requires_binary": "brew",
    },
    "vllm": {
        "command": [sys.executable, "-m", "pip", "install", "vllm", "huggingface_hub[cli]"],
        "label": "Install vLLM runtime",
    },
    "lmstudio": {
        "command": ["brew", "install", "--cask", "lm-studio"],
        "label": "Install LM Studio",
        "requires_binary": "brew",
    },
    "llamacpp": {
        "command": ["brew", "install", "llama.cpp"],
        "label": "Install llama.cpp",
        "requires_binary": "brew",
    },
}

ENGINE_MODEL_CATALOG = {
    "local_mlx": [
        {"id": "mlx-community/SmolLM-1.7B-Instruct-4bit", "name": "SmolLM 1.7B", "family": "SmolLM", "tag": "local-light", "size": "963MB", "pullable": True},
        {"id": "mlx-community/gemma-3-1b-it-4bit", "name": "Gemma 3 1B", "family": "Gemma 3", "tag": "local-light", "size": "733MB", "pullable": True},
        {"id": "mlx-community/Llama-3.2-1B-Instruct-4bit", "name": "Llama 3.2 1B", "family": "Llama 3.x", "tag": "local-light", "size": "1.3GB", "pullable": True},
        {"id": "mlx-community/gemma-2-2b-it-4bit", "name": "Gemma 2 2B", "family": "Gemma 2", "tag": "local-light", "size": "1.6GB", "pullable": True},
        {"id": "mlx-community/gemma-4-e2b-4bit", "name": "Gemma 4 E2B Base", "family": "Gemma 4", "tag": "local-vlm", "size": "3.6GB", "pullable": True},
        {"id": "mlx-community/gemma-4-e2b-it-4bit", "name": "Gemma 4 E2B Instruct", "family": "Gemma 4", "tag": "local-vlm", "size": "3.6GB", "pullable": True},
        {"id": "mlx-community/gemma-4-e4b-4bit", "name": "Gemma 4 E4B Base", "family": "Gemma 4", "tag": "local-vlm", "size": "5.2GB", "pullable": True},
        {"id": "mlx-community/gemma-4-e4b-it-4bit", "name": "Gemma 4 E4B Instruct", "family": "Gemma 4", "tag": "local-vlm", "size": "5.2GB", "pullable": True},
        {"id": "mlx-community/Qwen3-VL-4B-Instruct-4bit", "name": "Qwen3-VL 4B", "family": "Qwen3-VL", "tag": "local-vlm", "size": "2.7GB", "pullable": True},
        {"id": "mlx-community/Qwen3-VL-8B-Instruct-4bit", "name": "Qwen3-VL 8B", "family": "Qwen3-VL", "tag": "local-vlm", "size": "4.8GB", "pullable": True},
        {"id": "mlx-community/Qwen2.5-VL-7B-Instruct-4bit", "name": "Qwen2.5-VL 7B", "family": "Qwen2.5-VL", "tag": "local-vlm", "size": "4.4GB", "pullable": True},
        {"id": "mlx-community/gemma-3-4b-it-4bit", "name": "Gemma 3 4B", "family": "Gemma 3", "tag": "local-vlm", "size": "3.3GB", "pullable": True},
        {"id": "mlx-community/Llama-3.2-3B-Instruct-4bit", "name": "Llama 3.2 3B", "family": "Llama 3.x", "tag": "local-general", "size": "2.0GB", "pullable": True},
        {"id": "mlx-community/Llama-3.1-8B-Instruct-4bit", "name": "Llama 3.1 8B", "family": "Llama 3.1", "tag": "local-general", "size": "4.7GB", "pullable": True},
        {"id": "mlx-community/gemma-2-9b-it-4bit", "name": "Gemma 2 9B", "family": "Gemma 2", "tag": "local-general", "size": "5.4GB", "pullable": True},
        {"id": "mlx-community/gemma-3-12b-it-4bit", "name": "Gemma 3 12B", "family": "Gemma 3", "tag": "local-vlm", "size": "8.0GB", "pullable": True},
        {"id": "mlx-community/Phi-3.5-mini-instruct-4bit", "name": "Phi 3.5 Mini", "family": "Phi", "tag": "local-coding", "size": "2.2GB", "pullable": True},
        {"id": "mlx-community/Phi-4-mini-instruct-4bit", "name": "Phi 4 Mini", "family": "Phi", "tag": "local-coding", "size": "2.2GB", "pullable": True},
        {"id": "mlx-community/phi-4-4bit", "name": "Phi 4", "family": "Phi", "tag": "local-coding", "size": "8.3GB", "pullable": True},
        {"id": "mlx-community/Mistral-7B-Instruct-v0.3-4bit", "name": "Mistral 7B Instruct v0.3", "family": "Mistral", "tag": "local-general", "size": "4.1GB", "pullable": True},
        {"id": "mlx-community/Ministral-8B-Instruct-2410-4bit", "name": "Ministral 8B Instruct", "family": "Mistral", "tag": "local-general", "size": "4.5GB", "pullable": True},
        {"id": "mlx-community/Mistral-Small-24B-Instruct-2501-4bit", "name": "Mistral Small 24B", "family": "Mistral", "tag": "local-large", "size": "13.3GB", "pullable": True},
        {"id": "mlx-community/Qwen2.5-Coder-32B-Instruct-4bit", "name": "Qwen2.5 Coder 32B", "family": "Qwen2.5", "tag": "local-coding", "size": "18.5GB", "pullable": True},
        {"id": "mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit", "name": "Qwen3-VL 30B A3B", "family": "Qwen3-VL", "tag": "local-vlm", "size": "18GB", "pullable": True},
        {"id": "mlx-community/gemma-3-27b-it-4bit", "name": "Gemma 3 27B", "family": "Gemma 3", "tag": "local-vlm", "size": "17GB", "pullable": True},
        {"id": "mlx-community/gemma-4-26b-a4b-it-4bit", "name": "Gemma 4 26B A4B Instruct", "family": "Gemma 4", "tag": "local-vlm", "size": "15.6GB", "pullable": True},
        {"id": "mlx-community/gemma-4-31b-it-4bit", "name": "Gemma 4 31B Instruct", "family": "Gemma 4", "tag": "local-vlm", "size": "18.4GB", "pullable": True},
        {"id": "mlx-community/gpt-oss-20b-MXFP4-Q8", "name": "GPT-OSS 20B", "family": "GPT-OSS", "tag": "local-reasoning", "size": "12.1GB", "pullable": True},
        {"id": "mlx-community/gpt-oss-120b-MXFP4-Q4", "name": "GPT-OSS 120B", "family": "GPT-OSS", "tag": "local-large", "size": "62.3GB", "pullable": True},
        {"id": "mlx-community/Llama-3.3-70B-Instruct-4bit", "name": "Llama 3.3 70B", "family": "Llama 3.x", "tag": "local-general", "size": "40GB+", "pullable": True},
        {"id": "mlx-community/Llama-3.1-70B-Instruct-4bit", "name": "Llama 3.1 70B", "family": "Llama 3.1", "tag": "local-general", "size": "40GB+", "pullable": True},
    ],
    "ollama": [
        {"id": "ollama:qwen3-vl:4b", "name": "Qwen3-VL 4B via Ollama", "family": "Qwen3-VL", "tag": "local-vlm", "size": "pull required", "pullable": True},
        {"id": "ollama:qwen3-vl:8b", "name": "Qwen3-VL 8B via Ollama", "family": "Qwen3-VL", "tag": "local-vlm", "size": "pull required", "pullable": True},
        {"id": "ollama:qwen3-vl:30b", "name": "Qwen3-VL 30B via Ollama", "family": "Qwen3-VL", "tag": "local-vlm", "size": "pull required", "pullable": True},
        {"id": "ollama:gpt-oss:20b", "name": "GPT-OSS 20B via Ollama", "family": "GPT-OSS", "tag": "local-reasoning", "size": "pull required", "pullable": True},
        {"id": "ollama:gpt-oss:120b", "name": "GPT-OSS 120B via Ollama", "family": "GPT-OSS", "tag": "local-large", "size": "pull required", "pullable": True},
        {"id": "ollama:hf.co/ggml-org/gemma-4-31B-it-GGUF:Q4_K_M", "name": "Gemma 4 31B Q4 via Ollama", "family": "Gemma 4", "tag": "local-vlm", "size": "18.7GB", "pullable": True},
        {"id": "ollama:qwen3:8b", "name": "Qwen3 8B via Ollama", "family": "Qwen", "tag": "local-server", "size": "pull required", "pullable": True},
        {"id": "ollama:qwen2.5-coder:14b", "name": "Qwen2.5 Coder 14B via Ollama", "family": "Qwen", "tag": "local-coding", "size": "pull required", "pullable": True},
        {"id": "ollama:gemma3:1b", "name": "Gemma 3 1B via Ollama", "family": "Gemma", "tag": "local-light", "size": "pull required", "pullable": True},
        {"id": "ollama:gemma3:4b", "name": "Gemma 3 4B via Ollama", "family": "Gemma", "tag": "local-server", "size": "pull required", "pullable": True},
        {"id": "ollama:gemma3:4b-it-q4_K_M", "name": "Gemma 3 4B q4_K_M via Ollama", "family": "Gemma", "tag": "quantized", "size": "pull required", "pullable": True},
        {"id": "ollama:gemma3:12b", "name": "Gemma 3 12B via Ollama", "family": "Gemma", "tag": "local-server", "size": "pull required", "pullable": True},
        {"id": "ollama:gemma3:12b-it-q4_K_M", "name": "Gemma 3 12B q4_K_M via Ollama", "family": "Gemma", "tag": "quantized", "size": "pull required", "pullable": True},
        {"id": "ollama:gemma3:27b", "name": "Gemma 3 27B via Ollama", "family": "Gemma", "tag": "local-large", "size": "pull required", "pullable": True},
        {"id": "ollama:llama3.2:1b", "name": "Llama 3.2 1B via Ollama", "family": "Llama 3.x", "tag": "local-light", "size": "pull required", "pullable": True},
        {"id": "ollama:llama3.2:3b", "name": "Llama 3.2 3B via Ollama", "family": "Llama 3.x", "tag": "local-server", "size": "pull required", "pullable": True},
        {"id": "ollama:llama3.1:8b", "name": "Llama 3.1 8B via Ollama", "family": "Llama 3.1", "tag": "local-server", "size": "pull required", "pullable": True},
        {"id": "ollama:llama3.1:8b-instruct-q4_0", "name": "Llama 3.1 8B q4_0 via Ollama", "family": "Llama 3.1", "tag": "quantized", "size": "pull required", "pullable": True},
        {"id": "ollama:llama3.1:8b-instruct-q8_0", "name": "Llama 3.1 8B q8_0 via Ollama", "family": "Llama 3.1", "tag": "quantized", "size": "pull required", "pullable": True},
        {"id": "ollama:llama3.1:70b", "name": "Llama 3.1 70B via Ollama", "family": "Llama 3.1", "tag": "local-server", "size": "pull required", "pullable": True},
        {"id": "ollama:llama3.3:70b", "name": "Llama 3.3 70B via Ollama", "family": "Llama 3.x", "tag": "local-large", "size": "pull required", "pullable": True},
        {"id": "ollama:mistral:7b", "name": "Mistral 7B via Ollama", "family": "Mistral", "tag": "local-server", "size": "pull required", "pullable": True},
        {"id": "ollama:mixtral:8x7b", "name": "Mixtral 8x7B via Ollama", "family": "Mistral", "tag": "local-large", "size": "pull required", "pullable": True},
        {"id": "ollama:phi4-mini", "name": "Phi 4 Mini via Ollama", "family": "Phi", "tag": "local-coding", "size": "pull required", "pullable": True},
        {"id": "ollama:phi4", "name": "Phi 4 via Ollama", "family": "Phi", "tag": "local-coding", "size": "pull required", "pullable": True},
        {"id": "ollama:smollm2:1.7b", "name": "SmolLM2 1.7B via Ollama", "family": "SmolLM", "tag": "local-light", "size": "pull required", "pullable": True},
    ],
    "vllm": [
        {"id": "vllm:openai/gpt-oss-20b", "name": "GPT-OSS 20B via vLLM", "family": "GPT-OSS", "tag": "local-reasoning", "size": "server model", "pullable": True},
        {"id": "vllm:openai/gpt-oss-120b", "name": "GPT-OSS 120B via vLLM", "family": "GPT-OSS", "tag": "local-large", "size": "server model", "pullable": True},
        {"id": "vllm:Qwen/Qwen3-VL-4B-Instruct", "name": "Qwen3-VL 4B via vLLM", "family": "Qwen3-VL", "tag": "local-vlm", "size": "server model", "pullable": True},
        {"id": "vllm:Qwen/Qwen3-VL-8B-Instruct", "name": "Qwen3-VL 8B via vLLM", "family": "Qwen3-VL", "tag": "local-vlm", "size": "server model", "pullable": True},
        {"id": "vllm:Qwen/Qwen3-VL-30B-A3B-Instruct", "name": "Qwen3-VL 30B A3B via vLLM", "family": "Qwen3-VL", "tag": "local-vlm", "size": "server model", "pullable": True},
        {"id": "vllm:Qwen/Qwen2.5-VL-7B-Instruct", "name": "Qwen2.5-VL 7B via vLLM", "family": "Qwen2.5-VL", "tag": "local-vlm", "size": "server model", "pullable": True},
        {"id": "vllm:google/gemma-2-2b", "name": "Gemma 2 2B Base via vLLM", "family": "Gemma", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "vllm:google/gemma-2-2b-it", "name": "Gemma 2 2B via vLLM", "family": "Gemma", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "vllm:google/gemma-2-9b", "name": "Gemma 2 9B Base via vLLM", "family": "Gemma", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "vllm:google/gemma-2-9b-it", "name": "Gemma 2 9B via vLLM", "family": "Gemma", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "vllm:google/gemma-3-4b-it", "name": "Gemma 3 4B via vLLM", "family": "Gemma", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "vllm:google/gemma-3-12b-it", "name": "Gemma 3 12B via vLLM", "family": "Gemma", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "vllm:microsoft/Phi-3.5-mini-instruct", "name": "Phi 3.5 Mini via vLLM", "family": "Phi", "tag": "local-coding", "size": "server model", "pullable": True},
        {"id": "vllm:microsoft/Phi-4-mini-instruct", "name": "Phi 4 Mini via vLLM", "family": "Phi", "tag": "local-coding", "size": "server model", "pullable": True},
        {"id": "vllm:microsoft/phi-4", "name": "Phi 4 via vLLM", "family": "Phi", "tag": "local-coding", "size": "server model", "pullable": True},
        {"id": "vllm:mistralai/Mistral-7B-Instruct-v0.3", "name": "Mistral 7B via vLLM", "family": "Mistral", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "vllm:mistralai/Ministral-8B-Instruct-2410", "name": "Ministral 8B via vLLM", "family": "Mistral", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "vllm:mistralai/Mistral-Small-24B-Instruct-2501", "name": "Mistral Small 24B via vLLM", "family": "Mistral", "tag": "local-large", "size": "server model", "pullable": True},
        {"id": "vllm:meta-llama/Llama-3.2-3B-Instruct", "name": "Llama 3.2 3B via vLLM", "family": "Llama 3.x", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "vllm:meta-llama/Llama-3.1-8B-Instruct", "name": "Llama 3.1 8B via vLLM", "family": "Llama 3.1", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "vllm:meta-llama/Llama-3.3-70B-Instruct", "name": "Llama 3.3 70B via vLLM", "family": "Llama 3.x", "tag": "local-large", "size": "server model", "pullable": True},
        {"id": "vllm:meta-llama/Llama-3.1-70B-Instruct", "name": "Llama 3.1 70B via vLLM", "family": "Llama 3.1", "tag": "local-server", "size": "server model", "pullable": True},
    ],
    "lmstudio": [
        {"id": "lmstudio:openai/gpt-oss-20b", "name": "GPT-OSS 20B via LM Studio", "family": "GPT-OSS", "tag": "local-reasoning", "size": "server model", "pullable": True},
        {"id": "lmstudio:openai/gpt-oss-120b", "name": "GPT-OSS 120B via LM Studio", "family": "GPT-OSS", "tag": "local-large", "size": "server model", "pullable": True},
        {"id": "lmstudio:ggml-org/gemma-4-31B-it-GGUF", "name": "Gemma 4 31B 4-bit via LM Studio", "family": "Gemma 4", "tag": "local-vlm", "size": "server model", "pullable": True},
        {"id": "lmstudio:Qwen/Qwen3-VL-4B-Instruct", "name": "Qwen3-VL 4B via LM Studio", "family": "Qwen3-VL", "tag": "local-vlm", "size": "server model", "pullable": True},
        {"id": "lmstudio:Qwen/Qwen3-VL-8B-Instruct", "name": "Qwen3-VL 8B via LM Studio", "family": "Qwen3-VL", "tag": "local-vlm", "size": "server model", "pullable": True},
        {"id": "lmstudio:Qwen/Qwen3-VL-30B-A3B-Instruct", "name": "Qwen3-VL 30B A3B via LM Studio", "family": "Qwen3-VL", "tag": "local-vlm", "size": "server model", "pullable": True},
        {"id": "lmstudio:Qwen/Qwen2.5-VL-7B-Instruct", "name": "Qwen2.5-VL 7B via LM Studio", "family": "Qwen2.5-VL", "tag": "local-vlm", "size": "server model", "pullable": True},
        {"id": "lmstudio:google/gemma-2-2b-it", "name": "Gemma 2 2B via LM Studio", "family": "Gemma", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "lmstudio:google/gemma-2-9b-it", "name": "Gemma 2 9B via LM Studio", "family": "Gemma", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "lmstudio:google/gemma-3-4b-it", "name": "Gemma 3 4B via LM Studio", "family": "Gemma", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "lmstudio:google/gemma-3-12b-it", "name": "Gemma 3 12B via LM Studio", "family": "Gemma", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "lmstudio:microsoft/Phi-3.5-mini-instruct", "name": "Phi 3.5 Mini via LM Studio", "family": "Phi", "tag": "local-coding", "size": "server model", "pullable": True},
        {"id": "lmstudio:microsoft/Phi-4-mini-instruct", "name": "Phi 4 Mini via LM Studio", "family": "Phi", "tag": "local-coding", "size": "server model", "pullable": True},
        {"id": "lmstudio:microsoft/phi-4", "name": "Phi 4 via LM Studio", "family": "Phi", "tag": "local-coding", "size": "server model", "pullable": True},
        {"id": "lmstudio:mistralai/Mistral-7B-Instruct-v0.3", "name": "Mistral 7B via LM Studio", "family": "Mistral", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "lmstudio:mistralai/Ministral-8B-Instruct-2410", "name": "Ministral 8B via LM Studio", "family": "Mistral", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "lmstudio:mistralai/Mistral-Small-24B-Instruct-2501", "name": "Mistral Small 24B via LM Studio", "family": "Mistral", "tag": "local-large", "size": "server model", "pullable": True},
        {"id": "lmstudio:meta-llama/Llama-3.2-3B-Instruct", "name": "Llama 3.2 3B via LM Studio", "family": "Llama 3.x", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "lmstudio:meta-llama/Llama-3.1-8B-Instruct", "name": "Llama 3.1 8B via LM Studio", "family": "Llama 3.1", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "lmstudio:meta-llama/Llama-3.3-70B-Instruct", "name": "Llama 3.3 70B via LM Studio", "family": "Llama 3.x", "tag": "local-large", "size": "server model", "pullable": True},
        {"id": "lmstudio:meta-llama/Llama-3.1-70B-Instruct", "name": "Llama 3.1 70B via LM Studio", "family": "Llama 3.1", "tag": "local-server", "size": "server model", "pullable": True},
    ],
    "llamacpp": [
        {"id": "llamacpp:ggml-org/gpt-oss-20b-GGUF", "name": "GPT-OSS 20B GGUF via llama.cpp", "family": "GPT-OSS", "tag": "gguf-q4", "size": "gguf", "pullable": True},
        {"id": "llamacpp:ggml-org/gpt-oss-120b-GGUF", "name": "GPT-OSS 120B GGUF via llama.cpp", "family": "GPT-OSS", "tag": "gguf-q4", "size": "gguf", "pullable": True},
        {"id": "llamacpp:ggml-org/gemma-4-31B-it-GGUF", "name": "Gemma 4 31B GGUF via llama.cpp", "family": "Gemma 4", "tag": "gguf-q4", "size": "gguf", "pullable": True},
        {"id": "llamacpp:Qwen/Qwen3-VL-4B-Instruct-GGUF", "name": "Qwen3-VL 4B GGUF via llama.cpp", "family": "Qwen3-VL", "tag": "gguf-vlm", "size": "gguf", "pullable": True},
        {"id": "llamacpp:Qwen/Qwen3-VL-8B-Instruct-GGUF", "name": "Qwen3-VL 8B GGUF via llama.cpp", "family": "Qwen3-VL", "tag": "gguf-vlm", "size": "gguf", "pullable": True},
        {"id": "llamacpp:unsloth/gemma-2-2b-it-GGUF", "name": "Gemma 2 2B GGUF via llama.cpp", "family": "Gemma", "tag": "gguf-q4", "size": "gguf", "pullable": True},
        {"id": "llamacpp:unsloth/gemma-2-9b-it-GGUF", "name": "Gemma 2 9B GGUF via llama.cpp", "family": "Gemma", "tag": "gguf-q4", "size": "gguf", "pullable": True},
        {"id": "llamacpp:unsloth/gemma-3-4b-it-GGUF", "name": "Gemma 3 4B GGUF via llama.cpp", "family": "Gemma", "tag": "gguf-q4", "size": "gguf", "pullable": True},
        {"id": "llamacpp:bartowski/Mistral-7B-Instruct-v0.3-GGUF", "name": "Mistral 7B GGUF via llama.cpp", "family": "Mistral", "tag": "gguf-q4", "size": "gguf", "pullable": True},
        {"id": "llamacpp:bartowski/Phi-3.5-mini-instruct-GGUF", "name": "Phi 3.5 Mini GGUF via llama.cpp", "family": "Phi", "tag": "gguf-q4", "size": "gguf", "pullable": True},
        {"id": "llamacpp:bartowski/phi-4-GGUF", "name": "Phi 4 GGUF via llama.cpp", "family": "Phi", "tag": "gguf-q4", "size": "gguf", "pullable": True},
        {"id": "llamacpp:bartowski/Llama-3.2-3B-Instruct-GGUF", "name": "Llama 3.2 3B GGUF via llama.cpp", "family": "Llama 3.x", "tag": "gguf-q4", "size": "gguf", "pullable": True},
        {"id": "llamacpp:bartowski/Llama-3.1-8B-Instruct-GGUF", "name": "Llama 3.1 8B GGUF via llama.cpp", "family": "Llama 3.1", "tag": "local-server", "size": "gguf", "pullable": True},
        {"id": "llamacpp:bartowski/Llama-3.3-70B-Instruct-GGUF", "name": "Llama 3.3 70B GGUF via llama.cpp", "family": "Llama 3.x", "tag": "local-large", "size": "gguf", "pullable": True},
        {"id": "llamacpp:bartowski/Llama-3.1-70B-Instruct-GGUF", "name": "Llama 3.1 70B GGUF via llama.cpp", "family": "Llama 3.1", "tag": "local-server", "size": "gguf", "pullable": True},
    ],
}

MODEL_ENGINE_ALIASES = {
    "gpt-oss-20b": {
        "local_mlx": "mlx-community/gpt-oss-20b-MXFP4-Q8",
        "ollama": "gpt-oss:20b",
        "vllm": "openai/gpt-oss-20b",
        "lmstudio": "openai/gpt-oss-20b",
        "llamacpp": "ggml-org/gpt-oss-20b-GGUF",
    },
    "openai/gpt-oss-20b": {
        "local_mlx": "mlx-community/gpt-oss-20b-MXFP4-Q8",
        "ollama": "gpt-oss:20b",
        "vllm": "openai/gpt-oss-20b",
        "lmstudio": "openai/gpt-oss-20b",
        "llamacpp": "ggml-org/gpt-oss-20b-GGUF",
    },
    "gpt-oss-120b": {
        "local_mlx": "mlx-community/gpt-oss-120b-MXFP4-Q4",
        "ollama": "gpt-oss:120b",
        "vllm": "openai/gpt-oss-120b",
        "lmstudio": "openai/gpt-oss-120b",
        "llamacpp": "ggml-org/gpt-oss-120b-GGUF",
    },
    "openai/gpt-oss-120b": {
        "local_mlx": "mlx-community/gpt-oss-120b-MXFP4-Q4",
        "ollama": "gpt-oss:120b",
        "vllm": "openai/gpt-oss-120b",
        "lmstudio": "openai/gpt-oss-120b",
        "llamacpp": "ggml-org/gpt-oss-120b-GGUF",
    },
    "gemma-4-31b-it-4bit": {
        "local_mlx": "mlx-community/gemma-4-31b-it-4bit",
        "ollama": "hf.co/ggml-org/gemma-4-31B-it-GGUF:Q4_K_M",
        "vllm": "suitch/gemma-4-31B-it-4bit",
        "lmstudio": "ggml-org/gemma-4-31B-it-GGUF",
        "llamacpp": "ggml-org/gemma-4-31B-it-GGUF",
    },
    "suitch/gemma-4-31b-it-4bit": {
        "local_mlx": "mlx-community/gemma-4-31b-it-4bit",
        "ollama": "hf.co/ggml-org/gemma-4-31B-it-GGUF:Q4_K_M",
        "vllm": "suitch/gemma-4-31B-it-4bit",
        "lmstudio": "ggml-org/gemma-4-31B-it-GGUF",
        "llamacpp": "ggml-org/gemma-4-31B-it-GGUF",
    },
    "mlx-community/gemma-4-31b-it-4bit": {
        "local_mlx": "mlx-community/gemma-4-31b-it-4bit",
        "ollama": "hf.co/ggml-org/gemma-4-31B-it-GGUF:Q4_K_M",
        "vllm": "suitch/gemma-4-31B-it-4bit",
        "lmstudio": "ggml-org/gemma-4-31B-it-GGUF",
        "llamacpp": "ggml-org/gemma-4-31B-it-GGUF",
    },
}

_VERSIONED_MODEL_PATTERNS = (
    ("gemma", re.compile(r"\bgemma[-\s]?(\d+(?:\.\d+)?)", re.IGNORECASE)),
    ("qwen", re.compile(r"\bqwen[-\s]?(\d+(?:\.\d+)?)", re.IGNORECASE)),
    ("llama", re.compile(r"\bllama[-\s]?(\d+(?:\.\d+)?)", re.IGNORECASE)),
    ("phi", re.compile(r"\bphi[-\s]?(\d+(?:\.\d+)?)", re.IGNORECASE)),
)


def _version_tuple(raw: str) -> tuple[int, ...]:
    return tuple(int(part) for part in raw.split(".") if part.isdigit())


def _model_family_version(model: Dict[str, object]) -> Optional[tuple[str, tuple[int, ...]]]:
    text = " ".join(str(model.get(key) or "") for key in ("family", "name", "id"))
    for family, pattern in _VERSIONED_MODEL_PATTERNS:
        match = pattern.search(text)
        if match:
            version = _version_tuple(match.group(1))
            if version:
                return family, version
    return None


def filter_lower_family_versions(models: List[Dict[str, object]]) -> List[Dict[str, object]]:
    max_versions: Dict[str, tuple[int, ...]] = {}
    detected: List[tuple[Dict[str, object], Optional[tuple[str, tuple[int, ...]]]]] = []
    for model in models:
        version_info = _model_family_version(model)
        detected.append((model, version_info))
        if not version_info:
            continue
        family, version = version_info
        if version > max_versions.get(family, (0,)):
            max_versions[family] = version
    return [
        model for model, version_info in detected
        if not version_info or version_info[1] >= max_versions.get(version_info[0], version_info[1])
    ]

def _update_env_file(env_file: Path, key: str, value: str) -> None:
    lines = []
    found = False
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{key}="):
                lines.append(f"{key}={value}")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"{key}={value}")
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


LOCAL_SERVER_PROCESSES: Dict[str, subprocess.Popen] = {}
VLLM_METAL_ENV = Path.home() / ".venv-vllm-metal"
VLLM_METAL_BIN = VLLM_METAL_ENV / "bin" / "vllm"
VLLM_METAL_PYTHON = VLLM_METAL_ENV / "bin" / "python"
LMSTUDIO_BUNDLED_CLI = Path("/Applications/LM Studio.app/Contents/Resources/app/.webpack/lms")

def windows_binary_candidates(binary: str) -> List[Path]:
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    candidates = {
        "ollama": [
            Path(local_appdata) / "Programs" / "Ollama" / "ollama.exe" if local_appdata else None,
            Path(program_files) / "Ollama" / "ollama.exe",
        ],
        "lms": [
            Path(local_appdata) / "Programs" / "LM Studio" / "resources" / "app" / ".webpack" / "lms.exe" if local_appdata else None,
            Path(program_files) / "LM Studio" / "resources" / "app" / ".webpack" / "lms.exe",
        ],
        "nvidia-smi": [
            Path(program_files) / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe",
            Path(program_files_x86) / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe",
        ],
    }
    return [item for item in candidates.get(binary, []) if item is not None]


def local_binary(binary: str) -> Optional[str]:
    found = shutil.which(binary)
    if found:
        return found
    if platform.system() == "Windows":
        for candidate in windows_binary_candidates(binary):
            if candidate.exists():
                return str(candidate)
    return None


def find_lmstudio_cli() -> Optional[str]:
    cli = local_binary("lms")
    if cli:
        return cli
    if LMSTUDIO_BUNDLED_CLI.exists():
        return str(LMSTUDIO_BUNDLED_CLI)
    return None


def vllm_executable() -> Optional[str]:
    found = shutil.which("vllm")
    if found:
        return found
    if VLLM_METAL_BIN.exists():
        return str(VLLM_METAL_BIN)
    return None


def vllm_metal_python() -> Optional[str]:
    if VLLM_METAL_PYTHON.exists():
        return str(VLLM_METAL_PYTHON)
    return None


def _json_request(
    url: str,
    *,
    method: str = "GET",
    payload: Optional[Dict[str, object]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 10.0,
) -> Dict[str, object]:
    data = None
    req_headers = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as res:
        raw = res.read().decode("utf-8", errors="replace")
    if not raw.strip():
        return {}
    return json.loads(raw)


def lmstudio_api_base() -> str:
    return (os.getenv("LMSTUDIO_BASE_URL") or OPENAI_COMPATIBLE_PROVIDERS["lmstudio"]["base_url"]).rstrip("/")


def lmstudio_native_api_base() -> str:
    base = lmstudio_api_base()
    return base[:-3] if base.endswith("/v1") else base


def ensure_lmstudio_server() -> None:
    base_url = lmstudio_native_api_base()
    try:
        _json_request(f"{base_url}/api/v1/models", headers={"Authorization": "Bearer lmstudio"}, timeout=2.5)
        return
    except Exception:
        pass

    cli = find_lmstudio_cli()
    if not cli:
        raise HTTPException(status_code=400, detail="LM Studio CLI를 찾지 못했습니다. LM Studio를 설치한 뒤 다시 시도하세요.")

    try:
        subprocess.Popen(
            [cli, "server", "start"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LM Studio 서버 시작 실패: {e}")

    deadline = time.time() + 45
    while time.time() < deadline:
        try:
            _json_request(f"{base_url}/api/v1/models", headers={"Authorization": "Bearer lmstudio"}, timeout=2.5)
            return
        except Exception:
            time.sleep(1)
    raise HTTPException(status_code=500, detail="LM Studio Local Server를 자동으로 시작하지 못했습니다.")


_LMSTUDIO_MODELS_CACHE: List[Dict[str, object]] = []
_LMSTUDIO_MODELS_CACHE_TS: float = 0.0
_LMSTUDIO_MODELS_CACHE_TTL: float = 10.0


def get_lmstudio_models(*, force: bool = False) -> List[Dict[str, object]]:
    global _LMSTUDIO_MODELS_CACHE, _LMSTUDIO_MODELS_CACHE_TS
    if not force and time.monotonic() - _LMSTUDIO_MODELS_CACHE_TS < _LMSTUDIO_MODELS_CACHE_TTL:
        return _LMSTUDIO_MODELS_CACHE
    try:
        payload = _json_request(
            f"{lmstudio_native_api_base()}/api/v1/models",
            headers={"Authorization": f"Bearer {os.getenv('LMSTUDIO_API_KEY') or 'lmstudio'}"},
            timeout=2.5,
        )
    except Exception:
        return _LMSTUDIO_MODELS_CACHE
    models = payload.get("models")
    _LMSTUDIO_MODELS_CACHE = models if isinstance(models, list) else []
    _LMSTUDIO_MODELS_CACHE_TS = time.monotonic()
    return _LMSTUDIO_MODELS_CACHE


def _lmstudio_candidate_keys(model_name: str) -> List[str]:
    raw = model_name.strip()
    if not raw:
        return []
    slug = raw.split("/")[-1].lower()
    slug = slug.replace("-gguf", "").replace("-awq", "")
    parts = [p for p in slug.split("-") if p]
    candidates = [raw.lower(), slug]
    if parts:
        candidates.append("-".join(parts[: min(4, len(parts))]))
    return list(dict.fromkeys(candidates))


def _find_lmstudio_model_key(model_name: str, models: List[Dict[str, object]]) -> Optional[str]:
    if not models:
        return None
    candidate_keys = _lmstudio_candidate_keys(model_name)
    exact = []
    fuzzy = []
    for item in models:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        display_name = str(item.get("display_name") or "").strip()
        haystacks = [key.lower(), display_name.lower()]
        if any(raw == key.lower() for raw in candidate_keys):
            exact.append(key)
            continue
        if any(token and token in hay for token in candidate_keys for hay in haystacks):
            fuzzy.append(key)
    return (exact or fuzzy or [None])[0]


def ensure_lmstudio_model(model_name: str) -> Dict[str, object]:
    ensure_lmstudio_server()
    auth_header = {"Authorization": f"Bearer {os.getenv('LMSTUDIO_API_KEY') or 'lmstudio'}"}
    models = get_lmstudio_models()
    found_key = _find_lmstudio_model_key(model_name, models)
    model_key = found_key or model_name

    if not found_key:
        try:
            job = _json_request(
                f"{lmstudio_native_api_base()}/api/v1/models/download",
                method="POST",
                payload={"model": model_name},
                headers=auth_header,
                timeout=30,
            )
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[-2000:]
            raise HTTPException(status_code=500, detail=f"LM Studio 모델 다운로드 실패: {detail or e.reason}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"LM Studio 모델 다운로드 실패: {e}")

        status = str(job.get("status") or "")
        job_id = str(job.get("job_id") or "")
        if status not in {"completed", "already_downloaded"} and job_id:
            deadline = time.time() + 3600
            while time.time() < deadline:
                polled = _json_request(
                    f"{lmstudio_native_api_base()}/api/v1/models/download/status/{job_id}",
                    headers=auth_header,
                    timeout=30,
                )
                polled_status = str(polled.get("status") or "")
                if polled_status == "completed":
                    break
                if polled_status == "failed":
                    raise HTTPException(status_code=500, detail=f"LM Studio 모델 다운로드 실패: {polled}")
                time.sleep(2)
            else:
                raise HTTPException(status_code=408, detail="LM Studio 모델 다운로드 시간이 초과되었습니다.")

        models = get_lmstudio_models(force=True)
        model_key = _find_lmstudio_model_key(model_name, models) or model_name

    target = next((item for item in models if isinstance(item, dict) and item.get("key") == model_key), None)
    loaded_instances = target.get("loaded_instances") if isinstance(target, dict) else None
    if loaded_instances:
        return {"provider": "lmstudio", "model": model_name, "resolved_model": model_key, "server_ready": True, "cached": True}

    try:
        loaded = _json_request(
            f"{lmstudio_native_api_base()}/api/v1/models/load",
            method="POST",
            payload={"model": model_key, "context_length": 4096},
            headers=auth_header,
            timeout=120,
        )
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[-2000:]
        raise HTTPException(status_code=500, detail=f"LM Studio 모델 로드 실패: {detail or e.reason}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LM Studio 모델 로드 실패: {e}")

    if str(loaded.get("status") or "") != "loaded":
        raise HTTPException(status_code=500, detail=f"LM Studio 모델 로드 실패: {loaded}")

    return {
        "provider": "lmstudio",
        "model": model_name,
        "resolved_model": model_key,
        "instance_id": loaded.get("instance_id"),
        "server_ready": True,
        "cached": False,
    }

def engine_support_status(engine: str) -> Dict[str, object]:
    if engine != "vllm":
        return {"supported": True, "reason": None}
    is_apple_silicon = sys.platform == "darwin" and platform.machine() == "arm64"
    if sys.platform.startswith("win"):
        return {"supported": False, "reason": "vLLM은 Windows native 자동 설치보다 WSL2/Linux 환경을 권장합니다."}
    if sys.platform == "darwin" and not is_apple_silicon:
        return {"supported": False, "reason": "vLLM Metal 자동 설치는 Apple Silicon macOS에서만 지원됩니다."}
    if sys.version_info >= (3, 13) and is_apple_silicon:
        return {"supported": True, "reason": "현재 환경에서는 vLLM Metal 전용 런타임으로 설치합니다."}
    if sys.version_info >= (3, 13):
        return {"supported": False, "reason": "vLLM 설치는 현재 Python 3.13 이하 또는 별도 전용 런타임이 필요합니다."}
    return {"supported": True, "reason": None}

def hf_model_ready(repo_id: str, provider: str = "local_mlx") -> bool:
    model_dir = hf_model_dir(repo_id)
    if provider == "vllm" and (not model_dir.exists() or not model_dir.is_dir()):
        hf_cache_repo = Path.home() / ".cache" / "huggingface" / "hub" / f"models--{repo_id.replace('/', '--')}"
        if hf_cache_repo.exists() and any(hf_cache_repo.glob("snapshots/*")):
            return True
        return False
    if not model_dir.exists() or not model_dir.is_dir():
        return False
    if provider == "llamacpp":
        return any(model_dir.rglob("*.gguf"))
    has_config = (model_dir / "config.json").exists()
    has_weights = any(model_dir.glob("*.safetensors")) or any(model_dir.glob("*.bin"))
    has_tokenizer = (
        (model_dir / "tokenizer.json").exists()
        or (model_dir / "tokenizer.model").exists()
        or (model_dir / "tokenizer_config.json").exists()
    )
    return has_config and has_weights and has_tokenizer


def model_download_progress_payload(
    stage: str,
    message: str,
    *,
    percent: Optional[float] = None,
    detail: Optional[str] = None,
    downloaded_bytes: Optional[int] = None,
    total_bytes: Optional[int] = None,
    eta_seconds: Optional[float] = None,
    file: Optional[str] = None,
    indeterminate: bool = False,
) -> Dict[str, object]:
    payload: Dict[str, object] = {
        "stage": stage,
        "message": message,
        "indeterminate": indeterminate,
        "ts": time.time(),
    }
    if percent is not None:
        payload["percent"] = max(0, min(100, round(float(percent), 1)))
    if detail:
        payload["detail"] = detail
    if downloaded_bytes is not None:
        payload["downloaded_bytes"] = max(0, int(downloaded_bytes))
    if total_bytes is not None:
        payload["total_bytes"] = max(0, int(total_bytes))
    if eta_seconds is not None:
        payload["eta_seconds"] = max(0, round(float(eta_seconds)))
    if file:
        payload["file"] = file
    return payload


def estimate_eta_seconds(started_at: float, percent: Optional[float]) -> Optional[float]:
    if percent is None or percent <= 0 or percent >= 100:
        return None
    elapsed = max(0.0, time.time() - started_at)
    return elapsed * (100.0 - percent) / percent


def hf_repo_files_with_sizes(repo_id: str) -> List[Dict[str, object]]:
    from huggingface_hub import HfApi

    api = HfApi()
    try:
        info = api.model_info(repo_id, files_metadata=True)
        files = []
        for sibling in getattr(info, "siblings", []) or []:
            name = str(getattr(sibling, "rfilename", "") or "").strip()
            if not name or name.endswith("/"):
                continue
            files.append({"name": name, "size": int(getattr(sibling, "size", 0) or 0)})
        if files:
            return files
    except TypeError:
        pass
    except Exception as e:
        logging.warning("huggingface model_info failed for %s: %s", repo_id, e)

    return [{"name": str(name), "size": 0} for name in api.list_repo_files(repo_id) if str(name).strip()]


def download_hf_model(
    repo_id: str,
    provider: str = "local_mlx",
    progress_emit=None,
) -> Dict[str, object]:
    if importlib.util.find_spec("huggingface_hub") is None:
        raise HTTPException(status_code=400, detail="huggingface_hub가 없습니다. 먼저 MLX runtime 설치를 진행해 주세요.")

    target_dir = hf_model_dir(repo_id)
    if hf_model_ready(repo_id, provider):
        if progress_emit:
            progress_emit(model_download_progress_payload(
                "download",
                "이미 다운로드된 모델을 확인했습니다.",
                percent=100,
                downloaded_bytes=0,
                total_bytes=0,
                eta_seconds=0,
            ))
        return {"model": repo_id, "path": str(target_dir), "cached": True}

    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        from huggingface_hub import hf_hub_download

        started_at = time.time()
        all_files = hf_repo_files_with_sizes(repo_id)
        if provider == "llamacpp":
            ggufs = sorted(
                [item for item in all_files if str(item["name"]).lower().endswith(".gguf")],
                key=lambda item: str(item["name"]),
            )
            if not ggufs:
                raise RuntimeError("GGUF 파일을 찾지 못했습니다.")
            preference = ("q4_k_m", "q4_0", "q4_k_s", "q3_k_m", "q2_k")
            selected_files = [
                next(
                    (item for pref in preference for item in ggufs if pref in str(item["name"]).lower()),
                    ggufs[0],
                )
            ]
        else:
            selected_files = all_files

        total_bytes = sum(int(item.get("size") or 0) for item in selected_files) or None
        downloaded_bytes = 0
        total_files = max(1, len(selected_files))
        if progress_emit:
            progress_emit(model_download_progress_payload(
                "download",
                "모델 파일 정보를 확인했습니다.",
                percent=0,
                downloaded_bytes=0,
                total_bytes=total_bytes,
                indeterminate=total_bytes is None,
            ))

        for index, item in enumerate(selected_files, start=1):
            filename = str(item["name"])
            size = int(item.get("size") or 0)
            tqdm_class = None
            if progress_emit:
                current_percent = (
                    (downloaded_bytes / total_bytes) * 100 if total_bytes else ((index - 1) / total_files) * 100
                )
                progress_emit(model_download_progress_payload(
                    "download",
                    "모델 다운로드 중입니다.",
                    percent=current_percent,
                    detail=filename,
                    downloaded_bytes=downloaded_bytes,
                    total_bytes=total_bytes,
                    eta_seconds=estimate_eta_seconds(started_at, current_percent),
                    file=filename,
                    indeterminate=total_bytes is None and total_files <= 1,
                ))
                try:
                    from tqdm.auto import tqdm as base_tqdm

                    downloaded_before = downloaded_bytes
                    last_emit = {"at": 0.0, "percent": -1.0}

                    def emit_byte_progress(done_bytes: float) -> None:
                        done = max(0, int(done_bytes or 0))
                        if total_bytes:
                            aggregate = min(total_bytes, downloaded_before + done)
                            percent = (aggregate / total_bytes) * 100
                        else:
                            file_total = size or done
                            file_ratio = min(1.0, done / file_total) if file_total else 0.0
                            aggregate = downloaded_before + done
                            percent = ((index - 1) + file_ratio) / total_files * 100
                        now = time.time()
                        if percent < 100 and now - last_emit["at"] < 0.5 and percent - last_emit["percent"] < 0.3:
                            return
                        last_emit["at"] = now
                        last_emit["percent"] = percent
                        progress_emit(model_download_progress_payload(
                            "download",
                            "모델 다운로드 중입니다.",
                            percent=percent,
                            detail=filename,
                            downloaded_bytes=aggregate,
                            total_bytes=total_bytes,
                            eta_seconds=estimate_eta_seconds(started_at, percent),
                            file=filename,
                            indeterminate=total_bytes is None and total_files <= 1,
                        ))

                    class ProgressTqdm(base_tqdm):
                        def update(self, n=1):
                            result = super().update(n)
                            emit_byte_progress(float(getattr(self, "n", 0) or 0))
                            return result

                    tqdm_class = ProgressTqdm
                except Exception:
                    tqdm_class = None
            local_path = hf_hub_download(
                repo_id=repo_id,
                filename=filename,
                local_dir=str(target_dir),
                tqdm_class=tqdm_class,
            )
            if size <= 0:
                try:
                    size = Path(local_path).stat().st_size
                except OSError:
                    size = 0
            downloaded_bytes += size
            if progress_emit:
                current_percent = (
                    (downloaded_bytes / total_bytes) * 100 if total_bytes else (index / total_files) * 100
                )
                progress_emit(model_download_progress_payload(
                    "download",
                    "모델 다운로드 중입니다.",
                    percent=current_percent,
                    detail=filename,
                    downloaded_bytes=downloaded_bytes,
                    total_bytes=total_bytes,
                    eta_seconds=estimate_eta_seconds(started_at, current_percent),
                    file=filename,
                    indeterminate=False,
                ))

        if progress_emit:
            progress_emit(model_download_progress_payload(
                "download",
                "모델 다운로드가 완료되었습니다.",
                percent=100,
                downloaded_bytes=downloaded_bytes,
                total_bytes=total_bytes or downloaded_bytes,
                eta_seconds=0,
            ))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{repo_id} 다운로드 실패: {str(e)[-2000:]}")

    if not hf_model_ready(repo_id, provider):
        raise HTTPException(status_code=500, detail=f"{repo_id} 다운로드가 완료되지 않았습니다. 모델 파일을 찾지 못했습니다.")

    return {"model": repo_id, "path": str(target_dir), "cached": False}


def pull_ollama_model_with_progress(model_name: str, progress_emit=None) -> Dict[str, object]:
    ollama = local_binary("ollama")
    if not ollama:
        raise HTTPException(status_code=400, detail="Ollama가 설치되지 않았습니다.")
    started_at = time.time()
    if progress_emit:
        progress_emit(model_download_progress_payload(
            "download",
            "Ollama 모델 다운로드를 시작합니다.",
            percent=0,
            detail=model_name,
            indeterminate=True,
        ))
    process = subprocess.Popen(
        [ollama, "pull", model_name],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    last_percent: Optional[float] = None
    lines: List[str] = []
    try:
        assert process.stdout is not None
        for raw_line in process.stdout:
            for part in re.split(r"[\r\n]+", raw_line):
                line = part.strip()
                if not line:
                    continue
                lines.append(line)
                match = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%", line)
                if match:
                    last_percent = min(100.0, float(match.group(1)))
                    if progress_emit:
                        progress_emit(model_download_progress_payload(
                            "download",
                            "Ollama 모델 다운로드 중입니다.",
                            percent=last_percent,
                            detail=line[-180:],
                            eta_seconds=estimate_eta_seconds(started_at, last_percent),
                            indeterminate=False,
                        ))
                elif progress_emit:
                    progress_emit(model_download_progress_payload(
                        "download",
                        "Ollama 모델 다운로드 중입니다.",
                        percent=last_percent,
                        detail=line[-180:],
                        eta_seconds=estimate_eta_seconds(started_at, last_percent),
                        indeterminate=last_percent is None,
                    ))
        returncode = process.wait()
    except Exception:
        process.kill()
        raise

    if returncode != 0:
        tail = "\n".join(lines[-12:])
        raise HTTPException(status_code=500, detail=tail[-2000:] or "Ollama 모델 다운로드 실패")

    if progress_emit:
        progress_emit(model_download_progress_payload(
            "download",
            "Ollama 모델 다운로드가 완료되었습니다.",
            percent=100,
            detail=model_name,
            eta_seconds=0,
            indeterminate=False,
        ))
    return {"provider": "ollama", "model": model_name, "returncode": returncode}


def get_ollama_pulled_models() -> set:
    ollama = local_binary("ollama")
    if not ollama:
        return set()
    try:
        result = subprocess.run([ollama, "list"], capture_output=True, text=True, timeout=5, check=False)
        pulled = set()
        for line in result.stdout.splitlines()[1:]:
            parts = line.split()
            if parts:
                pulled.add(parts[0])
        return pulled
    except Exception:
        return set()


def get_openai_compatible_server_models(provider: str) -> List[str]:
    if provider == "lmstudio":
        models = []
        for item in get_lmstudio_models():
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            loaded_instances = item.get("loaded_instances") or []
            if loaded_instances:
                instance_ids = [
                    str(instance.get("id") or "").strip()
                    for instance in loaded_instances
                    if isinstance(instance, dict) and instance.get("id")
                ]
                models.extend(instance_ids or ([key] if key else []))
        return list(dict.fromkeys([model for model in models if model]))

    config = OPENAI_COMPATIBLE_PROVIDERS.get(provider) or {}
    base_url = os.getenv(config.get("base_url_env", "")) if config.get("base_url_env") else None
    base_url = (base_url or config.get("base_url") or "").rstrip("/")
    if not base_url:
        return []

    api_key = os.getenv(config.get("env_key", "")) or config.get("api_key_fallback") or provider
    req = urllib.request.Request(
        f"{base_url}/models",
        headers={"Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=2.5) as res:
            payload = json.loads(res.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return []

    models = []
    for item in payload.get("data") or []:
        model_id = item.get("id") if isinstance(item, dict) else None
        if model_id:
            models.append(str(model_id))
    return models


def ensure_ollama_server() -> None:
    ollama = local_binary("ollama")
    if not ollama:
        raise HTTPException(status_code=400, detail="Ollama가 설치되지 않았습니다.")
    try:
        probe = subprocess.run([ollama, "list"], capture_output=True, text=True, timeout=3, check=False)
        if probe.returncode == 0:
            return
    except Exception:
        pass
    subprocess.Popen(
        [ollama, "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            probe = subprocess.run([ollama, "list"], capture_output=True, text=True, timeout=3, check=False)
            if probe.returncode == 0:
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise HTTPException(status_code=500, detail="Ollama 서버를 자동으로 시작하지 못했습니다.")


def wait_for_openai_compatible_server(provider: str, model_name: Optional[str] = None, timeout: int = 45) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        models = get_openai_compatible_server_models(provider)
        if models and (not model_name or model_name in models):
            return True
        time.sleep(1)
    return False


def ensure_vllm_server(model_name: str) -> None:
    served_models = get_openai_compatible_server_models("vllm")
    if model_name in served_models:
        return
    vllm_bin = vllm_executable()
    vllm_metal_py = vllm_metal_python()
    if not vllm_bin and not vllm_metal_py and importlib.util.find_spec("vllm") is None:
        raise HTTPException(status_code=400, detail="vLLM runtime이 설치되지 않았습니다.")

    local_dir = hf_model_dir(model_name)
    if not vllm_metal_py and not hf_model_ready(model_name, "vllm"):
        download_hf_model(model_name, "vllm")

    running = LOCAL_SERVER_PROCESSES.get("vllm")
    if running and running.poll() is None:
        running.terminate()
        try:
            running.wait(timeout=10)
        except subprocess.TimeoutExpired:
            running.kill()
    elif served_models:
        raise HTTPException(status_code=409, detail="다른 vLLM 서버가 이미 실행 중입니다. 현재 서버를 종료한 뒤 다시 시도하세요.")

    running = LOCAL_SERVER_PROCESSES.get("vllm")
    if running and running.poll() is None:
        return

    _host_args = ["--host", "127.0.0.1", "--port", "8000"]
    if vllm_metal_py:
        command = [vllm_metal_py, "-m", "vllm_metal.server", "--model", model_name, *_host_args]
    elif vllm_bin:
        command = [vllm_bin, "serve", str(local_dir), "--served-model-name", model_name, *_host_args]
    else:
        command = [sys.executable, "-m", "vllm.entrypoints.openai.api_server", "--model", str(local_dir), "--served-model-name", model_name, *_host_args]
    LOCAL_SERVER_PROCESSES["vllm"] = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    if not wait_for_openai_compatible_server("vllm", model_name, timeout=90):
        raise HTTPException(status_code=500, detail="vLLM 서버가 모델을 자동 로드하지 못했습니다.")


def ensure_llamacpp_server(model_name: str) -> None:
    served_models = get_openai_compatible_server_models("llamacpp")
    if model_name in served_models:
        return
    running = LOCAL_SERVER_PROCESSES.get("llamacpp")
    if running and running.poll() is None:
        running.terminate()
        try:
            running.wait(timeout=10)
        except subprocess.TimeoutExpired:
            running.kill()
    elif served_models:
        raise HTTPException(status_code=409, detail="다른 llama.cpp 서버가 이미 실행 중입니다. 현재 서버를 종료한 뒤 다시 시도하세요.")
    if not shutil.which("llama-server"):
        raise HTTPException(status_code=400, detail="llama.cpp가 설치되지 않았습니다.")
    if not hf_model_ready(model_name, "llamacpp"):
        download_hf_model(model_name, "llamacpp")

    gguf_files = sorted(hf_model_dir(model_name).rglob("*.gguf"))
    if not gguf_files:
        raise HTTPException(status_code=500, detail="다운로드된 GGUF 파일을 찾지 못했습니다.")

    preferred = next((p for p in gguf_files if "q4_k_m" in p.name.lower()), None)
    model_file = preferred or gguf_files[0]
    LOCAL_SERVER_PROCESSES["llamacpp"] = subprocess.Popen(
        [
            "llama-server",
            "-m",
            str(model_file),
            "--alias",
            model_name,
            "--host",
            "127.0.0.1",
            "--port",
            "8080",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    if not wait_for_openai_compatible_server("llamacpp", model_name, timeout=45):
        raise HTTPException(status_code=500, detail="llama.cpp 서버가 모델을 자동 로드하지 못했습니다.")


def engine_installed(engine: str) -> bool:
    if engine == "local_mlx":
        return bool(importlib.util.find_spec("mlx") and importlib.util.find_spec("mlx_lm"))
    if engine == "ollama":
        return local_binary("ollama") is not None
    if engine == "vllm":
        return vllm_metal_python() is not None or vllm_executable() is not None or importlib.util.find_spec("vllm") is not None
    if engine == "lmstudio":
        return find_lmstudio_cli() is not None or Path("/Applications/LM Studio.app").exists()
    if engine == "llamacpp":
        return shutil.which("llama-server") is not None
    if engine in {"openai", "openrouter", "groq", "together", "xai"}:
        return AsyncOpenAI is not None
    return False

def engine_status() -> List[Dict]:
    cloud_models = router.detected_cloud_models()
    cloud_by_provider = {}
    for model in cloud_models:
        cloud_by_provider.setdefault(model["provider"], []).append(model)

    ollama_installed = engine_installed("ollama")
    pulled = get_ollama_pulled_models() if ollama_installed else set()
    ollama_models = []
    for m in ENGINE_MODEL_CATALOG["ollama"]:
        pull_name = m["id"].removeprefix("ollama:")
        ollama_models.append({**m, "pulled": pull_name in pulled})
    ollama_models = filter_lower_family_versions(ollama_models)

    HF_MODELS_ROOT.mkdir(parents=True, exist_ok=True)
    mlx_models = []
    for m in ENGINE_MODEL_CATALOG.get("local_mlx", []):
        repo_id = m["id"]
        mlx_models.append({**m, "pulled": hf_model_ready(repo_id, "local_mlx")})
    mlx_models = filter_lower_family_versions(mlx_models)

    vllm_models = []
    for m in ENGINE_MODEL_CATALOG.get("vllm", []):
        repo_id = m["id"].removeprefix("vllm:")
        vllm_models.append({**m, "pulled": hf_model_ready(repo_id, "vllm")})
    vllm_models = filter_lower_family_versions(vllm_models)

    lmstudio_models = []
    downloaded_lmstudio = get_lmstudio_models()
    downloaded_by_key = {}
    for item in downloaded_lmstudio:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        downloaded_by_key[key] = item
        loaded_instances = item.get("loaded_instances") or []
        lmstudio_models.append({
            "id": f"lmstudio:{key}",
            "name": item.get("display_name") or f"LM Studio · {key}",
            "family": item.get("architecture") or item.get("publisher") or "LM Studio",
            "tag": "loaded-server-model" if loaded_instances else "downloaded",
            "size": item.get("params_string") or item.get("format") or "LM Studio",
            "pullable": True,
            "pulled": True,
        })

    if not lmstudio_models:
        for m in ENGINE_MODEL_CATALOG.get("lmstudio", []):
            lmstudio_models.append({**m, "pulled": False})
    else:
        known_ids = {item["id"] for item in lmstudio_models}
        for m in ENGINE_MODEL_CATALOG.get("lmstudio", []):
            repo_id = m["id"].removeprefix("lmstudio:")
            if f"lmstudio:{repo_id}" not in known_ids and repo_id not in downloaded_by_key:
                lmstudio_models.append({**m, "pulled": False})
    lmstudio_models = filter_lower_family_versions(lmstudio_models)

    llamacpp_models = []
    for m in ENGINE_MODEL_CATALOG.get("llamacpp", []):
        repo_id = m["id"].removeprefix("llamacpp:")
        llamacpp_models.append({**m, "pulled": hf_model_ready(repo_id, "llamacpp")})
    llamacpp_models = filter_lower_family_versions(llamacpp_models)

    local_server_specs = [
        {
            "id": "vllm",
            "name": "vLLM",
            "description": "vLLM OpenAI 호환 서버(예: http://localhost:8000/v1)에 연결합니다.",
            "requires": "VLLM_BASE_URL",
            "note": engine_support_status("vllm").get("reason"),
        },
        {
            "id": "lmstudio",
            "name": "LM Studio",
            "description": "LM Studio 로컬 OpenAI 호환 서버에 연결합니다.",
            "requires": "LMSTUDIO_BASE_URL",
            "note": (
                "다운로드된 모델은 자동 감지하고, 선택 시 필요하면 다운로드 후 바로 로드합니다."
                if downloaded_lmstudio else
                "LM Studio 설치 후 모델을 선택하면 Local Server 시작, 다운로드, 로드를 자동으로 진행합니다."
            ),
            "server_ready": bool(downloaded_lmstudio),
        },
        {
            "id": "llamacpp",
            "name": "llama.cpp",
            "description": "llama.cpp 서버(OpenAI 호환 /v1)에 연결합니다.",
            "requires": "LLAMACPP_BASE_URL",
        },
    ]

    engines = [
        {
            "id": "local_mlx",
            "name": "MLX",
            "kind": "local",
            "description": "Apple Silicon GPU에서 MLX/MLX-VLM 모델을 직접 실행합니다.",
            "installed": engine_installed("local_mlx"),
            "installable": True,
            "install_label": ENGINE_INSTALLERS["local_mlx"]["label"],
            "models": mlx_models,
        },
        {
            "id": "ollama",
            "name": "Ollama",
            "kind": "local-server",
            "description": "Ollama 로컬 서버를 OpenAI 호환 엔진처럼 사용합니다.",
            "installed": ollama_installed,
            "installable": True,
            "install_label": ENGINE_INSTALLERS["ollama"]["label"],
            "models": ollama_models,
        },
    ]
    for spec in local_server_specs:
        support = engine_support_status(spec["id"])
        engines.append({
            "id": spec["id"],
            "name": spec["name"],
            "kind": "local-server",
            "description": spec["description"],
            "installed": engine_installed(spec["id"]),
            "supported": support["supported"],
            "support_reason": support["reason"],
            "installable": support["supported"] and spec["id"] in ENGINE_INSTALLERS,
            "install_label": ENGINE_INSTALLERS.get(spec["id"], {}).get("label"),
            "requires": spec["requires"],
            "models": (
                vllm_models if spec["id"] == "vllm"
                else lmstudio_models if spec["id"] == "lmstudio"
                else llamacpp_models if spec["id"] == "llamacpp"
                else ENGINE_MODEL_CATALOG.get(spec["id"], [])
            ),
            "note": spec.get("note") or support["reason"] or f"{spec['requires']} 설정 시 활성화됩니다.",
            "server_ready": spec.get("server_ready"),
        })
    for provider in ["openai", "openrouter", "groq", "together", "xai"]:
        env_key = next((item.get("requires") for item in cloud_by_provider.get(provider, []) if item.get("requires")), None)
        provider_models = []
        for model in cloud_by_provider.get(provider, []):
            cache = CLOUD_VERIFY_CACHE.get(model.get("id"))
            provider_models.append({
                **model,
                "verified": cache.get("ok") if cache else None,
                "verify_reason": cache.get("reason") if cache else None,
            })
        engines.append({
            "id": provider,
            "name": provider.title(),
            "kind": "cloud",
            "description": "OpenAI 호환 Chat Completions API로 cloud LLM을 실행합니다.",
            "installed": engine_installed(provider),
            "installable": True,
            "install_label": ENGINE_INSTALLERS[provider]["label"],
            "requires": env_key,
            "models": provider_models,
        })
    return engines

def runtime_features() -> Dict:
    return {
        "mode": APP_MODE,
        "public": IS_PUBLIC_MODE,
        "host": DEFAULT_HOST,
        "port": DEFAULT_PORT,
        "data_dir": str(DATA_DIR),
        "telegram_enabled": ENABLE_TELEGRAM,
        "graph_enabled": ENABLE_GRAPH,
        "autoload_models": AUTOLOAD_MODELS,
        "model_idle_unload_seconds": MODEL_IDLE_UNLOAD_SECONDS,
        "model_memory_policy": router.model_memory_policy(),
        "allow_local_models": ALLOW_LOCAL_MODELS,
        "security": {
            "host": DEFAULT_HOST,
            "require_auth": REQUIRE_AUTH,
            "invite_gate_enabled": INVITE_GATE_ENABLED,
            "keyring_available": keyring is not None,
            "plaintext_api_keys_allowed": ALLOW_PLAINTEXT_API_KEYS,
            "cors_allow_network": CORS_ALLOW_NETWORK,
        },
        "default_model": PUBLIC_MODEL if IS_PUBLIC_MODE else LOCAL_MODEL,
        "local_only_features": {
            "mlx": ALLOW_LOCAL_MODELS and not IS_PUBLIC_MODE,
            "telegram_bridge": ENABLE_TELEGRAM,
            "desktop_chrome_bridge": not IS_PUBLIC_MODE,
            "computer_use_bridge": not IS_PUBLIC_MODE,
        },
        "public_features": {
            "web_ui": True,
            "openai_compatible_models": True,
            "persistent_data_dir": str(DATA_DIR),
        },
    }

def install_engine(engine: str) -> Dict:
    if engine not in ENGINE_INSTALLERS:
        raise HTTPException(status_code=400, detail="지원하지 않는 엔진입니다.")
    installer = ENGINE_INSTALLERS[engine]
    required_binary = installer.get("requires_binary")
    if required_binary and shutil.which(required_binary) is None:
        raise HTTPException(status_code=400, detail=f"{required_binary}가 설치되어 있지 않아 자동 설치할 수 없습니다.")
    command = installer["command"]
    run_kwargs = {
        "cwd": str(BASE_DIR),
        "capture_output": True,
        "text": True,
        "timeout": 900,
        "check": False,
    }

    if engine == "vllm" and sys.platform == "darwin" and platform.machine() == "arm64":
        command = [
            "/bin/bash",
            "-lc",
            "set -euo pipefail; "
            "if [ ! -x /opt/homebrew/bin/python3.12 ]; then brew install python@3.12; fi; "
            "/opt/homebrew/bin/python3.12 -m venv ~/.venv-vllm-metal; "
            "~/.venv-vllm-metal/bin/pip install -U pip setuptools wheel; "
            "~/.venv-vllm-metal/bin/pip install vllm-metal",
        ]
    try:
        completed = subprocess.run(command, **run_kwargs)
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail="엔진 설치 시간이 초과되었습니다.")
    result = {
        "engine": engine,
        "command": " ".join(command),
        "returncode": completed.returncode,
        "stdout": completed.stdout[-12000:],
        "stderr": completed.stderr[-12000:],
        "installed": engine_installed(engine),
    }
    ollama = local_binary("ollama")
    if engine == "ollama" and completed.returncode == 0 and ollama:
        # Skip if already running to avoid orphan daemons.
        already_up = False
        try:
            probe = subprocess.run([ollama, "list"], capture_output=True, timeout=2, check=False)
            already_up = probe.returncode == 0
        except Exception:
            already_up = False
        if already_up:
            result["daemon_started"] = "already_running"
        else:
            try:
                # Detach so the daemon survives this request but doesn't become our zombie.
                subprocess.Popen(
                    [ollama, "serve"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                result["daemon_started"] = True
            except Exception as e:
                logging.warning("ollama serve spawn failed: %s", e)
                result["daemon_started"] = False
    return result


def _resolve_model_alias(model_id: str, engine: Optional[str] = None) -> str:
    raw = model_id.strip()
    engine_hint = (engine or "").strip().lower()
    provider: Optional[str] = None
    model_name = raw
    if ":" in raw:
        prefix, rest = raw.split(":", 1)
        prefix = prefix.strip().lower()
        if prefix in {"ollama", "vllm", "lmstudio", "llamacpp", "local_mlx", "mlx"}:
            provider = "local_mlx" if prefix in {"local_mlx", "mlx"} else prefix
            model_name = rest.strip()
    provider = provider or ("local_mlx" if engine_hint in {"", "local_mlx", "mlx"} else engine_hint)
    aliases = MODEL_ENGINE_ALIASES.get(model_name.lower())
    if not aliases:
        return raw
    mapped = aliases.get(provider)
    if not mapped:
        return raw
    return mapped if provider == "local_mlx" else f"{provider}:{mapped}"


def normalize_local_model_request(model_id: str, engine: Optional[str] = None) -> str:
    model_id = _resolve_model_alias(model_id, engine)
    engine = (engine or "").strip().lower()
    if engine in {"local_mlx", "mlx"} and model_id.startswith(("local_mlx:", "mlx:")):
        return model_id.split(":", 1)[1].strip()
    if engine and engine not in {"local_mlx", "mlx"} and ":" not in model_id:
        return f"{engine}:{model_id}"
    return model_id


def ensure_engine_ready(engine: str) -> Dict[str, object]:
    engine = "local_mlx" if engine == "mlx" else engine
    if engine not in ENGINE_INSTALLERS and engine not in OPENAI_COMPATIBLE_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 엔진입니다: {engine}")
    support = engine_support_status(engine)
    if not support["supported"]:
        raise HTTPException(status_code=400, detail=str(support["reason"]))

    if engine_installed(engine):
        if engine == "local_mlx":
            ensure_mlx_runtime()
        return {"engine": engine, "installed": True, "installed_now": False}

    if engine not in ENGINE_INSTALLERS:
        raise HTTPException(status_code=400, detail=f"{engine} 엔진 설치 방법이 등록되어 있지 않습니다.")

    result = install_engine(engine)
    if result.get("returncode") not in (0, None) or not engine_installed(engine):
        detail = result.get("stderr") or result.get("stdout") or f"{engine} 설치에 실패했습니다."
        raise HTTPException(status_code=500, detail=str(detail)[-2000:])

    if engine == "local_mlx":
        ensure_mlx_runtime()
    return {"engine": engine, "installed": True, "installed_now": True, "install": result}


def build_model_resolution(
    input_id: str,
    engine: Optional[str],
    *,
    user_email: Optional[str] = None,
    display_name: Optional[str] = None,
) -> _ModelResolution:
    """피드백 #1/#2 공용 ModelResolution 생성기.

    사용자가 클릭한 input_id + engine 힌트를 받아 모든 단계가 공유할
    canonical identity를 만든다.
    """
    normalized = normalize_local_model_request(input_id, engine)
    return _ModelResolution.from_request(
        normalized,
        engine=engine,
        user_email=user_email,
        display_name=display_name or input_id,
        engine_aliases=MODEL_ENGINE_ALIASES,
    )


_LOCAL_SMOKE_ENGINES = {"local_mlx", "ollama", "vllm", "lmstudio", "llamacpp"}


async def _smoke_test_loaded_model(
    resolution: _ModelResolution,
    *,
    api_key_override: Optional[str] = None,
) -> Dict[str, object]:
    """로드 직후 짧은 채팅 테스트를 돌려 ready_to_chat 여부를 판정한다.

    Cloud(OpenAI/Anthropic/OpenRouter 등) 모델은 사용자 비용 발생 가능성 때문에 skip.
    실패해도 예외를 던지지 않는다. 결과는 compat_cache에도 기록된다.
    """
    if (resolution.engine or "").lower() not in _LOCAL_SMOKE_ENGINES:
        profile = _ensure_compat_profile(resolution.load_id, resolution.engine)
        return {
            "ok": True,
            "reason": "skipped (cloud model — smoke test would incur cost)",
            "answer": None,
            "profile": profile.to_dict(),
            "skipped": True,
        }
    try:
        text = await asyncio.wait_for(
            router.generate(
                _SMOKE_PROMPT,
                context=None,
                max_tokens=128,
                temperature=0.1,
            ),
            timeout=30,
        )
    except Exception as exc:  # pragma: no cover - generator may not exist on all engines
        reason = str(exc)[:200] or "generation_failed"
        profile = _record_smoke_result(
            resolution.load_id, resolution.engine, False, reason, status="failed"
        )
        return {
            "ok": False,
            "status": "failed",
            "reason": reason,
            "answer": None,
            "profile": profile.to_dict(),
        }

    profile = _ensure_compat_profile(resolution.load_id, resolution.engine)
    cleaned = _compat_fast_postprocess(str(text or ""), profile.to_dict())
    # item 3-3: ok / degraded / failed 3분류. degraded는 채팅은 가능하다.
    status, reason = _classify_smoke_response(cleaned)
    ok = status != "failed"
    profile = _record_smoke_result(
        resolution.load_id, resolution.engine, ok, reason, status=status
    )
    return {
        "ok": ok,
        "status": status,
        "reason": reason,
        "answer": cleaned,
        "profile": profile.to_dict(),
    }


async def prepare_and_load_model(
    model_id: str,
    request: Request,
    engine: Optional[str] = None,
    user_email: Optional[str] = None,
    adapter_path: Optional[str] = None,
    draft_model_id: Optional[str] = None,
) -> Dict[str, object]:
    model_id = normalize_local_model_request(model_id, engine)
    if not model_id:
        raise HTTPException(status_code=400, detail="모델 식별자가 비어 있습니다.")

    # 피드백 #1: ModelResolution을 모든 단계가 공유한다.
    resolution = _ModelResolution.from_request(
        model_id,
        engine=engine,
        user_email=user_email or get_current_user(request),
        engine_aliases=MODEL_ENGINE_ALIASES,
    )

    parsed_provider, parsed_model = parse_model_ref(model_id)
    if parsed_provider == "mlx":
        parsed_provider = "local_mlx"

    local_engines = {"local_mlx", "ollama", "vllm", "lmstudio", "llamacpp"}
    install_result: Dict[str, object] = {}
    download_result: Optional[Dict[str, object]] = None

    if parsed_provider in local_engines:
        install_result = ensure_engine_ready(parsed_provider)

    if parsed_provider == "local_mlx":
        explicit_path = Path(parsed_model).expanduser()
        if not explicit_path.exists() and not hf_model_ready(parsed_model, "local_mlx"):
            download_result = download_hf_model(parsed_model, "local_mlx")
    elif parsed_provider == "ollama":
        ensure_ollama_server()
        ollama = local_binary("ollama")
        if not ollama:
            raise HTTPException(status_code=400, detail="Ollama가 설치되지 않았습니다.")
        if parsed_model not in get_ollama_pulled_models():
            completed = subprocess.run(
                [ollama, "pull", parsed_model],
                capture_output=True,
                text=True,
                timeout=900,
                check=False,
            )
            if completed.returncode != 0:
                raise HTTPException(status_code=500, detail=completed.stderr[-2000:] or "Ollama 모델 다운로드 실패")
            download_result = {"provider": "ollama", "model": parsed_model, "returncode": completed.returncode}
    elif parsed_provider == "vllm":
        ensure_vllm_server(parsed_model)
        download_result = {"provider": "vllm", "model": parsed_model, "server_ready": True}
    elif parsed_provider == "llamacpp":
        ensure_llamacpp_server(parsed_model)
        download_result = {"provider": "llamacpp", "model": parsed_model, "server_ready": True}
    elif parsed_provider == "lmstudio":
        ensured = ensure_lmstudio_model(parsed_model)
        resolved_model = str(
            ensured.get("instance_id")
            or ensured.get("resolved_model")
            or parsed_model
        ).strip()
        parsed_model = resolved_model
        model_id = f"lmstudio:{resolved_model}"
        download_result = ensured

    effective_email = (user_email or get_current_user(request) or "").strip()
    user_api_key = get_user_api_key(effective_email, parsed_provider) if parsed_provider != "local_mlx" else None
    msg = await router.load_model(
        model_id,
        adapter_path,
        draft_model_id=draft_model_id,
        api_key_override=user_api_key,
        owner=effective_email or None,
    )
    # 피드백 #1/#2: 로드 직후 ModelResolution을 실제 current로 동기화하고 smoke test 수행.
    resolution.update_after_load(actual_current=router.current_model_id)
    smoke_result: Dict[str, object] = {}
    ready_to_chat = True
    compat_status = "ok"
    try:
        smoke_result = await _smoke_test_loaded_model(resolution, api_key_override=user_api_key)
        ready_to_chat = bool(smoke_result.get("ok"))
        # item 3-3: smoke 결과의 3분류(ok/degraded/failed)를 그대로 노출한다.
        compat_status = str(smoke_result.get("status") or ("ok" if ready_to_chat else "degraded"))
    except Exception as exc:  # never break load on smoke test failures
        logging.warning("smoke test failed for %s: %s", resolution.load_id, exc)
        compat_status = "unknown"
    return {
        "status": "ok",
        "message": msg,
        "model": model_id,
        "current": router.current_model_id,
        "engine": parsed_provider,
        "installed_now": bool(install_result.get("installed_now")),
        "download": download_result,
        "resolution": resolution.to_dict(),
        "downloaded": True,
        "loaded": True,
        "ready_to_chat": ready_to_chat,
        "compatibility_status": compat_status,
        "smoke_test": smoke_result,
    }


def sse_event(event: str, data: Dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def prepare_and_load_model_stream(
    model_id: str,
    request: Request,
    engine: Optional[str] = None,
    user_email: Optional[str] = None,
) -> AsyncIterator[str]:
    model_id = normalize_local_model_request(model_id, engine)
    if not model_id:
        raise HTTPException(status_code=400, detail="모델 식별자가 비어 있습니다.")

    parsed_provider, parsed_model = parse_model_ref(model_id)
    if parsed_provider == "mlx":
        parsed_provider = "local_mlx"

    work_queue: "queue.Queue[Dict[str, object]]" = queue.Queue()
    work_result: Dict[str, object] = {}

    def emit_progress(payload: Dict[str, object]) -> None:
        work_queue.put({"kind": "progress", "data": payload})

    def blocking_prepare() -> None:
        try:
            local_engines = {"local_mlx", "ollama", "vllm", "lmstudio", "llamacpp"}
            install_result: Dict[str, object] = {}
            download_result: Optional[Dict[str, object]] = None
            prepared_model_id = model_id
            prepared_model_name = parsed_model

            if parsed_provider in local_engines:
                emit_progress(model_download_progress_payload(
                    "engine",
                    "실행 엔진을 확인하는 중입니다.",
                    percent=2,
                    indeterminate=True,
                ))
                install_result = ensure_engine_ready(parsed_provider)
                emit_progress(model_download_progress_payload(
                    "engine",
                    "실행 엔진 준비가 완료되었습니다.",
                    percent=10,
                    indeterminate=False,
                ))

            if parsed_provider == "local_mlx":
                explicit_path = Path(parsed_model).expanduser()
                if explicit_path.exists():
                    download_result = {"model": parsed_model, "path": str(explicit_path), "cached": True}
                    emit_progress(model_download_progress_payload(
                        "download",
                        "로컬 모델 경로를 확인했습니다.",
                        percent=100,
                        detail=str(explicit_path),
                        eta_seconds=0,
                    ))
                elif not hf_model_ready(parsed_model, "local_mlx"):
                    download_result = download_hf_model(parsed_model, "local_mlx", progress_emit=emit_progress)
                else:
                    download_result = {"model": parsed_model, "path": str(hf_model_dir(parsed_model)), "cached": True}
                    emit_progress(model_download_progress_payload(
                        "download",
                        "이미 다운로드된 모델을 확인했습니다.",
                        percent=100,
                        eta_seconds=0,
                    ))
            elif parsed_provider == "ollama":
                emit_progress(model_download_progress_payload(
                    "engine",
                    "Ollama 서버를 확인하는 중입니다.",
                    percent=12,
                    indeterminate=True,
                ))
                ensure_ollama_server()
                if parsed_model not in get_ollama_pulled_models():
                    download_result = pull_ollama_model_with_progress(parsed_model, progress_emit=emit_progress)
                else:
                    download_result = {"provider": "ollama", "model": parsed_model, "cached": True}
                    emit_progress(model_download_progress_payload(
                        "download",
                        "이미 다운로드된 Ollama 모델을 확인했습니다.",
                        percent=100,
                        detail=parsed_model,
                        eta_seconds=0,
                    ))
            elif parsed_provider == "vllm":
                if not hf_model_ready(parsed_model, "vllm"):
                    download_result = download_hf_model(parsed_model, "vllm", progress_emit=emit_progress)
                else:
                    download_result = {"provider": "vllm", "model": parsed_model, "cached": True}
                    emit_progress(model_download_progress_payload(
                        "download",
                        "이미 다운로드된 모델을 확인했습니다.",
                        percent=100,
                        detail=parsed_model,
                        eta_seconds=0,
                    ))
                emit_progress(model_download_progress_payload(
                    "server",
                    "vLLM 서버를 시작하는 중입니다.",
                    percent=92,
                    indeterminate=True,
                ))
                ensure_vllm_server(parsed_model)
                download_result = {**(download_result or {}), "provider": "vllm", "model": parsed_model, "server_ready": True}
            elif parsed_provider == "llamacpp":
                if not hf_model_ready(parsed_model, "llamacpp"):
                    download_result = download_hf_model(parsed_model, "llamacpp", progress_emit=emit_progress)
                else:
                    download_result = {"provider": "llamacpp", "model": parsed_model, "cached": True}
                    emit_progress(model_download_progress_payload(
                        "download",
                        "이미 다운로드된 GGUF 모델을 확인했습니다.",
                        percent=100,
                        detail=parsed_model,
                        eta_seconds=0,
                    ))
                emit_progress(model_download_progress_payload(
                    "server",
                    "llama.cpp 서버를 시작하는 중입니다.",
                    percent=92,
                    indeterminate=True,
                ))
                ensure_llamacpp_server(parsed_model)
                download_result = {**(download_result or {}), "provider": "llamacpp", "model": parsed_model, "server_ready": True}
            elif parsed_provider == "lmstudio":
                emit_progress(model_download_progress_payload(
                    "download",
                    "LM Studio 모델을 확인하는 중입니다.",
                    percent=35,
                    indeterminate=True,
                ))
                ensured = ensure_lmstudio_model(parsed_model)
                resolved_model = str(
                    ensured.get("instance_id")
                    or ensured.get("resolved_model")
                    or parsed_model
                ).strip()
                prepared_model_name = resolved_model
                prepared_model_id = f"lmstudio:{resolved_model}"
                download_result = ensured
            else:
                emit_progress(model_download_progress_payload(
                    "engine",
                    "모델 연결을 준비하는 중입니다.",
                    percent=30,
                    indeterminate=True,
                ))

            work_result.update({
                "model_id": prepared_model_id,
                "parsed_provider": parsed_provider,
                "parsed_model": prepared_model_name,
                "install_result": install_result,
                "download_result": download_result,
            })
            work_queue.put({"kind": "done"})
        except HTTPException as exc:
            work_queue.put({"kind": "error", "status_code": exc.status_code, "detail": exc.detail})
        except Exception as exc:
            logging.exception("model prepare stream worker failed")
            work_queue.put({"kind": "error", "status_code": 500, "detail": str(exc)[-2000:]})

    worker = threading.Thread(target=blocking_prepare, daemon=True)
    worker.start()

    while True:
        item = await asyncio.to_thread(work_queue.get)
        kind = item.get("kind")
        if kind == "progress":
            yield sse_event("progress", item["data"])
        elif kind == "error":
            raise HTTPException(
                status_code=int(item.get("status_code") or 500),
                detail=item.get("detail") or "모델 준비에 실패했습니다.",
            )
        elif kind == "done":
            break

    prepared_model_id = str(work_result.get("model_id") or model_id)
    prepared_provider = str(work_result.get("parsed_provider") or parsed_provider)
    install_result = work_result.get("install_result") or {}
    download_result = work_result.get("download_result")

    yield sse_event("progress", model_download_progress_payload(
        "load",
        "모델을 메모리에 로드하는 중입니다.",
        percent=96,
        indeterminate=True,
    ))

    effective_email = (user_email or get_current_user(request) or "").strip()
    user_api_key = get_user_api_key(effective_email, prepared_provider) if prepared_provider != "local_mlx" else None
    msg = await router.load_model(
        prepared_model_id,
        None,
        draft_model_id=None,
        api_key_override=user_api_key,
        owner=effective_email or None,
    )
    # 피드백 #1/#2: SSE에도 ModelResolution과 smoke test 결과를 같이 내려준다.
    resolution_stream = _ModelResolution.from_request(
        prepared_model_id,
        engine=prepared_provider,
        user_email=effective_email or None,
        engine_aliases=MODEL_ENGINE_ALIASES,
    )
    resolution_stream.update_after_load(actual_current=router.current_model_id)
    yield sse_event("progress", model_download_progress_payload(
        "smoke_test",
        "채팅 호환성 테스트 중입니다.",
        percent=98,
        indeterminate=True,
    ))
    smoke_result: Dict[str, object] = {}
    ready_to_chat = True
    compat_status = "ok"
    try:
        smoke_result = await _smoke_test_loaded_model(resolution_stream, api_key_override=user_api_key)
        ready_to_chat = bool(smoke_result.get("ok"))
        # item 3-3: smoke 결과의 3분류(ok/degraded/failed)를 그대로 노출한다.
        compat_status = str(smoke_result.get("status") or ("ok" if ready_to_chat else "degraded"))
    except Exception as exc:
        logging.warning("smoke test (stream) failed for %s: %s", resolution_stream.load_id, exc)
        compat_status = "unknown"
    result = {
        "status": "ok",
        "message": msg,
        "model": prepared_model_id,
        "current": router.current_model_id,
        "engine": prepared_provider,
        "installed_now": bool(isinstance(install_result, dict) and install_result.get("installed_now")),
        "download": download_result,
        "resolution": resolution_stream.to_dict(),
        "downloaded": True,
        "loaded": True,
        "ready_to_chat": ready_to_chat,
        "compatibility_status": compat_status,
        "smoke_test": smoke_result,
    }
    yield sse_event("progress", model_download_progress_payload(
        "done",
        "모델 준비가 완료되었습니다.",
        percent=100,
        eta_seconds=0,
    ))
    yield sse_event("done", result)


CLOUD_VERIFY_CACHE: Dict[str, Dict] = {}
CLOUD_VERIFY_TTL_SECONDS = 600

async def _probe_cloud_model(model_ref: str) -> Dict[str, object]:
    provider, model_name = parse_model_ref(model_ref)
    config = OPENAI_COMPATIBLE_PROVIDERS.get(provider)
    if not config:
        return {"ok": False, "reason": f"Unsupported provider: {provider}"}

    api_key = os.getenv(config["env_key"]) or config.get("api_key_fallback")
    if not api_key:
        return {"ok": False, "reason": f"Missing API key: {config['env_key']}"}

    base_url = os.getenv(config.get("base_url_env", "")) if config.get("base_url_env") else None
    base_url = base_url or config.get("base_url")
    client_kwargs = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url

    try:
        client = AsyncOpenAI(**client_kwargs)
        await asyncio.wait_for(
            client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
                temperature=0,
            ),
            timeout=15,
        )
        return {"ok": True, "reason": "ok"}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:220]}


async def verify_cloud_models(force: bool = False, provider_filter: Optional[str] = None) -> Dict[str, Dict]:
    now = time.time()
    cloud_items = [item for item in router.detected_cloud_models() if item.get("tag") == "cloud"]
    if provider_filter:
        cloud_items = [item for item in cloud_items if item.get("provider") == provider_filter]

    results: Dict[str, Dict] = {}
    for item in cloud_items:
        model_ref = item["id"]
        cached = CLOUD_VERIFY_CACHE.get(model_ref)
        if not force and cached and (now - cached.get("ts", 0) <= CLOUD_VERIFY_TTL_SECONDS):
            results[model_ref] = cached
            continue
        if item.get("available") is False:
            record = {"ok": False, "reason": item.get("requires") or "API key missing", "ts": now}
            CLOUD_VERIFY_CACHE[model_ref] = record
            results[model_ref] = record
            continue
        probe = await _probe_cloud_model(model_ref)
        record = {"ok": bool(probe.get("ok")), "reason": probe.get("reason", ""), "ts": now}
        CLOUD_VERIFY_CACHE[model_ref] = record
        results[model_ref] = record
    return results

@app.get("/health")
async def health(request: Request):
    base = {"status": "ok", "version": "0.5.1", "mode": APP_MODE}
    if not get_current_user(request) and REQUIRE_AUTH:
        return base
    engines = await asyncio.to_thread(engine_status)
    return {
        **base,
        "current_model": router.current_model_id,
        "loaded_models": router.loaded_model_ids,
        "device": "Apple Silicon MLX" if not IS_PUBLIC_MODE else "Public cloud/API runtime",
        "features": runtime_features(),
        "providers": router.detected_cloud_models(),
        "engines": engines,
    }


@app.get("/mode")
@app.get("/runtime_features")
async def mode():
    return runtime_features()


@app.get("/engines")
async def engines():
    return {"engines": await asyncio.to_thread(engine_status), "current": router.current_model_id}


@app.post("/engines/install")
async def engines_install(req: InstallEngineRequest, request: Request):
    require_user(request)
    return install_engine(req.engine)

@app.post("/engines/verify-cloud")
async def engines_verify_cloud(req: VerifyCloudRequest, request: Request):
    require_user(request)
    results = await verify_cloud_models(force=req.force, provider_filter=req.provider)
    return {"verified": results, "ttl_seconds": CLOUD_VERIFY_TTL_SECONDS}


@app.post("/engines/pull-model")
async def pull_ollama_model(req: PullModelRequest, request: Request):
    require_user(request)
    model_ref = normalize_local_model_request(req.model, None)
    if not model_ref:
        raise HTTPException(status_code=400, detail="모델 식별자가 비어 있습니다.")

    if ":" in model_ref and model_ref.split(":", 1)[0].strip().lower() in {"ollama", "vllm", "lmstudio", "llamacpp", "local_mlx", "mlx"}:
        provider, model_name = model_ref.split(":", 1)
        provider = provider.strip().lower()
        model_name = model_name.strip()
    else:
        provider, model_name = "local_mlx", model_ref

    if not model_name:
        raise HTTPException(status_code=400, detail="모델 이름이 비어 있습니다.")

    if provider == "ollama":
        ensure_ollama_server()
        ollama = local_binary("ollama")
        if not ollama:
            raise HTTPException(status_code=400, detail="Ollama가 설치되지 않았습니다.")
        try:
            completed = subprocess.run(
                [ollama, "pull", model_name],
                capture_output=True, text=True, timeout=900, check=False,
            )
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=408, detail="모델 다운로드 시간이 초과되었습니다.")
        if completed.returncode != 0:
            raise HTTPException(status_code=500, detail=completed.stderr[-2000:] or "pull 실패")
        return {"provider": provider, "model": model_name, "returncode": completed.returncode}

    if provider == "lmstudio":
        raise HTTPException(
            status_code=400,
            detail=(
                "LM Studio 모델은 Lattice에서 Hugging Face로 pull하지 않습니다. "
                "LM Studio 앱에서 모델을 다운로드하고 Local Server를 켠 뒤 모델을 로드하세요. "
                "그러면 모델 선택창에 실제 /v1/models 항목이 표시됩니다."
            ),
        )

    if provider in {"vllm", "llamacpp", "local_mlx", "mlx"}:
        download_provider = "local_mlx" if provider == "mlx" else provider
        result = download_hf_model(model_name, download_provider)
        return {"provider": provider, "model": model_name, "returncode": 0, **result}

    raise HTTPException(status_code=400, detail=f"{provider} 엔진 모델 다운로드는 아직 자동화되지 않았습니다.")


@app.post("/engines/prepare-model")
async def engines_prepare_model(req: PrepareModelRequest, request: Request):
    require_user(request)
    return await prepare_and_load_model(
        req.model,
        request,
        engine=req.engine,
        user_email=req.user_email,
    )


@app.post("/engines/prepare-model/stream")
async def engines_prepare_model_stream(req: PrepareModelRequest, request: Request):
    require_user(request)

    async def event_stream():
        try:
            async for chunk in prepare_and_load_model_stream(
                req.model,
                request,
                engine=req.engine,
                user_email=req.user_email,
            ):
                yield chunk
        except HTTPException as exc:
            yield sse_event("error", {
                "status_code": exc.status_code,
                "detail": exc.detail or "모델 준비에 실패했습니다.",
            })
        except Exception as exc:
            logging.exception("model prepare stream failed")
            yield sse_event("error", {
                "status_code": 500,
                "detail": str(exc)[-1000:] or "모델 준비에 실패했습니다.",
            })

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/setup/set-api-key")
async def set_api_key(req: SetApiKeyRequest, request: Request):
    from llm_router import OPENAI_COMPATIBLE_PROVIDERS
    config = OPENAI_COMPATIBLE_PROVIDERS.get(req.provider)
    if not config:
        raise HTTPException(status_code=400, detail="알 수 없는 프로바이더입니다.")
    if not req.key.strip():
        raise HTTPException(status_code=400, detail="API 키가 비어있습니다.")
    current_user = get_current_user(request)
    if REQUIRE_AUTH and not current_user:
        raise HTTPException(status_code=401, detail="인증이 필요합니다.")
    # req.user_email 을 통한 타 계정 위조를 방지: 관리자가 아니면 본인 이메일만 허용
    if req.user_email and req.user_email != current_user:
        users = load_users()
        if get_user_role(current_user or "", users) != "admin":
            raise HTTPException(status_code=403, detail="다른 사용자의 API 키를 설정할 권한이 없습니다.")
    target_email = (req.user_email or current_user or "").strip()
    if not target_email:
        raise HTTPException(status_code=400, detail="사용자 식별이 필요합니다. 로그인 후 다시 시도하세요.")
    set_user_api_key(target_email, req.provider, req.key.strip())
    return {"ok": True, "provider": req.provider, "user_email": target_email, "scope": "user"}


def _recommended_with_engine_options(items: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """피드백 #1: 추천 모델에 엔진별 선택지(engine_options)를 붙여 내려준다.

    프론트에서 추천 카드를 누르는 순간 어느 엔진/실제 모델로 다운로드/로드할지가
    이미 확정되도록 한다.
    """
    out: List[Dict[str, object]] = []
    for item in items:
        base = {
            "id": item["id"],
            "name": item["name"],
            "tag": item["tag"],
            "size": item["size"],
            "display_name": item.get("name") or item.get("id"),
        }
        short_id = str(item["id"]).lower()
        aliases = MODEL_ENGINE_ALIASES.get(short_id) or {}
        options: List[Dict[str, str]] = []
        for engine_name in ("local_mlx", "ollama", "lmstudio", "llamacpp", "vllm"):
            real = aliases.get(engine_name)
            if not real:
                continue
            options.append({
                "engine": engine_name,
                "model_id": real,
                "load_id": real if engine_name == "local_mlx" else f"{engine_name}:{real}",
            })
        # 어느 엔진도 alias가 없으면 local_mlx 카탈로그 자체를 사용한다.
        if not options:
            options.append({
                "engine": "local_mlx",
                "model_id": item["id"],
                "load_id": item["id"],
            })
        base["engine_options"] = options
        base["recommended_engine"] = options[0]["engine"]
        out.append(base)
    return out


@app.get("/models")
async def list_models():
    """HuggingFace 추천 모델 목록 및 로드 상태 반환"""
    recommended = _recommended_with_engine_options(
        list(filter_lower_family_versions(ENGINE_MODEL_CATALOG.get("local_mlx", [])))
    )
    return {
        "recommended": recommended,
        "cloud": router.detected_cloud_models(),
        "engines": await asyncio.to_thread(engine_status),
        "loaded": router.loaded_model_ids,
        "current": router.current_model_id,
        "compat_profiles": _list_compat_profiles(),
    }


@app.get("/models/compat-profiles")
async def list_model_compat_profiles(request: Request):
    """피드백 #3: Model Compatibility Layer 캐시 상태를 조회한다."""
    require_user(request)
    return {"profiles": _list_compat_profiles()}


# ── Model Management ───────────────────────────────────────────────────────────

@app.post("/models/load")
async def load_model(req: LoadModelRequest, request: Request):
    """모델 로드 (이미 로드됐으면 캐시에서 즉시 반환)"""
    try:
        model_id = req.model_id
        requested_engine = req.engine or (model_id.split(":", 1)[0] if ":" in model_id else "local_mlx")
        if IS_PUBLIC_MODE and not ALLOW_LOCAL_MODELS and requested_engine in {"local_mlx", "mlx"}:
            raise HTTPException(
                status_code=400,
                detail="Public mode blocks local MLX model loading. Use openai:, openrouter:, groq:, together:, or set LATTICEAI_ALLOW_LOCAL_MODELS=true.",
            )
        return await prepare_and_load_model(
            model_id,
            request,
            engine=req.engine,
            user_email=req.user_email,
            adapter_path=req.adapter_path,
            draft_model_id=req.draft_model_id,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/models/switch/{model_id:path}")
async def switch_model(model_id: str, request: Request):
    """이미 로드된 모델 중 활성 모델 전환 (즉시, 재로드 없음)"""
    require_user(request)
    try:
        router.switch_model(model_id)
        return {"status": "ok", "current": router.current_model_id}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not loaded. Call /models/load first.")


@app.delete("/models/unload/{model_id:path}")
async def unload_model(model_id: str, request: Request):
    """모델 언로드 → 메모리 해제"""
    require_user(request)
    router.unload_model(model_id)
    return {"status": "ok", "unloaded": model_id}


@app.delete("/models/unload-all")
async def unload_all_models(request: Request):
    """로드된 모든 모델 언로드 → 메모리 해제"""
    require_user(request)
    unloaded = router.loaded_model_ids
    router.unload_all()
    return {"status": "ok", "unloaded": unloaded}


# ── Chat / Completion ──────────────────────────────────────────────────────────

@app.post("/chat")
async def chat(req: ChatRequest, request: Request):
    current_user = require_user(request)
    enforce_rate_limit(current_user, "chat")
    img_len = len(req.image_data) if req.image_data else 0
    print(
        f"🧪 /chat request: stream={req.stream} image_data_len={img_len} "
        f"message_len={len(req.message or '')}"
    )
    effective_email = req.user_email or current_user or None
    history_user = get_history_user(effective_email, req.user_nickname)

    if is_network_status_request(req.message):
        history_message = f"{req.message}\n[Image attached]" if req.image_data else req.message
        save_to_history("user", history_message, source=req.source or "web", conversation_id=req.conversation_id, **history_user)
        try:
            answer = format_network_status(network_status())
        except ToolError as exc:
            answer = f"네트워크 정보를 확인하지 못했습니다: {exc}"
        save_to_history("assistant", answer, source=req.source or "web", conversation_id=req.conversation_id, **history_user)
        if req.source != "telegram":
            asyncio.create_task(broadcast_web_chat("user", req.message))
            asyncio.create_task(broadcast_web_chat("assistant", answer))
        if req.stream:
            return StreamingResponse(
                single_text_stream(answer),
                media_type="text/event-stream",
                headers={"X-Model": "network_status"},
            )
        return JSONResponse(content={"response": answer})

    if is_clear_command(req.message):
        command = req.message.strip().lower()
        clear_scope = "all" if command == "/clear_all" else "conversation"
        if ENABLE_GRAPH and KNOWLEDGE_GRAPH:
            try:
                KNOWLEDGE_GRAPH.ingest_event(
                    "ClearEvent",
                    f"{command} requested",
                    user_email=effective_email,
                    user_nickname=req.user_nickname,
                    source=req.source or "web",
                    conversation_id=req.conversation_id,
                    metadata={"command": command, "scope": clear_scope},
                )
            except Exception as e:
                logging.warning("knowledge graph clear event ingest failed: %s", e)
        if command == "/clear_all":
            result = clear_history(0)
            answer = f"채팅창을 정리했습니다. 화면에서 제거 {result.get('removed', 0)}개. 감사 로그와 지식 그래프/RAG 데이터는 유지됩니다."
        else:
            if req.conversation_id:
                result = clear_conversation(req.conversation_id)
                answer = f"현재 대화방 채팅창을 정리했습니다. 화면에서 제거 {result.get('removed', 0)}개. 감사 로그와 지식 그래프/RAG 데이터는 유지됩니다."
            else:
                result = clear_history(0)
                answer = f"채팅창을 정리했습니다. 화면에서 제거 {result.get('removed', 0)}개. 감사 로그와 지식 그래프/RAG 데이터는 유지됩니다."
        append_audit_event(
            "clear_command",
            user_email=effective_email,
            user_nickname=req.user_nickname,
            source=req.source or "web",
            conversation_id=req.conversation_id,
            command=command,
            scope=clear_scope,
            removed=result.get("removed", 0),
            kept=result.get("kept", 0),
        )
        if req.stream:
            return StreamingResponse(
                single_text_stream(answer),
                media_type="text/event-stream",
                headers={"X-Model": "history"},
            )
        return JSONResponse(content={"response": answer})

    if is_current_url_request(req.message) and req.client_url:
        answer = f"현재 페이지 URL: {req.client_url}"
        save_to_history("user", req.message, source=req.source or "web", conversation_id=req.conversation_id, **history_user)
        save_to_history("assistant", answer, source=req.source or "web", conversation_id=req.conversation_id, **history_user)
        if req.source != "telegram":
            asyncio.create_task(broadcast_web_chat("user", req.message))
            asyncio.create_task(broadcast_web_chat("assistant", answer))
        if req.stream:
            return StreamingResponse(
                single_text_stream(answer),
                media_type="text/event-stream",
                headers={"X-Model": "client_url"},
            )
        return JSONResponse(content={"response": answer})

    if not router.current_model_id:
        detail = "No model loaded. Call /models/load first."
        if IS_PUBLIC_MODE:
            detail = f"No public model loaded. Set OPENAI_API_KEY and LATTICEAI_PUBLIC_MODEL={PUBLIC_MODEL}, or call /models/load with an OpenAI-compatible model."
        raise HTTPException(status_code=400, detail=detail)

    if req.model and req.model != router.current_model_id:
        if req.model not in router.loaded_model_ids:
            raise HTTPException(status_code=404, detail=f"Model '{req.model}' not loaded.")
        router.switch_model(req.model)

    lang = detect_language(req.message)
    context = f"[LANGUAGE: {_LANG_HINT[lang]}]\n" + (req.context or "")
    try:
        knowledge_context = gardener.get_relevant_context(req.message)
        if knowledge_context:
            context += f"\n\n[LOCAL KNOWLEDGE BASE]\n{knowledge_context}"
            print(f"📖 Context reinforced with local knowledge.")
    except Exception as e:
        logging.warning("Knowledge reinforcement skipped: %s", e)

    is_doc_gen = detect_document_intent(req.message)
    doc_gen_context_result = None

    try:
        if ENABLE_GRAPH and KNOWLEDGE_GRAPH:
            if is_doc_gen:
                doc_gen_context_result = retrieve_context_for_generation(
                    KNOWLEDGE_GRAPH, req.message, max_results=10, max_hops=2,
                )
                graph_md = doc_gen_context_result.get("context_markdown", "")
                if graph_md:
                    context += f"\n\n[KNOWLEDGE GRAPH — Document Generation Context]\n{graph_md}"
                    print("📝 Document generation context retrieved from knowledge graph.")
            else:
                graph_context = KNOWLEDGE_GRAPH.context_for_query(req.message)
                if graph_context:
                    context += f"\n\n[KNOWLEDGE GRAPH]\n{graph_context}"
                    print("🕸️ Context reinforced with knowledge graph.")
    except Exception as e:
        logging.warning("Knowledge graph reinforcement skipped: %s", e)

    if req.image_data:
        screenshot_context = extract_screenshot_context(req.image_data)
        if screenshot_context:
            context += f"\n\n{screenshot_context}"

    if CONFIG.auto_read_chat_paths:
        _file_path_re = re.compile(r'(?:^|[\s\'\"(])((~|/[\w.])[^\s\'")\]]*)', re.MULTILINE)
        for _m in _file_path_re.finditer(req.message or ""):
            _fpath = _m.group(1).strip()
            try:
                _result = local_read(_fpath)
                _fcontent = _result.get("content", "")
                if _fcontent:
                    context += f"\n\n[FILE: {_fpath}]\n```\n{_fcontent[:6000]}\n```"
                    print(f"📂 Auto-injected file context: {_fpath}")
            except Exception:
                pass

    history_message = f"{req.message}\n[Image attached]" if req.image_data else req.message
    save_to_history("user", history_message, source=req.source or "web", conversation_id=req.conversation_id, **history_user)
    if req.source != "telegram":
        asyncio.create_task(broadcast_web_chat("user", req.message))

    if is_doc_gen and ENABLE_GRAPH and KNOWLEDGE_GRAPH:
        conv_key = req.conversation_id or "default"
        session = _doc_gen_sessions.get(conv_key)
        if session is None:
            session = DocumentGenerationSession()
            _doc_gen_sessions[conv_key] = session
        graph_md = (doc_gen_context_result or {}).get("context_markdown", "")
        system_prompt = session.get_system_prompt(graph_md)
        sources = (doc_gen_context_result or {}).get("sources", [])
        footnote = format_sources_footnote(sources)

        if req.stream:
            async def _stream_doc_gen():
                collected = []
                async for chunk in router.stream_generate_document(
                    req.message, system_prompt,
                    max_tokens=req.max_tokens or 8192,
                    temperature=req.temperature or 0.3,
                ):
                    collected.append(chunk)
                    yield f"data: {json.dumps({'text': chunk}, ensure_ascii=False)}\n\n"
                full_text = "".join(collected)
                if footnote:
                    yield f"data: {json.dumps({'text': footnote}, ensure_ascii=False)}\n\n"
                    full_text += footnote
                session.update(graph_md, full_text, req.conversation_id)
                save_to_history("assistant", full_text, source=req.source or "web", conversation_id=req.conversation_id, **history_user)
                if req.source != "telegram":
                    asyncio.create_task(broadcast_web_chat("assistant", full_text))
                yield "data: [DONE]\n\n"
            return StreamingResponse(
                _stream_doc_gen(),
                media_type="text/event-stream",
                headers={"X-Model": router.current_model_id, "X-Doc-Gen": "true"},
            )
        else:
            result = await router.generate_document(
                req.message, system_prompt,
                max_tokens=req.max_tokens or 8192,
                temperature=req.temperature or 0.3,
            )
            if footnote:
                result += footnote
            session.update(graph_md, result, req.conversation_id)
            save_to_history("assistant", str(result), source=req.source or "web", conversation_id=req.conversation_id, **history_user)
            if req.source != "telegram":
                asyncio.create_task(broadcast_web_chat("assistant", str(result)))
            return JSONResponse(content={"response": str(result)})

    if req.stream:
        recent_context = build_recent_chat_context(user_email=effective_email, conversation_id=req.conversation_id)
        stream_context = context
        if recent_context:
            stream_context = f"[RECENT CONVERSATION]\n{recent_context}\n\n{context}".strip()
        return StreamingResponse(
            _stream_chat(req, stream_context, req.image_data),
            media_type="text/event-stream",
            headers={"X-Model": router.current_model_id},
        )
    else:
        if req.image_data:
            recent_context = build_recent_chat_context(
                limit=6,
                include_image_missing_replies=False,
                user_email=effective_email,
                conversation_id=req.conversation_id,
            )
            full_context = f"[RECENT CONVERSATION]\n{recent_context}\n\n{context}".strip() if recent_context else context
        else:
            history_context = build_recent_chat_context(user_email=effective_email, conversation_id=req.conversation_id)
            full_context = f"{history_context}\n{context}" if context else history_context

        result = await router.generate(req.message, full_context, req.max_tokens, req.temperature, req.image_data)

        save_to_history("assistant", str(result), source=req.source or "web", conversation_id=req.conversation_id, **history_user)
        if req.source != "telegram":
            asyncio.create_task(broadcast_web_chat("assistant", str(result)))

        return JSONResponse(content={"response": str(result)})


@app.get("/history")
async def fetch_history(request: Request):
    """웹 화면에서 이전 대화를 불러올 수 있도록 히스토리를 반환합니다."""
    require_user(request)
    return get_history()

@app.get("/history/conversations")
async def fetch_history_conversations(request: Request):
    """저장된 히스토리를 대화 단위로 묶어 반환합니다."""
    require_user(request)
    return group_history_conversations()

@app.get("/history/conversations/{conversation_id:path}")
async def fetch_history_conversation(conversation_id: str, request: Request):
    """선택한 대화의 메시지를 반환합니다."""
    require_user(request)
    messages = get_conversation_messages(conversation_id)
    if not messages:
        raise HTTPException(status_code=404, detail="대화를 찾을 수 없습니다.")
    return {"id": conversation_id, "messages": messages}


@app.delete("/history/conversations/{conversation_id:path}")
async def delete_history_conversation(conversation_id: str, request: Request):
    """선택한 대화방의 메시지만 삭제합니다."""
    email = require_user(request)
    result = clear_conversation(conversation_id, request.query_params.get("started_at"))
    append_audit_event(
        "conversation_delete",
        user_email=email,
        conversation_id=conversation_id,
        started_at=request.query_params.get("started_at"),
        removed=result.get("removed", 0),
        kept=result.get("kept", 0),
    )
    return result


@app.delete("/history")
async def delete_history(request: Request, keep_last: int = 0):
    email = require_user(request)
    result = clear_history(keep_last)
    append_audit_event(
        "history_delete",
        user_email=email,
        keep_last=keep_last,
        removed=result.get("removed", 0),
        kept=result.get("kept", 0),
    )
    return result

@app.get("/history/search")
async def search_history(q: str, request: Request):
    """키워드로 채팅 히스토리를 검색합니다."""
    require_user(request)
    if not q or not q.strip():
        return {"results": [], "query": q}
    q_lower = q.strip().lower()
    history = get_history()
    matches = [item for item in history if q_lower in (item.get("content") or "").lower()]
    grouped: Dict[str, Dict] = {}
    for item in matches:
        cid = item.get("conversation_id") or "legacy"
        if cid not in grouped:
            grouped[cid] = {"conversation_id": cid, "title": conversation_title(item), "messages": []}
        grouped[cid]["messages"].append(item)
    return {"results": list(grouped.values())[-30:], "query": q}

async def _stream_chat(req: ChatRequest, context: str = "", image_data: str = None) -> AsyncIterator[str]:
    full_response = ""
    async for chunk in router.stream_generate(req.message, context, req.max_tokens, req.temperature, image_data):
        clean_chunk = chunk
        if hasattr(chunk, "text"):
            clean_chunk = chunk.text
        elif isinstance(chunk, str) and "text='" in chunk:
            try:
                clean_chunk = chunk.split("text='")[1].split("', token=")[0].replace('\\n', '\n').replace('\\\\n', '\n')
            except Exception:
                pass

        full_response += str(clean_chunk)
        yield f"data: {json.dumps({'chunk': clean_chunk, 'model': router.current_model_id}, ensure_ascii=False)}\n\n"
    history_user = get_history_user(req.user_email, req.user_nickname)
    save_to_history("assistant", full_response, source=req.source or "web", conversation_id=req.conversation_id, **history_user)
    if req.source != "telegram":
        asyncio.create_task(broadcast_web_chat("assistant", full_response))
    yield "data: [DONE]\n\n"


# ── Local Computer Agent ──────────────────────────────────────────────────────

# ── Agent Tool Registry / Governance ──────────────────────────────────────────

_FILE_CREATE_ACTIONS = set(DEFAULT_TOOL_REGISTRY.file_create_actions)
TOOL_GOVERNANCE: Dict[str, ToolPolicy] = dict(DEFAULT_TOOL_REGISTRY.governance)
_TOOL_GOVERNANCE_DEFAULT: ToolPolicy = DEFAULT_TOOL_REGISTRY.default_policy
ADMIN_ONLY_TOOLS: frozenset[str] = DEFAULT_TOOL_REGISTRY.admin_only_tools
_LOCAL_WRITE_BLOCKED_PREFIXES = DEFAULT_TOOL_REGISTRY.local_write_blocked_prefixes
_RISK_LEVEL_MAP = DEFAULT_TOOL_REGISTRY.risk_level_map


def _agent_policy(action_name: str, args: dict) -> ToolPolicy:
    return DEFAULT_TOOL_REGISTRY.policy_for(action_name, args)


def _agent_risk(action_name: str, args: dict) -> str:
    return DEFAULT_TOOL_REGISTRY.risk_level(action_name, args)


def get_tool_permission(name: str, args: Optional[dict] = None) -> ToolPermission:
    return DEFAULT_TOOL_REGISTRY.permission(name, args or {})


def list_tool_permissions() -> list:
    return DEFAULT_TOOL_REGISTRY.permissions()


# Tools that require admin role -- computer control + shell execution
def _check_tool_role(tool_name: str, current_user: str) -> None:
    if tool_name not in ADMIN_ONLY_TOOLS:
        return
    users = load_users()
    if get_user_role(current_user, users) != "admin":
        raise HTTPException(
            status_code=403,
            detail=f"'{tool_name}' 툴은 관리자 전용입니다.",
        )


def _collect_created_files(transcript: list) -> list:
    files = []
    for step in transcript:
        if step.get("action") in _FILE_CREATE_ACTIONS:
            result = step.get("result", {})
            if isinstance(result.get("created_files"), list):
                for rel_path in result["created_files"]:
                    files.append({
                        "path": rel_path,
                        "filename": Path(rel_path).name,
                        "bytes": 0,
                        "action": step["action"],
                    })
                continue
            path = result.get("path")
            if path:
                files.append({
                    "path": path,
                    "filename": Path(path).name,
                    "bytes": result.get("bytes", 0),
                    "action": step["action"],
                })
    return files


# ── Agent Runtime wiring ──────────────────────────────────────────────────────
# The Discover→Plan→Implement→Verify state machine lives in
# latticeai.core.agent. server.py wires the ports (LLM, tools, governance,
# audit, prompts) into one AgentRuntime and keeps only the HTTP glue below.

def _build_agent_runtime() -> AgentRuntime:
    deps = AgentDeps(
        generate_as=router.generate_as,
        generate=router.generate,
        execute_tool=execute_tool,
        policy_for=_agent_policy,
        risk_level=lambda policy: _RISK_LEVEL_MAP.get(policy["risk"], "medium"),
        check_role=_check_tool_role,
        tool_governance=TOOL_GOVERNANCE,
        file_create_actions=frozenset(_FILE_CREATE_ACTIONS),
        recent_chat_context=build_recent_chat_context,
        clear_history=clear_history,
        knowledge_save=knowledge_save,
        audit=append_audit_event,
        planner_prompt=PLANNER_PROMPT,
        executor_prompt=EXECUTOR_PROMPT,
        critic_prompt=CRITIC_PROMPT,
        memory_updater_prompt=MEMORY_UPDATER_PROMPT,
        agent_root=AGENT_ROOT,
    )
    return AgentRuntime(deps)


_AGENT_RUNTIME = _build_agent_runtime()


# ── Eval harness ──────────────────────────────────────────────────────────────

@app.post("/agent/eval")
async def agent_eval(req: AgentEvalRequest, request: Request):
    """Run a skill's eval cases from schema.json and return pass/fail per case."""
    require_user(request)
    skill_dir = BASE_DIR / "skills" / req.skill
    schema_path = skill_dir / "schema.json"
    if not schema_path.exists():
        raise HTTPException(404, detail=f"Skill '{req.skill}' not found or missing schema.json")

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    eval_cases = schema.get("evals", [])
    if req.case_id:
        eval_cases = [c for c in eval_cases if c.get("id") == req.case_id]
    if not eval_cases:
        return {"skill": req.skill, "total": 0, "passed": 0, "failed": 0, "results": [],
                "message": "No eval cases defined in schema.json"}

    action_name = schema.get("action", req.skill)
    results = []
    for case in eval_cases:
        case_id = case.get("id", "?")
        try:
            result   = execute_tool(action_name, case.get("input", {}))
            criteria = case.get("pass_criteria", "")
            if "success == true" in criteria:
                passed = result.get("success") is True
            elif "success == false" in criteria:
                passed = result.get("success") is False
            else:
                passed = True  # manual review required
            results.append({"id": case_id, "description": case.get("description", ""),
                            "passed": passed, "result": result, "pass_criteria": criteria})
        except Exception as exc:
            results.append({"id": case_id, "description": case.get("description", ""),
                            "passed": False, "error": str(exc),
                            "pass_criteria": case.get("pass_criteria", "")})

    n_passed = sum(1 for r in results if r.get("passed") is True)
    return {
        "skill": req.skill, "action": action_name,
        "total": len(results), "passed": n_passed, "failed": len(results) - n_passed,
        "results": results,
    }


@app.post("/agent")
async def agent(req: AgentRequest, request: Request):
    """Natural-language local agent.

    State machine:
        IDLE → PLANNING → WAITING_APPROVAL → EXECUTING → VERIFYING
                                       ↓                     ↓
                                     FAILED       DONE | EXECUTING(retry) | ROLLBACK
                                                                                  ↓
                                                                               FAILED
    """
    current_user = require_user(request)
    enforce_rate_limit(current_user, "agent")
    if not router.current_model_id:
        raise HTTPException(status_code=400, detail="No model loaded. Call /models/load first.")

    ensure_agent_root()
    lang = detect_language(req.message)
    lang_hint = _LANG_HINT[lang]
    max_steps = max(1, min(req.max_steps, 50))
    max_retry = 3

    ctx = AgentRunContext()
    ctx.executing_model = req.executing_model
    ctx.reviewing_model = req.reviewing_model

    # PLANNING phase
    ctx.state = AgentState.PLANNING
    ctx.state_history.append(ctx.state.value)
    await _AGENT_RUNTIME.plan(ctx, req, lang_hint, current_user, model_id=req.planning_model)

    # Human-in-the-loop: pause after planning, return plan to UI
    if req.human_in_loop:
        context_id = secrets.token_urlsafe(16)
        with _pending_agents_lock:
            _pending_agents[context_id] = (ctx, req, lang_hint, current_user)
        return {
            "status": "waiting_approval",
            "context_id": context_id,
            "plan": ctx.plan,
            "steps": ctx.transcript,
            "state_history": ctx.state_history,
            "planning_model": req.planning_model or router.current_model_id,
            "executing_model": req.executing_model or router.current_model_id,
            "reviewing_model": req.reviewing_model or router.current_model_id,
        }

    # Auto-approve and run to completion (default behaviour)
    _AGENT_RUNTIME.approve(ctx, current_user)
    return await _agent_finish(ctx, req, lang_hint, current_user, max_steps, max_retry)


async def _agent_finish(
    ctx: AgentRunContext, req: AgentRequest, lang_hint: str,
    current_user: str, max_steps: int, max_retry: int,
) -> dict:
    """HTTP glue: drive the runtime to a terminal state, persist, shape the response."""
    await _AGENT_RUNTIME.run_to_completion(ctx, req, lang_hint, current_user, max_steps, max_retry)
    asyncio.create_task(_AGENT_RUNTIME.memory_update(ctx, req, current_user))

    message = ctx.final_message or "작업을 완료했습니다."
    save_to_history("user", req.message, source=req.source or "web", conversation_id=req.conversation_id)
    save_to_history("assistant", message, source=req.source or "web", conversation_id=req.conversation_id)
    created_files = _collect_created_files(ctx.transcript)
    return {
        "status": "ok" if ctx.state == AgentState.DONE else "failed",
        "response": message,
        "workspace": str(AGENT_ROOT),
        "steps": ctx.transcript,
        "state_history": ctx.state_history,
        "final_state": ctx.state.value,
        "created_files": created_files,
    }


@app.post("/agent/resume")
async def agent_resume(req: AgentResumeRequest, request: Request):
    """Resume a paused agent after human approval of the plan."""
    current_user = require_user(request)

    with _pending_agents_lock:
        entry = _pending_agents.pop(req.context_id, None)
    if not entry:
        raise HTTPException(status_code=404, detail="Agent context not found or expired. Start a new request.")

    ctx, orig_req, lang_hint, _orig_user = entry

    if not req.approved:
        return {"status": "cancelled", "response": "사용자가 계획을 취소했습니다."}

    if req.modified_plan:
        ctx.plan = req.modified_plan
        ctx.transcript[-1].update(ctx.plan)  # keep transcript in sync

    # Apply model overrides from resume request (takes priority over original request)
    ctx.executing_model = req.executing_model or ctx.executing_model
    ctx.reviewing_model = req.reviewing_model or ctx.reviewing_model

    _AGENT_RUNTIME.approve(ctx, current_user)

    max_steps = max(1, min(orig_req.max_steps, 50))
    max_retry = 3
    return await _agent_finish(ctx, orig_req, lang_hint, current_user, max_steps, max_retry)


# ── Direct Tool API ───────────────────────────────────────────────────────────

def _tool_response(fn, *args):
    try:
        return {"status": "ok", "workspace": str(AGENT_ROOT), "result": fn(*args)}
    except ToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/tools/list_dir")
async def tools_list_dir(req: ToolPathRequest, request: Request):
    require_user(request)
    return _tool_response(list_dir, req.path)


@app.post("/tools/workspace_tree")
async def tools_workspace_tree(req: ToolWorkspaceTreeRequest, request: Request):
    require_user(request)
    return _tool_response(workspace_tree, req.path, req.max_depth)


@app.post("/tools/read_file")
async def tools_read_file(req: ToolReadFileRequest, request: Request):
    require_user(request)
    try:
        return {"status": "ok", "workspace": str(AGENT_ROOT),
                "result": read_file(req.path, offset=req.offset, limit=req.limit, line_numbers=req.line_numbers)}
    except ToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/tools/write_file")
async def tools_write_file(req: ToolWriteFileRequest, request: Request):
    require_user(request)
    return _tool_response(write_file, req.path, req.content)


@app.post("/tools/edit_file")
async def tools_edit_file(req: ToolEditFileRequest, request: Request):
    require_user(request)
    try:
        return {"status": "ok", "workspace": str(AGENT_ROOT),
                "result": edit_file(req.path, req.old_string, req.new_string, replace_all=req.replace_all)}
    except ToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/tools/search_files")
async def tools_search_files(req: ToolSearchFilesRequest, request: Request):
    require_user(request)
    return _tool_response(search_files, req.query, req.path, req.max_results)


@app.post("/tools/grep")
async def tools_grep(req: ToolGrepRequest, request: Request):
    require_user(request)
    try:
        return {"status": "ok", "workspace": str(AGENT_ROOT),
                "result": grep(
                    req.pattern,
                    path=req.path,
                    glob=req.glob,
                    max_results=req.max_results,
                    case_insensitive=req.case_insensitive,
                    context_lines=req.context_lines,
                )}
    except ToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/tools/todo_read")
async def tools_todo_read(request: Request):
    require_user(request)
    return _tool_response(todo_read)


@app.post("/tools/todo_write")
async def tools_todo_write(req: ToolTodoWriteRequest, request: Request):
    require_user(request)
    return _tool_response(todo_write, req.todos)


@app.post("/tools/clear_history")
async def tools_clear_history(req: ToolClearHistoryRequest, request: Request):
    current_user = require_user(request)
    result = clear_history(req.keep_last)
    append_audit_event(
        "history_delete",
        user_email=current_user,
        source="tools",
        keep_last=req.keep_last,
        removed=result.get("removed", 0),
        kept=result.get("kept", 0),
    )
    return result


@app.post("/tools/inspect_html")
async def tools_inspect_html(req: ToolPathRequest, request: Request):
    require_user(request)
    return _tool_response(inspect_html, req.path)


@app.post("/tools/preview_url")
async def tools_preview_url(req: ToolPathRequest, request: Request):
    require_user(request)
    return _tool_response(preview_url, req.path)


@app.post("/tools/create_docx")
async def tools_create_docx(req: ToolDocxRequest, request: Request):
    require_user(request)
    return _tool_response(create_docx, req.title, req.body, req.filename)


@app.post("/tools/create_xlsx")
async def tools_create_xlsx(req: ToolXlsxRequest, request: Request):
    require_user(request)
    return _tool_response(create_xlsx, req.rows, req.filename, req.sheet_name)


@app.post("/tools/create_pptx")
async def tools_create_pptx(req: ToolPptxRequest, request: Request):
    require_user(request)
    return _tool_response(create_pptx, req.title, req.slides, req.filename)


@app.post("/tools/create_pdf")
async def tools_create_pdf(req: ToolPdfRequest, request: Request):
    require_user(request)
    return _tool_response(create_pdf, req.title, req.body, req.filename)


@app.post("/tools/read_document")
async def tools_read_document(req: ToolPathRequest, request: Request):
    current_user = require_user(request)
    if Path(req.path).expanduser().is_absolute():
        _require_local_approval(token=req.approval_token, path=req.path, action="read", user_email=current_user)
    return _tool_response(read_document, req.path)


@app.get("/tools/pdf_pages")
async def tools_pdf_pages(path: str, request: Request, approval_token: Optional[str] = None):
    """Render PDF pages as base64 PNG images using pypdfium2 (Apache-2.0)."""
    current_user = require_user(request)
    _require_local_approval(token=approval_token, path=path, action="read", user_email=current_user)
    target = Path(path).expanduser().resolve()
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    import io
    import pypdfium2 as pdfium
    doc = None
    try:
        doc = pdfium.PdfDocument(str(target))
        total = len(doc)
        pages = []
        for i in range(min(total, 20)):  # 최대 20페이지
            page = doc[i]
            bitmap = page.render(scale=1.5)
            pil_image = bitmap.to_pil()
            buf = io.BytesIO()
            pil_image.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode()
            pages.append({"page": i + 1, "b64": b64})
        return {"total": total, "pages": pages}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF 렌더링 실패: {e}")
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception as e:
                logging.warning("pypdfium2 doc close failed: %s", e)


@app.get("/tools/download")
async def tools_download(path: str, request: Request):
    """Serve a generated file from agent workspace for download."""
    require_user(request)
    from urllib.parse import unquote
    rel = unquote(path).lstrip("/")
    target = (AGENT_ROOT / rel).resolve()
    if AGENT_ROOT not in target.parents and target != AGENT_ROOT:
        raise HTTPException(status_code=403, detail="경로가 작업 공간 밖입니다.")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="파일이 없습니다.")
    return FileResponse(
        path=target,
        filename=target.name,
        media_type="application/octet-stream",
    )


@app.post("/upload/document")
async def upload_document(request: Request, file: UploadFile = File(...)):
    current_user = require_user(request)
    enforce_rate_limit(current_user, "upload")
    """Upload a document and extract text (PDF, DOCX, XLSX, PPTX, TXT, MD, CSV)."""
    suffix = Path(file.filename or "upload").suffix.lower()
    allowed = {".pdf", ".docx", ".xlsx", ".pptx", ".txt", ".md", ".csv"}
    if suffix not in allowed:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 형식: {suffix}")
    contents = await file.read()
    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="파일이 너무 큽니다. 최대 10MB.")
    # MIME sniff — verify the bytes actually match the claimed extension (cheap header check)
    if not _bytes_match_extension(contents, suffix):
        raise HTTPException(status_code=400, detail=f"파일 내용이 확장자({suffix})와 일치하지 않습니다.")
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name
    try:
        result = read_document(tmp_path)
        sensitive = classify_sensitive_message(
            {
                "role": "document",
                "content": result.get("content") or result.get("preview") or "",
                "user_email": current_user,
                "timestamp": datetime.now().isoformat(),
            },
            -1,
        )
        try:
            if not (ENABLE_GRAPH and KNOWLEDGE_GRAPH):
                raise RuntimeError("graph disabled")
            graph_result = KNOWLEDGE_GRAPH.ingest_document(
                Path(tmp_path),
                original_filename=file.filename,
                mime_type=file.content_type,
                uploader=current_user,
                conversation_id=request.query_params.get("conversation_id"),
                extracted=result,
            )
            result["knowledge_graph"] = {
                "node_id": graph_result["node_id"],
                "sha256": graph_result["sha256"],
            }
        except Exception as graph_error:
            logging.warning("knowledge graph document ingest failed: %s", graph_error)
            result["knowledge_graph"] = {"error": str(graph_error)}
        append_audit_event(
            "document_upload",
            user_email=current_user,
            conversation_id=request.query_params.get("conversation_id"),
            filename=file.filename,
            mime_type=file.content_type,
            ext=suffix,
            bytes=len(contents),
            extracted_chars=result.get("chars"),
            graph_node=(result.get("knowledge_graph") or {}).get("node_id"),
            content_preview=sensitive.get("preview"),
            sensitivity=sensitive.get("sensitivity"),
            sensitive_labels=sensitive.get("labels") or [],
        )
    except ToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        try:
            Path(tmp_path).unlink()
        except OSError:
            pass
    result["original_filename"] = file.filename
    return result


_PERMISSION_ACTION_LABELS = {
    "list":  "폴더 목록 보기",
    "read":  "파일 읽기",
    "write": "파일 쓰기",
}

_LOCAL_APPROVAL_TTL_SECONDS = 5 * 60
_local_approval_lock = threading.Lock()
_local_approvals: Dict[str, Dict[str, object]] = {}

# Discord bot / webhook settings for permission notifications (optional)
DISCORD_PERMISSION_WEBHOOK_URL = CONFIG.discord_permission_webhook
DISCORD_BOT_TOKEN = CONFIG.discord_bot_token
DISCORD_PERMISSION_CHANNEL = CONFIG.discord_permission_channel

# Secret token that allows permission monitor script to call approve/deny endpoints
# without an admin user session (used by perm_monitor.py).
PERMISSION_MONITOR_SECRET = CONFIG.permission_monitor_secret

# Local queue file — written by server, read by perm_monitor.py
_PERM_QUEUE_FILE = DATA_DIR / "permission_queue.json"


def _perm_queue_write(token: str, record: Dict[str, object]) -> None:
    """Append a permission request to the local queue file for the monitor script."""
    try:
        queue: Dict = {}
        if _PERM_QUEUE_FILE.exists():
            try:
                queue = json.loads(_PERM_QUEUE_FILE.read_text(encoding="utf-8"))
            except Exception:
                queue = {}
        queue[token] = {**record, "notified": False}
        _PERM_QUEUE_FILE.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logging.warning("perm_queue_write failed: %s", exc)


def _perm_queue_remove(token: str) -> None:
    """Remove a token from the queue file after approval or denial."""
    try:
        if not _PERM_QUEUE_FILE.exists():
            return
        queue: Dict = json.loads(_PERM_QUEUE_FILE.read_text(encoding="utf-8"))
        queue.pop(token, None)
        _PERM_QUEUE_FILE.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logging.warning("perm_queue_remove failed: %s", exc)


def _normalize_local_path_for_approval(path: str) -> str:
    return str(Path(path).expanduser().resolve())


def _content_fingerprint(content: str = "") -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _notify_discord_permission_sync(token: str, path: str, action: str, user_email: str) -> None:
    """Fire-and-forget Discord bot/webhook notification for permission requests."""
    # Try Discord bot API first (sends to a specific channel), then fall back to webhook
    sent = False
    if DISCORD_BOT_TOKEN and DISCORD_PERMISSION_CHANNEL:
        action_label = _PERMISSION_ACTION_LABELS.get(action, action)
        expires_at_iso = time.strftime(
            "%Y-%m-%d %H:%M:%S UTC",
            time.gmtime(time.time() + _LOCAL_APPROVAL_TTL_SECONDS),
        )
        msg = (
            f"🔐 **파일 접근 권한 요청**\n"
            f"**경로:** `{path}`\n"
            f"**작업:** {action_label}\n"
            f"**요청자:** {user_email}\n"
            f"**토큰:** `{token}`\n"
            f"**만료:** {expires_at_iso}\n\n"
            f"승인하려면 `승인 {token[:8]}` / 거부하려면 `거부 {token[:8]}` 라고 답장하세요."
        )
        payload = json.dumps({"content": msg}, ensure_ascii=False).encode("utf-8")
        try:
            req = urllib.request.Request(
                f"https://discord.com/api/v10/channels/{DISCORD_PERMISSION_CHANNEL}/messages",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5):
                pass
            sent = True
        except Exception as exc:
            logging.warning("Discord bot permission notify failed: %s", exc)

    if not sent and DISCORD_PERMISSION_WEBHOOK_URL:
        action_label = _PERMISSION_ACTION_LABELS.get(action, action)
        expires_at_iso = time.strftime(
            "%Y-%m-%d %H:%M:%S UTC",
            time.gmtime(time.time() + _LOCAL_APPROVAL_TTL_SECONDS),
        )
        payload = json.dumps({
            "embeds": [
                {
                    "title": "🔐 파일 접근 권한 요청",
                    "color": 0xFF9900,
                    "fields": [
                        {"name": "경로", "value": f"`{path}`", "inline": False},
                        {"name": "작업", "value": action_label, "inline": True},
                        {"name": "요청자", "value": user_email, "inline": True},
                        {"name": "토큰", "value": f"`{token}`", "inline": False},
                        {"name": "만료", "value": expires_at_iso, "inline": True},
                    ],
                    "footer": {
                        "text": (
                            "승인: POST /permissions/approve/{token}  |  "
                            "거부: POST /permissions/deny/{token}  |  "
                            "목록: GET /permissions/pending"
                        )
                    },
                }
            ]
        }, ensure_ascii=False).encode("utf-8")
        try:
            req = urllib.request.Request(
                DISCORD_PERMISSION_WEBHOOK_URL,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5):
                pass
        except Exception as exc:  # pylint: disable=broad-except
            logging.warning("Discord permission webhook failed: %s", exc)


def _local_permission_response(path: str, action: str, user_email: str, content: str = "") -> dict:
    normalized = _normalize_local_path_for_approval(path)
    token = secrets.token_urlsafe(24)
    record: Dict[str, object] = {
        "path": normalized,
        "action": action,
        "user_email": user_email,
        "expires_at": time.time() + _LOCAL_APPROVAL_TTL_SECONDS,
        # approved=False until user explicitly confirms (Discord, web UI, etc.)
        "approved": False,
    }
    if action == "write":
        record["content_hash"] = _content_fingerprint(content)
    with _local_approval_lock:
        _local_approvals[token] = record
    # Write to local queue file — perm_monitor.py or Claude Code reads this
    # and relays the notification to Discord via the Discord MCP plugin.
    _perm_queue_write(token, record)
    action_label = _PERMISSION_ACTION_LABELS.get(action, action)
    return {
        "permission_required": True,
        "path": path,
        "action": action,
        "action_label": action_label,
        "approval_token": token,
        "expires_in": _LOCAL_APPROVAL_TTL_SECONDS,
        "message": f"AI가 '{path}' 에 대한 {action_label} 권한을 요청합니다.",
        "check_status_url": f"/permissions/status/{token}",
    }


def _require_local_user(request: Request) -> str:
    email = get_current_user(request)
    if not email:
        raise HTTPException(status_code=401, detail="로컬 파일 접근은 로그인 세션이 필요합니다.")
    return email


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
    normalized = _normalize_local_path_for_approval(path)
    now = time.time()
    with _local_approval_lock:
        expired = [key for key, value in _local_approvals.items() if float(value.get("expires_at", 0)) < now]
        for key in expired:
            _local_approvals.pop(key, None)
        record = _local_approvals.get(token)
    if not record:
        raise HTTPException(status_code=403, detail="파일 접근 승인이 만료되었거나 유효하지 않습니다.")
    if not record.get("approved"):
        raise HTTPException(status_code=403, detail="파일 접근이 아직 승인되지 않았습니다. Discord 또는 UI에서 승인해주세요.")
    if record.get("user_email") != user_email:
        raise HTTPException(status_code=403, detail="다른 사용자의 파일 접근 승인은 사용할 수 없습니다.")
    if record.get("path") != normalized or record.get("action") != action:
        raise HTTPException(status_code=403, detail="파일 접근 승인 범위가 일치하지 않습니다.")
    if action == "write" and record.get("content_hash") != _content_fingerprint(content):
        raise HTTPException(status_code=403, detail="승인된 파일 내용과 요청 내용이 다릅니다.")


# ── Permission management endpoints ──────────────────────────────────────────

@app.get("/permissions/pending")
async def permissions_pending(request: Request):
    """List all pending (not yet approved) permission requests. Admin only."""
    require_admin(request)
    now = time.time()
    with _local_approval_lock:
        result = {}
        for tok, rec in list(_local_approvals.items()):
            expires_at = float(rec.get("expires_at", 0))
            if expires_at < now:
                continue
            result[tok] = {
                "path": rec.get("path"),
                "action": rec.get("action"),
                "action_label": _PERMISSION_ACTION_LABELS.get(str(rec.get("action", "")), str(rec.get("action", ""))),
                "user_email": rec.get("user_email"),
                "approved": bool(rec.get("approved")),
                "expires_in": round(expires_at - now),
            }
    return {"pending": result, "count": len(result)}


def _check_permission_auth(request: Request, token: Optional[str] = None) -> None:
    """Allow access if requester is admin OR presents the LATTICEAI_PERMISSION_SECRET.
    Used by approve/deny endpoints so the permission monitor script can call them."""
    # Check secret header first (monitor script path)
    if PERMISSION_MONITOR_SECRET:
        auth_header = request.headers.get("Authorization", "")
        if auth_header == f"Bearer {PERMISSION_MONITOR_SECRET}":
            return  # Authorized via secret
    if token:
        current_user = get_current_user(request)
        with _local_approval_lock:
            record = _local_approvals.get(token)
        if current_user and record and record.get("user_email") == current_user:
            return
    # Fall back to admin session
    require_admin(request)


@app.post("/permissions/approve/{token}")
async def permissions_approve(token: str, request: Request):
    """Approve a pending permission request. Admin or permission-monitor secret.
    Called by Discord (via Claude Code) or web UI after user confirmation."""
    _check_permission_auth(request, token)
    with _local_approval_lock:
        record = _local_approvals.get(token)
        if not record:
            raise HTTPException(status_code=404, detail="토큰이 없거나 만료되었습니다.")
        if float(record.get("expires_at", 0)) < time.time():
            _local_approvals.pop(token, None)
            raise HTTPException(status_code=410, detail="토큰이 만료되었습니다.")
        record["approved"] = True
    _perm_queue_remove(token)
    logging.info(
        "Permission approved: token=%s path=%s action=%s user=%s",
        token, record.get("path"), record.get("action"), record.get("user_email"),
    )
    return {
        "ok": True,
        "token": token,
        "path": record.get("path"),
        "action": record.get("action"),
        "user_email": record.get("user_email"),
    }


@app.post("/permissions/deny/{token}")
async def permissions_deny(token: str, request: Request):
    """Deny/revoke a pending permission request. Admin or permission-monitor secret."""
    _check_permission_auth(request, token)
    with _local_approval_lock:
        record = _local_approvals.pop(token, None)
    _perm_queue_remove(token)
    if not record:
        raise HTTPException(status_code=404, detail="토큰이 없거나 이미 처리되었습니다.")
    logging.info(
        "Permission denied: token=%s path=%s action=%s user=%s",
        token, record.get("path"), record.get("action"), record.get("user_email"),
    )
    return {
        "ok": True,
        "denied": True,
        "token": token,
        "path": record.get("path"),
        "action": record.get("action"),
    }


@app.get("/permissions/status/{token}")
async def permissions_status(token: str, request: Request):
    """Check approval status of a token. Used by AI agents to poll for approval."""
    require_user(request)
    now = time.time()
    with _local_approval_lock:
        record = _local_approvals.get(token)
    if not record:
        return {"status": "denied_or_expired", "token": token}
    if float(record.get("expires_at", 0)) < now:
        return {"status": "expired", "token": token}
    if record.get("approved"):
        return {"status": "approved", "token": token}
    return {
        "status": "pending",
        "token": token,
        "expires_in": round(float(record.get("expires_at", 0)) - now),
    }


@app.post("/local/list")
async def local_list_endpoint(req: LocalAccessRequest, request: Request):
    current_user = _require_local_user(request)
    if not req.approved:
        return _local_permission_response(req.path, "list", current_user)
    _require_local_approval(token=req.approval_token, path=req.path, action="list", user_email=current_user)
    return _tool_response(local_list, req.path)


@app.get("/local/list")
async def local_list_get_endpoint(path: str, request: Request):
    current_user = _require_local_user(request)
    return _local_permission_response(path, "list", current_user)


@app.post("/local/read")
async def local_read_endpoint(req: LocalAccessRequest, request: Request):
    current_user = _require_local_user(request)
    if not req.approved:
        return _local_permission_response(req.path, "read", current_user)
    _require_local_approval(token=req.approval_token, path=req.path, action="read", user_email=current_user)
    return _tool_response(local_read, req.path)


@app.get("/local/serve")
async def local_serve_file(path: str, request: Request, approval_token: Optional[str] = None):
    """Serve a local file (images etc.) directly for browser preview."""
    current_user = _require_local_user(request)
    _require_local_approval(token=approval_token, path=path, action="read", user_email=current_user)
    target = Path(path).expanduser().resolve()
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(target))


@app.post("/local/write")
async def local_write_endpoint(req: LocalWriteRequest, request: Request):
    current_user = _require_local_user(request)
    if not req.approved:
        return _local_permission_response(req.path, "write", current_user, req.content)
    _require_local_approval(
        token=req.approval_token,
        path=req.path,
        action="write",
        user_email=current_user,
        content=req.content,
    )
    return _tool_response(local_write, req.path, req.content)


app.include_router(create_knowledge_graph_router(
    get_graph=lambda: KNOWLEDGE_GRAPH,
    require_graph=_require_graph,
    require_user=require_user,
    static_dir=STATIC_DIR,
))

app.include_router(create_local_knowledge_router(
    get_graph=lambda: KNOWLEDGE_GRAPH,
    require_graph=_require_graph,
    require_user=require_user,
    require_local_user=_require_local_user,
    local_permission_response=_local_permission_response,
    require_local_approval=_require_local_approval,
    watcher=LOCAL_KG_WATCHER,
))


@app.get("/tools/chrome_status")
async def tools_chrome_status(request: Request):
    require_user(request)
    return _tool_response(desktop_bridge_status)


@app.get("/tools/computer_use_status")
async def tools_computer_use_status(request: Request):
    require_user(request)
    return _tool_response(computer_status)


# ── 내 컴퓨터 API ──────────────────────────────────────────────────────────

CU_SYSTEM_PROMPT = """You are Lattice AI desktop-control agent. You control the Mac desktop using tools.
Prefer non-visual direct actions when possible. Use screenshots only when you must inspect visible UI state or choose screen coordinates.

Available actions:
- computer_screenshot: {"action":"computer_screenshot","args":{}} — capture screen, returns screenshot_b64
- computer_open_app: {"action":"computer_open_app","args":{"app":"Google Chrome"}} — open or focus a Mac app
- computer_open_url: {"action":"computer_open_url","args":{"url":"https://example.com","app":"Google Chrome"}} — open URL in app
- computer_click: {"action":"computer_click","args":{"x":500,"y":300,"button":"left","double":false}}
- computer_type: {"action":"computer_type","args":{"text":"hello world","interval":0.04}}
- computer_key: {"action":"computer_key","args":{"key":"return"}} — keys: return, escape, tab, space, command+c, etc.
- computer_scroll: {"action":"computer_scroll","args":{"x":500,"y":300,"direction":"down","clicks":3}}
- computer_move: {"action":"computer_move","args":{"x":500,"y":300}}
- computer_drag: {"action":"computer_drag","args":{"x1":100,"y1":100,"x2":500,"y2":500}}
- final: {"action":"final","message":"Korean summary of what was accomplished"}

Rules:
- Respond with exactly ONE JSON object. No markdown, no extra text.
- Do not take screenshots for simple app launch, URL opening, keyboard shortcuts, or non-visual tasks.
- Take a screenshot before coordinate-based clicks/drags or when the task explicitly asks you to inspect the screen.
- After coordinate-based clicking or typing into an unknown focused field, take a screenshot only if verification is necessary.
- Use coordinates relative to the screen (0,0 is top-left).
- If a UI element is not visible, scroll or search for it first.
- macOS Accessibility permission required for mouse/keyboard control.
"""

class CuAgentRequest(BaseModel):
    task: str
    conversation_id: Optional[str] = None
    max_steps: int = 15
    temperature: float = 0.1

class CuClickRequest(BaseModel):
    x: int
    y: int
    button: str = "left"
    double: bool = False

class CuOpenAppRequest(BaseModel):
    app: str = "Google Chrome"

class CuOpenUrlRequest(BaseModel):
    url: str
    app: str = "Google Chrome"

class CuTypeRequest(BaseModel):
    text: str
    interval: float = 0.04

class CuKeyRequest(BaseModel):
    key: str

class CuScrollRequest(BaseModel):
    x: int
    y: int
    direction: str = "down"
    clicks: int = 3

class CuMoveRequest(BaseModel):
    x: int
    y: int

class CuDragRequest(BaseModel):
    x1: int
    y1: int
    x2: int
    y2: int


@app.get("/cu/status")
async def cu_status(request: Request):
    require_user(request)
    try:
        return computer_status()
    except ToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/cu/screenshot")
async def cu_screenshot(request: Request):
    require_user(request)
    try:
        return computer_screenshot()
    except ToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/cu/open_app")
async def cu_open_app(req: CuOpenAppRequest, request: Request):
    require_user(request)
    return _tool_response(computer_open_app, req.app)


@app.post("/cu/open_url")
async def cu_open_url(req: CuOpenUrlRequest, request: Request):
    require_user(request)
    return _tool_response(computer_open_url, req.url, req.app)


@app.post("/cu/click")
async def cu_click(req: CuClickRequest, request: Request):
    require_user(request)
    return _tool_response(computer_click, req.x, req.y, req.button, req.double)


@app.post("/cu/type")
async def cu_type(req: CuTypeRequest, request: Request):
    require_user(request)
    return _tool_response(computer_type, req.text, req.interval)


@app.post("/cu/key")
async def cu_key(req: CuKeyRequest, request: Request):
    require_user(request)
    return _tool_response(computer_key, req.key)


@app.post("/cu/scroll")
async def cu_scroll(req: CuScrollRequest, request: Request):
    require_user(request)
    return _tool_response(computer_scroll, req.x, req.y, req.direction, req.clicks)


@app.post("/cu/move")
async def cu_move(req: CuMoveRequest, request: Request):
    require_user(request)
    return _tool_response(computer_move, req.x, req.y)


@app.post("/cu/drag")
async def cu_drag(req: CuDragRequest, request: Request):
    require_user(request)
    return _tool_response(computer_drag, req.x1, req.y1, req.x2, req.y2)


@app.post("/cu/agent")
async def cu_agent(req: CuAgentRequest, request: Request):
    """SSE streaming desktop-control agent loop."""
    require_user(request)
    async def _stream():
        task_lower = (req.task or "").lower()
        url_match = re.search(r"(https?://[^\s]+|localhost:\d+[^\s]*|127\.0\.0\.1:\d+[^\s]*)", req.task or "")

        def _send(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        if ("chrome" in task_lower or "크롬" in task_lower) and any(word in task_lower for word in ["open", "열", "켜", "실행", "띄"]):
            yield _send("start", {"task": req.task, "max_steps": 1})
            try:
                if url_match:
                    url = url_match.group(1)
                    yield _send("action", {"step": 1, "action": "computer_open_url", "args": {"url": url, "app": "Google Chrome"}})
                    result = computer_open_url(url, "Google Chrome")
                    yield _send("result", {"step": 1, "action": "computer_open_url", "result": result})
                    message = f"Google Chrome에서 {url}을 열었습니다."
                    action_name = "computer_open_url"
                else:
                    yield _send("action", {"step": 1, "action": "computer_open_app", "args": {"app": "Google Chrome"}})
                    result = computer_open_app("Google Chrome")
                    yield _send("result", {"step": 1, "action": "computer_open_app", "result": result})
                    message = "Google Chrome을 열었습니다."
                    action_name = "computer_open_app"
                save_to_history("user", req.task, source="web", conversation_id=req.conversation_id)
                save_to_history("assistant", message, source="web", conversation_id=req.conversation_id)
                yield _send("final", {"message": message, "steps": [{"step": 1, "action": action_name, "result": result}]})
            except ToolError as exc:
                yield _send("tool_error", {"step": 1, "action": "computer_open_app", "error": str(exc)})
            return

        if not router.current_model_id:
            yield _send("error", {"error": "No model loaded."})
            return

        transcript = []
        last_screenshot_b64: Optional[str] = None
        max_steps = max(1, min(req.max_steps, 20))

        yield _send("start", {"task": req.task, "max_steps": max_steps})

        for step in range(max_steps):
            context = (
                f"{CU_SYSTEM_PROMPT}\n\n"
                f"Task: {req.task}\n\n"
                f"Steps completed so far:\n{json.dumps(transcript, ensure_ascii=False, indent=2)}"
            )
            raw = await router.generate(
                message="Choose the next computer use action.",
                context=context,
                image_data=last_screenshot_b64,
                max_tokens=1024,
                temperature=req.temperature,
            )

            try:
                action = _extract_agent_action(str(raw))
            except ValueError as exc:
                yield _send("error", {"step": step + 1, "error": str(exc), "raw": str(raw)})
                break

            name = action.get("action")
            args = action.get("args") or {}

            if name == "final":
                message = action.get("message", "작업을 완료했습니다.")
                save_to_history("user", req.task, source="web", conversation_id=req.conversation_id)
                save_to_history("assistant", message, source="web", conversation_id=req.conversation_id)
                yield _send("final", {"message": message, "steps": transcript})
                return

            yield _send("action", {"step": step + 1, "action": name, "args": args})

            try:
                result = execute_tool(name, args)
                # store screenshot for next VLM call
                if name == "computer_screenshot" and "screenshot_b64" in result:
                    last_screenshot_b64 = result["screenshot_b64"]
                    # strip b64 from transcript to keep it small
                    result_summary = {k: v for k, v in result.items() if k != "screenshot_b64"}
                    result_summary["screenshot_captured"] = True
                    transcript.append({"step": step + 1, "action": name, "args": args, "result": result_summary})
                    yield _send("screenshot", {"step": step + 1, "screenshot_b64": last_screenshot_b64,
                                               "width": result.get("screen_width"), "height": result.get("screen_height")})
                else:
                    last_screenshot_b64 = None
                    transcript.append({"step": step + 1, "action": name, "args": args, "result": result})
                    yield _send("result", {"step": step + 1, "action": name, "result": result})
            except (ToolError, KeyError, TypeError) as exc:
                error_str = str(exc)
                transcript.append({"step": step + 1, "action": name, "args": args, "error": error_str})
                yield _send("tool_error", {"step": step + 1, "action": name, "error": error_str})

        yield _send("done", {"steps": len(transcript), "transcript": transcript})

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/tools/knowledge_save")
async def tools_knowledge_save(req: ToolKnowledgeSaveRequest, request: Request):
    require_user(request)
    return _tool_response(knowledge_save, req.content, req.folder, req.title)


@app.post("/tools/knowledge_search")
async def tools_knowledge_search(req: ToolKnowledgeSearchRequest, request: Request):
    require_user(request)
    return _tool_response(knowledge_search, req.query, req.max_results)


@app.get("/tools/knowledge_tree")
async def tools_knowledge_tree(request: Request):
    require_user(request)
    return _tool_response(knowledge_tree)


@app.post("/tools/obsidian_save")
async def tools_obsidian_save(req: ToolKnowledgeSaveRequest, request: Request):
    require_user(request)
    return _tool_response(obsidian_save, req.content, req.folder, req.title)


@app.post("/tools/obsidian_search")
async def tools_obsidian_search(req: ToolKnowledgeSearchRequest, request: Request):
    require_user(request)
    return _tool_response(obsidian_search, req.query, req.max_results)


@app.get("/tools/obsidian_tree")
async def tools_obsidian_tree(request: Request):
    require_user(request)
    return _tool_response(obsidian_tree)


@app.get("/obsidian/status")
async def obsidian_status(request: Request):
    require_user(request)
    return {
        "status": "ok",
        "vault_root": str(BRAIN_DIR),
        "folders": [path.name for path in BRAIN_DIR.iterdir() if path.is_dir()] if BRAIN_DIR.exists() else [],
        "ocr_engine": shutil.which("tesseract") or None,
    }


@app.get("/tools/git_status")
async def tools_git_status(request: Request):
    require_user(request)
    return _tool_response(git_status)


@app.post("/tools/git_diff")
async def tools_git_diff(req: ToolGitDiffRequest, request: Request):
    require_user(request)
    return _tool_response(git_diff, req.path, req.cwd)


@app.post("/tools/git_log")
async def tools_git_log(req: ToolGitLogRequest, request: Request):
    require_user(request)
    return _tool_response(git_log, req.max_count, req.cwd)


@app.post("/tools/git_show")
async def tools_git_show(req: ToolGitShowRequest, request: Request):
    require_user(request)
    return _tool_response(git_show, req.revision, req.cwd)


@app.post("/tools/run_command")
async def tools_run_command(req: ToolRunCommandRequest, request: Request):
    require_admin(request)
    return _tool_response(run_command, req.command, req.cwd)


@app.get("/tools/network_status")
async def tools_network_status(request: Request):
    require_user(request)
    return _tool_response(network_status)


@app.post("/tools/build_project")
async def tools_build_project(req: ToolScriptRequest, request: Request):
    require_admin(request)
    return _tool_response(build_project, req.cwd, req.script)


@app.post("/tools/deploy_project")
async def tools_deploy_project(req: ToolScriptRequest, request: Request):
    require_admin(request)
    return _tool_response(deploy_project, req.cwd, req.script)


@app.get("/tools/permissions")
async def tools_permissions(request: Request):
    """Compact tool permission view (tool / risk / requires_approval / network).

    A simpler authorization-layer summary derived from TOOL_GOVERNANCE.
    Use /mcp/tools for the full 7-dimensional governance object.
    """
    require_user(request)
    return {"status": "ok", "permissions": list_tool_permissions()}


@app.get("/mcp/tools")
async def mcp_tools():
    installed = load_mcp_installs().get("installed", {})
    registry = await _get_combined_registry()
    tools = []
    for name, description in MCP_TOOL_DESCRIPTIONS.items():
        policy = TOOL_GOVERNANCE.get(name, _TOOL_GOVERNANCE_DEFAULT)
        tools.append({
            "name": name,
            "description": description,
            "permission": get_tool_permission(name),
            "governance": {
                "risk":         policy["risk"],
                "destructive":  policy["destructive"],
                "shell":        policy["shell"],
                "network":      policy["network"],
                "auto_approve": policy["auto_approve"],
                "sandbox":      policy["sandbox"],
                "rollback":     policy["rollback"],
            },
        })
    return {
        "status": "ok",
        "workspace": str(AGENT_ROOT),
        "installed_mcps": [mcp_public_item(item, installed) for item in registry],
        "tools": tools,
    }


@app.post("/mcp/recommend")
async def mcp_recommend(req: McpRecommendRequest, request: Request):
    require_user(request)
    return {"recommendations": await recommend_mcps(req.query, req.limit)}


@app.post("/mcp/install")
async def mcp_install(req: McpInstallRequest, request: Request):
    admin_email, _ = require_admin(request)
    append_audit_event("mcp_install", user_email=admin_email, mcp_id=req.mcp_id)
    return await install_mcp(req.mcp_id)


@app.get("/mcp/installed")
async def mcp_installed(request: Request):
    require_user(request)
    installed = load_mcp_installs().get("installed", {})
    registry = await _get_combined_registry()
    return {"installed": [mcp_public_item(item, installed) for item in registry]}


@app.get("/mcp/connectors/{mcp_id}")
async def mcp_connector(mcp_id: str, request: Request):
    require_user(request)
    registry = await _get_combined_registry()
    item = next((e for e in registry if e["id"] == mcp_id), None)
    if not item or item.get("install_mode") != "connector":
        raise HTTPException(status_code=404, detail="커넥터를 찾을 수 없습니다.")
    installed = load_mcp_installs().get("installed", {})
    public = mcp_public_item(item, installed)
    public["instructions"] = [
        "Codex 또는 ChatGPT 앱의 Connectors 설정을 엽니다.",
        f"{item['name']} 항목을 선택하고 계정을 인증합니다.",
        "인증 후 Lattice AI에서 이 MCP를 다시 활성화하면 작업에 사용할 수 있습니다.",
    ]
    return public


@app.post("/mcp/registry/refresh")
async def mcp_registry_refresh(request: Request):
    require_user(request)
    mcp_registry._REMOTE_REGISTRY_FETCHED_AT = None
    registry = await _get_combined_registry()
    return {"status": "ok", "total": len(registry), "remote": len(mcp_registry._REMOTE_REGISTRY_CACHE)}


@app.get("/mcp/claude-code-servers")
async def mcp_claude_code_servers(request: Request):
    """Read ~/.claude/settings.json mcpServers and return them as Lattice MCP items."""
    require_user(request)
    settings_path = Path.home() / ".claude" / "settings.json"
    if not settings_path.exists():
        return {"servers": []}
    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            settings = json.load(f)
        mcp_servers = settings.get("mcpServers", {})
        servers = []
        for name, cfg in mcp_servers.items():
            cmd = cfg.get("command", "")
            args = cfg.get("args", [])
            package = " ".join([cmd] + args) if args else cmd
            env = cfg.get("env", {})
            env_vars = [{"name": k, "value": v} for k, v in env.items()]
            servers.append({
                "id": f"claude-code:{name}",
                "name": name,
                "description": f"Claude Code MCP: {package}",
                "package": package,
                "icon": "🤖",
                "category": "Claude Code",
                "source": "claude-code",
                "installed": True,
                "env_vars": env_vars,
            })
        return {"servers": servers}
    except Exception as e:
        logging.warning("mcp_claude_code_servers failed: %s", e)
        return {"servers": []}


_CUSTOM_MCP_FILE = DATA_DIR / "custom_mcps.json"

def _load_custom_mcps() -> List[Dict]:
    if not _CUSTOM_MCP_FILE.exists():
        return []
    try:
        with open(_CUSTOM_MCP_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def _save_custom_mcps(items: List[Dict]):
    with open(_CUSTOM_MCP_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


@app.get("/mcp/custom")
async def mcp_custom_list(request: Request):
    """Return user-added custom MCP entries."""
    require_user(request)
    return {"custom": _load_custom_mcps()}


@app.post("/mcp/custom")
async def mcp_custom_add(req: McpCustomRequest, request: Request):
    """Save a custom MCP entry (admin-only)."""
    admin_email, _ = require_admin(request)
    append_audit_event("mcp_custom_add", user_email=admin_email, name=req.name, package=req.package)
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="name은 필수입니다.")
    if not req.package.strip():
        raise HTTPException(status_code=400, detail="package는 필수입니다.")
    items = _load_custom_mcps()
    entry = {
        "id": f"custom:{req.name.strip().lower().replace(' ', '-')}",
        "name": req.name.strip(),
        "package": req.package.strip(),
        "description": req.description.strip(),
        "category": req.category or "custom",
        "icon": req.icon or "🔌",
        "env_vars": req.env_vars or [],
        "install_mode": "npm",
        "source": "custom",
        "installed": False,
        "added_at": datetime.now().isoformat(),
    }
    # overwrite if same id
    items = [e for e in items if e["id"] != entry["id"]]
    items.append(entry)
    _save_custom_mcps(items)
    return {"status": "ok", "entry": entry}


@app.delete("/mcp/custom/{mcp_id:path}")
async def mcp_custom_delete(mcp_id: str, request: Request):
    """Remove a custom MCP entry."""
    require_admin(request)
    items = _load_custom_mcps()
    before = len(items)
    items = [e for e in items if e["id"] != mcp_id]
    if len(items) == before:
        raise HTTPException(status_code=404, detail="항목을 찾을 수 없습니다.")
    _save_custom_mcps(items)
    return {"status": "ok"}


# ── Skills & Plugin Directory endpoints ───────────────────────────────────────

@app.get("/skills/marketplace")
async def skills_marketplace(request: Request, category: Optional[str] = None, author: Optional[str] = None):
    """Skills 마켓플레이스 (Anthropic Apache-2.0 + 검증된 서드파티 MIT/Apache-2.0)"""
    require_user(request)
    skills = await _fetch_skills_marketplace()
    installed_names = {d.name for d in SKILLS_DIR.iterdir() if d.is_dir()} if SKILLS_DIR.exists() else set()
    filtered = skills
    if category:
        filtered = [s for s in filtered if s.get("category", "").lower() == category.lower()]
    if author:
        filtered = [s for s in filtered if s.get("author", "").lower() == author.lower()]
    return {
        "skills": [{**s, "installed": s["skill"] in installed_names} for s in filtered],
        "total": len(filtered),
        "authors": sorted({s["author"] for s in skills}),
        "categories": sorted({s["category"] for s in skills}),
    }


@app.post("/skills/install")
async def skills_install(req: SkillInstallRequest, request: Request):
    """skill을 로컬 skills 디렉터리에 설치 (Apache-2.0 / MIT, 관리자 전용)"""
    admin_email, _ = require_admin(request)
    append_audit_event("skill_install", user_email=admin_email, plugin=req.plugin, skill=req.skill)
    return await install_skill(req.plugin, req.skill)


@app.get("/skills/list")
async def skills_list(request: Request):
    """로컬에 설치된 skills 목록"""
    require_user(request)
    if not SKILLS_DIR.exists():
        return {"skills": []}
    skills = []
    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        lines = skill_md.read_text(encoding="utf-8").splitlines()
        desc = next((l.split(":", 1)[1].strip() for l in lines if l.startswith("description:")), "")
        comment = lines[0] if lines else ""
        if "anthropics/claude-plugins-official" in comment:
            source = "anthropic"
        elif "Source:" in comment:
            source = "third-party"
        else:
            source = "local"
        skills.append({"name": skill_dir.name, "description": desc, "source": source})
    return {"skills": skills, "total": len(skills)}


@app.post("/skills/marketplace/refresh")
async def skills_marketplace_refresh(request: Request):
    """Skills 마켓플레이스 캐시 강제 갱신"""
    require_user(request)
    mcp_registry._SKILLS_MARKETPLACE_FETCHED_AT = None
    skills = await _fetch_skills_marketplace()
    by_author = {}
    for s in skills:
        by_author[s["author"]] = by_author.get(s["author"], 0) + 1
    return {"status": "ok", "total": len(skills), "by_author": by_author}


@app.get("/plugins/directory")
async def plugins_directory(
    request: Request,
    category: Optional[str] = None,
    license: Optional[str] = None,
    q: Optional[str] = None,
):
    """오픈소스 플러그인 디렉터리 (Apache-2.0 / MIT / MIT-0, 런타임 fetch)"""
    require_user(request)
    plugins = await _fetch_plugin_directory()
    filtered = plugins
    if category:
        filtered = [p for p in filtered if p.get("category", "").lower() == category.lower()]
    if license:
        filtered = [p for p in filtered if p.get("license", "").lower() == license.lower()]
    if q:
        q_lower = q.lower()
        filtered = [
            p for p in filtered
            if q_lower in p.get("name", "").lower()
            or q_lower in p.get("description", "").lower()
            or q_lower in p.get("author", "").lower()
        ]
    return {
        "plugins": filtered,
        "total": len(filtered),
        "categories": sorted({p["category"] for p in plugins if p.get("category")}),
        "licenses": sorted({p["license"] for p in plugins if p.get("license")}),
    }


@app.post("/plugins/directory/refresh")
async def plugins_directory_refresh(request: Request):
    """플러그인 디렉터리 캐시 강제 갱신"""
    require_user(request)
    mcp_registry._PLUGIN_DIRECTORY_FETCHED_AT = None
    plugins = await _fetch_plugin_directory()
    by_license = {}
    for p in plugins:
        lic = p.get("license", "unknown")
        by_license[lic] = by_license.get(lic, 0) + 1
    return {"status": "ok", "total": len(plugins), "by_license": by_license}


@app.post("/mcp/call")
async def mcp_call(req: McpCallRequest, request: Request):
    current_user = require_user(request)
    args = req.args or {}
    if req.action == "knowledge_graph_ingest":
        _require_graph()
        return KNOWLEDGE_GRAPH.ingest_message(
            args.get("role") or ("assistant" if args.get("type") == "ai_response" else "user"),
            args.get("content") or "",
            user_email=args.get("user_email") or current_user,
            user_nickname=args.get("user_nickname"),
            source=args.get("source") or "mcp",
            conversation_id=args.get("conversation_id"),
            raw=args,
        )
    if req.action == "knowledge_graph_search":
        _require_graph()
        return KNOWLEDGE_GRAPH.search(args.get("query") or args.get("q") or "", args.get("limit", 30))
    if req.action == "knowledge_graph_graph":
        _require_graph()
        return KNOWLEDGE_GRAPH.graph(args.get("limit", 300))
    if req.action == "knowledge_graph_context":
        _require_graph()
        return {
            "context": KNOWLEDGE_GRAPH.context_for_query(
                args.get("query") or args.get("q") or "",
                args.get("limit", 6),
            )
        }
    _check_tool_role(req.action, current_user)
    return _tool_response(execute_tool, req.action, req.args or {})


# ── P-Reinforce Knowledge Gardener ────────────────────────────────────────────

@app.post("/garden")
async def garden(req: GardenRequest, request: Request):
    """Raw 데이터를 P-Reinforce 구조로 자동 분류·저장"""
    require_user(request)
    result = await gardener.process(req.raw_data, req.category)
    return result


@app.get("/garden/tree")
async def garden_tree(request: Request):
    """지식 정원 파일트리 반환"""
    require_user(request)
    return gardener.get_tree()


# ── Setup Wizard ─────────────────────────────────────────────────────────────

class SetupInstallRequest(BaseModel):
    items: List[Dict]

def setup_auto_state() -> Dict[str, object]:
    """Return the PPT-aligned zero-config setup state used by setup UI/API."""
    profile = auto_setup_probe()
    recommendation = auto_setup_recommend(profile)
    install_plan = auto_setup_plan(profile, recommendation)
    return {
        "probe": profile.to_json(),
        "recommend": recommendation.to_json(),
        "plan": install_plan.to_json(),
        "verify": auto_setup_verify(profile, recommendation),
        "preset": auto_setup_preset(profile, recommendation),
    }


def primary_setup_model(recs: Dict[str, object]) -> Optional[Dict[str, object]]:
    models = recs.get("models") if isinstance(recs, dict) else None
    if not isinstance(models, list):
        return None
    candidates = [
        item for item in models
        if isinstance(item, dict) and not item.get("disabled") and (item.get("model_id") or (item.get("action") or {}).get("model_id"))
    ]
    if not candidates:
        return None
    return next((item for item in candidates if item.get("checked")), candidates[0])


@app.get("/setup/scan")
async def setup_scan(request: Request):
    """환경 감지 및 맞춤 추천 반환."""
    require_user(request)
    env  = scan_environment()
    recs = get_recommendations(env)
    zero_config = setup_auto_state()
    primary_model = primary_setup_model(recs)
    if primary_model:
        model_id = primary_model.get("model_id") or (primary_model.get("action") or {}).get("model_id")
        model_provider, provider_model = parse_model_ref(str(model_id))
        primary_runtime = "mlx" if model_provider == "local_mlx" else model_provider
        zero_config.setdefault("recommend", {})["model_id"] = model_id
        zero_config["recommend"]["runtime"] = primary_runtime
        rationale = [
            item for item in zero_config["recommend"].get("rationale", [])
            if not (isinstance(item, str) and item.startswith("RAM ") and "→" in item)
        ]
        rationale.append(f"실제 다운로드 및 로드 가능한 {primary_runtime} 모델 → {model_id}")
        zero_config["recommend"]["rationale"] = rationale
        if isinstance(zero_config.get("plan"), dict):
            if model_provider == "ollama":
                command = ["ollama", "pull", provider_model]
            elif model_provider in {"vllm", "lmstudio", "llamacpp"}:
                command = ["lattice-ai", "models", "load", str(model_id)]
            else:
                command = ["huggingface-cli", "download", str(model_id), "--quiet"]
            zero_config["plan"]["steps"] = [{
                "name": f"weights:{model_id}",
                "why": "추론에 사용할 모델 가중치",
                "command": command,
                "requires_admin": False,
            }]
        if isinstance(zero_config.get("preset"), dict):
            zero_config["preset"].setdefault("model", {})["id"] = model_id
            zero_config["preset"]["model"]["runtime"] = primary_runtime
    env["zero_config"] = zero_config
    recs.setdefault("summary", {})["zero_config"] = zero_config["recommend"]
    recs["install_plan"] = zero_config["plan"]
    recs["preset"] = zero_config["preset"]
    return {"environment": env, "recommendations": recs, "zero_config": zero_config}

@app.get("/setup/auto")
async def setup_auto(request: Request):
    """PPT-aligned zero-config setup pipeline: probe → recommend → plan → verify → preset."""
    require_user(request)
    return setup_auto_state()

@app.post("/setup/install")
async def setup_install(req: SetupInstallRequest, request: Request):
    """선택된 항목을 순서대로 설치 · 로드하는 SSE 스트림."""
    require_user(request)
    async def _gen():
        async for chunk in install_stream(req.items, router):
            yield chunk
    return StreamingResponse(_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.post("/setup/open-auth/{mcp_id}")
async def setup_open_auth(mcp_id: str, request: Request):
    require_user(request)
    """MCP 인증 페이지를 브라우저에서 자동으로 엽니다."""
    auth_urls: Dict[str, str] = {
        "github":      "https://github.com/apps",
        "google-drive": "https://chatgpt.com/connectors",
        "slack":       "https://chatgpt.com/connectors",
        "chrome":      "https://chatgpt.com/connectors",
        "computer-use": "https://chatgpt.com/connectors",
        "figma":       "https://chatgpt.com/connectors",
        "notion":      "https://chatgpt.com/connectors",
        "linear":      "https://chatgpt.com/connectors",
        "gmail":       "https://chatgpt.com/connectors",
        "google-calendar": "https://chatgpt.com/connectors",
        "outlook-email": "https://chatgpt.com/connectors",
        "outlook-calendar": "https://chatgpt.com/connectors",
        "teams":       "https://chatgpt.com/connectors",
        "sharepoint":  "https://chatgpt.com/connectors",
        "canva":       "https://chatgpt.com/connectors",
    }
    url = auth_urls.get(mcp_id)
    if not url:
        raise HTTPException(status_code=404, detail=f"알 수 없는 MCP: {mcp_id}")
    open_url(url)
    return {"status": "ok", "opened": url, "mcp_id": mcp_id}


@app.post("/permissions/open/{permission_id}")
async def open_permission_settings(permission_id: str, request: Request):
    require_user(request)
    """macOS 권한 설정 화면을 엽니다."""
    urls = {
        "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
        "automation": "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation",
        "screen": "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
    }
    url = urls.get(permission_id)
    if not url:
        raise HTTPException(status_code=404, detail="알 수 없는 권한 설정입니다.")
    open_url(url)
    return {"status": "ok", "opened": url, "permission": permission_id}


# ── Entry Point ────────────────────────────────────────────────────────────────

def main() -> None:
    print(f"🧠 Lattice AI Server starting in {APP_MODE} mode on http://{DEFAULT_HOST}:{DEFAULT_PORT}")
    uvicorn.run(app, host=DEFAULT_HOST, port=DEFAULT_PORT, log_level="info")


if __name__ == "__main__":
    main()
