//! Fast-path intents that answer before a model is consulted.
//!
//! Port of `latticeai/api/chat_intents.py`. Network status, `/clear`,
//! current-URL and the direct file-write branch all live here so `POST /chat`
//! can return before it ever asks the worker for tokens. The agent-loop
//! fallback (`route_file_to_agent`) stays with `lattice-agent`; this crate
//! only does the deterministic write the fixtures actually capture.

use std::path::{Path, PathBuf};

use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use fancy_regex::Regex;
use lattice_agent::inference::{infer_file_target, infer_project_manifest};
use lattice_agent::sandbox::Workspace;
use lattice_agent::state::AgentState;
use lattice_auth::OrderedMap;
use lattice_core::graph_write::types::{ExtractReply, IngestionRecord, SuppliedVector};
use lattice_core::messages::{self, LANGUAGE_HEADER};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

use crate::contracts::ChatRequest;
use crate::helpers::{file_action_target, format_network_status, inline_file_action_content};
use crate::history::{clear_all, clear_conversation, error_body, history_scope, json_body};
use crate::sse::{single_text_stream, stream_response};
use crate::state::ChatState;

/// `PREVIEWABLE_EXTENSIONS` — duplicated because lattice-agent keeps the table
/// private on the run-body module. `.docx` is deliberately absent.
const PREVIEWABLE: &[&str] = &[
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

/// `no_model_response` — the JSON 400 every stream×Accept combo must share.
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

/// `single_answer_response`.
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

fn language_of(headers: &HeaderMap) -> &'static str {
    messages::resolve_language(
        headers
            .get(LANGUAGE_HEADER)
            .and_then(|value| value.to_str().ok()),
        headers
            .get(axum::http::header::ACCEPT_LANGUAGE)
            .and_then(|value| value.to_str().ok()),
    )
}

/// Best-effort host facts. The fixture body is `@any`; only status + type pin.
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

/// `ChatIntentController.network`.
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

/// Attribution a persist call carries.
pub struct HistoryMeta<'a> {
    pub email: Option<&'a str>,
    pub nickname: Option<&'a str>,
    pub source: Option<&'a str>,
    pub conversation_id: Option<&'a str>,
    pub workspace_id: Option<&'a str>,
}

/// `ChatIntentController.clear`.
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
            // Best-effort audit event: the history clear itself must still run
            // if the graph write fails (same as the retired worker POST).
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

/// `ChatIntentController.current_url`.
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

/// `next_available_path`.
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

fn previewable(path: &str) -> bool {
    Path::new(path)
        .extension()
        .and_then(|ext| ext.to_str())
        .map(|ext| {
            let dotted = format!(".{}", ext.to_lowercase());
            PREVIEWABLE.binary_search(&dotted.as_str()).is_ok()
        })
        .unwrap_or(false)
}

fn write_file(root: &Path, rel: &str, content: &str) -> Result<(String, usize), String> {
    let workspace = Workspace::new(root).map_err(|error| error.to_string())?;
    let path = workspace.resolve(rel).map_err(|error| error.message)?;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    std::fs::write(&path, content.as_bytes()).map_err(|error| error.to_string())?;
    Ok((workspace.relative(&path), content.len()))
}

/// `file:` + sha256(workspace|digest)[:24], matching `_scoped_hash_id`.
pub fn scoped_file_id(content: &str, workspace_id: Option<&str>) -> String {
    let digest = format!("{:x}", Sha256::digest(content.as_bytes()));
    let identity = match workspace_id.filter(|id| !id.is_empty()) {
        Some(workspace) => format!("{workspace}|{digest}"),
        None => digest,
    };
    let hashed = format!("{:x}", Sha256::digest(identity.as_bytes()));
    format!("file:{}", &hashed[..24.min(hashed.len())])
}

