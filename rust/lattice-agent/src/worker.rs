//! The AI-worker seam: the things the loop cannot do natively.
//!
//! The kernel decides and (since v11.6.0's WP-W4) writes; the worker infers and
//! computes. This client is the whole boundary between them, and it is
//! deliberately three calls wide:
//!
//! * `POST /agent/llm` — one completion. No history, no persistence.
//! * `POST /agent/tool` — one governed tool dispatch, for the **compute-only**
//!   handlers. The worker keeps its mode-invariant server guards (circuit
//!   breaker / destructive → 403, fail-closed classification → 409), so a
//!   preflight bug here cannot become a write there. Every mutating handler is
//!   native now ([`crate::tools`]) and never reaches this call.
//! * `POST /worker/render/{docx,xlsx,pptx,pdf}` — the document builders, which
//!   need python-docx / openpyxl / python-pptx / reportlab. They return **bytes**
//!   and this side writes the file, so document creation is a compute call with
//!   a native write rather than a worker-side write.
//!
//! `POST /agent/change-proposal` was the fourth until v11.6.0 §P1a retired it
//! from the worker; staging a proposal is [`crate::proposals`]' job now, in
//! this process, so a run's *decisions* and the record of the ones it declined
//! to make are both native.
//!
//! Every failure is an *outcome*, not a panic: a worker that is down, slow or
//! shouting 403 produces a transcript step that says so. The one exception is
//! the LLM call, whose failure ends the run — there is no honest way to
//! continue a reasoning loop without the reasoner.

use base64::Engine;
use serde_json::{json, Map, Value};

/// The seam is off unless the host injects this into its own worker.
pub const SEAM_ENV: &str = "LATTICEAI_AGENT_TOOL_SEAM";

/// One document the worker built: its bytes, and what it said about them.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Rendered {
    /// The file, decoded.
    pub content: Vec<u8>,
    /// The whole 200 body. W2 §3 puts `filename`, `bytes` and the per-kind
    /// counts (`rows`, `slides`) at the top level; a nested `meta` object is
    /// honoured too, so the caller reads one shape either way.
    pub report: Value,
}

/// A completion could not be obtained.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkerError(pub String);

impl std::fmt::Display for WorkerError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for WorkerError {}

/// What one `/agent/tool` call came back as.
#[derive(Debug, Clone, PartialEq)]
pub enum ToolOutcome {
    /// `{"result": {...}}` — the tool ran.
    Result(Value),
    /// `{"error": "..."}`, a 403/409 guard, or a transport failure. The loop
    /// records the message on the step exactly as Python records `str(exc)`.
    Error(String),
}

/// One completion request, mirroring `LLMRouter.generate_as`'s arguments.
#[derive(Debug, Clone)]
pub struct Completion<'a> {
    pub model_id: Option<&'a str>,
    pub message: &'a str,
    pub context: &'a str,
    pub max_tokens: u32,
    pub temperature: f64,
    /// Strings that end generation, sent only when non-empty (v11.9.0).
    ///
    /// Deliberately **off by default**. A stop string is a knife: `"\n\n"` ends
    /// a rambling model's chatter and also truncates a `content` field the
    /// moment it contains a blank line, which is every HTML document. The loop
    /// uses it in exactly one place — the strict verify re-ask, where the reply
    /// is one short verdict object and a model that keeps talking after it has
    /// already cost the run its only retry.
    pub stop: &'a [&'a str],
}

/// The worker seam, over loopback HTTP.
#[derive(Debug, Clone)]
pub struct WorkerClient {
    origin: String,
    client: reqwest::Client,
}

impl WorkerClient {
    /// A client with its own connection pool.
    pub fn new(origin: impl AsRef<str>) -> Self {
        Self::with_client(origin, reqwest::Client::new())
    }

    /// A client sharing an existing pool (the host's, so loopback connections
    /// are not duplicated).
    pub fn with_client(origin: impl AsRef<str>, client: reqwest::Client) -> Self {
        Self {
            origin: origin.as_ref().trim_end_matches('/').to_string(),
            client,
        }
    }

    /// Where this client posts.
    pub fn origin(&self) -> &str {
        &self.origin
    }

    async fn post(&self, path: &str, body: Value) -> Result<(u16, Value), String> {
        let payload = serde_json::to_vec(&body).map_err(|err| err.to_string())?;
        let response = self
            .client
            .post(format!("{}{path}", self.origin))
            .header("content-type", "application/json")
            .body(payload)
            .send()
            .await
            .map_err(|err| format!("worker seam {path} unreachable: {err}"))?;
        let status = response.status().as_u16();
        let bytes = response
            .bytes()
            .await
            .map_err(|err| format!("worker seam {path} response unreadable: {err}"))?;
        // A worker that answers HTML or nothing is a failure with a body, not a
        // parse panic: the status still carries the decision.
        let value = serde_json::from_slice::<Value>(&bytes).unwrap_or(Value::Null);
        Ok((status, value))
    }

