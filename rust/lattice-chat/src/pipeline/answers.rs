use std::collections::BTreeSet;

use axum::body::Body;
use axum::http::StatusCode;
use axum::response::Response;
use lattice_auth::OrderedMap;
use lattice_core::embeddings::LocalEmbeddingModel;
use serde_json::{json, Value};

use crate::boundary::{resolve_hybrid_policy, strip_cloud_prefix, EscalationReason, NetworkMode};
use crate::cloud::{
    budget_for, build_minimal_context, cloud_egress_event, ingest_expansion,
    plan_kg_expansion_rich, record_budget, scope_key, CloudProvider, CloudTurnResult,
    MinimalContext,
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

#[allow(clippy::too_many_arguments)]
pub(crate) async fn hybrid_response(
    state: &ChatState,
    req: &ChatRequest,
    mode: NetworkMode,
    provider: CloudProvider,
    reason: EscalationReason,
    effective_email: Option<&str>,
    workspace_id: Option<&str>,
    meta: &HistoryMeta<'_>,
) -> Option<Response> {
    let policy = resolve_hybrid_policy(&state.config.data_dir, effective_email, workspace_id);
    let _ = policy;
    let question = strip_cloud_prefix(&req.message).to_string();
    let allowed = workspace_id
        .filter(|id| !id.is_empty())
        .map(|id| [id.to_string()].into_iter().collect::<BTreeSet<_>>());
    let minimal = if let Some(conn) = state.read_conn() {
        build_minimal_context(
            &conn,
            &LocalEmbeddingModel::default(),
            &question,
            mode,
            6,
            allowed.as_ref(),
            now_secs(),
        )
    } else {
        MinimalContext {
            query: question.clone(),
            keywords: crate::cloud::extract_keywords(&question, 12),
            quality: json!({
                "mode": "none", "nodes": 0, "limited": true,
                "reason": "no store or empty query",
            }),
            ..Default::default()
        }
    };
    let key = scope_key(effective_email, workspace_id);
    let budget = budget_for(&key);
    let composed = crate::cloud::compose_prompt(
        crate::cloud::HYBRID_SYSTEM_PROMPT,
        &question,
        &minimal.compact_text,
    );
    let token_estimate = crate::cloud::rough_token_estimate(&composed).max(minimal.token_estimate);
    if let Some(refusal) = budget.check_turn(token_estimate) {
        if let Some(egress) = state.egress.as_ref() {
            egress.record(&cloud_egress_event(
                &minimal.node_ids,
                token_estimate,
                mode,
                "(refused)",
                Some(provider.model()),
                effective_email,
                workspace_id,
                "refused_token_guard",
                Some(&refusal),
                Some(reason.as_str()),
            ));
        }
        if !req.stream {
            return Some(json_body(
                StatusCode::BAD_REQUEST,
                &json!({"detail": format!("cloud token guard: {refusal}")}),
            ));
        }
        let (sink, stream) = frame_channel();
        let mut payload = OrderedMap::new();
        payload.insert("type", json!("error"));
        payload.insert("detail", json!(format!("cloud token guard: {refusal}")));
        let _ = sink
            .send(data_frame(
                &serde_json::to_value(payload).unwrap_or(Value::Null),
            ))
            .await;
        let _ = sink.send(DONE).await;
        return Some(stream_response(
            Body::from_stream(stream),
            &[
                ("X-Model", provider.model()),
                ("X-Network-Mode", mode.as_str()),
                ("X-Hybrid", "1"),
            ],
        ));
    }

    if !req.stream {
        return Some(
            hybrid_json(
                state,
                req,
                mode,
                provider,
                reason,
                minimal,
                &key,
                &question,
                effective_email,
                workspace_id,
                meta,
            )
            .await,
        );
    }

    let (sink, stream) = frame_channel();
    let header_model = provider.model().to_string();
    let state = state.clone();
    let req = req.clone();
    let email = effective_email.map(str::to_string);
    let workspace = workspace_id.map(str::to_string);
    let hist_email = meta.email.map(str::to_string);
    let hist_nick = meta.nickname.map(str::to_string);
    let source = meta.source.map(str::to_string);
    let conversation = req.conversation_id.clone();
    tokio::spawn(async move {
        let context_frame = hybrid_context_frame(&minimal, &budget, reason);
        if !sink
            .send(data_frame(
                &serde_json::to_value(context_frame).unwrap_or(Value::Null),
            ))
            .await
        {
            return;
        }
        let cloud_model = provider.model().to_string();
        let cloud_name = provider.name().to_string();
        record_sent_egress(
            &state,
            &minimal,
            mode,
            &cloud_name,
            &cloud_model,
            email.as_deref(),
            workspace.as_deref(),
            reason,
        );
        let mut answer = String::new();
        let (piece_tx, mut piece_rx) = tokio::sync::mpsc::unbounded_channel::<String>();
        let mut on_piece = move |piece: &str| piece_tx.send(piece.to_string()).is_ok();
        let call = provider.stream(
            crate::cloud::HYBRID_SYSTEM_PROMPT,
            &question,
            &minimal.compact_text,
            &mut on_piece,
        );
        tokio::pin!(call);
        let result = loop {
            tokio::select! {
                piece = piece_rx.recv() => {
                    let Some(piece) = piece else { continue };
                    if piece.is_empty() {
                        continue;
                    }
                    answer.push_str(&piece);
                    let mut token = OrderedMap::new();
                    token.insert("type", json!("token"));
                    token.insert("text", json!(piece.clone()));
                    token.insert("chunk", json!(piece));
                    token.insert("model", json!(cloud_model.clone()));
                    if !sink
                        .send(data_frame(
                            &serde_json::to_value(token).unwrap_or(Value::Null),
                        ))
                        .await
                    {
                        return;
                    }
                }
                done = &mut call => {
                    while let Ok(piece) = piece_rx.try_recv() {
                        if piece.is_empty() {
                            continue;
                        }
                        answer.push_str(&piece);
                        let mut token = OrderedMap::new();
                        token.insert("type", json!("token"));
                        token.insert("text", json!(piece.clone()));
                        token.insert("chunk", json!(piece));
                        token.insert("model", json!(cloud_model.clone()));
                        let _ = sink
                            .send(data_frame(
                                &serde_json::to_value(token).unwrap_or(Value::Null),
                            ))
                            .await;
                    }
                    break done;
                }
            }
        };
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
        let ingest = finish_hybrid_turn(
            &state,
            &req,
            &minimal,
            &answer,
            &cloud_name,
            &cloud_model,
            &key,
            email.as_deref(),
            workspace.as_deref(),
            hist_email.as_deref(),
            hist_nick.as_deref(),
            source.as_deref(),
            conversation.as_deref(),
        )
        .await;
        let mut done = OrderedMap::new();
        done.insert("type", json!("hybrid_done"));
        done.insert("chunk", json!(""));
        done.insert("answer", json!(answer));
        done.insert("sent_node_ids", json!(minimal.node_ids));
        done.insert("provider", json!(cloud_name));
        done.insert("model", json!(cloud_model));
        done.insert("kg_expansion", ingest);
        done.insert("token_estimate", json!(minimal.token_estimate));
        done.insert("token_budget", budget_for(&key).snapshot());
        done.insert("reason", json!(reason.as_str()));
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

fn hybrid_context_frame(
    minimal: &MinimalContext,
    budget: &crate::cloud::TokenBudget,
    reason: EscalationReason,
) -> OrderedMap {
    let mut context_frame = OrderedMap::new();
    context_frame.insert("type", json!("hybrid_context"));
    context_frame.insert("node_ids", json!(minimal.node_ids));
    context_frame.insert("keywords", json!(minimal.keywords));
    context_frame.insert("token_estimate", json!(minimal.token_estimate));
    context_frame.insert("quality", minimal.quality.clone());
    context_frame.insert("titles", json!(minimal.titles()));
    context_frame.insert("token_budget", budget.snapshot());
    context_frame.insert("reason", json!(reason.as_str()));
    context_frame
}

#[allow(clippy::too_many_arguments)]
fn record_sent_egress(
    state: &ChatState,
    minimal: &MinimalContext,
    mode: NetworkMode,
    provider: &str,
    model: &str,
    email: Option<&str>,
    workspace: Option<&str>,
    reason: EscalationReason,
) {
    if let Some(egress) = state.egress.as_ref() {
        egress.record(&cloud_egress_event(
            &minimal.node_ids,
            minimal.token_estimate,
            mode,
            provider,
            Some(model),
            email,
            workspace,
            "sent",
            None,
            Some(reason.as_str()),
        ));
    }
}

#[allow(clippy::too_many_arguments)]
async fn finish_hybrid_turn(
    state: &ChatState,
    req: &ChatRequest,
    minimal: &MinimalContext,
    answer: &str,
    provider: &str,
    model: &str,
    key: &str,
    email: Option<&str>,
    workspace: Option<&str>,
    hist_email: Option<&str>,
    hist_nick: Option<&str>,
    source: Option<&str>,
    conversation: Option<&str>,
) -> Value {
    let turn = CloudTurnResult {
        user_message: req.message.clone(),
        answer_text: answer.to_string(),
        sent_node_ids: minimal.node_ids.clone(),
        provider: provider.to_string(),
        model: model.to_string(),
    };
    let plan = plan_kg_expansion_rich(&turn);
    let ingest = ingest_expansion(&plan, state.review.as_deref(), email, workspace);
    let used = minimal.token_estimate + (answer.chars().count() as i64 / 4).max(1);
    let _ = record_budget(key, used);
    let meta = HistoryMeta {
        email: hist_email,
        nickname: hist_nick,
        source,
        conversation_id: conversation,
        workspace_id: workspace,
    };
    persist_entry(state, "assistant", answer, &meta).await;
    state.notify("assistant", answer, source);
    ingest
}

#[allow(clippy::too_many_arguments)]
async fn hybrid_json(
    state: &ChatState,
    req: &ChatRequest,
    mode: NetworkMode,
    provider: CloudProvider,
    reason: EscalationReason,
    minimal: MinimalContext,
    key: &str,
    question: &str,
    email: Option<&str>,
    workspace: Option<&str>,
    meta: &HistoryMeta<'_>,
) -> Response {
    record_sent_egress(
        state,
        &minimal,
        mode,
        provider.name(),
        provider.model(),
        email,
        workspace,
        reason,
    );
    let mut answer = String::new();
    let result = provider
        .stream(
            crate::cloud::HYBRID_SYSTEM_PROMPT,
            question,
            &minimal.compact_text,
            &mut |piece| {
                answer.push_str(piece);
                true
            },
        )
        .await;
    if let Err(error) = result {
        return json_body(
            StatusCode::BAD_GATEWAY,
            &json!({"detail": error, "error": error}),
        );
    }
    let ingest = finish_hybrid_turn(
        state,
        req,
        &minimal,
        &answer,
        provider.name(),
        provider.model(),
        key,
        email,
        workspace,
        meta.email,
        meta.nickname,
        meta.source,
        meta.conversation_id,
    )
    .await;
    let mut body = OrderedMap::new();
    body.insert("response", json!(answer.clone()));
    body.insert("answer", json!(answer));
    body.insert("provider", json!(provider.name()));
    body.insert("model", json!(provider.model()));
    body.insert("reason", json!(reason.as_str()));
    body.insert("sent_node_ids", json!(minimal.node_ids));
    body.insert("kg_expansion", ingest);
    body.insert("token_estimate", json!(minimal.token_estimate));
    body.insert("token_budget", budget_for(key).snapshot());
    json_body(
        StatusCode::OK,
        &serde_json::to_value(body).unwrap_or(Value::Null),
    )
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
