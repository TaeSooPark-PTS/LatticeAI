use std::sync::Arc;

use rusqlite::Connection;

use super::*;
use crate::db::Store;
use crate::graph_write::types::IngestContentRequest;
use crate::graph_write::GraphWriter;
use crate::graph_write::RevisionReplace;
use crate::pytext::parse_iso;

fn request(text: &str) -> IngestContentRequest {
    IngestContentRequest {
        source_type: "note".into(),
        title: "policy".into(),
        text: text.into(),
        source_uri: Some("/vault/policy.md".into()),
        workspace_id: Some("personal".into()),
        ..Default::default()
    }
}

fn now() -> f64 {
    parse_iso(Some("2026-12-01T00:00:00")).unwrap()
}

#[test]
fn a_revision_replace_marks_the_prior_document_superseded_by_the_new_one() {
    let dir = tempfile::tempdir().unwrap();
    let store = Arc::new(Store::open(&dir.path().join("knowledge_graph.sqlite")).unwrap());
    let writer = GraphWriter::open(store, dir.path().join("blobs")).unwrap();
    let first = writer
        .replace_document_revision(
            &request("refund window is 14 days"),
            &RevisionReplace::default(),
        )
        .unwrap();
    let second = writer
        .replace_document_revision(
            &request("refund window is 30 days"),
            &RevisionReplace {
                prior_node_id: Some(first.ingest.node_id.clone()),
                subject: "policy".into(),
                recorded_at: "2026-11-26T00:00:00".into(),
            },
        )
        .unwrap();
    let old_id = first.ingest.node_id.clone();
    let new_id = second.ingest.node_id.clone();
    let states = writer
        .store()
        .with_read_conn(|conn| node_temporal_states(conn, &[old_id.clone(), new_id.clone()], now()))
        .unwrap();
    let old = &states[&old_id];
    assert!(old.is_superseded());
    assert_eq!(old.superseded_by.as_deref(), Some(new_id.as_str()));
    assert_eq!(old.valid_until.as_deref(), Some("2026-11-26T00:00:00"));
    let new = &states[&new_id];
    assert_eq!(new.status, STATUS_ACTIVE);
    assert_eq!(new.valid_from.as_deref(), Some("2026-11-26T00:00:00"));
    assert_eq!(new.as_json()["status"], "active");
    let edges: i64 = writer
        .store()
        .with_read_conn(|conn| {
            Ok(conn.query_row(
                "SELECT COUNT(*) FROM edges WHERE type='SUPERSEDES' AND from_node=?1 AND to_node=?2",
                [new_id.as_str(), old_id.as_str()],
                |r| r.get(0),
            )?)
        })
        .unwrap();
    assert_eq!(edges, 1);
}

fn legacy_store() -> Connection {
    let conn = Connection::open_in_memory().unwrap();
    conn.execute_batch(
        "CREATE TABLE nodes(id TEXT PRIMARY KEY, metadata_json TEXT, created_at TEXT);
         CREATE TABLE edges(id TEXT PRIMARY KEY, from_node TEXT, to_node TEXT, type TEXT);
         INSERT INTO nodes VALUES ('old', '{}', '2026-01-01T00:00:00');
         INSERT INTO nodes VALUES ('new', '{}', '2026-02-01T00:00:00');
         INSERT INTO nodes VALUES ('expired', '{\"temporal\":{\"valid_until\":\"2026-06-01T00:00:00\"}}', '2026-01-01T00:00:00');
         INSERT INTO nodes VALUES ('future_end', '{\"temporal\":{\"valid_until\":\"2027-06-01T00:00:00\"}}', '2026-01-01T00:00:00');
         INSERT INTO nodes VALUES ('retired', '{\"lifecycle_status\":\"deprecated\"}', '2026-01-01T00:00:00');
         INSERT INTO nodes VALUES ('self', '{}', '2026-01-01T00:00:00');
         INSERT INTO edges VALUES ('e1', 'new', 'old', 'SUPERSEDES');
         INSERT INTO edges VALUES ('e2', 'self', 'self', 'SUPERSEDES');
         INSERT INTO edges VALUES ('e3', 'new', 'future_end', 'RELATED_TO');",
    )
    .unwrap();
    conn
}

#[test]
fn an_inbound_supersedes_edge_alone_retires_a_node_without_nodes_v2() {
    let conn = legacy_store();
    let ids: Vec<String> = [
        "old",
        "new",
        "expired",
        "future_end",
        "retired",
        "self",
        "missing",
        " ",
    ]
    .iter()
    .map(|id| id.to_string())
    .collect();
    let states = node_temporal_states(&conn, &ids, now()).unwrap();
    assert_eq!(states["old"].superseded_by.as_deref(), Some("new"));
    assert_eq!(states["old"].superseded_via, Some("supersedes_edge"));
    assert_eq!(states["old"].lifecycle, "superseded");
    assert_eq!(states["new"].status, STATUS_ACTIVE);
    assert_eq!(states["expired"].superseded_via, Some("valid_until"));
    assert_eq!(states["future_end"].status, STATUS_ACTIVE);
    assert_eq!(states["retired"].superseded_via, Some("lifecycle"));
    assert_eq!(states["self"].status, STATUS_ACTIVE);
    assert!(!states.contains_key("missing"));
    assert!(node_temporal_states(&conn, &[], now()).unwrap().is_empty());
    let bare = Connection::open_in_memory().unwrap();
    assert!(node_temporal_states(&bare, &ids, now()).unwrap().is_empty());
}
