use super::*;

fn meta<'a>() -> HistoryMeta<'a> {
    HistoryMeta {
        email: Some("owner@example.com"),
        nickname: Some("owner"),
        source: Some("web"),
        conversation_id: Some("c1"),
        workspace_id: Some("personal"),
    }
}

#[test]
fn the_item_keeps_pythons_key_order_and_drops_empty_attribution() {
    let item = build_item("user", "hi", "2026-08-14T12:00:00", &meta());
    assert_eq!(
        serde_json::to_string(&item).unwrap(),
        "{\"role\":\"user\",\"content\":\"hi\",\"timestamp\":\"2026-08-14T12:00:00\",\
             \"user_email\":\"owner@example.com\",\"user_nickname\":\"owner\",\
             \"source\":\"web\",\"conversation_id\":\"c1\",\"workspace_id\":\"personal\"}"
    );
    let bare = build_item(
        "assistant",
        "yo",
        "t",
        &HistoryMeta {
            email: None,
            nickname: Some(""),
            source: None,
            conversation_id: None,
            workspace_id: None,
        },
    );
    assert_eq!(
        serde_json::to_string(&bare).unwrap(),
        "{\"role\":\"assistant\",\"content\":\"yo\",\"timestamp\":\"t\"}",
        "an empty nickname is as absent as a missing one"
    );
}

#[test]
fn the_message_hash_is_pythons_six_field_digest() {
    let item = build_item("user", "hi", "2026-08-14T12:00:00", &meta());
    // sha256("user|hi|2026-08-14T12:00:00|owner@example.com|c1|web")
    let expected: String = {
        let basis = "user|hi|2026-08-14T12:00:00|owner@example.com|c1|web";
        Sha256::digest(basis.as_bytes())
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect()
    };
    assert_eq!(message_hash(&item), expected);
    // A missing key hashes as an empty field, not as "null".
    let bare = json!({"role": "user", "content": "hi", "timestamp": "t"});
    assert_eq!(
        message_hash(&bare),
        Sha256::digest("user|hi|t|||".as_bytes())
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>()
    );
    // A non-string field stringifies rather than vanishing.
    assert_ne!(
        message_hash(&json!({"role": 1, "content": "hi", "timestamp": "t"})),
        message_hash(&bare)
    );
}

#[test]
fn the_audit_payload_has_all_ten_keys_even_when_empty() {
    let sensitive = json!({"preview": "hi", "sensitivity": "none", "labels": []});
    let payload = audit_payload("user", "안녕", &meta(), &sensitive);
    let keys: Vec<&String> = payload.as_object().unwrap().keys().collect();
    assert_eq!(
        keys,
        [
            "role",
            "user_email",
            "user_nickname",
            "source",
            "conversation_id",
            "workspace_id",
            "content_preview",
            "content_chars",
            "sensitivity",
            "sensitive_labels",
        ]
        .iter()
        .collect::<Vec<_>>()
    );
    assert_eq!(payload["content_chars"], 2, "characters, not UTF-8 bytes");
    let empty = audit_payload(
        "assistant",
        "",
        &HistoryMeta {
            email: None,
            nickname: None,
            source: None,
            conversation_id: None,
            workspace_id: None,
        },
        &json!({}),
    );
    assert_eq!(empty["user_email"], Value::Null);
    assert_eq!(empty["sensitive_labels"], json!([]));
    assert_eq!(empty["content_preview"], Value::Null);
}

#[test]
fn a_turn_stores_and_reads_back_through_the_same_columns() {
    let dir = tempfile::tempdir().unwrap();
    let conn = Connection::open(dir.path().join("g.sqlite")).unwrap();
    let item = build_item("user", "hi", "2026-08-14T12:00:00", &meta());
    store_item(&conn, &item).unwrap();
    // Idempotent: the same turn twice is one row (`INSERT OR IGNORE` on the
    // message hash), which is what makes a retry safe.
    store_item(&conn, &item).unwrap();
    let count: i64 = conn
        .query_row("SELECT COUNT(*) FROM conversation_messages", [], |row| {
            row.get(0)
        })
        .unwrap();
    assert_eq!(count, 1);
    let (content, metadata, workspace): (String, String, String) = conn
        .query_row(
            "SELECT content, metadata_json, workspace_id FROM conversation_messages",
            [],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
        )
        .unwrap();
    assert_eq!(content, "hi");
    assert_eq!(metadata, "{}");
    assert_eq!(workspace, "personal");
}

