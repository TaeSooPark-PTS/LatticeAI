#[allow(dead_code)]
mod common;

use std::sync::Arc;

use lattice_core::db::Store;
use lattice_core::graph_write::types::IngestContentRequest;
use lattice_core::graph_write::{GraphWriter, RevisionReplace};
use lattice_retrieval::temporal::TemporalRanking;
use lattice_retrieval::{retrieve_context_for_generation, DocumentContextRequest};
use rusqlite::Connection;
use serde_json::{json, Value};

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

struct Seeded {
    old_policy: String,
    new_policy: String,
    detailed: String,
    brief: String,
}

fn seed(dir: &std::path::Path) -> Seeded {
    let path = dir.join("knowledge_graph.sqlite");
    let store = Arc::new(Store::open(&path).unwrap());
    let writer = GraphWriter::open(store, dir.join("knowledge_graph_blobs")).unwrap();
    let add = |title: &str, uri: &str, text: &str, replace: &RevisionReplace| {
        writer
            .replace_document_revision(&request(title, uri, text), replace)
            .unwrap()
            .ingest
            .node_id
    };
    let none = RevisionReplace::default();
    let old_policy = add(
        "Okapi policy",
        "/vault/okapi.md",
        "Okapi policy: refunds within 14 days.",
        &none,
    );
    let new_policy = add(
        "Okapi policy",
        "/vault/okapi.md",
        "Okapi policy: refunds within 30 days.",
        &RevisionReplace {
            prior_node_id: Some(old_policy.clone()),
            subject: "Okapi policy".into(),
            recorded_at: "2026-11-18T09:00:00".into(),
        },
    );
    let detailed = add(
        "Zebrafish rollout plan",
        "/vault/plan.md",
        "Zebrafish rollout plan: zebrafish rollout stages for the zebrafish rollout.",
        &none,
    );
    let brief = add(
        "Zebrafish note",
        "/vault/note.md",
        "Zebrafish tank was cleaned.",
        &none,
    );
    let conn = Connection::open(&path).unwrap();
    for (id, stamp) in [
        (&old_policy, "2026-01-10T09:00:00"),
        (&new_policy, "2026-11-18T09:00:00"),
        (&detailed, "2026-03-02T09:00:00"),
        (&brief, "2026-11-20T09:00:00"),
    ] {
        conn.execute(
            "UPDATE nodes SET updated_at = ?1
             WHERE id = ?2 OR json_extract(metadata_json, '$.source_node') = ?2",
            [stamp, id.as_str()],
        )
        .unwrap();
        conn.execute(
            "UPDATE nodes_v2 SET updated_at = ?1 WHERE id = ?2",
            [stamp, id.as_str()],
        )
        .unwrap();
    }
    Seeded {
        old_policy,
        new_policy,
        detailed,
        brief,
    }
}

fn position(ids: &[String], id: &str) -> Option<usize> {
    ids.iter().position(|found| found == id)
}

fn ids_of(items: &Value) -> Vec<String> {
    items
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|item| item["id"].as_str().map(str::to_string))
        .collect()
}

async fn command(install: &common::brain::Install, query: Value) -> (u16, Value) {
    let (status, _, body) = install
        .issue(&json!({
            "method": "GET",
            "path": "/api/command/search",
            "query": query,
            "request_headers": {"cookie": format!("session_token={}", install.token)},
        }))
        .await;
    (status, serde_json::from_str(&body).expect("json"))
}

