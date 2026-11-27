use std::collections::BTreeSet;

use rusqlite::Connection;
use serde_json::json;

use super::*;
use crate::graph_write::schema::bootstrap;
use crate::learning_loop::{
    record_knowledge_correction, revert_knowledge_correction_in_workspace,
    update_correction_status_in_workspace, CorrectionStatus, RecordCorrectionRequest,
};

fn brain() -> Connection {
    let mut conn = Connection::open_in_memory().unwrap();
    {
        let tx = conn.transaction().unwrap();
        bootstrap(&tx, "2026-01-01T00:00:00").unwrap();
        tx.commit().unwrap();
    }
    for (id, ws) in [("doc-a", "ws-a"), ("doc-b", "ws-b"), ("fact-a", "ws-a")] {
        conn.execute(
            "INSERT INTO nodes(id, type, title, summary, raw_json, metadata_json, created_at, updated_at)
             VALUES (?1, 'Document', ?1, ?1, '{}', json_object('workspace_id', ?2), '2026-01-01', '2026-01-01')",
            [id, ws],
        )
        .unwrap();
    }
    conn
}

fn feedback(
    conn: &mut Connection,
    ws: &str,
    target: &str,
    verdict: &str,
    status: CorrectionStatus,
) -> String {
    record_knowledge_correction(
        conn,
        &RecordCorrectionRequest {
            workspace_id: ws.into(),
            target_kind: CorrectionTargetKind::Citation,
            target_id: target.into(),
            status,
            actor: "user@example.com".into(),
            reason: "citation feedback".into(),
            before_snapshot: json!({}),
            after_snapshot: json!({"feedback": verdict}),
        },
    )
    .unwrap()
    .correction_id
}

fn ids(values: &[&str]) -> Vec<String> {
    values.iter().map(|value| value.to_string()).collect()
}

#[test]
fn verdict_vocabulary_is_closed_and_normalized() {
    assert_eq!(
        CitationVerdict::parse("Wrong-Source"),
        Some(CitationVerdict::WrongSource)
    );
    assert_eq!(
        CitationVerdict::parse("not a good source"),
        Some(CitationVerdict::WrongSource)
    );
    assert_eq!(
        CitationVerdict::parse("useful"),
        Some(CitationVerdict::Useful)
    );
    assert_eq!(CitationVerdict::parse("maybe"), None);
    assert_eq!(CitationVerdict::from_snapshot(&json!({})), None);
    assert_eq!(CitationVerdict::WrongSource.as_str(), "wrong_source");
    assert_eq!(CitationVerdict::Useful.as_str(), "useful");
}

#[test]
fn missing_table_and_empty_ids_yield_no_signals() {
    let conn = Connection::open_in_memory().unwrap();
    assert!(active_correction_signals(&conn, &ids(&["doc-a"]), None)
        .unwrap()
        .is_empty());
    let conn = brain();
    assert!(active_correction_signals(&conn, &ids(&[" "]), None)
        .unwrap()
        .is_empty());
}

#[test]
fn only_confirmed_unreverted_feedback_in_the_owning_workspace_is_active() {
    let mut conn = brain();
    let pending = feedback(
        &mut conn,
        "ws-a",
        "doc-a",
        "wrong_source",
        CorrectionStatus::Pending,
    );
    let none = active_correction_signals(&conn, &ids(&["doc-a"]), None).unwrap();
    assert!(none.citation.is_empty());

    update_correction_status_in_workspace(&mut conn, "ws-a", &pending, CorrectionStatus::Confirmed)
        .unwrap();
    let active = active_correction_signals(&conn, &ids(&["doc-a", "doc-b"]), None).unwrap();
    assert_eq!(
        active.citation["doc-a"].verdict,
        Some(CitationVerdict::WrongSource)
    );
    assert_eq!(active.citation["doc-a"].correction_id, pending);

    let only_b: BTreeSet<String> = ["ws-b".to_string()].into_iter().collect();
    let scoped = active_correction_signals(&conn, &ids(&["doc-a"]), Some(&only_b)).unwrap();
    assert!(scoped.citation.is_empty());

    revert_knowledge_correction_in_workspace(&mut conn, "ws-a", &pending).unwrap();
    let reverted = active_correction_signals(&conn, &ids(&["doc-a"]), None).unwrap();
    assert!(reverted.is_empty());
}

#[test]
fn a_moved_node_drops_feedback_recorded_in_its_old_workspace() {
    let mut conn = brain();
    feedback(
        &mut conn,
        "ws-a",
        "doc-a",
        "useful",
        CorrectionStatus::Confirmed,
    );
    conn.execute(
        "UPDATE nodes SET metadata_json = json_object('workspace_id', 'ws-b') WHERE id = 'doc-a'",
        [],
    )
    .unwrap();
    assert!(active_correction_signals(&conn, &ids(&["doc-a"]), None)
        .unwrap()
        .is_empty());
}

#[test]
fn latest_feedback_wins_and_confirmed_facts_are_reported_separately() {
    let mut conn = brain();
    feedback(
        &mut conn,
        "ws-a",
        "doc-a",
        "wrong_source",
        CorrectionStatus::Confirmed,
    );
    let later = feedback(
        &mut conn,
        "ws-a",
        "doc-a",
        "useful",
        CorrectionStatus::Confirmed,
    );
    record_knowledge_correction(
        &mut conn,
        &RecordCorrectionRequest {
            workspace_id: "ws-a".into(),
            target_kind: CorrectionTargetKind::Citation,
            target_id: "fact-a".into(),
            status: CorrectionStatus::Confirmed,
            actor: "user@example.com".into(),
            reason: "no verdict".into(),
            before_snapshot: json!({}),
            after_snapshot: json!({"note": "free text"}),
        },
    )
    .unwrap();
    record_knowledge_correction(
        &mut conn,
        &RecordCorrectionRequest {
            workspace_id: "ws-a".into(),
            target_kind: CorrectionTargetKind::Fact,
            target_id: "fact-a".into(),
            status: CorrectionStatus::Confirmed,
            actor: "user@example.com".into(),
            reason: "checked".into(),
            before_snapshot: json!({}),
            after_snapshot: json!({}),
        },
    )
    .unwrap();
    let signals = active_correction_signals(&conn, &ids(&["doc-a", "fact-a"]), None).unwrap();
    assert_eq!(signals.citation["doc-a"].correction_id, later);
    assert_eq!(
        signals.citation["doc-a"].verdict,
        Some(CitationVerdict::Useful)
    );
    assert!(!signals.citation.contains_key("fact-a"));
    assert_eq!(
        signals.confirmed["fact-a"].target_kind,
        CorrectionTargetKind::Fact
    );
}
