pub mod author;
pub mod office;
pub mod prompts;
mod reply;

use std::collections::BTreeMap;

use axum::body::Body;
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use lattice_agent::content::sanitize::validate_file_content;
use lattice_agent::parse::inference::manifest_paths;
use lattice_auth::OrderedMap;
use serde_json::{json, Value};

use crate::contracts::ChatRequest;
use crate::helpers::inline_file_action_content;
use crate::history::{error_body, json_body};
use crate::intents::{ingest_generated, next_available_path, no_model_response};
use crate::sse::{agent_payload_stream, data_frame, stream_response, DONE};
use crate::state::ChatState;
use crate::stream::{frame_channel, FrameSink};

use author::author_file;
use reply::{created_sentence, file_name_of, project_sentence};

pub const MAX_PROJECT_FILES: usize = 3;

pub const PROGRESS_FRAME: &str = "file_generation";

const TOOL_MODEL: &str = "tool";

struct Turn {
    state: ChatState,
    req: ChatRequest,
    lang: &'static str,
    model: Option<String>,
    email: Option<String>,
    workspace: Option<String>,
}

struct Produced {
    text: String,
    bytes: Vec<u8>,
    valid: bool,
    repaired: bool,
    attempts: Vec<Value>,
    action: &'static str,
    authored: bool,
}

struct Written {
    requested: String,
    path: String,
    filename: String,
    bytes: usize,
    valid: bool,
    repaired: bool,
    action: &'static str,
    ingest: Option<Value>,
}

enum Refusal {
    Generation(Vec<Value>),
    Named {
        error: &'static str,
        detail: String,
        action: &'static str,
    },
    Write(String),
}

enum Outcome {
    Written { answer: String, payload: Value },
    Refused(Refusal),
}

pub(crate) async fn file_action(
    state: &ChatState,
    req: &ChatRequest,
    headers: &HeaderMap,
    model_id: Option<&str>,
    effective_email: Option<&str>,
    workspace_id: Option<&str>,
    target: &str,
) -> Response {
    if office::classify(target) == office::Target::Unsupported {
        return named_refusal(
            "office_format_unsupported",
            &office::unsupported(crate::intents::language_of(headers), target),
            "use_agent",
        );
    }
    let deduped = match next_available_path(&state.config.agent_root, target) {
        Ok(path) => path,
        Err(name) => {
            return error_body(409, "chat.file_name_collision", headers, &[("name", &name)]);
        }
    };
    let renamed = deduped != target;
    let inline = inline_file_action_content(&req.message);
    if inline.is_none() && model_id.is_none() {
        return no_model_response(state, headers);
    }
    let turn = Turn::new(state, req, headers, model_id, effective_email, workspace_id);
    let label = turn.model_label();
    if req.stream {
        let (sink, stream) = frame_channel();
        let streamed = label.clone();
        let lang = turn.lang;
        tokio::spawn(async move {
            let outcome = turn.single(&deduped, renamed, inline, Some(&sink)).await;
            turn_frames(&sink, outcome, &streamed, lang).await;
        });
        return stream_response(
            Body::from_stream(stream),
            &[("X-Model", &label), ("X-Routed-To", "agent")],
        );
    }
    let outcome = turn.single(&deduped, renamed, inline, None).await;
    respond(outcome, headers)
}

pub(crate) async fn project_action(
    state: &ChatState,
    req: &ChatRequest,
    headers: &HeaderMap,
    model_id: &str,
    effective_email: Option<&str>,
    workspace_id: Option<&str>,
    manifest: Value,
) -> Response {
    let turn = Turn::new(
        state,
        req,
        headers,
        Some(model_id),
        effective_email,
        workspace_id,
    );
    let label = turn.model_label();
    if req.stream {
        let (sink, stream) = frame_channel();
        let streamed = label.clone();
        let lang = turn.lang;
        tokio::spawn(async move {
            let outcome = turn.project(&manifest, Some(&sink)).await;
            turn_frames(&sink, outcome, &streamed, lang).await;
        });
        return stream_response(
            Body::from_stream(stream),
            &[("X-Model", &label), ("X-Routed-To", "agent")],
        );
    }
    let outcome = turn.project(&manifest, None).await;
    respond(outcome, headers)
}

impl Turn {
    fn new(
        state: &ChatState,
        req: &ChatRequest,
        headers: &HeaderMap,
        model_id: Option<&str>,
        effective_email: Option<&str>,
        workspace_id: Option<&str>,
    ) -> Self {
        Self {
            state: state.clone(),
            req: req.clone(),
            lang: crate::intents::language_of(headers),
            model: model_id.map(str::to_string),
            email: effective_email.map(str::to_string),
            workspace: workspace_id.map(str::to_string),
        }
    }

    fn model_label(&self) -> String {
        self.model.clone().unwrap_or_else(|| TOOL_MODEL.to_string())
    }

