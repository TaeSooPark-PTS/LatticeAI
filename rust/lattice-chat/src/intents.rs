use std::path::{Path, PathBuf};

use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use fancy_regex::Regex;
use lattice_agent::parse::inference::{infer_file_target, infer_project_manifest};
use lattice_agent::tools::sandbox::Workspace;
use lattice_auth::OrderedMap;
use lattice_core::graph_write::types::{ExtractReply, IngestionRecord, SuppliedVector};
use lattice_core::messages::{self, LANGUAGE_HEADER};
use serde_json::{json, Map, Value};

use crate::contracts::ChatRequest;
use crate::helpers::{file_action_target, format_network_status};
use crate::history::{clear_all, clear_conversation, history_scope, json_body};
use crate::sse::{single_text_stream, stream_response};
use crate::state::ChatState;

pub(crate) const PREVIEWABLE: &[&str] = &[
    ".css",
    ".csv",
    ".htm",
    ".html",
    ".js",
    ".json",
    ".jsx",
    ".markdown",
    ".md",
    ".py",
    ".sh",
    ".sql",
    ".svelte",
    ".ts",
    ".tsx",
    ".txt",
    ".vue",
    ".xml",
    ".yaml",
    ".yml",
];

pub fn no_model_response(state: &ChatState, headers: &HeaderMap) -> Response {
    let lang = language_of(headers);
    let detail = if state.config.is_public {
        messages::text(
            "models.public_model_missing",
            lang,
            &[("model", &state.config.public_model)],
        )
    } else {
        messages::text("chat.no_model_loaded", lang, &[])
    };
    let mut body = OrderedMap::new();
    body.insert("error", json!("no_model_loaded"));
    body.insert("detail", json!(detail));
    body.insert("message", json!(detail));
    body.insert("action", json!("load_model"));
    json_body(
        StatusCode::BAD_REQUEST,
        &serde_json::to_value(body).unwrap_or(Value::Null),
    )
}

pub fn single_answer_response(stream: bool, answer: &str, model: &str) -> Response {
    if stream {
        return stream_response(single_text_stream(answer, model), &[("X-Model", model)]);
    }
    let mut body = OrderedMap::new();
    body.insert("response", json!(answer));
    json_body(
        StatusCode::OK,
        &serde_json::to_value(body).unwrap_or(Value::Null),
    )
}

pub(crate) fn language_of(headers: &HeaderMap) -> &'static str {
    messages::resolve_language(
        headers
            .get(LANGUAGE_HEADER)
            .and_then(|value| value.to_str().ok()),
        headers
            .get(axum::http::header::ACCEPT_LANGUAGE)
            .and_then(|value| value.to_str().ok()),
    )
}

pub fn network_status_info() -> Map<String, Value> {
    let mut info = Map::new();
    let hostname = hostname();
    info.insert("hostname".into(), json!(hostname));
    info.insert("local_ip".into(), json!(local_ip()));
    info.insert("public_ip".into(), Value::Null);
    info.insert("local_ips".into(), json!({}));
    info
}

fn hostname() -> String {
    hostname_from_env()
        .or_else(hostname_from_cmd)
        .unwrap_or_else(|| "unknown".into())
}

fn hostname_from_env() -> Option<String> {
    std::env::var("HOSTNAME")
        .ok()
        .filter(|value| !value.is_empty())
}

fn hostname_from_cmd() -> Option<String> {
    let output = std::process::Command::new("hostname").output().ok()?;
    let text = String::from_utf8_lossy(&output.stdout).trim().to_string();
    (!text.is_empty()).then_some(text)
}

fn local_ip() -> Option<String> {
    let socket = std::net::UdpSocket::bind("0.0.0.0:0").ok()?;
    socket.connect("1.1.1.1:80").ok()?;
    Some(socket.local_addr().ok()?.ip().to_string())
}

pub async fn network(
    state: &ChatState,
    req: &ChatRequest,
    headers: &HeaderMap,
    history_meta: &HistoryMeta<'_>,
) -> Response {
    let _ = headers;
    let answer = format_network_status(&network_status_info());
    persist_exchange(state, req, &req.message, &answer, history_meta).await;
    single_answer_response(req.stream, &answer, "network_status")
}

