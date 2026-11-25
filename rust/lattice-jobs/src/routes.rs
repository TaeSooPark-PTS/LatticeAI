use std::sync::Arc;

use axum::extract::State;
use axum::http::{header, HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};

use crate::scheduler::Scheduler;

fn is_loopback_host(raw: &str) -> bool {
    let trimmed = raw.trim().to_ascii_lowercase();
    if trimmed.is_empty() {
        return true;
    }
    if let Some(rest) = trimmed.strip_prefix('[') {
        let host = rest.split(']').next().unwrap_or("");
        return host == "::1";
    }
    let host = trimmed.split(':').next().unwrap_or("");
    matches!(host, "127.0.0.1" | "localhost")
}

fn guard_jobs_request(headers: &HeaderMap) -> Result<(), Response> {
    let has_bearer = headers
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .map(|v| {
            let trimmed = v.trim();
            trimmed.len() > 7 && trimmed[..7].eq_ignore_ascii_case("bearer ")
        })
        .unwrap_or(false);
    if has_bearer {
        return Ok(());
    }
    if let Some(host) = headers.get(header::HOST).and_then(|v| v.to_str().ok()) {
        if !is_loopback_host(host) {
            return Err((
                StatusCode::FORBIDDEN,
                Json(serde_json::json!({
                    "detail": "Jobs control routes require loopback access or bearer authentication.",
                    "error": "jobs_access_denied"
                })),
            )
                .into_response());
        }
    }
    Ok(())
}

pub fn router(state: Arc<Scheduler>) -> Router {
    Router::new()
        .route("/host/jobs", get(jobs_status))
        .route("/host/jobs/tick", post(jobs_tick))
        .with_state(state)
}

pub async fn jobs_status(State(state): State<Arc<Scheduler>>, headers: HeaderMap) -> Response {
    if let Err(refusal) = guard_jobs_request(&headers) {
        return refusal;
    }
    Json(state.snapshot().await).into_response()
}

pub async fn jobs_tick(State(state): State<Arc<Scheduler>>, headers: HeaderMap) -> Response {
    if let Err(refusal) = guard_jobs_request(&headers) {
        return refusal;
    }
    state.tick().await;
    Json(state.snapshot().await).into_response()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::SchedulerConfig;

    #[test]
    fn the_router_builds_without_route_conflicts() {
        let scheduler = Scheduler::new(SchedulerConfig::new("http://127.0.0.1:1")).expect("built");
        let _router = router(Arc::new(scheduler));
    }

    #[test]
    fn jobs_guard_rejects_unauthenticated_non_loopback_host() {
        let mut headers = HeaderMap::new();
        headers.insert(header::HOST, "remote.example:4825".parse().unwrap());
        let refused = guard_jobs_request(&headers).unwrap_err();
        assert_eq!(refused.status(), StatusCode::FORBIDDEN);

        headers.insert(
            header::AUTHORIZATION,
            "Bearer secret-token".parse().unwrap(),
        );
        assert!(guard_jobs_request(&headers).is_ok());

        let mut loopback = HeaderMap::new();
        loopback.insert(header::HOST, "127.0.0.1:4825".parse().unwrap());
        assert!(guard_jobs_request(&loopback).is_ok());
    }
}
