use std::sync::Arc;

use axum::extract::State;
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use axum::routing::get;
use axum::Router;
use lattice_auth::response::json_response;
use lattice_auth::{AuthState, OrderedMap};
use lattice_core::db::{RuntimeConfig, Store};
use lattice_core::graph_write::types::RebuildRequest;
use lattice_core::graph_write::GraphWriter;
use lattice_core::messages::{self, LANGUAGE_HEADER};
use lattice_core::worker::WorkerSeamClient;
use lattice_core::CoreError;
use serde_json::{json, Value};

pub const MOUNTED: &[(&str, &str)] = &[
    ("GET", "/api/index/queue"),
    ("GET", "/api/index/status"),
    ("POST", "/api/index/drain"),
    ("POST", "/api/index/rebuild"),
];

pub const MIN_DRAIN_LIMIT: i64 = 1;
pub const MAX_DRAIN_LIMIT: i64 = 100;
pub const WORKER_EMBED_ENV: &str = "LATTICEAI_JOBS_WORKER_EMBED";

pub const VECTOR_INDEX_ENV: &str = "LATTICEAI_VECTOR_INDEX";
pub const DEFAULT_VECTOR_INDEX: &str = "hnsw";
pub const TARGET_REBUILD_MS: i64 = 10_000;

pub const SQLITE_VEC_REASON: &str = "sqlite-vec is not linked into the Rust gateway's SQLite build; using real brute-force cosine fallback, not sqlite-vec ANN";
pub const HONEST_FALLBACK: &str = "Vector search is available through the deterministic brute-force cosine backend. sqlite-vec ANN is unavailable.";

pub type Gate = Arc<dyn Fn(&HeaderMap) -> Option<Response> + Send + Sync>;

#[derive(Clone)]
pub struct IndexApiState {
    auth: Arc<AuthState>,
    store: Option<Arc<Store>>,
    config: RuntimeConfig,
    gate_read: Option<Gate>,
    graph: Option<GraphWriter>,
    seam: Option<WorkerSeamClient>,
    worker_embed: bool,
}

impl std::fmt::Debug for IndexApiState {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("IndexApiState")
            .field("graph_enabled", &self.store.is_some())
            .field("gated", &self.gate_read.is_some())
            .finish()
    }
}

impl IndexApiState {
    pub fn new(auth: Arc<AuthState>, store: Option<Arc<Store>>, config: RuntimeConfig) -> Self {
        Self {
            auth,
            store,
            config,
            gate_read: None,
            graph: None,
            seam: None,
            worker_embed: false,
        }
    }

    pub fn with_graph(mut self, graph: GraphWriter) -> Self {
        self.graph = Some(graph);
        self
    }

    pub fn with_seam(mut self, seam: WorkerSeamClient) -> Self {
        self.seam = Some(seam);
        self
    }

    pub fn with_worker_embed(mut self, on: bool) -> Self {
        self.worker_embed = on;
        self
    }

    pub fn with_read_gate(mut self, gate: Gate) -> Self {
        self.gate_read = Some(gate);
        self
    }

    pub fn db_path(&self) -> std::path::PathBuf {
        self.config.graph_db_path()
    }
}

pub fn router(state: Arc<IndexApiState>) -> Router {
    Router::new()
        .route("/api/index/queue", get(queue))
        .route("/api/index/status", get(status))
        .route("/api/index/drain", axum::routing::post(drain))
        .route("/api/index/rebuild", axum::routing::post(rebuild))
        .with_state(state)
}

fn language(headers: &HeaderMap) -> &'static str {
    messages::resolve_language(
        headers
            .get(LANGUAGE_HEADER)
            .and_then(|value| value.to_str().ok()),
        headers
            .get(axum::http::header::ACCEPT_LANGUAGE)
            .and_then(|value| value.to_str().ok()),
    )
}

fn detail(status: u16, text: &str) -> Response {
    let body = serde_json::to_string(&json!({ "detail": text })).unwrap_or_default();
    json_response(
        StatusCode::from_u16(status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR),
        &body,
        None,
    )
}