pub struct HistoryMeta<'a> {
    pub email: Option<&'a str>,
    pub nickname: Option<&'a str>,
    pub source: Option<&'a str>,
    pub conversation_id: Option<&'a str>,
    pub workspace_id: Option<&'a str>,
}

pub async fn clear(
    state: &ChatState,
    req: &ChatRequest,
    headers: &HeaderMap,
    effective_email: Option<&str>,
    workspace_id: Option<&str>,
) -> Response {
    let command = req.message.trim().to_lowercase();
    let clear_scope = if command == "/clear_all" {
        "all"
    } else {
        "conversation"
    };
    if state.config.enable_graph() {
        if let Some(graph) = state.graph.clone() {
            let mut metadata = Map::new();
            metadata.insert("command".into(), json!(command));
            metadata.insert("scope".into(), json!(clear_scope));
            let request = lattice_core::graph_write::types::IngestEventRequest {
                event_type: "ClearEvent".into(),
                title: format!("{command} requested"),
                user_email: effective_email.map(str::to_string),
                user_nickname: req.user_nickname.clone(),
                source: Some(req.source.clone().unwrap_or_else(|| "web".into())),
                conversation_id: req.conversation_id.clone(),
                workspace_id: workspace_id.map(str::to_string),
                metadata,
            };
            let _ = tokio::task::spawn_blocking(move || graph.ingest_event(&request)).await;
        }
    }
    let identity_email = effective_email.unwrap_or("");
    let scope = history_scope(state, identity_email);
    let result = match state.write_conn() {
        Some(conn) => {
            if command == "/clear_all" || req.conversation_id.is_none() {
                clear_all(&conn, 0, &scope)
            } else {
                clear_conversation(
                    &conn,
                    req.conversation_id.as_deref().unwrap_or(""),
                    None,
                    &scope,
                )
            }
        }
        None => json!({"status": "cleared", "removed": 0, "kept": 0}),
    };
    let prefix = if command != "/clear_all" && req.conversation_id.is_some() {
        "현재 대화방 채팅창을 정리했습니다."
    } else {
        "채팅창을 정리했습니다."
    };
    let removed = result.get("removed").and_then(Value::as_i64).unwrap_or(0);
    let kept = result.get("kept").and_then(Value::as_i64).unwrap_or(0);
    let answer = format!(
        "{prefix} 화면에서 제거 {removed}개. 감사 로그와 지식 그래프/RAG 데이터는 유지됩니다."
    );
    state.audit(
        "clear_command",
        &json!({
            "user_email": effective_email,
            "user_nickname": req.user_nickname,
            "source": req.source.as_deref().unwrap_or("web"),
            "conversation_id": req.conversation_id,
            "command": command,
            "scope": clear_scope,
            "removed": removed,
            "kept": kept,
        }),
    );
    state.notify("user", &req.message, req.source.as_deref());
    state.notify("assistant", &answer, req.source.as_deref());
    let _ = headers;
    single_answer_response(req.stream, &answer, "history")
}

pub async fn current_url(
    state: &ChatState,
    req: &ChatRequest,
    history_meta: &HistoryMeta<'_>,
) -> Response {
    let answer = format!(
        "현재 페이지 URL: {}",
        req.client_url.as_deref().unwrap_or("")
    );
    persist_exchange(state, req, &req.message, &answer, history_meta).await;
    single_answer_response(req.stream, &answer, "client_url")
}

