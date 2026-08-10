"""v11.0.1 — the admin security console redacts values, not serialized text.

Two defects from the v11.0.0 notes are pinned here.

*The file listing* (D6) masked only ``content_preview`` and masked it **in
place**, so ``extracted_text`` — the whole document body — reached the browser
untouched and the upload/audit rows were edited behind the seam.

*Every JSON surface* (D7) built the document first and ran the secret patterns
over the finished string. Those patterns end in an optional quote, so a secret
that ran up to the closing ``"`` swallowed it and the "export" was a file no
parser would accept. The fix walks the structure first, so the assertions below
parse the response instead of searching it — string-matching is exactly what
hid this.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.api.security_dashboard import _redact_structure, create_security_router

LEAKED = "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
MASKED = "api_key=[REDACTED_SECRET]"


def _client(**overrides: Any) -> TestClient:
    wiring: Dict[str, Any] = {
        "require_admin": lambda _request: ("admin@x.com", {"admin@x.com": {"role": "admin"}}),
        "get_history": lambda: [],
        "get_audit_events": lambda: [],
        "classify_sensitive_message": lambda item, index: {"sensitivity": "none"},
        "build_sensitivity_report": lambda history: {"summary": {}},
    }
    wiring.update(overrides)
    app = FastAPI()
    app.include_router(create_security_router(**wiring))
    return TestClient(app)


# ── the helper itself ─────────────────────────────────────────────────────
def test_the_walk_redacts_string_leaves_at_every_depth():
    payload = {
        "note": "api_key=" + LEAKED,
        "rows": [{"body": "api_key=" + LEAKED}, ["api_key=" + LEAKED]],
    }

    cleaned = _redact_structure(payload)

    assert cleaned == {
        "note": MASKED,
        "rows": [{"body": MASKED}, [MASKED]],
    }


def test_the_walk_copies_rather_than_editing_the_caller_s_rows():
    row = {"note": "api_key=" + LEAKED, "nested": {"body": "api_key=" + LEAKED}}

    cleaned = _redact_structure(row)

    assert cleaned is not row
    assert cleaned["nested"] is not row["nested"]
    assert row["note"] == "api_key=" + LEAKED, "the source row is left as it was found"


def test_non_string_leaves_survive_the_walk_unchanged():
    # an audit export that lost its counts and flags would be worse than useless
    assert _redact_structure({"count": 3, "ok": True, "missing": None}) == {
        "count": 3,
        "ok": True,
        "missing": None,
    }
    # containers keep their own type: the walk is a copy, not a reshaping
    assert _redact_structure(("api_key=" + LEAKED, 7)) == (MASKED, 7)


# ── D6: the file listing ──────────────────────────────────────────────────
def _files() -> List[Dict[str, Any]]:
    return [
        {
            "file_id": "f1",
            "filename": "payroll.xlsx",
            "bytes": 2048,
            "content_preview": "api_key=" + LEAKED,
            "extracted_text": "api_key=" + LEAKED,
            "sensitive_labels": ["api_key=" + LEAKED],
        }
    ]


def test_the_file_listing_masks_every_string_field_not_just_the_preview():
    body = _client(list_uploaded_files=_files).get("/admin/security/files").json()

    row = body["files"][0]
    assert row["content_preview"] == MASKED
    assert row["extracted_text"] == MASKED, "the document body is the bigger leak"
    assert row["sensitive_labels"] == [MASKED]
    assert LEAKED not in json.dumps(body, ensure_ascii=False)
    # the fields that are not secrets are still the fields the console renders
    assert row["filename"] == "payroll.xlsx"
    assert row["bytes"] == 2048


def test_the_file_listing_masks_a_copy_and_leaves_the_upload_store_intact():
    rows = _files()

    _client(list_uploaded_files=lambda: rows).get("/admin/security/files")

    assert rows[0]["content_preview"] == "api_key=" + LEAKED
    assert rows[0]["extracted_text"] == "api_key=" + LEAKED
    assert rows[0]["sensitive_labels"] == ["api_key=" + LEAKED]


def test_the_file_detail_masks_the_whole_record_not_only_the_preview():
    body = _client(list_uploaded_files=_files).get("/admin/security/files/f1").json()

    record = body["file"]
    assert record["content_preview"] == MASKED
    assert record["extracted_text"] == MASKED, "the detail view is not the looser door"
    assert record["sensitive_labels"] == [MASKED]
    assert LEAKED not in json.dumps(body, ensure_ascii=False)
    assert record["filename"] == "payroll.xlsx"
    assert record["bytes"] == 2048


def test_the_file_detail_masks_a_copy_and_leaves_the_upload_store_intact():
    rows = _files()

    _client(list_uploaded_files=lambda: rows).get("/admin/security/files/f1")

    assert rows[0]["content_preview"] == "api_key=" + LEAKED
    assert rows[0]["extracted_text"] == "api_key=" + LEAKED
    assert rows[0]["sensitive_labels"] == ["api_key=" + LEAKED]


def test_a_file_detail_without_a_preview_keeps_the_field_absent():
    rows = [{"file_id": "f2", "extracted_text": "api_key=" + LEAKED}]

    body = _client(list_uploaded_files=lambda: rows).get("/admin/security/files/f2").json()

    assert body["file"] == {"file_id": "f2", "extracted_text": MASKED}


def test_the_file_listing_masks_rows_inferred_from_the_audit_log_too():
    audit = [{"event_type": "document_upload", "content_preview": "api_key=" + LEAKED}]

    body = _client(get_audit_events=lambda: audit).get("/admin/security/files").json()

    assert body["files"] == [{"event_type": "document_upload", "content_preview": MASKED}]
    assert audit[0]["content_preview"] == "api_key=" + LEAKED


# ── D7: every JSON surface stays parseable ────────────────────────────────
def _secret_rows() -> List[Dict[str, Any]]:
    return [{"event_type": "chat_message", "note": "api_key=" + LEAKED, "count": 1}]


def test_the_raw_explorer_answers_with_parseable_json_for_every_scope():
    client = _client(
        get_audit_events=_secret_rows,
        get_history=lambda: [{"role": "user", "content": "api_key=" + LEAKED}],
        list_uploaded_files=lambda: [{"file_id": "api_key=" + LEAKED}],
    )

    for scope, expected in (
        ("audit", [{"event_type": "chat_message", "note": MASKED, "count": 1}]),
        ("history", [{"role": "user", "content": MASKED}]),
        ("files", [{"file_id": MASKED}]),
    ):
        response = client.get("/admin/security/raw", params={"scope": scope})
        assert response.json() == expected, scope
        assert LEAKED not in response.text


def test_a_json_export_of_a_secret_bearing_row_still_loads():
    client = _client(get_audit_events=_secret_rows)

    response = client.post("/admin/security/export", json={"scope": "events", "format": "json"})

    assert json.loads(response.text) == [
        {"event_type": "chat_message", "note": MASKED, "count": 1}
    ]


def test_a_json_export_masks_secrets_nested_below_the_top_level():
    client = _client(
        get_audit_events=lambda: [{"event_type": "x", "detail": {"lines": ["api_key=" + LEAKED]}}]
    )

    exported = client.post(
        "/admin/security/export", json={"scope": "events", "format": "json"}
    ).json()

    assert exported == [{"event_type": "x", "detail": {"lines": [MASKED]}}]


def test_a_csv_export_masks_a_secret_nested_inside_a_row_value():
    # csv writes a list cell as its repr, which happens after the row is
    # sanitized — so the walk has to reach inside it, not just past it.
    client = _client(
        get_audit_events=lambda: [{"event_type": "x", "sensitive_labels": ["api_key=" + LEAKED]}]
    )

    response = client.post("/admin/security/export", json={"scope": "events", "format": "csv"})

    assert LEAKED not in response.text
    assert response.text.splitlines()[1] == f"x,['{MASKED}']"


def test_every_line_of_a_text_export_is_still_one_json_object():
    client = _client(get_audit_events=lambda: _secret_rows() * 2)

    response = client.post("/admin/security/export", json={"scope": "events", "format": "txt"})

    lines = response.text.splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["note"] for line in lines] == [MASKED, MASKED]
