"""The admin security dashboard's HTTP surface.

Every route answers "what may an administrator see?", and two properties are
worth a regression test on each of them: a hard secret must never survive into
a response, and opening raw data must itself be recorded as an audit event. The
router is built through its factory with injected fakes (the
``tests/unit/test_auth_router.py`` idiom), so the routes are exercised without
an app, a database, or a session store.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.api.security_dashboard import create_security_router

LEAKED = "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"


def _classify(item: Dict[str, Any], index: int) -> Dict[str, Any]:
    content = str(item.get("content") or "")
    return {
        "index": index,
        "sensitivity": "high" if "rrn" in content else "none",
        "preview": content[:60],
        "user_email": item.get("user_email"),
    }


def _report(history: List[Dict[str, Any]]) -> Dict[str, Any]:
    risky = [h for h in history if "rrn" in str(h.get("content") or "")]
    return {
        "summary": {
            "total_messages": len(history),
            "risky_messages": len(risky),
            "severity_counts": {"high": len(risky), "medium": 0},
            "field_counts": {},
            "risk_rate": 0,
        }
    }


def _client(**overrides: Any) -> TestClient:
    users = overrides.pop("users", None) or {"admin@x.com": {"role": "admin"}}
    wiring: Dict[str, Any] = {
        "require_admin": lambda _request: ("admin@x.com", users),
        "get_history": lambda: [],
        "get_audit_events": lambda: [],
        "classify_sensitive_message": _classify,
        "build_sensitivity_report": _report,
    }
    wiring.update(overrides)
    app = FastAPI()
    app.include_router(create_security_router(**wiring))
    return TestClient(app)


def _recording_client(**overrides: Any) -> Tuple[TestClient, List[Dict[str, Any]]]:
    recorded: List[Dict[str, Any]] = []

    def sink(event_type: str, **payload: Any) -> None:
        recorded.append({"event_type": event_type, **payload})

    overrides.setdefault("append_audit_event", sink)
    return _client(**overrides), recorded


# ── user risk matrix ──────────────────────────────────────────────────────
def test_the_user_risk_matrix_joins_account_metadata_onto_each_row():
    users = {
        "a@x.com": {"role": "admin", "nickname": "Ann"},
        "b@x.com": {"role": "user", "disabled": True},
    }
    history = [
        {"role": "user", "user_email": "a@x.com", "content": "rrn 900101-1234567"},
        {"role": "user", "user_email": "b@x.com", "content": "hello"},
    ]
    body = _client(users=users, get_history=lambda: history).get("/admin/security/users").json()

    assert body["total"] == 2
    rows = {row["email"]: row for row in body["users"]}
    assert rows["a@x.com"]["role"] == "admin"
    assert rows["a@x.com"]["user"] == "Ann", "the account label wins over the chat nickname"
    assert rows["a@x.com"]["risky_chats"] == 1
    assert rows["a@x.com"]["disabled"] is False
    assert rows["b@x.com"]["role"] == "user"
    assert rows["b@x.com"]["disabled"] is True


# ── event listing ─────────────────────────────────────────────────────────
def _event(**overrides: Any) -> Dict[str, Any]:
    base = {
        "user_email": "alice@x.com",
        "event_type": "chat_message",
        "sensitivity": "high",
        "timestamp": "2026-01-03T00:00:00",
    }
    base.update(overrides)
    return base


def _filterable_events() -> List[Dict[str, Any]]:
    return [
        _event(user_email="bob@x.com"),
        _event(event_type="document_upload"),
        _event(sensitivity="low"),
        _event(timestamp="2026-01-01T00:00:00"),
        _event(timestamp="2026-01-09T00:00:00"),
        _event(
            timestamp="2026-01-04T00:00:00",
            event_id="audit-keep",
            content_preview="api_key=" + LEAKED,
        ),
        _event(),
    ]


def test_every_event_filter_narrows_the_listing():
    client = _client(get_audit_events=_filterable_events)
    response = client.get(
        "/admin/security/events",
        params={
            "user": "alice@x.com",
            "type": "chat_message",
            "severity": "high",
            "from": "2026-01-02",
            "to": "2026-01-05",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2, "each of the five filters must drop exactly one row"
    assert [e["event_id"] for e in body["events"]] == ["audit-keep", "6"]
    assert LEAKED not in body["events"][0]["content_preview"]
    assert "[REDACTED_SECRET]" in body["events"][0]["content_preview"]


def test_the_event_listing_is_newest_first_and_honours_the_limit():
    client = _client(get_audit_events=_filterable_events)
    unfiltered = client.get("/admin/security/events").json()
    assert unfiltered["total"] == 7, "no filter means no rows are dropped"
    stamps = [e["timestamp"] for e in unfiltered["events"]]
    assert stamps == sorted(stamps, reverse=True)

    capped = client.get("/admin/security/events", params={"limit": 2}).json()
    assert capped["total"] == 7, "the total counts matches, not the page"
    assert len(capped["events"]) == 2


def test_an_unreadable_audit_source_degrades_to_an_empty_listing():
    def broken() -> List[Dict[str, Any]]:
        raise RuntimeError("audit store offline")

    body = _client(get_audit_events=broken).get("/admin/security/events").json()
    assert body == {"events": [], "total": 0}


# ── event detail ──────────────────────────────────────────────────────────
def test_an_event_opens_by_index_or_by_id_and_the_view_is_audited():
    events = [
        {"event_type": "chat_message", "event_id": "audit-abc", "content_preview": "api_key=" + LEAKED},
        {"event_type": "clear_command"},
    ]
    client, recorded = _recording_client(get_audit_events=lambda: events)

    by_index = client.get("/admin/security/events/1")
    assert by_index.status_code == 200
    assert by_index.json()["event"]["event_type"] == "clear_command"

    by_id = client.get("/admin/security/events/audit-abc")
    assert by_id.status_code == 200
    assert by_id.json()["raw_available"] is True
    assert LEAKED not in by_id.json()["event"]["content_preview"]

    assert [e["event_type"] for e in recorded] == ["admin_view_sensitive_raw"] * 2
    assert recorded[0]["target_type"] == "event"
    assert recorded[0]["target_id"] == "1"
    assert recorded[1]["target_id"] == "audit-abc"
    assert recorded[1]["admin_email"] == "admin@x.com"


def test_an_unknown_event_id_is_a_404_and_is_not_audited_as_a_view():
    client, recorded = _recording_client(get_audit_events=lambda: [{"event_type": "x"}])

    assert client.get("/admin/security/events/audit-nope").status_code == 404
    assert client.get("/admin/security/events/99").status_code == 404
    assert recorded == []


# ── conversation drill-down ───────────────────────────────────────────────
def _history() -> List[Dict[str, Any]]:
    return [
        {"conversation_id": "c1", "role": "user", "content": "rrn 900101-1234567"},
        {"conversation_id": "c1", "role": "assistant", "content": "api_key=" + LEAKED},
        {"conversation_id": "c2", "role": "user", "content": "unrelated"},
    ]


def test_a_conversation_summary_counts_only_its_own_risky_messages():
    body = _client(get_history=_history).get("/admin/security/conversations/c1").json()

    assert body["conversation_id"] == "c1"
    assert body["messages_total"] == 2
    assert body["risky_messages"] == 1
    assert [item["index"] for item in body["items"]] == [0, 1]


def test_conversation_raw_redacts_secrets_and_records_the_view():
    client, recorded = _recording_client(get_history=_history)

    body = client.get("/admin/security/conversations/c1/raw").json()
    assert len(body["messages"]) == 2
    dumped = json.dumps(body, ensure_ascii=False)
    assert LEAKED not in dumped, "raw view still masks hard secrets"
    assert "[REDACTED_SECRET]" in dumped
    assert recorded[0]["target_type"] == "conversation"
    assert recorded[0]["target_id"] == "c1"


def test_an_unknown_conversation_is_a_404():
    client, recorded = _recording_client(get_history=_history)
    assert client.get("/admin/security/conversations/c9/raw").status_code == 404
    assert recorded == []


# ── file monitor ──────────────────────────────────────────────────────────
def _files() -> List[Dict[str, Any]]:
    return [
        {
            "file_id": "f1",
            "filename": "payroll.xlsx",
            "sensitivity": "high",
            "content_preview": "api_key=" + LEAKED,
            "extracted_text": "token=" + LEAKED,
        },
        {"filename": "notes.txt", "sensitivity": "none"},
    ]


def test_the_uploaded_file_source_is_preferred_over_audit_inference():
    audit = [{"event_type": "document_upload", "filename": "from-audit.txt"}]
    body = _client(
        list_uploaded_files=_files, get_audit_events=lambda: audit
    ).get("/admin/security/files").json()

    assert body["total"] == 2
    assert [f.get("filename") for f in body["files"]] == ["payroll.xlsx", "notes.txt"]
    # The listing masks the preview it renders. NOTE: `extracted_text` is passed
    # through untouched here, unlike /files/{id}/content which redacts it — see
    # the wp07 report. Asserted as-is so the gap is visible rather than implied.
    assert LEAKED not in body["files"][0]["content_preview"]
    assert "[REDACTED_SECRET]" in body["files"][0]["content_preview"]


def test_an_empty_upload_source_falls_back_to_the_audit_document_events():
    audit = [
        {"event_type": "document_upload", "filename": "from-audit.txt"},
        {"event_type": "chat_message", "filename": "not-a-file.txt"},
    ]
    body = _client(
        list_uploaded_files=lambda: [], get_audit_events=lambda: audit
    ).get("/admin/security/files").json()

    assert [f["filename"] for f in body["files"]] == ["from-audit.txt"]


def test_a_failing_upload_source_falls_back_to_the_audit_document_events():
    def broken() -> List[Dict[str, Any]]:
        raise RuntimeError("upload index offline")

    audit = [{"event_type": "document_upload", "filename": "from-audit.txt"}]
    body = _client(
        list_uploaded_files=broken, get_audit_events=lambda: audit
    ).get("/admin/security/files").json()

    assert [f["filename"] for f in body["files"]] == ["from-audit.txt"]


def test_a_file_detail_and_its_extracted_text_are_masked_and_audited():
    client, recorded = _recording_client(list_uploaded_files=_files)

    detail = client.get("/admin/security/files/f1").json()
    assert detail["file"]["filename"] == "payroll.xlsx"
    assert LEAKED not in detail["file"]["content_preview"]

    content = client.get("/admin/security/files/f1/content").json()
    assert content["file_id"] == "f1"
    assert LEAKED not in content["text"]
    assert "[REDACTED_SECRET]" in content["text"]

    assert [e["target_type"] for e in recorded] == ["file", "file_content"]
    assert recorded[1]["reason"] == "raw_content_review"


def test_a_file_without_an_id_is_addressable_by_filename():
    client = _client(list_uploaded_files=_files)
    body = client.get("/admin/security/files/notes.txt/content").json()
    assert body["text"] == "", "no extracted text and no preview reads as empty"


def test_unknown_file_ids_are_404_for_both_the_detail_and_the_content():
    client = _client(list_uploaded_files=_files)
    assert client.get("/admin/security/files/nope").status_code == 404
    assert client.get("/admin/security/files/nope/content").status_code == 404


# ── raw explorer ──────────────────────────────────────────────────────────
def _raw_client(**overrides: Any) -> Tuple[TestClient, List[Dict[str, Any]]]:
    overrides.setdefault("get_audit_events", lambda: [{"event_type": "chat_message"}])
    overrides.setdefault("get_history", lambda: [{"conversation_id": "c1", "role": "user"}])
    overrides.setdefault("list_uploaded_files", lambda: [{"file_id": "f1"}])
    return _recording_client(**overrides)


def test_the_raw_explorer_serves_each_supported_scope():
    client, recorded = _raw_client()

    audit = client.get("/admin/security/raw")
    assert audit.headers["content-type"].startswith("application/json")
    assert audit.json() == [{"event_type": "chat_message"}]
    assert client.get("/admin/security/raw", params={"scope": "history"}).json()[0]["role"] == "user"
    assert client.get("/admin/security/raw", params={"scope": "files"}).json() == [{"file_id": "f1"}]

    assert [e["target_id"] for e in recorded] == ["audit", "history", "files"]
    assert recorded[0]["reason"] == "raw_explorer"


def test_the_raw_explorer_refuses_an_unknown_scope():
    client, _ = _raw_client()
    response = client.get("/admin/security/raw", params={"scope": "everything"})
    assert response.status_code == 400


def test_the_raw_explorer_masks_secrets_in_the_dumped_payload():
    client, _ = _raw_client(
        get_audit_events=lambda: [{"event_type": "chat_message", "note": "api_key=" + LEAKED}]
    )
    text = client.get("/admin/security/raw").text
    assert LEAKED not in text
    assert "[REDACTED_SECRET]" in text


def test_the_raw_explorer_works_when_no_audit_sink_is_wired():
    client = _client(get_audit_events=lambda: [{"event_type": "chat_message"}])
    assert client.get("/admin/security/raw").status_code == 200


def test_a_failing_audit_sink_does_not_break_the_view_it_was_recording():
    def broken(event_type: str, **payload: Any) -> None:
        raise RuntimeError("audit sink is down")

    client = _client(
        get_audit_events=lambda: [{"event_type": "chat_message"}], append_audit_event=broken
    )
    assert client.get("/admin/security/raw").status_code == 200


# ── export ────────────────────────────────────────────────────────────────
def _export_client(**overrides: Any) -> Tuple[TestClient, List[Dict[str, Any]]]:
    overrides.setdefault(
        "get_audit_events",
        lambda: [{"event_type": "chat_message", "user_email": "a@x.com"}],
    )
    overrides.setdefault(
        "get_history",
        lambda: [{"role": "user", "user_email": "a@x.com", "content": "rrn 900101-1234567"}],
    )
    overrides.setdefault("list_uploaded_files", lambda: [{"file_id": "f1", "filename": "a.txt"}])
    return _recording_client(**overrides)


def test_a_json_export_is_a_download_and_is_recorded():
    client, recorded = _export_client()
    response = client.post("/admin/security/export", json={"scope": "events", "format": "json"})

    assert response.status_code == 200
    assert response.headers["content-disposition"] == "attachment; filename=security_events.json"
    assert response.json() == [{"event_type": "chat_message", "user_email": "a@x.com"}]
    assert recorded[0]["target_id"] == "events:json"
    assert recorded[0]["reason"] == "export"


def test_a_json_export_masks_secrets_before_they_reach_the_file():
    client, _ = _export_client(
        get_audit_events=lambda: [{"event_type": "chat_message", "note": "api_key=" + LEAKED}]
    )
    body = client.post("/admin/security/export", json={"scope": "events", "format": "json"}).text
    assert LEAKED not in body
    assert "[REDACTED_SECRET]" in body


def test_a_csv_export_carries_the_rows_as_text():
    client, _ = _export_client()
    response = client.post("/admin/security/export", json={"scope": "events", "format": "csv"})

    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == "attachment; filename=security_events.csv"
    assert response.text.splitlines()[0] == "event_type,user_email"


def test_an_excel_export_is_offered_under_both_of_its_names():
    client, _ = _export_client()
    for fmt in ("excel", "xlsx"):
        response = client.post("/admin/security/export", json={"scope": "events", "format": fmt})
        assert response.status_code == 200
        assert response.content[:2] == b"PK"
        assert response.headers["content-disposition"] == "attachment; filename=security_events.xlsx"


def test_a_pdf_export_is_served_as_a_pdf_download():
    """The renderer itself is covered in test_cov_wp07_secdash_helpers.py.

    Asserted here: the route hands the renderer's bytes back under PDF headers.
    The body is deliberately not matched against ``%PDF-`` — reportlab is not a
    declared dependency, so on a clean install ``_pdf_report`` returns its
    library-missing text and this route must still answer 200.
    """
    client, _ = _export_client()
    response = client.post("/admin/security/export", json={"scope": "events", "format": "pdf"})

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == "attachment; filename=security_events.pdf"
    assert response.content, "an empty download is not a report"


def test_a_text_export_is_one_json_row_per_line():
    client, _ = _export_client()
    response = client.post("/admin/security/export", json={"scope": "events", "format": "txt"})

    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["content-disposition"] == "attachment; filename=security_events.txt"
    assert json.loads(response.text.splitlines()[0])["event_type"] == "chat_message"


def test_every_export_scope_produces_its_own_shape():
    client, _ = _export_client()

    users = client.post("/admin/security/export", json={"scope": "users", "format": "json"}).json()
    assert users[0]["email"] == "a@x.com"
    assert users[0]["risky_chats"] == 1

    files = client.post("/admin/security/export", json={"scope": "files", "format": "json"}).json()
    assert files == [{"file_id": "f1", "filename": "a.txt"}]

    overview = client.post(
        "/admin/security/export", json={"scope": "overview", "format": "json"}
    ).json()
    assert overview[0]["total_messages"] == 1
    assert overview[0]["risky_messages"] == 1


def test_an_unknown_export_scope_or_format_is_refused_and_not_recorded():
    client, recorded = _export_client()

    bad_scope = client.post("/admin/security/export", json={"scope": "everything", "format": "json"})
    assert bad_scope.status_code == 400
    assert recorded == [], "a refused scope is not an export worth auditing"

    bad_format = client.post("/admin/security/export", json={"scope": "events", "format": "docx"})
    assert bad_format.status_code == 400
    assert len(recorded) == 1, "the format is only checked after the view is recorded"


def test_export_defaults_to_a_json_event_dump():
    client, _ = _export_client()
    response = client.post("/admin/security/export", json={})

    assert response.headers["content-disposition"] == "attachment; filename=security_events.json"
    assert response.json()[0]["event_type"] == "chat_message"
