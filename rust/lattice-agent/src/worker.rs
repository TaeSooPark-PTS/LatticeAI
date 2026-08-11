//! The AI-worker seam: the three things the loop cannot do natively.
//!
//! The kernel decides; the worker infers and mutates. This client is the whole
//! boundary between them, and it is deliberately three calls wide:
//!
//! * `POST /agent/llm` — one completion. No history, no persistence.
//! * `POST /agent/tool` — one governed tool dispatch. The worker keeps its
//!   **mode-invariant** server guards (circuit breaker / destructive → 403,
//!   fail-closed classification → 409), so a preflight bug here cannot become a
//!   write there.
//! * `POST /agent/change-proposal` — the proposal-first path under `strict`.
//!
//! Every failure is an *outcome*, not a panic: a worker that is down, slow or
//! shouting 403 produces a transcript step that says so. The one exception is
//! the LLM call, whose failure ends the run — there is no honest way to
//! continue a reasoning loop without the reasoner.

use serde_json::{json, Map, Value};

/// The seam is off unless the host injects this into its own worker.
pub const SEAM_ENV: &str = "LATTICEAI_AGENT_TOOL_SEAM";

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

    /// `POST /agent/change-proposal` → the governor verdict, or `None` when the
    /// governor could not be consulted (which falls through to the gates).
    pub async fn change_proposal(
        &self,
        tool: &str,
        args: &Map<String, Value>,
        policy: &Value,
        workspace_id: Option<&str>,
        conversation_id: Option<&str>,
    ) -> Option<Value> {
        let mut body = json!({
            "tool": tool,
            "args": Value::Object(args.clone()),
            "policy": policy.clone(),
        });
        if let Some(workspace_id) = workspace_id {
            body["workspace_id"] = json!(workspace_id);
        }
        if let Some(conversation_id) = conversation_id {
            body["conversation_id"] = json!(conversation_id);
        }
        match self.post("/agent/change-proposal", body).await {
            Ok((200, value)) if value.is_object() => Some(value),
            _ => None,
        }
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
            })
            .await
            .is_err());
        assert_eq!(
            client
                .change_proposal("write_file", &Map::new(), &json!({}), None, None)
                .await,
            None,
            "a governor that cannot be reached falls through to the gates"
        );
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