/// `ChatIntentController.direct_file_action`.
///
/// `None` means "not a direct write" — the pipeline falls through to
/// no-model / the agent loop.
pub async fn direct_file_action(
    state: &ChatState,
    req: &ChatRequest,
    headers: &HeaderMap,
    model_id: Option<&str>,
    effective_email: Option<&str>,
    workspace_id: Option<&str>,
) -> Option<Response> {
    let lang = language_of(headers);
    let explicit = file_action_target(&req.message);
    if explicit.is_none() && infer_project_manifest(&req.message).is_some() {
        if model_id.is_none() {
            return Some(no_model_response(state, headers));
        }
        // Multi-file generation needs a model-driven pipeline the with-model
        // path has no fixtures for. Refuse rather than invent a second one.
        return Some(error_body(400, "chat.file_generation_failed", headers, &[]));
    }
    let target_path = explicit.or_else(|| infer_file_target(&req.message))?;
    let deduped = match next_available_path(&state.config.agent_root, &target_path) {
        Ok(path) => path,
        Err(name) => {
            return Some(error_body(
                409,
                "chat.file_name_collision",
                headers,
                &[("name", &name)],
            ));
        }
    };
    let renamed = deduped != target_path;
    let mut content = inline_file_action_content(&req.message);
    if content.is_none() && model_id.is_none() {
        return Some(no_model_response(state, headers));
    }
    if content.is_none() {
        // Model-driven generation is the un-fixtured path; do not invent a
        // second extract/validate/repair pipeline here.
        return Some(error_body(400, "chat.file_generation_failed", headers, &[]));
    }
    let content = content.take().unwrap_or_default();
    let (final_path, bytes) = match write_file(&state.config.agent_root, &deduped, &content) {
        Ok(written) => written,
        Err(detail) => {
            return Some(json_body(
                StatusCode::BAD_REQUEST,
                &json!({"detail": detail}),
            ));
        }
    };
    let mut answer = format!("{final_path} 파일을 만들었습니다.");
    if renamed {
        answer.push_str(" (같은 이름의 파일이 있어 새 이름으로 저장했습니다.)");
    }
    let filename = Path::new(&final_path)
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or(&final_path)
        .to_string();
    let created = json!([{
        "path": final_path,
        "filename": filename,
        "bytes": bytes,
        "action": "write_file",
    }]);
    let artifacts = json!([{
        "kind": "file",
        "path": final_path,
        "filename": filename,
        "bytes": bytes,
        "previewable": previewable(&final_path),
        "valid": true,
        "repaired": false,
    }]);
    let mut payload = OrderedMap::new();
    payload.insert("status", json!("ok"));
    payload.insert("response", json!(answer));
    payload.insert(
        "workspace",
        json!(state.config.agent_root.to_string_lossy().into_owned()),
    );
    payload.insert(
        "steps",
        json!([{
            "state": AgentState::Executing.as_str(),
            "action": "write_file",
            "args": {"path": deduped},
            "result": {"path": final_path, "bytes": bytes},
        }]),
    );
    payload.insert(
        "state_history",
        json!([AgentState::Executing.as_str(), AgentState::Done.as_str()]),
    );
    payload.insert("final_state", json!(AgentState::Done.as_str()));
    payload.insert("created_files", created);
    payload.insert("artifacts", artifacts);
    payload.insert("routed_to_agent", json!(true));
    payload.insert("action_route", json!("direct_write_file"));
    if let Some(ingest) = ingest_generated(
        state,
        &final_path,
        &content,
        effective_email,
        workspace_id,
        req.conversation_id.as_deref(),
    )
    .await
    {
        payload.insert("brain_ingest", ingest);
    }
    state.funnel_increment("real_file_delivered");
    state.notify("user", &req.message, req.source.as_deref());
    state.notify("assistant", &answer, req.source.as_deref());
    let _ = lang;
    let rendered = serde_json::to_value(payload).unwrap_or(Value::Null);
    if req.stream {
        return Some(stream_response(
            crate::sse::agent_payload_stream(&answer, &rendered, model_id.unwrap_or("tool")),
            &[
                ("X-Model", model_id.unwrap_or("tool")),
                ("X-Routed-To", "agent"),
            ],
        ));
    }
    Some(json_body(StatusCode::OK, &rendered))
}

