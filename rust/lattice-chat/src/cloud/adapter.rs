use std::time::Duration;

use serde_json::{json, Map, Value};

use crate::boundary::NetworkMode;

use super::{
    CLOUD_API_KEY_ENV, CLOUD_BASE_URL_ENV, CLOUD_MODEL_ENV, DEFAULT_CLOUD_BASE_URL,
    DEFAULT_CLOUD_MODEL,
};

// ── egress audit ────────────────────────────────────────────────────────────

/// Where the "knowledge left the machine" record goes.
///
/// Injected rather than a process singleton: the audit sink belongs to the
/// gateway (`append_audit_event`), and a service that reaches for a global
/// cannot be tested against both the bound and the unbound branch.
pub trait EgressAudit: Send + Sync {
    /// Record one event. Must not raise: a failing sink never blocks a turn.
    fn record(&self, event: &Value);
}

/// `record_cloud_egress` — about *shape*, never content.
///
/// The compact payload text is deliberately absent: writing the outbound
/// knowledge into a second on-disk location to prove we were careful with it
/// would be its own leak.
#[allow(clippy::too_many_arguments)]
pub fn cloud_egress_event(
    node_ids: &[String],
    token_estimate: i64,
    mode: NetworkMode,
    provider: &str,
    model: Option<&str>,
    user_email: Option<&str>,
    workspace_id: Option<&str>,
    outcome: &str,
    detail: Option<&str>,
    reason: Option<&str>,
) -> Value {
    let mut event = Map::new();
    event.insert("event".into(), json!("cloud_egress"));
    event.insert("outcome".into(), json!(outcome));
    event.insert("mode".into(), json!(mode.as_str()));
    event.insert("provider".into(), json!(provider));
    event.insert("model".into(), json!(model));
    event.insert("node_ids".into(), json!(node_ids));
    event.insert("node_count".into(), json!(node_ids.len()));
    event.insert("token_estimate".into(), json!(token_estimate));
    event.insert("user_email".into(), json!(user_email));
    event.insert("workspace_id".into(), json!(workspace_id));
    if let Some(detail) = detail.filter(|detail| !detail.is_empty()) {
        event.insert("detail".into(), json!(detail));
    }
    if let Some(reason) = reason.filter(|reason| !reason.is_empty()) {
        event.insert("reason".into(), json!(reason));
    }
    Value::Object(event)
}

// ── the cloud adapter ───────────────────────────────────────────────────────

/// One completed cloud turn, ready for local KG expansion.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct CloudTurnResult {
    pub user_message: String,
    pub answer_text: String,
    pub sent_node_ids: Vec<String>,
    pub provider: String,
    pub model: String,
}

/// `OpenAICompatibleAdapter` — a minimal Chat Completions streaming client.
///
/// Configuration is entirely environment-driven so no secrets land in the repo.
/// A missing key is a clear error rather than a silent failure, so the lane can
/// say so honestly instead of streaming an empty answer.
#[derive(Debug, Clone)]
pub struct OpenAiCompatibleAdapter {
    api_key: String,
    base_url: String,
    default_model: String,
    client: reqwest::Client,
}

impl OpenAiCompatibleAdapter {
    /// `provider_name` — the literal the Python class carries.
    pub const PROVIDER_NAME: &'static str = "openai_compatible";

    /// Build from the environment, like `OpenAICompatibleAdapter()`.
    pub fn from_env(client: reqwest::Client) -> Self {
        let read = |name: &str| std::env::var(name).unwrap_or_default().trim().to_string();
        let base_url = read(CLOUD_BASE_URL_ENV);
        let model = read(CLOUD_MODEL_ENV);
        Self::from_parts(
            read(CLOUD_API_KEY_ENV),
            if base_url.is_empty() {
                DEFAULT_CLOUD_BASE_URL.to_string()
            } else {
                base_url
            },
            if model.is_empty() {
                DEFAULT_CLOUD_MODEL.to_string()
            } else {
                model
            },
        )
        .with_client(client)
    }

    /// Build from already-resolved parts (the provider file / env snapshot).
    pub fn from_parts(
        api_key: impl Into<String>,
        base_url: impl Into<String>,
        model: impl Into<String>,
    ) -> Self {
        Self {
            api_key: api_key.into(),
            base_url: base_url.into(),
            default_model: model.into(),
            client: reqwest::Client::new(),
        }
    }

    /// Replace the HTTP client (the gateway's pooled one, in production).
    pub fn with_client(mut self, client: reqwest::Client) -> Self {
        self.client = client;
        self
    }

    /// Point the adapter at a specific endpoint (the tests' seam).
    pub fn with_base_url(mut self, base_url: impl Into<String>) -> Self {
        self.base_url = base_url.into();
        self
    }

    /// Supply the key explicitly rather than through the environment.
    pub fn with_api_key(mut self, api_key: impl Into<String>) -> Self {
        self.api_key = api_key.into();
        self
    }

    /// Name the default model explicitly rather than through the environment.
    pub fn with_model(mut self, model: impl Into<String>) -> Self {
        self.default_model = model.into();
        self
    }

    /// The model this adapter falls back to.
    pub fn default_model(&self) -> &str {
        &self.default_model
    }

    /// Whether a call could even be made.
    pub fn configured(&self) -> bool {
        !self.api_key.is_empty()
    }

