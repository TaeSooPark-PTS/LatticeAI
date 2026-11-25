use std::sync::Arc;

use axum::extract::{Request, State};
use axum::middleware::Next;
use axum::response::Response;

use crate::csrf::csrf_denied_response;
use crate::state::{peer_of, AuthState};

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