pub fn next_available_path(root: &Path, target: &str) -> Result<String, String> {
    let candidate = root.join(target);
    if !candidate.exists() {
        return Ok(target.to_string());
    }
    let stem = candidate
        .file_stem()
        .and_then(|value| value.to_str())
        .unwrap_or(target);
    let suffix = candidate
        .extension()
        .and_then(|value| value.to_str())
        .map(|ext| format!(".{ext}"))
        .unwrap_or_default();
    let numbered = Regex::new(r"_\d+$").expect("ported pattern must compile");
    let base = numbered.replace(stem, "").into_owned();
    let base = if base.is_empty() {
        stem.to_string()
    } else {
        base
    };
    for index in 2..100 {
        let name = format!("{base}_{index}{suffix}");
        if !candidate.with_file_name(&name).exists() {
            let parent = Path::new(target)
                .parent()
                .filter(|parent| !parent.as_os_str().is_empty())
                .map(|parent| parent.join(&name))
                .unwrap_or_else(|| PathBuf::from(&name));
            return Ok(parent.to_string_lossy().into_owned());
        }
    }
    Err(target.to_string())
}

pub(crate) fn previewable(path: &str) -> bool {
    Path::new(path)
        .extension()
        .and_then(|ext| ext.to_str())
        .map(|ext| {
            let dotted = format!(".{}", ext.to_lowercase());
            PREVIEWABLE.binary_search(&dotted.as_str()).is_ok()
        })
        .unwrap_or(false)
}

pub(crate) fn write_bytes(
    root: &Path,
    rel: &str,
    content: &[u8],
) -> Result<(String, usize), String> {
    let workspace = Workspace::new(root).map_err(|error| error.to_string())?;
    let path = workspace.resolve(rel).map_err(|error| error.message)?;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    std::fs::write(&path, content).map_err(|error| error.to_string())?;
    Ok((workspace.relative(&path), content.len()))
}

pub fn scoped_file_id(content: &str, workspace_id: Option<&str>) -> String {
    let digest = lattice_core::graph_write::pyaux::sha256_text(content);
    let identity = match workspace_id.filter(|id| !id.is_empty()) {
        Some(workspace) => format!("{workspace}|{digest}"),
        None => digest,
    };
    let hashed = lattice_core::graph_write::pyaux::sha256_text(&identity);
    format!("file:{}", &hashed[..24.min(hashed.len())])
}

pub async fn direct_file_action(
    state: &ChatState,
    req: &ChatRequest,
    headers: &HeaderMap,
    model_id: Option<&str>,
    effective_email: Option<&str>,
    workspace_id: Option<&str>,
) -> Option<Response> {
    let explicit = file_action_target(&req.message);
    if explicit.is_none() {
        if let Some(manifest) = infer_project_manifest(&req.message) {
            let Some(model_id) = model_id else {
                return Some(no_model_response(state, headers));
            };
            return Some(
                crate::filegen::project_action(
                    state,
                    req,
                    headers,
                    model_id,
                    effective_email,
                    workspace_id,
                    manifest,
                )
                .await,
            );
        }
    }
    let target_path = explicit.or_else(|| infer_file_target(&req.message))?;
    Some(
        crate::filegen::file_action(
            state,
            req,
            headers,
            model_id,
            effective_email,
            workspace_id,
            &target_path,
        )
        .await,
    )
}

const EXTRACT_PATH: &str = "/worker/extract";

