"""The command screens — one Lattice-server read (or upload) rendered for a phone.

Everything reachable from the main menu that answers a question about the
running system rather than driving a conversation: server status, loaded
models, Knowledge Graph counts, a screenshot, chat history, the web-UI link,
the MCP tool list, and document upload into the graph.

Each screen follows the same shape: an optimistic chat action, one call through
:func:`~latticeai.integrations.telegram_bot.config._server_client`, and a plain
Korean rendering of exactly what came back — an unreachable server produces an
empty payload, never an invented one.

Stubbing note: these functions read ``_server_client``, ``_mac_ram_used_gb``,
``send_photo``, ``send_message`` and ``download_telegram_file`` as *this*
module's globals, so a test standing in for any of them patches this module.
``get_web_url``/``get_graph_url`` are read here but resolve
``PUBLIC_WEB_URL``/``get_lan_ip``/``SERVER_PORT`` inside ``helpers``.
"""

import asyncio
import os
import tempfile
from pathlib import Path

from latticeai.core.logging_safety import safe_log_text
from latticeai.core.quiet import quiet

from .config import (
    API_URL,
    BASE_URL,
    GRAPH_STATS_URL,
    HISTORY_URL,
    MCP_TOOLS_URL,
    MODELS_URL,
    STATUS_URL,
    UPLOAD_DOC_URL,
    _server_client,
    logger,
)
from .helpers import (
    download_telegram_file,
    get_graph_url,
    get_web_url,
    send_chat_action,
    send_message,
    send_photo,
)

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
