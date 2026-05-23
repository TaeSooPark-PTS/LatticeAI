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
from enum import Enum
from typing import AsyncIterator, Optional, List, Dict, TypedDict

import uvicorn
from fastapi import FastAPI, File, HTTPException, Request, Cookie, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from PIL import Image

from llm_router import AsyncOpenAI, LLMRouter, OPENAI_COMPATIBLE_PROVIDERS, HF_MODELS_ROOT, ensure_mlx_runtime, hf_model_dir, parse_model_ref, mx, normalize_branding
from knowledge_graph import KnowledgeGraphStore
from p_reinforce import BRAIN_DIR, PReinforceGardener
from setup import get_recommendations, install_stream, open_url, scan_environment
from telegram_bot import broadcast_web_chat
from tools import (
    AGENT_ROOT,
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

def env_value(primary: str, default: Optional[str] = None) -> str:
    return os.getenv(primary) or default or ""

def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}

APP_MODE = env_value("LATTICEAI_MODE", "local").strip().lower()
if APP_MODE not in {"local", "public"}:
    APP_MODE = "local"
IS_PUBLIC_MODE = APP_MODE == "public"
DEFAULT_HOST = env_value("LATTICEAI_HOST", "127.0.0.1")
DEFAULT_PORT = int(env_value("LATTICEAI_PORT", "4825"))
def _host_is_loopback(host: str) -> bool:
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False

NETWORK_EXPOSED = not _host_is_loopback(DEFAULT_HOST)
ENABLE_TELEGRAM = env_bool("LATTICEAI_ENABLE_TELEGRAM", default=not IS_PUBLIC_MODE)
ENABLE_GRAPH    = env_bool("LATTICEAI_ENABLE_GRAPH",    default=True)
AUTOLOAD_MODELS = env_bool("LATTICEAI_AUTOLOAD_MODELS", default=IS_PUBLIC_MODE)
MODEL_IDLE_UNLOAD_SECONDS = int(env_value("LATTICEAI_MODEL_IDLE_UNLOAD_SECONDS", "0"))
ALLOW_LOCAL_MODELS = env_bool("LATTICEAI_ALLOW_LOCAL_MODELS", default=not IS_PUBLIC_MODE)
REQUIRE_AUTH = env_bool("LATTICEAI_REQUIRE_AUTH", default=IS_PUBLIC_MODE or NETWORK_EXPOSED)
ALLOW_PLAINTEXT_API_KEYS = env_bool("LATTICEAI_ALLOW_PLAINTEXT_API_KEYS", default=False)
CORS_ALLOW_NETWORK = env_bool("LATTICEAI_CORS_ALLOW_NETWORK", default=False)
CORS_EXTRA_ORIGINS = [
    item.strip()
    for item in env_value("LATTICEAI_CORS_ALLOWED_ORIGINS", "").split(",")
    if item.strip()
]
PUBLIC_MODEL = env_value("LATTICEAI_PUBLIC_MODEL", env_value("LATTICEAI_DEFAULT_MODEL", "openai:gpt-4o-mini"))
LOCAL_MODEL = env_value("LATTICEAI_LOCAL_MODEL", "mlx-community/gemma-4-26b-a4b-it-4bit")
LOCAL_DRAFT_MODEL = env_value("LATTICEAI_LOCAL_DRAFT_MODEL", "")

# ── SSO / OIDC config ─────────────────────────────────────────────────────────
SSO_DISCOVERY_URL = env_value("OIDC_DISCOVERY_URL", "")
SSO_CLIENT_ID = env_value("OIDC_CLIENT_ID", "")
SSO_CLIENT_SECRET = env_value("OIDC_CLIENT_SECRET", "")
SSO_REDIRECT_URI = env_value("OIDC_REDIRECT_URI", "http://localhost:4825/auth/sso/callback")
SSO_PROVIDER_NAME = env_value("OIDC_PROVIDER_NAME", "SSO")
_sso_discovery_cache: Optional[Dict] = None
_sso_states: Dict[str, float] = {}  # state → timestamp (CSRF protection)

async def _get_sso_discovery() -> Optional[Dict]:
    global _sso_discovery_cache
    if _sso_discovery_cache:
        return _sso_discovery_cache
    if not SSO_DISCOVERY_URL:
        return None
    try:
        import httpx as _httpx
        async with _httpx.AsyncClient() as c:
            r = await c.get(SSO_DISCOVERY_URL, timeout=10)
            _sso_discovery_cache = r.json()
    except Exception as e:
        logging.warning("SSO discovery failed: %s", e)
        return None
    return _sso_discovery_cache

# ── Password hashing (stdlib scrypt, no extra deps) ────────────────────────────
def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    key = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1)
    return f"{salt}:{key.hex()}"

def verify_password(password: str, hashed: str) -> bool:
    try:
        salt, key_hex = hashed.split(":", 1)
        key = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1)
        return secrets.compare_digest(key.hex(), key_hex)
    except Exception:
        return False

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
        logging.info("Migrated plaintext password to bcrypt hash for %s", email)
        return True
    return False

# ── Session store (file-backed, survives restarts) ────────────────────────────
# 24-hour TTL with sliding-window refresh — every authenticated request bumps
# created_at, so an active user stays logged in while idle sessions auto-expire.
_SESSION_TTL = 60 * 60 * 24  # 24 hours
_SESSION_REFRESH_THRESHOLD = 60 * 15  # only persist if >15 min since last bump (write amplification guard)
_sessions_lock = threading.Lock()

def _sessions_file() -> Path:
    data_dir = Path(os.getenv("LATTICEAI_DATA_DIR") or (Path.home() / ".ltcai"))
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "sessions.json"

def _load_sessions() -> Dict[str, tuple]:
    try:
        f = _sessions_file()
        if f.exists():
            raw = json.loads(f.read_text())
            return {k: tuple(v) for k, v in raw.items()}
    except Exception as e:
        logging.warning("_load_sessions failed (starting empty): %s", e)
    return {}

def _persist_sessions(sessions: Dict[str, tuple]) -> None:
    try:
        _sessions_file().write_text(json.dumps({k: list(v) for k, v in sessions.items()}, ensure_ascii=False))
    except Exception as e:
        logging.warning("_persist_sessions failed: %s", e)

_sessions: Dict[str, tuple] = _load_sessions()

# ── Rate limiting ─────────────────────────────────────────────────────────────
_rate_windows: dict[tuple[str, str], list[float]] = {}
_rate_lock = threading.Lock()

def _check_rate_limit(ip: str, action: str, max_calls: int, window_secs: float) -> None:
    key = (ip, action)
    now = time.time()
    cutoff = now - window_secs
    with _rate_lock:
        calls = [t for t in _rate_windows.get(key, []) if t > cutoff]
        if len(calls) >= max_calls:
            raise HTTPException(status_code=429, detail="요청이 너무 많습니다. 잠시 후 다시 시도하세요.")
        calls.append(now)
        _rate_windows[key] = calls

def _client_ip(request: Request) -> str:
    for header in ("CF-Connecting-IP", "X-Forwarded-For"):
        val = request.headers.get(header)
        if val:
            return val.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

# ─────────────────────────────────────────────────────────────────────────────

def create_session(email: str) -> str:
    token = secrets.token_urlsafe(32)
    with _sessions_lock:
        _sessions[token] = (email, time.time())
        _persist_sessions(_sessions)
    return token

def get_session_email(token: str) -> Optional[str]:
    """Return email for a valid session, sliding the expiry forward on activity."""
    now = time.time()
    with _sessions_lock:
        entry = _sessions.get(token)
        if entry is None:
            return None
        email, created_at = entry
        if now - created_at > _SESSION_TTL:
            _sessions.pop(token, None)
            _persist_sessions(_sessions)
            return None
        # Sliding refresh: only update if the timestamp drifted enough to be worth a disk write
        if now - created_at > _SESSION_REFRESH_THRESHOLD:
            _sessions[token] = (email, now)
            _persist_sessions(_sessions)
        return email

def invalidate_session(token: str) -> None:
    with _sessions_lock:
        _sessions.pop(token, None)
        _persist_sessions(_sessions)

# ── User Management Logic ──────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(env_value("LATTICEAI_DATA_DIR", str(Path.home() / ".ltcai")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR = Path(env_value("LATTICEAI_STATIC_DIR", str(BASE_DIR / "static")))
if not STATIC_DIR.exists():
    packaged_static = Path(sys.prefix) / "static"
    if packaged_static.exists():
        STATIC_DIR = packaged_static

USERS_FILE = DATA_DIR / "users.json"
HISTORY_FILE = DATA_DIR / "chat_history.json"
VPC_FILE = DATA_DIR / "vpc_config.json"
MCP_FILE = DATA_DIR / "mcp_installs.json"
AUDIT_FILE = DATA_DIR / "audit_log.json"
KNOWLEDGE_GRAPH = KnowledgeGraphStore(DATA_DIR / "knowledge_graph.sqlite", DATA_DIR / "knowledge_graph_blobs") if ENABLE_GRAPH else None

def _require_graph():
    if not ENABLE_GRAPH or KNOWLEDGE_GRAPH is None:
        raise HTTPException(status_code=404, detail="Data Graph is disabled. Set LATTICEAI_ENABLE_GRAPH=true in .env to enable.")

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

class McpRecommendRequest(BaseModel):
    query: str
    limit: int = 5

class McpInstallRequest(BaseModel):
    mcp_id: str

class SkillInstallRequest(BaseModel):
    plugin: str
    skill: str

class KnowledgeGraphIngestRequest(BaseModel):
    type: str
    content: str = ""
    role: Optional[str] = None
    title: Optional[str] = None
    source: Optional[str] = None
    conversation_id: Optional[str] = None
    user_email: Optional[str] = None
    user_nickname: Optional[str] = None
    metadata: Optional[Dict] = None

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

MCP_REGISTRY = [
    {
        "id": "presentations",
        "name": "Presentations MCP",
        "category": "PPT / slides",
        "install_mode": "bundled",
        "description": "PowerPoint, Google Slides용 발표자료를 만들고 렌더링 검수까지 이어갑니다.",
        "keywords": ["ppt", "powerpoint", "slides", "slide", "deck", "presentation", "발표", "피피티", "프레젠테이션", "슬라이드", "제안서"],
        "capabilities": ["PPTX 생성", "슬라이드 구조화", "차트 중심 스토리", "렌더링 검수"],
    },
    {
        "id": "documents",
        "name": "Documents MCP",
        "category": "Docs / reports",
        "install_mode": "bundled",
        "description": "Word 문서, 보고서, 계약서 초안, 문서 redline 및 시각 검수를 처리합니다.",
        "keywords": ["docx", "word", "docs", "document", "report", "문서", "보고서", "계약서", "기획서", "레포트"],
        "capabilities": ["DOCX 생성", "문서 편집", "코멘트/수정", "PDF 렌더 확인"],
    },
    {
        "id": "spreadsheets",
        "name": "Spreadsheets MCP",
        "category": "Sheets / data",
        "install_mode": "bundled",
        "description": "Excel/CSV/Google Sheets형 데이터 분석, 수식, 표, 차트를 만듭니다.",
        "keywords": ["xlsx", "excel", "spreadsheet", "sheet", "csv", "data", "엑셀", "스프레드시트", "표", "데이터", "차트"],
        "capabilities": ["XLSX 생성", "수식/서식", "데이터 분석", "차트"],
    },
    {
        "id": "browser",
        "name": "Browser MCP",
        "category": "Web / dashboard QA",
        "install_mode": "bundled",
        "description": "로컬 웹앱, 대시보드, 폼, 페이지 렌더링을 브라우저에서 확인합니다.",
        "keywords": ["dashboard", "web", "website", "frontend", "ui", "browser", "localhost", "대시보드", "웹", "사이트", "프론트", "화면", "검수"],
        "capabilities": ["로컬 페이지 열기", "스크린샷", "DOM 검사", "UI 회귀 확인"],
    },
    {
        "id": "chrome",
        "name": "Chrome MCP",
        "category": "Browser / authenticated web",
        "install_mode": "connector",
        "connector_url": "/mcp/connectors/chrome",
        "external_url": "codex://plugins/chrome",
        "description": "사용자 Chrome 프로필, 로그인 세션, 기존 탭을 활용하는 브라우저 자동화 브리지입니다.",
        "keywords": ["chrome", "browser", "cookie", "session", "login", "크롬", "브라우저", "로그인", "세션", "탭"],
        "capabilities": ["Chrome 탭 확인", "로그인 세션 활용", "프로필 기반 웹 자동화"],
    },
    {
        "id": "computer-use",
        "name": "Computer Use MCP",
        "category": "Desktop / Mac UI",
        "install_mode": "connector",
        "connector_url": "/mcp/connectors/computer-use",
        "external_url": "codex://plugins/computer-use",
        "description": "Mac 앱 화면을 읽고 클릭, 타이핑, 스크롤하는 데스크톱 UI 자동화 브리지입니다.",
        "keywords": ["computer use", "desktop", "mac", "click", "type", "scroll", "컴퓨터", "맥", "앱", "클릭", "타이핑"],
        "capabilities": ["Mac 앱 UI 조작", "스크린샷 기반 상태 확인", "클릭/입력/스크롤"],
    },
    {
        "id": "filesystem",
        "name": "Workspace Files MCP",
        "category": "Files / coding",
        "install_mode": "builtin",
        "description": "프로젝트 파일 읽기/쓰기, 검색, 코드 생성, 로컬 preview URL 생성을 수행합니다.",
        "keywords": ["code", "coding", "file", "folder", "project", "build", "deploy", "구현", "코드", "파일", "폴더", "프로젝트", "빌드", "배포"],
        "capabilities": ["파일 생성", "코드 검색", "빌드 스크립트", "배포 스크립트"],
    },
    {
        "id": "google-drive",
        "name": "Google Drive Connector",
        "category": "File sharing",
        "install_mode": "connector",
        "connector_url": "/mcp/connectors/google-drive",
        "external_url": "https://chatgpt.com/connectors",
        "description": "Drive/Docs/Sheets/Slides 파일 공유, 검색, 협업 워크플로에 사용합니다.",
        "keywords": ["share", "sharing", "drive", "google drive", "file share", "공유", "파일공유", "드라이브", "구글드라이브", "협업"],
        "capabilities": ["파일 공유", "Drive 검색", "Google Docs/Sheets/Slides 연결"],
    },
    {
        "id": "github",
        "name": "GitHub Connector",
        "category": "Code hosting",
        "install_mode": "connector",
        "connector_url": "/mcp/connectors/github",
        "external_url": "https://github.com/apps",
        "description": "저장소, 이슈, PR, CI 확인과 코드 배포 워크플로를 연결합니다.",
        "keywords": ["github", "repo", "repository", "pr", "pull request", "issue", "ci", "깃허브", "저장소", "이슈", "배포"],
        "capabilities": ["PR 확인", "이슈 탐색", "CI 확인", "릴리즈 준비"],
    },
    {
        "id": "slack",
        "name": "Slack Connector",
        "category": "Team sharing",
        "install_mode": "connector",
        "connector_url": "/mcp/connectors/slack",
        "external_url": "https://chatgpt.com/connectors",
        "description": "팀 채널에 결과 공유, 논의 요약, 알림 워크플로를 연결합니다.",
        "keywords": ["slack", "message", "team", "notify", "공유", "알림", "메시지", "슬랙", "팀"],
        "capabilities": ["채널 공유", "메시지 작성", "협업 알림"],
    },
    {
        "id": "obsidian-memory",
        "name": "Obsidian Memory Vault",
        "category": "Memory / knowledge",
        "install_mode": "builtin",
        "description": "Lattice AI의 장기 기억을 Obsidian 호환 Markdown vault에 저장하고 검색합니다.",
        "keywords": ["memory", "remember", "obsidian", "vault", "knowledge", "기억", "메모리", "옵시디언", "지식", "노트"],
        "capabilities": ["Markdown vault 저장", "장기 기억 검색", "Obsidian URI 힌트", "프로젝트 로그"],
    },
    {
        "id": "voice-whisper",
        "name": "Voice STT (Whisper Local)",
        "category": "Voice / speech-to-text",
        "install_mode": "pip",
        "pip_packages": ["openai-whisper"],
        "description": "로컬 음성 인식(STT) 파이프라인용 Whisper 런타임을 설치합니다.",
        "keywords": ["voice", "speech", "stt", "whisper", "audio", "음성", "인식", "자막", "전사"],
        "capabilities": ["로컬 STT 런타임", "오디오 전사 워크플로 준비"],
    },
    {
        "id": "voice-speechrecognition",
        "name": "Voice STT (SpeechRecognition)",
        "category": "Voice / speech-to-text",
        "install_mode": "pip",
        "pip_packages": ["SpeechRecognition"],
        "description": "가벼운 음성 인식 실험용 SpeechRecognition 패키지를 설치합니다.",
        "keywords": ["voice", "speech", "recognition", "stt", "microphone", "음성", "마이크", "받아쓰기"],
        "capabilities": ["STT 파이썬 패키지", "마이크 입력 인식 실험"],
    },
    {
        "id": "audio-pydub",
        "name": "Audio Processing (PyDub)",
        "category": "Voice / audio processing",
        "install_mode": "pip",
        "pip_packages": ["pydub"],
        "description": "오디오 파일 분할/정규화/포맷 변환 워크플로용 패키지를 설치합니다.",
        "keywords": ["audio", "pydub", "wav", "mp3", "전처리", "오디오", "변환"],
        "capabilities": ["오디오 전처리", "세그먼트 분할", "포맷 변환"],
    },
    {
        "id": "threejs-workflow",
        "name": "3D Workflow (Three.js)",
        "category": "3D / interactive web",
        "install_mode": "bundled",
        "description": "브라우저 검수 + 코드 생성 흐름으로 Three.js 기반 3D 화면을 구현/검증합니다.",
        "keywords": ["3d", "three", "threejs", "webgl", "scene", "3차원", "쓰리제이에스", "렌더링"],
        "capabilities": ["Three.js 코드 생성", "3D 씬 검수", "브라우저 상호작용 테스트"],
    },
    {
        "id": "figma",
        "name": "Figma Connector",
        "category": "Design / handoff",
        "install_mode": "connector",
        "connector_url": "/mcp/connectors/figma",
        "external_url": "https://chatgpt.com/connectors",
        "description": "디자인 파일 참조, 컴포넌트 규칙 확인, 구현 핸드오프를 연결합니다.",
        "keywords": ["figma", "design", "handoff", "컴포넌트", "디자인", "피그마"],
        "capabilities": ["디자인 참조", "핸드오프 워크플로", "컴포넌트 맵핑"],
    },
    {
        "id": "notion",
        "name": "Notion Connector",
        "category": "Knowledge / docs",
        "install_mode": "connector",
        "connector_url": "/mcp/connectors/notion",
        "external_url": "https://chatgpt.com/connectors",
        "description": "노션 문서/DB와 연동해 구현 노트, 회의 요약, 지식 관리 워크플로를 만듭니다.",
        "keywords": ["notion", "wiki", "docs", "database", "노션", "위키", "문서", "지식관리"],
        "capabilities": ["페이지 검색", "문서 작성 보조", "지식 동기화"],
    },
    {
        "id": "linear",
        "name": "Linear Connector",
        "category": "Project / issue tracking",
        "install_mode": "connector",
        "connector_url": "/mcp/connectors/linear",
        "external_url": "https://chatgpt.com/connectors",
        "description": "이슈 상태 확인, 우선순위 정리, 릴리즈 태스크 연결에 사용합니다.",
        "keywords": ["linear", "issue", "project", "sprint", "이슈", "태스크", "프로젝트"],
        "capabilities": ["이슈 조회", "작업 우선순위", "릴리즈 트래킹"],
    },
    {
        "id": "gmail",
        "name": "Gmail Connector",
        "category": "Communication / email",
        "install_mode": "connector",
        "connector_url": "/mcp/connectors/gmail",
        "external_url": "https://chatgpt.com/connectors",
        "description": "이메일 요약, 답장 초안, 업무 메일 정리에 사용합니다.",
        "keywords": ["gmail", "email", "mail", "inbox", "메일", "지메일", "이메일"],
        "capabilities": ["메일 검색", "요약", "답장 초안"],
    },
    {
        "id": "google-calendar",
        "name": "Google Calendar Connector",
        "category": "Scheduling / calendar",
        "install_mode": "connector",
        "connector_url": "/mcp/connectors/google-calendar",
        "external_url": "https://chatgpt.com/connectors",
        "description": "일정 확인, 미팅 슬롯 탐색, 일정 생성 워크플로를 연결합니다.",
        "keywords": ["calendar", "schedule", "meeting", "구글캘린더", "일정", "미팅"],
        "capabilities": ["일정 조회", "빈 시간 탐색", "이벤트 생성"],
    },
    {
        "id": "outlook-email",
        "name": "Outlook Email Connector",
        "category": "Communication / email",
        "install_mode": "connector",
        "connector_url": "/mcp/connectors/outlook-email",
        "external_url": "https://chatgpt.com/connectors",
        "description": "Outlook 메일함 연동, 메일 검색/초안/요약 워크플로를 제공합니다.",
        "keywords": ["outlook", "email", "mail", "아웃룩", "메일"],
        "capabilities": ["메일 검색", "요약", "초안 작성"],
    },
    {
        "id": "outlook-calendar",
        "name": "Outlook Calendar Connector",
        "category": "Scheduling / calendar",
        "install_mode": "connector",
        "connector_url": "/mcp/connectors/outlook-calendar",
        "external_url": "https://chatgpt.com/connectors",
        "description": "Outlook 일정 연동으로 회의 준비/시간 조율 작업을 진행합니다.",
        "keywords": ["outlook calendar", "calendar", "schedule", "아웃룩 캘린더", "일정"],
        "capabilities": ["일정 조회", "회의 준비", "시간 조율"],
    },
    {
        "id": "teams",
        "name": "Microsoft Teams Connector",
        "category": "Team collaboration",
        "install_mode": "connector",
        "connector_url": "/mcp/connectors/teams",
        "external_url": "https://chatgpt.com/connectors",
        "description": "팀 대화 컨텍스트 기반 업무 자동화와 협업 공유를 지원합니다.",
        "keywords": ["teams", "microsoft teams", "chat", "협업", "팀즈"],
        "capabilities": ["팀 대화 공유", "협업 흐름 연결"],
    },
    {
        "id": "sharepoint",
        "name": "SharePoint Connector",
        "category": "Enterprise files",
        "install_mode": "connector",
        "connector_url": "/mcp/connectors/sharepoint",
        "external_url": "https://chatgpt.com/connectors",
        "description": "SharePoint 문서 저장소를 검색/참조하는 엔터프라이즈 워크플로를 지원합니다.",
        "keywords": ["sharepoint", "document", "enterprise", "문서", "셰어포인트"],
        "capabilities": ["문서 검색", "사내 파일 참조"],
    },
    {
        "id": "canva",
        "name": "Canva Connector",
        "category": "Design / visuals",
        "install_mode": "connector",
        "connector_url": "/mcp/connectors/canva",
        "external_url": "https://chatgpt.com/connectors",
        "description": "디자인 템플릿 기반 이미지/슬라이드 작업을 연동합니다.",
        "keywords": ["canva", "design", "poster", "card", "캔바", "디자인"],
        "capabilities": ["디자인 템플릿", "이미지 제작 워크플로"],
    },
]

