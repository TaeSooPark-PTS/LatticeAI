pub mod params;
pub mod plan;

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use axum::extract::{Request, State};
use axum::http::{Method, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, MethodRouter};
use axum::{Json, Router};
use lattice_core::{parse_iso, CoreError};

use params::{ParamError, RequestParams, MAX_BODY_BYTES};
use plan::{Endpoint, Plan};

#[derive(Debug, Clone)]
pub struct RetrievalState {
    db: PathBuf,
}

impl RetrievalState {
    pub fn db(&self) -> &Path {
        &self.db
    }
}

pub fn router(db: impl Into<PathBuf>) -> Router {
    let state = Arc::new(RetrievalState { db: db.into() });
    let both = |endpoint: Endpoint| -> MethodRouter<Arc<RetrievalState>> {
        get(
            move |state: State<Arc<RetrievalState>>, request: Request| async move {
                handle(state, request, endpoint).await
            },
        )
        .post(
            move |state: State<Arc<RetrievalState>>, request: Request| async move {
                handle(state, request, endpoint).await
            },
        )
    };
    Router::new()
        .route(
            Endpoint::ServiceHybrid.path(),
            both(Endpoint::ServiceHybrid),
        )
        .route(Endpoint::GraphSearch.path(), both(Endpoint::GraphSearch))
        .route(
            Endpoint::GraphRelationships.path(),
            both(Endpoint::GraphRelationships),
        )
        .route(
            Endpoint::GraphTraverse.path(),
            both(Endpoint::GraphTraverse),
        )
        .route(Endpoint::History.path(), both(Endpoint::History))
        .route(
            Endpoint::Conversations.path(),
            both(Endpoint::Conversations),
        )
        .route(
            "/rust/history/conversations/{*conversation_id}",
            both(Endpoint::ConversationMessages),
        )
        .route(
            Endpoint::HistorySearch.path(),
            both(Endpoint::HistorySearch),
        )
        .route(
            Endpoint::ContextAssemble.path(),
            both(Endpoint::ContextAssemble),
        )
        .route(
            Endpoint::ContextDocument.path(),
            both(Endpoint::ContextDocument),
        )
        .with_state(state)
}

fn path_conversation_id(uri_path: &str) -> Option<String> {
    let prefix = "/rust/history/conversations/";
    uri_path
        .strip_prefix(prefix)
        .map(|rest| rest.trim_start_matches('/').to_string())
        .filter(|rest| !rest.is_empty())
}

async fn handle(
    State(state): State<Arc<RetrievalState>>,
    request: Request,
    endpoint: Endpoint,
) -> Response {
    let (parts, body) = request.into_parts();
    let mut params = match RequestParams::from_uri(&parts.uri) {
        Ok(params) => params,
        Err(err) => return err.into_response(),
    };
    if parts.method == Method::POST {
        let bytes = match axum::body::to_bytes(body, MAX_BODY_BYTES).await {
            Ok(bytes) => bytes,
            Err(err) => {
                return ParamError::new("body", format!("could not read the request body: {err}"))
                    .into_response()
            }
        };
        if let Err(err) = params.merge_json(&bytes) {
            return err.into_response();
        }
    }
    if endpoint == Endpoint::ConversationMessages {
        if let Some(conversation_id) = path_conversation_id(parts.uri.path()) {
            params.set(
                "conversation_id",
                serde_json::Value::String(conversation_id),
            );
        }
    }
    let plan = match Plan::build(endpoint, &params) {
        Ok(plan) => plan,
        Err(err) => return err.into_response(),
    };

    let db = state.db.clone();
    if !db.is_file() {
        return brain_not_found(&db);
    }
    match tokio::task::spawn_blocking(move || plan.run(&db)).await {
        Ok(Ok(value)) => Json(value).into_response(),
        Ok(Err(err)) => engine_error(endpoint, err),
        Err(err) => retrieval_failed(endpoint, format!("the task did not finish: {err}")),
    }
}

