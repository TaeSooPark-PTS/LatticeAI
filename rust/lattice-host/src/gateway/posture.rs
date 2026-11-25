use std::sync::Arc;
use std::time::Duration;

use axum::extract::{Request, State};
use axum::http::StatusCode;
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::Value;

use super::GatewayState;
use crate::supervisor::HealthReport;

pub const POSTURE_TTL: Duration = Duration::from_secs(5);

pub const REFUSED_ERROR: &str = "native_lane_requires_open_posture";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Posture {
    Open,
    Closed,
    Unknown,
}

impl Posture {
    pub fn is_open(self) -> bool {
        matches!(self, Posture::Open)
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Posture::Open => "open",
            Posture::Closed => "closed",
            Posture::Unknown => "unknown",
        }
    }

    pub fn from_health(body: &Value) -> Self {
        let Some(access) = body.get("access") else {
            return Posture::Unknown;
        };
        let (Some(require_auth), Some(externally_reachable)) = (
            access.get("require_auth").and_then(Value::as_bool),
            access.get("externally_reachable").and_then(Value::as_bool),
        ) else {
            return Posture::Unknown;
        };
        if require_auth || externally_reachable {
            Posture::Closed
        } else {
            Posture::Open
        }
    }

    pub fn from_report(report: &HealthReport) -> Self {
        match (report.healthy, &report.body) {
            (true, Some(body)) => Posture::from_health(body),
            _ => Posture::Unknown,
        }
    }
}

pub fn refuse(posture: Posture, path: &str) -> Response {
    let detail = match posture {
        Posture::Closed => {
            "this worker requires authentication or is reachable off this machine; \
             the native /rust and /host lanes are the trusted-local-owner surface \
             and carry no credential, so they refuse rather than answer"
        }
        _ => {
            "the worker has not reported its access posture on GET /health; \
             the native lanes fail closed rather than guess"
        }
    };
    (
        StatusCode::UNAUTHORIZED,
        Json(serde_json::json!({
            "error": REFUSED_ERROR,
            "detail": detail,
            "posture": posture.as_str(),
            "path": path,
        })),
    )
        .into_response()
}

pub async fn require_open_posture(
    State(state): State<Arc<GatewayState>>,
    request: Request,
    next: Next,
) -> Response {
    let posture = state.worker_posture().await;
    if posture.is_open() {
        return next.run(request).await;
    }
    let path = request.uri().path().to_string();
    refuse(posture, &path)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn the_open_posture_is_pythons_trusted_local_owner() {
        assert_eq!(
            Posture::from_health(&json!({
                "access": {"require_auth": false, "externally_reachable": false}
            })),
            Posture::Open
        );
        for (require_auth, reachable) in [(true, false), (false, true), (true, true)] {
            assert_eq!(
                Posture::from_health(&json!({
                    "access": {
                        "require_auth": require_auth,
                        "externally_reachable": reachable,
                    }
                })),
                Posture::Closed,
                "require_auth={require_auth} reachable={reachable}"
            );
        }
    }

    #[test]
    fn an_unreadable_payload_is_unknown_rather_than_a_default() {
        for body in [
            json!({}),
            json!({"status": "ok"}),
            json!({"access": {}}),
            json!({"access": {"require_auth": false}}),
            json!({"access": {"externally_reachable": false}}),
            json!({"access": {"require_auth": "no", "externally_reachable": false}}),
            json!({"access": "open"}),
            json!("ok"),
        ] {
            assert_eq!(
                Posture::from_health(&body),
                Posture::Unknown,
                "must not guess from {body}"
            );
        }
    }

    #[test]
    fn a_failed_probe_is_unknown() {
        assert_eq!(
            Posture::from_report(&HealthReport::default()),
            Posture::Unknown
        );
        assert_eq!(
            Posture::from_report(&HealthReport {
                healthy: true,
                body: None,
            }),
            Posture::Unknown
        );
        assert_eq!(
            Posture::from_report(&HealthReport {
                healthy: false,
                body: Some(json!({
                    "access": {"require_auth": false, "externally_reachable": false}
                })),
            }),
            Posture::Unknown,
            "a body from a non-2xx answer is not a posture"
        );
        assert_eq!(
            Posture::from_report(&HealthReport {
                healthy: true,
                body: Some(json!({
                    "access": {"require_auth": false, "externally_reachable": false}
                })),
            }),
            Posture::Open
        );
    }

    #[tokio::test]
    async fn the_refusal_names_the_posture_and_the_path() {
        for posture in [Posture::Closed, Posture::Unknown] {
            let response = refuse(posture, "/rust/graph/search");
            assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
            let bytes = axum::body::to_bytes(response.into_body(), usize::MAX)
                .await
                .expect("body");
            let body: Value = serde_json::from_slice(&bytes).expect("json");
            assert_eq!(body["error"], REFUSED_ERROR);
            assert_eq!(body["posture"], posture.as_str());
            assert_eq!(body["path"], "/rust/graph/search");
            assert!(!body["detail"].as_str().unwrap_or_default().is_empty());
        }
        assert_eq!(Posture::Open.as_str(), "open");
        assert!(Posture::Open.is_open());
        assert!(!Posture::Closed.is_open());
        assert!(!Posture::Unknown.is_open());
    }
}