# ── Remote MCP Registry (registry.modelcontextprotocol.io) ───────────────────
_REMOTE_REGISTRY_CACHE: List[Dict] = []
_REMOTE_REGISTRY_FETCHED_AT: Optional[datetime] = None
_REMOTE_REGISTRY_TTL = timedelta(hours=1)
_REMOTE_REGISTRY_URL = "https://registry.modelcontextprotocol.io/v0/servers"
_LOCAL_IDS = {e["id"] for e in MCP_REGISTRY}

async def _fetch_remote_mcp_registry() -> List[Dict]:
    global _REMOTE_REGISTRY_CACHE, _REMOTE_REGISTRY_FETCHED_AT
    now = datetime.now()
    if _REMOTE_REGISTRY_FETCHED_AT and (now - _REMOTE_REGISTRY_FETCHED_AT) < _REMOTE_REGISTRY_TTL:
        return _REMOTE_REGISTRY_CACHE
    try:
        result: List[Dict] = []
        cursor = None
        async with httpx.AsyncClient(timeout=10.0) as client:
            while True:
                params: Dict = {"limit": 100}
                if cursor:
                    params["cursor"] = cursor
                resp = await client.get(_REMOTE_REGISTRY_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
                for s in data.get("servers", []):
                    srv = s["server"]
                    meta = s.get("_meta", {}).get("io.modelcontextprotocol.registry/official", {})
                    if not meta.get("isLatest", True):
                        continue
                    pkg = next(
                        (p for p in srv.get("packages", [])
                         if p.get("transport", {}).get("type") == "stdio"
                         and p.get("registryType") in ("npm", "pypi")),
                        None,
                    )
                    if not pkg:
                        continue
                    entry_id = srv["name"].replace("/", "-").replace(".", "-")
                    if entry_id in _LOCAL_IDS:
                        continue
                    result.append({
                        "id": entry_id,
                        "name": srv.get("title") or srv["name"],
                        "category": "MCP Registry",
                        "install_mode": pkg["registryType"],
                        "package": pkg["identifier"],
                        "package_version": pkg.get("version"),
                        "description": srv.get("description", ""),
                        "keywords": [],
                        "capabilities": [],
                        "source": "registry",
                        "homepage": (srv.get("repository") or {}).get("url"),
                    })
                cursor = data.get("nextCursor")
                if not cursor:
                    break
        _REMOTE_REGISTRY_CACHE = result
        _REMOTE_REGISTRY_FETCHED_AT = now
        logging.info("Fetched %d stdio MCP servers from remote registry", len(result))
    except Exception as e:
        logging.warning("Failed to fetch remote MCP registry: %s", e)
    return _REMOTE_REGISTRY_CACHE

async def _get_combined_registry() -> List[Dict]:
    remote = await _fetch_remote_mcp_registry()
    return MCP_REGISTRY + remote

# ── Anthropic Skills Marketplace (Apache 2.0) ─────────────────────────────────
_MARKETPLACE_RAW = "https://raw.githubusercontent.com/anthropics/claude-plugins-official/main"
_MARKETPLACE_API = "https://api.github.com/repos/anthropics/claude-plugins-official/contents"

# 검증된 서드파티 skills 소스 (Apache-2.0 / MIT)
_THIRD_PARTY_SKILL_SOURCES: List[Dict] = [
    {
        "plugin": "adobe-for-creativity", "author": "Adobe", "license": "Apache-2.0",
        "repo": "adobe/skills", "branch": "main",
        "plugin_path": "plugins/creative-cloud/adobe-for-creativity",
        "category": "design",
    },
    {
        "plugin": "airtable", "author": "Airtable", "license": "MIT",
        "repo": "Airtable/skills", "branch": "main",
        "plugin_path": "plugins/airtable",
        "category": "productivity",
    },
    {
        "plugin": "auth0", "author": "Auth0", "license": "Apache-2.0",
        "repo": "auth0/agent-skills", "branch": "main",
        "plugin_path": "plugins/auth0",
        "category": "security",
    },
    {
        "plugin": "expo", "author": "Expo", "license": "MIT",
        "repo": "expo/skills", "branch": "main",
        "plugin_path": "plugins/expo",
        "category": "development",
    },
    {
        "plugin": "logfire", "author": "Pydantic", "license": "MIT",
        "repo": "pydantic/skills", "branch": "main",
        "plugin_path": "plugins/logfire",
        "category": "monitoring",
    },
]

# 검증된 레포 라이선스 맵 (GitHub API 없이 빠르게 조회)
_KNOWN_REPO_LICENSES: Dict[str, str] = {
    # Apache-2.0
    "adobe/skills": "Apache-2.0", "awslabs/agent-plugins": "Apache-2.0",
    "auth0/agent-skills": "Apache-2.0", "aws/agent-toolkit-for-aws": "Apache-2.0",
    "carta/plugins": "Apache-2.0", "circlefin/skills": "Apache-2.0",
    "clickhouse/clickhouse-docs": "Apache-2.0", "cloudflare/agents": "Apache-2.0",
    "cockroachdb/claude-code": "Apache-2.0", "codspeed-hq/codspeed-claude": "Apache-2.0",
    "DataDog/datadog-claude-code": "Apache-2.0", "datahub-project/datahub-skills": "Apache-2.0",
    "neondatabase/agent-skills": "Apache-2.0", "PagerDuty/pd-ai-agents-plugins": "Apache-2.0",
    "getpostman/postman-mcp-server": "Apache-2.0", "qdrant/qdrant-skills": "Apache-2.0",
    "rootlyhq/rootly-plugins": "Apache-2.0", "snowflake-labs/snowflake-claude": "Apache-2.0",
    "sumup/sumup-claude": "Apache-2.0", "zilliz-labs/zilliz-skills": "Apache-2.0",
    "mercadopago/mercadopago-claude-marketplace": "Apache-2.0",
    # MIT
    "Airtable/skills": "MIT", "endorlabs/ai-plugins": "MIT",
    "apollographql/apollo-claude-skills": "MIT", "appwrite/skills": "MIT",
    "atlan-inc/claude-code-skills": "MIT", "boxer/boxerbox": "MIT",
    "buildkite/claude-code": "MIT", "coderabbitai/coderabbit-skills": "MIT",
    "CrowdStrike/crowdstrike-skills": "MIT", "microsoft/Dataverse-skills": "MIT",
    "duckdb/duckdb-skills": "MIT", "expo/skills": "MIT",
    "intercom/intercom-skills": "MIT", "pydantic/skills": "MIT",
    "mapbox/mapbox-skills": "MIT", "mintlify/mintlify-skills": "MIT",
    "miroapp/miro-ai": "MIT", "netlify/netlify-skills": "MIT",
    "pinecone-io/pinecone-skills": "MIT", "railwayapp/railway-skills": "MIT",
    "resend/resend-skills": "MIT", "sanity-io/sanity-skills": "MIT",
    "getsentry/sentry-ai-skills": "MIT", "Shopify/liquid-skills": "MIT",
    "slackapi/slack-skills": "MIT", "stripe/stripe-skills": "MIT",
    "twilio-labs/twilio-skills": "MIT", "workos/workos-skills": "MIT",
    "zoom/zoom-skills": "MIT", "aws-samples/sample-claude-code-plugins-for-startups": "MIT-0",
}

_SKILLS_MARKETPLACE_CACHE: List[Dict] = []
_SKILLS_MARKETPLACE_FETCHED_AT: Optional[datetime] = None
_SKILLS_MARKETPLACE_TTL = timedelta(hours=1)

def _extract_skill_desc(skill_md: str, fallback: str) -> str:
    for line in skill_md.splitlines():
        if line.startswith("description:"):
            return line.split(":", 1)[1].strip()
    return fallback

async def _fetch_plugin_skills(client: httpx.AsyncClient, source: Dict) -> List[Dict]:
    """단일 소스에서 skill 목록을 fetch해 반환"""
    repo, branch, plugin_path = source["repo"], source["branch"], source["plugin_path"]
    raw_base = f"https://raw.githubusercontent.com/{repo}/{branch}"
    api_base = f"https://api.github.com/repos/{repo}/contents"
    homepage_base = f"https://github.com/{repo}/tree/{branch}"

    dir_resp = await client.get(f"{api_base}/{plugin_path}/skills")
    if dir_resp.status_code != 200:
        return []
    skill_dirs = [f["name"] for f in dir_resp.json() if f["type"] == "dir"]

    skills: List[Dict] = []
    for skill_name in skill_dirs:
        skill_md_url = f"{raw_base}/{plugin_path}/skills/{skill_name}/SKILL.md"
        sm_resp = await client.get(skill_md_url)
        if sm_resp.status_code != 200:
            continue
        skills.append({
            "plugin":       source["plugin"],
            "skill":        skill_name,
            "category":     source.get("category", "development"),
            "description":  _extract_skill_desc(sm_resp.text, source.get("description", "")),
            "skill_md_url": skill_md_url,
            "homepage":     f"{homepage_base}/{plugin_path}/skills/{skill_name}",
            "license":      source["license"],
            "author":       source["author"],
        })
    return skills

async def _fetch_skills_marketplace() -> List[Dict]:
    global _SKILLS_MARKETPLACE_CACHE, _SKILLS_MARKETPLACE_FETCHED_AT
    now = datetime.now()
    if _SKILLS_MARKETPLACE_FETCHED_AT and (now - _SKILLS_MARKETPLACE_FETCHED_AT) < _SKILLS_MARKETPLACE_TTL:
        return _SKILLS_MARKETPLACE_CACHE
    try:
        result: List[Dict] = []
        async with httpx.AsyncClient(timeout=15.0) as client:
            # ── Anthropic 공식 skills (Apache-2.0) ──────────────────────────
            mp_resp = await client.get(f"{_MARKETPLACE_RAW}/.claude-plugin/marketplace.json")
            mp_resp.raise_for_status()
            marketplace_json = mp_resp.json()
            anthropic_plugins = [
                p for p in marketplace_json.get("plugins", [])
                if (p.get("author") or {}).get("name") == "Anthropic"
                and isinstance(p.get("source"), str)
                and p["source"].startswith("./")
            ]
            for plugin in anthropic_plugins:
                plugin_path = plugin["source"].lstrip("./")
                result.extend(await _fetch_plugin_skills(client, {
                    "plugin":      plugin["name"],
                    "author":      "Anthropic",
                    "license":     "Apache-2.0",
                    "repo":        "anthropics/claude-plugins-official",
                    "branch":      "main",
                    "plugin_path": plugin_path,
                    "category":    plugin.get("category", "development"),
                    "description": plugin.get("description", ""),
                }))
            # ── 검증된 서드파티 skills ────────────────────────────────────────
            for source in _THIRD_PARTY_SKILL_SOURCES:
                result.extend(await _fetch_plugin_skills(client, source))

        _SKILLS_MARKETPLACE_CACHE = result
        _SKILLS_MARKETPLACE_FETCHED_AT = now
        logging.info("Fetched %d skills from marketplace (%d sources)",
                     len(result), len(anthropic_plugins) + len(_THIRD_PARTY_SKILL_SOURCES))
    except Exception as e:
        logging.warning("Failed to fetch skills marketplace: %s", e)
    return _SKILLS_MARKETPLACE_CACHE

# ── Plugin Directory ──────────────────────────────────────────────────────────
_PLUGIN_DIRECTORY_CACHE: List[Dict] = []
_PLUGIN_DIRECTORY_FETCHED_AT: Optional[datetime] = None
_PLUGIN_DIRECTORY_TTL = timedelta(hours=1)
_OPEN_LICENSES = {"Apache-2.0", "MIT", "MIT-0", "CC-BY-4.0"}
_REPO_LICENSE_CACHE: Dict[str, str] = {}

async def _get_repo_license(client: httpx.AsyncClient, repo: str) -> str:
    if repo in _REPO_LICENSE_CACHE:
        return _REPO_LICENSE_CACHE[repo]
    if repo in _KNOWN_REPO_LICENSES:
        _REPO_LICENSE_CACHE[repo] = _KNOWN_REPO_LICENSES[repo]
        return _KNOWN_REPO_LICENSES[repo]
    try:
        r = await client.get(f"https://api.github.com/repos/{repo}", timeout=5.0)
        lic = (r.json().get("license") or {}).get("spdx_id", "") if r.status_code == 200 else ""
    except Exception:
        lic = ""
    _REPO_LICENSE_CACHE[repo] = lic
    return lic

async def _fetch_plugin_directory() -> List[Dict]:
    global _PLUGIN_DIRECTORY_CACHE, _PLUGIN_DIRECTORY_FETCHED_AT
    now = datetime.now()
    if _PLUGIN_DIRECTORY_FETCHED_AT and (now - _PLUGIN_DIRECTORY_FETCHED_AT) < _PLUGIN_DIRECTORY_TTL:
        return _PLUGIN_DIRECTORY_CACHE
    try:
        result: List[Dict] = []
        async with httpx.AsyncClient(timeout=15.0) as client:
            mp_resp = await client.get(f"{_MARKETPLACE_RAW}/.claude-plugin/marketplace.json")
            mp_resp.raise_for_status()
            plugins = mp_resp.json().get("plugins", [])

            for p in plugins:
                author = (p.get("author") or {}).get("name", "")
                src = p.get("source", {})

                # Anthropic 같은 레포 플러그인 → Apache-2.0 확인됨
                if isinstance(src, str) and src.startswith("./") and author == "Anthropic":
                    plugin_path = src.lstrip("./")
                    result.append({
                        "name":        p["name"],
                        "description": p.get("description", ""),
                        "category":    p.get("category", ""),
                        "author":      author,
                        "license":     "Apache-2.0",
                        "homepage":    p.get("homepage") or f"https://github.com/anthropics/claude-plugins-official/tree/main/{plugin_path}",
                        "source_type": "anthropic",
                    })
                    continue

                # 외부 레포 플러그인 → 라이선스 확인
                if not isinstance(src, dict):
                    continue
                repo_url = src.get("url", "").replace("https://github.com/", "").replace(".git", "").split("/tree/")[0]
                if not repo_url:
                    continue
                license_id = await _get_repo_license(client, repo_url)
                if license_id not in _OPEN_LICENSES:
                    continue
                result.append({
                    "name":        p["name"],
                    "description": p.get("description", ""),
                    "category":    p.get("category", ""),
                    "author":      author or repo_url.split("/")[0],
                    "license":     license_id,
                    "homepage":    p.get("homepage") or f"https://github.com/{repo_url}",
                    "source_type": "third-party",
                })

        _PLUGIN_DIRECTORY_CACHE = result
        _PLUGIN_DIRECTORY_FETCHED_AT = now
        logging.info("Fetched plugin directory: %d open-source plugins", len(result))
    except Exception as e:
        logging.warning("Failed to fetch plugin directory: %s", e)
    return _PLUGIN_DIRECTORY_CACHE

# ─────────────────────────────────────────────────────────────────────────────

SKILLS_DIR = Path(__file__).resolve().parent / "skills"

async def install_skill(plugin: str, skill: str) -> Dict:
    marketplace = await _fetch_skills_marketplace()
    entry = next((s for s in marketplace if s["plugin"] == plugin and s["skill"] == skill), None)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Skill '{plugin}/{skill}' not found in marketplace")
    skill_dir = SKILLS_DIR / skill
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md_path = skill_dir / "SKILL.md"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(entry["skill_md_url"])
        resp.raise_for_status()
        content = resp.text
    # 출처 표기 (Apache-2.0 / MIT 공통)
    repo_hint = entry.get("homepage", "")
    attribution = f"<!-- Source: {repo_hint}, {entry['license']} -->\n"
    if not content.startswith("<!--"):
        content = attribution + content
    skill_md_path.write_text(content, encoding="utf-8")
    risk_path = skill_dir / "risk.json"
    if not risk_path.exists():
        risk_path.write_text(json.dumps({
            "risk": "read", "destructive": False,
            "shell": False, "network": False,
            "auto_approve": True, "sandbox": "workspace", "rollback": "none"
        }, indent=2), encoding="utf-8")
    return {
        "status":  "installed",
        "plugin":  plugin,
        "skill":   skill,
        "path":    str(skill_dir),
        "license": entry["license"],
        "author":  entry["author"],
    }

# ─────────────────────────────────────────────────────────────────────────────

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
    if not os.path.exists(AUDIT_FILE):
        return []
    try:
        with open(AUDIT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        logging.warning("get_audit_log failed: %s", e)
        return []

def append_audit_event(event_type: str, **payload) -> None:
    try:
        event = {
            "event_type": event_type,
            "timestamp": datetime.now().isoformat(),
            **payload,
        }
        with _history_lock:
            events = get_audit_log()
            events.append(event)
            if len(events) > 5000:
                events = events[-5000:]
            tmp_path = str(AUDIT_FILE) + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(events, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, AUDIT_FILE)
    except Exception as e:
        logging.warning("append_audit_event failed: %s", e)

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
    if not text:
        return ""
    patterns = [
        r"(?i)(api[_ -]?key|secret|token|password|passwd)\s*[:=]\s*['\"]?([A-Za-z0-9_\-\.]{12,})['\"]?",
        r"\b(sk-[A-Za-z0-9_\-]{16,})\b",
        r"\b(xai-[A-Za-z0-9_\-]{16,})\b",
        r"\b(gsk_[A-Za-z0-9_\-]{16,})\b",
    ]
    redacted = str(text)
    for pattern in patterns:
        redacted = re.sub(pattern, lambda m: f"{m.group(1)}=[REDACTED]" if len(m.groups()) > 1 else "[REDACTED]", redacted)
    return redacted

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
    admin_emails = {
        item.strip().lower()
        for item in env_value("LATTICEAI_ADMIN_EMAILS", "").split(",")
        if item.strip()
    }
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


# ── Rate limiting ─────────────────────────────────────────────────────────────
# Per-user token bucket. Disabled when LATTICEAI_RATE_LIMIT=0 (default: enabled).
_RATE_LIMIT_ENABLED = os.getenv("LATTICEAI_RATE_LIMIT", "1") != "0"
_rate_buckets: Dict[str, Dict[str, float]] = {}
_rate_lock = threading.Lock()

# (capacity, refill_per_second) per endpoint family
_RATE_LIMITS = {
    "chat":   (30, 0.5),   # 30 burst, 30/min sustained
    "agent":  (10, 0.1),   # 10 burst, 6/min sustained (agent is expensive)
    "upload": (20, 0.2),   # 20 burst, 12/min sustained
}


def enforce_rate_limit(email: str, bucket_key: str) -> None:
    """Raise HTTP 429 if user exceeds the bucket. No-op when disabled or unauth'd."""
    if not _RATE_LIMIT_ENABLED or not email:
        return
    cap, refill = _RATE_LIMITS.get(bucket_key, (60, 1.0))
    key = f"{email}:{bucket_key}"
    now = time.time()
    with _rate_lock:
        bucket = _rate_buckets.get(key)
        if bucket is None:
            _rate_buckets[key] = {"tokens": cap - 1, "ts": now}
            return
        elapsed = now - bucket["ts"]
        bucket["tokens"] = min(cap, bucket["tokens"] + elapsed * refill)
        bucket["ts"] = now
        if bucket["tokens"] < 1:
            retry_after = max(1, int((1 - bucket["tokens"]) / refill))
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded for {bucket_key}. Retry after {retry_after}s.",
                headers={"Retry-After": str(retry_after)},
            )
        bucket["tokens"] -= 1


# ── File magic-number validation ──────────────────────────────────────────────
# Map of extension → list of byte-prefix signatures (any-match). Files without
# distinctive magic (.txt, .md, .csv) skip the check.
_FILE_MAGIC: Dict[str, List[bytes]] = {
    ".pdf":  [b"%PDF-"],
    ".docx": [b"PK\x03\x04"],
    ".xlsx": [b"PK\x03\x04"],
    ".pptx": [b"PK\x03\x04"],
    ".zip":  [b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"],
    ".png":  [b"\x89PNG\r\n\x1a\n"],
    ".jpg":  [b"\xff\xd8\xff"],
    ".jpeg": [b"\xff\xd8\xff"],
    ".gif":  [b"GIF87a", b"GIF89a"],
}


def _bytes_match_extension(data: bytes, ext: str) -> bool:
    """Return True if the file bytes match the claimed extension (or extension has no magic)."""
    ext = (ext or "").lower()
    signatures = _FILE_MAGIC.get(ext)
    if not signatures:
        return True  # text-like formats — no reliable magic
    head = data[:16]
    return any(head.startswith(sig) for sig in signatures)

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

SENSITIVE_PATTERNS = [
    {"key": "rrn", "label": "주민등록번호", "severity": "high", "pattern": r"\b\d{6}[- ]?[1-4]\d{6}\b"},
    {"key": "card", "label": "카드번호", "severity": "high", "pattern": r"\b(?:\d[ -]?){13,19}\b"},
    {"key": "account", "label": "계좌번호", "severity": "medium", "pattern": r"(?:계좌|account|bank).{0,12}\d[\d -]{8,24}"},
    {"key": "password", "label": "비밀번호/인증정보", "severity": "high", "pattern": r"(?:password|passwd|비밀번호|암호|token|api[_ -]?key|secret)\s*[:=]\s*[^\s,;]{4,}"},
    {"key": "email", "label": "이메일", "severity": "low", "pattern": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"},
    {"key": "phone", "label": "전화번호", "severity": "medium", "pattern": r"\b(?:01[016789]|02|0[3-6][1-5])[- ]?\d{3,4}[- ]?\d{4}\b"},
    {"key": "address", "label": "주소", "severity": "medium", "pattern": r"(?:[가-힣]+(?:시|도)\s*)?[가-힣]+(?:시|군|구)\s+[가-힣0-9\s-]+(?:로|길)\s*\d*"},
    {"key": "health", "label": "건강/의료정보", "severity": "medium", "pattern": r"(?:진단|병명|처방|복용|수술|장애|임신|혈액형|알레르기|medical|diagnosis)"},
]

SEVERITY_SCORE = {"low": 1, "medium": 2, "high": 3}

def mask_sensitive_text(text: str, matches: List[Dict]) -> str:
    masked = text
    for item in sorted(matches, key=lambda match: match["start"], reverse=True):
        value = masked[item["start"]:item["end"]]
        if len(value) <= 4:
            replacement = "*" * len(value)
        else:
            replacement = value[:2] + "*" * min(len(value) - 4, 12) + value[-2:]
        masked = masked[:item["start"]] + replacement + masked[item["end"]:]
    return masked

def classify_sensitive_message(item: Dict, index: int) -> Dict:
    content = str(item.get("content", ""))
    found = []
    seen = set()
    for rule in SENSITIVE_PATTERNS:
        for match in re.finditer(rule["pattern"], content, flags=re.IGNORECASE):
            key = (rule["key"], match.start(), match.end())
            if key in seen:
                continue
            seen.add(key)
            found.append({
                "type": rule["key"],
                "label": rule["label"],
                "severity": rule["severity"],
                "start": match.start(),
                "end": match.end(),
            })
    severity = "none"
    if found:
        severity = max(found, key=lambda item: SEVERITY_SCORE[item["severity"]])["severity"]
    preview_text = content[:240]
    preview_matches = [match for match in found if match["start"] < len(preview_text)]
    return {
        "index": index,
        "role": item.get("role", ""),
        "user_email": item.get("user_email"),
        "user_nickname": item.get("user_nickname") or item.get("user_email") or "Unknown",
        "timestamp": item.get("timestamp"),
        "sensitivity": severity,
        "labels": sorted({match["label"] for match in found}),
        "risk_fields": found,
        "compliance_fields": [] if found else ["민감정보 미검출"],
        "preview": mask_sensitive_text(preview_text, preview_matches),
    }

def build_sensitivity_report(history: List[Dict]) -> Dict:
    items = [classify_sensitive_message(item, index) for index, item in enumerate(history)]
    risky_items = [item for item in items if item["risk_fields"]]
    compliant_items = [item for item in items if not item["risk_fields"]]
    field_counts = {}
    user_counts = {}
    severity_counts = {"high": 0, "medium": 0, "low": 0, "none": len(compliant_items)}
    for item in risky_items:
        severity_counts[item["sensitivity"]] += 1
        user_key = item.get("user_email") or item.get("user_nickname") or "Unknown"
        user_counts[user_key] = user_counts.get(user_key, 0) + 1
        for field in item["risk_fields"]:
            field_counts[field["label"]] = field_counts.get(field["label"], 0) + 1
    return {
        "summary": {
            "total_messages": len(items),
            "risky_messages": len(risky_items),
            "compliant_messages": len(compliant_items),
            "risk_rate": round((len(risky_items) / len(items)) * 100, 1) if items else 0,
            "severity_counts": severity_counts,
            "field_counts": field_counts,
            "user_counts": user_counts,
        },
        "risk_fields": risky_items[-30:],
        "compliance_fields": compliant_items[-30:],
    }

AUDIT_DELETE_EVENTS = {"conversation_delete", "history_delete", "user_delete"}

def _audit_user_bucket(email: Optional[str], nickname: Optional[str] = None, users: Optional[Dict] = None) -> Dict:
    user = (users or {}).get(email or "", {})
    return {
        "email": email or "Unknown",
        "nickname": nickname or user.get("nickname") or user.get("name") or email or "Unknown",
        "role": get_user_role(email, users or {}) if email else "unknown",
        "disabled": bool(user.get("disabled")) if user else False,
        "user_messages": 0,
        "assistant_messages": 0,
        "document_uploads": 0,
        "clear_events": 0,
        "delete_events": 0,
        "sensitive_events": 0,
        "high_sensitive_events": 0,
        "total_content_chars": 0,
        "last_activity_at": None,
    }

def _public_audit_event(event: Dict) -> Dict:
    allowed = {
        "event_type",
        "timestamp",
        "role",
        "user_email",
        "user_nickname",
        "source",
        "conversation_id",
        "command",
        "scope",
        "target_email",
        "filename",
        "mime_type",
        "ext",
        "bytes",
        "extracted_chars",
        "graph_node",
        "keep_last",
        "removed",
        "kept",
        "started_at",
        "sensitivity",
        "sensitive_labels",
        "content_preview",
        "content_chars",
    }
    return {key: event.get(key) for key in allowed if key in event}

def build_admin_audit_report(users: Dict) -> Dict:
    events = get_audit_log()
    per_user: Dict[str, Dict] = {}

    def ensure_user(email: Optional[str], nickname: Optional[str] = None) -> Dict:
        key = email or nickname or "Unknown"
        if key not in per_user:
            per_user[key] = _audit_user_bucket(email, nickname, users)
        elif nickname and per_user[key].get("nickname") in {"Unknown", email, None}:
            per_user[key]["nickname"] = nickname
        return per_user[key]

    for email, user in users.items():
        ensure_user(email, user.get("nickname") or user.get("name"))

    summary = {
        "total_events": len(events),
        "chat_events": 0,
        "user_messages": 0,
        "assistant_messages": 0,
        "document_uploads": 0,
        "clear_events": 0,
        "delete_events": 0,
        "sensitive_events": 0,
        "high_sensitive_events": 0,
    }

    sensitive_events = []
    deletion_events = []
    for event in events:
        event_type = event.get("event_type")
        email = event.get("user_email")
        user = ensure_user(email, event.get("user_nickname"))
        timestamp = event.get("timestamp")
        if timestamp and (not user["last_activity_at"] or timestamp > user["last_activity_at"]):
            user["last_activity_at"] = timestamp

        user["total_content_chars"] += int(event.get("content_chars") or event.get("extracted_chars") or 0)
        sensitivity = event.get("sensitivity") or "none"
        labels = event.get("sensitive_labels") or []
        is_sensitive = sensitivity != "none" or bool(labels)

        if event_type == "chat_message":
            summary["chat_events"] += 1
            if event.get("role") == "user":
                summary["user_messages"] += 1
                user["user_messages"] += 1
            elif event.get("role") == "assistant":
                summary["assistant_messages"] += 1
                user["assistant_messages"] += 1
        elif event_type == "document_upload":
            summary["document_uploads"] += 1
            user["document_uploads"] += 1
        elif event_type == "clear_command":
            summary["clear_events"] += 1
            user["clear_events"] += 1
        elif event_type in AUDIT_DELETE_EVENTS:
            summary["delete_events"] += 1
            user["delete_events"] += 1
            deletion_events.append(_public_audit_event(event))

        if is_sensitive:
            summary["sensitive_events"] += 1
            user["sensitive_events"] += 1
            sensitive_events.append(_public_audit_event(event))
        if sensitivity == "high":
            summary["high_sensitive_events"] += 1
            user["high_sensitive_events"] += 1

    return {
        "summary": summary,
        "per_user": sorted(
            per_user.values(),
            key=lambda item: (item.get("last_activity_at") or "", item.get("user_messages", 0) + item.get("assistant_messages", 0)),
            reverse=True,
        ),
        "recent_events": [_public_audit_event(event) for event in events[-80:]][::-1],
        "sensitive_events": sensitive_events[-80:][::-1],
        "deletion_events": deletion_events[-80:][::-1],
    }

router = LLMRouter()
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
    except Exception as e:
        print(f"⚠️ Startup sequence failed: {e}")
    try:
        yield
    finally:
        router.unload_all()
        for proc in LOCAL_SERVER_PROCESSES.values():
            try:
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait(timeout=5)
            except Exception:
                pass

app = FastAPI(title=f"Lattice AI Server ({APP_MODE})", version="2.1.0", lifespan=lifespan)

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

OPEN_REGISTRATION = env_bool("LATTICEAI_OPEN_REGISTRATION", default=not NETWORK_EXPOSED and not IS_PUBLIC_MODE)

@app.post("/register")
async def register(req: UserRegister, request: Request):
    # 5 registration attempts per IP per hour
    _check_rate_limit(_client_ip(request), "register", max_calls=5, window_secs=3600)
    if not OPEN_REGISTRATION:
        raise HTTPException(status_code=403, detail="회원가입이 비활성화되어 있습니다. 관리자에게 문의하세요.")
    users = load_users()
    if req.email in users:
        raise HTTPException(status_code=400, detail="이미 존재하는 이메일입니다.")
    # First user to register on a fresh server becomes admin automatically
    role = "admin" if not users else "user"
    users[req.email] = {
        "password": hash_password(req.password),
        "name": req.name,
        "nickname": req.nickname,
        "role": role,
        "disabled": False,
    }
    save_users(users)
    msg = "회원가입 성공! 첫 번째 사용자로 관리자 권한이 부여되었습니다." if role == "admin" else "회원가입 성공!"
    return {"status": "ok", "message": msg, "role": role}

@app.post("/login")
async def login(req: UserLogin, request: Request):
    # 10 login attempts per IP per 5 minutes
    _check_rate_limit(_client_ip(request), "login", max_calls=10, window_secs=300)
    users = load_users()
    user = users.get(req.email)
    if not user or not verify_and_migrate_password(req.email, req.password, user.get("password", ""), users):
        raise HTTPException(status_code=401, detail="이메일 또는 비밀번호가 틀렸습니다.")
    if user.get("disabled"):
        raise HTTPException(status_code=403, detail="비활성화된 계정입니다.")
    role = get_user_role(req.email, users)
    token = create_session(req.email)
    response = JSONResponse(content={
        "status": "ok",
        "nickname": user["nickname"],
        "name": user["name"],
        "email": req.email,
        "role": role,
        "is_admin": role == "admin",
    })
    response.set_cookie(key="session_token", value=token, httponly=True, samesite="lax", max_age=_SESSION_TTL)
    return response

@app.get("/auth/sso/config")
async def sso_config():
    enabled = bool(SSO_DISCOVERY_URL and SSO_CLIENT_ID and SSO_CLIENT_SECRET)
    return {"enabled": enabled, "provider_name": SSO_PROVIDER_NAME if enabled else ""}

@app.get("/auth/sso/login")
async def sso_login():
    from urllib.parse import urlencode
    from fastapi.responses import RedirectResponse as _Redirect
    discovery = await _get_sso_discovery()
    if not discovery:
        raise HTTPException(status_code=503, detail="SSO가 설정되지 않았습니다.")
    state = secrets.token_urlsafe(16)
    _sso_states[state] = time.time()
    params = urlencode({
        "client_id": SSO_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": SSO_REDIRECT_URI,
        "scope": "openid email profile",
        "state": state,
    })
    return _Redirect(f"{discovery['authorization_endpoint']}?{params}")

@app.get("/auth/sso/callback")
async def sso_callback(code: str = "", state: str = "", error: str = ""):
    from fastapi.responses import RedirectResponse as _Redirect
    import base64 as _b64
    if error:
        return _Redirect(f"/?sso_error={error}")
    ts = _sso_states.pop(state, None)
    if ts is None or time.time() - ts > 300:
        raise HTTPException(status_code=400, detail="유효하지 않은 SSO 상태입니다.")
    discovery = await _get_sso_discovery()
    if not discovery:
        raise HTTPException(status_code=503, detail="SSO 설정 오류입니다.")
    import httpx as _httpx
    async with _httpx.AsyncClient() as c:
        r = await c.post(discovery["token_endpoint"], data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": SSO_REDIRECT_URI,
            "client_id": SSO_CLIENT_ID,
            "client_secret": SSO_CLIENT_SECRET,
        }, headers={"Accept": "application/json"}, timeout=15)
        tokens = r.json()
    id_token = tokens.get("id_token")
    if not id_token:
        raise HTTPException(status_code=400, detail="ID 토큰을 받지 못했습니다.")
    # Decode JWT payload (no signature verification — trust IdP redirect)
    padded = id_token.split(".")[1] + "=="
    payload = json.loads(_b64.urlsafe_b64decode(padded))
    email = payload.get("email") or payload.get("preferred_username") or payload.get("upn") or ""
    if not email:
        raise HTTPException(status_code=400, detail="이메일을 확인할 수 없습니다.")
    users = load_users()
    if email not in users:
        is_first = len(users) == 0
        users[email] = {
            "password": "",
            "name": payload.get("name", email.split("@")[0]),
            "nickname": payload.get("given_name", email.split("@")[0]),
            "role": "admin" if is_first else "user",
            "disabled": False,
            "sso": True,
        }
        save_users(users)
    if users[email].get("disabled"):
        raise HTTPException(status_code=403, detail="비활성화된 계정입니다.")
    token = create_session(email)
    resp = _Redirect("/chat", status_code=302)
    resp.set_cookie("session_token", token, httponly=True, samesite="lax", max_age=_SESSION_TTL)
    return resp

@app.post("/logout")
async def logout(request: Request):
    token = _extract_bearer_token(request)
    if token:
        invalidate_session(token)
    response = JSONResponse(content={"status": "ok"})
    response.delete_cookie("session_token")
    return response

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

@app.post("/account/change-password")
async def change_password(req: ChangePasswordRequest, request: Request):
    email = require_user(request)
    if not email:
        raise HTTPException(status_code=401, detail="인증이 필요합니다.")
    if len(req.new_password) < 4:
        raise HTTPException(status_code=400, detail="새 비밀번호는 4자 이상이어야 합니다.")
    users = load_users()
    user = users.get(email)
    if not user:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    if not verify_and_migrate_password(email, req.current_password, user.get("password", ""), users):
        raise HTTPException(status_code=401, detail="현재 비밀번호가 틀렸습니다.")
    users[email]["password"] = hash_password(req.new_password)
    save_users(users)
    return {"status": "ok", "message": "비밀번호가 변경되었습니다."}

class UpdateProfileRequest(BaseModel):
    name: Optional[str] = None
    nickname: Optional[str] = None

@app.patch("/account/profile")
async def update_profile(req: UpdateProfileRequest, request: Request):
    email = require_user(request)
    if not email:
        raise HTTPException(status_code=401, detail="인증이 필요합니다.")
    if req.name is not None and not req.name.strip():
        raise HTTPException(status_code=400, detail="이름을 입력해주세요.")
    if req.nickname is not None and not req.nickname.strip():
        raise HTTPException(status_code=400, detail="닉네임을 입력해주세요.")
    users = load_users()
    user = users.get(email)
    if not user:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    if req.name is not None:
        users[email]["name"] = req.name.strip()
    if req.nickname is not None:
        users[email]["nickname"] = req.nickname.strip()
    save_users(users)
    return {"status": "ok", "name": users[email]["name"], "nickname": users[email]["nickname"]}

@app.get("/account/profile")
async def get_profile(request: Request):
    email = require_user(request)
    if not email:
        raise HTTPException(status_code=401, detail="인증이 필요합니다.")
    users = load_users()
    user = users.get(email)
    if not user:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    role = get_user_role(email, users)
    return {"email": email, "name": user.get("name", ""), "nickname": user.get("nickname", ""),
            "role": role, "is_admin": role == "admin"}

@app.get("/admin/summary")
async def admin_summary(request: Request):
    _, users = require_admin(request)
    history = get_history()
    user_messages = [item for item in history if item.get("role") == "user"]
    assistant_messages = [item for item in history if item.get("role") == "assistant"]
    last_timestamp = history[-1].get("timestamp") if history else None
    return {
        "total_users": len(users),
        "active_users": sum(1 for user in users.values() if not user.get("disabled")),
        "admin_users": sum(1 for email in users if get_user_role(email, users) == "admin"),
        "total_messages": len(history),
        "user_messages": len(user_messages),
        "assistant_messages": len(assistant_messages),
        "last_message_at": last_timestamp,
    }

@app.get("/admin/stats")
async def admin_stats(request: Request):
    require_admin(request)
    history = get_history()
    from collections import defaultdict
    daily: dict = defaultdict(lambda: {"user": 0, "assistant": 0})
    for item in history:
        ts = item.get("timestamp", "")
        day = ts[:10] if ts else "unknown"
        role = item.get("role", "")
        if role in ("user", "assistant"):
            daily[day][role] += 1
    sorted_days = sorted(daily.keys())[-14:]
    return {
        "daily": [{"date": d, "user": daily[d]["user"], "assistant": daily[d]["assistant"]} for d in sorted_days]
    }

@app.get("/admin/users")
async def admin_users(request: Request):
    _, users = require_admin(request)
    return [public_user(email, user, users) for email, user in users.items()]

@app.get("/admin/sensitivity")
async def admin_sensitivity(request: Request):
    require_admin(request)
    return build_sensitivity_report(get_history())

@app.get("/admin/audit")
async def admin_audit(request: Request):
    _, users = require_admin(request)
    report = build_admin_audit_report(users)
    try:
        report["graph"] = KNOWLEDGE_GRAPH.stats() if (ENABLE_GRAPH and KNOWLEDGE_GRAPH) else {"disabled": True}
    except Exception as e:
        logging.warning("knowledge graph stats for audit failed: %s", e)
        report["graph"] = {"error": str(e)}
    return report

@app.get("/vpc/status")
async def vpc_status(request: Request):
    require_user(request)
    return load_vpc_config()

@app.patch("/admin/vpc")
async def admin_update_vpc(req: VpcConfigUpdate, request: Request):
    require_admin(request)
    config = load_vpc_config()
    update = req.dict(exclude_unset=True)
    if "private_subnets" in update and update["private_subnets"] is not None:
        update["private_subnets"] = [item.strip() for item in update["private_subnets"] if item.strip()]
    config.update(update)
    save_vpc_config(config)
    return config

@app.patch("/admin/users/{email:path}")
async def admin_update_user(email: str, req: AdminUserUpdate, request: Request):
    admin_email, users = require_admin(request)
    if email not in users:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    before = public_user(email, users[email], users)
    if req.role is not None:
        if req.role not in {"admin", "user"}:
            raise HTTPException(status_code=400, detail="role은 admin 또는 user만 가능합니다.")
        users[email]["role"] = req.role
    if req.disabled is not None:
        if email == admin_email and req.disabled:
            raise HTTPException(status_code=400, detail="자기 자신은 비활성화할 수 없습니다.")
        users[email]["disabled"] = req.disabled
    save_users(users)
    after = public_user(email, users[email], users)
    append_audit_event("user_update", user_email=admin_email, target_email=email, before=before, after=after)
    return after

@app.delete("/admin/users/{email:path}")
async def admin_delete_user(email: str, request: Request):
    admin_email, users = require_admin(request)
    if email == admin_email:
        raise HTTPException(status_code=400, detail="자기 자신은 삭제할 수 없습니다.")
    if email not in users:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    deleted = public_user(email, users[email], users)
    append_audit_event("user_delete", user_email=admin_email, target_email=email, deleted_user=deleted)
    del users[email]
    save_users(users)
    return {"status": "ok", "deleted": deleted}

@app.get("/admin/invite-link")
async def admin_invite_link(request: Request):
    require_admin(request)
    host = request.headers.get("host", f"localhost:{DEFAULT_PORT}")
    scheme = "https" if request.headers.get("x-forwarded-proto") == "https" else "http"
    if INVITE_GATE_ENABLED:
        url = f"{scheme}://{host}/?code={INVITE_CODE}"
    else:
        url = f"{scheme}://{host}/"
    return {"invite_url": url, "invite_code": INVITE_CODE, "gate_enabled": INVITE_GATE_ENABLED}

# ── Invitation Logic ────────────────────────────────────────────────────────
INVITE_CODE = env_value("LATTICEAI_INVITE_CODE", "gemma-lattice-ai")
INVITE_GATE_ENABLED = env_bool("LATTICEAI_INVITE_GATE_ENABLED", default=False)

@app.get("/")
async def root(request: Request, code: Optional[str] = None, authorized: Optional[str] = Cookie(None)):
    """로그인/회원가입 페이지. 초대 게이트 활성화 시 코드 검증 후 진입."""
    if not INVITE_GATE_ENABLED:
        return FileResponse(STATIC_DIR / "account.html")

    # 1. 이미 쿠키로 인증된 경우
    if authorized == "true":
        return FileResponse(STATIC_DIR / "account.html")

    # 2. 초대 코드가 일치하는 경우 (최초 진입)
    if code == INVITE_CODE:
        response = FileResponse(STATIC_DIR / "account.html")
        response.set_cookie(key="authorized", value="true", httponly=True, samesite="lax", max_age=60*60*24*7)
        return response

    # 3. 인증 실패 시 차단 화면
    return HTMLResponse(content=f"""
        <body style="background:#0f1115; color:white; display:flex; flex-direction:column; align-items:center; justify-content:center; height:100vh; font-family:sans-serif;">
            <div style="background:#16191f; padding:40px; border-radius:24px; border:1px solid rgba(255,255,255,0.1); text-align:center; box-shadow: 0 20px 40px rgba(0,0,0,0.5);">
                <div style="font-size:48px; margin-bottom:20px;">🔒</div>
                <h1 style="color:#378ADD; margin:0; font-size:24px;">Invitation Required</h1>
                <p style="color:#94a3b8; margin:20px 0; line-height:1.6;">이 서비스는 비공개로 운영되고 있습니다.<br>선생님께 받은 <b>초대용 전용 링크</b>를 통해 접속해 주세요.</p>
                <div style="margin-top:30px; padding-top:20px; border-top:1px solid rgba(255,255,255,0.05); font-size:11px; color:rgba(255,255,255,0.2); letter-spacing:1px;">LATTICE AI SECURITY AGENT</div>
            </div>
        </body>
    """, status_code=403)


@app.get("/account")
async def account_page():
    """Direct login/register page route used by logout and manual navigation."""
    return FileResponse(STATIC_DIR / "account.html")


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
    return FileResponse(STATIC_DIR / "chat.html")


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


class AgentState(str, Enum):
    IDLE             = "IDLE"
    PLANNING         = "PLANNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    EXECUTING        = "EXECUTING"
    VERIFYING        = "VERIFYING"
    FAILED           = "FAILED"
    ROLLBACK         = "ROLLBACK"
    DONE             = "DONE"


# Terminal states — the agent loop exits when reaching one of these
AGENT_TERMINAL_STATES = frozenset({AgentState.DONE, AgentState.FAILED})


class AgentRunContext:
    """Mutable state carrier passed through all agent phases."""
    __slots__ = ("state", "plan", "transcript", "retry_count",
                 "state_history", "corrections", "final_message", "rollback_log",
                 "executing_model", "reviewing_model")

    def __init__(self) -> None:
        self.state:           AgentState   = AgentState.IDLE
        self.plan:            dict         = {}
        self.transcript:      list         = []
        self.retry_count:     int          = 0
        self.state_history:   list         = []
        self.corrections:     list         = []
        self.final_message:   str          = ""
        self.rollback_log:    list         = []
        self.executing_model: str | None   = None
        self.reviewing_model: str | None   = None


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
        {"id": "mlx-community/gemma-4-e2b-4bit", "name": "Gemma 4 E2B Base", "family": "Gemma 4", "tag": "local-vlm", "size": "3.6GB", "pullable": True},
        {"id": "mlx-community/gemma-4-e2b-it-4bit", "name": "Gemma 4 E2B Instruct", "family": "Gemma 4", "tag": "local-vlm", "size": "3.6GB", "pullable": True},
        {"id": "mlx-community/gemma-4-e4b-4bit", "name": "Gemma 4 E4B Base", "family": "Gemma 4", "tag": "local-vlm", "size": "5.2GB", "pullable": True},
        {"id": "mlx-community/gemma-4-e4b-it-4bit", "name": "Gemma 4 E4B Instruct", "family": "Gemma 4", "tag": "local-vlm", "size": "5.2GB", "pullable": True},
        {"id": "mlx-community/gemma-4-26b-a4b-it-4bit", "name": "Gemma 4 26B A4B Instruct", "family": "Gemma 4", "tag": "local-vlm", "size": "Apple Silicon", "pullable": True},
        {"id": "Jiunsong/supergemma4-26b-abliterated-multimodal-mlx-4bit", "name": "SuperGemma4 26B Abliterated Multimodal", "family": "Gemma 4", "tag": "local-vlm", "size": "Apple Silicon", "pullable": True},
        {"id": "mlx-community/Qwen2.5-Coder-3B-Instruct-4bit", "name": "Qwen 2.5 Coder 3B", "family": "Qwen 2.5 Coder", "tag": "local-coding", "size": "2.1GB", "pullable": True},
        {"id": "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit", "name": "Qwen 2.5 Coder 7B", "family": "Qwen 2.5 Coder", "tag": "local-coding", "size": "4.3GB", "pullable": True},
        {"id": "mlx-community/Qwen2.5-Coder-14B-Instruct-4bit", "name": "Qwen 2.5 Coder 14B", "family": "Qwen 2.5 Coder", "tag": "local-coding", "size": "8.5GB", "pullable": True},
        {"id": "mlx-community/Qwen2.5-3B-Instruct-4bit", "name": "Qwen 2.5 3B", "family": "Qwen 2.5", "tag": "local-general", "size": "2.1GB", "pullable": True},
        {"id": "mlx-community/Qwen2.5-7B-Instruct-4bit", "name": "Qwen 2.5 7B", "family": "Qwen 2.5", "tag": "local-general", "size": "4.3GB", "pullable": True},
        {"id": "mlx-community/Qwen2.5-14B-Instruct-4bit", "name": "Qwen 2.5 14B", "family": "Qwen 2.5", "tag": "local-general", "size": "8.5GB", "pullable": True},
        {"id": "mlx-community/Llama-3.2-3B-Instruct-4bit", "name": "Llama 3.2 3B", "family": "Llama 3.x", "tag": "local-general", "size": "2.0GB", "pullable": True},
        {"id": "mlx-community/Llama-3.1-8B-Instruct-4bit", "name": "Llama 3.1 8B", "family": "Llama 3.1", "tag": "local-general", "size": "4.7GB", "pullable": True},
        {"id": "mlx-community/Llama-3.3-70B-Instruct-4bit", "name": "Llama 3.3 70B", "family": "Llama 3.x", "tag": "local-general", "size": "40GB+", "pullable": True},
        {"id": "mlx-community/Llama-3.1-70B-Instruct-4bit", "name": "Llama 3.1 70B", "family": "Llama 3.1", "tag": "local-general", "size": "40GB+", "pullable": True},
        {"id": "mlx-community/Phi-3.5-mini-instruct-4bit", "name": "Phi 3.5 Mini", "family": "Phi", "tag": "local-light", "size": "2.2GB", "pullable": True},
        {"id": "mlx-community/DeepSeek-R1-Distill-Qwen-7B-4bit", "name": "DeepSeek R1 Distill 7B", "family": "DeepSeek", "tag": "reasoning", "size": "4.3GB", "pullable": True},
    ],
    "ollama": [
        {"id": "ollama:gemma3:4b", "name": "Gemma 3 4B via Ollama", "family": "Gemma", "tag": "local-server", "size": "pull required", "pullable": True},
        {"id": "ollama:gemma3:4b-it-q4_K_M", "name": "Gemma 3 4B q4_K_M via Ollama", "family": "Gemma", "tag": "quantized", "size": "pull required", "pullable": True},
        {"id": "ollama:gemma3:12b", "name": "Gemma 3 12B via Ollama", "family": "Gemma", "tag": "local-server", "size": "pull required", "pullable": True},
        {"id": "ollama:gemma3:12b-it-q4_K_M", "name": "Gemma 3 12B q4_K_M via Ollama", "family": "Gemma", "tag": "quantized", "size": "pull required", "pullable": True},
        {"id": "ollama:qwen2.5:3b", "name": "Qwen 2.5 3B via Ollama", "family": "Qwen 2.5", "tag": "local-server", "size": "pull required", "pullable": True},
        {"id": "ollama:qwen2.5:7b", "name": "Qwen 2.5 7B via Ollama", "family": "Qwen 2.5", "tag": "local-server", "size": "pull required", "pullable": True},
        {"id": "ollama:qwen2.5:14b", "name": "Qwen 2.5 14B via Ollama", "family": "Qwen 2.5", "tag": "local-server", "size": "pull required", "pullable": True},
        {"id": "ollama:qwen2.5:32b", "name": "Qwen 2.5 32B via Ollama", "family": "Qwen 2.5", "tag": "local-server", "size": "pull required", "pullable": True},
        {"id": "ollama:qwen2.5-coder:7b", "name": "Qwen 2.5 Coder 7B via Ollama", "family": "Qwen 2.5 Coder", "tag": "local-coding", "size": "pull required", "pullable": True},
        {"id": "ollama:qwen2.5-coder:14b", "name": "Qwen 2.5 Coder 14B via Ollama", "family": "Qwen 2.5 Coder", "tag": "local-coding", "size": "pull required", "pullable": True},
        {"id": "ollama:llama3.2:3b", "name": "Llama 3.2 3B via Ollama", "family": "Llama 3.x", "tag": "local-server", "size": "pull required", "pullable": True},
        {"id": "ollama:llama3.1:8b", "name": "Llama 3.1 8B via Ollama", "family": "Llama 3.1", "tag": "local-server", "size": "pull required", "pullable": True},
        {"id": "ollama:llama3.1:8b-instruct-q4_0", "name": "Llama 3.1 8B q4_0 via Ollama", "family": "Llama 3.1", "tag": "quantized", "size": "pull required", "pullable": True},
        {"id": "ollama:llama3.1:8b-instruct-q8_0", "name": "Llama 3.1 8B q8_0 via Ollama", "family": "Llama 3.1", "tag": "quantized", "size": "pull required", "pullable": True},
        {"id": "ollama:llama3.1:70b", "name": "Llama 3.1 70B via Ollama", "family": "Llama 3.1", "tag": "local-server", "size": "pull required", "pullable": True},
    ],
    "vllm": [
        {"id": "vllm:Qwen/Qwen2.5-0.5B-Instruct-AWQ", "name": "Qwen 2.5 0.5B AWQ via vLLM", "family": "Qwen 2.5", "tag": "local-light", "size": "0.5B", "pullable": True},
        {"id": "vllm:google/gemma-2-2b", "name": "Gemma 2 2B Base via vLLM", "family": "Gemma", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "vllm:google/gemma-2-2b-it", "name": "Gemma 2 2B via vLLM", "family": "Gemma", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "vllm:google/gemma-2-9b", "name": "Gemma 2 9B Base via vLLM", "family": "Gemma", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "vllm:google/gemma-2-9b-it", "name": "Gemma 2 9B via vLLM", "family": "Gemma", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "vllm:Qwen/Qwen2.5-3B-Instruct", "name": "Qwen 2.5 3B via vLLM", "family": "Qwen 2.5", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "vllm:Qwen/Qwen2.5-7B-Instruct", "name": "Qwen 2.5 7B via vLLM", "family": "Qwen 2.5", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "vllm:Qwen/Qwen2.5-14B-Instruct", "name": "Qwen 2.5 14B via vLLM", "family": "Qwen 2.5", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "vllm:Qwen/Qwen2.5-32B-Instruct", "name": "Qwen 2.5 32B via vLLM", "family": "Qwen 2.5", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "vllm:Qwen/Qwen2.5-Coder-7B-Instruct", "name": "Qwen 2.5 Coder 7B via vLLM", "family": "Qwen 2.5 Coder", "tag": "local-coding", "size": "server model", "pullable": True},
        {"id": "vllm:Qwen/Qwen2.5-Coder-14B-Instruct", "name": "Qwen 2.5 Coder 14B via vLLM", "family": "Qwen 2.5 Coder", "tag": "local-coding", "size": "server model", "pullable": True},
        {"id": "vllm:meta-llama/Llama-3.2-3B-Instruct", "name": "Llama 3.2 3B via vLLM", "family": "Llama 3.x", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "vllm:meta-llama/Llama-3.1-8B-Instruct", "name": "Llama 3.1 8B via vLLM", "family": "Llama 3.1", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "vllm:meta-llama/Llama-3.1-70B-Instruct", "name": "Llama 3.1 70B via vLLM", "family": "Llama 3.1", "tag": "local-server", "size": "server model", "pullable": True},
    ],
    "lmstudio": [
        {"id": "lmstudio:https://huggingface.co/lmstudio-community/Qwen2.5-0.5B-Instruct-GGUF", "name": "Qwen 2.5 0.5B GGUF via LM Studio", "family": "Qwen 2.5", "tag": "local-light", "size": "0.5B", "pullable": True},
        {"id": "lmstudio:google/gemma-2-2b-it", "name": "Gemma 2 2B via LM Studio", "family": "Gemma", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "lmstudio:google/gemma-2-9b-it", "name": "Gemma 2 9B via LM Studio", "family": "Gemma", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "lmstudio:Qwen/Qwen2.5-3B-Instruct", "name": "Qwen 2.5 3B via LM Studio", "family": "Qwen 2.5", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "lmstudio:Qwen/Qwen2.5-7B-Instruct", "name": "Qwen 2.5 7B via LM Studio", "family": "Qwen 2.5", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "lmstudio:Qwen/Qwen2.5-14B-Instruct", "name": "Qwen 2.5 14B via LM Studio", "family": "Qwen 2.5", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "lmstudio:Qwen/Qwen2.5-32B-Instruct", "name": "Qwen 2.5 32B via LM Studio", "family": "Qwen 2.5", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "lmstudio:Qwen/Qwen2.5-Coder-7B-Instruct", "name": "Qwen 2.5 Coder 7B via LM Studio", "family": "Qwen 2.5 Coder", "tag": "local-coding", "size": "server model", "pullable": True},
        {"id": "lmstudio:Qwen/Qwen2.5-Coder-14B-Instruct", "name": "Qwen 2.5 Coder 14B via LM Studio", "family": "Qwen 2.5 Coder", "tag": "local-coding", "size": "server model", "pullable": True},
        {"id": "lmstudio:meta-llama/Llama-3.2-3B-Instruct", "name": "Llama 3.2 3B via LM Studio", "family": "Llama 3.x", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "lmstudio:meta-llama/Llama-3.1-8B-Instruct", "name": "Llama 3.1 8B via LM Studio", "family": "Llama 3.1", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "lmstudio:meta-llama/Llama-3.1-70B-Instruct", "name": "Llama 3.1 70B via LM Studio", "family": "Llama 3.1", "tag": "local-server", "size": "server model", "pullable": True},
    ],
    "llamacpp": [
        {"id": "llamacpp:lmstudio-community/Qwen2.5-0.5B-Instruct-GGUF", "name": "Qwen 2.5 0.5B GGUF via llama.cpp", "family": "Qwen 2.5", "tag": "gguf-q4", "size": "0.5B", "pullable": True},
        {"id": "llamacpp:unsloth/gemma-2-2b-it-GGUF", "name": "Gemma 2 2B GGUF via llama.cpp", "family": "Gemma", "tag": "gguf-q4", "size": "gguf", "pullable": True},
        {"id": "llamacpp:unsloth/gemma-2-9b-it-GGUF", "name": "Gemma 2 9B GGUF via llama.cpp", "family": "Gemma", "tag": "gguf-q4", "size": "gguf", "pullable": True},
        {"id": "llamacpp:Qwen/Qwen2.5-7B-Instruct-GGUF", "name": "Qwen 2.5 7B GGUF via llama.cpp", "family": "Qwen 2.5", "tag": "local-server", "size": "gguf", "pullable": True},
        {"id": "llamacpp:Qwen/Qwen2.5-14B-Instruct-GGUF", "name": "Qwen 2.5 14B GGUF via llama.cpp", "family": "Qwen 2.5", "tag": "local-server", "size": "gguf", "pullable": True},
        {"id": "llamacpp:Qwen/Qwen2.5-32B-Instruct-GGUF", "name": "Qwen 2.5 32B GGUF via llama.cpp", "family": "Qwen 2.5", "tag": "gguf-q4", "size": "gguf", "pullable": True},
        {"id": "llamacpp:Qwen/Qwen2.5-Coder-7B-Instruct-GGUF", "name": "Qwen 2.5 Coder 7B GGUF via llama.cpp", "family": "Qwen 2.5 Coder", "tag": "local-coding", "size": "gguf", "pullable": True},
        {"id": "llamacpp:Qwen/Qwen2.5-Coder-14B-Instruct-GGUF", "name": "Qwen 2.5 Coder 14B GGUF via llama.cpp", "family": "Qwen 2.5 Coder", "tag": "gguf-q4", "size": "gguf", "pullable": True},
        {"id": "llamacpp:bartowski/Llama-3.2-3B-Instruct-GGUF", "name": "Llama 3.2 3B GGUF via llama.cpp", "family": "Llama 3.x", "tag": "gguf-q4", "size": "gguf", "pullable": True},
        {"id": "llamacpp:bartowski/Llama-3.1-8B-Instruct-GGUF", "name": "Llama 3.1 8B GGUF via llama.cpp", "family": "Llama 3.1", "tag": "local-server", "size": "gguf", "pullable": True},
        {"id": "llamacpp:bartowski/Llama-3.1-70B-Instruct-GGUF", "name": "Llama 3.1 70B GGUF via llama.cpp", "family": "Llama 3.1", "tag": "local-server", "size": "gguf", "pullable": True},
    ],
}

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

