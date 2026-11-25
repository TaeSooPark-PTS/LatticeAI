use std::sync::Arc;

use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use lattice_core::CoreError;

use rusqlite::Connection;

use crate::search_api::{engine_error, RetrievalApiState};
#[cfg(test)]
use crate::service::Scope;

pub const MOUNTED: &[(&str, &str)] = &[
    ("POST", "/knowledge-graph/ingest"),
    ("POST", "/knowledge-graph/curate"),
    ("POST", "/knowledge-graph/curate/noise"),
    ("GET", "/knowledge-graph/promotions"),
    ("POST", "/knowledge-graph/promotions/apply"),
    ("POST", "/knowledge-graph/promotions/reject"),
    ("GET", "/knowledge-graph/provenance/coverage"),
    ("GET", "/knowledge-graph/stats"),
    ("GET", "/knowledge-graph/pipeline/status"),
    ("GET", "/knowledge-graph/schema"),
    ("GET", "/knowledge-graph/graph"),
    ("GET", "/knowledge-graph/documents"),
    ("GET", "/knowledge-graph/search"),
    ("GET", "/knowledge-graph/context"),
    ("GET", "/knowledge-graph/neighbors/*node_id"),
];

pub const GRAPH_SCHEMA_VERSION: i64 = 1;
pub const KG_SCHEMA_V2_VERSION: i64 = 2;
pub const DEFAULT_EMBED_DIM: i64 = 1024;

pub const GRAPH_VISIBLE_TYPES: &[&str] = &[
    "Computer",
    "Drive",
    "Folder",
    "File",
    "Chat",
    "Document",
    "CodeFile",
    "Spreadsheet",
    "SlideDeck",
    "Image",
    "ImageText",
    "Audio",
    "Concept",
    "Person",
    "Error",
    "Code",
    "Feature",
    "Task",
    "Decision",
    "Source",
    "Repository",
    "Meeting",
    "Organization",
    "Workflow",
    "Agent",
];

pub fn router(state: Arc<RetrievalApiState>) -> Router {
    Router::new()
        .route("/knowledge-graph/ingest", post(ingest))
        .route("/knowledge-graph/curate", post(curate))
        .route("/knowledge-graph/curate/noise", post(curate_noise))
        .route("/knowledge-graph/promotions", get(promotions))
        .route("/knowledge-graph/promotions/apply", post(promotions_apply))
        .route(
            "/knowledge-graph/promotions/reject",
            post(promotions_reject),
        )
        .route(
            "/knowledge-graph/provenance/coverage",
            get(provenance_coverage_route),
        )
        .route("/knowledge-graph/stats", get(stats_route))
        .route("/knowledge-graph/pipeline/status", get(pipeline_status))
        .route("/knowledge-graph/schema", get(schema_route))
        .route("/knowledge-graph/graph", get(graph_route))
        .route("/knowledge-graph/documents", get(documents_route))
        .route("/knowledge-graph/search", get(search_route))
        .route("/knowledge-graph/context", get(context_route))
        .route(
            "/knowledge-graph/neighbors/{*node_id}",
            get(neighbors_route),
        )
        .with_state(state)
}

pub(crate) async fn read<T, F>(state: &RetrievalApiState, work: F) -> Result<T, Response>
where
    T: Send + 'static,
    F: FnOnce(&Connection) -> Result<T, CoreError> + Send + 'static,
{
    let store = state.require_graph()?;
    store.read(work).await.map_err(engine_error)
}

pub(crate) mod handlers;
pub(crate) mod ingest;
pub(crate) mod reads;
pub(crate) mod view;
pub(crate) mod writes;
use handlers::{
    context_route, documents_route, graph_route, neighbors_route, pipeline_status, promotions,
    provenance_coverage_route, schema_route, search_route, stats_route,
};
use ingest::ingest;
use writes::{curate, curate_noise, promotions_apply, promotions_reject};

pub use reads::{
    filter_scoped, list_documents, neighbors, pending_promotions, provenance_coverage, scope_sql,
    scoped_documents, stats,
};
pub use view::{
    civil_from_days, context_for_query, format_context, get_node, graph_view,
    naive_local_iso_seconds, pipeline_payload, stage_view,
};

