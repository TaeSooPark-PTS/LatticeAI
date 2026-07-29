"""The audit log and the sensitivity report.

Two properties matter more than the rest here. The audit log is append-only
evidence, so a partially-written file must never replace a good one and a
failing write must not take the caller down with it. And the sensitivity report
is shown to an operator — if it masked nothing, or masked so much that the
operator could not tell what was found, it would be worse than absent.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from latticeai.core.audit import (
    append_audit_event,
    build_sensitivity_report,
    classify_sensitive_message,
    get_audit_log,
    mask_sensitive_text,
)


# ── the log ───────────────────────────────────────────────────────────────
def test_a_missing_log_reads_as_empty_not_an_error(tmp_path):
    assert get_audit_log(tmp_path / "nope.json") == []


def test_a_corrupt_log_reads_as_empty_rather_than_crashing_the_caller(tmp_path):
    """A damaged audit file must not stop the server from starting."""
    bad = tmp_path / "audit.json"
    bad.write_text("{ this is not json", encoding="utf-8")
    assert get_audit_log(bad) == []


def test_a_log_holding_a_non_list_reads_as_empty(tmp_path):
    bad = tmp_path / "audit.json"
    bad.write_text('{"events": []}', encoding="utf-8")
    assert get_audit_log(bad) == []


def test_an_event_round_trips_with_an_id_timestamp_and_contract(tmp_path):
    log = tmp_path / "audit.json"
    append_audit_event(log, "cloud_egress", node_count=3, mode="cloud_allowed")

    events = get_audit_log(log)
    assert len(events) == 1
    event = events[0]
    assert event["event_type"] == "cloud_egress"
    assert event["node_count"] == 3
    assert event["event_id"].startswith("audit-")
    assert event["timestamp"], "an audit row with no time is not evidence"
    assert event["contract"], "family envelope missing"


def test_events_append_rather_than_replace(tmp_path):
    log = tmp_path / "audit.json"
    for i in range(3):
        append_audit_event(log, "login", attempt=i)
    assert [e["attempt"] for e in get_audit_log(log)] == [0, 1, 2]


def test_secrets_in_a_payload_are_redacted_before_they_reach_disk(tmp_path):
    """The audit log is a file on disk; writing a live key into it is a leak."""
    log = tmp_path / "audit.json"
    secret = "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcd"
    append_audit_event(log, "model_load", api_key=secret)
    assert secret not in log.read_text(encoding="utf-8")


def test_the_log_is_capped_and_keeps_the_newest(tmp_path):
    log = tmp_path / "audit.json"
    events = [{"event_type": "x", "n": i} for i in range(5100)]
    log.write_text(json.dumps(events), encoding="utf-8")

    append_audit_event(log, "newest", n=99999)
    kept = get_audit_log(log)
    assert len(kept) <= 5000
    assert kept[-1]["event_type"] == "newest", "the cap must drop the oldest, not the newest"


def test_a_write_failure_is_swallowed_rather_than_breaking_the_caller(tmp_path):
    """Audit is evidence, not a dependency: losing it must not lose the action."""
    unwritable = tmp_path / "missing-dir" / "audit.json"
    append_audit_event(unwritable, "login", user="me")  # must not raise


def test_no_temp_file_survives_a_successful_write(tmp_path):
    log = tmp_path / "audit.json"
    append_audit_event(log, "login", user="me")
    assert not (tmp_path / "audit.json.tmp").exists()


# ── masking ───────────────────────────────────────────────────────────────
def test_short_values_are_fully_masked():
    masked = mask_sensitive_text("pin 1234", [{"start": 4, "end": 8}])
    assert masked == "pin ****"


def test_longer_values_keep_their_edges_so_an_operator_can_recognise_them():
    text = "card 4111111111111111 end"
    masked = mask_sensitive_text(text, [{"start": 5, "end": 21}])
    assert masked.startswith("card 41")
    assert masked.endswith("11 end")
    assert "4111111111111111" not in masked


def test_overlapping_matches_mask_right_to_left_without_corrupting_offsets():
    text = "a 1234567890 b 0987654321 c"
    masked = mask_sensitive_text(text, [{"start": 2, "end": 12}, {"start": 15, "end": 25}])
    assert "1234567890" not in masked
    assert "0987654321" not in masked
    assert masked.startswith("a ") and masked.endswith(" c")


# ── classification ────────────────────────────────────────────────────────
def test_a_clean_message_is_reported_as_clean_with_a_reason():
    result = classify_sensitive_message({"content": "오늘 릴리스 절차를 정리했다", "role": "user"}, 0)
    assert result["sensitivity"] == "none"
    assert result["risk_fields"] == []
    assert result["compliance_fields"] == ["민감정보 미검출"]


def test_a_message_with_a_resident_number_is_flagged_and_masked():
    content = "제 주민번호는 900101-1234567 입니다"
    result = classify_sensitive_message({"content": content, "role": "user"}, 3)
    assert result["sensitivity"] == "high"
    assert "주민등록번호" in result["labels"]
    assert "900101-1234567" not in result["preview"], "PII must not survive into the preview"
    assert result["index"] == 3


def test_a_password_assignment_is_flagged():
    result = classify_sensitive_message(
        {"content": "api_key=abcd1234efgh", "role": "user"}, 0
    )
    assert result["sensitivity"] == "high"
    assert "비밀번호/인증정보" in result["labels"]


def test_an_email_is_low_severity_rather_than_ignored():
    result = classify_sensitive_message({"content": "write to me@example.com", "role": "user"}, 0)
    assert result["sensitivity"] == "low"
    assert "이메일" in result["labels"]


def test_the_reported_severity_is_the_worst_one_found():
    content = "email me@example.com and 주민번호 900101-1234567"
    result = classify_sensitive_message({"content": content, "role": "user"}, 0)
    severities = {f["severity"] for f in result["risk_fields"]}
    if len(severities) > 1:
        assert result["sensitivity"] == max(
            severities, key=lambda s: {"low": 1, "medium": 2, "high": 3}[s]
        )


def test_an_unknown_author_is_named_rather_than_left_blank():
    result = classify_sensitive_message({"content": "hi"}, 0)
    assert result["user_nickname"] == "Unknown"


def test_a_match_beyond_the_preview_window_does_not_break_masking():
    """Only matches inside the 240-char preview may be masked into it."""
    content = "x" * 300 + " 900101-1234567"
    result = classify_sensitive_message({"content": content, "role": "user"}, 0)
    assert len(result["preview"]) <= 240
    assert result["risk_fields"], "the match past the preview is still reported"


# ── the report ────────────────────────────────────────────────────────────
def test_an_empty_history_reports_zero_rather_than_dividing_by_zero():
    report = build_sensitivity_report([])
    assert report["summary"]["total_messages"] == 0
    assert report["summary"]["risk_rate"] == 0


def test_the_report_counts_risky_and_clean_messages_separately():
    history = [
        {"content": "clean one", "role": "user", "user_email": "a@x"},
        {"content": "주민번호 900101-1234567", "role": "user", "user_email": "b@x"},
        {"content": "also clean", "role": "assistant", "user_email": "a@x"},
    ]
    summary = build_sensitivity_report(history)["summary"]
    assert summary["total_messages"] == 3
    assert summary["risky_messages"] == 1
    assert summary["compliant_messages"] == 2
    assert summary["risk_rate"] == 33.3
    assert summary["user_counts"] == {"b@x": 1}


def test_the_report_truncates_to_the_most_recent_thirty_of_each_kind():
    history = [
        {"content": f"주민번호 90010{i%10}-1234567", "role": "user"}
        for i in range(40)
    ]
    report = build_sensitivity_report(history)
    assert len(report["risk_fields"]) == 30
    assert report["risk_fields"][-1]["index"] == 39, "truncation must keep the newest"
