import asyncio
import json
import logging
import os
from pathlib import Path

import httpx
from openai import AsyncOpenAI


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

TELEGRAM_TOKEN = os.getenv("CODEX_TELEGRAM_BOT_TOKEN", "")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
OPENAI_MODEL = os.getenv("CODEX_OPENAI_MODEL", "gpt-5.4")
STATE_FILE = Path(os.getenv("CODEX_TELEGRAM_STATE_FILE", "codex_telegram_state.json"))
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "")

SYSTEM_PROMPT = """You are Lattice AI, the user's Codex development partner.
Discuss architecture, implementation plans, code reviews, and GitHub-ready changes.
Keep Korean responses concise and actionable.
When the user asks for changes that should land in the repository, propose a clear patch plan.
Use /issue to create GitHub issues when the user wants work tracked in git.
Do not claim that local Mac files were changed unless a GitHub action or explicit repository operation completed."""

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("codex_telegram_bot")
openai_client = AsyncOpenAI()


def load_state():
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


async def send_message(client, chat_id, text):
    chunks = [text[i:i + 3900] for i in range(0, len(text), 3900)] or [""]
    for chunk in chunks:
        await client.post(
            f"{TELEGRAM_API_URL}/sendMessage",
            json={"chat_id": chat_id, "text": chunk},
            timeout=30,
        )


async def get_updates(client, offset=None):
    params = {"timeout": 30}
    if offset is not None:
        params["offset"] = offset
    try:
        res = await client.get(f"{TELEGRAM_API_URL}/getUpdates", params=params, timeout=35)
        return res.json()
    except Exception as exc:
        logger.error("Telegram update failed: %s", exc)
        return None


async def create_github_issue(title, body):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return "GITHUB_TOKEN 또는 GITHUB_REPO가 설정되지 않아 이슈를 만들 수 없습니다."

    async with httpx.AsyncClient() as client:
        res = await client.post(
            f"https://api.github.com/repos/{GITHUB_REPO}/issues",
            headers={
                "Authorization": f"Bearer {GITHUB_TOKEN}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={"title": title, "body": body},
            timeout=30,
        )

    if res.status_code >= 300:
        return f"GitHub 이슈 생성 실패 ({res.status_code}): {res.text[:1000]}"

    data = res.json()
    return f"GitHub 이슈 생성 완료: {data.get('html_url')}"


async def ask_codex(chat_id, message):
    state = load_state()
    chat_key = str(chat_id)
    previous_response_id = state.get(chat_key, {}).get("previous_response_id")

    kwargs = {
        "model": OPENAI_MODEL,
        "instructions": SYSTEM_PROMPT,
        "input": message,
        "max_output_tokens": 1800,
    }
    if previous_response_id:
        kwargs["previous_response_id"] = previous_response_id

    response = await openai_client.responses.create(**kwargs)
    state[chat_key] = {"previous_response_id": response.id}
    save_state(state)
    return response.output_text


def parse_issue_command(text):
    content = text[len("/issue"):].strip()
    if not content:
        return None, None
    parts = content.split("\n", 1)
    title = parts[0].strip()
    body = parts[1].strip() if len(parts) > 1 else title
    return title, body


async def handle_message(client, chat_id, text):
    if text == "/start":
        await send_message(
            client,
            chat_id,
            "Codex 전용 봇입니다.\n"
            "- 일반 메시지: Codex와 개발 상담\n"
            "- /reset: 이 대화 컨텍스트 초기화\n"
            "- /issue 제목\\n내용: GitHub 이슈 생성",
        )
        return

    if text == "/reset":
        state = load_state()
        state.pop(str(chat_id), None)
        save_state(state)
        await send_message(client, chat_id, "Codex 대화 컨텍스트를 초기화했습니다.")
        return

    if text.startswith("/issue"):
        title, body = parse_issue_command(text)
        if not title:
            await send_message(client, chat_id, "사용법: /issue 제목\\n내용")
            return
        await send_message(client, chat_id, await create_github_issue(title, body))
        return

    await send_message(client, chat_id, "Codex가 검토 중입니다...")
    try:
        answer = await ask_codex(chat_id, text)
    except Exception as exc:
        logger.exception("OpenAI request failed")
        answer = f"OpenAI 요청 실패: {exc}"
    await send_message(client, chat_id, answer)


async def run_bot():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("CODEX_TELEGRAM_BOT_TOKEN is required.")
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required.")

    logger.info("Codex Telegram bot started.")
    last_update_id = None
    async with httpx.AsyncClient() as client:
        while True:
            updates = await get_updates(client, last_update_id)
            if updates and updates.get("ok"):
                for update in updates.get("result", []):
                    last_update_id = update["update_id"] + 1
                    msg = update.get("message") or {}
                    chat = msg.get("chat") or {}
                    text = msg.get("text") or ""
                    if chat.get("id") and text:
                        asyncio.create_task(handle_message(client, chat["id"], text))
            await asyncio.sleep(0.5)


if __name__ == "__main__":
    asyncio.run(run_bot())
