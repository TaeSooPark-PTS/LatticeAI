"""One question, end to end: ask, render honestly, and get approval before acting.

The conversational half of the bridge. :func:`ask_ai` is the single door to the
Lattice server for a user's message; everything else in this module renders what
came back or negotiates what happens next:

* **honest rendering** — the artifact card, the grounding badge and the run
  explanation report exactly the flags the server issued. An absent flag is
  never promoted into a claim (SURFACE_PARITY, v9.9.6/9.9.7).
* **Review Center** — staged change proposals with inline approve/reject over
  the same ``/api/proposals`` surface the web app uses.
* **plan approval** — the human-in-the-loop pause. ``_bot_pending_plans`` is the
  bridge's only mutable module state and lives *here*, beside the two functions
  that write and consume it, so a stub or a reset reaches the real dictionary.

Stubbing note: ``_server_client``, ``ask_ai``, ``send_message``,
``send_chat_action``, ``send_generated_files``, ``send_preview_links``,
``send_run_explanation`` and ``send_artifact_card`` are read as *this* module's
globals, so a test standing in for any of them patches this module.
"""

import json
from typing import Any, Dict

from latticeai.core.logging_safety import safe_log_text
from latticeai.core.quiet import quiet

from .config import (
    AGENT_RESUME_URL,
    AGENT_URL,
    CHAT_URL,
    PROPOSALS_URL,
    _server_client,
    logger,
)
from .helpers import (
    collect_generated_files,
    collect_preview_urls,
    send_chat_action,
    send_generated_files,
    send_message,
    send_preview_links,
)

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


# ── Plan approval (Human-in-the-loop) ────────────────────────────────────────

# Pending plan approvals keyed by run_id (or legacy context_id).
# Values: chat_id, models, and the resume credentials (token and/or legacy).
_bot_pending_plans: dict[str, dict] = {}


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
