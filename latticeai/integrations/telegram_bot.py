import asyncio
import base64
import json
import logging
import os
import socket
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict

import httpx

from latticeai.cli.runtime import _load_env_file
from latticeai.core.io_utils import atomic_write_json
from latticeai.core.logging_safety import install_sensitive_log_filter, safe_log_text
from latticeai.core.quiet import quiet

install_sensitive_log_filter()

def load_env_file(path=".env"):
    # Single source of truth: shared with ltcai_cli via latticeai.cli.runtime.
    _load_env_file(Path(path))

load_env_file()

def env_value(primary: str, default: str = "") -> str:
    return os.getenv(primary) or default

TOKEN          = env_value("LATTICEAI_TELEGRAM_BOT_TOKEN")
API_URL        = f"https://api.telegram.org/bot{TOKEN}"
SERVER_PORT    = int(env_value("LATTICEAI_SERVER_PORT", "4825"))
BASE_URL       = env_value("LATTICEAI_BASE_URL", env_value("LATTICEAI_SERVER_URL", f"http://127.0.0.1:{SERVER_PORT}")).rstrip("/")
CHAT_URL       = f"{BASE_URL}/chat"
AGENT_URL      = f"{BASE_URL}/agent"
MCP_TOOLS_URL  = f"{BASE_URL}/mcp/tools"
HISTORY_URL    = f"{BASE_URL}/history"
STATUS_URL     = f"{BASE_URL}/status"
MODELS_URL     = f"{BASE_URL}/models"
GRAPH_STATS_URL = f"{BASE_URL}/knowledge-graph/stats"
UPLOAD_DOC_URL  = f"{BASE_URL}/upload/document"
PROPOSALS_URL   = f"{BASE_URL}/api/proposals"

AGENT_RESUME_URL      = f"{BASE_URL}/agent/resume"
AGENT_WORKSPACE       = Path(env_value("LATTICEAI_AGENT_ROOT", "agent_workspace")).resolve()