fn ok(value: &Value) -> Response {
    let body = serde_json::to_string(value).unwrap_or_else(|_| "null".into());
    json_response(StatusCode::OK, &body, None)
}

fn admit(state: &IndexApiState, headers: &HeaderMap) -> Result<(), Response> {
    state.auth.require_user(headers)?;
    if let Some(gate) = state.gate_read.as_ref() {
        if let Some(refusal) = gate(headers) {
            return Err(refusal);
        }
    }
    if state.store.is_none() {
        return Err(detail(
            503,
            &messages::text("capture.ingestion_disabled", language(headers), &[]),
        ));
    }
    Ok(())
}

async fn queue(State(state): State<Arc<IndexApiState>>, headers: HeaderMap) -> Response {
    if let Err(refusal) = admit(&state, &headers) {
        return refusal;
    }
    let db = state.db_path();
    let counts = match tokio::task::spawn_blocking(move || crate::queue::read_counts(&db)).await {
        Ok(counts) => counts,
        Err(error) => return detail(500, &format!("queue read failed: {error}")),
    };
    let mut payload = OrderedMap::new();
    payload.insert("available", json!(counts.available));
    let mut by_status = OrderedMap::new();
    for status in crate::queue::VECTOR_JOB_STATUSES {
        by_status.insert(status, json!(counts.get(status)));
    }
    for (status, total) in &counts.counts {
        if !crate::queue::VECTOR_JOB_STATUSES.contains(&status.as_str()) {
            by_status.insert(status.clone(), json!(total));
        }
    }
    payload.insert(
        "counts",
        serde_json::to_value(by_status).unwrap_or(Value::Null),
    );
    payload.insert("pending", json!(counts.pending));
    ok(&serde_json::to_value(payload).unwrap_or(Value::Null))
}

async fn status(State(state): State<Arc<IndexApiState>>, headers: HeaderMap) -> Response {
    if let Err(refusal) = state.auth.require_user(&headers) {
        return refusal;
    }
    let Some(store) = state.store.clone() else {
        return detail(
            404,
            "지식 그래프가 비활성화되어 있습니다. LATTICEAI_ENABLE_GRAPH=true 설정 후 다시 시도해 주세요.",
        );
    };
    match store.read(index_status).await {
        Ok(value) => ok(&value),
        Err(CoreError::InvalidRequest(message)) => detail(404, &message),
        Err(error) => detail(500, &format!("Internal Server Error: {error}")),
    }
}

async fn drain(
    State(state): State<Arc<IndexApiState>>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if let Err(refusal) = admit(&state, &headers) {
        return refusal;
    }
    let requested = if body.is_empty() {
        None
    } else {
        serde_json::from_slice::<Value>(&body)
            .ok()
            .and_then(|v| v.get("limit").and_then(Value::as_i64))
    };
    let limit = requested.unwrap_or(crate::DEFAULT_DRAIN_LIMIT as i64);
    if !(MIN_DRAIN_LIMIT..=MAX_DRAIN_LIMIT).contains(&limit) {
        return index_api_http_error(&headers, limit);
    }
    let Some(graph) = state.graph.clone() else {
        return detail(503, "the knowledge-graph write engine is not configured");
    };
    let db = state.db_path();
    let seam = worker_embed_seam(&state);
    let outcome = match engine::drain_once(graph, db, limit as usize, seam).await {
        Ok(outcome) => outcome,
        Err(error) => return detail(500, &error),
    };
    let counts = crate::queue::read_counts(&state.db_path());
    let mut payload = OrderedMap::new();
    payload.insert("claimed", json!(outcome.claimed));
    payload.insert("indexed", json!(outcome.indexed));
    payload.insert("retried", json!(outcome.retried));
    payload.insert("failed", json!(outcome.failed));
    payload.insert("detail", json!(outcome.detail));
    payload.insert("limit", json!(limit));
    payload.insert("scope", json!("machine"));
    let mut queue = OrderedMap::new();
    queue.insert("available", json!(counts.available));
    payload.insert("queue", serde_json::to_value(queue).unwrap_or(Value::Null));
    ok(&serde_json::to_value(payload).unwrap_or(Value::Null))
}

