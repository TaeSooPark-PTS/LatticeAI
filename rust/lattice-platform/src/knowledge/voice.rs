pub const KEEP: &[(&str, &str)] = &[];

pub const MOUNTED: &[(&str, &str)] = &[("POST", "/api/capture/voice")];

#[derive(Clone, Default)]
pub struct VoiceState {
    pub auth: Option<std::sync::Arc<lattice_auth::AuthState>>,
    pub graph: Option<lattice_core::graph_write::GraphWriter>,
    pub seam: Option<lattice_core::worker::WorkerSeamClient>,
}

pub fn router() -> axum::Router {
    router_with(VoiceState::default())
}

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
    let parsed = serde_json::from_slice::<serde_json::Value>(&body).ok();
    let provided_title = parsed
        .as_ref()
        .and_then(|value| value.get("title"))
        .and_then(serde_json::Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string();
    let provided_text = parsed
        .as_ref()
        .and_then(|value| value.get("text"))
        .and_then(serde_json::Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string();
    let audio_b64 = if let Some(value) = parsed.as_ref() {
        value
            .get("audio_b64")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("")
            .to_string()
    } else {
        use base64::Engine;
        base64::engine::general_purpose::STANDARD.encode(&body)
    };

    if !audio_b64.is_empty() {
        if let Some(auth) = &state.auth {
            let mime = parsed
                .as_ref()
                .and_then(|v| v.get("mime_type").and_then(serde_json::Value::as_str))
                .unwrap_or("audio/mp4");
            let ws = headers
                .get("x-lattice-workspace")
                .and_then(|v| v.to_str().ok());
            let decoded_estimate = (audio_b64.len() * 3) / 4;
            if let Err(violation) = crate::knowledge::multimodal::validate_speech_audio_payload(
                &auth.config().data_dir,
                mime,
                decoded_estimate.max(1),
                None,
                ws,
            ) {
                let err_json = serde_json::to_string(
                    &json!({"detail": violation.message, "code": violation.code}),
                )
                .unwrap_or_else(|_| "{}".into());
                return json_response(StatusCode::BAD_REQUEST, &err_json, None);
            }
        }
    }

    let mut transcription = "unavailable".to_string();
    let mut asr_text = String::new();
    let mut provider = json!("");
    let mut asr_detail = json!("");

    if let Some(seam) = state.seam.clone() {
        match seam
            .post_json(
                "/worker/asr",
                &json!({"audio_b64": audio_b64, "filename": "memo.m4a"}),
            )
            .await
        {
            Ok(asr) => {
                asr_text = asr
                    .get("text")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or("")
                    .trim()
                    .to_string();
                provider = asr.get("provider").cloned().unwrap_or(json!(""));
                asr_detail = asr.get("detail").cloned().unwrap_or(json!(""));
                let status = asr
                    .get("status")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or("");
                transcription = if status == "unavailable" || asr_text.is_empty() {
                    "unavailable".into()
                } else {
                    "ok".into()
                };
            }
            Err(err) => {
                if provided_text.is_empty() {
                    let body = serde_json::to_string(&json!({"detail": err.to_string()}))
                        .unwrap_or_default();
                    return json_response(StatusCode::BAD_GATEWAY, &body, None);
                }
                asr_detail = json!(err.to_string());
            }
        }
    } else if provided_text.is_empty() {
        let body = serde_json::to_string(&json!({"detail": "asr seam is not configured"}))
            .unwrap_or_default();
        return json_response(StatusCode::SERVICE_UNAVAILABLE, &body, None);
    }

    let memo = if provided_text.is_empty() {
        asr_text
    } else {
        provided_text
    };
    let title = if provided_title.is_empty() {
        "Voice capture".to_string()
    } else {
        provided_title
    };

    let mut node_id = serde_json::Value::Null;
    if !memo.is_empty() {
        if let Some(graph) = state.graph.clone() {
            let text = memo.clone();
            let written = tokio::task::spawn_blocking(move || {
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
            if let Ok(Ok(outcome)) = written {
                node_id = json!(outcome.node_id);
            }
        }
    }

    let payload = json!({
        "status": "ok",
        "transcription": transcription,
        "text": memo,
        "provider": provider,
        "detail": asr_detail,
        "node_id": node_id,
    });
    let body = serde_json::to_string(&payload).unwrap_or_else(|_| "{}".into());
    json_response(StatusCode::OK, &body, None)
}
