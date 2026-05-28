"""Unit tests for latticeai.api.security_dashboard (피드백 #5)."""

from latticeai.api.security_dashboard import (
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
