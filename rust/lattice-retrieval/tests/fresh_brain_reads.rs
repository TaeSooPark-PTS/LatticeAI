//! Fresh-Brain regression: memory reads must not 500 before the first chat write.

#[allow(dead_code)]
mod common;

use serde_json::Value;

#[tokio::test]
async fn memory_reads_on_a_fresh_brain_do_not_500() {
    let install = common::brain::Install::start_fresh().await;
    let cookie = format!("session_token={}", install.token);

    let manager = install
        .issue(&serde_json::json!({
            "method": "GET",
            "path": "/api/memory/manager",
            "query": {},
            "request_headers": {"cookie": cookie},
        }))
        .await;
    assert_eq!(manager.0, 200, "manager: {}", manager.2);
    assert!(
        !manager.2.contains("no such table"),
        "manager leaked a missing table: {}",
        manager.2
    );

    let recall = install
        .issue(&serde_json::json!({
            "method": "POST",
            "path": "/api/memory/recall",
            "query": {},
            "request_headers": {"cookie": cookie},
            "request_body": {"query": "phoenix", "limit": 5},
        }))
        .await;
    assert_eq!(recall.0, 200, "recall: {}", recall.2);
    assert!(
        !recall.2.contains("no such table"),
        "recall leaked a missing table: {}",
        recall.2
    );
    let recalled: Value = serde_json::from_str(&recall.2).expect("recall json");
    assert_eq!(recalled["results"], serde_json::json!([]));

    let brief = install
        .issue(&serde_json::json!({
            "method": "GET",
            "path": "/api/memory/brain-brief",
            "query": {"q": "phoenix", "limit": "3"},
            "request_headers": {"cookie": cookie},
        }))
        .await;
    assert_eq!(brief.0, 200, "brain-brief: {}", brief.2);
    assert!(
        !brief.2.contains("no such table"),
        "brain-brief leaked a missing table: {}",
        brief.2
    );

    // Schema-at-init: the table the chat writer owns exists before any turn.
    let db = install.data_dir().join("knowledge_graph.sqlite");
    let conn = rusqlite::Connection::open(&db).expect("open fresh store");
    let found: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master \
             WHERE type='table' AND name='conversation_messages'",
            [],
            |row| row.get(0),
        )
        .expect("sqlite_master");
    assert_eq!(found, 1, "bootstrap must create conversation_messages");
}
