use std::time::Duration;

use lattice_core::worker::{SseUpstream, WorkerSeamClient, WorkerSeamError};
use serde_json::{json, Value};

pub const LLM_STREAM_PATH: &str = "/worker/llm/stream";
pub const EMBED_PATH: &str = "/worker/embed";
pub const MODELS_PATH: &str = "/models";

#[derive(Debug, Clone, PartialEq)]
pub struct EmbedReply {
    pub vectors: Vec<Vec<f64>>,
    pub dim: usize,
    pub model_id: String,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct ModelSnapshot {
    pub loaded: Vec<String>,
    pub current: Option<String>,
}

impl ModelSnapshot {
    pub fn is_loaded(&self, model_id: &str) -> bool {
        self.loaded.iter().any(|id| id == model_id)
    }
}

#[derive(Debug, Clone)]
pub struct ChatWorker {
    client: WorkerSeamClient,
}

fn text(value: Option<&Value>) -> Option<String> {
    match value {
        Some(Value::String(text)) if !text.is_empty() => Some(text.clone()),
        _ => None,
    }
}

impl ChatWorker {
    pub fn new(origin: impl AsRef<str>) -> Result<Self, WorkerSeamError> {
        Ok(Self {
            client: WorkerSeamClient::new(origin)?,
        })
    }

    pub fn with_client(client: reqwest::Client, origin: impl AsRef<str>) -> Self {
        Self {
            client: WorkerSeamClient::with_client(client, origin),
        }
    }

    pub fn client(&self) -> &WorkerSeamClient {
        &self.client
    }

    pub fn origin(&self) -> &str {
        self.client.origin()
    }

    pub fn with_timeout(mut self, timeout: Duration) -> Self {
        self.client = self.client.with_timeout(timeout);
        self
    }

    pub async fn models(&self) -> Result<ModelSnapshot, WorkerSeamError> {
        let payload = self.client.get_json(MODELS_PATH).await?;
        Ok(ModelSnapshot {
            loaded: payload
                .get("loaded")
                .and_then(Value::as_array)
                .map(|ids| {
                    ids.iter()
                        .filter_map(|id| id.as_str().map(str::to_string))
                        .collect()
                })
                .unwrap_or_default(),
            current: text(payload.get("current")),
        })
    }

    pub async fn embed(&self, texts: &[String], kind: &str) -> Result<EmbedReply, WorkerSeamError> {
        let body = json!({"texts": texts, "kind": kind});
        let payload = self
            .client
            .post_idempotent_compute(EMBED_PATH, &body)
            .await?;
        Ok(EmbedReply {
            vectors: payload
                .get("vectors")
                .and_then(Value::as_array)
                .map(|rows| {
                    rows.iter()
                        .map(|row| {
                            row.as_array()
                                .map(|values| {
                                    values
                                        .iter()
                                        .filter_map(Value::as_f64)
                                        .collect::<Vec<f64>>()
                                })
                                .unwrap_or_default()
                        })
                        .collect()
                })
                .unwrap_or_default(),
            dim: payload
                .get("dim")
                .and_then(Value::as_u64)
                .unwrap_or_default() as usize,
            model_id: payload
                .get("model_id")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string(),
        })
    }

    #[allow(clippy::too_many_arguments)]
    pub async fn llm_stream(
        &self,
        model_id: Option<&str>,
        message: &str,
        context: &str,
        max_tokens: i64,
        temperature: f64,
        image_data: Option<&str>,
    ) -> Result<SseUpstream, WorkerSeamError> {
        let body = json!({
            "model_id": model_id,
            "message": message,
            "context": context,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "image_data": image_data,
            "mode": "chat",
        });
        self.client.stream_sse(LLM_STREAM_PATH, &body).await
    }

