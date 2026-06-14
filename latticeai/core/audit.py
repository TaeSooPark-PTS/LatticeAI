"""Audit logging, sensitivity analysis, and admin reporting."""

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import timezones
from .security import redact_secrets

_history_lock = threading.Lock()

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
AUDIT_DELETE_EVENTS = {"conversation_delete", "history_delete", "user_delete"}


def get_audit_log(audit_file: Path) -> List[Dict]:
    if not os.path.exists(audit_file):
        return []
    try:
        with open(audit_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        logging.warning("get_audit_log failed: %s", e)
        return []


def append_audit_event(audit_file: Path, event_type: str, **payload) -> None:
    try:
        safe_payload = redact_secrets(payload)
        event = {
            "event_type": event_type,
            # item 7: 대시보드 "오늘" 계산과 동일한 시간대 기준으로 기록한다.
            "timestamp": timezones.now_iso(),
            **safe_payload,
        }
        with _history_lock:
            events = get_audit_log(audit_file)
            events.append(event)
            if len(events) > 5000:
                events = events[-5000:]
            tmp_path = str(audit_file) + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(events, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, audit_file)
    except Exception as e:
        logging.warning("append_audit_event failed: %s", e)


def mask_sensitive_text(text: str, matches: List[Dict]) -> str:
    masked = text
    for item in sorted(matches, key=lambda m: m["start"], reverse=True):
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
    seen: set = set()
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
        severity = max(found, key=lambda m: SEVERITY_SCORE[m["severity"]])["severity"]
    preview_text = content[:240]
    preview_matches = [m for m in found if m["start"] < len(preview_text)]
    return {
        "index": index,
        "role": item.get("role", ""),
        "user_email": item.get("user_email"),
        "user_nickname": item.get("user_nickname") or item.get("user_email") or "Unknown",
        "timestamp": item.get("timestamp"),
        "sensitivity": severity,
        "labels": sorted({m["label"] for m in found}),
        "risk_fields": found,
        "compliance_fields": [] if found else ["민감정보 미검출"],
        "preview": mask_sensitive_text(preview_text, preview_matches),
    }


def build_sensitivity_report(history: List[Dict]) -> Dict:
    items = [classify_sensitive_message(item, i) for i, item in enumerate(history)]
    risky = [x for x in items if x["risk_fields"]]
    compliant = [x for x in items if not x["risk_fields"]]
    field_counts: Dict[str, int] = {}
    user_counts: Dict[str, int] = {}
    severity_counts = {"high": 0, "medium": 0, "low": 0, "none": len(compliant)}
    for item in risky:
        severity_counts[item["sensitivity"]] += 1
        user_key = item.get("user_email") or item.get("user_nickname") or "Unknown"
        user_counts[user_key] = user_counts.get(user_key, 0) + 1
        for field in item["risk_fields"]:
            field_counts[field["label"]] = field_counts.get(field["label"], 0) + 1
    return {
        "summary": {
            "total_messages": len(items),
            "risky_messages": len(risky),
            "compliant_messages": len(compliant),
            "risk_rate": round((len(risky) / len(items)) * 100, 1) if items else 0,
            "severity_counts": severity_counts,
            "field_counts": field_counts,
            "user_counts": user_counts,
        },
        "risk_fields": risky[-30:],
        "compliance_fields": compliant[-30:],
    }


def build_admin_audit_report(
    audit_file: Path,
    users: Dict,
    *,
    get_user_role: Callable[[str, Optional[Dict]], str],
    graph_stats: Optional[Dict] = None,
    audit_events: Optional[List[Dict]] = None,
) -> Dict:
    events = audit_events if audit_events is not None else get_audit_log(audit_file)

    def _user_bucket(email: Optional[str], nickname: Optional[str] = None) -> Dict:
        user = users.get(email or "", {})
        return {
            "email": email or "Unknown",
            "nickname": nickname or user.get("nickname") or user.get("name") or email or "Unknown",
            "role": get_user_role(email, users) if email else "unknown",
            "disabled": bool(user.get("disabled")) if user else False,
            "user_messages": 0, "assistant_messages": 0, "document_uploads": 0,
            "clear_events": 0, "delete_events": 0, "sensitive_events": 0,
            "high_sensitive_events": 0, "total_content_chars": 0, "last_activity_at": None,
        }

    per_user: Dict[str, Dict] = {}

    def ensure(email: Optional[str], nickname: Optional[str] = None) -> Dict:
        key = email or nickname or "Unknown"
        if key not in per_user:
            per_user[key] = _user_bucket(email, nickname)
        elif nickname and per_user[key].get("nickname") in {"Unknown", email, None}:
            per_user[key]["nickname"] = nickname
        return per_user[key]

    for email, user in users.items():
        ensure(email, user.get("nickname") or user.get("name"))

    summary: Dict[str, Any] = {
        "total_events": len(events), "chat_events": 0, "user_messages": 0,
        "assistant_messages": 0, "document_uploads": 0, "clear_events": 0,
        "delete_events": 0, "sensitive_events": 0, "high_sensitive_events": 0,
    }
    sensitive_events: List[Dict] = []
    deletion_events: List[Dict] = []

    for event in events:
        event_type = event.get("event_type")
        email = event.get("user_email")
        u = ensure(email, event.get("user_nickname"))
        ts = event.get("timestamp")
        if ts and (not u["last_activity_at"] or ts > u["last_activity_at"]):
            u["last_activity_at"] = ts
        u["total_content_chars"] += int(event.get("content_chars") or event.get("extracted_chars") or 0)
        sensitivity = event.get("sensitivity") or "none"
        labels = event.get("sensitive_labels") or []
        is_sensitive = sensitivity != "none" or bool(labels)

        if event_type == "chat_message":
            summary["chat_events"] += 1
            if event.get("role") == "user":
                summary["user_messages"] += 1
                u["user_messages"] += 1
            elif event.get("role") == "assistant":
                summary["assistant_messages"] += 1
                u["assistant_messages"] += 1
        elif event_type == "document_upload":
            summary["document_uploads"] += 1
            u["document_uploads"] += 1
        elif event_type == "clear_command":
            summary["clear_events"] += 1
            u["clear_events"] += 1
        elif event_type in AUDIT_DELETE_EVENTS:
            summary["delete_events"] += 1
            u["delete_events"] += 1
            deletion_events.append(_public_audit_event(event))

        if is_sensitive:
            summary["sensitive_events"] += 1
            u["sensitive_events"] += 1
            if sensitivity == "high":
                summary["high_sensitive_events"] += 1
                u["high_sensitive_events"] += 1
            sensitive_events.append(_public_audit_event(event))

    recent = [_public_audit_event(e) for e in events[-50:]]

    result: Dict[str, Any] = {
        "summary": summary,
        "per_user": sorted(per_user.values(), key=lambda u: u.get("last_activity_at") or "", reverse=True),
        "recent_events": list(reversed(recent)),
        "sensitive_events": sensitive_events[-30:],
        "deletion_events": deletion_events[-30:],
    }
    if graph_stats:
        result["summary"]["graph_nodes"] = graph_stats.get("total_nodes", 0)
        result["summary"]["graph_edges"] = graph_stats.get("total_edges", 0)
    return result


def _public_audit_event(event: Dict) -> Dict:
    allowed = {
        "event_type", "timestamp", "role", "user_email", "user_nickname", "source",
        "conversation_id", "workspace_id", "command", "scope", "target_email", "filename", "mime_type",
        "ext", "bytes", "extracted_chars", "graph_node", "keep_last", "removed", "kept",
        "started_at", "sensitivity", "sensitive_labels", "content_preview", "content_chars",
    }
    return {k: event.get(k) for k in allowed if k in event}
