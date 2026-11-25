use std::sync::Arc;

use axum::routing::{get, post};
use axum::Router;

use crate::history;
use crate::history::json_body;
use crate::pipeline;
use crate::state::ChatState;

pub const MOUNTED: &[(&str, &str)] = &[
    ("GET", "/api/cloud/status"),
    ("POST", "/chat"),
    ("DELETE", "/history"),
    ("GET", "/history"),
    ("GET", "/history/conversations"),
    ("DELETE", "/history/conversations/*conversation_id"),
    ("GET", "/history/conversations/*conversation_id"),
    ("GET", "/history/search"),
];

pub fn router(state: ChatState) -> Router {
    router_from_arc(Arc::new(state))
}

pub fn router_from_arc(state: Arc<ChatState>) -> Router {
    Router::new()
        .route("/api/cloud/status", get(cloud_status))
        .route("/chat", post(pipeline::chat))
        .route(
            "/history",
            get(history::fetch_history).delete(history::delete_history),
        )
        .route("/history/conversations", get(history::fetch_conversations))
        .route(
            "/history/conversations/{*conversation_id}",
            get(history::fetch_conversation).delete(history::delete_conversation),
        )
        .route("/history/search", get(history::search))
        .with_state((*state).clone())
}

async fn cloud_status(
    axum::extract::State(state): axum::extract::State<ChatState>,
    headers: axum::http::HeaderMap,
) -> axum::response::Response {
    if let Err(refusal) = state.auth.require_user(&headers) {
        return refusal;
    }
    json_body(
        axum::http::StatusCode::OK,
        &state.cloud_status().await.to_value(),
    )
}