    /// Live `GET {base}/models` with the key. No completion, no billing.
    ///
    /// Fail-closed: an unreachable provider or a rejected key is an error,
    /// never a silent "configured". Tests point this at a loopback mock.
    pub async fn probe_models(&self) -> Result<Vec<String>, String> {
        if !self.configured() {
            return Err("The API key is empty.".into());
        }
        let url = format!("{}/models", self.base_url.trim_end_matches('/'));
        let request = self
            .client
            .get(&url)
            .header("authorization", format!("Bearer {}", self.api_key))
            .header("content-type", "application/json")
            .send();
        let response = tokio::time::timeout(Duration::from_secs(3), request)
            .await
            .map_err(|_| "cloud provider unreachable: timed out".to_string())?
            .map_err(|error| format!("cloud provider unreachable: {error}"))?;
        if !response.status().is_success() {
            return Err(format!(
                "cloud provider answered {}",
                response.status().as_u16()
            ));
        }
        let bytes = response
            .bytes()
            .await
            .map_err(|error| format!("cloud /models body failed: {error}"))?;
        let body: Value = serde_json::from_slice(&bytes)
            .map_err(|error| format!("cloud /models was not JSON: {error}"))?;
        let ids = body
            .get("data")
            .and_then(Value::as_array)
            .map(|rows| {
                rows.iter()
                    .filter_map(|row| row.get("id").and_then(Value::as_str))
                    .map(str::to_string)
                    .collect::<Vec<_>>()
            })
            .or_else(|| {
                body.get("models").and_then(Value::as_array).map(|rows| {
                    rows.iter()
                        .filter_map(|row| {
                            row.as_str()
                                .or_else(|| row.get("id").and_then(Value::as_str))
                        })
                        .map(str::to_string)
                        .collect()
                })
            })
            .unwrap_or_default();
        if ids.is_empty() {
            return Err("cloud /models listed no models".into());
        }
        Ok(ids)
    }

    /// The `messages` array a turn sends.
    pub fn messages(system: &str, user: &str, context: &str) -> Value {
        let mut messages = vec![json!({"role": "system", "content": system})];
        if !context.is_empty() {
            messages.push(json!({
                "role": "system",
                "content": format!(
                    "Local Knowledge Graph context (minimal related nodes only):\n{context}"
                ),
            }));
        }
        messages.push(json!({"role": "user", "content": user}));
        Value::Array(messages)
    }

    /// Stream one completion, handing each text piece to `on_piece`.
    ///
    /// Returns the provider error text on failure. `on_piece` returning `false`
    /// means the client hung up, and the stream stops there.
    pub async fn stream(
        &self,
        system: &str,
        user: &str,
        context: &str,
        model: Option<&str>,
        on_piece: &mut (dyn FnMut(&str) -> bool + Send),
    ) -> Result<(), String> {
        if !self.configured() {
            return Err(
                "Cloud adapter is not configured. Set LATTICEAI_CLOUD_API_KEY \
                 (and optionally LATTICEAI_CLOUD_BASE_URL / LATTICEAI_CLOUD_MODEL)."
                    .to_string(),
            );
        }
        let chosen = model
            .map(str::trim)
            .filter(|model| !model.is_empty())
            .unwrap_or(&self.default_model);
        let body = json!({
            "model": chosen,
            "messages": Self::messages(system, user, context),
            "stream": true,
            "temperature": 0.2,
        });
        let mut response = self
            .client
            .post(format!(
                "{}/chat/completions",
                self.base_url.trim_end_matches('/')
            ))
            .header("authorization", format!("Bearer {}", self.api_key))
            .header("content-type", "application/json")
            .body(serde_json::to_vec(&body).map_err(|error| error.to_string())?)
            .send()
            .await
            .map_err(|error| format!("cloud provider unreachable: {error}"))?;
        if !response.status().is_success() {
            return Err(format!(
                "cloud provider answered {}",
                response.status().as_u16()
            ));
        }
        let mut reader = crate::worker::FrameReader::new();
        loop {
            let chunk = response
                .chunk()
                .await
                .map_err(|error| format!("cloud stream failed: {error}"))?;
            let Some(chunk) = chunk else { break };
            for frame in reader.push_data(&chunk) {
                if !Self::emit(&frame, on_piece) {
                    return Ok(());
                }
            }
        }
        if let Some(frame) = reader.finish_data() {
            Self::emit(&frame, on_piece);
        }
        Ok(())
    }

    /// One provider frame → zero or one text pieces. `false` stops the stream.
    ///
    /// A frame that does not parse, or whose delta carries no `content`, is
    /// skipped: the Python adapter swallows the same shapes (`except: piece = ""`,
    /// `if piece:`) rather than ending the answer on a keep-alive.
    fn emit(
        frame: &crate::worker::DataFrame,
        on_piece: &mut (dyn FnMut(&str) -> bool + Send),
    ) -> bool {
        let crate::worker::DataFrame::Payload(payload) = frame else {
            return false;
        };
        let Ok(parsed) = serde_json::from_str::<Value>(payload) else {
            return true;
        };
        let piece = Self::delta_text(&parsed);
        if piece.is_empty() {
            return true;
        }
        on_piece(&piece)
    }

    /// `choices[0].delta.content` — what a Chat Completions delta carries.
    pub fn delta_text(payload: &Value) -> String {
        payload
            .get("choices")
            .and_then(Value::as_array)
            .and_then(|choices| choices.first())
            .and_then(|choice| choice.get("delta"))
            .and_then(|delta| delta.get("content"))
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string()
    }
}
