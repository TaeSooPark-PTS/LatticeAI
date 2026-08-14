//! The gateway-wide layers this crate owns.
//!
//! One today: the CSRF Origin guard. It is a *request-header* decision, so it
//! must not touch the response — the product streams (SSE chat, live agent
//! steps), and buffering a response to make a decision the request already
//! answered is exactly the mistake `core/csrf.py` avoided by being pure ASGI
//! rather than a `BaseHTTPMiddleware`. `axum::middleware::from_fn` passes the
//! response through untouched, so the same property holds here.
//!
//! Security headers (CSP and friends) are **not** here: `PRODUCTION_CSP` lives
//! with the SPA shell in `api/static_routes.py`, and the crate that serves the
//! shell is the one that should state its policy (WP-I4).

use std::sync::Arc;

use axum::extract::{Request, State};
use axum::middleware::Next;
use axum::response::Response;

use crate::csrf::csrf_denied_response;
use crate::state::{peer_of, AuthState};

/// Refuse a cookie-authenticated state change whose origin we cannot vouch for.
///
/// Mount it around everything the front door serves:
///
/// ```no_run
/// # use std::sync::Arc;
/// # let state: Arc<lattice_auth::AuthState> =
/// #     lattice_auth::AuthState::new(lattice_auth::AuthConfig::from_env());
/// let app: axum::Router = lattice_auth::router(Arc::clone(&state))
///     .layer(axum::middleware::from_fn_with_state(
///         state,
///         lattice_auth::csrf_guard,
///     ));
/// # let _ = app;
/// ```
pub async fn csrf_guard(
    State(state): State<Arc<AuthState>>,
    request: Request,
    next: Next,
) -> Response {
    let (parts, body) = request.into_parts();
    let peer = peer_of(&parts);
    let decision =
        state
            .csrf_policy()
            .evaluate_headers(&parts.method, &parts.headers, peer.as_deref());
    if !decision.allowed {
        return csrf_denied_response(decision.reason);
    }
    next.run(Request::from_parts(parts, body)).await
}
