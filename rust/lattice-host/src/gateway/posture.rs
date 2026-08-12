//! The worker's access posture, and the guard the native lanes sit behind.
//!
//! Everything under `/rust/*` (and `/host/status`, `/host/jobs`) is answered by
//! this process out of the store directly. Those handlers resolve
//! `allowed_workspaces = None` and carry no credential — they are Python's
//! `trusted_local_owner` branch, spelled natively. Python computes that branch
//! as
//!
//! ```text
//! trusted_local_owner = not require_auth and not externally_reachable
//! ```
//!
//! (`latticeai/runtime/access_runtime.py`), and until 11.5.2 the gateway
//! mirrored only the *loopback bind* half of it: with
//! `LATTICEAI_REQUIRE_AUTH=true` the worker answered 401 while the gateway
//! happily served the whole knowledge graph next to it. This module closes that
//! by asking the worker what its posture is (it states it in `GET /health`) and
//! refusing the native lanes unless the answer is "open".
//!
//! **Fail closed.** A worker that cannot be reached, answers no posture, or is
//! too old to state one leaves the posture [`Posture::Unknown`], and unknown
//! refuses exactly like closed. Guessing "probably open" is the one option that
//! turns a missing fact into an unauthenticated read of someone's brain.
//!
//! `/host/health` stays open on purpose: it is liveness, it is what the desktop
//! shell and the smoke tests poll, and it is where a person reads *why* the
//! worker is not up. It carries the supervisor snapshot and nothing from the
//! store.

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

/// How long an observed posture is reused before the worker is asked again.
///
/// Matched to the supervisor's liveness poll: the posture can only change when
/// the worker restarts with a different environment, and a restart is already
/// several seconds of unavailability.
pub const POSTURE_TTL: Duration = Duration::from_secs(5);

/// The error a refused native request carries.
pub const REFUSED_ERROR: &str = "native_lane_requires_open_posture";

/// What the worker says about who may talk to it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Posture {
    /// `trusted_local_owner`: no authentication required and not reachable off
    /// this machine. The native lanes may answer.
    Open,
    /// The worker requires authentication, or is reachable off this machine, or
    /// both. The native lanes carry no credential, so they refuse.
    Closed,
    /// The worker has not said. Treated as closed.
    Unknown,
}

impl Posture {
    /// Whether the native lanes may answer.
    pub fn is_open(self) -> bool {
        matches!(self, Posture::Open)
    }

    /// The wire spelling, used in the refusal body.
    pub fn as_str(self) -> &'static str {
        match self {
            Posture::Open => "open",
            Posture::Closed => "closed",
            Posture::Unknown => "unknown",
        }
    }

    /// Read the posture out of a `/health` payload.
    ///
    /// Both facts must be present and boolean. A payload that states only one
    /// of them (or states them as something other than a boolean) is a payload
    /// this gateway does not understand, and an unreadable posture is
    /// [`Posture::Unknown`], never a default.
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

    /// Read the posture out of one health probe.
    pub fn from_report(report: &HealthReport) -> Self {
        match (report.healthy, &report.body) {
            (true, Some(body)) => Posture::from_health(body),
            _ => Posture::Unknown,
        }
    }
}

/// The 401 a refused native request gets.
///
/// Machine-readable on purpose: `/rust/agent/run` used to die *mid-loop* with a
/// 502 when the seam behind it answered 401, and "the front door refused before
/// starting, and here is the reason" is a different and much better failure.
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

/// Middleware: let the request through only while the worker's posture is open.
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
