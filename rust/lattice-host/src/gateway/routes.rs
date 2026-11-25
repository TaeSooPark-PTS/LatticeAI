use std::sync::Arc;

use axum::extract::{OriginalUri, Request, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;

use super::{proxy, GatewayState};
use crate::VERSION;

pub const NATIVE_FAMILIES: [&str; 6] = [
    "/rust/search",
    "/rust/graph",
    "/rust/history",
    "/rust/context",
    "/rust/ingest",
    "/rust/agent",
];

pub async fn host_health(State(state): State<Arc<GatewayState>>) -> Response {
    let worker_healthy = state.probe_worker_health().await;
    let snapshot = state.status();
    let overall = if worker_healthy { "ok" } else { "degraded" };
    Json(serde_json::json!({
        "host": "ok",
        "status": overall,
        "version": VERSION,
        "worker_healthy": worker_healthy,
        "worker_origin": state.worker_origin(),
        "worker": snapshot,
    }))
    .into_response()
}

pub async fn host_status(State(state): State<Arc<GatewayState>>) -> Response {
    Json(serde_json::json!({
        "gateway": {
            "version": VERSION,
            "worker_origin": state.worker_origin(),
        },
        "worker": state.status(),
    }))
    .into_response()
}

pub async fn host_diagnostics(State(state): State<Arc<GatewayState>>) -> Response {
    let worker_healthy = state.probe_worker_health().await;
    let status = state.status();
    let redacted_error = status
        .last_error
        .as_deref()
        .map(lattice_core::redact_pii_and_secrets);
    let db_path = state.db_path().to_path_buf();
    let db_size_bytes = std::fs::metadata(&db_path).map(|m| m.len()).unwrap_or(0);
    let integrity_summary = tokio::task::spawn_blocking(move || {
        if !db_path.exists() {
            return serde_json::json!({"present": false});
        }
        match rusqlite::Connection::open_with_flags(
            &db_path,
            rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY,
        ) {
            Ok(conn) => match lattice_core::integrity::check_graph_integrity(&conn) {
                Ok(rep) => serde_json::json!({
                    "present": true,
                    "healthy": rep.healthy,
                    "sqlite_integrity_ok": rep.sqlite_integrity_ok,
                    "total_issues": rep.total_issues,
                    "critical_issues": rep.critical_issues,
                }),
                Err(err) => serde_json::json!({
                    "present": true,
                    "error": lattice_core::redact_pii_and_secrets(&err.to_string()),
                }),
            },
            Err(err) => serde_json::json!({
                "present": true,
                "error": lattice_core::redact_pii_and_secrets(&err.to_string()),
            }),
        }
    })
    .await
    .unwrap_or_else(|_| serde_json::json!({"present": false}));

    Json(serde_json::json!({
        "schema_version": "lattice.host.diagnostics.v1",
        "redacted": true,
        "version": VERSION,
        "worker_healthy": worker_healthy,
        "worker_running": status.running,
        "worker_restarts": status.restarts,
        "worker_last_error": redacted_error,
        "db_size_bytes": db_size_bytes,
        "graph_integrity": integrity_summary,
    }))
    .into_response()
}

pub async fn host_not_found(OriginalUri(uri): OriginalUri) -> Response {
    (
        StatusCode::NOT_FOUND,
        Json(serde_json::json!({
            "error": "unknown_host_route",
            "detail": "the /host namespace belongs to lattice-host and is never proxied",
            "path": uri.path(),
        })),
    )
        .into_response()
}

pub async fn unknown_search(OriginalUri(uri): OriginalUri) -> Response {
    (
        StatusCode::NOT_FOUND,
        Json(serde_json::json!({
            "error": "unknown_search_route",
            "detail": "the /rust/search namespace is served natively by lattice-retrieval \
                       and is never proxied; four lanes exist",
            "path": uri.path(),
            "available": [
                "/rust/search/hybrid",
                "/rust/search/keyword",
                "/rust/search/vector",
                "/rust/search/service-hybrid",
            ],
        })),
    )
        .into_response()
}

pub async fn unknown_native(OriginalUri(uri): OriginalUri) -> Response {
    (
        StatusCode::NOT_FOUND,
        Json(serde_json::json!({
            "error": "unknown_native_route",
            "detail": "the /rust namespace is served natively by the lattice crates and is \
                       never proxied to the worker",
            "path": uri.path(),
            "namespaces": NATIVE_FAMILIES,
        })),
    )
        .into_response()
}

pub fn not_found() -> Response {
    (
        StatusCode::NOT_FOUND,
        Json(serde_json::json!({"detail": "Not Found"})),
    )
        .into_response()
}

pub async fn gateway_fallback(state: State<Arc<GatewayState>>, request: Request) -> Response {
    let path = request.uri().path().to_string();
    let uri = OriginalUri(request.uri().clone());
    if in_namespace(&path, "/host") {
        return host_not_found(uri).await;
    }
    if in_namespace(&path, "/rust/search") {
        return unknown_search(uri).await;
    }
    if in_namespace(&path, "/rust") {
        return unknown_native(uri).await;
    }
    if !state.allowlist().allows(request.method(), &path) {
        return not_found();
    }
    proxy::proxy_handler(state, request).await
}

pub fn in_namespace(path: &str, namespace: &str) -> bool {
    path == namespace || path.starts_with(&format!("{namespace}/"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use lattice_ingest::{CHUNK_PATH, PLAN_PATH};
    use lattice_retrieval::routes::plan::Endpoint;

    #[test]
    fn the_advertised_families_cover_every_mounted_path() {
        let mut declared: Vec<&str> = vec![PLAN_PATH, CHUNK_PATH];
        for endpoint in [
            Endpoint::ServiceHybrid,
            Endpoint::GraphSearch,
            Endpoint::GraphRelationships,
            Endpoint::GraphTraverse,
            Endpoint::History,
            Endpoint::Conversations,
            Endpoint::ConversationMessages,
            Endpoint::HistorySearch,
            Endpoint::ContextAssemble,
        ] {
            declared.push(endpoint.path());
        }
        declared.extend([
            "/rust/agent/preflight",
            "/rust/agent/exec",
            "/rust/agent/contract",
        ]);
        declared.extend([
            "/rust/search/hybrid",
            "/rust/search/keyword",
            "/rust/search/vector",
        ]);
        for path in declared {
            assert!(
                NATIVE_FAMILIES
                    .iter()
                    .any(|family| in_namespace(path, family)),
                "{path} is mounted but no advertised family covers it"
            );
        }
    }

    #[test]
    fn a_namespace_matches_itself_and_its_children_only() {
        assert!(in_namespace("/host", "/host"));
        assert!(in_namespace("/host/", "/host"));
        assert!(in_namespace("/host/jobs", "/host"));
        assert!(!in_namespace("/hostile", "/host"));
        assert!(!in_namespace("/api/host", "/host"));
        assert!(in_namespace("/rust/search/hybrid", "/rust/search"));
        assert!(!in_namespace("/rust/searchlight", "/rust/search"));
    }

    #[tokio::test]
    async fn the_two_native_404s_name_the_path_and_what_exists() {
        let uri = |path: &str| OriginalUri(path.parse().expect("uri"));
        for (response, error) in [
            (
                unknown_search(uri("/rust/search/telepathy")).await,
                "unknown_search_route",
            ),
            (
                unknown_native(uri("/rust/telepathy")).await,
                "unknown_native_route",
            ),
            (
                host_not_found(uri("/host/telepathy")).await,
                "unknown_host_route",
            ),
        ] {
            assert_eq!(response.status(), StatusCode::NOT_FOUND);
            let bytes = axum::body::to_bytes(response.into_body(), usize::MAX)
                .await
                .expect("body");
            let body: serde_json::Value = serde_json::from_slice(&bytes).expect("json");
            assert_eq!(body["error"], error);
            assert!(body["path"]
                .as_str()
                .unwrap_or_default()
                .contains("telepathy"));
        }
    }
}
