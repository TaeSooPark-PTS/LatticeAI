"""Routing and lifecycle: the poll loop and the two things it can route to.

Slash commands (:func:`handle_command`), inline-button callbacks
(:func:`handle_callback_query`), and the long-poll loop (:func:`run_bot`) that
feeds both. This is the only layer that decides *what* happens for an incoming
update; every branch delegates to a screen or a flow and holds no logic of its
own beyond the routing table.

Fail-closed startup, unchanged: no bot token, no allowlist, or no server
capability means the loop never starts. Every allowed update is dispatched onto
its own task with an exception logger attached, so one failing handler cannot
stop the poll.

Stubbing note: ``get_updates``, ``TOKEN``, ``download_as_base64``,
``process_ai_request``, ``handle_command``, ``handle_callback_query``, and every
screen/flow handler are read as *this* module's globals, so a test standing in
for any of them patches this module.
"""

import asyncio

import httpx

from latticeai.core.logging_safety import safe_log_text

from .config import TOKEN, _get_server_session, logger
from .flows import (
    _is_approval_pause,
    ask_ai,
    handle_plan_callback,
    handle_proposal_callback,
    process_ai_request,
    send_plan_for_approval,
    show_review_center,
)
from .helpers import (
    allowed_chat_ids,
    answer_callback,
    download_as_base64,
    get_updates,
    is_chat_allowed,
    register_chat_id,
    send_chat_action,
    send_message,
)
from .screens import (
    clear_server_history,
    do_unload_model,
    process_document_file,
    send_mcp_tools,
    send_web_link,
    show_graph_stats,
    show_history_summary,
    show_menu,
    show_model_info,
    show_status,
    take_screenshot,
)

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