pub(crate) async fn ingest_generated(
    state: &ChatState,
    rel_path: &str,
    content: &str,
    effective_email: Option<&str>,
    workspace_id: Option<&str>,
    conversation_id: Option<&str>,
) -> Option<Value> {
    if !state.config.ingest_generated {
        return None;
    }
    let graph = state.graph.clone()?;
    let filename = Path::new(rel_path)
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or(rel_path)
        .to_string();
    let enrichment = format!("{filename}\n{content}");
    let extracted = extract_for_file(state, &enrichment).await;
    let embedding = embed_for_file(state, &enrichment).await;
    let native_agrees = match embedding.as_ref() {
        Some(vector) => {
            vector.model_id == graph.embedder().model_id() && vector.dim == graph.embedder().dim()
        }
        None => true,
    };

    let mut extracted_content = Map::new();
    if !content.is_empty() {
        extracted_content.insert("content".into(), json!(content));
    }
    let mut metadata = Map::new();
    metadata.insert("origin".into(), json!("generated_file"));
    metadata.insert("route".into(), json!("direct_write_file"));
    let path = state.config.agent_root.join(rel_path);
    let source_uri = format!("workspace://{rel_path}");
    let request = lattice_core::graph_write::types::IngestFileRequest {
        path,
        original_filename: Some(filename.clone()),
        uploader: effective_email.map(str::to_string),
        conversation_id: conversation_id.map(str::to_string),
        extracted: extracted_content,
        source_type: Some("file".into()),
        source_uri: Some(source_uri.clone()),
        owner: effective_email.map(str::to_string),
        workspace_id: workspace_id.map(str::to_string),
        concepts: extracted.concepts,
        triples: extracted.triples,
        semantic: extracted.semantic,
        embedding,
        ..Default::default()
    };
    let owner = request.owner.clone();
    let workspace = request.workspace_id.clone();
    let written = tokio::task::spawn_blocking(move || {
        let outcome = graph.ingest_file(&request)?;
        let embedded = native_agrees
            && !outcome.node_id.is_empty()
            && graph.write_vectors(&outcome.node_id).status != "failed";
        let _ = graph.record_ingestion(&IngestionRecord {
            node_id: outcome.node_id.clone(),
            source_type: "file".into(),
            pipeline: "unified-ingestion".into(),
            source_uri: Some(source_uri),
            content_hash: outcome.content_hash.clone(),
            title: Some(filename),
            owner,
            workspace_id: workspace,
            captured_at: outcome.captured_at.clone(),
            embedded,
            duplicate: outcome.duplicate,
            chunk_count: outcome.chunk_count as i64,
            metadata,
            ..Default::default()
        });
        Ok::<_, lattice_core::CoreError>(outcome)
    })
    .await;
    match written {
        Ok(Ok(outcome)) => Some(json!({
            "status": "ok",
            "node_id": outcome.node_id,
            "chunk_count": outcome.chunk_count,
            "duplicate": outcome.duplicate,
        })),
        Ok(Err(error)) => Some(failed_ingest(&error.to_string())),
        Err(error) => Some(failed_ingest(&error.to_string())),
    }
}

fn failed_ingest(detail: &str) -> Value {
    json!({
        "status": "failed",
        "detail": detail.chars().take(200).collect::<String>(),
    })
}

async fn extract_for_file(state: &ChatState, text: &str) -> ExtractReply {
    if text.trim().is_empty() {
        return ExtractReply::default();
    }
    let Some(worker) = state.worker.as_ref() else {
        return ExtractReply::default();
    };
    let body = json!({"text": text, "kind": "document"});
    match worker
        .client()
        .post_idempotent_compute(EXTRACT_PATH, &body)
        .await
    {
        Ok(payload) => ExtractReply::from_json(&payload),
        Err(_) => ExtractReply::default(),
    }
}

async fn embed_for_file(state: &ChatState, text: &str) -> Option<SuppliedVector> {
    if text.trim().is_empty() {
        return None;
    }
    let worker = state.worker.as_ref()?;
    let reply = worker.embed(&[text.to_string()], "passage").await.ok()?;
    let values = reply.vectors.into_iter().next()?;
    if values.is_empty() {
        return None;
    }
    Some(SuppliedVector {
        dim: if reply.dim == 0 {
            values.len()
        } else {
            reply.dim
        },
        model_id: reply.model_id,
        values,
    })
}

pub async fn persist_exchange(
    state: &ChatState,
    req: &ChatRequest,
    stored_user: &str,
    answer: &str,
    meta: &HistoryMeta<'_>,
) {
    persist_entry(state, "user", stored_user, meta).await;
    persist_entry(state, "assistant", answer, meta).await;
    state.notify("user", &req.message, req.source.as_deref());
    state.notify("assistant", answer, req.source.as_deref());
}

pub async fn persist_entry(state: &ChatState, role: &str, message: &str, meta: &HistoryMeta<'_>) {
    let _ = crate::turn::write_chat_turn(state, role, message, meta).await;
}

#[cfg(test)]
#[path = "intents_tests.rs"]
mod tests;
