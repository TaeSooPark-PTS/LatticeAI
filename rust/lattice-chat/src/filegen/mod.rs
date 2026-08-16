//! Model-authored file generation on `POST /chat` (v11.9.0).
//!
//! Before this module, "HTML 파일 만들어줘" in plain chat was a deliberate
//! **400**: the branch could only write content the user had typed inline
//! (`내용: …`), and everything else was refused with `chat.file_generation_failed`
//! and the comment "model-driven generation is the un-fixtured path". A person
//! with a 2B model loaded and a perfectly ordinary request got an error.
//!
//! What that comment was protecting is real, and it is kept: a weak model's
//! reply is not a file, and the difference between the two is not something a
//! chat surface may guess at. So the generation this module adds is the one the
//! agent path already trusts, reached from here rather than reimplemented —
//! [`lattice_agent::sanitize`] validates, unwraps and repairs, and this module
//! adds only the parts that are chat's own: the anchored prompts
//! ([`prompts`]), the single corrective retry ([`author`]), the refusal to write
//! a scaffold nobody wrote, and real Office/PDF bytes instead of prose under a
//! `.docx` name ([`office`]).
//!
//! ## What the reply says, and why it can be trusted
//!
//! | key | before | now |
//! |---|---|---|
//! | `artifacts[].valid` | hard-coded `true` | the validator's verdict on the bytes on disk |
//! | `artifacts[].repaired` | hard-coded `false` | whether deterministic repair produced them |
//! | `generation.repaired` | absent | the same fact, where the SPA's "자동 보정됨" badge reads it |
//! | `generation.attempts` | absent | one record per model attempt: outcome and the validator's reason |
//!
//! The badge is `agentPayloadFiles`' `Boolean(agent.generation?.repaired)`
//! (`frontend/src/features/brain/brainData.ts`), which is where the **agent**
//! path's repairs surface too — so a repaired file from chat and a repaired file
//! from the agent now render identically, which is the whole point of not
//! having a second pipeline.
//!
//! ## Order of operations, and where the response is committed
//!
//! The never-overwrite dedup ([`crate::intents::next_available_path`]) runs
//! **before** anything is streamed, so a name collision is still a 409 and not
//! an error frame inside a 200. Everything after it — the model call, the
//! sanitize pass, the write, the Brain ingest — runs inside the stream task when
//! `stream: true`, so the `file_generation` progress frame reaches the client
//! before the model is asked rather than after the work is finished.

pub mod author;
pub mod office;
pub mod prompts;
mod reply;

use std::collections::BTreeMap;

use axum::body::Body;
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use lattice_agent::inference::manifest_paths;
use lattice_agent::sanitize::validate_file_content;
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

/// How many files one multi-file request writes in a single turn.
///
/// A recognised project manifest can name five (the React one does). Writing
/// five files means five model calls in a row, which on a local 2B is minutes
/// of silence, and it means five files the user has not seen yet. Three is what
/// this surface does per turn; the rest are named in the reply and are one
/// follow-up away.
pub const MAX_PROJECT_FILES: usize = 3;

/// The `type` of the progress frame a generating stream emits.
pub const PROGRESS_FRAME: &str = "file_generation";

/// The model label a file-action stream carries when no model was consulted.
const TOOL_MODEL: &str = "tool";

/// One file-writing turn, owned so it can move into a stream task.
struct Turn {
    state: ChatState,
    req: ChatRequest,
    lang: &'static str,
    model: Option<String>,
    email: Option<String>,
    workspace: Option<String>,
}

/// Content ready to be written, and everything the artifact must say about it.
struct Produced {
    /// The text the Brain remembers — and, for a document, what it was built
    /// from. Never the binary.
    text: String,
    /// The bytes that land on disk.
    bytes: Vec<u8>,
    valid: bool,
    repaired: bool,
    /// One record per model attempt; empty when the user typed the content.
    attempts: Vec<Value>,
    /// `write_file`, `create_docx`, `create_pdf` — what actually happened.
    action: &'static str,
    /// The model authored this, rather than the user typing it inline.
    authored: bool,
}

/// One file on disk, as the reply describes it.
struct Written {
    /// The path asked for (post-dedup), which is what the step's args show.
    requested: String,
    /// The path the workspace resolved to, which is what the client opens.
    path: String,
    filename: String,
    bytes: usize,
    valid: bool,
    repaired: bool,
    action: &'static str,
    ingest: Option<Value>,
}

/// Why nothing was written.
enum Refusal {
    /// The model never produced a document. `chat.file_generation_failed`,
    /// with the attempts attached so the reply says what was tried.
    Generation(Vec<Value>),
    /// A refusal this surface composes itself: an Office format it will not
    /// fake, or a render that failed.
    Named {
        error: &'static str,
        detail: String,
        action: &'static str,
    },
    /// The write itself failed (permissions, an escaping path).
    Write(String),
}

/// What a turn ended as.
enum Outcome {
    Written { answer: String, payload: Value },
    Refused(Refusal),
}

