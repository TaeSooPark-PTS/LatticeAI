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
import re
import secrets
import threading
import shutil
import subprocess
import sys
import tempfile
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
from typing import AsyncIterator, Optional, List, Dict

import uvicorn
from fastapi import FastAPI, File, HTTPException, Request, Cookie, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from PIL import Image

from llm_router import AsyncOpenAI, LLMRouter, OPENAI_COMPATIBLE_PROVIDERS, parse_model_ref, mx, normalize_branding
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

from datetime import datetime

def detect_language(text: str) -> str:
    """Detect language: 'ko' (Korean), 'zh' (Chinese), or 'en' (English)."""
    total = max(len(text), 1)
    ko = sum(1 for c in text if '가' <= c <= '힣')
    zh = sum(1 for c in text if '一' <= c <= '鿿')
    if ko / total > 0.05:
        return "ko"
    if zh / total > 0.05:
        return "zh"
    return "en"

_LANG_HINT = {
    "ko": "Respond in Korean (한국어로 답변하세요).",
    "zh": "Respond in Chinese (用中文回答).",
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
ENABLE_TELEGRAM = env_bool("LATTICEAI_ENABLE_TELEGRAM", default=not IS_PUBLIC_MODE)
ENABLE_GRAPH    = env_bool("LATTICEAI_ENABLE_GRAPH",    default=True)
AUTOLOAD_MODELS = env_bool("LATTICEAI_AUTOLOAD_MODELS", default=IS_PUBLIC_MODE)
MODEL_IDLE_UNLOAD_SECONDS = int(env_value("LATTICEAI_MODEL_IDLE_UNLOAD_SECONDS", "0"))
ALLOW_LOCAL_MODELS = env_bool("LATTICEAI_ALLOW_LOCAL_MODELS", default=not IS_PUBLIC_MODE)
REQUIRE_AUTH = env_bool("LATTICEAI_REQUIRE_AUTH", default=IS_PUBLIC_MODE)
ALLOW_PLAINTEXT_API_KEYS = env_bool("LATTICEAI_ALLOW_PLAINTEXT_API_KEYS", default=False)
CORS_ALLOW_NETWORK = env_bool("LATTICEAI_CORS_ALLOW_NETWORK", default=False)
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
    return DATA_DIR / "sessions.json"

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
        **{key: item[key] for key in ["id", "name", "category", "install_mode", "description", "capabilities"]},
        "connector_url": item.get("connector_url"),
        "external_url": item.get("external_url"),
        "installed": installed,
        "status": state.get("status") or ("active" if installed and not connector_pending else "needs_auth" if connector_pending else "available"),
        "authenticated": authenticated,
        "updated_at": state.get("updated_at"),
    }

def recommend_mcps(query: str, limit: int = 5) -> List[Dict]:
    text = (query or "").lower()
    installed = load_mcp_installs().get("installed", {})
    scored = []
    for item in MCP_REGISTRY:
        score = 0
        hits = []
        for keyword in item["keywords"]:
            if keyword.lower() in text:
                score += 3 if len(keyword) > 2 else 1
                hits.append(keyword)
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
            for item in MCP_REGISTRY
            if item["id"] in fallback_ids
        ]
    return sorted(scored, key=lambda item: item["score"], reverse=True)[: max(1, min(limit, 24))]

def install_mcp(mcp_id: str) -> Dict:
    item = next((entry for entry in MCP_REGISTRY if entry["id"] == mcp_id), None)
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
                capture_output=True,
                text=True,
                timeout=900,
                check=False,
            )
            if completed.returncode != 0:
                raise HTTPException(status_code=500, detail=completed.stderr[-2000:] or f"{pkg} 설치 실패")
        status = "active"
        message = f"필수 패키지 설치 완료: {', '.join(packages)}"
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

def connector_info(mcp_id: str) -> Dict:
    item = next((entry for entry in MCP_REGISTRY if entry["id"] == mcp_id), None)
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