pub use crate::search_api::detail as json_detail;

#[cfg(test)]
mod tests {
    use super::*;
    use lattice_core::pytext::parse_iso;
    use serde_json::{json, Value};

    fn store() -> (tempfile::TempDir, Connection) {
        let dir = tempfile::tempdir().expect("tempdir");
        let conn = Connection::open(dir.path().join("knowledge_graph.sqlite")).expect("open");
        conn.execute_batch(
            "CREATE TABLE nodes(id TEXT PRIMARY KEY, type TEXT, title TEXT, summary TEXT,
                                metadata_json TEXT, created_at TEXT, updated_at TEXT);
             CREATE TABLE edges(id TEXT PRIMARY KEY, from_node TEXT, to_node TEXT, type TEXT,
                                weight REAL, metadata_json TEXT, created_at TEXT);
             CREATE TABLE chunks(id TEXT PRIMARY KEY, source_node TEXT, text TEXT,
                                 metadata_json TEXT, created_at TEXT);
             CREATE TABLE nodes_v2(id TEXT PRIMARY KEY, workspace_id TEXT, type TEXT);
             CREATE TABLE edges_v2(id TEXT PRIMARY KEY, source TEXT, target TEXT, type TEXT);
             CREATE TABLE knowledge_sources(id TEXT PRIMARY KEY, root_path TEXT);
             CREATE TABLE local_file_index(id TEXT PRIMARY KEY, source_id TEXT, status TEXT);
             CREATE TABLE ingestion_provenance(id TEXT PRIMARY KEY, node_id TEXT,
                                               source_type TEXT);
             CREATE TABLE graph_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
             INSERT INTO nodes VALUES
               ('doc-a','Document','handbook.md','a handbook',
                '{\"filename\":\"handbook.md\",\"ext\":\".md\",\"extracted\":{\"chars\":12}}',
                '2026-01-01T00:00:00','2026-01-03T00:00:00'),
               ('doc-b','Document','notes.md','notes','{}',
                '2026-01-01T00:00:00','2026-01-02T00:00:00'),
               ('c-1','Concept','Lattice','','{}','2026-01-01T00:00:00','2026-01-04T00:00:00');
             INSERT INTO nodes_v2 VALUES ('doc-a','w1','Document'),('doc-b',NULL,'Document'),
                                         ('c-1','w1','Concept');
             INSERT INTO edges VALUES
               ('e1','doc-a','c-1','MENTIONS',0.9,'{}','2026-02-01T00:00:00');
             INSERT INTO edges_v2 VALUES ('e1','doc-a','c-1','MENTIONS');
             INSERT INTO chunks VALUES ('k1','doc-a','text','{}','2026-01-01T00:00:00');
             INSERT INTO knowledge_sources VALUES ('source:1','/tmp/corpus');
             INSERT INTO local_file_index VALUES ('f1','source:1','indexed');
             INSERT INTO ingestion_provenance VALUES ('p1','doc-a','note');",
        )
        .expect("schema");
        (dir, conn)
    }

    fn unscoped() -> Scope {
        Scope::default()
    }

    fn scoped(workspaces: &[&str]) -> Scope {
        Scope {
            allowed_workspaces: Some(workspaces.iter().map(|w| (*w).to_string()).collect()),
            include_legacy_global: false,
        }
    }

    #[test]
    fn the_route_table_includes_native_ingest() {
        assert_eq!(MOUNTED.len(), 15);
        assert!(MOUNTED.contains(&("POST", "/knowledge-graph/ingest")));
        assert!(MOUNTED
            .iter()
            .any(|(_, path)| *path == "/knowledge-graph/neighbors/*node_id"));
    }

    #[test]
    fn documents_report_their_index_state() {
        let (_dir, conn) = store();
        let payload = list_documents(&conn, 200).unwrap();
        let documents = payload["documents"].as_array().unwrap();
        assert_eq!(documents.len(), 2);
        assert_eq!(documents[0]["id"], json!("doc-a"));
        assert_eq!(documents[0]["filename"], json!("handbook.md"));
        assert_eq!(documents[0]["chars"], json!(12));
        assert_eq!(documents[0]["chunks"], json!(1));
        assert_eq!(documents[0]["indexed"], json!(true));
        assert_eq!(documents[0]["ingest_state"], json!("indexed"));
        assert_eq!(documents[1]["ingest_state"], json!("ingested"));
        assert_eq!(payload["total"], json!(2));
        let rendered = serde_json::to_string(&documents[0]).unwrap();
        assert!(rendered.starts_with(r#"{"id":"doc-a","filename":"handbook.md","ext":".md""#));
    }

    #[test]
    fn a_zero_limit_is_the_default_not_one_document() {
        let (_dir, conn) = store();
        assert_eq!(
            list_documents(&conn, 0).unwrap()["documents"]
                .as_array()
                .unwrap()
                .len(),
            2
        );
        assert_eq!(
            list_documents(&conn, 1).unwrap()["documents"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
    }

    #[test]
    fn scoped_documents_drop_the_legacy_global_row() {
        let (_dir, conn) = store();
        let payload = list_documents(&conn, 200).unwrap();
        let scoped = scoped_documents(&conn, payload, &scoped(&["w1"])).unwrap();
        assert_eq!(scoped["total"], json!(1));
        assert_eq!(scoped["documents"][0]["id"], json!("doc-a"));
    }

    #[test]
    fn stats_counts_the_whole_store_when_nobody_is_scoped() {
        let (_dir, conn) = store();
        let payload = stats(&conn, &unscoped()).unwrap();
        assert_eq!(payload["schema_version"], json!(1));
        assert_eq!(payload["v2_schema_available"], json!(true));
        assert_eq!(payload["nodes"]["Document"], json!(2));
        assert_eq!(payload["edges"]["MENTIONS"], json!(1));
        assert_eq!(payload["local_sources"], json!(1));
        assert_eq!(payload["local_file_status"]["indexed"], json!(1));
        assert_eq!(payload["v2"]["schema_version"], json!(2));
        assert_eq!(payload["v2"]["nodes"], json!(3));
    }

    #[test]
    fn scoped_stats_report_no_machine_local_bookkeeping() {
        let (_dir, conn) = store();
        let payload = stats(&conn, &scoped(&["w1"])).unwrap();
        assert_eq!(payload["nodes"]["Document"], json!(1));
        assert_eq!(payload["local_sources"], json!(0));
        assert_eq!(payload["local_file_status"], json!({}));
        assert_eq!(payload["v2"]["nodes"], json!(2));
        let nobody = stats(&conn, &scoped(&[])).unwrap();
        assert_eq!(nobody["nodes"], json!({}));
    }

    #[test]
    fn provenance_coverage_reports_the_uncovered_types() {
        let (_dir, conn) = store();
        let payload = provenance_coverage(&conn).unwrap();
        assert_eq!(payload["total_nodes"], json!(3));
        assert_eq!(payload["nodes_with_provenance"], json!(1));
        assert_eq!(payload["coverage_ratio"], json!(0.3333));
        assert_eq!(payload["uncovered_by_type"]["Document"], json!(1));
        assert_eq!(payload["provenance_by_source_type"]["note"], json!(1));
    }

    #[test]
    fn an_empty_store_reports_a_null_coverage_ratio() {
        let dir = tempfile::tempdir().unwrap();
        let conn = Connection::open(dir.path().join("g.sqlite")).unwrap();
        conn.execute_batch(
            "CREATE TABLE nodes(id TEXT PRIMARY KEY, type TEXT, title TEXT, summary TEXT,
                                metadata_json TEXT, created_at TEXT, updated_at TEXT);
             CREATE TABLE edges(id TEXT PRIMARY KEY, from_node TEXT, to_node TEXT, type TEXT,
                                weight REAL, metadata_json TEXT, created_at TEXT);
             CREATE TABLE ingestion_provenance(id TEXT PRIMARY KEY, node_id TEXT,
                                               source_type TEXT);",
        )
        .unwrap();
        assert_eq!(
            provenance_coverage(&conn).unwrap()["coverage_ratio"],
            Value::Null
        );
    }

    #[test]
    fn neighbors_answers_one_hop_with_its_own_key_set() {
        let (_dir, conn) = store();
        let payload = neighbors(&conn, "doc-a").unwrap();
        assert_eq!(payload["node_id"], json!("doc-a"));
        let nodes = payload["neighbors"].as_array().unwrap();
        assert_eq!(nodes.len(), 1);
        assert_eq!(nodes[0]["id"], json!("c-1"));
        assert!(nodes[0].get("updated_at").is_none());
        let edges = payload["edges"].as_array().unwrap();
        assert_eq!(edges[0]["from"], json!("doc-a"));
        assert!(edges[0].get("id").is_none());
        assert_eq!(payload["edges"].as_array().unwrap().len(), 1);
    }

    #[test]
    fn the_graph_view_carries_importance_metrics() {
        let (_dir, conn) = store();
        let payload = graph_view(&conn, 300, &unscoped(), 1_800_000_000.0).unwrap();
        let nodes = payload["nodes"].as_array().unwrap();
        assert_eq!(nodes.len(), 3);
        for node in nodes {
            let metrics = &node["metadata"]["graph_metrics"];
            assert!(metrics["degree"].is_number());
            assert!(metrics["recency_score"].is_number());
            assert!(metrics["importance_raw"].is_number());
            assert!(metrics["importance_norm"].is_number());
            assert!(node["importance"].is_number());
            assert!(node["importance_norm"].is_number());
        }
        let by_id: std::collections::HashMap<&str, &Value> = nodes
            .iter()
            .map(|node| (node["id"].as_str().unwrap(), node))
            .collect();
        assert_eq!(
            by_id["doc-a"]["metadata"]["graph_metrics"]["degree"],
            json!(1)
        );
        assert_eq!(
            by_id["doc-b"]["metadata"]["graph_metrics"]["degree"],
            json!(0)
        );
        assert_eq!(payload["edges"].as_array().unwrap().len(), 1);
    }

    #[test]
    fn the_graph_view_drops_rows_a_scope_cannot_read() {
        let (_dir, conn) = store();
        let payload = graph_view(&conn, 300, &scoped(&["w1"]), 1_800_000_000.0).unwrap();
        let ids: Vec<&str> = payload["nodes"]
            .as_array()
            .unwrap()
            .iter()
            .map(|node| node["id"].as_str().unwrap())
            .collect();
        assert_eq!(ids, vec!["c-1", "doc-a"]);
        assert_eq!(payload["edges"].as_array().unwrap().len(), 1);
    }

    #[test]
    fn get_node_reports_its_degree_and_hides_what_a_scope_may_not_read() {
        let (_dir, conn) = store();
        let node = get_node(&conn, "doc-a", &unscoped()).unwrap();
        assert_eq!(node["id"], json!("doc-a"));
        assert_eq!(node["degree"], json!(1));
        let missing = get_node(&conn, "nope", &unscoped()).unwrap_err();
        assert!(matches!(missing, CoreError::InvalidRequest(ref message)
            if message == "graph node not found: nope"));
        let hidden = get_node(&conn, "doc-b", &scoped(&["w1"])).unwrap_err();
        assert!(matches!(hidden, CoreError::InvalidRequest(ref message)
            if message == "graph node not found: doc-b"));
        assert!(matches!(
            get_node(&conn, "  ", &unscoped()).unwrap_err(),
            CoreError::InvalidRequest(ref message) if message == "node_id required"
        ));
    }

    #[test]
    fn pending_promotions_tolerates_every_broken_shape() {
        let (_dir, conn) = store();
        assert!(pending_promotions(&conn).unwrap().is_empty());
        for (value, expected) in [
            (r#"[{"id":"topic:a"},{"no":"id"},{"id":""}]"#, 1),
            ("not json", 0),
            (r#"{"id":"a"}"#, 0),
            ("[]", 0),
        ] {
            conn.execute(
                "INSERT OR REPLACE INTO graph_meta(key, value) VALUES ('pending_promotions', ?)",
                [value],
            )
            .unwrap();
            assert_eq!(
                pending_promotions(&conn).unwrap().len(),
                expected,
                "{value}"
            );
        }
    }

    #[test]
    fn the_context_line_is_the_one_python_writes() {
        let matches = vec![json!({
            "id": "doc-a",
            "type": "Document",
            "title": "handbook.md",
            "summary": "  a   handbook\nwith lines ",
            "metadata": {"relative_path": "notes/handbook.md"},
        })];
        assert_eq!(
            format_context(&matches, 6),
            "- [Document] handbook.md | source=notes/handbook.md | a handbook with lines"
        );
        let bare = vec![json!({"id": "x", "type": null, "title": null, "summary": null})];
        assert_eq!(format_context(&bare, 6), "- [None] None | source=x | ");
        assert_eq!(format_context(&matches, 0), "");
    }

    #[test]
    fn the_pipeline_ribbon_derives_a_coherent_stage() {
        assert_eq!(stage_view(0, 0)["status"], json!("waiting"));
        assert_eq!(stage_view(3, 0)["status"], json!("done"));
        assert_eq!(stage_view(3, 1)["status"], json!("working"));
        assert_eq!(stage_view(-3, -1)["count"], json!(0));
        let (_dir, conn) = store();
        let payload = pipeline_payload(&conn, &unscoped(), 1_800_000_000.0).unwrap();
        assert_eq!(payload["received"], json!(2));
        assert_eq!(payload["extracted"], json!(1));
        assert_eq!(payload["connected"], json!(1));
        assert_eq!(payload["stages"]["extracted"]["pending"], json!(1));
        assert_eq!(payload["stages"]["extracted"]["status"], json!("working"));
        assert!(payload["updated_at"].as_str().unwrap().contains('T'));
    }

    #[test]
    fn a_naive_local_stamp_is_seconds_precision_with_no_offset() {
        let stamp = naive_local_iso_seconds();
        assert_eq!(stamp.len(), 19, "{stamp}");
        assert!(parse_iso(Some(&stamp)).is_some(), "{stamp}");
        assert!(!stamp.contains('+'), "{stamp}");
    }

    #[test]
    fn the_civil_calendar_reduction_is_the_standard_one() {
        assert_eq!(civil_from_days(0), (1970, 1, 1));
        assert_eq!(civil_from_days(19_723), (2024, 1, 1));
        assert_eq!(civil_from_days(-1), (1969, 12, 31));
    }

    #[test]
    fn the_scope_predicate_distinguishes_none_from_empty() {
        assert!(scope_sql(&unscoped()).is_none());
        let (predicate, params) = scope_sql(&scoped(&["w1", "w2"])).unwrap();
        assert_eq!(predicate, "workspace_id IN (?,?)");
        assert_eq!(params, vec!["w1".to_string(), "w2".to_string()]);
        let (nothing, params) = scope_sql(&scoped(&[])).unwrap();
        assert_eq!(nothing, "0");
        assert!(params.is_empty());
        let legacy = Scope {
            allowed_workspaces: Some(Default::default()),
            include_legacy_global: true,
        };
        assert_eq!(scope_sql(&legacy).unwrap().0, "workspace_id IS NULL");
        let personal = scoped(&[lattice_core::DEFAULT_WORKSPACE_ID]);
        let (predicate, params) = scope_sql(&personal).unwrap();
        assert!(
            predicate.contains("workspace_id IS NULL"),
            "personal matches unstamped rows: {predicate}"
        );
        assert_eq!(params, vec![lattice_core::DEFAULT_WORKSPACE_ID.to_string()]);
        let named = scoped(&["acme"]);
        let (predicate, _) = scope_sql(&named).unwrap();
        assert_eq!(predicate, "workspace_id IN (?)");
    }

    #[test]
    fn a_graph_without_a_write_engine_is_a_503_about_the_host() {
        let refusal = crate::search_api::detail(503, writes::WRITE_ENGINE_UNCONFIGURED);
        assert_eq!(refusal.status(), 503);
    }

    #[test]
    fn the_json_detail_alias_is_the_shared_renderer() {
        let response = json_detail(404, "gone");
        assert_eq!(response.status(), 404);
    }
}
