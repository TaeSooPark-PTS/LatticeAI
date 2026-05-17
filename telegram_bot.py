import asyncio
import httpx
import logging
import base64
import os
import socket
import tempfile
import zipfile
import json
from pathlib import Path

def load_env_file(path=".env"):
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

load_env_file()

def env_value(primary: str, default: str = "") -> str:
    return os.getenv(primary) or default

# 설정
TOKEN = env_value("LATTICEAI_TELEGRAM_BOT_TOKEN")
API_URL = f"https://api.telegram.org/bot{TOKEN}"
CHAT_URL = "http://127.0.0.1:4825/chat"
AGENT_URL = "http://127.0.0.1:4825/agent"
MCP_TOOLS_URL = "http://127.0.0.1:4825/mcp/tools"
HISTORY_URL = "http://127.0.0.1:4825/history"
AGENT_WORKSPACE = Path(env_value("LATTICEAI_AGENT_ROOT", "agent_workspace")).resolve()
MAX_TELEGRAM_FILE_BYTES = 45 * 1024 * 1024
SERVER_PORT = int(env_value("LATTICEAI_SERVER_PORT", "4825"))
INVITE_CODE = env_value("LATTICEAI_INVITE_CODE", "gemma-lattice-ai")
PUBLIC_WEB_URL = env_value("LATTICEAI_PUBLIC_URL")
DATA_DIR = Path(env_value("LATTICEAI_DATA_DIR", str(Path.home() / ".ltcai")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
CHAT_IDS_FILE = Path(env_value("LATTICEAI_TELEGRAM_CHATS_FILE", str(DATA_DIR / "telegram_chats.json")))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def load_chat_ids():
    try:
        if CHAT_IDS_FILE.exists():
            data = json.loads(CHAT_IDS_FILE.read_text(encoding="utf-8"))
            return {int(chat_id) for chat_id in data.get("chat_ids", [])}
    except Exception as e:
        logger.error(f"텔레그램 채팅 목록 로드 실패: {e}")
    return set()

def save_chat_ids(chat_ids):
    try:
        CHAT_IDS_FILE.write_text(
            json.dumps({"chat_ids": sorted(chat_ids)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.error(f"텔레그램 채팅 목록 저장 실패: {e}")

def register_chat_id(chat_id):
    chat_ids = load_chat_ids()
    if chat_id in chat_ids:
        return
    chat_ids.add(chat_id)
    save_chat_ids(chat_ids)
    logger.info(f"텔레그램 웹 미러링 대상 등록: {chat_id}")

async def broadcast_web_chat(role, text):
    if not TOKEN:
        logger.info("LATTICEAI_TELEGRAM_BOT_TOKEN이 없어 웹 대화 텔레그램 미러링을 건너뜁니다.")
        return

    chat_ids = load_chat_ids()
    if not chat_ids:
        logger.info("웹 대화 미러링 대상 텔레그램 채팅이 없습니다. 봇에 /start 또는 /web을 먼저 보내세요.")
        return

    label = "사용자" if role == "user" else "Lattice AI"
    message = f"[Web] {label}\n{text}"

    async with httpx.AsyncClient() as client:
        for chat_id in chat_ids:
            await send_message(client, chat_id, message)

async def get_updates(client, offset=None):
    url = f"{API_URL}/getUpdates?timeout=30"
    if offset:
        url += f"&offset={offset}"
    try:
        res = await client.get(url, timeout=35)
        return res.json()
    except Exception as e:
        return None

async def send_message(client, chat_id, text):
    url = f"{API_URL}/sendMessage"
    try:
        chunks = [text[i:i + 3900] for i in range(0, len(text), 3900)] or [""]
        for chunk in chunks:
            await client.post(url, json={"chat_id": chat_id, "text": chunk})
    except Exception as e:
        logger.error(f"메시지 전송 실패: {e}")

async def send_chat_action(client, chat_id, action="typing"):
    url = f"{API_URL}/sendChatAction"
    try:
        await client.post(url, json={"chat_id": chat_id, "action": action})
    except Exception as e:
        logger.error(f"채팅 액션 전송 실패: {e}")

def get_lan_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            if not ip.startswith("127."):
                return ip
    except OSError:
        pass

    try:
        hostname = socket.gethostname()
        for ip in socket.gethostbyname_ex(hostname)[2]:
            if not ip.startswith("127."):
                return ip
    except OSError:
        pass

    return "127.0.0.1"

def get_web_url():
    if PUBLIC_WEB_URL:
        return PUBLIC_WEB_URL.rstrip("/")
    return f"http://{get_lan_ip()}:{SERVER_PORT}/?code={INVITE_CODE}"

async def send_web_link(client, chat_id):
    url = f"{API_URL}/sendMessage"
    web_url = get_web_url()
    text = (
        "웹 UI 링크입니다.\n"
        f"{web_url}\n\n"
        "핸드폰이 Mac과 같은 Wi-Fi에 있어야 바로 열립니다. 외부망에서 쓰려면 LATTICEAI_PUBLIC_URL에 터널 주소를 설정하세요."
    )
    payload = {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": {
            "inline_keyboard": [[{"text": "Lattice AI Web 열기", "url": web_url}]]
        },
    }
    try:
        await client.post(url, json=payload)
    except Exception as e:
        logger.error(f"웹 링크 전송 실패: {e}")

async def send_mcp_tools(client, chat_id):
    try:
        async with httpx.AsyncClient() as local_client:
            res = await local_client.get(MCP_TOOLS_URL, timeout=10.0)
            if res.status_code != 200:
                await send_message(client, chat_id, f"MCP 도구 목록을 가져오지 못했습니다: {res.status_code}")
                return
            data = res.json()
        names = [tool["name"] for tool in data.get("tools", [])]
        await send_message(client, chat_id, "사용 가능한 로컬 MCP 도구:\n" + "\n".join(f"- {name}" for name in names))
    except Exception as e:
        await send_message(client, chat_id, f"MCP 도구 목록 조회 실패: {e}")

async def clear_server_history(client, chat_id, keep_last=0):
    try:
        async with httpx.AsyncClient() as local_client:
            res = await local_client.delete(HISTORY_URL, params={"keep_last": keep_last}, timeout=10.0)
            data = res.json() if res.headers.get("content-type", "").startswith("application/json") else {}
        if res.status_code == 200:
            await send_message(client, chat_id, f"대화 기록을 정리했습니다. 삭제 {data.get('removed', 0)}개, 유지 {data.get('kept', 0)}개.")
        else:
            await send_message(client, chat_id, f"대화 기록 정리에 실패했습니다: {res.status_code}")
    except Exception as e:
        await send_message(client, chat_id, f"대화 기록 정리 실패: {e}")

async def ask_ai(client, message, image_data=None, agent_mode=True):
    try:
        url = CHAT_URL if image_data or not agent_mode else AGENT_URL
        payload = {"message": message, "source": "telegram"}
        if image_data:
            payload["stream"] = False
            payload["image_data"] = image_data
            
        res = await client.post(url, json=payload, timeout=300.0)
        if res.status_code == 200:
            data = res.json()
            return data
        else:
            return {"response": f"❌ 서버 에러 ({res.status_code}): {res.text}"}
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
    files = []
    seen = set()
    for step in agent_data.get("steps", []):
        if step.get("action") != "write_file":
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
    urls = []
    seen = set()
    for step in agent_data.get("steps", []):
        if step.get("action") != "preview_url":
            continue
        result = step.get("result") or {}
        local_url = result.get("local_url")
        path = result.get("path")
        if not local_url or local_url in seen:
            continue
        phone_url = local_url.replace("http://127.0.0.1:4825", f"http://{get_lan_ip()}:{SERVER_PORT}")
        seen.add(local_url)
        urls.append((path or "preview", phone_url))
    return urls

async def send_preview_links(client, chat_id, preview_urls):
    if not preview_urls:
        return
    lines = ["미리보기 링크입니다. 핸드폰이 Mac과 같은 Wi-Fi에 있어야 열립니다."]
    keyboard = []
    for label, url in preview_urls:
        lines.append(f"- {label}: {url}")
        keyboard.append([{"text": f"{label} 열기"[:64], "url": url}])

    try:
        await client.post(
            f"{API_URL}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": "\n".join(lines),
                "reply_markup": {"inline_keyboard": keyboard[:8]},
            },
        )
    except Exception as e:
        logger.error(f"미리보기 링크 전송 실패: {e}")

async def send_document(client, chat_id, file_path, caption=None, filename=None):
    url = f"{API_URL}/sendDocument"
    try:
        with open(file_path, "rb") as f:
            files = {"document": (filename or Path(file_path).name, f)}
            data = {"chat_id": str(chat_id)}
            if caption:
                data["caption"] = caption[:1024]
            res = await client.post(url, data=data, files=files, timeout=300.0)
            if res.status_code != 200:
                logger.error(f"파일 전송 실패 ({res.status_code}): {res.text}")
    except Exception as e:
        logger.error(f"파일 전송 실패: {e}")

async def send_generated_files(client, chat_id, generated_files):
    if not generated_files:
        return

    if len(generated_files) == 1:
        relative_path, file_path = generated_files[0]
        await send_document(client, chat_id, file_path, caption=f"생성 파일: {relative_path}")
        return

    with tempfile.NamedTemporaryFile(prefix="ltcai-", suffix=".zip", delete=False) as temp:
        zip_path = Path(temp.name)

    try:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for relative_path, file_path in generated_files:
                zf.write(file_path, arcname=relative_path)

        if zip_path.stat().st_size <= MAX_TELEGRAM_FILE_BYTES:
            await send_document(
                client,
                chat_id,
                zip_path,
                caption=f"생성 파일 {len(generated_files)}개를 zip으로 묶었습니다.",
                filename="ltcai-generated-files.zip",
            )
        else:
            await send_message(client, chat_id, "생성 파일 묶음이 너무 커서 텔레그램으로 전송하지 못했습니다.")
    finally:
        try:
            zip_path.unlink()
        except OSError:
            pass

async def download_telegram_file(client, file_id):
    """텔레그램 서버에서 파일을 다운로드하여 Base64로 변환합니다."""
    try:
        # 1. 파일 경로 가져오기
        res = await client.get(f"{API_URL}/getFile?file_id={file_id}")
        file_info = res.json()
        file_path = file_info.get("result", {}).get("file_path")
        if not file_path:
            return None
        
        # 2. 실제 파일 다운로드
        file_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"
        file_res = await client.get(file_url)
        if file_res.status_code == 200:
            return base64.b64encode(file_res.content).decode("utf-8")
    except Exception as e:
        logger.error(f"Failed to download file: {e}")
    return None

async def run_bot():
    if not TOKEN:
        logger.warning("LATTICEAI_TELEGRAM_BOT_TOKEN이 설정되지 않아 텔레그램 봇을 시작하지 않습니다.")
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
                logger.error(f"get_updates 실패: {e}")
                await asyncio.sleep(min(retry_delay, 30))
                retry_delay = min(retry_delay * 2, 30)
                continue

            if updates and updates.get("ok"):
                for update in updates.get("result", []):
                    try:
                        last_update_id = update.get("update_id") + 1

                        if "message" not in update:
                            continue

                        msg = update["message"]
                        chat_id = msg["chat"]["id"]
                        register_chat_id(chat_id)
                        text = msg.get("text", "")
                        caption = msg.get("caption", "")

                        image_data = None
                        final_prompt = text or caption or "이 이미지를 분석해줘."

                        if "photo" in msg:
                            file_id = msg["photo"][-1]["file_id"]
                            await send_message(client, chat_id, "📸 사진을 받았습니다. 분석을 시작합니다...")
                            image_data = await download_telegram_file(client, file_id)
                        elif "document" in msg and msg["document"].get("mime_type", "").startswith("image/"):
                            file_id = msg["document"]["file_id"]
                            image_data = await download_telegram_file(client, file_id)

                        if not (text or image_data):
                            continue

                        if final_prompt == "/start":
                            await send_message(client, chat_id, "🧠 Lattice AI 준비 완료! 텍스트로 지시하면 agent_workspace 안에서 파일 작업을 하고, 사진을 보내면 분석합니다. /web 은 웹 UI 링크, /mcp 는 로컬 도구 목록입니다.")
                            continue
                        if final_prompt == "/web":
                            await send_web_link(client, chat_id)
                            continue
                        if final_prompt == "/mcp":
                            await send_mcp_tools(client, chat_id)
                            continue
                        if final_prompt in {"/clear", "/clear_history", "/forget"}:
                            await clear_server_history(client, chat_id)
                            continue

                        task = asyncio.create_task(process_ai_request(client, chat_id, final_prompt, image_data))
                        task.add_done_callback(
                            lambda t: logger.error(f"process_ai_request 예외: {t.exception()}") if not t.cancelled() and t.exception() else None
                        )
                    except Exception as e:
                        logger.error(f"업데이트 처리 중 예외: {e}")

            await asyncio.sleep(0.5)

async def process_ai_request(client, chat_id, user_text, image_data=None):
    """별도의 태스크로 AI 답변을 처리합니다."""
    try:
        await send_chat_action(client, chat_id, "upload_photo" if image_data else "typing")
        data = await ask_ai(client, user_text, image_data, agent_mode=not image_data)
        logger.info("🤖 AI 답변 생성 완료")

        ans = data.get("response", str(data)) if isinstance(data, dict) else str(data)
        if not ans or not str(ans).strip():
            ans = "⚠️ AI가 답변을 생성하지 못했습니다."

        await send_message(client, chat_id, str(ans))

        if not image_data and isinstance(data, dict):
            generated_files = collect_generated_files(data)
            await send_generated_files(client, chat_id, generated_files)
            preview_urls = collect_preview_urls(data)
            await send_preview_links(client, chat_id, preview_urls)
    except Exception as e:
        logger.error(f"process_ai_request 실패 (chat_id={chat_id}): {e}")
        try:
            await send_message(client, chat_id, f"⚠️ 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")
        except Exception:
            pass

if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        pass
