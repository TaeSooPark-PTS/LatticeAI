use std::sync::Arc;

use lattice_core::db::Store;
use lattice_core::graph_write::types::IngestContentRequest;
use lattice_core::graph_write::{GraphWriter, RevisionReplace};
use lattice_core::{parse_iso, LocalEmbeddingModel};
use lattice_retrieval::service_hybrid::{service_hybrid_search, ServiceHybridOptions};
use lattice_retrieval::temporal::{ranked_search, SortBy, TemporalRanking};
use rusqlite::Connection;
use serde_json::Value;

fn request(title: &str, uri: &str, text: &str) -> IngestContentRequest {
    IngestContentRequest {
        source_type: "note".into(),
        title: title.into(),
        text: text.into(),
        source_uri: Some(uri.into()),
        workspace_id: Some("personal".into()),
        ..Default::default()
    }
}

struct Brain {
    _dir: tempfile::TempDir,
    path: std::path::PathBuf,
    old_policy: String,
    new_policy: String,
    meeting: String,
}

fn brain() -> Brain {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("knowledge_graph.sqlite");
    let store = Arc::new(Store::open(&path).unwrap());
    let writer = GraphWriter::open(store, dir.path().join("blobs")).unwrap();
    let first = writer
        .replace_document_revision(
            &request(
                "Refund policy",
                "/vault/refund.md",
                "Refund policy: customers may request a refund within 14 days.",
            ),
            &RevisionReplace::default(),
        )
        .unwrap();
    let second = writer
        .replace_document_revision(
            &request(
                "Refund policy",
                "/vault/refund.md",
                "Refund policy: customers may request a refund within 30 days.",
            ),
            &RevisionReplace {
                prior_node_id: Some(first.ingest.node_id.clone()),
                subject: "Refund policy".into(),
                recorded_at: "2026-11-18T09:00:00".into(),
            },
        )
        .unwrap();
    let meeting = writer
        .replace_document_revision(
            &request(
                "Weekly meeting",
                "/vault/meeting.md",
                "Weekly meeting: the refund policy review moved to Friday.",
            ),
            &RevisionReplace::default(),
        )
        .unwrap();
    let conn = Connection::open(&path).unwrap();
    for (id, stamp) in [
        (&first.ingest.node_id, "2026-01-10T09:00:00"),
        (&second.ingest.node_id, "2026-11-18T09:00:00"),
        (&meeting.ingest.node_id, "2026-06-02T09:00:00"),
    ] {
        conn.execute(
            "UPDATE nodes SET updated_at = ?1
             WHERE id = ?2 OR json_extract(metadata_json, '$.source_node') = ?2",
            [stamp, id.as_str()],
        )
        .unwrap();
    }
    Brain {
        old_policy: first.ingest.node_id,
        new_policy: second.ingest.node_id,
        meeting: meeting.ingest.node_id,
        path,
        _dir: dir,
    }
}

fn now() -> f64 {
    parse_iso(Some("2026-11-26T15:30:00")).unwrap()
}

fn search(brain: &Brain, query: &str, ranking: &TemporalRanking) -> Value {
    let conn = Connection::open(&brain.path).unwrap();
    let model = LocalEmbeddingModel::new(384);
    ranked_search(&conn, query, now(), ranking, 10, true, |call| {
        let options = ServiceHybridOptions {
            limit: call.fetch_limit,
            now_secs: now(),
            recency: Some(call.decay),
            include_historical: call.include_historical,
            ..ServiceHybridOptions::default()
        };
        service_hybrid_search(&conn, &model, &call.search_query, &options)
    })
    .unwrap()
}

fn owners(report: &Value) -> Vec<String> {
    report["matches"]
        .as_array()
        .unwrap()
        .iter()
        .map(|m| {
            m["metadata"]["source_node"]
                .as_str()
                .or_else(|| m["node_id"].as_str())
                .unwrap_or_default()
                .to_string()
        })
        .collect()
}

#[test]
fn a_synced_revision_hides_its_predecessor_from_current_state_queries() {
    let brain = brain();
    let report = search(&brain, "refund policy days", &TemporalRanking::default());
    let found = owners(&report);
    assert!(found.contains(&brain.new_policy), "{found:?}");
    assert!(!found.contains(&brain.old_policy), "{found:?}");
    assert!(
        report["temporal"]["superseded"]["excluded"]
            .as_u64()
            .unwrap()
            >= 1
    );
    for item in report["matches"].as_array().unwrap() {
        assert_eq!(item["status"], "active");
        assert!(item["source_scores"]["age_decay"].is_number());
    }
    assert_eq!(report["sort_by"], "relevance");
}

#[test]
fn a_history_question_keeps_the_predecessor_but_ranks_it_below_its_successor() {
    let brain = brain();
    let report = search(
        &brain,
        "이전 버전 refund policy days",
        &TemporalRanking::default(),
    );
    let items = report["matches"].as_array().unwrap();
    let old = items
        .iter()
        .position(|m| m["node_id"] == brain.old_policy.as_str())
        .expect("history keeps the superseded document");
    assert_eq!(items[old]["status"], "superseded");
    assert_eq!(
        items[old]["temporal"]["superseded_by"],
        brain.new_policy.as_str()
    );
    assert_eq!(report["temporal"]["superseded"]["policy"], "penalize");
}

#[test]
fn a_time_window_prefilters_by_updated_at_and_falls_back_when_empty() {
    let brain = brain();
    let windowed = search(&brain, "지난주 refund policy", &TemporalRanking::default());
    assert_eq!(
        windowed["temporal"]["prefilter"]["applied"], true,
        "{}",
        windowed["temporal"]
    );
    assert_eq!(owners(&windowed), vec![brain.new_policy.clone()]);
    assert_eq!(windowed["sort_by"], "newest");

    let empty = search(
        &brain,
        "since 2027-01-01 refund policy",
        &TemporalRanking::default(),
    );
    assert_eq!(
        empty["temporal"]["prefilter"]["fallback"],
        "no_documents_in_window"
    );
    let found = owners(&empty);
    assert!(
        found.contains(&brain.new_policy) && found.contains(&brain.meeting),
        "{found:?}"
    );
    let stamps: Vec<f64> = empty["matches"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|m| parse_iso(m["updated_at"].as_str()))
        .collect();
    assert!(
        stamps.windows(2).all(|pair| pair[0] >= pair[1]),
        "fallback still sorts newest first: {stamps:?}"
    );
}

#[test]
fn sort_by_newest_reorders_the_relevant_top_k_by_updated_at() {
    let brain = brain();
    let relevance = TemporalRanking::default().with_request(SortBy::Relevance, false, None, None);
    let newest = TemporalRanking::default().with_request(SortBy::Newest, false, None, None);
    let by_score = search(&brain, "refund policy", &relevance);
    let by_date = search(&brain, "refund policy", &newest);
    let stamps: Vec<f64> = by_date["matches"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|m| parse_iso(m["updated_at"].as_str()))
        .collect();
    assert!(
        stamps.windows(2).all(|pair| pair[0] >= pair[1]),
        "{stamps:?}"
    );
    assert!(by_date["matches"].as_array().unwrap().len() <= 12);
    assert_eq!(by_score["temporal"]["sort"]["applied"], "relevance");
    assert_eq!(
        by_date["temporal"]["sort"]["order"],
        "rerank_then_relevance_gate_then_updated_at_desc"
    );
}