/// `POST /chat`'s direct file action for one named file.
///
/// `target` is the path the request named or the inference produced;
/// `model_id` is the loaded model, absent when none is.
pub(crate) async fn file_action(
    state: &ChatState,
    req: &ChatRequest,
    headers: &HeaderMap,
    model_id: Option<&str>,
    effective_email: Option<&str>,
    workspace_id: Option<&str>,
    target: &str,
) -> Response {
    // A format this surface will not fake is knowable from the name alone, so
    // it is refused first and refused as a real 4xx — better than an error
    // frame inside a 200, and better than picking a free filename for a file
    // that is never going to exist. Only a refusal that needs the worker (a
    // render that failed) has to happen inside the stream.
    if office::classify(target) == office::Target::Unsupported {
        return named_refusal(
            "office_format_unsupported",
            &office::unsupported(crate::intents::language_of(headers), target),
            "use_agent",
        );
    }
    // Never overwrite: this runs before a stream is opened, so a collision is
    // still an honest 409 rather than an error frame inside a 200.
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

/// `POST /chat`'s multi-file branch: a recognised project manifest.
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

    /// The `X-Model` header and the stream's model label.
    fn model_label(&self) -> String {
        self.model.clone().unwrap_or_else(|| TOOL_MODEL.to_string())
    }

    /// One file: the user's own bytes, or the model's.
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

    /// Up to [`MAX_PROJECT_FILES`] files, one after another. Never in parallel:
    /// one local model serves them all, so two at once is two half-speed
    /// generations and twice the memory.
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

    /// The bytes for one file: inline content kept as typed, or model output
    /// put through the write-side pipeline, then a real render for a document.
    async fn produce(
        &self,
        target: &str,
        brief: Option<&str>,
        inline: Option<String>,
        progress: Option<&FrameSink>,
    ) -> Result<Produced, Refusal> {
        let kind = office::classify(target);
        if kind == office::Target::Unsupported {
            // A spreadsheet or a deck built out of one block of prose would be a
            // guess about what the rows or the slides were; the agent takes them
            // as data instead.
            return Err(Refusal::Named {
                error: "office_format_unsupported",
                detail: office::unsupported(self.lang, target),
                action: "use_agent",
            });
        }

        let (text, mut valid, repaired, attempts, authored) = match inline {
            // The user's words are the user's: they are written exactly as
            // typed. What changes in v11.9.0 is that the artifact stops
            // *claiming* they are valid and reports the validator's verdict.
            Some(content) => {
                let (ok, _reason) = validate_file_content(&content, target);
                (content, ok, false, Vec::new(), false)
            }
            None => {
                announce(progress, target, "generating").await;
                let Some(model) = self.model.as_deref() else {
                    // Unreachable: the caller refuses with `no_model_loaded`
                    // before it gets here. Honest failure rather than a panic.
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
                        // The bytes are the builder's own document, so the
                        // artifact's verdict is about the render, not about the
                        // prose the render was typeset from.
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
            // Unreachable — refused above — but written as a branch rather than
            // an `unwrap`: an extension added to the table without a decision
            // here writes its text, which is the old behaviour, not a panic.
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

    /// Write the bytes and remember the file, exactly as the inline path did.
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

    /// The two sides of the exchange the bridges mirror, as the branch has
    /// always announced them.
    fn announce_answer(&self, answer: &str) {
        self.state
            .notify("user", &self.req.message, self.req.source.as_deref());
        self.state
            .notify("assistant", answer, self.req.source.as_deref());
    }
}

/// `{"type": "file_generation", "path": …, "status": …, "chunk": ""}` — the
/// progress frame.
///
/// `generating` is sent before the model is asked, `written` after the bytes are
/// on disk, `failed` when nothing was written.
///
/// **The empty `chunk` is load-bearing**, and it is the one thing about this
/// frame that is not obvious. The SPA reads `data.chunk || data.text || ""` and
/// would ignore a frame without it — but the VS Code extension's reader is
/// `chunks.push(parsed.chunk)` (`vscode-extension/client.ts`), and its consumer
/// then does `accumulated += chunk`. A frame with no `chunk` key would put the
/// literal string "undefined" in the middle of that editor's answer. An empty
/// string appends nothing in every client that reads one.
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

/// The JSON answer, for `stream: false`.
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

/// A refusal this surface words itself, in the shape `no_model_response` uses.
///
/// `error` / `detail` / `message` / `action` are the four keys the SPA's error
/// path already reads (`payload?.error || payload?.detail`), and `action` names
/// what to do instead — `use_agent`, beside `load_model`'s precedent.
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

/// `chat.file_generation_failed`, with what was tried attached.
///
/// The message is the catalog's, unchanged — a client that matched on it before
/// still matches. `attempts` is additive: it is the difference between "the file
/// content could not be generated" and knowing the model was asked twice and
/// refused twice.
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

/// The catalog's "the file content could not be generated", in one place so the
/// JSON body and the stream's error frame cannot drift apart.
fn generation_failed_text(lang: &str) -> String {
    lattice_core::messages::text("chat.file_generation_failed", lang, &[])
}

/// The frames a streaming turn ends with.
///
/// A refusal here is not an HTTP status: the 200 and its `text/event-stream`
/// header were sent when the stream opened, before the model was asked. So the
/// refusal is a frame — carrying the same wording the JSON body would have
/// carried, in the same language — followed by the sentinel every client waits
/// for. A stream that just stopped would leave the SPA spinning.
async fn turn_frames(sink: &FrameSink, outcome: Outcome, model: &str, lang: &str) {
    match outcome {
        Outcome::Written { answer, payload } => {
            // One send of the shared builder, so a streamed file action is the
            // same three frames whether or not a model wrote the file.
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

/// Attach the file each attempt was for, so a bundle's trail stays readable.
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

/// path → the manifest's line about that file.
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
