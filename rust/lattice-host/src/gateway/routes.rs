//! Routes the host answers itself.
//!
//! `/host/*` is the host's own namespace and is never proxied — an unknown
//! `/host/...` path is a 404 from here, not a request leaked to the worker.
//! `/rust/search/*` belongs to the native retrieval crate the same way (the
//! lanes themselves live in [`super::search`]); an unknown path under it is a
//! 404 that lists what does exist, never a silent fall-through to the worker
//! under a name that promised a native answer.

use std::sync::Arc;

use axum::extract::{OriginalUri, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;

use super::GatewayState;
use crate::VERSION;

/// `GET /host/health` — host liveness plus a live worker health probe.
///
/// Always 200: the host answering at all *is* the host's health. The worker's
/// state is in the body (`status` is `ok` only when both are up).
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

/// `GET /host/status` — the supervisor snapshot, verbatim.
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

/// Anything else under `/host/` — 404, and explicitly not proxied.
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

/// Anything under `/rust/search/` that is not one of the three lanes.
pub async fn unknown_search(OriginalUri(uri): OriginalUri) -> Response {
    (
        StatusCode::NOT_FOUND,
        Json(serde_json::json!({
            "error": "unknown_search_route",
            "detail": "the /rust/search namespace is served natively by lattice-retrieval \
                       and is never proxied; three lanes exist",
            "path": uri.path(),
            "available": ["/rust/search/hybrid", "/rust/search/keyword", "/rust/search/vector"],
        })),
    )
        .into_response()
}
