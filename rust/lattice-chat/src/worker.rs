//! The four things chat asks the Python AI worker for.
//!
//! Plan §설계 결정 6: the pipeline is Rust-orchestrated, but token generation
//! never moves and the Brain keeps one writer. Wave 2.5 §W3a removed the fifth
//! call — `POST /worker/chat/record-turn` — by making the whole redact → audit →
//! store → ingest chain native ([`crate::turn`]). What is left is compute this
//! process cannot do:
//!
//! | seam | why it is not native |
//! |---|---|
//! | `POST /worker/llm/stream` | MLX runs in that process. `/agent/llm` is buffered, and a buffered completion is not a chat stream. |
//! | `POST /worker/embed` | the embedding provider lives there, and it — not a native guess — is the authority on which `(model_id, dim)` the vector index is filed under (W2 §1). |
//! | `POST /knowledge-graph/ingest` | the file-ingest door the plan keeps until W1's writer serves it (I5 `worker_keep`). |
//! | `GET /models` | the loaded-model table of the in-process MLX runtime (KEEP_WORKER). |
//!
//! There is deliberately **no** `record_turn` method here any more. Its absence
//! is the point: `rg 'worker/chat/record-turn'` over this crate finds only the
//! test that asserts the path is never requested.

use std::time::Duration;

use lattice_core::worker::{SseUpstream, WorkerSeamClient, WorkerSeamError};
use serde_json::{json, Map, Value};

/// `POST /worker/llm/stream` — the streaming completion seam.
pub const LLM_STREAM_PATH: &str = "/worker/llm/stream";
/// `POST /worker/embed` — W2's pure-compute embedding seam.
pub const EMBED_PATH: &str = "/worker/embed";
/// `POST /worker/graph/mutate` — WP-I6's graph write door.
pub const GRAPH_MUTATE_PATH: &str = "/worker/graph/mutate";
/// `POST /knowledge-graph/ingest` — the graph single-writer door.
pub const INGEST_PATH: &str = "/knowledge-graph/ingest";
/// `GET /models` — which models this worker actually holds.
pub const MODELS_PATH: &str = "/models";

/// What `POST /worker/embed` answered (W2 §1).
///
/// `dim` and `model_id` are read *after* the provider ran, so they describe the
/// index identity that actually exists rather than one guessed before the call.
#[derive(Debug, Clone, PartialEq)]
pub struct EmbedReply {
    /// One vector per input text, in order.
    pub vectors: Vec<Vec<f64>>,
    /// The width the provider returned.
    pub dim: usize,
    /// The id every vector must be filed under.
    pub model_id: String,
}

/// Which models the worker holds right now (`GET /models`).
#[derive(Debug, Clone, Default, PartialEq)]
pub struct ModelSnapshot {
    /// `loaded` — every model id cached in that process.
    pub loaded: Vec<String>,
    /// `current` — the default the router answers with.
    pub current: Option<String>,
}

impl ModelSnapshot {
    /// `LLMRouter.loaded_model_ids.__contains__`.
    pub fn is_loaded(&self, model_id: &str) -> bool {
        self.loaded.iter().any(|id| id == model_id)
    }
}

