"""Lattice AI Admin Security & Audit Command Center.

피드백 #5 (lattice_ai_admin_security_dashboard_review.txt) 반영.

추가 엔드포인트:
  GET  /admin/security/overview
  GET  /admin/security/users
  GET  /admin/security/events
  GET  /admin/security/events/{event_id}
  GET  /admin/security/conversations/{conversation_id}
  GET  /admin/security/conversations/{conversation_id}/raw
  GET  /admin/security/files
  GET  /admin/security/files/{file_id}
  GET  /admin/security/files/{file_id}/content
  GET  /admin/security/raw
  POST /admin/security/export

핵심 원칙:
- Secret/API key/token/password/private key는 관리자도 원문을 보면 안 됨.
- 원문 조회 자체를 admin_view_sensitive_raw 감사로그로 남김.
- 모든 응답은 마스킹된 preview를 기본으로, raw는 별도 권한 필요.
"""

from __future__ import annotations

import io
import json
import logging
import re
import time
from collections import defaultdict
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from ..core import timezones

logger = logging.getLogger(__name__)


# ── Hard secret patterns ──────────────────────────────────────────────────────
# 이 값들은 관리자도 절대 원문으로 보면 안 된다.

HARD_SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|access[_-]?token|password|passwd|bearer)\s*[:=]\s*\S+"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+PRIVATE KEY-----"),
]


def redact_hard_secrets(text: str) -> str:
    if not text:
        return text or ""
    out = text
    for pat in HARD_SECRET_PATTERNS:
        out = pat.sub("[REDACTED_SECRET]", out)
    return out


def soft_mask(text: str, *, keep: int = 4) -> str:
    if not text:
        return ""
    raw = redact_hard_secrets(text)
    if len(raw) <= keep * 2:
        return "*" * len(raw)
    return raw[:keep] + "*" * min(len(raw) - keep * 2, 30) + raw[-keep:]


# ── Export models ─────────────────────────────────────────────────────────────


class ExportRequest(BaseModel):
    scope: str = "events"  # events | users | files | conversations | overview
    format: str = "json"   # json | csv | excel | pdf | txt
    filters: Optional[Dict[str, Any]] = None


# ── Helpers ───────────────────────────────────────────────────────────────────


def _user_label(users: Dict[str, Dict[str, Any]], email: Optional[str]) -> str:
    if not email:
        return "Unknown"
    u = users.get(email) or {}
    return u.get("nickname") or u.get("name") or email


def _summarize_user_risk(
    history: List[Dict[str, Any]],
    file_events: List[Dict[str, Any]],
    classify_sensitive_message: Callable[[Dict[str, Any], int], Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """사용자별 준수 채팅/위험 채팅/준수 파일/위험 파일 카운트."""
    buckets: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "user": "Unknown",
        "total_chats": 0,
        "compliant_chats": 0,
        "risky_chats": 0,
        "uploaded_files": 0,
        "compliant_files": 0,
        "risky_files": 0,
        "high_risk_events": 0,
        "last_activity_at": None,
    })

    for idx, item in enumerate(history):
        role = item.get("role")
        if role != "user":
            continue
        email = item.get("user_email") or item.get("user_nickname") or "Unknown"
        nickname = item.get("user_nickname") or email
        bucket = buckets[email]
        bucket["user"] = nickname
        bucket["total_chats"] += 1
        try:
            cls = classify_sensitive_message(item, idx)
        except Exception:
            cls = {"sensitivity": "none"}
        if (cls.get("sensitivity") or "none") != "none":
            bucket["risky_chats"] += 1
            if cls.get("sensitivity") == "high":
                bucket["high_risk_events"] += 1
        else:
            bucket["compliant_chats"] += 1
        ts = item.get("timestamp")
        if ts and (not bucket["last_activity_at"] or ts > bucket["last_activity_at"]):
            bucket["last_activity_at"] = ts

    for fe in file_events:
        email = fe.get("user_email") or "Unknown"
        bucket = buckets[email]
        bucket["uploaded_files"] += 1
        if (fe.get("sensitivity") or "none") != "none" or fe.get("sensitive_labels"):
            bucket["risky_files"] += 1
            if fe.get("sensitivity") == "high":
                bucket["high_risk_events"] += 1
        else:
            bucket["compliant_files"] += 1

    out: List[Dict[str, Any]] = []
    for email, b in buckets.items():
        total = b["total_chats"] + b["uploaded_files"]
        risk = b["risky_chats"] + b["risky_files"]
        b["email"] = email
        b["risk_rate"] = round((risk / total) * 100, 1) if total else 0.0
        out.append(b)
    out.sort(key=lambda x: (x["high_risk_events"], x["risky_chats"] + x["risky_files"]), reverse=True)
    return out


