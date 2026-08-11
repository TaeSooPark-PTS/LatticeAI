"""The bridge's primitives: who may talk to it, how it talks back, and what it
can hand over.

Four small layers that everything above depends on and that depend on nothing
above:

* **chat-id registry** — the fail-closed allowlist plus the persisted mirror
  recipients (``LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS`` denies all when unset);
* **Telegram API** — the six outbound calls (message, photo, document, chat
  action, callback answer, message edit) and the long-poll read;
* **network** — where this machine is reachable from a phone;
* **artifacts** — resolving agent-produced files inside the workspace root and
  shipping them (or a zip of them) back to the chat.

Stubbing note: these functions read ``TOKEN``, ``CHAT_IDS_FILE``,
``AGENT_WORKSPACE``, ``MAX_TELEGRAM_FILE_BYTES``, ``SERVER_PORT``,
``PUBLIC_WEB_URL``, ``INVITE_CODE`` and ``atomic_write_json`` as *this* module's
globals, so a test standing in for any of them patches this module.
"""

import asyncio
import base64
import json
import socket
import tempfile
import zipfile
from pathlib import Path

import httpx

from latticeai.core.io_utils import atomic_write_json
from latticeai.core.logging_safety import safe_log_text
from latticeai.core.quiet import quiet

from .config import (
    AGENT_WORKSPACE,
    API_URL,
    CHAT_IDS_FILE,
    INVITE_CODE,
    MAX_TELEGRAM_FILE_BYTES,
    PUBLIC_WEB_URL,
    SERVER_PORT,
    TOKEN,
    env_value,
    logger,
)

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

# ── Agent artifacts (workspace files and preview links) ──────────────────────

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