    #[allow(clippy::too_many_arguments)]
    pub async fn document_stream(
        &self,
        model_id: Option<&str>,
        message: &str,
        system_prompt: &str,
        max_tokens: i64,
        temperature: f64,
    ) -> Result<SseUpstream, WorkerSeamError> {
        let body = json!({
            "model_id": model_id,
            "message": message,
            "context": system_prompt,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "image_data": Value::Null,
            "mode": "document",
        });
        self.client.stream_sse(LLM_STREAM_PATH, &body).await
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DataFrame {
    Payload(String),
    Done,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LlmFrame {
    Text(String),
    Error(String),
    Done,
}

#[derive(Debug, Default)]
pub struct FrameReader {
    buffer: String,
}

impl FrameReader {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn push_data(&mut self, chunk: &[u8]) -> Vec<DataFrame> {
        self.buffer.push_str(&String::from_utf8_lossy(chunk));
        let mut frames = Vec::new();
        while let Some(index) = self.buffer.find("\n\n") {
            let raw: String = self.buffer.drain(..index + 2).collect();
            if let Some(frame) = Self::split(&raw) {
                frames.push(frame);
            }
        }
        frames
    }

    pub fn finish_data(&mut self) -> Option<DataFrame> {
        let rest = std::mem::take(&mut self.buffer);
        Self::split(&rest)
    }

    pub fn push(&mut self, chunk: &[u8]) -> Vec<LlmFrame> {
        self.push_data(chunk)
            .into_iter()
            .filter_map(Self::interpret)
            .collect()
    }

    pub fn finish(&mut self) -> Option<LlmFrame> {
        self.finish_data().and_then(Self::interpret)
    }

    fn split(raw: &str) -> Option<DataFrame> {
        let mut data = String::new();
        let mut seen = false;
        for line in raw.lines() {
            let line = line.trim_start();
            if let Some(rest) = line.strip_prefix("data:") {
                if seen {
                    data.push('\n');
                }
                seen = true;
                data.push_str(rest.strip_prefix(' ').unwrap_or(rest));
            }
        }
        if !seen {
            return None;
        }
        if data.trim() == "[DONE]" {
            return Some(DataFrame::Done);
        }
        Some(DataFrame::Payload(data))
    }

    fn interpret(frame: DataFrame) -> Option<LlmFrame> {
        let DataFrame::Payload(payload) = frame else {
            return Some(LlmFrame::Done);
        };
        let parsed: Value = serde_json::from_str(&payload).ok()?;
        if let Some(error) = text(parsed.get("error")) {
            return Some(LlmFrame::Error(error));
        }
        text(parsed.get("text"))
            .or_else(|| text(parsed.get("chunk")))
            .map(LlmFrame::Text)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_embed_reply_survives_a_worker_that_answers_thinly() {
        let reply = EmbedReply {
            vectors: vec![vec![0.5, -0.5]],
            dim: 2,
            model_id: "lattice-local-hash-v1:2".into(),
        };
        assert_eq!(reply.vectors[0][1], -0.5);
        assert!(format!("{reply:?}").contains("model_id"));
        assert_ne!(
            reply,
            EmbedReply {
                vectors: Vec::new(),
                dim: 0,
                model_id: String::new(),
            }
        );
    }

    #[test]
    fn a_model_snapshot_answers_membership() {
        let snapshot = ModelSnapshot {
            loaded: vec!["a".into()],
            current: Some("a".into()),
        };
        assert!(snapshot.is_loaded("a"));
        assert!(!snapshot.is_loaded("b"));
        assert_eq!(ModelSnapshot::default().current, None);
    }

    #[test]
    fn frames_split_on_the_blank_line_and_honour_the_sentinel() {
        let mut reader = FrameReader::new();
        assert!(reader.push(b"data: {\"text\":\"he").is_empty());
        let frames = reader.push(b"llo\"}\n\ndata: [DONE]\n\n");
        assert_eq!(frames, vec![LlmFrame::Text("hello".into()), LlmFrame::Done]);
        assert!(reader.finish().is_none());
    }

    #[test]
    fn raw_frames_keep_their_payload_uninterpreted() {
        let mut reader = FrameReader::new();
        assert_eq!(
            reader.push_data(b"data: {\"choices\":[]}\n\nevent: x\ndata: \n\n"),
            vec![
                DataFrame::Payload("{\"choices\":[]}".into()),
                DataFrame::Payload(String::new()),
            ],
            "an empty data line is still a frame"
        );
        assert!(reader.finish_data().is_none());
    }

    #[test]
    fn malformed_frames_are_skipped_and_errors_surface() {
        let mut reader = FrameReader::new();
        let frames = reader.push(b": comment\n\ndata: not json\n\ndata: {\"error\":\"boom\"}\n\n");
        assert_eq!(frames, vec![LlmFrame::Error("boom".into())]);
        assert!(reader.push(b"data: {\"other\":1}\n\n").is_empty());
        assert_eq!(
            reader.push(b"data: {\"chunk\":\"c\"}\n\n"),
            vec![LlmFrame::Text("c".into())]
        );
    }

    #[test]
    fn a_body_that_ends_without_a_blank_line_still_yields_its_frame() {
        let mut reader = FrameReader::new();
        assert!(reader.push(b"data: {\"text\":\"tail\"}").is_empty());
        assert_eq!(reader.finish(), Some(LlmFrame::Text("tail".into())));
        let mut reader = FrameReader::new();
        assert_eq!(
            reader.push(b"data: {\"text\":\n data: \"two\"}\n\n"),
            vec![LlmFrame::Text("two".into())]
        );
    }

    #[test]
    fn the_client_exposes_its_origin_and_timeout_knob() {
        let worker = ChatWorker::new("http://127.0.0.1:9/").expect("client");
        assert_eq!(worker.origin(), "http://127.0.0.1:9");
        assert_eq!(worker.client().origin(), "http://127.0.0.1:9");
        let worker = worker.with_timeout(Duration::from_secs(3));
        assert_eq!(worker.client().timeout(), Duration::from_secs(3));
        assert!(format!("{worker:?}").contains("ChatWorker"));
    }
}