app = FastAPI(title=f"Lattice AI Server ({APP_MODE})", version="2.1.0", lifespan=lifespan)

CORS_ALLOWED_ORIGINS = ["http://localhost:4825", "http://127.0.0.1:4825"]
if CORS_ALLOW_NETWORK:
    CORS_ALLOWED_ORIGINS = ["*"]

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
app.mount("/agent-files", StaticFiles(directory=str(AGENT_ROOT)), name="agent-files")

@app.post("/register")
async def register(req: UserRegister):
    users = load_users()
    if req.email in users:
        raise HTTPException(status_code=400, detail="이미 존재하는 이메일입니다.")
    users[req.email] = {
        "password": hash_password(req.password),
        "name": req.name,
        "nickname": req.nickname,
        "role": "user",
        "disabled": False,
    }
    save_users(users)
    return {"status": "ok", "message": "회원가입 성공!"}

@app.post("/login")
async def login(req: UserLogin):
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
        "token": token,
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


class ToolPathRequest(BaseModel):
    path: str = "."


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


class LocalWriteRequest(BaseModel):
    path: str
    content: str
    approved: bool = False


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
        "command": [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
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
        {"id": "llamacpp:unsloth/gemma-2-2b-it-GGUF", "name": "Gemma 2 2B GGUF via llama.cpp", "family": "Gemma", "tag": "gguf-q4", "size": "gguf", "pullable": True},
        {"id": "llamacpp:unsloth/gemma-2-9b-it-GGUF", "name": "Gemma 2 9B GGUF via llama.cpp", "family": "Gemma", "tag": "gguf-q4", "size": "gguf", "pullable": True},
        {"id": "llamacpp:Qwen/Qwen2.5-3B-Instruct-GGUF", "name": "Qwen 2.5 3B GGUF via llama.cpp", "family": "Qwen 2.5", "tag": "gguf-q4", "size": "gguf", "pullable": True},
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


def engine_installed(engine: str) -> bool:
    if engine == "local_mlx":
        return bool(mx is not None)
    if engine == "ollama":
        return shutil.which("ollama") is not None
    if engine == "vllm":
        return importlib.util.find_spec("vllm") is not None
    if engine == "lmstudio":
        return shutil.which("lms") is not None or Path("/Applications/LM Studio.app").exists()
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

    hf_models_root = Path.home() / ".latticeai" / "hf-models"
    hf_models_root.mkdir(parents=True, exist_ok=True)
    mlx_models = []
    for m in ENGINE_MODEL_CATALOG.get("local_mlx", []):
        repo_id = m["id"]
        marker = hf_models_root / repo_id.replace("/", "__")
        mlx_models.append({**m, "pulled": marker.exists()})

    vllm_models = []
    for m in ENGINE_MODEL_CATALOG.get("vllm", []):
        repo_id = m["id"].removeprefix("vllm:")
        marker = hf_models_root / repo_id.replace("/", "__")
        vllm_models.append({**m, "pulled": marker.exists()})

    lmstudio_models = []
    for m in ENGINE_MODEL_CATALOG.get("lmstudio", []):
        repo_id = m["id"].removeprefix("lmstudio:")
        marker = hf_models_root / repo_id.replace("/", "__")
        lmstudio_models.append({**m, "pulled": marker.exists()})

    llamacpp_models = []
    for m in ENGINE_MODEL_CATALOG.get("llamacpp", []):
        repo_id = m["id"].removeprefix("llamacpp:")
        marker = hf_models_root / repo_id.replace("/", "__")
        llamacpp_models.append({**m, "pulled": marker.exists()})

    local_server_specs = [
        {
            "id": "vllm",
            "name": "vLLM",
            "description": "vLLM OpenAI 호환 서버(예: http://localhost:8000/v1)에 연결합니다.",
            "requires": "VLLM_BASE_URL",
        },
        {
            "id": "lmstudio",
            "name": "LM Studio",
            "description": "LM Studio 로컬 OpenAI 호환 서버에 연결합니다.",
            "requires": "LMSTUDIO_BASE_URL",
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
        engines.append({
            "id": spec["id"],
            "name": spec["name"],
            "kind": "local-server",
            "description": spec["description"],
            "installed": engine_installed(spec["id"]),
            "installable": spec["id"] in ENGINE_INSTALLERS,
            "install_label": ENGINE_INSTALLERS.get(spec["id"], {}).get("label"),
            "requires": spec["requires"],
            "models": (
                vllm_models if spec["id"] == "vllm"
                else lmstudio_models if spec["id"] == "lmstudio"
                else llamacpp_models if spec["id"] == "llamacpp"
                else ENGINE_MODEL_CATALOG.get(spec["id"], [])
            ),
            "note": f"{spec['requires']} 설정 시 활성화됩니다.",
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
    try:
        completed = subprocess.run(
            installer["command"],
            cwd=str(Path(__file__).resolve().parent),
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail="엔진 설치 시간이 초과되었습니다.")
    result = {
        "engine": engine,
        "command": " ".join(installer["command"]),
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
    return {
        **base,
        "current_model": router.current_model_id,
        "loaded_models": router.loaded_model_ids,
        "device": "Apple Silicon MLX" if not IS_PUBLIC_MODE else "Public cloud/API runtime",
        "features": runtime_features(),
        "providers": router.detected_cloud_models(),
        "engines": engine_status(),
    }


@app.get("/mode")
@app.get("/runtime_features")
async def mode():
    return runtime_features()


@app.get("/engines")
async def engines():
    return {"engines": engine_status(), "current": router.current_model_id}


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
        if not shutil.which("ollama"):
            raise HTTPException(status_code=400, detail="Ollama가 설치되지 않았습니다.")
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

    if provider in {"vllm", "lmstudio", "llamacpp", "local_mlx", "mlx"}:
        if importlib.util.find_spec("huggingface_hub") is None:
            raise HTTPException(status_code=400, detail="huggingface_hub가 없습니다. 먼저 엔진 설치를 진행해 주세요.")
        target_dir = Path.home() / ".latticeai" / "hf-models" / model_name.replace("/", "__")
        target_dir.mkdir(parents=True, exist_ok=True)
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "huggingface_hub", "download", model_name, "--local-dir", str(target_dir)],
                capture_output=True, text=True, timeout=3600, check=False,
            )
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=408, detail=f"{provider} 모델 다운로드 시간이 초과되었습니다.")
        if completed.returncode != 0:
            raise HTTPException(status_code=500, detail=completed.stderr[-2000:] or f"{provider} 모델 다운로드 실패")
        return {"provider": provider, "model": model_name, "returncode": completed.returncode, "path": str(target_dir)}

    raise HTTPException(status_code=400, detail=f"{provider} 엔진 모델 다운로드는 아직 자동화되지 않았습니다.")


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
        "engines": engine_status(),
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
        if req.engine and req.engine not in {"local_mlx", "mlx"} and ":" not in model_id:
            model_id = f"{req.engine}:{model_id}"
        effective_email = (req.user_email or get_current_user(request) or "").strip()
        user_api_key = None
        if ":" in model_id:
            provider = model_id.split(":", 1)[0]
            user_api_key = get_user_api_key(effective_email, provider)
        msg = await router.load_model(
            model_id,
            req.adapter_path,
            draft_model_id=req.draft_model_id,
            api_key_override=user_api_key,
            owner=effective_email or None,
        )
        return {"status": "ok", "message": msg, "current": router.current_model_id}
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

    # 메시지 안에 절대 경로나 ~/... 경로가 있으면 자동으로 파일 읽어서 컨텍스트 주입
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

AGENT_SYSTEM_PROMPT = """You are Lattice AI Agent — a local, professional-grade coding assistant.
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
- Korean/English/Chinese: answer in the language the user used; default to
  Korean if mixed or ambiguous.

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
_TOOL_RISK: Dict[str, str] = {
    # read-only workspace tools
    "list_dir": "low", "workspace_tree": "low", "read_file": "low",
    "search_files": "low", "grep": "low", "inspect_html": "low",
    # read-only planning
    "todo_read": "low",
    # read-only local FS
    "local_list": "low", "local_read": "low",
    # read-only git
    "git_status": "low", "git_log": "low", "git_diff": "low", "git_show": "low",
    # read-only knowledge / computer
    "knowledge_search": "low", "knowledge_tree": "low",
    "obsidian_search": "low", "obsidian_tree": "low",
    "computer_screenshot": "low", "computer_status": "low",
    # write workspace
    "write_file": "medium", "edit_file": "medium", "create_web_project": "medium",
    "create_docx": "medium", "create_xlsx": "medium",
    "create_pptx": "medium", "create_pdf": "medium",
    # write planning
    "todo_write": "low",
    # write knowledge
    "knowledge_save": "medium", "obsidian_save": "medium",
    # write local FS (arbitrary path — treated as medium; blocked from system roots below)
    "local_write": "medium",
    # preview
    "preview_url": "medium",
    # execute commands
    "run_command": "high",
    # computer control
    "computer_click": "high", "computer_type": "high", "computer_key": "high",
    "computer_scroll": "high", "computer_drag": "high", "computer_move": "high",
    "computer_open_app": "high", "computer_open_url": "high",
}

# Paths that local_write must never target (system-level protection)
_LOCAL_WRITE_BLOCKED_PREFIXES = (
    "/etc/", "/usr/", "/bin/", "/sbin/", "/System/", "/private/etc/",
    "/Library/LaunchDaemons/", "/Library/LaunchAgents/",
)


def _agent_risk(action_name: str, args: dict) -> str:
    """Return risk level for an action, upgrading local_write to 'high' for system paths."""
    risk = _TOOL_RISK.get(action_name, "medium")
    if action_name == "local_write":
        path = str(args.get("path", ""))
        if any(path.startswith(p) for p in _LOCAL_WRITE_BLOCKED_PREFIXES):
            risk = "high"
    return risk


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


@app.post("/agent")
async def agent(req: AgentRequest, request: Request):
    """Natural-language local agent loop for Telegram and future clients."""
    current_user = require_user(request)
    enforce_rate_limit(current_user, "agent")
    if not router.current_model_id:
        raise HTTPException(status_code=400, detail="No model loaded. Call /models/load first.")

    ensure_agent_root()
    transcript = []
    max_steps = max(1, min(req.max_steps, 50))
    lang = detect_language(req.message)
    lang_hint = _LANG_HINT[lang]

    for step in range(max_steps):
        recent_context = build_recent_chat_context(conversation_id=req.conversation_id)
        context = (
            f"{AGENT_SYSTEM_PROMPT}\n\n"
            f"[LANGUAGE: {lang_hint}]\n\n"
            f"Workspace root: {AGENT_ROOT}\n\n"
            f"Recent conversation:\n{recent_context or '(none)'}\n\n"
            f"User request:\n{req.message}\n\n"
            f"Previous tool results:\n{json.dumps(transcript, ensure_ascii=False, indent=2)}"
        )
        raw = await router.generate(
            message="Choose the next agent action.",
            context=context,
            max_tokens=4096,
            temperature=req.temperature,
        )

        try:
            action = _extract_agent_action(str(raw))
        except ValueError as exc:
            transcript.append({"step": step + 1, "action": "parse_error", "raw": str(raw), "error": str(exc)})
            message = "작업 계획을 안정적으로 해석하지 못해 자동 실행을 중단했습니다. 요청을 더 짧고 구체적으로 다시 시도해 주세요."
            save_to_history("user", req.message, source=req.source or "web", conversation_id=req.conversation_id)
            save_to_history("assistant", message, source=req.source or "web", conversation_id=req.conversation_id)
            created_files = _collect_created_files(transcript)
            return {
                "status": "ok",
                "response": message,
                "workspace": str(AGENT_ROOT),
                "steps": transcript,
                "created_files": created_files,
            }

        name = action.get("action")
        thoughts = str(action.get("thoughts") or "")[:600]
        if name == "final":
            message = action.get("message", "작업을 완료했습니다.")
            save_to_history("user", req.message, source=req.source or "web", conversation_id=req.conversation_id)
            save_to_history("assistant", message, source=req.source or "web", conversation_id=req.conversation_id)
            created_files = _collect_created_files(transcript)
            return {"status": "ok", "response": message, "workspace": str(AGENT_ROOT), "steps": transcript, "created_files": created_files}

        # Prevent repeated file/project creation loops with identical action+args.
        last_step = transcript[-1] if transcript else None
        current_args = action.get("args") or {}
        if (
            name in _FILE_CREATE_ACTIONS
            and last_step
            and last_step.get("action") == name
            and (last_step.get("args") or {}) == current_args
            and "result" in last_step
        ):
            message = "동일한 파일 작성을 반복 시도해서 중단했습니다. 직전 결과를 확인하고 다음 단계로 진행하세요."
            save_to_history("user", req.message, source=req.source or "web", conversation_id=req.conversation_id)
            save_to_history("assistant", message, source=req.source or "web", conversation_id=req.conversation_id)
            created_files = _collect_created_files(transcript)
            return {"status": "ok", "response": message, "workspace": str(AGENT_ROOT), "steps": transcript, "created_files": created_files}

        if name == "clear_history":
            result = clear_history(current_args.get("keep_last", 0))
            append_audit_event(
                "history_delete",
                user_email=current_user,
                source=req.source or "agent",
                keep_last=current_args.get("keep_last", 0),
                removed=result.get("removed", 0),
                kept=result.get("kept", 0),
            )
            transcript.append({"step": step + 1, "thoughts": thoughts, "action": name, "args": current_args, "result": result})
            continue

        risk = _agent_risk(name, current_args)

        # Block system-path local_write even if the LLM tries it
        if name == "local_write":
            path = str(current_args.get("path", ""))
            if any(path.startswith(p) for p in _LOCAL_WRITE_BLOCKED_PREFIXES):
                transcript.append({
                    "step": step + 1, "thoughts": thoughts, "action": name, "args": current_args,
                    "risk": "high", "error": f"BLOCKED: writing to system path is not allowed: {path}",
                })
                append_audit_event(
                    "agent_blocked", user_email=current_user, source=req.source or "agent",
                    action=name, path=path, reason="system_path",
                )
                continue

        # Audit medium/high actions before execution
        if risk in ("medium", "high"):
            append_audit_event(
                "agent_exec", user_email=current_user, source=req.source or "agent",
                step=step + 1, action=name, risk=risk,
                args={k: v for k, v in (current_args or {}).items() if k != "content"},
            )

        try:
            result = execute_tool(name, current_args)
            transcript.append({"step": step + 1, "thoughts": thoughts, "action": name, "args": current_args, "risk": risk, "result": result})
        except (ToolError, KeyError, TypeError) as exc:
            transcript.append({"step": step + 1, "thoughts": thoughts, "action": name, "args": current_args, "risk": risk, "error": str(exc)})

    summary_context = (
        f"{AGENT_SYSTEM_PROMPT}\n\n"
        f"Recent conversation:\n{build_recent_chat_context(conversation_id=req.conversation_id) or '(none)'}\n\n"
        f"User request:\n{req.message}\n\n"
        f"Tool transcript:\n{json.dumps(transcript, ensure_ascii=False, indent=2)}"
    )
    summary = await router.generate(
        message='Return only {"action":"final","message":"..."} summarizing the current result in Korean.',
        context=summary_context,
        max_tokens=1024,
        temperature=0.1,
    )
    try:
        final_action = _extract_agent_action(str(summary))
        message = final_action.get("message", str(summary))
    except ValueError:
        message = str(summary)

    save_to_history("user", req.message, source=req.source or "web", conversation_id=req.conversation_id)
    save_to_history("assistant", message, source=req.source or "web", conversation_id=req.conversation_id)
    created_files = _collect_created_files(transcript)
    return {"status": "ok", "response": message, "workspace": str(AGENT_ROOT), "steps": transcript, "created_files": created_files}


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
    require_user(request)
    return _tool_response(read_document, req.path)


@app.get("/tools/pdf_pages")
async def tools_pdf_pages(path: str, request: Request):
    """Render PDF pages as base64 PNG images using PyMuPDF."""
    require_user(request)
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

def _local_permission_response(path: str, action: str) -> dict:
    return {
        "permission_required": True,
        "path": path,
        "action": action,
        "action_label": _PERMISSION_ACTION_LABELS.get(action, action),
        "message": f"AI가 '{path}' 에 대한 {_PERMISSION_ACTION_LABELS.get(action, action)} 권한을 요청합니다.",
    }


@app.post("/local/list")
async def local_list_endpoint(req: LocalAccessRequest, request: Request):
    require_user(request)
    if not req.approved:
        return _local_permission_response(req.path, "list")
    return _tool_response(local_list, req.path)


@app.post("/local/read")
async def local_read_endpoint(req: LocalAccessRequest, request: Request):
    require_user(request)
    if not req.approved:
        return _local_permission_response(req.path, "read")
    return _tool_response(local_read, req.path)


@app.get("/local/serve")
async def local_serve_file(path: str, request: Request):
    """Serve a local file (images etc.) directly for browser preview."""
    require_user(request)
    target = Path(path).expanduser().resolve()
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(target))


@app.post("/local/write")
async def local_write_endpoint(req: LocalWriteRequest, request: Request):
    require_user(request)
    if not req.approved:
        return _local_permission_response(req.path, "write")
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
    require_user(request)
    return _tool_response(run_command, req.command, req.cwd)


@app.get("/tools/network_status")
async def tools_network_status(request: Request):
    require_user(request)
    return _tool_response(network_status)


@app.post("/tools/build_project")
async def tools_build_project(req: ToolScriptRequest, request: Request):
    require_user(request)
    return _tool_response(build_project, req.cwd, req.script)


@app.post("/tools/deploy_project")
async def tools_deploy_project(req: ToolScriptRequest, request: Request):
    require_user(request)
    return _tool_response(deploy_project, req.cwd, req.script)


@app.get("/mcp/tools")
async def mcp_tools():
    installed = load_mcp_installs().get("installed", {})
    return {
        "status": "ok",
        "workspace": str(AGENT_ROOT),
        "installed_mcps": [mcp_public_item(item, installed) for item in MCP_REGISTRY],
        "tools": [
            {"name": "list_dir", "description": "List files in the agent workspace."},
            {"name": "workspace_tree", "description": "Return a recursive workspace tree."},
            {"name": "read_file", "description": "Read a UTF-8 file from the workspace with optional line numbers and offset/limit slicing."},
            {"name": "write_file", "description": "Write a UTF-8 file inside the workspace (new files / full rewrites)."},
            {"name": "edit_file", "description": "Precise diff-style edit: replace exact old_string with new_string. Requires unique match unless replace_all=true."},
            {"name": "search_files", "description": "Substring search in text files (legacy)."},
            {"name": "grep", "description": "Regex search across the workspace with line numbers and optional context."},
            {"name": "todo_read", "description": "Read the agent's persistent TODO list for the current workspace."},
            {"name": "todo_write", "description": "Replace the agent's TODO list (id, content, status: pending/in_progress/completed)."},
            {"name": "clear_history", "description": "Clear chat history to reduce context and speed up responses."},
            {"name": "inspect_html", "description": "Inspect local HTML structure and assets."},
            {"name": "preview_url", "description": "Return a server URL for a workspace file."},
            {"name": "create_docx", "description": "Create a Word DOCX document in the agent workspace."},
            {"name": "create_xlsx", "description": "Create an XLSX spreadsheet in the agent workspace."},
            {"name": "create_pptx", "description": "Create a PPTX presentation deck in the agent workspace."},
            {"name": "create_pdf", "description": "Create a PDF document in the agent workspace."},
            {"name": "local_list", "description": "List any local folder (requires user permission via UI)."},
            {"name": "local_read", "description": "Read any local file (requires user permission via UI)."},
            {"name": "local_write", "description": "Write any local file (requires user permission via UI)."},
            {"name": "read_document", "description": "Extract text from PDF, DOCX, XLSX, PPTX, TXT, MD, CSV files."},
            {"name": "computer_screenshot", "description": "Capture the current Mac screen as base64 PNG."},
            {"name": "computer_open_app", "description": "Open or focus a Mac app, e.g. Google Chrome."},
            {"name": "computer_open_url", "description": "Open a URL in a Mac app, e.g. Google Chrome."},
            {"name": "computer_click", "description": "Click at screen coordinates (x, y)."},
            {"name": "computer_type", "description": "Type text at the current focus position."},
            {"name": "computer_key", "description": "Press a keyboard key or shortcut (e.g. 'command+c')."},
            {"name": "computer_scroll", "description": "Scroll at screen coordinates."},
            {"name": "computer_move", "description": "Move the mouse to screen coordinates."},
            {"name": "computer_drag", "description": "Drag from (x1,y1) to (x2,y2)."},
            {"name": "computer_status", "description": "Check if Mac Computer Use (pyautogui) is available."},
            {"name": "chrome_status", "description": "Report Chrome desktop bridge availability."},
            {"name": "computer_use_status", "description": "Report Mac Computer Use bridge availability."},
            {"name": "knowledge_save", "description": "Save a note into the local knowledge garden."},
            {"name": "knowledge_search", "description": "Search the local knowledge garden."},
            {"name": "knowledge_tree", "description": "List local knowledge garden markdown files."},
            {"name": "knowledge_graph_ingest", "description": "Ingest a message, AI answer, or connector event into the SQLite knowledge graph."},
            {"name": "knowledge_graph_search", "description": "Search graph nodes, summaries, and JSON metadata."},
            {"name": "knowledge_graph_graph", "description": "Return Obsidian-style graph nodes and edges."},
            {"name": "knowledge_graph_context", "description": "Return compact graph-backed RAG context for a prompt."},
            {"name": "obsidian_save", "description": "Save a note into the Obsidian-compatible memory vault."},
            {"name": "obsidian_search", "description": "Search the Obsidian-compatible memory vault."},
            {"name": "obsidian_tree", "description": "List Obsidian memory vault markdown files."},
            {"name": "git_status", "description": "Read-only local git status inside the workspace."},
            {"name": "git_diff", "description": "Read-only local git diff inside the workspace."},
            {"name": "git_log", "description": "Read-only local git log inside the workspace."},
            {"name": "git_show", "description": "Read-only local git show --stat inside the workspace."},
            {"name": "network_status", "description": "Get current local/private IP, public IP, hostname, and Wi-Fi info."},
            {"name": "run_command", "description": "Run an allowlisted local command inside the workspace."},
            {"name": "build_project", "description": "Run an allowlisted package.json build/compile/typecheck/test script to verify changes actually work."},
            {"name": "deploy_project", "description": "Run an allowlisted package.json deploy/preview/release/package installer script (pkg/exe)."},
        ],
    }


@app.post("/mcp/recommend")
async def mcp_recommend(req: McpRecommendRequest, request: Request):
    require_user(request)
    return {"recommendations": recommend_mcps(req.query, req.limit)}


@app.post("/mcp/install")
async def mcp_install(req: McpInstallRequest, request: Request):
    require_user(request)
    return install_mcp(req.mcp_id)


@app.get("/mcp/installed")
async def mcp_installed(request: Request):
    require_user(request)
    installed = load_mcp_installs().get("installed", {})
    return {"installed": [mcp_public_item(item, installed) for item in MCP_REGISTRY]}


@app.get("/mcp/connectors/{mcp_id}")
async def mcp_connector(mcp_id: str, request: Request):
    require_user(request)
    return connector_info(mcp_id)


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