def _csv_dump(rows: List[Dict[str, Any]]) -> bytes:
    import csv

    buf = io.StringIO()
    if not rows:
        return b""
    keys: List[str] = []
    seen: set = set()
    for r in rows:
        for k in r.keys():
            if k not in seen:
                seen.add(k)
                keys.append(k)
    writer = csv.DictWriter(buf, fieldnames=keys, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        sanitized = {k: redact_hard_secrets(str(v)) if isinstance(v, str) else v for k, v in r.items()}
        writer.writerow(sanitized)
    return buf.getvalue().encode("utf-8")


def _excel_dump(rows: List[Dict[str, Any]]) -> bytes:
    try:
        from openpyxl import Workbook
    except Exception:  # pragma: no cover
        return _csv_dump(rows)
    wb = Workbook()
    ws = wb.active
    ws.title = "security_export"
    headers: List[str] = []
    if rows:
        seen: set = set()
        for r in rows:
            for k in r.keys():
                if k not in seen:
                    seen.add(k)
                    headers.append(k)
        ws.append(headers)
        for r in rows:
            ws.append([
                redact_hard_secrets(str(r.get(h))) if isinstance(r.get(h), str) else r.get(h)
                for h in headers
            ])
    else:
        ws.append(["empty"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _pdf_report(title: str, rows: List[Dict[str, Any]], overview: Dict[str, Any]) -> bytes:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table
    except Exception:  # pragma: no cover
        return ("PDF library not available\n" + json.dumps(overview, ensure_ascii=False, indent=2)).encode("utf-8")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, title=title)
    styles = getSampleStyleSheet()
    story: List[Any] = []
    story.append(Paragraph(f"<b>{title}</b>", styles["Title"]))
    story.append(Spacer(1, 12))
    story.append(Paragraph(f"Generated: {datetime.utcnow().isoformat()}Z", styles["Normal"]))
    story.append(Spacer(1, 12))
    story.append(Paragraph("Overview", styles["Heading2"]))
    for k, v in overview.items():
        story.append(Paragraph(f"{k}: {redact_hard_secrets(str(v))}", styles["Normal"]))
    story.append(Spacer(1, 12))
    if rows:
        story.append(Paragraph("Top entries", styles["Heading2"]))
        headers = list(rows[0].keys())
        table_data = [headers] + [
            [redact_hard_secrets(str(r.get(h, ""))) for h in headers] for r in rows[:30]
        ]
        story.append(Table(table_data))
    doc.build(story)
    return buf.getvalue()


# ── Router factory ────────────────────────────────────────────────────────────


def create_security_router(
    *,
    require_admin: Callable,
    get_history: Callable,
    get_audit_events: Callable,
    classify_sensitive_message: Callable,
    build_sensitivity_report: Callable,
    list_uploaded_files: Optional[Callable] = None,
    get_conversation: Optional[Callable] = None,
    append_audit_event: Optional[Callable] = None,
) -> APIRouter:
    """관리자 보안/감사 Command Center API.

    - require_admin(request) -> (admin_email, users)
    - get_history() -> List[Dict]
    - get_audit_events() -> List[Dict]
    - classify_sensitive_message(item, index) -> Dict
    - build_sensitivity_report(history) -> Dict
    - list_uploaded_files() -> Optional[List[Dict]]  (없으면 audit_events에서 추론)
    - get_conversation(conversation_id) -> Optional[Dict]
    - append_audit_event(event_type, **payload) -> None
    """

    router = APIRouter()

    # ── Common loaders ────────────────────────────────────────────────────

    def _events() -> List[Dict[str, Any]]:
        try:
            return list(get_audit_events() or [])
        except Exception as e:
            logger.warning("get_audit_events failed: %s", e)
            return []

    def _file_events() -> List[Dict[str, Any]]:
        if list_uploaded_files:
            try:
                items = list(list_uploaded_files() or [])
                if items:
                    return items
            except Exception:
                logger.debug("list_uploaded_files failed", exc_info=True)
        return [e for e in _events() if e.get("event_type") == "document_upload"]

    def _log_view(admin_email: str, target_type: str, target_id: str, reason: str = "security_review") -> None:
        if not append_audit_event:
            return
        try:
            append_audit_event(
                "admin_view_sensitive_raw",
                admin_email=admin_email,
                target_type=target_type,
                target_id=target_id,
                reason=reason,
            )
        except Exception:
            logger.debug("append_audit_event for admin_view_sensitive_raw failed", exc_info=True)

    # ── 1. Security Overview ──────────────────────────────────────────────

    @router.get("/admin/security/overview")
    async def security_overview(request: Request):
        require_admin(request)
        history = get_history() or []
        events = _events()
        report = build_sensitivity_report(history) or {}
        summary = report.get("summary", {})
        sev = summary.get("severity_counts", {}) or {}
        # item 7: audit timestamp(로컬/설정 시간대)와 동일한 기준으로 "오늘"을 계산한다.
        today = timezones.today_str()
        today_events = [e for e in events if str(e.get("timestamp", ""))[:10] == today]

        return {
            "generated_at": timezones.now_iso(),
            "timezone": timezones.tz_name(),
            "cards": {
                "events_today": len(today_events),
                "high_risk_events": int(sev.get("high", 0)),
                "risky_chats": int(summary.get("risky_messages", 0)),
                "risky_files": sum(
                    1 for fe in _file_events()
                    if (fe.get("sensitivity") or "none") != "none" or fe.get("sensitive_labels")
                ),
                "secret_blocks": sum(
                    1 for e in events
                    if (e.get("event_type") in {"secret_block", "external_send_block"})
                    or "secret" in (e.get("sensitive_labels") or [])
                ),
                "external_blocks": sum(
                    1 for e in events
                    if e.get("event_type") == "external_send_block"
                ),
                "admin_raw_views": sum(
                    1 for e in events
                    if e.get("event_type") == "admin_view_sensitive_raw"
                ),
                "review_required": int(sev.get("high", 0)) + int(sev.get("medium", 0)),
            },
            "field_counts": summary.get("field_counts", {}),
            "severity_counts": sev,
            "risk_rate": summary.get("risk_rate", 0),
        }

    # ── 2. User Risk Matrix ───────────────────────────────────────────────

    @router.get("/admin/security/users")
    async def security_users(request: Request):
        _, users = require_admin(request)
        history = get_history() or []
        per_user = _summarize_user_risk(history, _file_events(), classify_sensitive_message)
        # 사용자 메타데이터 join
        for row in per_user:
            email = row.get("email")
            meta = users.get(email or "") or {}
            row["role"] = meta.get("role") or "user"
            row["disabled"] = bool(meta.get("disabled"))
            row["user"] = _user_label(users, email)
        return {"users": per_user, "total": len(per_user)}

    # ── 3. Events listing (with filters) ──────────────────────────────────

    @router.get("/admin/security/events")
    async def security_events(
        request: Request,
        user: Optional[str] = Query(None),
        type: Optional[str] = Query(None),
        severity: Optional[str] = Query(None),
        date_from: Optional[str] = Query(None, alias="from"),
        date_to: Optional[str] = Query(None, alias="to"),
        limit: int = Query(200, ge=1, le=2000),
    ):
        require_admin(request)
        events = _events()
        out: List[Dict[str, Any]] = []
        for idx, e in enumerate(events):
            ts = str(e.get("timestamp") or "")
            if user and (e.get("user_email") != user and e.get("user_nickname") != user):
                continue
            if type and e.get("event_type") != type:
                continue
            if severity and (e.get("sensitivity") or "none") != severity:
                continue
            if date_from and ts < date_from:
                continue
            if date_to and ts > date_to:
                continue
            mc = dict(e)
            mc["event_id"] = str(e.get("event_id") or idx)
            if "content_preview" in mc:
                mc["content_preview"] = redact_hard_secrets(str(mc.get("content_preview") or ""))
            out.append(mc)
        out.sort(key=lambda x: str(x.get("timestamp") or ""), reverse=True)
        return {"events": out[:limit], "total": len(out)}

    @router.get("/admin/security/events/{event_id}")
    async def security_event_detail(event_id: str, request: Request):
        admin_email, _ = require_admin(request)
        events = _events()
        idx_to_find: Optional[int] = None
        try:
            idx_to_find = int(event_id)
        except Exception:
            idx_to_find = None

        target: Optional[Dict[str, Any]] = None
        if idx_to_find is not None and 0 <= idx_to_find < len(events):
            target = events[idx_to_find]
        else:
            for e in events:
                if str(e.get("event_id") or "") == event_id:
                    target = e
                    break
        if not target:
            raise HTTPException(status_code=404, detail="이벤트를 찾을 수 없습니다.")
        _log_view(admin_email, "event", str(event_id))
        masked = dict(target)
        if "content_preview" in masked:
            masked["content_preview"] = redact_hard_secrets(str(masked.get("content_preview") or ""))
        return {"event": masked, "raw_available": True}

    # ── 4. Conversation drill-down ────────────────────────────────────────

    @router.get("/admin/security/conversations/{conversation_id}")
    async def security_conversation_summary(conversation_id: str, request: Request):
        require_admin(request)
        history = [h for h in (get_history() or []) if h.get("conversation_id") == conversation_id]
        items = [classify_sensitive_message(h, i) for i, h in enumerate(history)]
        return {
            "conversation_id": conversation_id,
            "messages_total": len(items),
            "risky_messages": sum(1 for it in items if it.get("sensitivity") != "none"),
            "items": items,
        }

    @router.get("/admin/security/conversations/{conversation_id}/raw")
    async def security_conversation_raw(conversation_id: str, request: Request):
        admin_email, _ = require_admin(request)
        history = [h for h in (get_history() or []) if h.get("conversation_id") == conversation_id]
        if not history:
            raise HTTPException(status_code=404, detail="대화를 찾을 수 없습니다.")
        _log_view(admin_email, "conversation", conversation_id)
        # 원문 조회 — 단, hard secret은 항상 redact
        masked = []
        for h in history:
            cleaned = dict(h)
            if "content" in cleaned:
                cleaned["content"] = redact_hard_secrets(str(cleaned.get("content") or ""))
            masked.append(cleaned)
        return {"conversation_id": conversation_id, "messages": masked}

    # ── 5. File monitor ───────────────────────────────────────────────────

    @router.get("/admin/security/files")
    async def security_files(request: Request):
        require_admin(request)
        files = _file_events()
        for f in files:
            if "content_preview" in f:
                f["content_preview"] = redact_hard_secrets(str(f.get("content_preview") or ""))
        return {"files": files, "total": len(files)}

    @router.get("/admin/security/files/{file_id}")
    async def security_file_detail(file_id: str, request: Request):
        admin_email, _ = require_admin(request)
        files = _file_events()
        target = next(
            (f for f in files if str(f.get("file_id") or f.get("filename") or "") == file_id),
            None,
        )
        if not target:
            raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")
        _log_view(admin_email, "file", file_id)
        cleaned = dict(target)
        if "content_preview" in cleaned:
            cleaned["content_preview"] = redact_hard_secrets(str(cleaned.get("content_preview") or ""))
        return {"file": cleaned}

    @router.get("/admin/security/files/{file_id}/content")
    async def security_file_content(file_id: str, request: Request):
        admin_email, _ = require_admin(request)
        files = _file_events()
        target = next(
            (f for f in files if str(f.get("file_id") or f.get("filename") or "") == file_id),
            None,
        )
        if not target:
            raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")
        _log_view(admin_email, "file_content", file_id, reason="raw_content_review")
        # raw text가 있더라도 hard secret은 redact
        text = target.get("extracted_text") or target.get("content_preview") or ""
        return {"file_id": file_id, "text": redact_hard_secrets(str(text))}

    # ── 6. Raw data explorer ──────────────────────────────────────────────

    @router.get("/admin/security/raw")
    async def security_raw(request: Request, scope: str = Query("audit")):
        admin_email, _ = require_admin(request)
        _log_view(admin_email, "raw", scope, reason="raw_explorer")
        if scope == "audit":
            payload = _events()
        elif scope == "history":
            payload = get_history() or []
        elif scope == "files":
            payload = _file_events()
        else:
            raise HTTPException(status_code=400, detail="지원하지 않는 scope입니다.")
        # JSON 전체에서 hard secret redact
        text = json.dumps(payload, ensure_ascii=False)
        text = redact_hard_secrets(text)
        return Response(content=text, media_type="application/json; charset=utf-8")

    # ── 7. Export ─────────────────────────────────────────────────────────

    @router.post("/admin/security/export")
    async def security_export(req: ExportRequest, request: Request):
        admin_email, users = require_admin(request)
        scope = (req.scope or "events").lower()
        fmt = (req.format or "json").lower()
        if scope == "events":
            rows = _events()
        elif scope == "users":
            rows = _summarize_user_risk(get_history() or [], _file_events(), classify_sensitive_message)
        elif scope == "files":
            rows = _file_events()
        elif scope == "overview":
            report = build_sensitivity_report(get_history() or []) or {}
            rows = [report.get("summary", {})]
        else:
            raise HTTPException(status_code=400, detail="지원하지 않는 scope입니다.")

        if not isinstance(rows, list):
            rows = []

        _log_view(admin_email, "export", f"{scope}:{fmt}", reason="export")

        if fmt == "json":
            body = json.dumps(rows, ensure_ascii=False, indent=2)
            body = redact_hard_secrets(body)
            filename = f"security_{scope}.json"
            return Response(
                content=body,
                media_type="application/json; charset=utf-8",
                headers={"Content-Disposition": f"attachment; filename={filename}"},
            )
        if fmt == "csv":
            body = _csv_dump(rows)
            filename = f"security_{scope}.csv"
            return Response(
                content=body,
                media_type="text/csv; charset=utf-8",
                headers={"Content-Disposition": f"attachment; filename={filename}"},
            )
        if fmt in {"excel", "xlsx"}:
            body = _excel_dump(rows)
            filename = f"security_{scope}.xlsx"
            return Response(
                content=body,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-Disposition": f"attachment; filename={filename}"},
            )
        if fmt == "pdf":
            overview = build_sensitivity_report(get_history() or []).get("summary", {}) or {}
            body = _pdf_report("Lattice AI Security Report", rows, overview)
            filename = f"security_{scope}.pdf"
            return Response(
                content=body,
                media_type="application/pdf",
                headers={"Content-Disposition": f"attachment; filename={filename}"},
            )
        if fmt == "txt":
            body = "\n".join(
                redact_hard_secrets(json.dumps(r, ensure_ascii=False)) for r in rows
            )
            return Response(
                content=body,
                media_type="text/plain; charset=utf-8",
                headers={"Content-Disposition": f"attachment; filename=security_{scope}.txt"},
            )
        raise HTTPException(status_code=400, detail="지원하지 않는 포맷입니다.")

    return router


__all__ = ["create_security_router", "redact_hard_secrets", "soft_mask"]
