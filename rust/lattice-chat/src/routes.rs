//! The chat + history router this crate owns.
//!
//! `GET /chat` (308 to `/app#/chat`) is WP-I4's. The `/agent*` operations that
//! share `rust/fixtures/openapi/chat.json` are `lattice-agent`'s, mounted by
//! the host. `tests/openapi_contract.rs` states both splits.

use std::sync::Arc;

use axum::routing::{get, post};
use axum::Router;

use crate::history;
use crate::pipeline;
use crate::state::ChatState;

/// The routes this crate mounts, in the shape WP-I5's contract test compares.
///
/// Paths use axum 0.7 spelling (`/*conversation_id` for FastAPI `{id:path}`).
/// The `/agent*` ops and `GET /chat` are intentionally absent — see the
/// module docs.
pub const MOUNTED: &[(&str, &str)] = &[
    ("POST", "/chat"),
    ("DELETE", "/history"),
    ("GET", "/history"),
    ("GET", "/history/conversations"),
    ("DELETE", "/history/conversations/*conversation_id"),
    ("GET", "/history/conversations/*conversation_id"),
    ("GET", "/history/search"),
];

/// Build the chat + history router over one process-wide [`ChatState`].
pub fn router(state: ChatState) -> Router {
    router_from_arc(Arc::new(state))
}

/// Same factory, taking a pre-shared `Arc` so a test can hold a clone.
pub fn router_from_arc(state: Arc<ChatState>) -> Router {
    Router::new()
        .route("/chat", post(pipeline::chat))
        .route(
            "/history",
            get(history::fetch_history).delete(history::delete_history),
        )
        .route("/history/conversations", get(history::fetch_conversations))
        .route(
            "/history/conversations/*conversation_id",
            get(history::fetch_conversation).delete(history::delete_conversation),
        )
        .route("/history/search", get(history::search))
        .with_state((*state).clone())
}
