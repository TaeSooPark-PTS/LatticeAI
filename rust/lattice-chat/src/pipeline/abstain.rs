use axum::body::Body;
use axum::http::StatusCode;
use axum::response::Response;
use lattice_auth::OrderedMap;
use serde_json::{json, Value};

use crate::contracts::ChatRequest;
use crate::history::json_body;
use crate::intents::HistoryMeta;
use crate::sse::{data_frame, stream_response, DONE};
use crate::state::ChatState;
use crate::stream::frame_channel;

use super::answers::persist_answer;

pub(crate) fn local_only_evidence_message(language: &str) -> String {
    if language == "ko" {
        "관련 기록은 로컬 전용이라 클라우드로 보낼 수 없고, 로컬 모델이 로드되어 있지 않습니다. 로컬 모델을 불러온 뒤 다시 시도해 주세요.".to_string()
    } else {
        "The matching records are local-only and cannot be sent to the cloud, and no local model is loaded. Load a local model and ask again.".to_string()
    }
}

#[allow(clippy::too_many_arguments)]
pub(crate) async fn abstention_response(
    state: &ChatState,
    req: &ChatRequest,
    model_id: &str,
    message: String,
    reason: Value,
    trace_seed: &Value,
    context_quality: &Value,
    effective_email: Option<&str>,
    workspace_id: Option<&str>,
    meta: &HistoryMeta<'_>,
) -> Response {
    let grounding = json!({
        "status": "no_context",
        "label": "근거 없음",
        "source_ids": [],
        "cited": [],
        "overlap": 0.0,
        "reason": message,
    });
    let mut trace = trace_seed.clone();
    if let Some(object) = trace.as_object_mut() {
        for key in ["source_files", "graph_nodes", "graph_edges"] {
            object.insert(key.into(), json!([]));
        }
        object.insert("confidence".into(), json!(0.0));
        object.insert("grounding".into(), grounding.clone());
    }
    let record = persist_answer(
        state,
        req,
        &message,
        &trace,
        effective_email,
        workspace_id,
        meta,
    )
    .await;
    let abstention = json!({"abstained": true, "reason": reason});
    let mut body = OrderedMap::new();
    if req.stream {
        let (sink, stream) = frame_channel();
        let piece = json!({"chunk": message, "model": model_id});
        body.insert("chunk", json!(""));
        body.insert("model", json!(model_id));
        body.insert("trace_id", record.get("id").cloned().unwrap_or(Value::Null));
        body.insert("trace", record);
        body.insert("context_quality", context_quality.clone());
        body.insert("grounding", grounding);
        body.insert("abstention", abstention);
        let trailer = serde_json::to_value(body).unwrap_or(Value::Null);
        tokio::spawn(async move {
            let _ = sink.send(data_frame(&piece)).await;
            let _ = sink.send(data_frame(&trailer)).await;
            let _ = sink.send(DONE).await;
        });
        return stream_response(Body::from_stream(stream), &[("X-Model", model_id)]);
    }
    body.insert("response", json!(message));
    body.insert("trace_id", record.get("id").cloned().unwrap_or(Value::Null));
    body.insert("trace", record);
    body.insert("context_quality", context_quality.clone());
    body.insert("grounding", grounding);
    body.insert("abstention", abstention);
    json_body(
        StatusCode::OK,
        &serde_json::to_value(body).unwrap_or(Value::Null),
    )
}
