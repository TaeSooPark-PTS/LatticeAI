//! Routes the host answers itself, plus the namespace guard.
//!
//! `/host/*` is the host's own namespace and is never proxied — an unknown
//! `/host/...` path is a 404 from here, not a request leaked to the worker.
//! `/rust/*` belongs to the native crates the same way; an unknown path under
//! it is a 404 that lists what does exist, never a silent fall-through to the
//! worker under a name that promised a native answer.

use std::sync::Arc;

use axum::extract::{OriginalUri, Request, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;

use super::{proxy, GatewayState};
use crate::VERSION;

/// The `/rust/*` families the gateway mounts, in mount order.
///
/// Named here rather than discovered from the router, because a 404 that lists
/// the routes is only useful if the list is the truth — so the test at the
/// bottom of this file checks every path the mounted crates *declare* against
/// it, and a family added to a crate without a line here fails there.
pub const NATIVE_FAMILIES: [&str; 6] = [
    "/rust/search",
    "/rust/graph",
    "/rust/history",
    "/rust/context",
    "/rust/ingest",
    "/rust/agent",
];

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

/// Anything under `/rust/search/` that is not one of the mounted lanes.
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

/// Anything under `/rust/` outside the search namespace that nothing mounted.
///
/// Separate from [`unknown_search`] because the answer is different: there the
/// caller asked for a lane by name, here they asked for a family that may not
/// be mounted on this host at all (the agent kernel is absent when no workspace
/// can be created).
pub async fn unknown_native(OriginalUri(uri): OriginalUri) -> Response {
    (
        StatusCode::NOT_FOUND,
        Json(serde_json::json!({
            "error": "unknown_native_route",
            "detail": "the /rust namespace is served natively by the lattice crates and is \
                       never proxied to the worker",
            "path": uri.path(),
            // Deliberately "namespaces", not "available": these are the
            // families this host owns, and one of them (`/rust/agent`) is
            // absent when no workspace could be prepared. Calling the list
            // "available" would promise a route that may not be mounted.
            "namespaces": NATIVE_FAMILIES,
        })),
    )
        .into_response()
}

/// FastAPI's own answer for a path no route matched.
///
/// The gateway is the product server now, so an unknown product path must read
/// exactly as it read when Python served it — clients (and the SPA's error
/// handling) parse `{"detail": …}`.
pub fn not_found() -> Response {
    (
        StatusCode::NOT_FOUND,
        Json(serde_json::json!({"detail": "Not Found"})),
    )
        .into_response()
}

/// Nothing matched: guard the host namespaces, then the worker allowlist.
///
/// This is the whole of the routing decision that cannot be a route, and since
/// v11.6.0 it is also the security boundary. Three rules, in order:
///
/// 1. **`/host` and `/rust` are ours.** Both namespaces mount real paths from
///    several crates, so reserving them with catch-all routes would collide
///    with those mounts; deciding here keeps the rule — *answered here or a
///    404, never proxied* — in one readable place.
/// 2. **The worker gets only its own surface.** [`allowlist`] is the committed
///    projection of `worker_route_keys()`. A path on it is forwarded; a path
///    off it never reaches the worker at all.
/// 3. **Everything else is 404.** Not "probably the worker's" — the gateway
///    mounts the product, so a path it does not serve and the worker does not
///    own is a path that does not exist.
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

/// Whether `path` is `namespace` itself or something under it.
///
/// `/hostile` is not in the `/host` namespace; `/host` and `/host/` and
/// `/host/anything` are.
pub fn in_namespace(path: &str, namespace: &str) -> bool {
    path == namespace || path.starts_with(&format!("{namespace}/"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use lattice_ingest::{CHUNK_PATH, PLAN_PATH};
    use lattice_retrieval::routes::plan::Endpoint;

    /// Every path the mounted crates declare must fall inside a family the 404
    /// names. A crate that grows a family the guard has never heard of would
    /// otherwise be advertised as "not here" by a route that is.
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
        // The agent kernel's three routes are declared as literals by
        // `lattice_agent::router`; naming them here keeps them in the sweep.
        declared.extend([
            "/rust/agent/preflight",
            "/rust/agent/exec",
            "/rust/agent/contract",
        ]);
        // …and so are the host's own P1 lanes.
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