#[tokio::test]
async fn command_search_applies_sort_by_and_superseded_policy() {
    let install = common::brain::Install::start_fresh().await;
    let seeded = seed(install.data_dir());
    let knowledge = |body: &Value| ids_of(&body["groups"][0]["items"]);

    let (status, body) = command(
        &install,
        json!({"q": "zebrafish rollout", "sort_by": "relevance", "limit": "20"}),
    )
    .await;
    assert_eq!(status, 200, "{body}");
    let ids = knowledge(&body);
    assert!(
        position(&ids, &seeded.detailed) < position(&ids, &seeded.brief),
        "{body}"
    );

    let (status, body) = command(
        &install,
        json!({"q": "zebrafish rollout", "sort_by": "newest", "limit": "20"}),
    )
    .await;
    assert_eq!(status, 200, "{body}");
    let ids = knowledge(&body);
    assert!(position(&ids, &seeded.brief).is_some(), "{body}");
    assert!(
        position(&ids, &seeded.brief) < position(&ids, &seeded.detailed),
        "{body}"
    );

    let (_, body) = command(&install, json!({"q": "okapi policy", "limit": "20"})).await;
    let ids = knowledge(&body);
    assert!(ids.contains(&seeded.new_policy), "{body}");
    assert!(!ids.contains(&seeded.old_policy), "{body}");

    let (status, body) = command(&install, json!({"q": "okapi", "sort_by": "oldest"})).await;
    assert_eq!(status, 422, "{body}");
    assert_eq!(body["detail"][0]["type"], "literal_error");
}

#[tokio::test]
async fn memory_recall_excludes_superseded_and_honours_time_words() {
    let install = common::brain::Install::start_fresh().await;
    let seeded = seed(install.data_dir());
    let recall = |query: &'static str| {
        let cookie = format!("session_token={}", install.token);
        json!({
            "method": "POST",
            "path": "/api/memory/recall",
            "query": {},
            "request_headers": {"cookie": cookie},
            "request_body": {"query": query, "limit": 20},
        })
    };
    let (status, _, body) = install.issue(&recall("okapi")).await;
    assert_eq!(status, 200, "{body}");
    let body: Value = serde_json::from_str(&body).unwrap();
    let ids = ids_of(&body["results"]);
    assert!(ids.contains(&seeded.new_policy), "{body}");
    assert!(!ids.contains(&seeded.old_policy), "{body}");
    assert_eq!(body["results"][0]["status"], "active");
    assert!(body["results"][0]["updated_at"].is_string(), "{body}");
    assert_eq!(body["temporal"]["pipeline"][5], "sort");

    let (status, _, body) = install.issue(&recall("latest zebrafish rollout")).await;
    assert_eq!(status, 200, "{body}");
    let body: Value = serde_json::from_str(&body).unwrap();
    let stamps: Vec<&str> = body["results"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|row| row["updated_at"].as_str())
        .collect();
    assert!(stamps.len() > 1, "{body}");
    let mut newest_first = stamps.clone();
    newest_first.sort_by(|a, b| b.cmp(a));
    assert_eq!(stamps, newest_first, "{body}");
}

#[test]
fn document_context_runs_the_temporal_pipeline_only_when_asked() {
    let dir = tempfile::tempdir().unwrap();
    let seeded = seed(dir.path());
    let conn = Connection::open(dir.path().join("knowledge_graph.sqlite")).unwrap();
    let build = |query: &str, temporal: Option<TemporalRanking>| {
        retrieve_context_for_generation(
            &conn,
            &DocumentContextRequest {
                query: query.into(),
                include_self_model: false,
                now_secs: lattice_core::parse_iso(Some("2026-11-26T12:00:00")).unwrap(),
                temporal,
                ..DocumentContextRequest::default()
            },
        )
        .unwrap()
    };
    let legacy = build("okapi policy", None);
    assert!(ids_of(&legacy["sources"]).contains(&seeded.old_policy));
    assert!(legacy.get("temporal").is_none());

    let ranked = build("okapi policy", Some(TemporalRanking::default()));
    let ids = ids_of(&ranked["sources"]);
    assert!(ids.contains(&seeded.new_policy), "{ranked}");
    assert!(!ids.contains(&seeded.old_policy), "{ranked}");
    assert_eq!(ranked["temporal"]["superseded"]["policy"], "exclude");
}
