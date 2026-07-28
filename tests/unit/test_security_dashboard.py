"""Unit tests for latticeai.api.security_dashboard (피드백 #5)."""

from latticeai.api.security_dashboard import (
    _csv_dump,
    create_security_router,
    redact_hard_secrets,
    soft_mask,
)


def test_redact_hard_secrets_blocks_known_token_shapes():
    text = """
        api_key=sk-1234567890abcdefghij1234567890
        github=ghp_abcdefghijklmnopqrstuvwxyz12345678
        slack=xoxb-1234-5678-aaaabbbbccccdddd
        aws=AKIAABCDEFGHIJKLMNOP
    """
    out = redact_hard_secrets(text)
    assert "sk-1234567890" not in out
    assert "ghp_abcdef" not in out
    assert "xoxb-1234" not in out
    assert "AKIAABCDEFGHIJKLMNOP" not in out
    assert "[REDACTED_SECRET]" in out


def test_soft_mask_short_string():
    masked = soft_mask("ab")
    assert "*" in masked
    assert "a" not in masked or masked.count("*") >= 2


def test_soft_mask_preserves_edges():
    masked = soft_mask("rnlgnquvk@gmail.com")
    assert masked.startswith("rnlg")
    assert masked.endswith(".com")


def _build_minimal_router(events, history):
    """Helper that wires the router with in-memory test data."""
    users = {
        "admin@x.com": {"role": "admin"},
        "alice@x.com": {"role": "user"},
    }

    def fake_admin(req):
        return ("admin@x.com", users)

    def fake_classify(item, idx):
        content = str(item.get("content", ""))
        return {
            "sensitivity": "high" if "rrn" in content else "none",
            "labels": ["주민등록번호"] if "rrn" in content else [],
            "preview": content[:60],
            "user_email": item.get("user_email"),
            "timestamp": item.get("timestamp"),
        }

    def fake_sens(history_in):
        items = [fake_classify(h, i) for i, h in enumerate(history_in)]
        risky = [x for x in items if x["sensitivity"] != "none"]
        field_counts = {"주민등록번호": len(risky)} if risky else {}
        return {
            "summary": {
                "total_messages": len(items),
                "risky_messages": len(risky),
                "compliant_messages": len(items) - len(risky),
                "risk_rate": (len(risky) / len(items)) * 100 if items else 0,
                "severity_counts": {"high": len(risky), "medium": 0, "low": 0, "none": len(items) - len(risky)},
                "field_counts": field_counts,
                "user_counts": {},
            },
            "risk_fields": risky,
            "compliance_fields": [],
        }

    events_recorded = []

    def fake_append(event_type, **kwargs):
        events_recorded.append({"event_type": event_type, **kwargs})

    router = create_security_router(
        require_admin=fake_admin,
        get_history=lambda: history,
        get_audit_events=lambda: events,
        classify_sensitive_message=fake_classify,
        build_sensitivity_report=fake_sens,
        append_audit_event=fake_append,
    )
    return router, events_recorded


def test_csv_export_never_leaks_hard_secrets():
    """item 6: export 파일에도 hard secret 원문이 절대 들어가면 안 된다."""
    rows = [
        {
            "user": "alice",
            "content_preview": "api_key=sk-1234567890abcdefghij1234567890",
            "note": "github ghp_abcdefghijklmnopqrstuvwxyz12345678",
        }
    ]
    out = _csv_dump(rows).decode("utf-8")
    assert "sk-1234567890" not in out
    assert "ghp_abcdef" not in out
    assert "[REDACTED_SECRET]" in out


def test_overview_events_today_uses_configured_timezone(monkeypatch):
    """item 7 회귀 방지: events_today 가 audit timestamp 와 같은 시간대로 계산된다."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from latticeai.core import timezones

    monkeypatch.setenv("LATTICE_TZ", "Asia/Seoul")
    # audit 와 동일한 헬퍼로 만든 "오늘" timestamp 2건 + "어제" 1건.
    today_ts = timezones.now_iso()
    yesterday = (timezones.now().date().toordinal() - 1)
    from datetime import date
    yday_ts = date.fromordinal(yesterday).isoformat() + "T10:00:00+09:00"
    events = [
        {"event_type": "secret_block", "timestamp": today_ts},
        {"event_type": "external_send_block", "timestamp": today_ts},
        {"event_type": "secret_block", "timestamp": yday_ts},
    ]
    router, _ = _build_minimal_router(events, [])
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    resp = client.get("/admin/security/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert data["cards"]["events_today"] == 2  # 어제 1건은 제외
    assert data.get("timezone") == "Asia/Seoul"


def test_router_registers_expected_routes():
    router, _ = _build_minimal_router([], [])
    paths = {r.path for r in router.routes}
    expected = {
        "/admin/security/overview",
        "/admin/security/users",
        "/admin/security/events",
        "/admin/security/events/{event_id}",
        "/admin/security/conversations/{conversation_id}",
        "/admin/security/conversations/{conversation_id}/raw",
        "/admin/security/files",
        "/admin/security/files/{file_id}",
        "/admin/security/files/{file_id}/content",
        "/admin/security/raw",
        "/admin/security/export",
    }
    assert expected.issubset(paths)
