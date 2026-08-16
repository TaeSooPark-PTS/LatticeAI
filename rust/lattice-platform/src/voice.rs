//! Voice-capture family.
//!
//! `POST /api/capture/voice` is native since W3b: this module stores the memo
//! and asks the worker's `POST /worker/asr` for the words.
//!
//! `GET /api/capture/voice/status` was the family's other half and was
//! KEEP_WORKER — a capability probe answered inside the interpreter holding
//! the transcriber. v11.8.0 deleted it: no surface ever called it, and
//! `/worker/asr` already reports per call whether it heard anything. It is
//! therefore neither mounted here nor on the worker, and it is off the
//! gateway's proxy allowlist. Its `voice_capture.py` HTTP fixtures stay in
//! `rust/fixtures/http/tools_misc.json` as the frozen record of what the route
//! answered while it existed.

/// Routes this crate must **not** claim — they stay on the worker allowlist.
/// Empty since v11.8.0: the one entry was the status probe, now deleted
/// outright. Kept as a declared, empty table so the parity test still asserts
/// the family claims nothing it should not.
pub const KEEP: &[(&str, &str)] = &[];

/// Native mounts. Spec still lives in worker_keep.json.
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

/// Native voice router — the whole surviving family.
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