# Pending plan approvals keyed by run_id (or legacy context_id).
# Values: chat_id, models, and the resume credentials (token and/or legacy).
_bot_pending_plans: dict[str, dict] = {}
MAX_TELEGRAM_FILE_BYTES = 45 * 1024 * 1024
INVITE_CODE           = env_value("LATTICEAI_INVITE_CODE")
PUBLIC_WEB_URL        = env_value("LATTICEAI_PUBLIC_URL")
DATA_DIR              = Path(env_value("LATTICEAI_DATA_DIR", str(Path.home() / ".ltcai")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
CHAT_IDS_FILE = Path(env_value("LATTICEAI_TELEGRAM_CHATS_FILE", str(DATA_DIR / "telegram_chats.json")))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Server session auth ───────────────────────────────────────────────────────

def _get_server_session() -> str:
    """Return only the explicitly provisioned bot-to-server token.

    Web sessions are hashed at rest and belong to interactive users. Scanning
    ``sessions.json`` cannot recover a usable token and would couple the bot to
    whichever administrator happened to be logged in most recently.
    """
    return env_value("LATTICEAI_SERVER_SESSION_TOKEN").strip()

def _server_client(**kwargs) -> httpx.AsyncClient:
    """Return a server client authenticated by an explicit bearer capability."""
    token = _get_server_session()
    if not token:
        raise RuntimeError(
            "LATTICEAI_SERVER_SESSION_TOKEN is required for Telegram server API calls."
        )
    headers = dict(kwargs.pop("headers", {}) or {})
    headers["Authorization"] = f"Bearer {token}"
    return httpx.AsyncClient(headers=headers, **kwargs)

# ── Chat ID registry ─────────────────────────────────────────────────────────

def parse_allowed_chat_ids(raw: str) -> frozenset[int]:
    """Parse a comma-separated Telegram chat-id allowlist."""
    allowed: set[int] = set()
    for value in str(raw or "").split(","):
        value = value.strip()
        if not value:
            continue
        try:
            allowed.add(int(value))
        except ValueError:
            logger.warning(
                "Invalid Telegram chat id ignored in allowlist: %r",
                safe_log_text(value),
            )
    return frozenset(allowed)


def allowed_chat_ids() -> frozenset[int]:
    """Return the configured allowlist; missing configuration denies all."""
    return parse_allowed_chat_ids(env_value("LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS"))


def is_chat_allowed(chat_id) -> bool:
    try:
        return int(chat_id) in allowed_chat_ids()
    except (TypeError, ValueError):
        return False


def load_chat_ids():
    try:
        if CHAT_IDS_FILE.exists():
            data = json.loads(CHAT_IDS_FILE.read_text(encoding="utf-8"))
            return {int(cid) for cid in data.get("chat_ids", [])}
    except Exception as e:
        logger.error("텔레그램 채팅 목록 로드 실패: %s", safe_log_text(e))
    return set()

def save_chat_ids(chat_ids):
    try:
        atomic_write_json(CHAT_IDS_FILE, {"chat_ids": sorted(chat_ids)})
    except Exception as e:
        logger.error("텔레그램 채팅 목록 저장 실패: %s", safe_log_text(e))

def register_chat_id(chat_id):
    if not is_chat_allowed(chat_id):
        logger.warning(
            "허용되지 않은 텔레그램 채팅 등록 차단: %s",
            safe_log_text(chat_id),
        )
        return False
    chat_id = int(chat_id)
    chat_ids = load_chat_ids()
    if chat_id not in chat_ids:
        chat_ids.add(chat_id)
        save_chat_ids(chat_ids)
        logger.info("텔레그램 웹 미러링 대상 등록: %s", chat_id)
    return True

# ── Telegram API helpers ──────────────────────────────────────────────────────

async def send_message(client, chat_id, text, reply_markup=None):
    url = f"{API_URL}/sendMessage"
    try:
        chunks = [text[i:i+3900] for i in range(0, len(text), 3900)] or [""]
        for i, chunk in enumerate(chunks):
            payload = {"chat_id": chat_id, "text": chunk}
            if reply_markup and i == len(chunks) - 1:
                payload["reply_markup"] = reply_markup
            await client.post(url, json=payload)
    except Exception as e:
        logger.error("메시지 전송 실패: %s", safe_log_text(e))

async def send_photo(client, chat_id, file_path: Path, caption: str = ""):
    url = f"{API_URL}/sendPhoto"
    try:
        # Read on a worker thread: a screenshot is megabytes, and reading it
        # inline stalled the bot's poll loop (and everything else on the loop)
        # for the length of the read.
        blob = await asyncio.to_thread(Path(file_path).read_bytes)
        res = await client.post(url, data={"chat_id": str(chat_id), "caption": caption[:1024]},
                                files={"photo": (file_path.name, blob)}, timeout=60.0)
        if res.status_code != 200:
            await send_message(client, chat_id, f"사진 전송 실패 ({res.status_code})")
    except Exception as e:
        logger.error("사진 전송 실패: %s", safe_log_text(e))
        await send_message(client, chat_id, f"사진 전송 오류: {safe_log_text(e)}")

async def send_document(client, chat_id, file_path, caption=None, filename=None):
    url = f"{API_URL}/sendDocument"
    try:
        blob = await asyncio.to_thread(Path(file_path).read_bytes)
        res = await client.post(
            url,
            data={"chat_id": str(chat_id), **({"caption": caption[:1024]} if caption else {})},
            files={"document": (filename or Path(file_path).name, blob)},
            timeout=300.0,
        )
        if res.status_code != 200:
            logger.error("파일 전송 실패 (%s): %s", res.status_code, safe_log_text(res.text))
    except Exception as e:
        logger.error("파일 전송 실패: %s", safe_log_text(e))

async def send_chat_action(client, chat_id, action="typing"):
    try:
        await client.post(f"{API_URL}/sendChatAction", json={"chat_id": chat_id, "action": action})
    except Exception:
        quiet()

async def answer_callback(client, callback_query_id, text=""):
    try:
        await client.post(f"{API_URL}/answerCallbackQuery",
                          json={"callback_query_id": callback_query_id, "text": text})
    except Exception:
        quiet()

async def edit_message(client, chat_id, message_id, text, reply_markup=None):
    try:
        payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        await client.post(f"{API_URL}/editMessageText", json=payload)
    except Exception:
        quiet()

# ── Network helpers ───────────────────────────────────────────────────────────

def get_lan_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if not ip.startswith("127."):
                return ip
    except OSError:
        quiet()
    try:
        hostname = socket.gethostname()
        for ip in socket.gethostbyname_ex(hostname)[2]:
            if not ip.startswith("127."):
                return ip
    except OSError:
        quiet()
    return "127.0.0.1"

def get_web_url():
    if PUBLIC_WEB_URL:
        return PUBLIC_WEB_URL.rstrip("/")
    base = f"http://{get_lan_ip()}:{SERVER_PORT}/"
    return f"{base}?code={INVITE_CODE}" if INVITE_CODE else base

def get_graph_url():
    if PUBLIC_WEB_URL:
        return f"{PUBLIC_WEB_URL.rstrip('/')}/graph"
    return f"http://{get_lan_ip()}:{SERVER_PORT}/graph"

# ── Broadcast (web → telegram mirror) ────────────────────────────────────────

async def broadcast_web_chat(role, text):
    if not TOKEN:
        return
    # Old releases registered every sender. Intersect persisted recipients
    # with the current allowlist so stale files cannot keep receiving mirrors.
    allowed = allowed_chat_ids()
    chat_ids = load_chat_ids() & set(allowed)
    if not chat_ids:
        return
    label = "사용자" if role == "user" else "Lattice AI"
    message = f"[Web] {label}\n{text}"
    async with httpx.AsyncClient() as client:
        for chat_id in chat_ids:
            await send_message(client, chat_id, message)

# ── Polling ───────────────────────────────────────────────────────────────────

async def get_updates(client, offset=None):
    url = f"{API_URL}/getUpdates?timeout=30"
    if offset:
        url += f"&offset={offset}"
    try:
        res = await client.get(url, timeout=35)
        return res.json()
    except Exception:
        return None

# ── File download ─────────────────────────────────────────────────────────────

async def download_telegram_file(client, file_id) -> bytes | None:
    try:
        res = await client.get(f"{API_URL}/getFile?file_id={file_id}")
        file_path = res.json().get("result", {}).get("file_path")
        if not file_path:
            return None
        dl = await client.get(f"https://api.telegram.org/file/bot{TOKEN}/{file_path}")
        return dl.content if dl.status_code == 200 else None
    except Exception as e:
        logger.error("파일 다운로드 실패: %s", safe_log_text(e))
        return None

async def download_as_base64(client, file_id) -> str | None:
    data = await download_telegram_file(client, file_id)
    return base64.b64encode(data).decode() if data else None

# ── Main menu ─────────────────────────────────────────────────────────────────

MAIN_MENU = {
    "inline_keyboard": [
        [
            {"text": "📊 서버 상태",          "callback_data": "cmd:status"},
            {"text": "🧠 현재 모델",           "callback_data": "cmd:model"},
        ],
        [
            {"text": "🕸 Knowledge Graph",     "callback_data": "cmd:graph"},
            {"text": "📸 스크린샷",            "callback_data": "cmd:screenshot"},
        ],
        [
            {"text": "📜 최근 대화 5건",        "callback_data": "cmd:history"},
            {"text": "🗑 기록 정리",            "callback_data": "cmd:clear"},
        ],
        [
            {"text": "🔗 웹 UI 열기",           "callback_data": "cmd:web"},
            {"text": "🔌 MCP 도구 목록",        "callback_data": "cmd:mcp"},
        ],
        [
            {"text": "🗂 변경 제안 검토",        "callback_data": "cmd:review"},
        ],
    ]
}

async def show_menu(client, chat_id):
    await send_message(client, chat_id, "📱 Lattice AI 원격 제어 메뉴입니다.", reply_markup=MAIN_MENU)

# ── Server status ─────────────────────────────────────────────────────────────

async def _mac_ram_used_gb() -> str:
    try:
        vm_proc = await asyncio.create_subprocess_exec(
            "vm_stat", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        vm_out, _ = await vm_proc.communicate()
        lines = vm_out.decode().splitlines()

        # Parse page size from header line: "Mach Virtual Memory Statistics: (page size of 16384 bytes)"
        page_size = 4096
        if lines:
            import re
            m = re.search(r"page size of (\d+) bytes", lines[0])
            if m:
                page_size = int(m.group(1))

        stats = {}
        for line in lines[1:]:
            if ":" in line:
                k, _, v = line.partition(":")
                try:
                    stats[k.strip()] = int(v.strip().rstrip(".")) * page_size
                except ValueError:
                    quiet()

        used = stats.get("Pages active", 0) + stats.get("Pages wired down", 0)

        mem_proc = await asyncio.create_subprocess_exec(
            "sysctl", "-n", "hw.memsize", stdout=asyncio.subprocess.PIPE
        )
        mem_out, _ = await mem_proc.communicate()
        total = int(mem_out.strip())
        return f"{used/1e9:.1f} GB / {total/1e9:.0f} GB"
    except Exception:
        return "N/A"

async def show_status(client, chat_id):
    await send_chat_action(client, chat_id, "typing")
    try:
        async with _server_client() as lc:
            res = await lc.get(STATUS_URL, timeout=5.0)
            data = res.json() if res.status_code == 200 else {}
    except Exception:
        data = {}

    ram = await _mac_ram_used_gb()
    model = data.get("loaded_model") or "없음"
    mode  = data.get("mode") or "unknown"
    state = "🟢 온라인" if data.get("status") == "online" else "🔴 오프라인"

    text = (
        f"📊 Lattice AI 서버 상태\n"
        f"상태: {state}\n"
        f"모드: {mode}\n"
        f"모델: {model}\n"
        f"RAM: {ram}"
    )
    await send_message(client, chat_id, text)

# ── Model info & unload ───────────────────────────────────────────────────────

async def show_model_info(client, chat_id):
    await send_chat_action(client, chat_id, "typing")
    try:
        async with _server_client() as lc:
            res = await lc.get(MODELS_URL, timeout=5.0)
            data = res.json() if res.status_code == 200 else {}
    except Exception:
        data = {}

    current = data.get("current") or "없음"
    loaded  = data.get("loaded") or []
    loaded_str = "\n".join(f"  - {m}" for m in loaded) if loaded else "  없음"
    text = f"🧠 현재 모델: {current}\n\n로드된 모델:\n{loaded_str}"

    markup = None
    if loaded:
        markup = {
            "inline_keyboard": [
                [{"text": f"🗑 {m} 언로드", "callback_data": f"model:unload:{m}"}]
                for m in loaded
            ] + [[{"text": "↩ 메뉴로", "callback_data": "cmd:menu"}]]
        }
    await send_message(client, chat_id, text, reply_markup=markup)

def _unload_all_report(results: list[tuple[str, int]]) -> str:
    """Report an unload-all run from the statuses the server actually returned."""
    failed = [(mid, code) for mid, code in results if code != 200]
    if not failed:
        return "✅ 모든 모델 언로드 완료. RAM이 해제되었습니다."
    detail = ", ".join(f"{mid} ({code})" for mid, code in failed)
    return (
        f"일부 모델 언로드 실패: {detail}\n"
        f"성공 {len(results) - len(failed)}개 / 실패 {len(failed)}개"
    )

async def do_unload_model(client, chat_id, model_id: str = ""):
    await send_chat_action(client, chat_id, "typing")
    try:
        results: list[tuple[str, int]] | None = None
        async with _server_client() as lc:
            if model_id:
                res = await lc.delete(f"{BASE_URL}/models/unload/{model_id}", timeout=15.0)
            else:
                # Unload all: keep every delete's real status. Discarding them
                # for a synthesized 200 reported "모든 모델 언로드 완료" even
                # when a model refused to unload.
                res = await lc.get(MODELS_URL, timeout=5.0)
                if res.status_code == 200:
                    results = []
                    for mid in res.json().get("loaded") or []:
                        deleted = await lc.delete(f"{BASE_URL}/models/unload/{mid}", timeout=15.0)
                        results.append((mid, deleted.status_code))
        if results is not None:
            await send_message(client, chat_id, _unload_all_report(results))
        elif res.status_code == 200:
            await send_message(client, chat_id, f"✅ {model_id} 언로드 완료. RAM이 해제되었습니다.")
        else:
            await send_message(client, chat_id, f"언로드 실패 ({res.status_code})")
    except Exception as e:
        await send_message(client, chat_id, f"언로드 오류: {e}")

# ── Knowledge Graph stats ─────────────────────────────────────────────────────

async def show_graph_stats(client, chat_id):
    await send_chat_action(client, chat_id, "typing")
    try:
        async with _server_client() as lc:
            res = await lc.get(GRAPH_STATS_URL, timeout=5.0)
            data = res.json() if res.status_code == 200 else {}
    except Exception:
        data = {}

    nodes = data.get("nodes") or {}
    edges = data.get("edges") or {}
    total_nodes = sum(nodes.values())
    total_edges = sum(edges.values())

    node_lines = "\n".join(f"  {t}: {c}" for t, c in sorted(nodes.items(), key=lambda x: -x[1])) or "  없음"
    edge_lines  = "\n".join(f"  {t}: {c}" for t, c in sorted(edges.items(), key=lambda x: -x[1])[:8]) or "  없음"

    text = (
        f"🕸 Knowledge Graph 통계\n\n"
        f"노드 총 {total_nodes}개:\n{node_lines}\n\n"
        f"엣지 총 {total_edges}개:\n{edge_lines}\n\n"
        f"그래프 보기: {get_graph_url()}"
    )
    markup = {
        "inline_keyboard": [[
            {"text": "🔗 그래프 열기", "url": get_graph_url()},
            {"text": "↩ 메뉴로", "callback_data": "cmd:menu"},
        ]]
    }
    await send_message(client, chat_id, text, reply_markup=markup)

# ── Screenshot ────────────────────────────────────────────────────────────────

async def take_screenshot(client, chat_id):
    await send_chat_action(client, chat_id, "upload_photo")
    # mkstemp, not mktemp: mktemp only predicts an unused name, leaving a
    # window in which anything can create that path first. mkstemp creates
    # the file atomically with 0600.
    _fd, _name = tempfile.mkstemp(suffix=".jpg")
    os.close(_fd)
    tmp = Path(_name)
    try:
        proc = await asyncio.create_subprocess_exec(
            "screencapture", "-x", str(tmp),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.communicate(), timeout=10.0)
        if tmp.exists() and tmp.stat().st_size > 0:
            await send_photo(client, chat_id, tmp, caption="현재 화면입니다.")
        else:
            await send_message(client, chat_id, "스크린샷 파일이 생성되지 않았습니다. screencapture가 설치되어 있는지 확인하세요.")
    except asyncio.TimeoutError:
        await send_message(client, chat_id, "스크린샷 시간 초과")
    except FileNotFoundError:
        await send_message(client, chat_id, "screencapture 명령이 없습니다. macOS에서만 동작합니다.")
    except Exception as e:
        await send_message(client, chat_id, f"스크린샷 오류: {e}")
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            quiet()

# ── History ───────────────────────────────────────────────────────────────────

async def show_history_summary(client, chat_id, n: int = 5):
    await send_chat_action(client, chat_id, "typing")
    try:
        async with _server_client() as lc:
            res = await lc.get(HISTORY_URL, timeout=10.0)
            items = res.json() if res.status_code == 200 else []
    except Exception:
        items = []

    if not items:
        await send_message(client, chat_id, "저장된 대화 기록이 없습니다.")
        return

    recent = [i for i in items if i.get("role") == "user"][-n:]
    lines = [f"📜 최근 사용자 메시지 {len(recent)}건\n"]
    for item in recent:
        ts  = str(item.get("timestamp", ""))[:16]
        src = item.get("source", "web")
        content = str(item.get("content", ""))[:120].replace("\n", " ")
        lines.append(f"[{ts}] ({src}) {content}")
    await send_message(client, chat_id, "\n".join(lines))

async def clear_server_history(client, chat_id, keep_last=0):
    try:
        async with _server_client() as lc:
            res = await lc.delete(HISTORY_URL, params={"keep_last": keep_last}, timeout=10.0)
            data = res.json() if res.headers.get("content-type", "").startswith("application/json") else {}
        if res.status_code == 200:
            await send_message(client, chat_id, f"대화 기록을 정리했습니다. 삭제 {data.get('removed', 0)}개, 유지 {data.get('kept', 0)}개.")
        else:
            await send_message(client, chat_id, f"대화 기록 정리 실패: {res.status_code}")
    except Exception as e:
        await send_message(client, chat_id, f"대화 기록 정리 오류: {e}")

# ── Web UI link ───────────────────────────────────────────────────────────────

async def send_web_link(client, chat_id):
    web_url = get_web_url()
    text = (
        "웹 UI 링크입니다.\n"
        f"{web_url}\n\n"
        "핸드폰이 Mac과 같은 Wi-Fi에 있어야 바로 열립니다. "
        "외부망에서 쓰려면 LATTICEAI_PUBLIC_URL에 터널 주소를 설정하세요."
    )
    payload = {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": {
            "inline_keyboard": [[
                {"text": "Lattice AI Web 열기", "url": web_url},
                {"text": "Knowledge Graph", "url": get_graph_url()},
            ]]
        },
    }
    try:
        # The Telegram client, like every other helper here. Sending this on the
        # server client shipped the local bearer capability to api.telegram.org
        # and failed outright whenever that token was unset.
        await client.post(f"{API_URL}/sendMessage", json=payload)
    except Exception as e:
        logger.error("웹 링크 전송 실패: %s", safe_log_text(e))

# ── MCP tools ─────────────────────────────────────────────────────────────────

async def send_mcp_tools(client, chat_id):
    try:
        async with _server_client() as lc:
            res = await lc.get(MCP_TOOLS_URL, timeout=10.0)
            if res.status_code != 200:
                await send_message(client, chat_id, f"MCP 도구 목록을 가져오지 못했습니다: {res.status_code}")
                return
            data = res.json()
        names = [tool["name"] for tool in data.get("tools", [])]
        await send_message(client, chat_id, "사용 가능한 MCP 도구:\n" + ("\n".join(f"- {n}" for n in names) or "없음"))
    except Exception as e:
        await send_message(client, chat_id, f"MCP 도구 조회 실패: {e}")

# ── Document upload → knowledge graph ────────────────────────────────────────

async def process_document_file(client, chat_id, file_id: str, filename: str, caption: str = ""):
    await send_chat_action(client, chat_id, "upload_document")
    raw = await download_telegram_file(client, file_id)
    if not raw:
        await send_message(client, chat_id, "파일 다운로드 실패")
        return

    suffix = Path(filename).suffix.lower()
    allowed = {".pdf", ".docx", ".xlsx", ".pptx", ".txt", ".md", ".csv"}
    if suffix not in allowed:
        await send_message(client, chat_id,
                           f"지원하지 않는 파일 형식입니다({suffix}). "
                           f"지원 형식: {', '.join(sorted(allowed))}")
        return

    _fd, _name = tempfile.mkstemp(suffix=suffix)  # see take_screenshot
    os.close(_fd)
    tmp = Path(_name)
    try:
        tmp.write_bytes(raw)
        async with _server_client() as lc:
            res = await lc.post(
                UPLOAD_DOC_URL,
                files={"file": (filename, raw)},
                timeout=60.0,
            )
        if res.status_code == 200:
            data = res.json()
            chars   = data.get("chars") or len(raw)
            preview = str(data.get("preview") or "")[:300]
            kg      = data.get("knowledge_graph") or {}
            node_id = kg.get("node_id", "")
            text = (
                f"✅ {filename} 수집 완료\n"
                f"크기: {len(raw) // 1024} KB | 문자: {chars}\n"
                f"노드: {node_id}\n"
                f"\n미리보기:\n{preview}"
            )
            await send_message(client, chat_id, text)
        else:
            err = res.json().get("detail") if res.headers.get("content-type", "").startswith("application/json") else res.text
            await send_message(client, chat_id, f"업로드 실패 ({res.status_code}): {err}")
    except Exception as e:
        await send_message(client, chat_id, f"문서 처리 오류: {e}")
    finally:
        tmp.unlink(missing_ok=True)

# ── AI chat ───────────────────────────────────────────────────────────────────

async def ask_ai(client, message, image_data=None, agent_mode=False,
                 planning_model=None, executing_model=None, reviewing_model=None):
    try:
        if agent_mode and not image_data:
            url = AGENT_URL
            payload = {
                "message": message, "source": "telegram",
                "human_in_loop": True,
                "planning_model": planning_model,
                "executing_model": executing_model,
                "reviewing_model": reviewing_model,
            }
        else:
            url = CHAT_URL
            payload = {"message": message, "source": "telegram", "stream": False}
            if image_data:
                payload["image_data"] = image_data
        async with _server_client() as sc:
            res = await sc.post(url, json=payload, timeout=300.0)
        if res.status_code == 200:
            ct = res.headers.get("content-type", "")
            if "text/event-stream" in ct:
                text = ""
                for line in res.text.splitlines():
                    if line.startswith("data:"):
                        try:
                            chunk = json.loads(line[5:].strip()).get("chunk", "")
                            text += chunk
                        except Exception:
                            quiet()
                return {"response": text.strip() or "⚠️ 빈 응답"}
            return res.json()
        try:
            detail = res.json().get("detail", "")
        except Exception:
            detail = ""
        if res.status_code == 400 and "model" in detail.lower():
            return {"response": "⚠️ 로드된 모델이 없습니다. 먼저 /model 명령으로 모델을 선택해주세요."}
        return {"response": f"❌ 서버 에러 ({res.status_code}){': ' + detail if detail else ''}"}
    except Exception as e:
        return {"response": f"❌ 서버 연결 실패: {e}"}

def resolve_workspace_file(relative_path):
    target = (AGENT_WORKSPACE / relative_path).resolve()
    if target != AGENT_WORKSPACE and AGENT_WORKSPACE not in target.parents:
        return None
    if not target.exists() or not target.is_file():
        return None
    if target.stat().st_size > MAX_TELEGRAM_FILE_BYTES:
        return None
    return target

def collect_generated_files(agent_data):
    files, seen = [], set()
    for step in agent_data.get("steps", []):
        if step.get("action") not in {"write_file", "create_docx", "create_xlsx", "create_pptx", "create_pdf"}:
            continue
        path = (step.get("result") or {}).get("path") or (step.get("args") or {}).get("path")
        if not path or path in seen:
            continue
        target = resolve_workspace_file(path)
        if target:
            seen.add(path)
            files.append((path, target))
    return files

def format_artifact_card(data, *, language: str = "ko") -> str:
    """Artifact card summary for a finished run (v10.4.0).

    Telegram used to send the produced files and nothing else, so a
    deterministically repaired scaffold arrived looking exactly like clean
    model output — SURFACE_PARITY recorded that as ◐. This renders the same
    ``artifacts[]`` flags the web cards show, and reports only what the server
    said: an absent flag is never promoted into a claim of validity.
    """
    if not isinstance(data, dict):
        return ""
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        return ""
    korean = not str(language or "ko").startswith("en")
    lines = ["📄 만든 파일" if korean else "📄 Files produced"]
    for item in artifacts[:8]:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        notes = []
        size = item.get("bytes")
        if isinstance(size, (int, float)) and size > 0:
            notes.append(f"{int(size):,} B")
        if item.get("repaired") is True:
            notes.append("자동 보정됨" if korean else "auto-repaired")
        if item.get("valid") is False:
            notes.append("검증 실패" if korean else "failed validation")
        suffix = f" ({' · '.join(notes)})" if notes else ""
        lines.append(f"• {path}{suffix}")
    extra = len(artifacts) - 8
    if extra > 0:
        lines.append(f"… +{extra}")
    return "\n".join(lines) if len(lines) > 1 else ""


async def send_artifact_card(client, chat_id, data, *, language: str = "ko") -> None:
    text = format_artifact_card(data, language=language)
    if text:
        await send_message(client, chat_id, text)


def format_grounding_badge(data, *, language: str = "ko") -> str:
    """Answer-grounding badge line for a `/chat` reply (v9.9.7).

    Telegram used to show only the answer text, so a reply the Brain could not
    ground looked identical to one built on real sources. This renders exactly
    the verdict the server issued — and reports an absent verdict as unknown
    rather than promoting it to "근거 있음".
    """
    if not isinstance(data, dict):
        return ""
    grounding = data.get("grounding")
    if not isinstance(grounding, dict):
        return ""
    status = str(grounding.get("status") or "")
    if not status:
        return ""
    korean = not str(language or "ko").startswith("en")
    cited = [
        str(item.get("title") or "").strip()
        for item in (grounding.get("cited") or [])
        if isinstance(item, dict) and str(item.get("title") or "").strip()
    ]
    if status == "supported":
        head = "✅ 근거 있음" if korean else "✅ grounded"
        if cited:
            head += " — " + ", ".join(cited[:3])
        return head
    if status in {"unsupported", "no_context"}:
        head = "⚠️ 근거 없음" if korean else "⚠️ not grounded"
        reason = str(grounding.get("reason") or "").strip()
        return f"{head} — {reason}" if reason else head
    return "❔ 근거 확인 불가" if korean else "❔ grounding unknown"


async def send_grounding_badge(client, chat_id, data):
    text = format_grounding_badge(data)
    if text:
        await send_message(client, chat_id, text)


def format_proposals(payload) -> list:
    """`GET /api/proposals` → ``[(item_id, label)]`` for the Review Center.

    Rows without an id are dropped: an un-actionable row is worse than none.
    """
    root = payload if isinstance(payload, dict) else {}
    rows = payload if isinstance(payload, list) else (root.get("items") or [])
    out = []
    for raw in rows if isinstance(rows, list) else []:
        if not isinstance(raw, dict):
            continue
        item_id = str(raw.get("id") or "").strip()
        if not item_id:
            continue
        raw_body = raw.get("payload")
        raw_prov = raw.get("provenance")
        body: Dict[str, Any] = raw_body if isinstance(raw_body, dict) else {}
        provenance: Dict[str, Any] = raw_prov if isinstance(raw_prov, dict) else {}
        path = str(body.get("path") or provenance.get("path") or "")
        title = str(raw.get("title") or path or item_id)
        change_class = str(body.get("change_class") or provenance.get("change_class") or "")
        label = title if not change_class else f"{title} ({change_class})"
        out.append((item_id, label))
    return out


async def show_review_center(client, chat_id):
    """Review Center parity (v9.9.7): staged change proposals with inline
    approve/reject, using the same `/api/proposals` surface as the web app."""
    try:
        async with _server_client() as sc:
            res = await sc.get(PROPOSALS_URL, timeout=30.0)
        if res.status_code != 200:
            await send_message(client, chat_id, f"검토함을 불러오지 못했습니다 ({res.status_code})")
            return
        items = format_proposals(res.json())
    except Exception as exc:
        await send_message(client, chat_id, f"검토함 조회 실패: {exc}")
        return
    if not items:
        await send_message(client, chat_id, "🗂 검토할 변경 제안이 없습니다.")
        return
    lines = ["🗂 변경 제안 (승인해야 적용됩니다)"]
    keyboard = []
    for item_id, label in items[:8]:
        lines.append(f"- {label}")
        keyboard.append([
            {"text": f"✅ 승인 — {label}"[:64], "callback_data": f"proposal:approve:{item_id}"[:64]},
            {"text": "❌ 거절"[:64], "callback_data": f"proposal:reject:{item_id}"[:64]},
        ])
    if len(items) > 8:
        lines.append(f"… 외 {len(items) - 8}건")
    await send_message(
        client, chat_id, "\n".join(lines),
        reply_markup={"inline_keyboard": keyboard},
    )


async def handle_proposal_callback(client, chat_id, data: str):
    """Apply or reject one staged proposal.

    A 409 means the target file changed since staging — nothing was written,
    and the user is told exactly that instead of a silent retry.
    """
    try:
        _, decision, item_id = data.split(":", 2)
    except ValueError:
        return
    if not item_id:
        return
    suffix = "approve" if decision == "approve" else "reject"
    url = f"{PROPOSALS_URL}/{item_id}/{suffix}"
    try:
        async with _server_client() as sc:
            res = await sc.post(url, json={}, timeout=120.0)
    except Exception as exc:
        await send_message(client, chat_id, f"❌ 처리 실패: {exc}")
        return
    if res.status_code == 409:
        await send_message(
            client, chat_id,
            "⚠️ 제안을 만든 뒤 파일이 바뀌어서 적용하지 않았습니다. 아무것도 쓰지 않았습니다.",
        )
        return
    if res.status_code != 200:
        await send_message(client, chat_id, f"❌ 서버 에러 ({res.status_code})")
        return
    if suffix == "approve":
        try:
            applied = res.json().get("path") or item_id
        except Exception:
            applied = item_id
        await send_message(client, chat_id, f"✅ 적용했습니다: {applied}")
    else:
        await send_message(client, chat_id, "🚫 제안을 거절했습니다.")


def format_run_explanation(agent_data, *, language: str = "ko") -> str:
    """Plain-language outcome line for a finished agent run (v9.9.6).

    Telegram used to show only the final message, so a NEEDS_REVIEW run read
    exactly like a success. The server already computes the honest sentence
    (`explanation`); this renders it. Returns "" when there is nothing to add
    — a clean, verified run gets no extra noise.
    """
    if not isinstance(agent_data, dict):
        return ""
    explanation = agent_data.get("explanation")
    if not isinstance(explanation, dict):
        return ""
    lang = "ko" if str(language or "ko").startswith("ko") else "en"

    def pick(entry):
        return str(entry.get(lang) or "") if isinstance(entry, dict) else ""

    details = [pick(item) for item in explanation.get("details") or []]
    details = [line for line in details if line]
    if explanation.get("ok") and not details:
        return ""
    headline = pick(explanation.get("headline"))
    marker = "✅" if explanation.get("ok") else "⚠️"
    lines = [f"{marker} {headline}" if headline else marker]
    lines.extend(f"· {line}" for line in details[:4])
    return "\n".join(lines)


async def send_run_explanation(client, chat_id, agent_data):
    text = format_run_explanation(agent_data)
    if text:
        await send_message(client, chat_id, text)


def collect_preview_urls(agent_data):
    urls, seen = [], set()
    for step in agent_data.get("steps", []):
        if step.get("action") != "preview_url":
            continue
        result = step.get("result") or {}
        local_url = result.get("local_url")
        if not local_url or local_url in seen:
            continue
        phone_url = local_url.replace("http://127.0.0.1:4825", f"http://{get_lan_ip()}:{SERVER_PORT}")
        seen.add(local_url)
        urls.append((result.get("path") or "preview", phone_url))
    return urls

async def send_preview_links(client, chat_id, preview_urls):
    if not preview_urls:
        return
    lines = ["미리보기 링크 (Mac과 같은 Wi-Fi 필요):"]
    keyboard = []
    for label, url in preview_urls:
        lines.append(f"- {label}: {url}")
        keyboard.append([{"text": f"{label} 열기"[:64], "url": url}])
    await send_message(client, chat_id, "\n".join(lines), reply_markup={"inline_keyboard": keyboard[:8]})

async def send_generated_files(client, chat_id, generated_files):
    if not generated_files:
        return
    if len(generated_files) == 1:
        path, fpath = generated_files[0]
        await send_document(client, chat_id, fpath, caption=f"생성 파일: {path}")
        return
    with tempfile.NamedTemporaryFile(prefix="ltcai-", suffix=".zip", delete=False) as tmp:
        zip_path = Path(tmp.name)
    try:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for rel, fpath in generated_files:
                zf.write(fpath, arcname=rel)
        if zip_path.stat().st_size <= MAX_TELEGRAM_FILE_BYTES:
            await send_document(client, chat_id, zip_path,
                                caption=f"생성 파일 {len(generated_files)}개", filename="ltcai-files.zip")
        else:
            await send_message(client, chat_id, "생성 파일이 너무 커서 전송할 수 없습니다.")
    finally:
        zip_path.unlink(missing_ok=True)

# ── Plan approval (Human-in-the-loop) ────────────────────────────────────────

def _approval_pause_id(data: dict) -> str:
    """Stable key for a paused plan: run_id preferred, legacy context_id fallback."""
    return str(data.get("run_id") or data.get("context_id") or "")


def _is_approval_pause(data: dict) -> bool:
    status = str(data.get("status") or "")
    return status in {"waiting_approval", "awaiting_approval"}


async def send_plan_for_approval(client, chat_id, data: dict) -> None:
    """Show the agent plan to the user and present Done/Cancel buttons.

    Handles both the modern ``awaiting_approval`` (run_id + token) and the
    legacy ``waiting_approval`` / ``context_id`` wire contracts (v9.9.5
    SURFACE_PARITY — Telegram approval flow).
    """
    pause_id = _approval_pause_id(data)
    if not pause_id:
        await send_message(client, chat_id, "❌ 승인할 계획을 식별할 수 없습니다.")
        return
    plan = data.get("plan", {}) or {}
    goal = plan.get("goal") or (data.get("approval") or {}).get("plan_summary") or ""
    steps = plan.get("steps", []) or data.get("non_auto_steps") or []
    p_model = data.get("planning_model", "current")
    e_model = data.get("executing_model", "current")
    r_model = data.get("reviewing_model", "current")
    approval = data.get("approval") or {}
    expires_at = approval.get("expires_at")

    lines = ["📋 *플래닝 완료* — 실행 전 확인해주세요\n"]
    if goal:
        lines.append(f"*목표:* {goal}\n")
    for i, step in enumerate(steps, 1):
        if isinstance(step, dict):
            desc = step.get("description") or step.get("action") or str(step)
        else:
            desc = str(step)
        lines.append(f"{i}. {desc}")
    lines.append(f"\n🧠 플래닝: `{p_model}`")
    lines.append(f"⚙️ 실행: `{e_model}`")
    lines.append(f"🔍 검토: `{r_model}`")
    if expires_at:
        lines.append(f"⏳ 승인 만료: `{expires_at}`")

    _bot_pending_plans[pause_id] = {
        "chat_id": chat_id,
        "run_id": data.get("run_id") or pause_id,
        "context_id": data.get("context_id"),
        "approval_token": approval.get("token"),
        "legacy": data.get("status") == "waiting_approval" or bool(data.get("context_id")),
        "executing_model": data.get("executing_model"),
        "reviewing_model": data.get("reviewing_model"),
    }

    # callback_data max 64 bytes — pause ids are token_urlsafe(16) (~22 chars).
    keyboard = {"inline_keyboard": [[
        {"text": "✅ Done — 실행 시작", "callback_data": f"plan:approve:{pause_id}"},
        {"text": "❌ 취소", "callback_data": f"plan:cancel:{pause_id}"},
    ]]}
    await send_message(client, chat_id, "\n".join(lines), reply_markup=keyboard)


async def handle_plan_callback(client, chat_id, data: str) -> None:
    """Handle Done/Cancel callback from plan approval buttons."""
    parts = data.split(":", 2)
    if len(parts) != 3:
        return
    _, action, pause_id = parts
    pending = _bot_pending_plans.get(pause_id)

    # A callback is bound to the chat that received the plan. Even two allowed
    # chats cannot approve or cancel each other's plan by replaying callback
    # data.
    if pending and int(pending.get("chat_id") or 0) != int(chat_id):
        await send_message(client, chat_id, "❌ 다른 채팅의 작업은 처리할 수 없습니다.")
        return
    pending = _bot_pending_plans.pop(pause_id, None)

    if action == "cancel":
        if pending:
            try:
                resume_body = _resume_payload(pending, approved=False)
                async with _server_client() as sc:
                    await sc.post(AGENT_RESUME_URL, json=resume_body, timeout=30.0)
            except Exception as exc:  # noqa: BLE001 — cancel is best-effort
                logger.warning("telegram cancel resume failed: %s", safe_log_text(exc))
        await send_message(client, chat_id, "❌ 작업이 취소되었습니다.")
        return
    if not pending:
        await send_message(client, chat_id, "❌ 작업이 취소되었습니다.")
        return

    await send_message(client, chat_id, "⚙️ 실행 중입니다. 잠시 기다려주세요...")
    await send_chat_action(client, chat_id, "typing")

    try:
        async with _server_client() as sc:
            res = await sc.post(
                AGENT_RESUME_URL,
                json=_resume_payload(pending, approved=True),
                timeout=300.0,
            )
        data_r = res.json() if res.status_code == 200 else {}
        ans = data_r.get("response", f"❌ 서버 에러 ({res.status_code})")
        await send_message(client, chat_id, str(ans))
        if isinstance(data_r, dict):
            await send_run_explanation(client, chat_id, data_r)
            await send_artifact_card(client, chat_id, data_r)
            await send_generated_files(client, chat_id, collect_generated_files(data_r))
            await send_preview_links(client, chat_id, collect_preview_urls(data_r))
    except Exception as e:
        await send_message(client, chat_id, f"❌ 실행 중 오류: {e}")


def _resume_payload(pending: dict, *, approved: bool) -> dict:
    """Build /agent/resume body: token path preferred, else legacy context_id."""
    body: dict = {
        "approved": approved,
        "executing_model": pending.get("executing_model"),
        "reviewing_model": pending.get("reviewing_model"),
    }
    token = pending.get("approval_token")
    run_id = pending.get("run_id")
    # Unified durable store (9.9.5): token-gated resume works for both the
    # modern awaiting_approval and the legacy human_in_loop pause.
    if token and run_id:
        body["run_id"] = run_id
        body["approval_token"] = token
        return body
    body["context_id"] = pending.get("context_id") or run_id
    return body


# ── AI request task ───────────────────────────────────────────────────────────

async def process_ai_request(client, chat_id, user_text, image_data=None):
    try:
        await send_chat_action(client, chat_id, "upload_photo" if image_data else "typing")
        logger.info("ask_ai 호출 시작: chat_id=%s text=%r", chat_id, safe_log_text(user_text[:30]))
        data  = await ask_ai(client, user_text, image_data, agent_mode=not image_data)
        logger.info("ask_ai 완료: chat_id=%s result_keys=%s", chat_id, list(data.keys()) if isinstance(data, dict) else type(data))

        # Approval pause (legacy waiting_approval or modern awaiting_approval)
        if isinstance(data, dict) and _is_approval_pause(data):
            await send_plan_for_approval(client, chat_id, data)
            return

        ans   = data.get("response", str(data)) if isinstance(data, dict) else str(data)
        if not ans or not str(ans).strip():
            ans = "⚠️ AI가 답변을 생성하지 못했습니다."
        await send_message(client, chat_id, str(ans))
        if isinstance(data, dict):
            # Recall parity (v9.9.7): the same grounding verdict the web app
            # badges, never promoted when the server issued none.
            await send_grounding_badge(client, chat_id, data)
        if not image_data and isinstance(data, dict):
            await send_run_explanation(client, chat_id, data)
            await send_artifact_card(client, chat_id, data)
            await send_generated_files(client, chat_id, collect_generated_files(data))
            await send_preview_links(client, chat_id, collect_preview_urls(data))
    except Exception as e:
        logger.error("process_ai_request 실패 (chat_id=%s): %s", chat_id, safe_log_text(e), exc_info=True)
        try:
            await send_message(client, chat_id, "⚠️ 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")
        except Exception:
            quiet()

# ── Command dispatch ──────────────────────────────────────────────────────────

HELP_TEXT = """\
🧠 Lattice AI 원격 제어 명령어

/menu — 메인 메뉴 (인라인 키보드)
/status — 서버 상태 및 메모리
/model — 현재 모델 + 언로드 버튼
/unload — 모든 모델 언로드 (RAM 해제)
/graph — Knowledge Graph 통계
/ss 또는 /screenshot — 현재 화면 캡처
/history [n] — 최근 대화 n건 (기본 5)
/clear [n] — 기록 정리 (마지막 n건 유지)
/web — 웹 UI 링크
/mcp — MCP 도구 목록
/review — 변경 제안 검토함 (승인/거절)
/help — 이 도움말

/agent <작업> — 멀티 LLM 에이전트 (계획 확인 후 실행)
/agent <작업> --exec <모델> --review <모델> — 실행/검토 LLM 지정

일반 텍스트 → AI에게 질문
사진 전송 → AI 이미지 분석
문서 전송(PDF, DOCX, XLSX, PPTX, TXT, CSV) → Knowledge Graph 수집
"""

async def handle_command(client, chat_id, command: str, args: str):
    cmd = command.lower().lstrip("/").split("@")[0]

    if cmd == "start":
        await send_message(client, chat_id, "🧠 Lattice AI 원격 제어 준비 완료!")
        await show_menu(client, chat_id)
    elif cmd == "menu":
        await show_menu(client, chat_id)
    elif cmd == "status":
        await show_status(client, chat_id)
    elif cmd == "model":
        await show_model_info(client, chat_id)
    elif cmd == "unload":
        await do_unload_model(client, chat_id)
    elif cmd == "graph":
        await show_graph_stats(client, chat_id)
    elif cmd in {"ss", "screenshot"}:
        await take_screenshot(client, chat_id)
    elif cmd == "history":
        n = int(args.strip()) if args.strip().isdigit() else 5
        await show_history_summary(client, chat_id, n)
    elif cmd in {"clear", "clear_history", "forget"}:
        keep = int(args.strip()) if args.strip().isdigit() else 0
        await clear_server_history(client, chat_id, keep)
    elif cmd == "web":
        await send_web_link(client, chat_id)
    elif cmd == "mcp":
        await send_mcp_tools(client, chat_id)
    elif cmd in {"review", "proposals"}:
        await show_review_center(client, chat_id)
    elif cmd in {"help", "h"}:
        await send_message(client, chat_id, HELP_TEXT)
    elif cmd == "agent":
        if not args:
            await send_message(client, chat_id, "사용법: /agent <작업 내용>\n예: /agent 쇼핑몰 메인 페이지 HTML 만들어줘\n\n특정 AI 지정:\n/agent <작업> --exec openai/gpt-4o --review together:Qwen/Qwen3-VL-32B-Instruct")
            return
        # Parse optional --exec / --review flags
        exec_model = reviewing_model = None
        task_text = args
        import re as _re
        em = _re.search(r'--exec\s+(\S+)', args)
        rm = _re.search(r'--review\s+(\S+)', args)
        if em:
            exec_model = em.group(1)
            task_text = task_text.replace(em.group(0), "").strip()
        if rm:
            reviewing_model = rm.group(1)
            task_text = task_text.replace(rm.group(0), "").strip()
        await send_chat_action(client, chat_id, "typing")
        data = await ask_ai(client, task_text, agent_mode=True,
                            executing_model=exec_model, reviewing_model=reviewing_model)
        if isinstance(data, dict) and _is_approval_pause(data):
            await send_plan_for_approval(client, chat_id, data)
        else:
            ans = data.get("response", str(data)) if isinstance(data, dict) else str(data)
            await send_message(client, chat_id, ans)
    else:
        await send_message(client, chat_id, f"알 수 없는 명령어: /{cmd}\n/help 로 명령어 목록을 확인하세요.")

# ── Callback query handler ────────────────────────────────────────────────────

async def handle_callback_query(client, callback_query):
    cq_id = callback_query.get("id", "")
    chat_id = (
        (callback_query.get("message") or {}).get("chat") or {}
    ).get("id")
    data    = callback_query.get("data", "")

    if not is_chat_allowed(chat_id):
        logger.warning("허용되지 않은 텔레그램 callback 차단: %s", safe_log_text(chat_id))
        await answer_callback(client, cq_id, "허용되지 않은 채팅입니다.")
        return

    await answer_callback(client, cq_id)

    if data == "cmd:status":
        await show_status(client, chat_id)
    elif data == "cmd:model":
        await show_model_info(client, chat_id)
    elif data == "cmd:graph":
        await show_graph_stats(client, chat_id)
    elif data == "cmd:screenshot":
        await take_screenshot(client, chat_id)
    elif data == "cmd:history":
        await show_history_summary(client, chat_id, 5)
    elif data == "cmd:clear":
        await clear_server_history(client, chat_id, 0)
    elif data == "cmd:web":
        await send_web_link(client, chat_id)
    elif data == "cmd:mcp":
        await send_mcp_tools(client, chat_id)
    elif data == "cmd:review":
        await show_review_center(client, chat_id)
    elif data == "cmd:menu":
        await show_menu(client, chat_id)
    elif data.startswith("model:unload:"):
        model_id = data[len("model:unload:"):]
        await do_unload_model(client, chat_id, model_id)
    elif data.startswith("plan:"):
        task = asyncio.create_task(handle_plan_callback(client, chat_id, data))
        task.add_done_callback(_log_task_exception)
    elif data.startswith("proposal:"):
        task = asyncio.create_task(handle_proposal_callback(client, chat_id, data))
        task.add_done_callback(_log_task_exception)

# ── Main loop ─────────────────────────────────────────────────────────────────

async def run_bot():
    if not TOKEN:
        logger.warning("LATTICEAI_TELEGRAM_BOT_TOKEN이 설정되지 않아 텔레그램 봇을 시작하지 않습니다.")
        return
    if not allowed_chat_ids():
        logger.error(
            "LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS가 없어 텔레그램 봇을 시작하지 않습니다."
        )
        return
    if not _get_server_session():
        logger.error(
            "LATTICEAI_SERVER_SESSION_TOKEN이 없어 텔레그램 봇을 시작하지 않습니다."
        )
        return

    logger.info("🚀 비동기 텔레그램 봇 모드 시작!")
    last_update_id = None
    retry_delay = 1

    async with httpx.AsyncClient() as client:
        while True:
            try:
                updates = await get_updates(client, last_update_id)
                retry_delay = 1
            except Exception as e:
                logger.error("get_updates 실패: %s", safe_log_text(e))
                await asyncio.sleep(min(retry_delay, 30))
                retry_delay = min(retry_delay * 2, 30)
                continue

            if not (updates and updates.get("ok")):
                await asyncio.sleep(0.5)
                continue

            for update in updates.get("result", []):
                try:
                    last_update_id = update.get("update_id") + 1

                    # ── Callback query (inline button press) ──────────────────
                    if "callback_query" in update:
                        task = asyncio.create_task(handle_callback_query(client, update["callback_query"]))
                        task.add_done_callback(_log_task_exception)
                        continue

                    if "message" not in update:
                        continue

                    msg     = update["message"]
                    chat_id = msg["chat"]["id"]
                    if not is_chat_allowed(chat_id):
                        logger.warning(
                            "허용되지 않은 텔레그램 메시지 차단: %s",
                            safe_log_text(chat_id),
                        )
                        continue
                    register_chat_id(chat_id)
                    text    = msg.get("text", "")
                    caption = msg.get("caption", "")

                    # ── Photo → vision AI ─────────────────────────────────────
                    if "photo" in msg:
                        file_id = msg["photo"][-1]["file_id"]
                        await send_message(client, chat_id, "📸 사진을 받았습니다. 분석을 시작합니다...")
                        image_data = await download_as_base64(client, file_id)
                        prompt = caption or text or "이 이미지를 분석해줘."
                        task = asyncio.create_task(process_ai_request(client, chat_id, prompt, image_data))
                        task.add_done_callback(_log_task_exception)
                        continue

                    # ── Document ──────────────────────────────────────────────
                    if "document" in msg:
                        doc      = msg["document"]
                        mime     = doc.get("mime_type", "")
                        filename = doc.get("file_name", "file")
                        if mime.startswith("image/"):
                            image_data = await download_as_base64(client, doc["file_id"])
                            prompt = caption or text or "이 이미지를 분석해줘."
                            task = asyncio.create_task(process_ai_request(client, chat_id, prompt, image_data))
                        else:
                            await send_message(client, chat_id, f"📄 {filename} 을 Knowledge Graph에 수집합니다...")
                            task = asyncio.create_task(
                                process_document_file(client, chat_id, doc["file_id"], filename, caption)
                            )
                        task.add_done_callback(_log_task_exception)
                        continue

                    # ── Voice / audio ─────────────────────────────────────────
                    if "voice" in msg or "audio" in msg:
                        await send_message(
                            client, chat_id,
                            "🎤 음성 메시지를 받았습니다. 현재 음성 인식(Whisper)이 설정되어 있지 않습니다.\n"
                            "텍스트로 질문을 보내주세요."
                        )
                        continue

                    if not text:
                        continue

                    # ── Commands ──────────────────────────────────────────────
                    if text.startswith("/"):
                        parts   = text.split(None, 1)
                        command = parts[0]
                        args    = parts[1] if len(parts) > 1 else ""
                        task = asyncio.create_task(handle_command(client, chat_id, command, args))
                        task.add_done_callback(_log_task_exception)
                        continue

                    # ── Plain text → AI ───────────────────────────────────────
                    task = asyncio.create_task(process_ai_request(client, chat_id, text))
                    task.add_done_callback(_log_task_exception)

                except Exception as e:
                    logger.error("업데이트 처리 중 예외: %s", safe_log_text(e))

            await asyncio.sleep(0.5)

def _log_task_exception(task):
    if not task.cancelled() and task.exception():
        logger.error("백그라운드 태스크 예외: %s", safe_log_text(task.exception()))

if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        quiet()