    /// `POST /agent/llm` → the completion text.
    pub async fn llm(&self, request: Completion<'_>) -> Result<String, WorkerError> {
        let mut body = json!({
            "message": request.message,
            "context": request.context,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        });
        if let Some(model_id) = request.model_id {
            body["model_id"] = json!(model_id);
        }
        if !request.stop.is_empty() {
            body["stop"] = json!(request.stop);
        }
        let (status, value) = self.post("/agent/llm", body).await.map_err(WorkerError)?;
        if status != 200 {
            return Err(WorkerError(format!(
                "worker seam /agent/llm answered {status}: {}",
                detail_of(&value)
            )));
        }
        match value.get("text") {
            Some(Value::String(text)) => Ok(text.clone()),
            Some(other) => Ok(crate::pystr::py_str(other)),
            None => Err(WorkerError(
                "worker seam /agent/llm returned no text field".into(),
            )),
        }
    }

    /// `POST /agent/tool` → the dispatch result, or the refusal.
    pub async fn tool(
        &self,
        tool: &str,
        args: &Map<String, Value>,
        workspace_id: Option<&str>,
    ) -> ToolOutcome {
        let mut body = json!({"tool": tool, "args": Value::Object(args.clone())});
        if let Some(workspace_id) = workspace_id {
            body["workspace_id"] = json!(workspace_id);
        }
        match self.post("/agent/tool", body).await {
            Err(transport) => ToolOutcome::Error(transport),
            Ok((200, value)) => match (value.get("result"), value.get("error")) {
                (Some(result), _) => ToolOutcome::Result(result.clone()),
                (None, Some(error)) => ToolOutcome::Error(crate::pystr::py_str(error)),
                _ => ToolOutcome::Error(format!(
                    "worker seam /agent/tool returned neither result nor error for '{tool}'"
                )),
            },
            Ok((status, value)) => ToolOutcome::Error(format!(
                "worker seam /agent/tool answered {status} for '{tool}': {}",
                detail_of(&value)
            )),
        }
    }

    /// `POST /worker/render/{kind}` → the document bytes the builder produced.
    ///
    /// The request body is the tool handler's own argument schema — W2 §3 binds
    /// it field for field to `ToolDocxRequest` and friends, so the builder is
    /// the same code reading the same fields. The response carries the file as
    /// base64 under `content_b64` (`bytes_b64` is accepted as an alias) and its
    /// counts beside it. A refusal — `{"error": …}`, or a non-2xx such as W2's
    /// `503 worker_compute.render_unavailable` — comes back as the message, so
    /// "this worker cannot render 'xlsx'" reaches the transcript.
    pub async fn render(&self, kind: &str, body: Value) -> Result<Rendered, String> {
        let path = format!("/worker/render/{kind}");
        let (status, value) = self.post(&path, body).await?;
        if status != 200 {
            return Err(match value.get("error") {
                Some(error) => crate::pystr::py_str(error),
                None => format!(
                    "worker seam {path} answered {status}: {}",
                    detail_of(&value)
                ),
            });
        }
        if let Some(error) = value.get("error").filter(|error| !error.is_null()) {
            return Err(crate::pystr::py_str(error));
        }
        let encoded = ["content_b64", "bytes_b64"]
            .iter()
            .find_map(|key| value.get(*key).and_then(Value::as_str))
            .ok_or_else(|| format!("worker seam {path} returned no document bytes"))?;
        let content = base64::engine::general_purpose::STANDARD
            .decode(encoded)
            .map_err(|error| format!("worker seam {path} returned unreadable bytes: {error}"))?;
        Ok(Rendered {
            content,
            report: value,
        })
    }
}

/// FastAPI puts its refusal text in `detail`; anything else is shown raw.
fn detail_of(value: &Value) -> String {
    match value.get("detail") {
        Some(detail) => crate::pystr::py_str(detail),
        None => crate::pystr::py_str(value),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_origin_is_normalised_once() {
        assert_eq!(
            WorkerClient::new("http://127.0.0.1:4825/").origin(),
            "http://127.0.0.1:4825"
        );
        assert_eq!(
            WorkerClient::new("http://127.0.0.1:4825").origin(),
            "http://127.0.0.1:4825"
        );
    }

    #[tokio::test]
    async fn an_unreachable_worker_is_an_outcome_not_a_panic() {
        // Port 1 on loopback refuses immediately; nothing is listening.
        let client = WorkerClient::new("http://127.0.0.1:1");
        let outcome = client.tool("write_file", &Map::new(), None).await;
        match outcome {
            ToolOutcome::Error(message) => assert!(message.contains("unreachable"), "{message}"),
            other => panic!("expected an error outcome, got {other:?}"),
        }
        assert!(client
            .llm(Completion {
                model_id: None,
                message: "m",
                context: "c",
                max_tokens: 16,
                temperature: 0.1,
                stop: &[],
            })
            .await
            .is_err());
    }

    #[test]
    fn a_fastapi_detail_is_preferred_over_the_raw_body() {
        assert_eq!(detail_of(&json!({"detail": "blocked"})), "blocked");
        assert_eq!(detail_of(&json!({"other": 1})), "{'other': 1}");
        assert_eq!(detail_of(&Value::Null), "None");
    }

    #[test]
    fn the_seam_env_name_is_the_one_the_host_injects() {
        assert_eq!(SEAM_ENV, "LATTICEAI_AGENT_TOOL_SEAM");
    }
}
