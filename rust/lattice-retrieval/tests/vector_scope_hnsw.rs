use lattice_core::graph_write::schema::bootstrap;
use lattice_core::LocalEmbeddingModel;
use lattice_retrieval::service::Scope;
use lattice_retrieval::vector_alignment::{
    vector_search_aligned, VectorQueryInput, VectorSearchOptions,
};
use rusqlite::{params, Connection};
use serde_json::json;

const STAMP: &str = "2026-04-01T00:00:00";
const QUERY: &str = "phoenix rollout plan";

fn insert(conn: &Connection, model: &LocalEmbeddingModel, id: &str, workspace: &str, text: &str) {
    let meta = json!({"workspace_id": workspace}).to_string();
    conn.execute(
        "INSERT INTO nodes(id, type, title, summary, raw_json, metadata_json, created_at, updated_at)
         VALUES (?1, 'Document', ?2, ?2, '{}', ?3, ?4, ?4)",
        params![id, text, meta, STAMP],
    )
    .expect("node");
    conn.execute(
        "INSERT INTO nodes_v2(id, type, label, summary, attrs, workspace_id, importance_score, created_at, updated_at)
         VALUES (?1, 'Document', ?2, ?2, ?3, ?4, 0.8, ?5, ?5)",
        params![id, text, meta, workspace, STAMP],
    )
    .expect("node v2");
    conn.execute(
        "INSERT INTO vector_embeddings(item_id, item_type, source_node, text_hash, embedding, embedding_dim, embedding_model, metadata_json, indexed_at)
         VALUES (?1, 'node', ?1, ?2, ?3, ?4, ?5, '{}', ?6)",
        params![
            id,
            format!("h-{id}"),
            model.encode(&model.embed(text)),
            model.dim() as i64,
            model.model_id(),
            STAMP
        ],
    )
    .expect("vector");
}

#[test]
fn an_approximate_index_starved_by_a_neighbour_workspace_falls_back_to_an_exact_scoped_scan() {
    assert!(std::env::var_os("LATTICEAI_VECTOR_INDEX").is_none());
    let model = LocalEmbeddingModel::new(384);
    let mut conn = Connection::open_in_memory().expect("open");
    {
        let tx = conn.transaction().expect("tx");
        bootstrap(&tx, STAMP).expect("bootstrap");
        tx.commit().expect("commit");
    }
    for index in 0..300 {
        insert(
            &conn,
            &model,
            &format!("noisy-{index:03}"),
            "noisy",
            &format!("{QUERY} {index}"),
        );
    }
    insert(&conn, &model, "quiet-exact", "quiet", QUERY);
    for index in 0..560 {
        insert(
            &conn,
            &model,
            &format!("quiet-{index:03}"),
            "quiet",
            &format!(
                "glacier {index} turbine quartz meadow lantern {}",
                index * 7
            ),
        );
    }
    let scope = Scope::for_workspace("quiet");
    let payload = vector_search_aligned(
        &conn,
        VectorQueryInput::Model(&model),
        QUERY,
        &VectorSearchOptions {
            limit: 10,
            min_score: -1.0,
            scope: Some(&scope),
            include_historical: false,
        },
    )
    .expect("search");
    let found: Vec<&str> = payload["matches"]
        .as_array()
        .expect("matches")
        .iter()
        .filter_map(|item| item["id"].as_str())
        .collect();
    assert_eq!(found.len(), 10, "{payload}");
    assert_eq!(found[0], "quiet-exact");
    assert!(found.iter().all(|id| id.starts_with("quiet-")), "{found:?}");
}