    async fn single(
        &self,
        deduped: &str,
        renamed: bool,
        inline: Option<String>,
        progress: Option<&FrameSink>,
    ) -> Outcome {
        let produced = match self.produce(deduped, None, inline, progress).await {
            Ok(produced) => produced,
            Err(refusal) => {
                announce(progress, deduped, "failed").await;
                return Outcome::Refused(refusal);
            }
        };
        let written = match self.write_one(deduped, &produced).await {
            Ok(written) => written,
            Err(refusal) => {
                announce(progress, deduped, "failed").await;
                return Outcome::Refused(refusal);
            }
        };
        announce(progress, &written.path, "written").await;
        let answer = created_sentence(self.lang, &written.path, renamed);
        let payload = reply::payload(
            &self.state.config.agent_root,
            &[written],
            &answer,
            &produced.attempts,
            produced.authored,
            true,
        );
        self.announce_answer(&answer);
        Outcome::Written { answer, payload }
    }

    async fn project(&self, manifest: &Value, progress: Option<&FrameSink>) -> Outcome {
        let paths = manifest_paths(manifest);
        let briefs = file_briefs(manifest);
        let deferred: Vec<String> = paths.iter().skip(MAX_PROJECT_FILES).cloned().collect();
        let mut written: Vec<Written> = Vec::new();
        let mut attempts: Vec<Value> = Vec::new();
        let mut failed: Vec<String> = Vec::new();
        for path in paths.iter().take(MAX_PROJECT_FILES) {
            let Ok(deduped) = next_available_path(&self.state.config.agent_root, path) else {
                failed.push(path.clone());
                continue;
            };
            let brief = briefs.get(path).map(String::as_str);
            match self.produce(&deduped, brief, None, progress).await {
                Ok(produced) => {
                    attempts.extend(produced.attempts.iter().cloned());
                    match self.write_one(&deduped, &produced).await {
                        Ok(file) => {
                            announce(progress, &file.path, "written").await;
                            written.push(file);
                        }
                        Err(_) => {
                            announce(progress, &deduped, "failed").await;
                            failed.push(path.clone());
                        }
                    }
                }
                Err(Refusal::Generation(tried)) => {
                    attempts.extend(tried);
                    announce(progress, &deduped, "failed").await;
                    failed.push(path.clone());
                }
                Err(_) => {
                    announce(progress, &deduped, "failed").await;
                    failed.push(path.clone());
                }
            }
        }
        if written.is_empty() {
            return Outcome::Refused(Refusal::Generation(attempts));
        }
        let answer = project_sentence(self.lang, &written, &deferred, &failed);
        let payload = reply::payload(
            &self.state.config.agent_root,
            &written,
            &answer,
            &attempts,
            true,
            failed.is_empty(),
        );
        self.announce_answer(&answer);
        Outcome::Written { answer, payload }
    }

    async fn produce(
        &self,
        target: &str,
        brief: Option<&str>,
        inline: Option<String>,
        progress: Option<&FrameSink>,
    ) -> Result<Produced, Refusal> {
        let kind = office::classify(target);
        if kind == office::Target::Unsupported {
            return Err(Refusal::Named {
                error: "office_format_unsupported",
                detail: office::unsupported(self.lang, target),
                action: "use_agent",
            });
        }

        let (text, mut valid, repaired, attempts, authored) = match inline {
            Some(content) => {
                let (ok, _reason) = validate_file_content(&content, target);
                (content, ok, false, Vec::new(), false)
            }
            None => {
                announce(progress, target, "generating").await;
                let Some(model) = self.model.as_deref() else {
                    return Err(Refusal::Generation(Vec::new()));
                };
                match author_file(
                    &self.state,
                    model,
                    &self.req.message,
                    target,
                    brief,
                    self.req.max_tokens,
                )
                .await
                {
                    Ok(authored) => (
                        authored.content,
                        authored.valid,
                        authored.repaired,
                        tag_attempts(authored.attempts, target),
                        true,
                    ),
                    Err(unauthored) => {
                        return Err(Refusal::Generation(tag_attempts(
                            unauthored.attempts,
                            target,
                        )))
                    }
                }
            }
        };

        let mut action = "write_file";
        let bytes = match kind {
            office::Target::Rendered(kind) => {
                let filename = file_name_of(target);
                match office::render(&self.state, kind, &filename, &text).await {
                    Ok(bytes) => {
                        action = if kind == "pdf" {
                            "create_pdf"
                        } else {
                            "create_docx"
                        };
                        valid = true;
                        bytes
                    }
                    Err(detail) => {
                        return Err(Refusal::Named {
                            error: "document_render_failed",
                            detail: office::render_failed(self.lang, target, &detail),
                            action: "use_agent",
                        })
                    }
                }
            }
            office::Target::Text | office::Target::Unsupported => text.as_bytes().to_vec(),
        };

        Ok(Produced {
            text,
            bytes,
            valid,
            repaired,
            attempts,
            action,
            authored,
        })
    }

