use std::collections::BTreeSet;

use axum::body::Body;
use axum::http::StatusCode;
use axum::response::Response;
use lattice_auth::OrderedMap;
use lattice_core::embeddings::LocalEmbeddingModel;
use serde_json::{json, Value};

use crate::boundary::{resolve_hybrid_policy, NetworkMode};
use crate::cloud::{
    budget_for, build_minimal_context, cloud_egress_event, ingest_expansion,
    plan_kg_expansion_rich, record_budget, scope_key, CloudTurnResult, OpenAiCompatibleAdapter,
};
use crate::contracts::ChatRequest;
use crate::documents::DocumentPreparation;
use crate::graph::trace_record;
use crate::history::json_body;
use crate::intents::{persist_entry, HistoryMeta};
use crate::sse::{data_frame, stream_response, DONE};
use crate::state::ChatState;
use crate::stream::frame_channel;

use super::model::collect_completion;
use super::{now_secs, utc_now};

#[allow(clippy::too_many_arguments)]
pub(crate) async fn document_response(
    state: &ChatState,
    req: &ChatRequest,
    prepared: &DocumentPreparation,
    model_id: &str,
    effective_email: Option<&str>,
    workspace_id: Option<&str>,
    meta: &HistoryMeta<'_>,
    trace_seed: &Value,
) -> Option<Response> {
    if !(prepared.is_document && state.config.enable_graph()) {
        return None;
    }
    let graph_markdown = prepared.graph_markdown();
    let system = state.document_sessions.system_prompt(
        effective_email,
        workspace_id,
        req.conversation_id.as_deref(),
        &graph_markdown,
    );
    let footnote = lattice_retrieval::format_sources_footnote(&prepared.sources());
    let mut seed = trace_seed.clone();
    if let Some(object) = seed.as_object_mut() {
        if let Some(quality) = prepared.context_quality() {
            object.insert("context_quality".into(), quality.clone());
        }
        if let Some(trace) = prepared.assembly_trace() {
            object.insert("context_assembly".into(), trace.clone());
        }
    }
    if req.stream {
        let (sink, stream) = frame_channel();
        let state = state.clone();
        let req = req.clone();
        let model_id = model_id.to_string();
        let header_model = model_id.clone();
        let system = system.clone();
        let footnote = footnote.clone();
        let email = effective_email.map(str::to_string);
        let workspace = workspace_id.map(str::to_string);
        let conversation = req.conversation_id.clone();
        let hist_email = meta.email.map(str::to_string);
        let hist_nick = meta.nickname.map(str::to_string);
        let source = meta.source.map(str::to_string);
        tokio::spawn(async move {
            let max_tokens = if req.max_tokens == 0 {
                8192
            } else {
                req.max_tokens
            };
            let temperature = if req.temperature == 0.0 {
                0.3
            } else {
                req.temperature
            };
            let (mut full, error) = collect_completion(
                state.worker.as_ref(),
                Some(&model_id),
                &req.message,
                &system,
                max_tokens,
                temperature,
                None,
                true,
            )
            .await;
            if !full.is_empty() {
                let mut payload = OrderedMap::new();
                payload.insert("text", json!(full.clone()));
                let _ = sink
                    .send(data_frame(
                        &serde_json::to_value(payload).unwrap_or(Value::Null),
                    ))
                    .await;
            }
            if let Some(error) = error.as_ref() {
                let mut payload = OrderedMap::new();
                payload.insert("error", json!(error));
                let _ = sink
                    .send(data_frame(
                        &serde_json::to_value(payload).unwrap_or(Value::Null),
                    ))
                    .await;
            }
            if !footnote.is_empty() {
                let mut payload = OrderedMap::new();
                payload.insert("text", json!(footnote.clone()));
                let _ = sink
                    .send(data_frame(
                        &serde_json::to_value(payload).unwrap_or(Value::Null),
                    ))
                    .await;
                full.push_str(&footnote);
            }
            if let Some(error) = error.as_ref() {
                full = if full.is_empty() {
                    format!("[stream_error] {error}")
                } else {
                    format!("{full}\n\n[stream_error] {error}")
                };
            }
            state.document_sessions.update(
                email.as_deref(),
                workspace.as_deref(),
                conversation.as_deref(),
                &graph_markdown,
                &full,
            );
            let meta = HistoryMeta {
                email: hist_email.as_deref(),
                nickname: hist_nick.as_deref(),
                source: source.as_deref(),
                conversation_id: conversation.as_deref(),
                workspace_id: workspace.as_deref(),
            };
            let record = persist_answer(
                &state,
                &req,
                &full,
                &seed,
                email.as_deref(),
                workspace.as_deref(),
                &meta,
            )
            .await;
            let mut trailer = OrderedMap::new();
            trailer.insert("text", json!(""));
            trailer.insert("trace_id", record.get("id").cloned().unwrap_or(Value::Null));
            trailer.insert("trace", record);
            let _ = sink
                .send(data_frame(
                    &serde_json::to_value(trailer).unwrap_or(Value::Null),
                ))
                .await;
            let _ = sink.send(DONE).await;
        });
        return Some(stream_response(
            Body::from_stream(stream),
            &[("X-Model", &header_model), ("X-Doc-Gen", "true")],
        ));
    }
    let max_tokens = if req.max_tokens == 0 {
        8192
    } else {
        req.max_tokens
    };
    let temperature = if req.temperature == 0.0 {
        0.3
    } else {
        req.temperature
    };
    let (mut result, _) = collect_completion(
        state.worker.as_ref(),
        Some(model_id),
        &req.message,
        &system,
        max_tokens,
        temperature,
        None,
        true,
    )
    .await;
    if !footnote.is_empty() {
        result.push_str(&footnote);
    }
    state.document_sessions.update(
        effective_email,
        workspace_id,
        req.conversation_id.as_deref(),
        &graph_markdown,
        &result,
    );
    let record = persist_answer(
        state,
        req,
        &result,
        &seed,
        effective_email,
        workspace_id,
        meta,
    )
    .await;
    let mut body = OrderedMap::new();
    body.insert("response", json!(result));
    body.insert("trace_id", record.get("id").cloned().unwrap_or(Value::Null));
    body.insert("trace", record);
    if let Some(quality) = prepared.context_quality() {
        body.insert("context_quality", quality.clone());
    }
    Some(json_body(
        StatusCode::OK,
        &serde_json::to_value(body).unwrap_or(Value::Null),
    ))
}