pub fn brain_not_found(db: &Path) -> Response {
    (
        StatusCode::NOT_FOUND,
        Json(serde_json::json!({
            "error": "brain_not_found",
            "detail": "no knowledge graph exists yet at this path; \
                       ingest something first, or point LATTICEAI_DATA_DIR at an existing brain",
            "path": db.display().to_string(),
        })),
    )
        .into_response()
}

fn engine_error(endpoint: Endpoint, err: CoreError) -> Response {
    let CoreError::InvalidRequest(message) = &err else {
        return retrieval_failed(endpoint, err.to_string());
    };
    if message.starts_with("graph node not found") {
        return (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({
                "error": "graph_node_not_found",
                "detail": message,
            })),
        )
            .into_response();
    }
    ParamError::new("node_id", message.clone()).into_response()
}

fn retrieval_failed(endpoint: Endpoint, detail: String) -> Response {
    (
        StatusCode::INTERNAL_SERVER_ERROR,
        Json(serde_json::json!({
            "error": "retrieval_failed",
            "endpoint": endpoint.path(),
            "detail": detail,
        })),
    )
        .into_response()
}

pub fn naive_local_now() -> f64 {
    let since_epoch = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    let utc_secs = since_epoch.as_secs() as i64;
    let fraction = f64::from(since_epoch.subsec_nanos()) / 1e9;
    naive_local_secs(utc_secs).unwrap_or(utc_secs as f64) + fraction
}