    async fn write_one(&self, deduped: &str, produced: &Produced) -> Result<Written, Refusal> {
        let (path, bytes) =
            crate::intents::write_bytes(&self.state.config.agent_root, deduped, &produced.bytes)
                .map_err(Refusal::Write)?;
        let ingest = ingest_generated(
            &self.state,
            &path,
            &produced.text,
            self.email.as_deref(),
            self.workspace.as_deref(),
            self.req.conversation_id.as_deref(),
        )
        .await;
        self.state.funnel_increment("real_file_delivered");
        Ok(Written {
            requested: deduped.to_string(),
            filename: file_name_of(&path),
            path,
            bytes,
            valid: produced.valid,
            repaired: produced.repaired,
            action: produced.action,
            ingest,
        })
    }

    fn announce_answer(&self, answer: &str) {
        self.state
            .notify("user", &self.req.message, self.req.source.as_deref());
        self.state
            .notify("assistant", answer, self.req.source.as_deref());
    }
}

async fn announce(progress: Option<&FrameSink>, path: &str, status: &str) {
    let Some(sink) = progress else { return };
    let mut payload = OrderedMap::new();
    payload.insert("type", json!(PROGRESS_FRAME));
    payload.insert("path", json!(path));
    payload.insert("status", json!(status));
    payload.insert("chunk", json!(""));
    let _ = sink
        .send(data_frame(
            &serde_json::to_value(payload).unwrap_or(Value::Null),
        ))
        .await;
}

fn respond(outcome: Outcome, headers: &HeaderMap) -> Response {
    match outcome {
        Outcome::Written { payload, .. } => json_body(StatusCode::OK, &payload),
        Outcome::Refused(Refusal::Generation(attempts)) => {
            generation_failed_body(headers, &attempts)
        }
        Outcome::Refused(Refusal::Named {
            error,
            detail,
            action,
        }) => named_refusal(error, &detail, action),
        Outcome::Refused(Refusal::Write(detail)) => {
            json_body(StatusCode::BAD_REQUEST, &json!({"detail": detail}))
        }
    }
}

fn named_refusal(error: &str, detail: &str, action: &str) -> Response {
    let mut body = OrderedMap::new();
    body.insert("error", json!(error));
    body.insert("detail", json!(detail));
    body.insert("message", json!(detail));
    body.insert("action", json!(action));
    json_body(
        StatusCode::BAD_REQUEST,
        &serde_json::to_value(body).unwrap_or(Value::Null),
    )
}

fn generation_failed_body(headers: &HeaderMap, attempts: &[Value]) -> Response {
    if attempts.is_empty() {
        return error_body(400, "chat.file_generation_failed", headers, &[]);
    }
    let mut body = OrderedMap::new();
    body.insert(
        "detail",
        json!(generation_failed_text(crate::intents::language_of(headers))),
    );
    body.insert("attempts", json!(attempts));
    json_body(
        StatusCode::BAD_REQUEST,
        &serde_json::to_value(body).unwrap_or(Value::Null),
    )
}

fn generation_failed_text(lang: &str) -> String {
    lattice_core::messages::text("chat.file_generation_failed", lang, &[])
}

async fn turn_frames(sink: &FrameSink, outcome: Outcome, model: &str, lang: &str) {
    match outcome {
        Outcome::Written { answer, payload } => {
            let _ = sink
                .send(agent_payload_stream(&answer, &payload, model))
                .await;
        }
        Outcome::Refused(refusal) => {
            let (error, detail) = match refusal {
                Refusal::Generation(_) => ("file_generation_failed", generation_failed_text(lang)),
                Refusal::Named { error, detail, .. } => (error, detail),
                Refusal::Write(detail) => ("write_failed", detail),
            };
            let mut payload = OrderedMap::new();
            payload.insert("chunk", json!(detail));
            payload.insert("model", json!(model));
            payload.insert("error", json!(error));
            let _ = sink
                .send(data_frame(
                    &serde_json::to_value(payload).unwrap_or(Value::Null),
                ))
                .await;
            let _ = sink.send(DONE).await;
        }
    }
}

fn tag_attempts(attempts: Vec<Value>, target: &str) -> Vec<Value> {
    attempts
        .into_iter()
        .map(|mut record| {
            if let Some(object) = record.as_object_mut() {
                object.insert("path".into(), json!(target));
            }
            record
        })
        .collect()
}

fn file_briefs(manifest: &Value) -> BTreeMap<String, String> {
    let mut briefs = BTreeMap::new();
    for file in manifest
        .get("files")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        let (Some(path), Some(brief)) = (
            file.get("path").and_then(Value::as_str),
            file.get("brief").and_then(Value::as_str),
        ) else {
            continue;
        };
        briefs.insert(path.to_string(), brief.to_string());
    }
    briefs
}

#[cfg(test)]
mod tests;