#[test]
fn a_missing_workspace_stamps_personal_on_the_row() {
    let dir = tempfile::tempdir().unwrap();
    let conn = Connection::open(dir.path().join("g.sqlite")).unwrap();
    let item = build_item(
        "user",
        "hi",
        "t",
        &HistoryMeta {
            email: Some("a@x"),
            nickname: None,
            source: Some("web"),
            conversation_id: Some("c"),
            workspace_id: None,
        },
    );
    assert!(item.get("workspace_id").is_none(), "the item is unstamped");
    store_item(&conn, &item).unwrap();
    let workspace: String = conn
        .query_row(
            "SELECT workspace_id FROM conversation_messages",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(workspace, "personal");
}

#[test]
fn unknown_item_keys_land_in_metadata_json() {
    let dir = tempfile::tempdir().unwrap();
    let conn = Connection::open(dir.path().join("g.sqlite")).unwrap();
    let item = json!({
        "role": "user", "content": "hi", "timestamp": "t", "trace_id": "abc",
    });
    store_item(&conn, &item).unwrap();
    let metadata: String = conn
        .query_row(
            "SELECT metadata_json FROM conversation_messages",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(metadata, "{\"trace_id\":\"abc\"}");
}

#[test]
fn the_ingestion_receipt_is_the_pipelines_thirteen_keys() {
    let receipt = ingestion_result("message:abc", "hi", true, "indexed", Some("prov-1"), None);
    let keys: Vec<&String> = receipt.as_object().unwrap().keys().collect();
    assert_eq!(keys.len(), 13);
    assert_eq!(keys[0], "status");
    assert_eq!(receipt["source_type"], "chat_message");
    assert_eq!(receipt["chunk_count"], 0);
    assert_eq!(receipt["duplicate"], false);
    assert_eq!(receipt["provenance_id"], "prov-1");
    assert_eq!(receipt["detail"], Value::Null);
    assert!(
        receipt.get("extraction_quality").is_none(),
        "chat turns carry no quality annotation, so the key is absent"
    );
    let degraded = ingestion_result("m", "t", false, "pending", None, Some("boom"));
    assert_eq!(degraded["provenance_id"], Value::Null);
    assert_eq!(degraded["detail"], "boom");
}

#[test]
fn the_ingest_metadata_carries_the_raw_item() {
    let item = build_item("user", "hi", "t", &meta());
    let metadata = ingest_metadata("user", &meta(), &item);
    assert_eq!(metadata["role"], "user");
    assert_eq!(metadata["user_nickname"], "owner");
    assert_eq!(metadata["source"], "web");
    assert_eq!(metadata["raw"]["content"], "hi");
}

#[test]
fn a_receipt_reports_the_stored_text() {
    let turn = RecordedTurn {
        stored: true,
        item: Some(json!({"role": "user", "content": "[redacted]"})),
        ingested: None,
    };
    assert_eq!(turn.content(), Some("[redacted]"));
    let refused = RecordedTurn {
        stored: false,
        item: None,
        ingested: None,
    };
    assert_eq!(refused.content(), None);
    assert_ne!(turn, refused);
    assert!(format!("{refused:?}").contains("stored"));
}

#[test]
fn the_timestamps_are_iso_and_the_utc_one_says_so() {
    let local = naive_local_iso();
    assert_eq!(&local[4..5], "-");
    assert_eq!(&local[10..11], "T");
    assert!(local.len() >= 19, "{local}");
    let utc = utc_now_iso();
    assert!(utc.ends_with("+00:00"), "{utc}");
    assert_eq!(civil_iso(0), "1970-01-01T00:00:00");
    // `datetime.fromtimestamp(1_786_000_000, timezone.utc).isoformat()`.
    assert_eq!(civil_iso(1_786_000_000), "2026-08-06T07:06:40");
    assert_eq!(civil_iso(-86_400), "1969-12-31T00:00:00");
}

#[test]
fn a_divergent_embedder_is_named_rather_than_written_over() {
    let identity = VectorIdentity::Diverges("worker x vs native y".into());
    assert!(format!("{identity:?}").contains("worker x"));
    assert_ne!(identity, VectorIdentity::Agrees);
    assert_ne!(VectorIdentity::NotAsked, VectorIdentity::Agrees);
    assert_eq!(VectorIdentity::Agrees.clone(), VectorIdentity::Agrees);
}
