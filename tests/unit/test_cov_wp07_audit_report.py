"""The admin audit report: what the operator console is built from.

``build_admin_audit_report`` is the only reader that turns the append-only
audit log into per-user totals, so the tests here pin the roll-up arithmetic
(which bucket each event kind lands in) and the projection that decides which
fields of a raw audit row are safe to hand back.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from latticeai.core import audit
from latticeai.core.audit import (
    append_audit_event,
    build_admin_audit_report,
    classify_sensitive_message,
)


def _role(email: Optional[str], users: Optional[Dict] = None) -> str:
    return ((users or {}).get(email or "") or {}).get("role") or "user"


USERS: Dict[str, Dict[str, Any]] = {
    "ann@x.com": {"nickname": "Ann", "role": "admin"},
    "bob@x.com": {"name": "Bob Lee", "disabled": True},
    "carol@x.com": {},
}


def _events() -> List[Dict[str, Any]]:
    return [
        {
            "event_type": "chat_message",
            "role": "user",
            "user_email": "ann@x.com",
            "timestamp": "2026-01-01T09:00:00",
            "content_chars": 12,
            "internal_note": "not a public field",
        },
        {
            "event_type": "chat_message",
            "role": "assistant",
            "user_email": "ann@x.com",
            "timestamp": "2026-01-01T09:00:05",
        },
        {
            "event_type": "document_upload",
            "user_email": "bob@x.com",
            "timestamp": "2026-01-02T10:00:00",
            "extracted_chars": 400,
            "sensitivity": "high",
            "filename": "payroll.xlsx",
        },
        {
            "event_type": "clear_command",
            "user_email": "bob@x.com",
            "timestamp": "2026-01-02T11:00:00",
        },
        {
            "event_type": "conversation_delete",
            "user_email": "ghost@x.com",
            "timestamp": "2026-01-03T08:00:00",
            "conversation_id": "c1",
        },
        {
            "event_type": "chat_message",
            "role": "user",
            "user_email": "ghost@x.com",
            "user_nickname": "Ghost",
            "timestamp": "2026-01-03T09:00:00",
            "sensitive_labels": ["secret"],
        },
    ]


def _report(tmp_path, **kwargs: Any) -> Dict[str, Any]:
    return build_admin_audit_report(
        tmp_path / "audit.json",
        dict(USERS),
        get_user_role=_role,
        audit_events=_events(),
        **kwargs,
    )


# ── the roll-up ───────────────────────────────────────────────────────────
def test_each_event_kind_lands_in_its_own_summary_counter(tmp_path):
    summary = _report(tmp_path)["summary"]

    assert summary["total_events"] == 6
    assert summary["chat_events"] == 3
    assert summary["user_messages"] == 2
    assert summary["assistant_messages"] == 1
    assert summary["document_uploads"] == 1
    assert summary["clear_events"] == 1
    assert summary["delete_events"] == 1


def test_an_event_is_sensitive_by_severity_or_by_label(tmp_path):
    summary = _report(tmp_path)["summary"]

    assert summary["sensitive_events"] == 2, "a labels-only event still counts"
    assert summary["high_sensitive_events"] == 1


def test_per_user_totals_follow_the_events_not_the_account_list(tmp_path):
    per_user = {row["email"]: row for row in _report(tmp_path)["per_user"]}

    ann = per_user["ann@x.com"]
    assert ann["nickname"] == "Ann"
    assert ann["role"] == "admin"
    assert ann["user_messages"] == 1
    assert ann["assistant_messages"] == 1
    assert ann["total_content_chars"] == 12
    assert ann["last_activity_at"] == "2026-01-01T09:00:05", "the newest stamp wins"

    bob = per_user["bob@x.com"]
    assert bob["nickname"] == "Bob Lee", "the account name stands in for a missing nickname"
    assert bob["disabled"] is True
    assert bob["document_uploads"] == 1
    assert bob["clear_events"] == 1
    assert bob["sensitive_events"] == 1
    assert bob["high_sensitive_events"] == 1
    assert bob["total_content_chars"] == 400


def test_an_account_with_no_events_is_still_listed_with_zeros(tmp_path):
    per_user = {row["email"]: row for row in _report(tmp_path)["per_user"]}

    carol = per_user["carol@x.com"]
    assert carol["nickname"] == "carol@x.com"
    assert carol["user_messages"] == 0
    assert carol["last_activity_at"] is None


def test_a_nickname_seen_later_replaces_the_placeholder_one(tmp_path):
    """``ghost@x.com`` is not an account: the first event names it by address."""
    per_user = {row["email"]: row for row in _report(tmp_path)["per_user"]}

    assert per_user["ghost@x.com"]["nickname"] == "Ghost"
    assert per_user["ghost@x.com"]["delete_events"] == 1


def test_users_are_ordered_by_most_recent_activity(tmp_path):
    emails = [row["email"] for row in _report(tmp_path)["per_user"]]
    assert emails[:3] == ["ghost@x.com", "bob@x.com", "ann@x.com"]
    assert emails[-1] == "carol@x.com", "an account with no activity sorts last"


def test_deletions_and_sensitive_events_are_kept_as_their_own_evidence(tmp_path):
    report = _report(tmp_path)

    assert [e["event_type"] for e in report["deletion_events"]] == ["conversation_delete"]
    assert report["deletion_events"][0]["conversation_id"] == "c1"
    assert {e["event_type"] for e in report["sensitive_events"]} == {
        "document_upload",
        "chat_message",
    }


def test_recent_events_are_newest_first_and_carry_only_public_fields(tmp_path):
    recent = _report(tmp_path)["recent_events"]

    assert len(recent) == 6
    assert recent[0]["timestamp"] == "2026-01-03T09:00:00"
    assert "internal_note" not in recent[-1], "the projection drops unlisted keys"
    assert recent[-1]["content_chars"] == 12


def test_graph_stats_are_folded_into_the_summary_only_when_supplied(tmp_path):
    without = _report(tmp_path)["summary"]
    assert "graph_nodes" not in without

    with_graph = _report(tmp_path, graph_stats={"total_nodes": 7, "total_edges": 9})["summary"]
    assert with_graph["graph_nodes"] == 7
    assert with_graph["graph_edges"] == 9


def test_the_report_reads_the_log_from_disk_when_no_events_are_passed(tmp_path):
    log = tmp_path / "audit.json"
    append_audit_event(log, "chat_message", role="user", user_email="ann@x.com")

    report = build_admin_audit_report(log, dict(USERS), get_user_role=_role)

    assert report["summary"]["total_events"] == 1
    assert report["summary"]["user_messages"] == 1
    assert report["recent_events"][0]["event_id"].startswith("audit-")


# ── classification ────────────────────────────────────────────────────────
def test_one_span_is_reported_once_even_when_two_rules_claim_it(monkeypatch):
    """Two rules sharing a key must not double-count the same characters."""
    rule = {"key": "dup", "label": "중복", "severity": "high", "pattern": r"secret-value"}
    monkeypatch.setattr(
        audit,
        "SENSITIVE_PATTERNS",
        [rule, dict(rule, pattern=r"secret-(?:value)")],
    )

    result = classify_sensitive_message({"content": "x secret-value y", "role": "user"}, 0)

    assert len(result["risk_fields"]) == 1
    assert result["labels"] == ["중복"]
    assert result["sensitivity"] == "high"