def find_lmstudio_cli() -> Optional[str]:
    cli = shutil.which("lms")
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
        ensure_lmstudio_server()
    except HTTPException:
        return _LMSTUDIO_MODELS_CACHE
    try:
        payload = _json_request(
            f"{lmstudio_native_api_base()}/api/v1/models",
            headers={"Authorization": f"Bearer {os.getenv('LMSTUDIO_API_KEY') or 'lmstudio'}"},
            timeout=5,
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

def download_hf_model(repo_id: str, provider: str = "local_mlx") -> Dict[str, object]:
    if importlib.util.find_spec("huggingface_hub") is None:
        raise HTTPException(status_code=400, detail="huggingface_hub가 없습니다. 먼저 MLX runtime 설치를 진행해 주세요.")

    target_dir = hf_model_dir(repo_id)
    if hf_model_ready(repo_id, provider):
        return {"model": repo_id, "path": str(target_dir), "cached": True}

    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        from huggingface_hub import HfApi, hf_hub_download, snapshot_download

        if provider == "llamacpp":
            files = HfApi().list_repo_files(repo_id)
            ggufs = sorted([name for name in files if name.lower().endswith(".gguf")])
            if not ggufs:
                raise RuntimeError("GGUF 파일을 찾지 못했습니다.")
            preference = ("q4_k_m", "q4_0", "q4_k_s", "q3_k_m", "q2_k")
            filename = next(
                (name for pref in preference for name in ggufs if pref in name.lower()),
                ggufs[0],
            )
            hf_hub_download(repo_id=repo_id, filename=filename, local_dir=str(target_dir))
        else:
            snapshot_download(repo_id=repo_id, local_dir=str(target_dir), resume_download=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{repo_id} 다운로드 실패: {str(e)[-2000:]}")

    if not hf_model_ready(repo_id, provider):
        raise HTTPException(status_code=500, detail=f"{repo_id} 다운로드가 완료되지 않았습니다. 모델 파일을 찾지 못했습니다.")

    return {"model": repo_id, "path": str(target_dir), "cached": False}


def get_ollama_pulled_models() -> set:
    if not shutil.which("ollama"):
        return set()
    try:
        result = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=5, check=False)
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
    if not shutil.which("ollama"):
        raise HTTPException(status_code=400, detail="Ollama가 설치되지 않았습니다.")
    try:
        probe = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=3, check=False)
        if probe.returncode == 0:
            return
    except Exception:
        pass
    subprocess.Popen(
        ["ollama", "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            probe = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=3, check=False)
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
        return shutil.which("ollama") is not None
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

    HF_MODELS_ROOT.mkdir(parents=True, exist_ok=True)
    mlx_models = []
    for m in ENGINE_MODEL_CATALOG.get("local_mlx", []):
        repo_id = m["id"]
        mlx_models.append({**m, "pulled": hf_model_ready(repo_id, "local_mlx")})

    vllm_models = []
    for m in ENGINE_MODEL_CATALOG.get("vllm", []):
        repo_id = m["id"].removeprefix("vllm:")
        vllm_models.append({**m, "pulled": hf_model_ready(repo_id, "vllm")})

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

    llamacpp_models = []
    for m in ENGINE_MODEL_CATALOG.get("llamacpp", []):
        repo_id = m["id"].removeprefix("llamacpp:")
        llamacpp_models.append({**m, "pulled": hf_model_ready(repo_id, "llamacpp")})

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
        "cwd": str(Path(__file__).resolve().parent),
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
    if engine == "ollama" and completed.returncode == 0 and shutil.which("ollama"):
        # Skip if already running to avoid orphan daemons.
        already_up = False
        try:
            probe = subprocess.run(["ollama", "list"], capture_output=True, timeout=2, check=False)
            already_up = probe.returncode == 0
        except Exception:
            already_up = False
        if already_up:
            result["daemon_started"] = "already_running"
        else:
            try:
                # Detach so the daemon survives this request but doesn't become our zombie.
                subprocess.Popen(
                    ["ollama", "serve"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                result["daemon_started"] = True
            except Exception as e:
                logging.warning("ollama serve spawn failed: %s", e)
                result["daemon_started"] = False
    return result


def normalize_local_model_request(model_id: str, engine: Optional[str] = None) -> str:
    model_id = model_id.strip()
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
        if parsed_model not in get_ollama_pulled_models():
            completed = subprocess.run(
                ["ollama", "pull", parsed_model],
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
    return {
        "status": "ok",
        "message": msg,
        "model": model_id,
        "current": router.current_model_id,
        "engine": parsed_provider,
        "installed_now": bool(install_result.get("installed_now")),
        "download": download_result,
    }

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
    base = {"status": "ok", "version": "2.1.0", "mode": APP_MODE}
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
    model_ref = req.model.strip()
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
        try:
            completed = subprocess.run(
                ["ollama", "pull", model_name],
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


@app.get("/models")
async def list_models():
    """HuggingFace 추천 모델 목록 및 로드 상태 반환"""
    recommended = [
        # Qwen Series
        {"id": "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit",  "name": "Qwen 2.5 Coder 7B", "tag": "coding",  "size": "4.3GB"},
        {"id": "mlx-community/Qwen2.5-7B-Instruct-4bit",        "name": "Qwen 2.5 7B",       "tag": "general", "size": "4.3GB"},
        
        # Llama Series
        {"id": "mlx-community/Llama-3.2-3B-Instruct-4bit",      "name": "Llama 3.2 3B",      "tag": "light",   "size": "2.0GB"},
        {"id": "mlx-community/Llama-3.1-8B-Instruct-4bit",      "name": "Llama 3.1 8B",      "tag": "general", "size": "4.7GB"},
        
        # Gemma Series
        {"id": "google/gemma-4-E4B",                            "name": "Gemma 4 E4B (Latest)", "tag": "next-gen", "size": "Next-Gen"},
        {"id": "mlx-community/gemma-2-9b-it-4bit",              "name": "Gemma 2 9B",        "tag": "balanced","size": "5.4GB"},
        {"id": "mlx-community/gemma-2-2b-it-4bit",              "name": "Gemma 2 2B",        "tag": "ultra-light", "size": "1.6GB"},

        # Reasoning
        {"id": "mlx-community/DeepSeek-R1-Distill-Qwen-7B-4bit","name": "DeepSeek R1 (7B)",  "tag": "reasoning","size": "4.3GB"},
    ]
    return {
        "recommended": recommended,
        "cloud": router.detected_cloud_models(),
        "engines": await asyncio.to_thread(engine_status),
        "loaded": router.loaded_model_ids,
        "current": router.current_model_id,
    }


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
            answer = f"채팅창을 정리했습니다. 화면에서 제거 {result.get('removed', 0)}개. 감사 로그와 Data Graph/RAG 데이터는 유지됩니다."
        else:
            if req.conversation_id:
                result = clear_conversation(req.conversation_id)
                answer = f"현재 대화방 채팅창을 정리했습니다. 화면에서 제거 {result.get('removed', 0)}개. 감사 로그와 Data Graph/RAG 데이터는 유지됩니다."
            else:
                result = clear_history(0)
                answer = f"채팅창을 정리했습니다. 화면에서 제거 {result.get('removed', 0)}개. 감사 로그와 Data Graph/RAG 데이터는 유지됩니다."
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

    try:
        if ENABLE_GRAPH and KNOWLEDGE_GRAPH:
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

    if env_bool("LATTICEAI_AUTO_READ_CHAT_PATHS", default=False):
        # Off by default: automatic local-file injection can leak files to cloud models.
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


@app.get("/graph")
async def knowledge_graph_page(request: Request):
    """Serve the interactive knowledge graph canvas UI."""
    _require_graph()
    require_user(request)
    return FileResponse(STATIC_DIR / "graph.html")


@app.get("/knowledge-graph")
async def knowledge_graph_legacy_page(request: Request):
    """Backward-compatible route for the graph page."""
    _require_graph()
    require_user(request)
    return FileResponse(STATIC_DIR / "graph.html")


@app.get("/knowledge-graph/stats")
async def knowledge_graph_stats(request: Request):
    _require_graph()
    require_user(request)
    return KNOWLEDGE_GRAPH.stats()


@app.get("/knowledge-graph/graph")
async def knowledge_graph_data(request: Request, limit: int = 300):
    _require_graph()
    require_user(request)
    return KNOWLEDGE_GRAPH.graph(limit)


@app.get("/knowledge-graph/search")
async def knowledge_graph_search(q: str, request: Request, limit: int = 30):
    _require_graph()
    require_user(request)
    if not q or not q.strip():
        return {"query": q, "matches": []}
    return KNOWLEDGE_GRAPH.search(q, limit)


@app.get("/knowledge-graph/context")
async def knowledge_graph_context(q: str, request: Request, limit: int = 6):
    _require_graph()
    require_user(request)
    return {"query": q, "context": KNOWLEDGE_GRAPH.context_for_query(q, limit)}


@app.get("/knowledge-graph/neighbors/{node_id:path}")
async def knowledge_graph_neighbors(node_id: str, request: Request):
    _require_graph()
    require_user(request)
    if not node_id:
        raise HTTPException(status_code=400, detail="node_id required")
    return KNOWLEDGE_GRAPH.neighbors(node_id)


@app.post("/knowledge-graph/ingest")
async def knowledge_graph_ingest(req: KnowledgeGraphIngestRequest, request: Request):
    _require_graph()
    current_user = require_user(request)
    event_type = (req.type or "").strip().lower()
    if event_type not in {"message", "ai_response", "note"}:
        raise HTTPException(status_code=400, detail="지원하는 type: message, ai_response, note")
    role = req.role or ("assistant" if event_type == "ai_response" else "user")
    return KNOWLEDGE_GRAPH.ingest_message(
        role,
        req.content,
        user_email=req.user_email or current_user,
        user_nickname=req.user_nickname,
        source=req.source or "mcp",
        conversation_id=req.conversation_id,
        raw={
            "type": req.type,
            "title": req.title,
            "content": req.content,
            "metadata": req.metadata or {},
        },
    )


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

# ── Agent Role Prompts (Planner / Executor / Critic / Memory Updater) ─────────

_TOOL_CATALOG_BRIEF = """
FILESYSTEM  : list_dir  workspace_tree  read_file  write_file  edit_file  grep  search_files  inspect_html  preview_url
PLANNING    : todo_read  todo_write
PROJECT     : run_command  build_project  deploy_project  create_web_project
GIT (read)  : git_status  git_diff  git_log  git_show
LOCAL FS    : local_list  local_read  local_write  read_document
DOCS        : create_docx  create_xlsx  create_pptx  create_pdf
KNOWLEDGE   : knowledge_save  knowledge_search  knowledge_tree
COMPUTER    : computer_screenshot  computer_open_app  computer_open_url  computer_click  computer_type  computer_key
MISC        : network_status  clear_history  final
"""

PLANNER_PROMPT = """You are the PLANNER role in Lattice AI's multi-role agent harness.
Your ONLY job: analyze the request and produce a structured execution plan.
You do NOT call tools or write code.

Respond with exactly ONE JSON object (no markdown, no fences):
{
  "action": "plan",
  "state": "PLANNING",
  "goal": "one-sentence goal in the user's language",
  "steps": [
    {"id": 1, "description": "what this step does", "action": "expected_tool", "purpose": "why needed"}
  ],
  "requires_approval": true,
  "rollback_strategy": "git",
  "estimated_steps": 3
}

Rules:
- requires_approval = true if ANY step uses write/exec tools (edit_file, write_file, run_command, etc.)
- rollback_strategy = "git" if steps modify existing files; "none" otherwise
- Keep steps realistic: 2-4 for simple tasks, up to 10 for complex ones
- Do NOT specify full tool args — that is the Executor's job

Available tools:""" + _TOOL_CATALOG_BRIEF

EXECUTOR_PROMPT = """You are the EXECUTOR role in Lattice AI's multi-role agent harness.
You have a plan from the Planner. Execute it step by step using exactly one tool per response.

You think and act like a senior software engineer:
- Read (read_file, grep) BEFORE editing — never guess at file contents
- Prefer edit_file over write_file for existing files
- Keep changes small and precise
- Verify after changes with build_project or run_command

Respond with exactly ONE JSON object per step:
{"thoughts": "what you learned / why this next action", "action": "tool_name", "args": {...}}

When the task is fully done AND a tool result in this run confirms it:
{"thoughts": "verified", "action": "final", "message": "한국어로 무엇을 했고 어디서 검증했는지 요약"}

ANTI-PATTERNS (will halt the loop):
- Editing without reading first → read_file + grep BEFORE edit_file
- Repeating the same action+args → check the transcript
- Claiming done without a verification tool result in transcript
- Hallucinating imports or file paths that were never confirmed by a tool result

Available tools:""" + _TOOL_CATALOG_BRIEF

CRITIC_PROMPT = """You are the CRITIC / REVIEWER role in Lattice AI's multi-role agent harness.
Review the execution transcript and determine whether the goal was achieved.

Respond with exactly ONE JSON object:
{
  "action": "verdict",
  "state": "VERIFYING",
  "verdict": "PASS",
  "reason": "why you think it passed or failed (cite specific tool results)",
  "corrections": [],
  "confidence": 0.95,
  "next_state": "DONE"
}

verdict: "PASS" | "FAIL"
next_state:
  "DONE"      — task succeeded; finish
  "EXECUTING" — task failed but corrections can fix it (use corrections field for retry)
  "ROLLBACK"  — task failed AND file changes should be undone

Criteria for PASS: a tool result in the transcript explicitly confirms success.
Be strict. Claiming done without evidence = FAIL."""

MEMORY_UPDATER_PROMPT = """You are the MEMORY UPDATER role in Lattice AI's multi-role agent harness.
After a completed task, extract reusable learnings.

Respond with exactly ONE JSON object:
{
  "action": "memory",
  "state": "DONE",
  "learnings": ["one concise fact about this codebase or task"],
  "artifacts": ["relative/path/to/created_or_modified_file"],
  "save_to_knowledge": false
}

Rules:
- max 5 learnings, one sentence each
- save_to_knowledge = true only if learnings are genuinely useful across future sessions
- artifacts = files the Executor actually created or modified (from transcript)
"""

# Keep backward-compat alias used by any existing callers
AGENT_SYSTEM_PROMPT = EXECUTOR_PROMPT

# Marker: the old monolithic prompt was replaced by 4-role prompts above.
# Legacy variable kept so Telegram bot / VS Code extension still work.

_ORIGINAL_MONOLITHIC_PROMPT_NOTE = """You are Lattice AI Agent — a local, professional-grade coding assistant.
You have full access to a sandboxed workspace and (with user approval) the wider filesystem.
You think and work like a senior software engineer, not like an autocompleter.

================================================================================
HOW A PROFESSIONAL DEVELOPER THINKS — your operating loop
================================================================================
Every multi-step task follows four phases. Skipping phases is the #1 cause of bad
output. Do not skip them.

1) DISCOVER (read first, then act)
   - Map the territory before changing it. Use workspace_tree, list_dir, grep,
     and read_file BEFORE writing or editing anything.
   - When the user names a file/feature/function, locate it (grep) and read the
     surrounding code BEFORE proposing a change.
   - Read package.json, pyproject.toml, requirements.txt, tsconfig.json, and
     other config files before assuming a library/version/tool is available.
   - Never guess at APIs, imports, file paths, function signatures, or types.
     If you don't know, look it up with grep/read_file. Hallucinated code is
     the worst possible output.

2) PLAN (write the plan down)
   - For any task with 3+ distinct steps, call todo_write FIRST with a concrete
     checklist (3–10 items). Keep exactly one item in_progress at a time.
   - The plan should describe WHAT will change and HOW you'll verify it works,
     not vague intentions ("look at code", "fix bugs"). Bad plans produce bad code.
   - Update the todo list (todo_write again) as items complete or new ones emerge.

3) IMPLEMENT (small, precise diffs)
   - Prefer edit_file over write_file when modifying existing files. edit_file
     requires exact byte-level old_string match — read the file first and copy
     the surrounding context verbatim. This forces correctness.
   - Use write_file only for brand-new files or when fully rewriting a file you
     understand end-to-end.
   - Keep diffs as small as the task requires. Don't refactor "while you're
     there." Don't add abstractions for hypothetical future needs.
   - Code quality:
       * No new comments unless the WHY is non-obvious (a subtle invariant, a
         workaround for a specific bug, behavior that would surprise a reader).
         Never write comments that just restate what the code does.
       * No backward-compat shims, no dead code, no unused imports/variables.
       * No defensive try/except around code that can't fail. Trust internal
         contracts; validate only at system boundaries (user input, network).
       * Match the surrounding code's style (indent, quotes, naming).

4) VERIFY (prove it works before claiming done)
   - After code changes, RUN something that confirms correctness:
       * build_project for build/typecheck/test scripts
       * run_command for python/node scripts and tests
       * inspect_html + preview_url for generated UI
   - If verification fails, treat the failure as the new task. Diagnose root
     cause; do not paper over it (no try/except shortcuts, no --no-verify, no
     disabling tests). Re-enter Discover phase if needed.
   - Never claim a task is "complete," "saved," "fixed," "working," or
     "deployed" unless a tool result in this same agent run confirms it.

================================================================================
RESPONSE FORMAT (strict)
================================================================================
Respond with exactly ONE JSON object per step. No markdown, no code fences, no
extra prose. Include a short `thoughts` field that records your current reasoning
(what you just learned, what you'll do next, why). The user does not see it
directly — it exists so you can plan across steps.

  {"thoughts": "Need to read App.tsx before editing the import. Workspace tree
   confirms only one App.tsx exists.",
   "action": "read_file",
   "args": {"path": "src/App.tsx"}}

When the task is fully complete AND verified:
  {"thoughts": "Build passed, file written, ready to summarize.",
   "action": "final",
   "message": "한국어로 간결하게 무엇을 만들었고 어디서 검증했는지 요약."}

If you cannot proceed (missing tool, blocked path, ambiguous user intent), use
`final` and clearly state the blocker and the smallest next step the user can
take to unblock it. Do NOT loop on the same failing action.

================================================================================
TOOL CATALOG
================================================================================
Filesystem (workspace, relative paths):
  list_dir        {"path":"."}
  workspace_tree  {"path":".", "max_depth":3}
  read_file       {"path":"src/App.tsx", "offset":0, "limit":0, "line_numbers":true}
                  — returns numbered view + total_lines. Use offset/limit for big files.
  write_file      {"path":"new_file.py", "content":"..."}   — new files / full rewrites
  edit_file       {"path":"existing.py", "old_string":"exact text", "new_string":"new text",
                   "replace_all":false}
                  — preferred for existing files. old_string MUST appear once
                    (unless replace_all=true). Include enough surrounding context
                    to make it unique.
  grep            {"pattern":"regex", "path":".", "glob":"*.py", "max_results":50,
                   "case_insensitive":false, "context_lines":2}
                  — regex search across the codebase. Use this before assuming a
                    symbol exists.
  search_files    {"query":"substring", "path":".", "max_results":20}   — legacy substring search
  inspect_html    {"path":"index.html"}
  preview_url     {"path":"index.html"}

Planning:
  todo_read       {}
  todo_write      {"todos":[{"id":"1","content":"...","status":"pending"}]}
                  — status ∈ pending|in_progress|completed.
                    Use proactively for any task with 3+ steps.

Project ops:
  run_command     {"command":"python3 app.py", "cwd":"."}
                  — allowed binaries: pwd ls find cat sed head tail wc rg python python3 node npm npx
                  — git is NOT allowed here; use the git_* tools below (read-only).
  build_project   {"cwd":".", "script":"build"}    — also: compile, typecheck, test
  deploy_project  {"cwd":".", "script":"deploy"}   — also: preview, release, package, dist, make, build:pkg, build:exe
  create_web_project {"path":"my_app", "framework":"react", "template":"vite"}

Git (read-only):
  git_status, git_diff, git_log, git_show
  — Never commit/push/pull/fetch/clone/reset/checkout. Lattice agent does not author git history.

Local filesystem (outside workspace; UI prompts user for approval):
  local_list      {"path":"/Users/.../Downloads"}
  local_read      {"path":"/abs/path/file.txt"}
  local_write     {"path":"/abs/path/file.txt", "content":"..."}
  read_document   {"path":"/abs/path/report.pdf"}   — PDF, DOCX, XLSX, PPTX, TXT, MD, CSV

Document generation (written to workspace generated_* folders):
  create_docx     {"title":"...", "body":"...", "filename":"doc.docx"}
  create_xlsx     {"rows":[["A","B"],[1,2]], "filename":"sheet.xlsx", "sheet_name":"Sheet1"}
  create_pptx     {"title":"...", "slides":[{"title":"...","bullets":["..."]}], "filename":"deck.pptx"}
  create_pdf      {"title":"...", "body":"...", "filename":"doc.pdf"}

Knowledge / memory (Obsidian-compatible Markdown vault):
  knowledge_save  {"folder":"30_Projects", "title":"...", "content":"..."}
  knowledge_search {"query":"...", "max_results":5}
  knowledge_tree  {}
  obsidian_save / obsidian_search / obsidian_tree  — same as knowledge_*, with vault URIs

Computer use (macOS desktop control, requires Accessibility permission):
  computer_screenshot, computer_open_app, computer_open_url, computer_click,
  computer_type, computer_key, computer_scroll, computer_move, computer_drag,
  computer_status, chrome_status, computer_use_status
  — Use screenshot to ground state; click/type to interact. Verify with another screenshot.

Misc:
  network_status  {}
  clear_history   {"keep_last":0}
  final           {"message":"..."}

================================================================================
DOMAIN RULES (keep in mind)
================================================================================
- Frontend: don't assume Tailwind/framer-motion/TypeScript exist. Read
  package.json first. If a dependency is missing, either add it explicitly to
  package.json (and create the config files it needs) or pick a simpler stack
  that already works.
- Installers (.pkg/.exe): set up the packaging config (e.g. electron-builder)
  with full scripts in package.json, then run deploy_project. If the current
  OS/toolchain can't produce the artifact, still generate complete config and
  state the exact missing prerequisite — do not say "I can't."
- Data analysis: read the data files (read_document/local_read), compute with
  run_command, report concrete findings plus output artifact paths.
- Document requests (docx/xlsx/pptx/pdf, 문서/엑셀/PPT/피피티/파워포인트): call
  the matching create_* action immediately with rich, complete content. Never
  say you cannot create files.
- Korean/English: answer in the language the user used; default to Korean
  if mixed or ambiguous.

================================================================================
ANTI-PATTERNS (will be flagged by the orchestrator)
================================================================================
- Editing without reading first → use read_file + grep before edit_file.
- Repeating the same action with the same args → the loop will halt you.
- Claiming "done" without a verification tool result in the transcript.
- Adding new dependencies without updating package.json / requirements.txt.
- Producing fragments when the user asked for a complete file or runnable app.
- Stuffing speculative features beyond the user's actual request.
- Decorative placeholder URLs / fake data when real data is available.
"""


_FILE_CREATE_ACTIONS = {"create_docx", "create_xlsx", "create_pptx", "create_pdf", "write_file", "edit_file", "create_web_project"}

# Harness risk level per tool action.
# low    — read-only, no side effects
# medium — write/create files or knowledge entries
# high   — execute commands, control computer, write to arbitrary FS paths
class ToolPolicy(TypedDict):
    risk: str         # "read" | "write" | "exec" | "destructive"
    destructive: bool # True = data loss possible, no auto-undo
    shell: bool       # True = spawns a subprocess
    network: bool     # True = makes external network calls
    auto_approve: bool# True = agent may call without human confirmation
    sandbox: str      # "workspace" | "home" | "system"
    rollback: str     # "none" | "backup" | "git"


_R = lambda s, sb="workspace", ro="none": ToolPolicy(risk="read",        destructive=False, shell=False, network=False, auto_approve=True,  sandbox=sb, rollback=ro)
_RS = lambda s, sb="workspace", ro="none": ToolPolicy(risk="read",       destructive=False, shell=True,  network=False, auto_approve=True,  sandbox=sb, rollback=ro)
_RN = lambda s, sb="system",    ro="none": ToolPolicy(risk="read",       destructive=False, shell=True,  network=True,  auto_approve=True,  sandbox=sb, rollback=ro)
_W = lambda s, sb="workspace", ro="none": ToolPolicy(risk="write",       destructive=False, shell=False, network=False, auto_approve=False, sandbox=sb, rollback=ro)
_E = lambda s, sb="workspace", ro="none": ToolPolicy(risk="exec",        destructive=False, shell=True,  network=False, auto_approve=False, sandbox=sb, rollback=ro)
_EN = lambda s, sb="workspace", ro="none": ToolPolicy(risk="exec",       destructive=False, shell=True,  network=True,  auto_approve=False, sandbox=sb, rollback=ro)
_EC = lambda s, sb="system",   ro="none": ToolPolicy(risk="exec",        destructive=False, shell=False, network=False, auto_approve=False, sandbox=sb, rollback=ro)
_D = lambda s, sb="workspace", ro="none": ToolPolicy(risk="destructive", destructive=True,  shell=True,  network=False, auto_approve=False, sandbox=sb, rollback=ro)

TOOL_GOVERNANCE: Dict[str, ToolPolicy] = {
    # ── read-only / workspace ──────────────────────────────────────────────────
    "list_dir":           _R("list_dir"),
    "workspace_tree":     _R("workspace_tree"),
    "read_file":          _R("read_file"),
    "search_files":       _R("search_files"),
    "grep":               _R("grep"),
    "inspect_html":       _R("inspect_html"),
    "todo_read":          _R("todo_read"),
    # ── read-only / home FS ───────────────────────────────────────────────────
    "local_list":         _R("local_list",  sb="home"),
    "local_read":         _R("local_read",  sb="home"),
    # ── read-only / git (spawns subprocess, read-only) ───────────────────────
    "git_status":         _RS("git_status"),
    "git_diff":           _RS("git_diff"),
    "git_log":            _RS("git_log"),
    "git_show":           _RS("git_show"),
    # ── read-only / knowledge ─────────────────────────────────────────────────
    "knowledge_search":   _R("knowledge_search", sb="home"),
    "knowledge_tree":     _R("knowledge_tree",   sb="home"),
    "obsidian_search":    _R("obsidian_search",  sb="home"),
    "obsidian_tree":      _R("obsidian_tree",    sb="home"),
    # ── read-only / system ────────────────────────────────────────────────────
    "computer_screenshot":_R("computer_screenshot", sb="system"),
    "computer_status":    _R("computer_status",     sb="system"),
    "chrome_status":      _R("chrome_status",       sb="system"),
    "computer_use_status":_R("computer_use_status", sb="system"),
    "network_status":     _RN("network_status"),
    # ── write / workspace ─────────────────────────────────────────────────────
    "write_file":         _W("write_file",       ro="git"),
    "edit_file":          _W("edit_file",        ro="git"),
    "create_web_project": _W("create_web_project"),
    "create_docx":        _W("create_docx"),
    "create_xlsx":        _W("create_xlsx"),
    "create_pptx":        _W("create_pptx"),
    "create_pdf":         _W("create_pdf"),
    "preview_url":        _W("preview_url"),
    "todo_write":         _W("todo_write"),
    # ── write / home FS ───────────────────────────────────────────────────────
    "knowledge_save":     _W("knowledge_save",  sb="home"),
    "obsidian_save":      _W("obsidian_save",   sb="home"),
    "local_write":        _W("local_write",     sb="home"),
    # ── exec / workspace ──────────────────────────────────────────────────────
    "run_command":        _E("run_command"),
    "build_project":      _E("build_project"),
    # ── exec / network ────────────────────────────────────────────────────────
    "deploy_project":     _EN("deploy_project"),
    # ── exec / computer use (system-level input injection) ───────────────────
    "computer_click":     _EC("computer_click"),
    "computer_type":      _EC("computer_type"),
    "computer_key":       _EC("computer_key"),
    "computer_scroll":    _EC("computer_scroll"),
    "computer_drag":      _EC("computer_drag"),
    "computer_move":      _EC("computer_move"),
    "computer_open_app":  _EC("computer_open_app"),
    "computer_open_url":  ToolPolicy(risk="exec", destructive=False, shell=False, network=True,  auto_approve=False, sandbox="system",    rollback="none"),
}

_TOOL_GOVERNANCE_DEFAULT = ToolPolicy(
    risk="write", destructive=False, shell=False, network=False,
    auto_approve=False, sandbox="workspace", rollback="none",
)

# Tools that require admin role — computer control + shell execution
ADMIN_ONLY_TOOLS: frozenset[str] = frozenset(
    name for name, policy in TOOL_GOVERNANCE.items()
    if policy["sandbox"] == "system" or policy["risk"] in {"exec", "destructive"}
)

def _check_tool_role(tool_name: str, current_user: str) -> None:
    if tool_name not in ADMIN_ONLY_TOOLS:
        return
    users = load_users()
    if get_user_role(current_user, users) != "admin":
        raise HTTPException(
            status_code=403,
            detail=f"'{tool_name}' 툴은 관리자 전용입니다.",
        )

# Paths that local_write / local_list must never target
_LOCAL_WRITE_BLOCKED_PREFIXES = (
    "/etc/", "/usr/", "/bin/", "/sbin/", "/System/", "/private/etc/",
    "/Library/LaunchDaemons/", "/Library/LaunchAgents/",
)

# Backward-compat: map policy risk → legacy low/medium/high string
_RISK_LEVEL_MAP = {"read": "low", "write": "medium", "exec": "high", "destructive": "high"}


def _agent_policy(action_name: str, args: dict) -> ToolPolicy:
    """Return the full governance policy for an action.

    Upgrades local_write to destructive risk when targeting system paths.
    """
    policy = TOOL_GOVERNANCE.get(action_name, _TOOL_GOVERNANCE_DEFAULT)
    if action_name == "local_write":
        path = str(args.get("path", ""))
        if any(path.startswith(p) for p in _LOCAL_WRITE_BLOCKED_PREFIXES):
            policy = ToolPolicy(
                risk="destructive", destructive=True, shell=False, network=False,
                auto_approve=False, sandbox="system", rollback="none",
            )
    return policy


def _agent_risk(action_name: str, args: dict) -> str:
    """Return legacy low/medium/high risk string (kept for transcript backward-compat)."""
    return _RISK_LEVEL_MAP.get(_agent_policy(action_name, args)["risk"], "medium")


# ── Tool Permission Layer ─────────────────────────────────────────────────────
# A compact, public-facing view of each tool's authorization profile, derived
# from TOOL_GOVERNANCE. Designed for client UIs / approval dialogs that don't
# need the full 7-dimensional governance object.
#
# Example:
#   { "tool": "shell", "risk": "high", "requires_approval": true, "network": false }

class ToolPermission(TypedDict):
    tool: str
    risk: str                 # "low" | "medium" | "high"
    requires_approval: bool   # inverse of governance.auto_approve
    network: bool             # tool makes external network calls


def get_tool_permission(name: str, args: Optional[dict] = None) -> ToolPermission:
    """Return the simplified permission view for a tool name.

    `args` lets path-sensitive tools (e.g. local_write to /etc) escalate risk;
    omit it for static catalog views.
    """
    policy = _agent_policy(name, args or {})
    return ToolPermission(
        tool=name,
        risk=_RISK_LEVEL_MAP.get(policy["risk"], "medium"),
        requires_approval=not policy["auto_approve"],
        network=policy["network"],
    )


def list_tool_permissions() -> list:
    """Return permission views for every governed tool, sorted by tool name."""
    return [get_tool_permission(name) for name in sorted(TOOL_GOVERNANCE.keys())]


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


def _extract_agent_action(raw: str) -> Dict:
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    elif not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]

    try:
        action = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Agent did not return valid JSON: {exc}") from exc

    if not isinstance(action, dict) or "action" not in action:
        raise ValueError("Agent JSON must include an action field.")
    return action


# ── Agent State Machine — Phase Functions ─────────────────────────────────────

async def _phase_plan(
    ctx: AgentRunContext, req: AgentRequest, router, lang_hint: str, current_user: str,
    model_id: str | None = None,
) -> None:
    """PLAN: Planner role produces a structured plan JSON."""
    context = (
        f"{PLANNER_PROMPT}\n\n"
        f"[LANGUAGE HINT: {lang_hint}]\n"
        f"Workspace root: {AGENT_ROOT}\n\n"
        f"User request: {req.message}"
    )
    raw = await router.generate_as(
        model_id,
        message="Produce a JSON execution plan for this request.",
        context=context, max_tokens=1024, temperature=0.1,
    )
    try:
        plan = _extract_agent_action(str(raw))
    except ValueError:
        plan = {
            "action": "plan", "state": "PLAN",
            "goal": req.message, "steps": [],
            "requires_approval": False, "rollback_strategy": "none", "estimated_steps": 1,
        }
    ctx.plan = plan
    ctx.transcript.append({
        "state": AgentState.PLANNING.value,
        "goal": plan.get("goal", req.message),
        "steps": plan.get("steps", []),
        "requires_approval": plan.get("requires_approval", False),
        "rollback_strategy": plan.get("rollback_strategy", "none"),
        "estimated_steps": plan.get("estimated_steps", 1),
    })
    ctx.state = AgentState.WAITING_APPROVAL


def _phase_approval(ctx: AgentRunContext, current_user: str) -> None:
    """APPROVAL: Check governance, log decision, auto-approve (future: UI prompt)."""
    auto_approve_tools = {name for name, p in TOOL_GOVERNANCE.items() if p["auto_approve"]}
    steps = ctx.plan.get("steps", [])
    non_auto = [s.get("action") for s in steps if s.get("action") not in auto_approve_tools]
    requires = ctx.plan.get("requires_approval", False) or bool(non_auto)

    ctx.transcript.append({
        "state": AgentState.WAITING_APPROVAL.value,
        "requires_approval": requires,
        "non_auto_approve_steps": non_auto,
        "decision": "auto_approved",
    })
    append_audit_event(
        "agent_approval", user_email=current_user,
        requires_approval=requires, non_auto_steps=non_auto, decision="auto_approved",
    )
    ctx.state = AgentState.EXECUTING


async def _phase_execute(
    ctx: AgentRunContext, req: AgentRequest, router, lang_hint: str,
    current_user: str, max_steps: int, model_id: str | None = None,
) -> None:
    """EXECUTE: Executor role calls tools one at a time until final or budget exhausted."""
    exec_count = sum(1 for s in ctx.transcript if s.get("state") == AgentState.EXECUTING.value)
    budget = max(1, max_steps - exec_count)

    for _ in range(budget):
        corrections_hint = (
            "\n\nCritic corrections from previous attempt:\n"
            + "\n".join(f"- {c}" for c in ctx.corrections)
        ) if ctx.corrections else ""

        context = (
            f"{EXECUTOR_PROMPT}\n\n"
            f"[LANGUAGE HINT: {lang_hint}]\n"
            f"Workspace root: {AGENT_ROOT}\n\n"
            f"PLAN:\n{json.dumps(ctx.plan, ensure_ascii=False)}\n\n"
            f"Recent conversation:\n{build_recent_chat_context(conversation_id=req.conversation_id) or '(none)'}\n\n"
            f"User request: {req.message}{corrections_hint}\n\n"
            f"Execution transcript:\n{json.dumps(ctx.transcript, ensure_ascii=False, indent=2)}"
        )
        raw = await router.generate_as(
            model_id,
            message="Execute the next step.",
            context=context, max_tokens=4096, temperature=req.temperature,
        )
        try:
            action = _extract_agent_action(str(raw))
        except ValueError as exc:
            ctx.transcript.append({
                "state": AgentState.EXECUTING.value, "action": "parse_error",
                "raw": str(raw)[:400], "error": str(exc),
            })
            break

        name     = action.get("action")
        thoughts = str(action.get("thoughts") or "")[:600]
        args     = action.get("args") or {}

        if name == "final":
            ctx.final_message = action.get("message", "작업을 완료했습니다.")
            ctx.transcript.append({
                "state": AgentState.EXECUTING.value, "action": "final", "thoughts": thoughts,
            })
            ctx.state = AgentState.VERIFYING
            return

        # Loop guard
        exec_steps = [s for s in ctx.transcript if s.get("state") == AgentState.EXECUTING.value]
        last = exec_steps[-1] if exec_steps else None
        if (
            name in _FILE_CREATE_ACTIONS and last
            and last.get("action") == name
            and (last.get("args") or {}) == args
            and "result" in last
        ):
            ctx.transcript.append({
                "state": AgentState.EXECUTING.value, "action": name,
                "error": "LOOP_DETECTED: identical action+args repeated — halted.",
            })
            break

        if name == "clear_history":
            result = clear_history(args.get("keep_last", 0))
            ctx.transcript.append({
                "state": AgentState.EXECUTING.value, "action": name,
                "thoughts": thoughts, "args": args, "result": result,
            })
            continue

        policy = _agent_policy(name, args)
        risk   = _RISK_LEVEL_MAP.get(policy["risk"], "medium")

        if policy["risk"] == "destructive":
            ctx.transcript.append({
                "state": AgentState.EXECUTING.value, "action": name,
                "thoughts": thoughts, "args": args, "risk": risk,
                "governance": dict(policy),
                "error": f"BLOCKED: destructive action '{name}' not permitted in agent mode.",
            })
            append_audit_event(
                "agent_blocked", user_email=current_user, source=req.source or "agent",
                action=name, reason="destructive", governance=dict(policy),
            )
            continue

        if not policy["auto_approve"]:
            append_audit_event(
                "agent_exec", user_email=current_user, source=req.source or "agent",
                state=AgentState.EXECUTING.value, action=name, risk=risk,
                shell=policy["shell"], network=policy["network"],
                destructive=policy["destructive"], sandbox=policy["sandbox"],
                rollback=policy["rollback"],
                args={k: v for k, v in args.items() if k != "content"},
            )

        try:
            _check_tool_role(name, current_user)
            result = execute_tool(name, args)
            ctx.transcript.append({
                "state": AgentState.EXECUTING.value, "action": name,
                "thoughts": thoughts, "args": args,
                "risk": risk, "governance": dict(policy), "result": result,
            })
        except (ToolError, KeyError, TypeError) as exc:
            ctx.transcript.append({
                "state": AgentState.EXECUTING.value, "action": name,
                "thoughts": thoughts, "args": args,
                "risk": risk, "governance": dict(policy), "error": str(exc),
            })

    ctx.state = AgentState.VERIFYING


async def _phase_verify(
    ctx: AgentRunContext, req: AgentRequest, router, lang_hint: str, current_user: str,
    max_retry: int = 3, model_id: str | None = None,
) -> None:
    """VERIFYING: Critic role evaluates transcript → DONE / EXECUTING (retry) / ROLLBACK / FAILED."""
    context = (
        f"{CRITIC_PROMPT}\n\n"
        f"[LANGUAGE HINT: {lang_hint}]\n\n"
        f"Original request: {req.message}\n"
        f"Plan goal: {ctx.plan.get('goal', req.message)}\n\n"
        f"Full transcript:\n{json.dumps(ctx.transcript, ensure_ascii=False, indent=2)}"
    )
    raw = await router.generate_as(
        model_id,
        message="Review the execution transcript and return your verdict JSON.",
        context=context, max_tokens=512, temperature=0.1,
    )
    try:
        verdict = _extract_agent_action(str(raw))
    except ValueError:
        verdict = {"action": "verdict", "verdict": "PASS", "next_state": "DONE",
                   "reason": "Critic parse failed — assuming pass.", "corrections": [], "confidence": 0.7}

    ctx.corrections = verdict.get("corrections", [])
    # Normalize legacy verdict next_state strings to current AgentState names
    raw_next = verdict.get("next_state", "DONE")
    next_s = {"COMPLETE": "DONE", "RETRY": "EXECUTING"}.get(raw_next, raw_next)

    ctx.transcript.append({
        "state": AgentState.VERIFYING.value,
        "verdict":     verdict.get("verdict", "PASS"),
        "reason":      verdict.get("reason", ""),
        "corrections": ctx.corrections,
        "confidence":  verdict.get("confidence", 0.9),
        "next_state":  next_s,
    })

    if verdict.get("verdict") == "PASS" or next_s == "DONE":
        if not ctx.final_message:
            ctx.final_message = verdict.get("reason", "작업이 완료되었습니다.")
        ctx.state = AgentState.DONE
    elif next_s == "ROLLBACK":
        ctx.state = AgentState.ROLLBACK
    elif next_s == "EXECUTING":
        if ctx.retry_count >= max_retry:
            ctx.final_message = (
                f"최대 재시도({max_retry}회) 초과로 작업을 종료했습니다. "
                f"마지막 비판: {verdict.get('reason', '(없음)')}"
            )
            ctx.state = AgentState.FAILED
        else:
            ctx.retry_count += 1
            ctx.transcript.append({
                "state": AgentState.EXECUTING.value,
                "retry_attempt": ctx.retry_count,
                "corrections": ctx.corrections,
            })
            ctx.state = AgentState.EXECUTING
    else:
        ctx.final_message = verdict.get("reason", "검증자가 인식되지 않은 다음 상태를 반환했습니다.")
        ctx.state = AgentState.FAILED


def _phase_rollback(ctx: AgentRunContext, current_user: str) -> None:
    """ROLLBACK: attempt git checkout for each edited file, then COMPLETE."""
    import subprocess as _sp
    rolled: list = []
    for step in ctx.transcript:
        if step.get("state") != AgentState.EXECUTING.value:
            continue
        gov = step.get("governance", {})
        if gov.get("rollback") != "git":
            continue
        result = step.get("result", {})
        if not (isinstance(result, dict) and result.get("success")):
            continue
        path = result.get("path") or (step.get("args") or {}).get("path", "")
        if not path:
            continue
        try:
            r = _sp.run(
                ["git", "checkout", "--", path], cwd=str(AGENT_ROOT),
                capture_output=True, text=True, timeout=10,
            )
            rolled.append({"path": path, "ok": r.returncode == 0, "stderr": r.stderr[:200]})
        except Exception as exc:
            rolled.append({"path": path, "ok": False, "error": str(exc)})

    ctx.transcript.append({"state": AgentState.ROLLBACK.value, "rolled_back": rolled})
    recovered = [r["path"] for r in rolled if r.get("ok")]
    ctx.final_message = (
        f"실행 실패로 롤백했습니다. 복구 파일: {recovered}"
        if recovered
        else "롤백을 시도했으나 복구할 파일이 없거나 git이 초기화되지 않았습니다."
    )
    append_audit_event("agent_rollback", user_email=current_user, rolled_back=rolled)
    # Rollback is a recovery from a failed verification — terminal state is FAILED
    ctx.state = AgentState.FAILED


async def _phase_memory_update(
    ctx: AgentRunContext, req: AgentRequest, router, current_user: str,
) -> None:
    """Background: Memory Updater role extracts learnings after COMPLETE."""
    context = (
        f"{MEMORY_UPDATER_PROMPT}\n\n"
        f"Completed task: {req.message}\n\n"
        f"Last 5 transcript steps:\n{json.dumps(ctx.transcript[-5:], ensure_ascii=False)}"
    )
    try:
        raw = await router.generate(
            message="Extract learnings from this completed task.",
            context=context, max_tokens=256, temperature=0.1,
        )
        mem = _extract_agent_action(str(raw))
        if mem.get("save_to_knowledge") and mem.get("learnings"):
            from tools import knowledge_save
            knowledge_save(
                "\n".join(mem["learnings"]),
                folder="30_Projects",
                title=f"Agent: {req.message[:60]}",
            )
    except Exception:
        pass


# ── Eval harness ──────────────────────────────────────────────────────────────

@app.post("/agent/eval")
async def agent_eval(req: AgentEvalRequest, request: Request):
    """Run a skill's eval cases from schema.json and return pass/fail per case."""
    require_user(request)
    skill_dir = Path(__file__).resolve().parent / "skills" / req.skill
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
    await _phase_plan(ctx, req, router, lang_hint, current_user, model_id=req.planning_model)

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
    _phase_approval(ctx, current_user)
    return await _agent_run_to_completion(ctx, req, router, lang_hint, current_user, max_steps, max_retry)


async def _agent_run_to_completion(
    ctx: AgentRunContext, req: AgentRequest, router, lang_hint: str,
    current_user: str, max_steps: int, max_retry: int,
) -> dict:
    """Run EXECUTING → VERIFYING loop until terminal state."""
    while ctx.state not in AGENT_TERMINAL_STATES:
        ctx.state_history.append(ctx.state.value)
        if len(ctx.state_history) > 200:
            ctx.final_message = "에이전트 상태 머신이 최대 반복(200)에 도달해 중단했습니다."
            ctx.state = AgentState.FAILED
            break

        if ctx.state == AgentState.EXECUTING:
            await _phase_execute(ctx, req, router, lang_hint, current_user, max_steps,
                                 model_id=ctx.executing_model)

        elif ctx.state == AgentState.VERIFYING:
            await _phase_verify(ctx, req, router, lang_hint, current_user, max_retry,
                                model_id=ctx.reviewing_model)

        elif ctx.state == AgentState.ROLLBACK:
            _phase_rollback(ctx, current_user)

        else:
            ctx.state = AgentState.FAILED

    ctx.state_history.append(ctx.state.value)
    asyncio.create_task(_phase_memory_update(ctx, req, router, current_user))

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

    _phase_approval(ctx, current_user)

    max_steps = max(1, min(orig_req.max_steps, 50))
    max_retry = 3
    return await _agent_run_to_completion(ctx, orig_req, router, lang_hint, current_user, max_steps, max_retry)


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
    """Render PDF pages as base64 PNG images using PyMuPDF."""
    current_user = require_user(request)
    _require_local_approval(token=approval_token, path=path, action="read", user_email=current_user)
    target = Path(path).expanduser().resolve()
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    import fitz  # PyMuPDF
    doc = None
    try:
        doc = fitz.open(str(target))
        total = len(doc)
        pages = []
        for i, page in enumerate(doc):
            if i >= 20:  # 최대 20페이지
                break
            mat = fitz.Matrix(1.5, 1.5)
            pix = page.get_pixmap(matrix=mat)
            b64 = base64.b64encode(pix.tobytes("png")).decode()
            pages.append({"page": i + 1, "b64": b64})
        return {"total": total, "pages": pages}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF 렌더링 실패: {e}")
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception as e:
                logging.warning("fitz doc close failed: %s", e)


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

# Discord webhook URL for permission notifications (optional)
DISCORD_PERMISSION_WEBHOOK_URL = env_value("LATTICEAI_DISCORD_PERMISSION_WEBHOOK", "")


def _normalize_local_path_for_approval(path: str) -> str:
    return str(Path(path).expanduser().resolve())


def _content_fingerprint(content: str = "") -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _notify_discord_permission_sync(token: str, path: str, action: str, user_email: str) -> None:
    """Fire-and-forget Discord webhook notification for permission requests."""
    if not DISCORD_PERMISSION_WEBHOOK_URL:
        return
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
    # Notify Discord in background thread so the HTTP response isn't delayed
    if DISCORD_PERMISSION_WEBHOOK_URL:
        threading.Thread(
            target=_notify_discord_permission_sync,
            args=(token, path, action, user_email),
            daemon=True,
        ).start()
    action_label = _PERMISSION_ACTION_LABELS.get(action, action)
    return {
        "permission_required": True,
        "path": path,
        "action": action,
        "action_label": action_label,
        "approval_token": token,
        "expires_in": _LOCAL_APPROVAL_TTL_SECONDS,
        "message": f"AI가 '{path}' 에 대한 {action_label} 권한을 요청합니다.",
        "discord_notified": bool(DISCORD_PERMISSION_WEBHOOK_URL),
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


@app.post("/permissions/approve/{token}")
async def permissions_approve(token: str, request: Request):
    """Approve a pending permission request. Admin only.
    Called by Discord (via Claude Code) or web UI after user confirmation."""
    require_admin(request)
    with _local_approval_lock:
        record = _local_approvals.get(token)
        if not record:
            raise HTTPException(status_code=404, detail="토큰이 없거나 만료되었습니다.")
        if float(record.get("expires_at", 0)) < time.time():
            _local_approvals.pop(token, None)
            raise HTTPException(status_code=410, detail="토큰이 만료되었습니다.")
        record["approved"] = True
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
    """Deny/revoke a pending permission request. Admin only."""
    require_admin(request)
    with _local_approval_lock:
        record = _local_approvals.pop(token, None)
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
    current_user = require_user(request)
    if not req.approved:
        return _local_permission_response(req.path, "list", current_user)
    _require_local_approval(token=req.approval_token, path=req.path, action="list", user_email=current_user)
    return _tool_response(local_list, req.path)


@app.post("/local/read")
async def local_read_endpoint(req: LocalAccessRequest, request: Request):
    current_user = require_user(request)
    if not req.approved:
        return _local_permission_response(req.path, "read", current_user)
    _require_local_approval(token=req.approval_token, path=req.path, action="read", user_email=current_user)
    return _tool_response(local_read, req.path)


@app.get("/local/serve")
async def local_serve_file(path: str, request: Request, approval_token: Optional[str] = None):
    """Serve a local file (images etc.) directly for browser preview."""
    current_user = require_user(request)
    _require_local_approval(token=approval_token, path=path, action="read", user_email=current_user)
    target = Path(path).expanduser().resolve()
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(target))


@app.post("/local/write")
async def local_write_endpoint(req: LocalWriteRequest, request: Request):
    current_user = require_user(request)
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


@app.get("/tools/chrome_status")
async def tools_chrome_status(request: Request):
    require_user(request)
    return _tool_response(desktop_bridge_status)


@app.get("/tools/computer_use_status")
async def tools_computer_use_status(request: Request):
    require_user(request)
    return _tool_response(computer_status)


# ── Computer Use API ──────────────────────────────────────────────────────────

CU_SYSTEM_PROMPT = """You are Lattice AI Computer Use Agent. You control the Mac desktop using tools.
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
    """SSE streaming Computer Use agent loop."""
    require_admin(request)
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


_MCP_TOOL_DESCRIPTIONS: Dict[str, str] = {
    "list_dir":              "List files in the agent workspace.",
    "workspace_tree":        "Return a recursive workspace tree.",
    "read_file":             "Read a UTF-8 file from the workspace with optional line numbers and offset/limit slicing.",
    "write_file":            "Write a UTF-8 file inside the workspace (new files / full rewrites).",
    "edit_file":             "Precise diff-style edit: replace exact old_string with new_string. Requires unique match unless replace_all=true.",
    "search_files":          "Substring search in text files (legacy).",
    "grep":                  "Regex search across the workspace with line numbers and optional context.",
    "todo_read":             "Read the agent's persistent TODO list for the current workspace.",
    "todo_write":            "Replace the agent's TODO list (id, content, status: pending/in_progress/completed).",
    "clear_history":         "Clear chat history to reduce context and speed up responses.",
    "inspect_html":          "Inspect local HTML structure and assets.",
    "preview_url":           "Return a server URL for a workspace file.",
    "create_docx":           "Create a Word DOCX document in the agent workspace.",
    "create_xlsx":           "Create an XLSX spreadsheet in the agent workspace.",
    "create_pptx":           "Create a PPTX presentation deck in the agent workspace.",
    "create_pdf":            "Create a PDF document in the agent workspace.",
    "local_list":            "List any local folder (requires user permission via UI).",
    "local_read":            "Read any local file (requires user permission via UI).",
    "local_write":           "Write any local file (requires user permission via UI).",
    "read_document":         "Extract text from PDF, DOCX, XLSX, PPTX, TXT, MD, CSV files.",
    "computer_screenshot":   "Capture the current Mac screen as base64 PNG.",
    "computer_open_app":     "Open or focus a Mac app, e.g. Google Chrome.",
    "computer_open_url":     "Open a URL in a Mac app, e.g. Google Chrome.",
    "computer_click":        "Click at screen coordinates (x, y).",
    "computer_type":         "Type text at the current focus position.",
    "computer_key":          "Press a keyboard key or shortcut (e.g. 'command+c').",
    "computer_scroll":       "Scroll at screen coordinates.",
    "computer_move":         "Move the mouse to screen coordinates.",
    "computer_drag":         "Drag from (x1,y1) to (x2,y2).",
    "computer_status":       "Check if Mac Computer Use (pyautogui) is available.",
    "chrome_status":         "Report Chrome desktop bridge availability.",
    "computer_use_status":   "Report Mac Computer Use bridge availability.",
    "knowledge_save":        "Save a note into the local knowledge garden.",
    "knowledge_search":      "Search the local knowledge garden.",
    "knowledge_tree":        "List local knowledge garden markdown files.",
    "knowledge_graph_ingest":"Ingest a message, AI answer, or connector event into the SQLite knowledge graph.",
    "knowledge_graph_search":"Search graph nodes, summaries, and JSON metadata.",
    "knowledge_graph_graph": "Return Obsidian-style graph nodes and edges.",
    "knowledge_graph_context":"Return compact graph-backed RAG context for a prompt.",
    "obsidian_save":         "Save a note into the Obsidian-compatible memory vault.",
    "obsidian_search":       "Search the Obsidian-compatible memory vault.",
    "obsidian_tree":         "List Obsidian memory vault markdown files.",
    "git_status":            "Read-only local git status inside the workspace.",
    "git_diff":              "Read-only local git diff inside the workspace.",
    "git_log":               "Read-only local git log inside the workspace.",
    "git_show":              "Read-only local git show --stat inside the workspace.",
    "network_status":        "Get current local/private IP, public IP, hostname, and Wi-Fi info.",
    "run_command":           "Run an allowlisted local command inside the workspace.",
    "build_project":         "Run an allowlisted package.json build/compile/typecheck/test script to verify changes actually work.",
    "deploy_project":        "Run an allowlisted package.json deploy/preview/release/package installer script (pkg/exe).",
}


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
    for name, description in _MCP_TOOL_DESCRIPTIONS.items():
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
    require_user(request)
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
    global _REMOTE_REGISTRY_FETCHED_AT
    _REMOTE_REGISTRY_FETCHED_AT = None
    registry = await _get_combined_registry()
    return {"status": "ok", "total": len(registry), "remote": len(_REMOTE_REGISTRY_CACHE)}


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
    """skill을 로컬 skills 디렉터리에 설치 (Apache-2.0 / MIT)"""
    require_user(request)
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
    global _SKILLS_MARKETPLACE_FETCHED_AT
    _SKILLS_MARKETPLACE_FETCHED_AT = None
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
    global _PLUGIN_DIRECTORY_FETCHED_AT
    _PLUGIN_DIRECTORY_FETCHED_AT = None
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

@app.get("/setup/scan")
async def setup_scan(request: Request):
    """환경 감지 및 맞춤 추천 반환."""
    require_user(request)
    env  = scan_environment()
    recs = get_recommendations(env)
    return {"environment": env, "recommendations": recs}

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

if __name__ == "__main__":
    print(f"🧠 Lattice AI Server starting in {APP_MODE} mode on http://{DEFAULT_HOST}:{DEFAULT_PORT}")
    uvicorn.run(app, host=DEFAULT_HOST, port=DEFAULT_PORT, log_level="info")
