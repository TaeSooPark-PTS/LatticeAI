use std::collections::BTreeSet;

use axum::body::Body;
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use lattice_auth::OrderedMap;
use lattice_core::embeddings::LocalEmbeddingModel;
use serde_json::{json, Value};

use crate::boundary::{request_network_mode, NetworkMode};
use crate::contracts::ChatRequest;
use crate::documents;
use crate::graph::{build_context_quality, build_graph_trace, QUALITY_LIMIT, TRACE_LIMIT};
use crate::helpers::{assess_answer_grounding, detect_language, language_hint};
use crate::history::json_body;
use crate::intents::{persist_entry, HistoryMeta};
use crate::sse::{data_frame, stream_response, DONE};
use crate::state::ChatState;
use crate::stream::frame_channel;
use crate::worker::{ChatWorker, FrameReader, LlmFrame};

use super::answers::{document_response, hybrid_response, persist_answer};
use super::{file_path_count, now_secs, recent_context};

#[allow(clippy::too_many_arguments)]
pub(crate) async fn run_model_turn(
    state: ChatState,
    req: ChatRequest,
    _headers: HeaderMap,
    model_id: String,
    effective_email: Option<String>,
    workspace_id: Option<String>,
    hist_email: Option<String>,
    hist_nick: Option<String>,
) -> Response {
    let language = detect_language(&req.message);
    let mut prompt_context = format!(
        "[LANGUAGE: {}]\n{}",
        language_hint(language),
        req.context.as_deref().unwrap_or("")
    );
    let mut context_trace = None;
    if let Some(conn) = state.read_conn() {
        let request = lattice_retrieval::ContextRequest {
            query: req.message.clone(),
            budget: 2000,
            knowledge: true,
            user_email: effective_email.clone(),
            conversation_id: req.conversation_id.clone(),
            workspace_id: workspace_id.clone(),
            now_secs: now_secs(),
            ..Default::default()
        };
        if let Ok(assembled) =
            lattice_retrieval::assemble_context(&conn, &LocalEmbeddingModel::default(), &request)
        {
            context_trace = assembled.get("trace").cloned();
            if let Some(text) = assembled.get("text").and_then(Value::as_str) {
                if !text.is_empty() {
                    prompt_context.push_str("\n\n");
                    prompt_context.push_str(text);
                }
            }
        }
    }

    let prepared = if let Some(conn) = state.read_conn() {
        documents::prepare(
            Some(&conn),
            &req.message,
            &prompt_context,
            workspace_id.as_deref(),
            now_secs(),
        )
    } else {
        documents::prepare(
            None,
            &req.message,
            &prompt_context,
            workspace_id.as_deref(),
            now_secs(),
        )
    };
    prompt_context = prepared.context.clone();
    if let Some(image) = req.image_data.as_deref() {
        let screenshot = documents::screenshot_context(image);
        if !screenshot.is_empty() {
            prompt_context.push_str("\n\n");
            prompt_context.push_str(&screenshot);
        }
    }

    if state.config.auto_read_chat_paths {
        let count = file_path_count(&req.message);
        if count > 0 {
            state.audit(
                "auto_file_context_blocked",
                &json!({
                    "user_email": effective_email,
                    "path_count": count,
                    "allow_file_context": req.allow_file_context,
                    "reason": "local file context requires an explicit approved file/tool flow",
                }),
            );
            if req.allow_file_context {
                return json_body(
                    StatusCode::BAD_REQUEST,
                    &json!({
                        "detail": "Automatic local file reads are disabled in chat. \
                                   Attach the file, upload it, or use an approved \
                                   local-file tool flow."
                    }),
                );
            }
        }
    }

    let allowed = workspace_id
        .as_deref()
        .filter(|id| !id.is_empty())
        .map(|id| [id.to_string()].into_iter().collect::<BTreeSet<_>>());
    let conn = state.read_conn();
    let mut trace_seed = build_graph_trace(
        conn.as_ref(),
        &req.message,
        &prompt_context,
        TRACE_LIMIT,
        allowed.as_ref(),
    );
    if let Some(trace) = context_trace {
        if let Some(object) = trace_seed.as_object_mut() {
            object.insert("context_assembly".into(), trace);
        }
    }
    let context_quality = build_context_quality(
        conn.as_ref(),
        &LocalEmbeddingModel::default(),
        &req.message,
        allowed.as_ref(),
        QUALITY_LIMIT,
        now_secs(),
    );
    if let Some(object) = trace_seed.as_object_mut() {
        object.insert("context_quality".into(), context_quality.clone());
    }
    if context_quality
        .get("nodes")
        .and_then(Value::as_i64)
        .unwrap_or(0)
        > 0
    {
        if let Some(funnel) = state.funnel.as_ref() {
            funnel.record_recall_success();
        }
    }

    let history_message = if req.image_data.is_some() {
        format!("{}\n[Image attached]", req.message)
    } else {
        req.message.clone()
    };
    let meta = HistoryMeta {
        email: hist_email.as_deref(),
        nickname: hist_nick.as_deref(),
        source: req.source.as_deref().or(Some("web")),
        conversation_id: req.conversation_id.as_deref(),
        workspace_id: workspace_id.as_deref(),
    };
    persist_entry(&state, "user", &history_message, &meta).await;
    state.notify("user", &req.message, req.source.as_deref());

    if let Some(response) = document_response(
        &state,
        &req,
        &prepared,
        &model_id,
        effective_email.as_deref(),
        workspace_id.as_deref(),
        &meta,
        &trace_seed,
    )
    .await
    {
        return response;
    }

    let mode = request_network_mode(
        req.network_mode.as_deref(),
        &state.config.data_dir,
        effective_email.as_deref(),
        workspace_id.as_deref(),
    );
    if mode == NetworkMode::CloudAllowed {
        let policy = crate::boundary::resolve_hybrid_policy(
            &state.config.data_dir,
            effective_email.as_deref(),
            workspace_id.as_deref(),
        );
        let matched_nodes = context_quality
            .get("nodes")
            .and_then(Value::as_i64)
            .unwrap_or(0);
        if let Some(reason) = crate::boundary::escalation_reason(
            policy.escalation,
            model_id.is_empty(),
            matched_nodes,
            &req.message,
        ) {
            if let Some(provider) = state.resolved_cloud_provider() {
                if provider.configured() {
                    if let Some(response) = hybrid_response(
                        &state,
                        &req,
                        mode,
                        provider,
                        reason,
                        effective_email.as_deref(),
                        workspace_id.as_deref(),
                        &meta,
                    )
                    .await
                    {
                        return response;
                    }
                }
            }
        }
    }

    let recent = recent_context(
        &state,
        effective_email.as_deref(),
        req.conversation_id.as_deref(),
        workspace_id.as_deref(),
        if req.stream {
            10
        } else if req.image_data.is_some() {
            6
        } else {
            10
        },
        req.image_data.is_none() || req.stream,
    );
    let stream_context = if recent.is_empty() {
        prompt_context.clone()
    } else if req.stream || req.image_data.is_some() {
        format!("[RECENT CONVERSATION]\n{recent}\n\n{prompt_context}")
            .trim()
            .to_string()
    } else if prompt_context.is_empty() {
        recent
    } else {
        format!("{recent}\n{prompt_context}")
    };

    if req.stream {
        return stream_model_turn(
            state,
            req,
            model_id,
            stream_context,
            trace_seed,
            context_quality,
            effective_email,
            workspace_id,
            hist_email,
            hist_nick,
        );
    }

    let (answer, stream_error) = collect_completion(
        state.worker.as_ref(),
        Some(&model_id),
        &req.message,
        &stream_context,
        req.max_tokens,
        req.temperature,
        req.image_data.as_deref(),
        false,
    )
    .await;
    let mut persisted = answer.clone();
    if let Some(error) = stream_error.as_ref() {
        persisted = if persisted.is_empty() {
            format!("[stream_error] {error}")
        } else {
            format!("{persisted}\n\n[stream_error] {error}")
        };
    }
    let grounding = assess_answer_grounding(&answer, Some(&trace_seed), Some(&context_quality));
    if let Some(object) = trace_seed.as_object_mut() {
        object.insert("grounding".into(), grounding.clone());
    }
    let record = persist_answer(
        &state,
        &req,
        &persisted,
        &trace_seed,
        effective_email.as_deref(),
        workspace_id.as_deref(),
        &meta,
    )
    .await;
    let mut body = OrderedMap::new();
    body.insert("response", json!(answer));
    body.insert("trace_id", record.get("id").cloned().unwrap_or(Value::Null));
    body.insert("trace", record);
    body.insert("context_quality", context_quality);
    body.insert("grounding", grounding);
    json_body(
        StatusCode::OK,
        &serde_json::to_value(body).unwrap_or(Value::Null),
    )
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn stream_model_turn(
    state: ChatState,
    req: ChatRequest,
    model_id: String,
    context: String,
    mut trace_seed: Value,
    context_quality: Value,
    effective_email: Option<String>,
    workspace_id: Option<String>,
    hist_email: Option<String>,
    hist_nick: Option<String>,
) -> Response {
    let (sink, stream) = frame_channel();
    let header_model = model_id.clone();
    tokio::spawn(async move {
        let mut full = String::new();
        let mut stream_error = None;
        if let Some(worker) = state.worker.as_ref() {
            match worker
                .llm_stream(
                    Some(&model_id),
                    &req.message,
                    &context,
                    req.max_tokens,
                    req.temperature,
                    req.image_data.as_deref(),
                )
                .await
            {
                Ok(upstream) => {
                    let mut reader = FrameReader::new();
                    let mut bytes = std::pin::pin!(upstream.into_byte_stream());
                    while let Some(chunk) = next_chunk(&mut bytes).await {
                        let Ok(chunk) = chunk else {
                            stream_error = Some("upstream closed".into());
                            break;
                        };
                        for frame in reader.push(&chunk) {
                            match frame {
                                LlmFrame::Text(text) => {
                                    full.push_str(&text);
                                    let mut payload = OrderedMap::new();
                                    payload.insert("chunk", json!(text));
                                    payload.insert("model", json!(model_id));
                                    if !sink
                                        .send(data_frame(
                                            &serde_json::to_value(payload).unwrap_or(Value::Null),
                                        ))
                                        .await
                                    {
                                        return;
                                    }
                                }
                                LlmFrame::Error(error) => {
                                    stream_error = Some(error.clone());
                                    let mut payload = OrderedMap::new();
                                    payload.insert("error", json!(error));
                                    payload.insert("model", json!(model_id));
                                    let _ = sink
                                        .send(data_frame(
                                            &serde_json::to_value(payload).unwrap_or(Value::Null),
                                        ))
                                        .await;
                                }
                                LlmFrame::Done => {}
                            }
                        }
                    }
                    if let Some(LlmFrame::Text(text)) = reader.finish() {
                        full.push_str(&text);
                        let mut payload = OrderedMap::new();
                        payload.insert("chunk", json!(text));
                        payload.insert("model", json!(model_id));
                        let _ = sink
                            .send(data_frame(
                                &serde_json::to_value(payload).unwrap_or(Value::Null),
                            ))
                            .await;
                    }
                }
                Err(error) => {
                    stream_error = Some(error.to_string());
                    let mut payload = OrderedMap::new();
                    payload.insert("error", json!(error.to_string()));
                    payload.insert("model", json!(model_id));
                    let _ = sink
                        .send(data_frame(
                            &serde_json::to_value(payload).unwrap_or(Value::Null),
                        ))
                        .await;
                }
            }
        } else {
            stream_error = Some("no worker".into());
        }

        let mut persisted = full.clone();
        if let Some(error) = stream_error.as_ref() {
            persisted = if persisted.is_empty() {
                format!("[stream_error] {error}")
            } else {
                format!("{persisted}\n\n[stream_error] {error}")
            };
        }
        let grounding = assess_answer_grounding(&full, Some(&trace_seed), Some(&context_quality));
        if let Some(object) = trace_seed.as_object_mut() {
            object.insert("grounding".into(), grounding.clone());
        }
        let meta = HistoryMeta {
            email: hist_email.as_deref(),
            nickname: hist_nick.as_deref(),
            source: req.source.as_deref().or(Some("web")),
            conversation_id: req.conversation_id.as_deref(),
            workspace_id: workspace_id.as_deref(),
        };
        let record = persist_answer(
            &state,
            &req,
            &persisted,
            &trace_seed,
            effective_email.as_deref(),
            workspace_id.as_deref(),
            &meta,
        )
        .await;
        let mut trailer = OrderedMap::new();
        trailer.insert("chunk", json!(""));
        trailer.insert("model", json!(model_id));
        if let Some(id) = record.get("id") {
            trailer.insert("trace_id", id.clone());
            trailer.insert("trace", record);
        }
        trailer.insert("context_quality", context_quality);
        trailer.insert("grounding", grounding);
        if let Some(error) = stream_error {
            trailer.insert("error", json!(error));
        }
        let _ = sink
            .send(data_frame(
                &serde_json::to_value(trailer).unwrap_or(Value::Null),
            ))
            .await;
        let _ = sink.send(DONE).await;
    });
    stream_response(Body::from_stream(stream), &[("X-Model", &header_model)])
}

pub(crate) async fn next_chunk<S>(
    stream: &mut std::pin::Pin<&mut S>,
) -> Option<Result<bytes::Bytes, reqwest::Error>>
where
    S: futures_core::Stream<Item = Result<bytes::Bytes, reqwest::Error>>,
{
    use futures_core::Stream;
    std::future::poll_fn(|cx| Stream::poll_next(stream.as_mut(), cx)).await
}

#[allow(clippy::too_many_arguments)]
pub(crate) async fn collect_completion(
    worker: Option<&ChatWorker>,
    model_id: Option<&str>,
    message: &str,
    context: &str,
    max_tokens: i64,
    temperature: f64,
    image_data: Option<&str>,
    document: bool,
) -> (String, Option<String>) {
    let Some(worker) = worker else {
        return (String::new(), Some("no worker".into()));
    };
    let upstream = if document {
        worker
            .document_stream(model_id, message, context, max_tokens, temperature)
            .await
    } else {
        worker
            .llm_stream(
                model_id,
                message,
                context,
                max_tokens,
                temperature,
                image_data,
            )
            .await
    };
    let upstream = match upstream {
        Ok(upstream) => upstream,
        Err(error) => return (String::new(), Some(error.to_string())),
    };
    let mut reader = FrameReader::new();
    let mut full = String::new();
    let mut error = None;
    let mut bytes = std::pin::pin!(upstream.into_byte_stream());
    while let Some(chunk) = next_chunk(&mut bytes).await {
        let Ok(chunk) = chunk else { break };
        for frame in reader.push(&chunk) {
            match frame {
                LlmFrame::Text(text) => full.push_str(&text),
                LlmFrame::Error(detail) => error = Some(detail),
                LlmFrame::Done => {}
            }
        }
    }
    if let Some(LlmFrame::Text(text)) = reader.finish() {
        full.push_str(&text);
    }
    (full, error)
}
