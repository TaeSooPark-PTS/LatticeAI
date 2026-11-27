use std::collections::BTreeSet;

use lattice_core::graph_write::schema::bootstrap;
use lattice_core::LocalEmbeddingModel;
use lattice_retrieval::hybrid::{hybrid_search, HybridOptions};
use lattice_retrieval::service::{vector_search, Scope};
use lattice_retrieval::service_hybrid::{service_hybrid_search, ServiceHybridOptions};
use rusqlite::{params, Connection};
use serde_json::{json, Value};

const STAMP: &str = "2026-04-01T00:00:00";
const QUERY: &str = "phoenix rollout plan";
const QUIET: &str = "quiet-plan";
const RETIRED: &str = "quiet-retired";

fn insert(
    conn: &Connection,
    model: &LocalEmbeddingModel,
    id: &str,
    workspace: &str,
    text: &str,
    superseded_by: Option<&str>,
) {
    let meta = json!({"workspace_id": workspace, "superseded_by": superseded_by}).to_string();
    conn.execute(
        "INSERT INTO nodes(id, type, title, summary, raw_json, metadata_json, created_at, updated_at)
         VALUES (?1, 'Document', ?2, ?2, '{}', ?3, ?4, ?4)",
        params![id, text, meta, STAMP],
    )
    .expect("node");
    conn.execute(
        "INSERT INTO nodes_v2(id, type, label, summary, attrs, workspace_id, superseded_by, importance_score, created_at, updated_at)
         VALUES (?1, 'Document', ?2, ?2, ?3, ?4, ?5, 0.8, ?6, ?6)",
        params![id, text, meta, workspace, superseded_by, STAMP],
    )
    .expect("node v2");
    conn.execute(
        "INSERT INTO vector_embeddings(item_id, item_type, source_node, text_hash, embedding, embedding_dim, embedding_model, metadata_json, indexed_at)
         VALUES (?1, 'node', ?1, 'h', ?2, ?3, ?4, '{}', ?5)",
        params![
            id,
            model.encode(&model.embed(text)),
            model.dim() as i64,
            model.model_id(),
            STAMP
        ],
    )
    .expect("vector");
}

fn crowded_brain(model: &LocalEmbeddingModel) -> Connection {
    let mut conn = Connection::open_in_memory().expect("open");
    {
        let tx = conn.transaction().expect("tx");
        bootstrap(&tx, STAMP).expect("bootstrap");
        tx.commit().expect("commit");
    }
    for index in 0..40 {
        insert(
            &conn,
            model,
            &format!("noisy-{index:02}"),
            "noisy",
            QUERY,
            None,
        );
    }
    insert(
        &conn,
        model,
        QUIET,
        "quiet",
        "phoenix rollout plan for the quiet team",
        None,
    );
    insert(
        &conn,
        model,
        RETIRED,
        "quiet",
        "phoenix rollout plan retired draft",
        Some(QUIET),
    );
    conn
}

fn ids(payload: &Value, key: &str) -> Vec<String> {
    payload[key]
        .as_array()
        .map(|items| {
            items
                .iter()
                .filter_map(|item| item.get("node_id").or_else(|| item.get("id")))
                .filter_map(Value::as_str)
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default()
}

#[test]
fn a_crowded_neighbour_workspace_cannot_push_in_scope_vectors_out_of_the_top_k() {
    let model = LocalEmbeddingModel::new(384);
    let conn = crowded_brain(&model);
    let quiet = Scope::for_workspace("quiet");

    let current = vector_search(&conn, &model, QUERY, 5, 0.0, &quiet, false).expect("search");
    assert_eq!(ids(&current, "matches"), [QUIET]);

    let history = vector_search(&conn, &model, QUERY, 5, 0.0, &quiet, true).expect("history");
    let mut seen = ids(&history, "matches");
    seen.sort();
    assert_eq!(seen, [QUIET, RETIRED]);

    let noisy = vector_search(
        &conn,
        &model,
        QUERY,
        5,
        0.0,
        &Scope::for_workspace("noisy"),
        false,
    )
    .expect("noisy");
    let found = ids(&noisy, "matches");
    assert_eq!(found.len(), 5);
    assert!(found.iter().all(|id| id.starts_with("noisy-")));
}

#[test]
fn the_no_worker_chat_lane_keeps_its_vector_channel_inside_the_workspace() {
    let model = LocalEmbeddingModel::new(384);
    let conn = crowded_brain(&model);
    let options = ServiceHybridOptions {
        limit: 5,
        vector_limit: 5,
        scope: Scope::for_workspace("quiet"),
        ..ServiceHybridOptions::default()
    };
    let report = service_hybrid_search(&conn, &model, QUERY, &options).expect("hybrid");
    let vector_hits: Vec<String> = report["matches"]
        .as_array()
        .expect("matches")
        .iter()
        .filter(|item| {
            item["sources"]
                .as_array()
                .is_some_and(|sources| sources.iter().any(|source| source == "vector"))
        })
        .filter_map(|item| item["id"].as_str().map(str::to_string))
        .collect();
    assert_eq!(vector_hits, [QUIET]);
    assert!(ids(&report, "matches")
        .iter()
        .all(|id| !id.starts_with("noisy-")));
}

#[test]
fn the_scoped_hybrid_search_keeps_in_scope_vector_hits() {
    let model = LocalEmbeddingModel::new(384);
    let conn = crowded_brain(&model);
    let options = HybridOptions {
        top_k: 5,
        vector_limit: Some(5),
        allowed_workspaces: Some(BTreeSet::from(["quiet".to_string()])),
        ..HybridOptions::default()
    };
    let report = hybrid_search(&conn, &model, QUERY, &options).expect("hybrid");
    let found = ids(&report, "matches");
    assert!(found.contains(&QUIET.to_string()), "{found:?}");
    assert!(
        found.iter().all(|id| !id.starts_with("noisy-")),
        "{found:?}"
    );
    let vector_scored = report["matches"]
        .as_array()
        .expect("matches")
        .iter()
        .any(|item| item["node_id"] == QUIET && item["scores"]["vector"].as_f64() > Some(0.0));
    assert!(vector_scored, "{report}");
}

#[test]
fn workspace_recall_blends_the_in_scope_vector_hit() {
    let model = LocalEmbeddingModel::new(384);
    let conn = crowded_brain(&model);
    let out = lattice_retrieval::memory_api::recall::recall(
        &conn,
        &json!({}),
        true,
        QUERY,
        "",
        Some("quiet"),
        5,
    )
    .expect("recall");
    let report = serde_json::to_value(out).expect("encode");
    let rows = report["results"].as_array().expect("results");
    assert!(
        rows.iter()
            .all(|row| !row["id"].as_str().unwrap_or_default().starts_with("noisy-")),
        "{report}"
    );
    let quiet = rows
        .iter()
        .find(|row| row["id"] == QUIET)
        .unwrap_or_else(|| panic!("{report}"));
    assert!(quiet["vector_score"].as_f64() > Some(0.0), "{report}");
}