/// `POST /worker/extract` — W5's LLM-first concept / triple / semantic seam.
const EXTRACT_PATH: &str = "/worker/extract";

/// `_ingest_generated_file` — index a just-generated file into the Brain.
///
/// The knowledge promise is "what Lattice makes, Lattice remembers": without
/// this, generated files are invisible to recall until the user re-uploads
/// them. Best-effort and additive — an ingestion problem never fails the file
/// creation, and `LATTICEAI_INGEST_GENERATED=0` disables it.
///
/// **v11.7.0 correction.** This posted the item to
/// `POST {worker}/knowledge-graph/ingest` until now. v11.6.0 took that route
/// off the worker (it is absent from `rust/fixtures/worker_allowlist.json`), so
/// the call 404'd — and because the fixture worker answered it, the reply still
/// said `{"status": "ok"}` while nothing had been remembered. The write is
/// native now, through the same [`GraphWriter`](lattice_core::graph_write::GraphWriter)
/// door the upload path uses, with the W5 extract / W2 embed chain around it:
///
/// * [`GraphWriter::ingest_file`] because the material *is* a file on disk —
///   that is what `IngestionItem(source_type="file", path=…)` routed to
///   (`_ingest_file` → `ingest_document`), so the node id stays
///   `file:<scoped hash of the bytes>` and the blob sidecar is still written;
/// * the content is handed over as `extracted["content"]`, exactly as the
///   native `POST /upload/document` door hands a parsed upload over, so the
///   node's text is searchable rather than just its filename;
/// * `write_vectors` runs only when the seam's embedder is the one this process
///   would file the row under (W2 §4), and `record_ingestion` captures
///   provenance whether or not it did.
///
/// The four reported keys are unchanged, so every client that reads
/// `brain_ingest` reads the same shape — it is finally telling the truth.
async fn ingest_generated(
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
        // Provenance must never turn an already-persisted file into a failure.
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

/// `{"status": "failed", "detail": …}` — the shape the clients already parse.
fn failed_ingest(detail: &str) -> Value {
    json!({
        "status": "failed",
        "detail": detail.chars().take(200).collect::<String>(),
    })
}

/// Ask `/worker/extract` about a generated file, best-effort.
///
/// The same `kind` the garden and upload doors use for a document, so a file
/// Lattice wrote and the same file uploaded produce one concept subgraph rather
/// than two subtly different ones. No worker, or a failed call, means no
/// concepts — never a lost file.
async fn extract_for_file(state: &ChatState, text: &str) -> ExtractReply {
    if text.trim().is_empty() {
        return ExtractReply::default();
    }
    let Some(worker) = state.worker.as_ref() else {
        return ExtractReply::default();
    };
    let body = json!({"text": text, "kind": "document"});
    match worker.client().post_json(EXTRACT_PATH, &body).await {
        Ok(payload) => ExtractReply::from_json(&payload),
        Err(_) => ExtractReply::default(),
    }
}

/// Ask `/worker/embed` for the file's passage vector, best-effort.
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

/// Persist both sides of an intent exchange through the native turn chain.
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

/// One turn through the native redact → audit → store → ingest chain.
///
/// Before WP-W3a this posted to `POST /worker/chat/record-turn` and threw the
/// receipt away. It still throws the receipt away — no caller has ever read it —
/// but the chain now runs here ([`crate::turn::write_chat_turn`]), so a turn
/// lands whether or not the AI worker is reachable. That is a strict improvement
/// over the seam: an unreachable worker used to lose the message.
pub async fn persist_entry(state: &ChatState, role: &str, message: &str, meta: &HistoryMeta<'_>) {
    let _ = crate::turn::write_chat_turn(state, role, message, meta).await;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn next_available_fills_the_first_free_slot() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("note.md"), b"x").unwrap();
        assert_eq!(
            next_available_path(dir.path(), "note.md").unwrap(),
            "note_2.md"
        );
        std::fs::write(dir.path().join("note_2.md"), b"x").unwrap();
        assert_eq!(
            next_available_path(dir.path(), "note.md").unwrap(),
            "note_3.md"
        );
        assert_eq!(
            next_available_path(dir.path(), "fresh.md").unwrap(),
            "fresh.md"
        );
    }

    #[test]
    fn previewable_matches_the_agent_table() {
        assert!(previewable("a.md"));
        assert!(previewable("A.HTML"));
        assert!(!previewable("a.docx"));
        assert!(!previewable("noext"));
    }

    #[test]
    fn scoped_file_ids_are_stable() {
        let a = scoped_file_id("Hello Lattice", Some("personal"));
        let b = scoped_file_id("Hello Lattice", Some("personal"));
        assert_eq!(a, b);
        assert!(a.starts_with("file:"));
        assert_eq!(a.len(), 5 + 24);
        assert_ne!(a, scoped_file_id("Hello Lattice", None));
    }

    #[tokio::test]
    async fn clear_writes_the_audit_event_through_graph_writer() {
        use std::collections::HashMap;
        use std::sync::Arc;

        use lattice_auth::AuthConfig;
        use lattice_core::db::Store;
        use lattice_core::graph_write::GraphWriter;

        use crate::state::{ChatConfig, ChatState};

        let dir = tempfile::tempdir().unwrap();
        let mut env = HashMap::new();
        env.insert(
            "LATTICEAI_DATA_DIR".into(),
            dir.path().to_string_lossy().into_owned(),
        );
        let auth = lattice_auth::AuthState::new(AuthConfig::from_map(&env, None));
        let store = Arc::new(Store::open(&dir.path().join("knowledge_graph.sqlite")).unwrap());
        let graph = GraphWriter::open(Arc::clone(&store), dir.path().join("knowledge_graph_blobs"))
            .unwrap();
        let state = ChatState::new(
            auth,
            ChatConfig {
                data_dir: dir.path().to_path_buf(),
                graph_db: Some(dir.path().join("knowledge_graph.sqlite")),
                agent_root: dir.path().join("agent"),
                ..ChatConfig::default()
            },
        )
        .with_graph(graph.clone());
        let req = ChatRequest {
            message: "/clear".into(),
            conversation_id: Some("conv-1".into()),
            ..ChatRequest::default()
        };
        let _ = clear(
            &state,
            &req,
            &HeaderMap::new(),
            Some("owner@lattice.test"),
            Some("personal"),
        )
        .await;
        let count: i64 = graph
            .store()
            .with_read_conn(|conn| {
                Ok(conn
                    .query_row(
                        "SELECT COUNT(*) FROM nodes WHERE type = ?1",
                        ["ClearEvent"],
                        |row| row.get(0),
                    )
                    .unwrap_or(0))
            })
            .unwrap();
        assert_eq!(count, 1);
    }

    /// A state with a Brain, an agent workspace and **no worker** — the shape
    /// that used to make `ingest_generated` return `None` and, with a worker,
    /// made it post into a route that no longer exists.
    fn brain_state(
        dir: &Path,
    ) -> (
        crate::state::ChatState,
        lattice_core::graph_write::GraphWriter,
    ) {
        use std::collections::HashMap;
        use std::sync::Arc;

        use lattice_auth::AuthConfig;
        use lattice_core::db::Store;
        use lattice_core::graph_write::GraphWriter;

        use crate::state::{ChatConfig, ChatState};

        let mut env = HashMap::new();
        env.insert(
            "LATTICEAI_DATA_DIR".into(),
            dir.to_string_lossy().into_owned(),
        );
        let auth = lattice_auth::AuthState::new(AuthConfig::from_map(&env, None));
        let store = Arc::new(Store::open(&dir.join("knowledge_graph.sqlite")).unwrap());
        let graph = GraphWriter::open(store, dir.join("knowledge_graph_blobs")).unwrap();
        let agent_root = dir.join("agent");
        std::fs::create_dir_all(&agent_root).unwrap();
        let state = ChatState::new(
            auth,
            ChatConfig {
                data_dir: dir.to_path_buf(),
                graph_db: Some(dir.join("knowledge_graph.sqlite")),
                agent_root,
                ..ChatConfig::default()
            },
        )
        .with_graph(graph.clone());
        (state, graph)
    }

    #[tokio::test]
    async fn a_generated_file_is_written_through_the_graph_writer() {
        let dir = tempfile::tempdir().unwrap();
        let (state, graph) = brain_state(dir.path());
        std::fs::write(state.config.agent_root.join("메모.md"), b"Hello Lattice").unwrap();

        let receipt = ingest_generated(
            &state,
            "메모.md",
            "Hello Lattice",
            Some("owner@lattice.test"),
            Some("personal"),
            Some("conv-1"),
        )
        .await
        .expect("the Brain is bound, so there is a receipt");
        assert_eq!(receipt["status"], "ok");
        assert_eq!(
            receipt["node_id"],
            json!(scoped_file_id("Hello Lattice", Some("personal"))),
            "the id is the scoped hash of the bytes on disk — the same id the \
             retired route's fixture pinned"
        );
        assert_eq!(receipt["chunk_count"], 0);
        assert_eq!(receipt["duplicate"], false);

        let node_id = receipt["node_id"].as_str().unwrap().to_string();
        let (node_type, title, summary, metadata): (String, String, String, String) = graph
            .store()
            .with_read_conn(|conn| {
                Ok(conn
                    .query_row(
                        "SELECT type, title, summary, metadata_json FROM nodes WHERE id = ?1",
                        [&node_id],
                        |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
                    )
                    .expect("the generated file is a node"))
            })
            .unwrap();
        assert_eq!(node_type, "Document");
        assert_eq!(title, "메모.md");
        assert_eq!(
            summary, "Hello Lattice",
            "the text is searchable, not just the name"
        );
        let metadata: Value = serde_json::from_str(&metadata).unwrap();
        assert_eq!(metadata["source_uri"], "workspace://메모.md");
        assert_eq!(metadata["source_type"], "file");
        assert_eq!(metadata["owner"], "owner@lattice.test");
        assert_eq!(metadata["workspace_id"], "personal");
        assert_eq!(metadata["conversation_id"], "conv-1");

        let provenance: i64 = graph
            .store()
            .with_read_conn(|conn| {
                Ok(conn
                    .query_row(
                        "SELECT COUNT(*) FROM ingestion_provenance WHERE source_type = 'file'",
                        [],
                        |row| row.get(0),
                    )
                    .unwrap_or(0))
            })
            .unwrap();
        assert_eq!(provenance, 1);

        // Writing the same file again is the same node, reported as such.
        let again = ingest_generated(
            &state,
            "메모.md",
            "Hello Lattice",
            Some("owner@lattice.test"),
            Some("personal"),
            Some("conv-1"),
        )
        .await
        .expect("receipt");
        assert_eq!(again["duplicate"], true);
    }

    #[tokio::test]
    async fn without_a_brain_or_with_the_gate_off_nothing_is_claimed() {
        let dir = tempfile::tempdir().unwrap();
        let (state, _graph) = brain_state(dir.path());
        std::fs::write(state.config.agent_root.join("a.md"), b"x").unwrap();

        // Gate off: no ingest, and no `brain_ingest` key for a client to read.
        let mut off = state.clone();
        off.config.ingest_generated = false;
        assert!(ingest_generated(&off, "a.md", "x", None, None, None)
            .await
            .is_none());

        // No Brain bound: the same silence, rather than a "failed" receipt for
        // something that was never attempted.
        let mut brainless = state.clone();
        brainless.graph = None;
        assert!(ingest_generated(&brainless, "a.md", "x", None, None, None)
            .await
            .is_none());

        // A file that is not on disk is a named failure, not a false "ok".
        let missing = ingest_generated(&state, "gone.md", "x", None, None, None)
            .await
            .expect("receipt");
        assert_eq!(missing["status"], "failed");
        assert!(
            missing["detail"].as_str().unwrap_or_default().len() <= 200,
            "the detail is truncated for a log line"
        );
    }
}