pub(crate) fn should_use_worker_embed(configured: bool, db_path: &std::path::Path) -> bool {
    let env_val = std::env::var(WORKER_EMBED_ENV).ok();
    should_use_worker_embed_with_env(configured, env_val.as_deref(), db_path)
}

fn should_use_worker_embed_with_env(
    configured: bool,
    env_override: Option<&str>,
    db_path: &std::path::Path,
) -> bool {
    if let Some(raw) = env_override {
        let normalized = raw.trim().to_ascii_lowercase();
        if matches!(normalized.as_str(), "0" | "false" | "no" | "off") {
            return false;
        }
        if matches!(normalized.as_str(), "1" | "true" | "yes" | "on") {
            return true;
        }
    }
    if configured {
        return true;
    }
    if db_path.exists() {
        if let Ok(conn) = lattice_core::db::open_read_only(db_path) {
            let raw: Option<String> = conn
                .query_row(
                    "SELECT value FROM graph_meta WHERE key='embedding_index_contract'",
                    [],
                    |row| row.get(0),
                )
                .ok();
            if let Some(contract) = raw.and_then(|s| {
                serde_json::from_str::<lattice_core::embedding_contract::EmbeddingIndexContract>(&s)
                    .ok()
            }) {
                if contract.is_semantic() {
                    return true;
                }
            }
        }
    }
    false
}

fn worker_embed_seam(state: &IndexApiState) -> Option<WorkerSeamClient> {
    if should_use_worker_embed(state.worker_embed, &state.config.graph_db_path()) {
        state.seam.clone()
    } else {
        None
    }
}

fn index_api_http_error(headers: &HeaderMap, _limit: i64) -> Response {
    let lang = language(headers);
    let min = MIN_DRAIN_LIMIT.to_string();
    let max = MAX_DRAIN_LIMIT.to_string();
    detail(
        422,
        &messages::text(
            "index.limit_out_of_range",
            lang,
            &[("min", min.as_str()), ("max", max.as_str())],
        ),
    )
}

async fn rebuild(State(state): State<Arc<IndexApiState>>, headers: HeaderMap) -> Response {
    if let Err(refusal) = admit(&state, &headers) {
        return refusal;
    }
    let Some(graph) = state.graph.clone() else {
        return detail(503, "the knowledge-graph write engine is not configured");
    };
    match tokio::task::spawn_blocking(move || {
        graph.rebuild_vector_index(&RebuildRequest::default())
    })
    .await
    {
        Ok(Ok(outcome)) => ok(&outcome.to_json()),
        Ok(Err(error)) => detail(500, &error.to_string()),
        Err(error) => detail(500, &error.to_string()),
    }
}

pub(crate) mod engine;
mod status;

pub use engine::{claim_pending, drain_claimed, drain_queue, embed_items_for_nodes};
pub use status::index_status;

#[cfg(test)]
mod tests {
    use super::status::{
        py_str, sha256_text, source_items, truthy, vector_index_selection, vector_text_for_node,
    };
    use super::*;
    use lattice_core::pytext::clean_text;
    use lattice_core::LocalEmbeddingModel;
    use rusqlite::Connection;
    use serde_json::{json, Value};

