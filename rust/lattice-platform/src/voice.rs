//! Voice-capture family — KEEP_WORKER routes stay on the Python worker.
//!
//! Scout + the plan's final worker surface keep both voice-capture routes
//! in process with the ASR port:
//!
//! * `GET  /api/capture/voice/status`
//! * `POST /api/capture/voice`
//!
//! This module therefore mounts nothing. The three HTTP fixtures for
//! `voice_capture.py` are the status route; they are listed as KEEP gaps,
//! not replayed here.

#![allow(
    dead_code,
    unused_imports,
    unused_variables,
    unused_assignments,
    unused_mut,
    private_interfaces,
    clippy::result_large_err,
    clippy::needless_lifetimes,
    clippy::too_many_arguments,
    clippy::type_complexity,
    clippy::collapsible_if,
    clippy::needless_as_bytes,
    clippy::redundant_closure,
    clippy::needless_return,
    clippy::manual_clamp,
    clippy::ptr_arg,
    clippy::unnecessary_sort_by,
    clippy::result_unit_err,
    clippy::useless_vec,
    clippy::uninlined_format_args,
    clippy::manual_contains,
    clippy::needless_borrows_for_generic_args,
    clippy::implicit_clone,
    clippy::unnecessary_map_or,
    clippy::match_like_matches_macro,
    clippy::manual_range_contains,
    clippy::derivable_impls,
    clippy::needless_pass_by_ref_mut,
    clippy::redundant_guards,
    clippy::map_identity,
    clippy::iter_overeager_cloned,
    clippy::explicit_auto_deref,
    clippy::bool_comparison,
    clippy::nonminimal_bool,
    clippy::if_same_then_else,
    clippy::question_mark,
    clippy::single_char_pattern,
    clippy::manual_pattern_char_comparison,
    clippy::manual_is_ascii_check,
    clippy::repeat_once,
    clippy::unused_self,
    clippy::module_inception
)]
/// Routes this crate must **not** claim — they stay on the worker allowlist.
pub const KEEP: &[(&str, &str)] = &[
    ("GET", "/api/capture/voice/status"),
    ("POST", "/api/capture/voice"),
];

/// Native mounts. `POST /api/capture/voice` is product-native (W3b);
/// status stays KEEP (ASR probe). Spec for both still lives in worker_keep.json.
pub const MOUNTED: &[(&str, &str)] = &[("POST", "/api/capture/voice")];

/// Voice capture state.
#[derive(Clone, Default)]
pub struct VoiceState {
    /// Auth.
    pub auth: Option<std::sync::Arc<lattice_auth::AuthState>>,
    /// Native writer.
    pub graph: Option<lattice_core::graph_write::GraphWriter>,
    /// Worker compute (`/worker/asr`).
    pub seam: Option<lattice_core::worker::WorkerSeamClient>,
}

/// Empty router — host-compatible. Prefer [`router_with`] once wired.
pub fn router() -> axum::Router {
    router_with(VoiceState::default())
}

/// Native voice router. `GET /api/capture/voice/status` stays on the worker.
pub fn router_with(state: VoiceState) -> axum::Router {
    use axum::routing::post;
    axum::Router::new()
        .route("/api/capture/voice", post(capture_voice))
        .with_state(state)
}

async fn capture_voice(
    axum::extract::State(state): axum::extract::State<VoiceState>,
    headers: axum::http::HeaderMap,
    body: axum::body::Bytes,
) -> axum::response::Response {
    use axum::http::StatusCode;
    use lattice_auth::response::json_response;
    use serde_json::json;
    if let Some(auth) = &state.auth {
        if let Err(refusal) = auth.require_user(&headers) {
            return refusal;
        }
    }
    let Some(seam) = state.seam.clone() else {
        let body = serde_json::to_string(&json!({"detail": "asr seam is not configured"}))
            .unwrap_or_default();
        return json_response(StatusCode::SERVICE_UNAVAILABLE, &body, None);
    };
    // Multipart audio → base64 for `/worker/asr`. Tests typically send JSON.
    let audio_b64 = if let Ok(value) = serde_json::from_slice::<serde_json::Value>(&body) {
        value
            .get("audio_b64")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("")
            .to_string()
    } else {
        use base64::Engine;
        base64::engine::general_purpose::STANDARD.encode(&body)
    };
    let asr = match seam
        .post_json(
            "/worker/asr",
            &json!({"audio_b64": audio_b64, "filename": "memo.m4a"}),
        )
        .await
    {
        Ok(value) => value,
        Err(err) => {
            let body =
                serde_json::to_string(&json!({"detail": err.to_string()})).unwrap_or_default();
            return json_response(StatusCode::BAD_GATEWAY, &body, None);
        }
    };
    let text = asr
        .get("text")
        .and_then(serde_json::Value::as_str)
        .unwrap_or("");
    if let Some(graph) = state.graph.clone() {
        let title = "Voice capture".to_string();
        let text = text.to_string();
        let _ = tokio::task::spawn_blocking(move || {
            let request = lattice_core::graph_write::types::IngestContentRequest {
                source_type: "voice".into(),
                title,
                text,
                node_type: Some("Audio".into()),
                ..Default::default()
            };
            graph.ingest_content(&request)
        })
        .await;
    }
    let payload = json!({
        "status": asr.get("status").cloned().unwrap_or(json!("ok")),
        "text": text,
        "provider": asr.get("provider").cloned().unwrap_or(json!("")),
        "detail": asr.get("detail").cloned().unwrap_or(json!("")),
    });
    let body = serde_json::to_string(&payload).unwrap_or_else(|_| "{}".into());
    json_response(StatusCode::OK, &body, None)
}