fn naive_local_secs(utc_secs: i64) -> Option<f64> {
    let iso = lattice_core::graph_write::clock::local_iso_for(utc_secs)?;
    parse_iso(Some(&iso))
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::Body;
    use axum::http::Uri;
    use serde_json::Value;

    fn store() -> (tempfile::TempDir, PathBuf) {
        let dir = tempfile::tempdir().expect("tempdir");
        let path = dir.path().join("knowledge_graph.sqlite");
        let conn = rusqlite::Connection::open(&path).expect("open");
        conn.execute_batch(
            "CREATE TABLE nodes(id TEXT PRIMARY KEY, type TEXT, title TEXT, summary TEXT,
                                metadata_json TEXT, updated_at TEXT);
             CREATE TABLE edges(id TEXT PRIMARY KEY, from_node TEXT, to_node TEXT, type TEXT,
                                weight REAL, metadata_json TEXT, created_at TEXT);
             CREATE TABLE nodes_v2(id TEXT PRIMARY KEY, workspace_id TEXT);
             CREATE TABLE conversation_messages(id INTEGER PRIMARY KEY AUTOINCREMENT,
               conversation_id TEXT, role TEXT, content TEXT, user_email TEXT,
               user_nickname TEXT, source TEXT, timestamp TEXT,
               metadata_json TEXT NOT NULL DEFAULT '{}', workspace_id TEXT,
               organization_id TEXT);
             INSERT INTO nodes VALUES
               ('a','Decision','Alpha ranking','about ranking','{}','2026-01-02T00:00:00'),
               ('b','Concept','Beta ranking','also ranking','{}','2026-01-03T00:00:00');
             INSERT INTO nodes_v2 VALUES ('a','w1'),('b','w1');
             INSERT INTO edges VALUES ('e1','a','b','MENTIONS',0.9,'{}','2026-02-01T00:00:00');
             INSERT INTO conversation_messages
               (conversation_id, role, content, timestamp) VALUES
               ('c1','user','ranking question','2026-01-01T00:00:00'),
               (NULL,'user','legacy line','2026-01-02T00:00:00');",
        )
        .expect("schema");
        drop(conn);
        (dir, path)
    }

    fn call(db: &Path, method: Method, uri: &str, body: &str) -> (StatusCode, Value) {
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .expect("runtime");
        runtime.block_on(async {
            let uri: Uri = uri.parse().expect("uri");
            let endpoint = endpoint_for(uri.path()).expect("a mounted route");
            let request = Request::builder()
                .method(method)
                .uri(uri)
                .body(Body::from(body.to_string()))
                .expect("request");
            let state = State(Arc::new(RetrievalState {
                db: db.to_path_buf(),
            }));
            let response = handle(state, request, endpoint).await;
            let status = response.status();
            let bytes = axum::body::to_bytes(response.into_body(), usize::MAX)
                .await
                .expect("body");
            let value = serde_json::from_slice(&bytes).unwrap_or(Value::Null);
            (status, value)
        })
    }

    fn endpoint_for(path: &str) -> Option<Endpoint> {
        for endpoint in [
            Endpoint::ServiceHybrid,
            Endpoint::GraphSearch,
            Endpoint::GraphRelationships,
            Endpoint::GraphTraverse,
            Endpoint::HistorySearch,
            Endpoint::Conversations,
            Endpoint::History,
            Endpoint::ContextAssemble,
            Endpoint::ContextDocument,
        ] {
            if endpoint.path() == path {
                return Some(endpoint);
            }
        }
        path.starts_with("/rust/history/conversations/")
            .then_some(Endpoint::ConversationMessages)
    }

    #[test]
    fn the_router_mounts_every_endpoint_on_both_verbs() {
        let (_dir, db) = store();
        let router = router(&db);
        assert!(format!("{router:?}").contains("Router"));
        let state = RetrievalState { db: db.clone() };
        assert_eq!(state.db(), db.as_path());
        assert!(format!("{state:?}").contains("RetrievalState"));
        for uri in [
            "/rust/graph/search?q=ranking",
            "/rust/graph/relationships",
            "/rust/graph/traverse?node_id=a",
            "/rust/history",
            "/rust/history/conversations",
            "/rust/history/search?q=ranking",
            "/rust/context/document?q=ranking",
        ] {
            for method in [Method::GET, Method::POST] {
                let (status, _) = call(&db, method, uri, "");
                assert_eq!(status, StatusCode::OK, "{uri}");
            }
        }
    }

    #[test]
    fn the_document_context_route_answers_the_whole_contract() {
        let (_dir, db) = store();
        let (status, body) = call(
            &db,
            Method::POST,
            "/rust/context/document",
            r#"{"query": "ranking", "max_results": 2, "max_hops": 2, "budget": 500,
                "include_self_model": false, "now": "2026-01-04T00:00:00"}"#,
        );
        assert_eq!(status, StatusCode::OK);
        assert_eq!(body["query"], "ranking");
        assert_eq!(body["stats"]["method"], "hybrid");
        assert_eq!(body["stats"]["primary_matches"], 2);
        assert!(body["context_markdown"]
            .as_str()
            .unwrap()
            .contains("### \u{2705} 관련 결정사항/작업"));
        assert_eq!(body["context_quality"]["mode"], "hybrid");
        assert_eq!(body["trace"]["budget_approx_tokens"], 500);
        assert!(!body["sources"].as_array().unwrap().is_empty());
        let (_, with_profile) = call(
            &db,
            Method::GET,
            "/rust/context/document?q=ranking&now=2026-01-04T00:00:00",
            "",
        );
        assert_eq!(
            with_profile["trace"]["sections"][0]["source"], "knowledge",
            "no profile means no self_model section"
        );
        let (_, nothing) = call(&db, Method::GET, "/rust/context/document?q=", "");
        assert_eq!(
            nothing["stats"],
            serde_json::json!({"method": "none", "matches": 0})
        );
    }

    #[test]
    fn a_post_body_and_a_query_string_reach_the_same_engine() {
        let (_dir, db) = store();
        let (_, from_url) = call(&db, Method::GET, "/rust/graph/search?q=ranking&limit=1", "");
        let (_, from_body) = call(
            &db,
            Method::POST,
            "/rust/graph/search",
            r#"{"q": "ranking", "limit": 1}"#,
        );
        assert_eq!(from_url, from_body);
        assert_eq!(from_url["matches"].as_array().unwrap().len(), 1);
    }

    #[test]
    fn the_path_segment_names_the_conversation() {
        let (_dir, db) = store();
        let (status, body) = call(&db, Method::GET, "/rust/history/conversations/c1", "");
        assert_eq!(status, StatusCode::OK);
        assert_eq!(body["id"], "c1");
        assert_eq!(body["messages"].as_array().unwrap().len(), 1);
        let (_, legacy) = call(
            &db,
            Method::GET,
            "/rust/history/conversations/legacy-previous-history",
            "",
        );
        assert_eq!(legacy["messages"].as_array().unwrap().len(), 1);
        let (_, pinned) = call(
            &db,
            Method::POST,
            "/rust/history/conversations/c1",
            r#"{"conversation_id": "nope"}"#,
        );
        assert_eq!(pinned["id"], "c1");
        assert_eq!(path_conversation_id("/rust/history/conversations/"), None);
        assert_eq!(path_conversation_id("/rust/history"), None);
    }

    #[test]
    fn bad_input_is_a_422_that_names_the_field() {
        let (_dir, db) = store();
        let (status, body) = call(&db, Method::GET, "/rust/graph/search", "");
        assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
        assert_eq!(body["error"], "invalid_request");
        assert_eq!(body["field"], "query");
        let (status, body) = call(&db, Method::POST, "/rust/graph/search", "{nope");
        assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
        assert_eq!(body["field"], "body");
        let (status, body) = call(&db, Method::GET, "/rust/graph/traverse?node_id=%20", "");
        assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
        assert_eq!(body["field"], "node_id");
        assert_eq!(body["detail"], "node_id required");
    }

    #[test]
    fn a_missing_brain_is_a_404_that_names_the_path() {
        let dir = tempfile::tempdir().expect("tempdir");
        let (status, body) = call(
            &dir.path().join("nope.sqlite"),
            Method::GET,
            "/rust/history",
            "",
        );
        assert_eq!(status, StatusCode::NOT_FOUND);
        assert_eq!(body["error"], "brain_not_found");
        assert!(body["path"].as_str().unwrap().ends_with("nope.sqlite"));
    }

    #[test]
    fn an_engine_failure_is_reported_by_its_kind() {
        let response = engine_error(
            Endpoint::GraphTraverse,
            CoreError::InvalidRequest("graph node not found: x".into()),
        );
        assert_eq!(response.status(), StatusCode::NOT_FOUND);
        let response = engine_error(
            Endpoint::GraphTraverse,
            CoreError::InvalidRequest("node_id required".into()),
        );
        assert_eq!(response.status(), StatusCode::UNPROCESSABLE_ENTITY);
        let response = engine_error(
            Endpoint::History,
            CoreError::DimensionMismatch { left: 1, right: 2 },
        );
        assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
        let response = retrieval_failed(Endpoint::History, "boom".into());
        assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
    }

    #[test]
    fn a_store_without_the_history_table_answers_empty() {
        let dir = tempfile::tempdir().expect("tempdir");
        let path = dir.path().join("bare.sqlite");
        rusqlite::Connection::open(&path)
            .expect("open")
            .execute_batch("CREATE TABLE t(x)")
            .expect("schema");
        let (status, body) = call(&path, Method::GET, "/rust/history", "");
        assert_eq!(status, StatusCode::OK);
        assert_eq!(body, serde_json::json!([]));
    }

    #[test]
    fn the_clock_lands_within_one_local_offset_of_utc() {
        let now = naive_local_now();
        let utc = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("epoch")
            .as_secs_f64();
        assert!(
            (now - utc).abs() <= 14.0 * 3600.0 + 5.0,
            "naive local now {now} is nowhere near utc {utc}"
        );
        assert!(now > 1_700_000_000.0);
        let local = naive_local_secs(1_785_585_600).expect("unix hosts convert");
        assert_eq!((local - 1_785_585_600.0) % 60.0, 0.0);
        assert!(naive_local_secs(0).is_some());
    }
}