    fn store() -> (tempfile::TempDir, Connection) {
        let dir = tempfile::tempdir().expect("tempdir");
        let conn = Connection::open(dir.path().join("knowledge_graph.sqlite")).expect("open");
        conn.execute_batch(
            "CREATE TABLE nodes(id TEXT PRIMARY KEY, type TEXT, title TEXT, summary TEXT,
                                metadata_json TEXT, created_at TEXT, updated_at TEXT);
             CREATE TABLE chunks(id TEXT PRIMARY KEY, source_node TEXT, text TEXT,
                                 metadata_json TEXT, created_at TEXT);
             CREATE TABLE vector_embeddings(item_id TEXT PRIMARY KEY, item_type TEXT,
               source_node TEXT, embedding BLOB, embedding_dim INT, embedding_model TEXT,
               text_hash TEXT, metadata_json TEXT, indexed_at TEXT);
             CREATE TABLE vector_index_operations(id INTEGER PRIMARY KEY, operation TEXT,
               status TEXT, requested_at TEXT, started_at TEXT, completed_at TEXT,
               items_total INT, items_indexed INT, items_skipped INT, error_message TEXT,
               metadata_json TEXT);
             CREATE TABLE graph_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
             INSERT INTO nodes VALUES
               ('n1','Decision','Ranking','we chose alpha fusion','{\"source\":\"notes\"}',
                '2026-01-01T00:00:00','2026-01-02T00:00:00'),
               ('n2','Concept','Lattice','','{}','2026-01-01T00:00:00','2026-01-01T00:00:00'),
               ('k1','Chunk','chunk one','','{}','2026-01-01T00:00:00','2026-01-01T00:00:00');
             INSERT INTO chunks VALUES ('k1','n1','chunk body','{}','2026-01-01T00:00:00');",
        )
        .expect("schema");
        (dir, conn)
    }

    #[test]
    fn the_route_table_includes_native_drain_and_rebuild() {
        assert_eq!(MOUNTED.len(), 4);
        assert!(MOUNTED.iter().any(|(_, path)| path.ends_with("/drain")));
        assert!(MOUNTED.iter().any(|(_, path)| path.ends_with("/rebuild")));
    }

    #[test]
    fn the_source_walk_is_nodes_then_chunks() {
        let (_dir, conn) = store();
        let items = source_items(&conn).unwrap();
        let ids: Vec<&str> = items.iter().map(|item| item.item_id.as_str()).collect();
        assert_eq!(ids, vec!["n1", "n2", "k1"]);
        assert_eq!(items[0].item_type, "node");
        assert_eq!(items[0].text, "Ranking we chose alpha fusion notes");
        assert_eq!(items[2].item_type, "chunk");
        assert_eq!(items[2].text, "chunk body");
        assert_eq!(
            items[2].metadata["parent_source_node"],
            json!("n1"),
            "a chunk carries its parent in metadata, not in source_node"
        );
        assert_eq!(items[2].source_node, "k1");
    }

    #[test]
    fn every_unindexed_item_is_missing_and_the_status_says_so() {
        let (_dir, conn) = store();
        let payload = index_status(&conn).unwrap();
        assert_eq!(payload["status"], json!("needs_reindex"));
        assert_eq!(payload["source_items"], json!(3));
        assert_eq!(payload["missing_items"], json!(3));
        assert_eq!(payload["ready_items"], json!(0));
        assert_eq!(payload["pending_items"], json!(3));
        assert_eq!(payload["scale"]["coverage_ratio"], json!(0.0));
        assert_eq!(
            payload["scale"]["backlog_reasons"]["missing_vector"],
            json!(3)
        );
        assert_eq!(payload["scale"]["backlog_by_item_type"]["node"], json!(2));
        assert_eq!(payload["scale"]["backlog_by_item_type"]["chunk"], json!(1));
        assert_eq!(
            payload["scale"]["backlog_samples"]
                .as_array()
                .unwrap()
                .len(),
            3
        );
        assert_eq!(payload["embedder"]["recorded"], Value::Null);
        assert_eq!(payload["embedder"]["stale_embedder"], json!(false));
        assert_eq!(payload["storage"]["backend"], json!("sqlite"));
        assert_eq!(payload["storage"]["fts_enabled"], json!(false));
        assert_eq!(payload["storage"]["vector_index"]["name"], json!("hnsw"));
        assert_eq!(
            payload["scale"]["latency_budget"]["within_target"],
            Value::Null
        );
    }

    #[test]
    fn an_indexed_item_is_ready_and_a_changed_one_is_stale() {
        let (_dir, conn) = store();
        let model = LocalEmbeddingModel::from_env();
        let items = source_items(&conn).unwrap();
        for item in &items {
            conn.execute(
                "INSERT INTO vector_embeddings(item_id, item_type, source_node, embedding,
                    embedding_dim, embedding_model, text_hash, metadata_json, indexed_at)
                 VALUES (?1, ?2, ?3, x'00', ?4, ?5, ?6, '{}', '2026-01-02T00:00:00')",
                rusqlite::params![
                    item.item_id,
                    item.item_type,
                    item.source_node,
                    model.dim() as i64,
                    model.model_id(),
                    sha256_text(&clean_text(&item.text)),
                ],
            )
            .unwrap();
        }
        let payload = index_status(&conn).unwrap();
        assert_eq!(payload["status"], json!("ready"));
        assert_eq!(payload["ready_items"], json!(3));
        assert_eq!(payload["pending_items"], json!(0));
        assert_eq!(payload["indexed_items"], json!(3));
        assert_eq!(payload["scale"]["coverage_ratio"], json!(1.0));
        assert_eq!(payload["scale"]["coverage_percent"], json!(100.0));

        conn.execute("UPDATE nodes SET summary='changed' WHERE id='n1'", [])
            .unwrap();
        let payload = index_status(&conn).unwrap();
        assert_eq!(payload["stale_items"], json!(1));
        assert_eq!(
            payload["scale"]["backlog_reasons"]["text_changed"],
            json!(1)
        );

        conn.execute(
            "UPDATE vector_embeddings SET embedding_model='other' WHERE item_id='n2'",
            [],
        )
        .unwrap();
        let payload = index_status(&conn).unwrap();
        assert_eq!(
            payload["scale"]["backlog_reasons"]["model_changed"],
            json!(1)
        );
    }

    #[test]
    fn an_orphaned_vector_row_recommends_a_full_rebuild() {
        let (_dir, conn) = store();
        conn.execute(
            "INSERT INTO vector_embeddings(item_id, item_type, source_node, embedding,
                embedding_dim, embedding_model, text_hash, metadata_json, indexed_at)
             VALUES ('ghost','node','ghost', x'00', 384, 'm', 'h', '{}', '2026-01-01T00:00:00')",
            [],
        )
        .unwrap();
        let payload = index_status(&conn).unwrap();
        assert_eq!(payload["scale"]["orphaned_items"], json!(1));
        assert_eq!(payload["scale"]["full_rebuild_recommended"], json!(true));
    }

    #[test]
    fn a_recorded_fingerprint_that_disagrees_is_a_stale_embedder() {
        let (_dir, conn) = store();
        conn.execute(
            "INSERT INTO graph_meta(key, value) VALUES
             ('embedder_fingerprint', '{\"model_id\":\"old\",\"dim\":768}')",
            [],
        )
        .unwrap();
        let payload = index_status(&conn).unwrap();
        assert_eq!(payload["embedder"]["recorded"]["model_id"], json!("old"));
        assert_eq!(payload["embedder"]["recorded"]["dim"], json!(768));
        assert_eq!(payload["embedder"]["stale_embedder"], json!(true));
        assert_eq!(payload["scale"]["full_rebuild_recommended"], json!(true));
        conn.execute(
            "UPDATE graph_meta SET value='{\"dim\":768}' WHERE key='embedder_fingerprint'",
            [],
        )
        .unwrap();
        assert_eq!(
            index_status(&conn).unwrap()["embedder"]["recorded"],
            Value::Null
        );
    }

    #[test]
    fn the_latency_budget_reads_the_newest_completed_rebuild() {
        let (_dir, conn) = store();
        conn.execute_batch(
            "INSERT INTO vector_index_operations VALUES
               (1,'rebuild','completed','2026-01-01T00:00:00','2026-01-01T00:00:00',
                '2026-01-01T00:00:05',85,80,5,NULL,'{\"duration_ms\": 2500}'),
               (2,'rebuild','failed','2026-01-02T00:00:00',NULL,NULL,0,0,0,'boom','{}');",
        )
        .unwrap();
        let payload = index_status(&conn).unwrap();
        let budget = &payload["scale"]["latency_budget"];
        assert_eq!(budget["last_rebuild_duration_ms"], json!(2500.0));
        assert_eq!(budget["last_items_per_second"], json!(34.0));
        assert_eq!(budget["within_target"], json!(true));
        let operations = payload["operations"].as_array().unwrap();
        assert_eq!(operations.len(), 2);
        assert_eq!(operations[0]["status"], json!("failed"));
        assert_eq!(operations[1]["metadata"]["duration_ms"], json!(2500));
    }

    #[test]
    fn the_vector_index_selection_reports_hnsw_default_and_fallbacks_honestly() {
        std::env::remove_var(VECTOR_INDEX_ENV);
        let default = vector_index_selection();
        assert_eq!(default["requested"], json!("hnsw"));
        assert_eq!(default["name"], json!("hnsw"));
        assert_eq!(default["honored"], json!(true));
        assert_eq!(default["detail"], Value::Null);

        std::env::set_var(VECTOR_INDEX_ENV, "brute");
        let brute = vector_index_selection();
        assert_eq!(brute["name"], json!("brute"));
        assert_eq!(brute["honored"], json!(true));
        assert_eq!(brute["detail"], Value::Null);

        std::env::set_var(VECTOR_INDEX_ENV, "nope");
        let unknown = vector_index_selection();
        assert!(unknown["detail"].as_str().unwrap().contains("unknown"));
        std::env::remove_var(VECTOR_INDEX_ENV);
    }

    #[test]
    fn the_vector_text_is_title_summary_and_the_eight_metadata_fields() {
        let mut metadata = serde_json::Map::new();
        metadata.insert("filename".into(), json!("a.md"));
        metadata.insert("ignored".into(), json!("no"));
        metadata.insert("role".into(), json!("user"));
        metadata.insert("ext".into(), json!(""));
        assert_eq!(
            vector_text_for_node("Title", "Summary  here", &metadata),
            "Title Summary here a.md user"
        );
        assert_eq!(vector_text_for_node("", "", &serde_json::Map::new()), "");
    }

    #[test]
    fn python_scalars_render_the_way_python_renders_them() {
        assert_eq!(py_str(&json!(true)), "True");
        assert_eq!(py_str(&json!(false)), "False");
        assert_eq!(py_str(&Value::Null), "None");
        assert_eq!(py_str(&json!(3)), "3");
        assert!(truthy(&json!("x")) && !truthy(&json!("")));
        assert!(!truthy(&json!(0)) && truthy(&json!(1)));
        assert!(!truthy(&Value::Null));
        assert!(truthy(&json!([1])) && !truthy(&json!([])));
        assert!(truthy(&json!({"a": 1})) && !truthy(&json!({})));
    }

    #[test]
    fn the_digest_is_the_one_the_write_path_stores() {
        assert_eq!(
            sha256_text("abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }

    #[test]
    fn semantic_contract_auto_enables_worker_embed_unless_explicitly_disabled() {
        let (dir, conn) = store();
        let db = dir.path().join("knowledge_graph.sqlite");
        assert!(!should_use_worker_embed_with_env(false, None, &db));

        let semantic = lattice_core::embedding_contract::EmbeddingIndexContract::from_semantic(
            "huggingface",
            "huggingface:google/embeddinggemma-2:768",
            768,
        );
        conn.execute(
            "INSERT OR REPLACE INTO graph_meta(key, value) VALUES ('embedding_index_contract', ?1)",
            [serde_json::to_string(&semantic).unwrap()],
        )
        .unwrap();
        assert!(should_use_worker_embed_with_env(false, None, &db));
        assert!(!should_use_worker_embed_with_env(false, Some("false"), &db));
    }
}