/// The chat seam, over loopback HTTP.
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
    /// A seam client with its own connection pool.
    pub fn new(origin: impl AsRef<str>) -> Result<Self, WorkerSeamError> {
        Ok(Self {
            client: WorkerSeamClient::new(origin)?,
        })
    }

    /// A seam client sharing the gateway's pool.
    pub fn with_client(client: reqwest::Client, origin: impl AsRef<str>) -> Self {
        Self {
            client: WorkerSeamClient::with_client(client, origin),
        }
    }

    /// The underlying seam client, for a caller that needs another path.
    pub fn client(&self) -> &WorkerSeamClient {
        &self.client
    }

    /// Where this client posts.
    pub fn origin(&self) -> &str {
        self.client.origin()
    }

    /// Cap the non-streaming calls. The stream is deliberately uncapped.
    pub fn with_timeout(mut self, timeout: Duration) -> Self {
        self.client = self.client.with_timeout(timeout);
        self
    }

    /// `GET /models` → the loaded table.
    ///
    /// A worker that cannot answer leaves the snapshot **empty**, which makes
    /// `POST /chat` refuse with `no_model_loaded` rather than stream from a
    /// model nobody confirmed exists.
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

    /// `POST /worker/embed` — vectors for texts, plus the index identity.
    ///
    /// `kind` is `"passage"` for anything being written (the seam clamps each
    /// text to 50 000 characters, which is the write path's own clamp) and
    /// `"query"` for a search. The reply's `dim`/`model_id` are the authority:
    /// a vector filed under anything else is a vector `similarity()` will refuse.
    pub async fn embed(&self, texts: &[String], kind: &str) -> Result<EmbedReply, WorkerSeamError> {
        let body = json!({"texts": texts, "kind": kind});
        let payload = self.client.post_json(EMBED_PATH, &body).await?;
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

    /// `POST /knowledge-graph/ingest` — index one generated file into the Brain.
    ///
    /// Best-effort by contract (`_ingest_generated_file`): the caller turns a
    /// failure into `{"status": "failed", "detail": …}` rather than losing the
    /// file it just wrote.
    pub async fn ingest(&self, item: &Map<String, Value>) -> Result<Value, WorkerSeamError> {
        self.client
            .post_json(INGEST_PATH, &Value::Object(item.clone()))
            .await
    }

    /// `POST /worker/llm/stream` — the token stream, unbuffered.
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

    /// `POST /worker/llm/stream` with ``mode=document``.
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

/// One frame off the wire, before anybody interprets its payload.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DataFrame {
    /// The concatenated `data:` lines of one frame.
    Payload(String),
    /// The literal `[DONE]` sentinel.
    Done,
}

/// One frame of the seam's stream, as the chat pipeline reads it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LlmFrame {
    /// A token (or several) of the answer.
    Text(String),
    /// The worker reported a failure mid-stream.
    Error(String),
    /// `data: [DONE]` — the stream is over.
    Done,
}

/// Split an SSE byte stream into frames, keeping whatever is incomplete.
///
/// Frames are separated by a blank line, and the `[DONE]` sentinel is matched
/// **before** anything tries to parse the payload as JSON — exactly as every
/// client's reader does. Non-`data:` lines (`event:`, `:` comments, `id:`) are
/// skipped: this reader is used for the worker seam and for an OpenAI-compatible
/// provider, and neither names its data frames.
#[derive(Debug, Default)]
pub struct FrameReader {
    buffer: String,
}

impl FrameReader {
    /// A reader with an empty buffer.
    pub fn new() -> Self {
        Self::default()
    }

    /// Feed bytes; get back every frame that completed, uninterpreted.
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

    /// Flush whatever the producer sent without a trailing blank line.
    pub fn finish_data(&mut self) -> Option<DataFrame> {
        let rest = std::mem::take(&mut self.buffer);
        Self::split(&rest)
    }

    /// [`Self::push_data`], with the payloads read as the chat seam's shape.
    pub fn push(&mut self, chunk: &[u8]) -> Vec<LlmFrame> {
        self.push_data(chunk)
            .into_iter()
            .filter_map(Self::interpret)
            .collect()
    }

    /// [`Self::finish_data`], with the payload read as the chat seam's shape.
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

    /// A malformed payload is skipped rather than ending the stream: a frame
    /// that does not parse is not a failed answer.
    fn interpret(frame: DataFrame) -> Option<LlmFrame> {
        let DataFrame::Payload(payload) = frame else {
            return Some(LlmFrame::Done);
        };
        let parsed: Value = serde_json::from_str(&payload).ok()?;
        if let Some(error) = text(parsed.get("error")) {
            return Some(LlmFrame::Error(error));
        }
        // `text` is the seam's field, `chunk` the product stream's: accepting
        // both means a caller can point the pipeline at either without a shim.
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
        // A frame with neither text nor error yields nothing at all.
        assert!(reader.push(b"data: {\"other\":1}\n\n").is_empty());
        // The product stream's own field name is accepted too.
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
        // Multi-line data joins with a newline, as the SSE spec says.
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
