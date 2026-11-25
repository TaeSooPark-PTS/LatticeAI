use base64::Engine;
use serde_json::{json, Map, Value};

pub const SEAM_ENV: &str = "LATTICEAI_AGENT_TOOL_SEAM";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Rendered {
    pub content: Vec<u8>,
    pub report: Value,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkerError(pub String);

impl std::fmt::Display for WorkerError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for WorkerError {}

#[derive(Debug, Clone, PartialEq)]
pub enum ToolOutcome {
    Result(Value),
    Error(String),
}

#[derive(Debug, Clone)]
pub struct Completion<'a> {
    pub model_id: Option<&'a str>,
    pub message: &'a str,
    pub context: &'a str,
    pub max_tokens: u32,
    pub temperature: f64,
    pub stop: &'a [&'a str],
    pub prefix: &'a str,
}

#[derive(Debug, Clone)]
pub struct WorkerClient {
    origin: String,
    client: reqwest::Client,
}

impl WorkerClient {
    pub fn new(origin: impl AsRef<str>) -> Self {
        Self::with_client(origin, reqwest::Client::new())
    }

    pub fn with_client(origin: impl AsRef<str>, client: reqwest::Client) -> Self {
        Self {
            origin: origin.as_ref().trim_end_matches('/').to_string(),
            client,
        }
    }

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
        let value = serde_json::from_slice::<Value>(&bytes).unwrap_or(Value::Null);
        Ok((status, value))
    }

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
        if !request.prefix.is_empty() {
            body["prefix"] = json!(request.prefix);
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
            Some(other) => Ok(crate::parse::pystr::py_str(other)),
            None => Err(WorkerError(
                "worker seam /agent/llm returned no text field".into(),
            )),
        }
    }

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
                (None, Some(error)) => ToolOutcome::Error(crate::parse::pystr::py_str(error)),
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

    pub async fn render(&self, kind: &str, body: Value) -> Result<Rendered, String> {
        let path = format!("/worker/render/{kind}");
        let (status, value) = self.post(&path, body).await?;
        if status != 200 {
            return Err(match value.get("error") {
                Some(error) => crate::parse::pystr::py_str(error),
                None => format!(
                    "worker seam {path} answered {status}: {}",
                    detail_of(&value)
                ),
            });
        }
        if let Some(error) = value.get("error").filter(|error| !error.is_null()) {
            return Err(crate::parse::pystr::py_str(error));
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

fn detail_of(value: &Value) -> String {
    match value.get("detail") {
        Some(detail) => crate::parse::pystr::py_str(detail),
        None => crate::parse::pystr::py_str(value),
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
                prefix: "",
            })
            .await
            .is_err());
    }

    #[tokio::test]
    async fn the_optional_fields_are_sent_only_when_they_carry_something() {
        use std::sync::{Arc, Mutex};
        let seen: Arc<Mutex<Vec<Value>>> = Arc::new(Mutex::new(Vec::new()));
        let sink = Arc::clone(&seen);
        let app = axum::Router::new().route(
            "/agent/llm",
            axum::routing::post(move |axum::Json(body): axum::Json<Value>| {
                let sink = Arc::clone(&sink);
                async move {
                    sink.lock().expect("lock").push(body);
                    axum::Json(json!({"text": "ok"}))
                }
            }),
        );
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind");
        let origin = format!("http://{}", listener.local_addr().expect("addr"));
        tokio::spawn(async move {
            let _ = axum::serve(listener, app).await;
        });
        let client = WorkerClient::new(&origin);

        let bare = Completion {
            model_id: None,
            message: "m",
            context: "c",
            max_tokens: 16,
            temperature: 0.1,
            stop: &[],
            prefix: "",
        };
        client.llm(bare.clone()).await.expect("bare");
        client
            .llm(Completion {
                model_id: Some("some/model"),
                stop: &["\n"],
                prefix: "{\"thoughts\": \"",
                ..bare
            })
            .await
            .expect("full");

        let bodies = seen.lock().expect("lock").clone();
        assert_eq!(bodies[0].get("prefix"), None, "an empty prefix is absent");
        assert_eq!(bodies[0].get("stop"), None);
        assert_eq!(bodies[0].get("model_id"), None);
        assert_eq!(bodies[1]["prefix"], json!("{\"thoughts\": \""));
        assert_eq!(bodies[1]["stop"], json!(["\n"]));
        assert_eq!(bodies[1]["model_id"], json!("some/model"));
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