pub(crate) async fn hybrid_response(
    state: &ChatState,
    req: &ChatRequest,
    mode: NetworkMode,
    model_id: &str,
    effective_email: Option<&str>,
    workspace_id: Option<&str>,
    meta: &HistoryMeta<'_>,
) -> Option<Response> {
    let conn = state.read_conn()?;
    let policy = resolve_hybrid_policy(&state.config.data_dir, effective_email, workspace_id);
    let (sink, stream) = frame_channel();
    let state = state.clone();
    let req = req.clone();
    let model_id = model_id.to_string();
    let header_model = model_id.clone();
    let email = effective_email.map(str::to_string);
    let workspace = workspace_id.map(str::to_string);
    let hist_email = meta.email.map(str::to_string);
    let hist_nick = meta.nickname.map(str::to_string);
    let source = meta.source.map(str::to_string);
    let conversation = req.conversation_id.clone();
    tokio::spawn(async move {
        let allowed = workspace
            .as_deref()
            .filter(|id| !id.is_empty())
            .map(|id| [id.to_string()].into_iter().collect::<BTreeSet<_>>());
        let minimal = build_minimal_context(
            &conn,
            &LocalEmbeddingModel::default(),
            &req.message,
            mode,
            6,
            allowed.as_ref(),
            now_secs(),
        );
        let key = scope_key(email.as_deref(), workspace.as_deref());
        let budget = budget_for(&key);
        if let Some(refusal) = budget.check_turn(minimal.token_estimate) {
            if let Some(egress) = state.egress.as_ref() {
                egress.record(&cloud_egress_event(
                    &minimal.node_ids,
                    minimal.token_estimate,
                    mode,
                    "(refused)",
                    Some(&model_id),
                    email.as_deref(),
                    workspace.as_deref(),
                    "refused_token_guard",
                    Some(&refusal),
                ));
            }
            let mut payload = OrderedMap::new();
            payload.insert("type", json!("error"));
            payload.insert("detail", json!(format!("cloud token guard: {refusal}")));
            let _ = sink
                .send(data_frame(
                    &serde_json::to_value(payload).unwrap_or(Value::Null),
                ))
                .await;
            let _ = sink.send(DONE).await;
            return;
        }
        let mut context_frame = OrderedMap::new();
        context_frame.insert("type", json!("hybrid_context"));
        context_frame.insert("node_ids", json!(minimal.node_ids));
        context_frame.insert("keywords", json!(minimal.keywords));
        context_frame.insert("token_estimate", json!(minimal.token_estimate));
        context_frame.insert("quality", minimal.quality.clone());
        context_frame.insert("titles", json!(minimal.titles()));
        context_frame.insert("token_budget", budget.snapshot());
        let _ = sink
            .send(data_frame(
                &serde_json::to_value(context_frame).unwrap_or(Value::Null),
            ))
            .await;

        let adapter = OpenAiCompatibleAdapter::from_env(reqwest::Client::new());
        if let Some(egress) = state.egress.as_ref() {
            egress.record(&cloud_egress_event(
                &minimal.node_ids,
                minimal.token_estimate,
                mode,
                OpenAiCompatibleAdapter::PROVIDER_NAME,
                Some(&model_id),
                email.as_deref(),
                workspace.as_deref(),
                "sent",
                None,
            ));
        }
        let mut answer = String::new();
        let mut on_piece = |piece: &str| {
            answer.push_str(piece);
            true
        };
        let result = adapter
            .stream(
                crate::cloud::HYBRID_SYSTEM_PROMPT,
                &req.message,
                &minimal.compact_text,
                Some(&model_id),
                &mut on_piece,
            )
            .await;
        if !answer.is_empty() {
            let mut token = OrderedMap::new();
            token.insert("type", json!("token"));
            token.insert("text", json!(answer.clone()));
            token.insert("chunk", json!(answer.clone()));
            token.insert("model", json!(model_id));
            let _ = sink
                .send(data_frame(
                    &serde_json::to_value(token).unwrap_or(Value::Null),
                ))
                .await;
        }
        if let Err(error) = result {
            let mut payload = OrderedMap::new();
            payload.insert("type", json!("error"));
            payload.insert("detail", json!(error.clone()));
            payload.insert("error", json!(error));
            let _ = sink
                .send(data_frame(
                    &serde_json::to_value(payload).unwrap_or(Value::Null),
                ))
                .await;
            let _ = sink.send(DONE).await;
            return;
        }
        let turn = CloudTurnResult {
            user_message: req.message.clone(),
            answer_text: answer.clone(),
            sent_node_ids: minimal.node_ids.clone(),
            provider: OpenAiCompatibleAdapter::PROVIDER_NAME.to_string(),
            model: model_id.clone(),
        };
        let plan = plan_kg_expansion_rich(&turn);
        let ingest = ingest_expansion(
            &plan,
            state.review.as_deref(),
            email.as_deref(),
            workspace.as_deref(),
        );
        let used = minimal.token_estimate + (answer.chars().count() as i64 / 4).max(1);
        let snapshot = record_budget(&key, used).snapshot();
        let meta = HistoryMeta {
            email: hist_email.as_deref(),
            nickname: hist_nick.as_deref(),
            source: source.as_deref(),
            conversation_id: conversation.as_deref(),
            workspace_id: workspace.as_deref(),
        };
        persist_entry(&state, "assistant", &answer, &meta).await;
        state.notify("assistant", &answer, source.as_deref());
        let _ = policy;
        let mut done = OrderedMap::new();
        done.insert("type", json!("hybrid_done"));
        done.insert("chunk", json!(""));
        done.insert("answer", json!(answer));
        done.insert("sent_node_ids", json!(turn.sent_node_ids));
        done.insert("provider", json!(turn.provider));
        done.insert("model", json!(turn.model));
        done.insert("kg_expansion", ingest);
        done.insert("token_estimate", json!(minimal.token_estimate));
        done.insert("token_budget", snapshot);
        let _ = sink
            .send(data_frame(
                &serde_json::to_value(done).unwrap_or(Value::Null),
            ))
            .await;
        let _ = sink.send(DONE).await;
    });
    Some(stream_response(
        Body::from_stream(stream),
        &[
            ("X-Model", &header_model),
            ("X-Network-Mode", mode.as_str()),
            ("X-Hybrid", "1"),
        ],
    ))
}

pub(crate) async fn persist_answer(
    state: &ChatState,
    req: &ChatRequest,
    response: &str,
    trace: &Value,
    effective_email: Option<&str>,
    workspace_id: Option<&str>,
    meta: &HistoryMeta<'_>,
) -> Value {
    persist_entry(state, "assistant", response, meta).await;
    let record = trace_record(
        &req.message,
        response,
        req.conversation_id.as_deref(),
        effective_email,
        workspace_id,
        &utc_now(),
        trace,
    );
    let stored = if let Some(sink) = state.traces.as_ref() {
        sink.record(&record)
    } else {
        record.clone()
    };
    state.notify("assistant", response, req.source.as_deref());
    stored
}
