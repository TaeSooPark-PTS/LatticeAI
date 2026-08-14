//! The worker seam, from the gateway's side.
//!
//! v11.6.0 inverts the topology: `lattice-host` becomes the product server and
//! the Python worker keeps only what it alone can do — inference, tool-handler
//! execution and graph writes, proposal staging, the document parser matrix,
//! embedding production. Everything a Rust route needs from that box goes over
//! loopback HTTP, and this is the client every crate uses to make the call.
//!
//! It is deliberately shapeless: `post_json`, `get_json`, `stream_sse`. The
//! typed seams that already exist — `lattice_agent::worker::WorkerClient` for
//! `/agent/{llm,tool,change-proposal}`, `lattice_ingest::worker::WorkerClient`
//! for `/knowledge-graph/ingest` — encode one contract each and keep it. This
//! carries the rest (WP-I6's history/graph/sysinfo seams, `/models`,
//! `/engines/prepare-model/stream`, `/api/index/*`) without inventing a type
//! per endpoint before the endpoint exists.
//!
//! ## Deliberate non-features
//!
//! * **No retries.** A drain tick backs off, a chat turn fails the turn, an
//!   admin read shows an error — three different right answers, none of which
//!   this layer can pick. `lattice_jobs::tick` already documents its own.
//! * **No authentication of its own.** The worker's routes call
//!   `require_user`, so a deployment with auth on must supply the credential;
//!   [`WorkerSeamClient::with_header`] is how, and nothing here invents one.
//! * **No JSON feature on reqwest.** The workspace pins reqwest without it (the
//!   proxy streams bytes and never deserialises), so bodies are serialised and
//!   parsed with `serde_json` by hand — the same way `lattice-jobs` and
//!   `lattice-ingest` do it.

use std::collections::BTreeMap;
use std::time::Duration;

use bytes::Bytes;
use futures_core::Stream;
use serde_json::Value;

/// How long a request/response round trip may take before it is abandoned.
///
/// Matches `lattice_ingest::worker::DEFAULT_TIMEOUT`: the slowest thing behind
/// this seam is a document parse or an embedding batch, and both are seconds,
/// not minutes.
pub const DEFAULT_TIMEOUT: Duration = Duration::from_secs(30);

/// How long the connect phase may take.
///
/// Short, because the worker is on loopback: a connect that has not completed
/// in five seconds is a worker that is not there, and waiting the full request
/// timeout to learn that just delays the honest answer.
pub const DEFAULT_CONNECT_TIMEOUT: Duration = Duration::from_secs(5);

/// Bytes of an error body kept for the message. Enough to read a FastAPI
/// `detail`, short enough for a log line.
const ERROR_BODY_CHARS: usize = 400;

/// Why a seam call did not produce an answer.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum WorkerSeamError {
    /// The HTTP client could not be built.
    Client(String),
    /// The request never completed — worker down, or the timeout elapsed.
    Transport { path: String, detail: String },
    /// The worker answered, and said no.
    Rejected {
        /// The seam path asked for.
        path: String,
        /// HTTP status.
        status: u16,
        /// Body, truncated.
        detail: String,
    },
    /// The worker answered 2xx with something that was not JSON.
    Malformed { path: String, detail: String },
}

impl WorkerSeamError {
    /// The status the worker answered, when it answered at all.
    ///
    /// Callers that mirror the worker's refusal back to their own client (a
    /// 403 for a 403 rather than a blanket 502) need this, and reading it out
    /// of the message would be parsing our own prose.
    pub fn status(&self) -> Option<u16> {
        match self {
            WorkerSeamError::Rejected { status, .. } => Some(*status),
            _ => None,
        }
    }

    /// The seam path this failure is about, when it is about one.
    pub fn path(&self) -> Option<&str> {
        match self {
            WorkerSeamError::Client(_) => None,
            WorkerSeamError::Transport { path, .. }
            | WorkerSeamError::Rejected { path, .. }
            | WorkerSeamError::Malformed { path, .. } => Some(path),
        }
    }
}

impl std::fmt::Display for WorkerSeamError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            WorkerSeamError::Client(detail) => write!(formatter, "worker client: {detail}"),
            WorkerSeamError::Transport { path, detail } => {
                write!(formatter, "worker seam {path} unreachable: {detail}")
            }
            WorkerSeamError::Rejected {
                path,
                status,
                detail,
            } => write!(formatter, "worker seam {path} answered {status}: {detail}"),
            WorkerSeamError::Malformed { path, detail } => {
                write!(formatter, "worker seam {path} answered non-JSON: {detail}")
            }
        }
    }
}

impl std::error::Error for WorkerSeamError {}

/// An HTTP client pointed at one worker origin.
///
/// Cheap to clone — the underlying `reqwest::Client` is an `Arc` around a
/// connection pool, so cloning shares the pool rather than opening a second one.
#[derive(Debug, Clone)]
pub struct WorkerSeamClient {
    client: reqwest::Client,
    origin: String,
    headers: BTreeMap<String, String>,
    timeout: Duration,
}

impl WorkerSeamClient {
    /// A client with its own connection pool.
    ///
    /// `no_proxy()` is load-bearing for the same reason it is in the host: a
    /// machine-wide `HTTP_PROXY` must never intercept loopback traffic to our
    /// own worker.
    pub fn new(origin: impl AsRef<str>) -> Result<Self, WorkerSeamError> {
        let client = reqwest::Client::builder()
            .no_proxy()
            .connect_timeout(DEFAULT_CONNECT_TIMEOUT)
            .build()
            .map_err(|error| WorkerSeamError::Client(error.to_string()))?;
        Ok(Self::with_client(client, origin))
    }

    /// A client reusing an existing pool — the gateway's, so loopback
    /// connections are not duplicated per crate.
    pub fn with_client(client: reqwest::Client, origin: impl AsRef<str>) -> Self {
        Self {
            client,
            origin: origin.as_ref().trim().trim_end_matches('/').to_string(),
            headers: BTreeMap::new(),
            timeout: DEFAULT_TIMEOUT,
        }
    }

    /// Attach a header to every call — the auth seam.
    pub fn with_header(mut self, name: impl Into<String>, value: impl Into<String>) -> Self {
        self.headers.insert(name.into(), value.into());
        self
    }

    /// Change the per-request timeout.
    ///
    /// It applies to [`WorkerSeamClient::post_json`] and
    /// [`WorkerSeamClient::get_json`] only — see [`WorkerSeamClient::stream_sse`].
    pub fn with_timeout(mut self, timeout: Duration) -> Self {
        self.timeout = timeout;
        self
    }

    /// The worker origin, without a trailing slash.
    pub fn origin(&self) -> &str {
        &self.origin
    }

    /// The per-request timeout in force.
    pub fn timeout(&self) -> Duration {
        self.timeout
    }

    /// The absolute URL for a seam path.
    pub fn url(&self, path: &str) -> String {
        if path.starts_with('/') {
            format!("{}{path}", self.origin)
        } else {
            format!("{}/{path}", self.origin)
        }
    }

    /// `POST {origin}{path}` with a JSON body, answered as JSON.
    pub async fn post_json(&self, path: &str, body: &Value) -> Result<Value, WorkerSeamError> {
        let payload =
            serde_json::to_vec(body).map_err(|error| WorkerSeamError::Client(error.to_string()))?;
        let request = self
            .request(reqwest::Method::POST, path)
            .header("content-type", "application/json")
            .timeout(self.timeout)
            .body(payload);
        read_json(request, path).await
    }

    /// `GET {origin}{path}`, answered as JSON.
    ///
    /// `path` may carry a query string; it is forwarded verbatim, so the caller
    /// owns escaping exactly as it does when it builds any other URL.
    pub async fn get_json(&self, path: &str) -> Result<Value, WorkerSeamError> {
        let request = self
            .request(reqwest::Method::GET, path)
            .timeout(self.timeout);
        read_json(request, path).await
    }

    /// `POST {origin}{path}` and hand back the response before its body is read.
    ///
    /// **No total timeout is applied.** An SSE response is meant to stay open;
    /// `reqwest`'s per-request timeout covers the whole exchange *including*
    /// the body, so setting it here would cut a healthy stream off at 30 s. The
    /// connect timeout still bounds "is the worker there at all", and the
    /// caller ends the stream by dropping it.
    ///
    /// A non-2xx status is still an error — a 401 arriving as an event stream
    /// would otherwise reach the browser as an empty, successful-looking chat.
    pub async fn stream_sse(
        &self,
        path: &str,
        body: &Value,
    ) -> Result<SseUpstream, WorkerSeamError> {
        let payload =
            serde_json::to_vec(body).map_err(|error| WorkerSeamError::Client(error.to_string()))?;
        let response = self
            .request(reqwest::Method::POST, path)
            .header("content-type", "application/json")
            .header("accept", "text/event-stream")
            .body(payload)
            .send()
            .await
            .map_err(|error| WorkerSeamError::Transport {
                path: path.to_string(),
                detail: error.to_string(),
            })?;
        let status = response.status();
        if !status.is_success() {
            let detail = response.text().await.unwrap_or_default();
            return Err(WorkerSeamError::Rejected {
                path: path.to_string(),
                status: status.as_u16(),
                detail: truncate(detail.trim(), ERROR_BODY_CHARS),
            });
        }
        Ok(SseUpstream { response })
    }

    fn request(&self, method: reqwest::Method, path: &str) -> reqwest::RequestBuilder {
        let mut request = self.client.request(method, self.url(path));
        for (name, value) in &self.headers {
            request = request.header(name.as_str(), value.as_str());
        }
        request
    }
}

/// A streaming response the worker has already accepted.
///
/// Kept whole rather than reduced to bytes because an SSE passthrough has to
/// mirror the upstream's headers too — `content-type`, and whatever the worker
/// says about buffering. `lattice-host`'s proxy learned that the hard way
/// (`gateway/proxy.rs`, `X-Accel-Buffering`).
#[derive(Debug)]
pub struct SseUpstream {
    response: reqwest::Response,
}

impl SseUpstream {
    /// The status the worker answered — always a success, by construction.
    pub fn status(&self) -> reqwest::StatusCode {
        self.response.status()
    }

    /// The upstream response headers, for a passthrough to copy.
    pub fn headers(&self) -> &reqwest::header::HeaderMap {
        self.response.headers()
    }

    /// The upstream `content-type`, when it sent a readable one.
    pub fn content_type(&self) -> Option<&str> {
        self.response
            .headers()
            .get(reqwest::header::CONTENT_TYPE)
            .and_then(|value| value.to_str().ok())
    }

    /// The body as a chunk stream, for `axum::body::Body::from_stream`.
    ///
    /// Nothing is buffered: chunks arrive as the worker flushes them, which is
    /// the whole point of an event stream and the property the 11.5.x proxy
    /// tests pin with a real gap between events.
    pub fn into_byte_stream(self) -> impl Stream<Item = Result<Bytes, reqwest::Error>> {
        self.response.bytes_stream()
    }

    /// The raw response, for a caller that wants everything at once.
    pub fn into_response(self) -> reqwest::Response {
        self.response
    }
}

async fn read_json(request: reqwest::RequestBuilder, path: &str) -> Result<Value, WorkerSeamError> {
    let response = request
        .send()
        .await
        .map_err(|error| WorkerSeamError::Transport {
            path: path.to_string(),
            detail: error.to_string(),
        })?;
    let status = response.status();
    let text = response
        .text()
        .await
        .map_err(|error| WorkerSeamError::Transport {
            path: path.to_string(),
            detail: format!("answered {status} with an unreadable body: {error}"),
        })?;
    if !status.is_success() {
        return Err(WorkerSeamError::Rejected {
            path: path.to_string(),
            status: status.as_u16(),
            detail: truncate(text.trim(), ERROR_BODY_CHARS),
        });
    }
    serde_json::from_str(&text).map_err(|_| WorkerSeamError::Malformed {
        path: path.to_string(),
        detail: truncate(text.trim(), ERROR_BODY_CHARS),
    })
}

/// Truncate on character boundaries — a byte slice would panic on the Korean
/// half of a worker error message (recorded lesson, v11.5.0).
fn truncate(text: &str, max: usize) -> String {
    if text.chars().count() <= max {
        return text.to_string();
    }
    text.chars().take(max).collect::<String>() + "…"
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_origin_loses_its_trailing_slash_exactly_once() {
        let client = WorkerSeamClient::new("http://127.0.0.1:4825/").expect("client");
        assert_eq!(client.origin(), "http://127.0.0.1:4825");
        assert_eq!(client.url("/health"), "http://127.0.0.1:4825/health");
        // A path without a leading slash is still a path, not a suffix.
        assert_eq!(client.url("health"), "http://127.0.0.1:4825/health");
        let bare = WorkerSeamClient::new("  http://127.0.0.1:4825  ").expect("client");
        assert_eq!(bare.origin(), client.origin());
    }

    #[test]
    fn the_timeout_is_configurable_and_defaults_to_the_ingest_seams() {
        let client = WorkerSeamClient::new("http://127.0.0.1:4825").expect("client");
        assert_eq!(client.timeout(), DEFAULT_TIMEOUT);
        assert_eq!(
            client.with_timeout(Duration::from_secs(2)).timeout(),
            Duration::from_secs(2)
        );
    }

    #[test]
    fn errors_say_what_went_wrong_and_carry_the_status() {
        let rejected = WorkerSeamError::Rejected {
            path: "/agent/tool".into(),
            status: 403,
            detail: "forbidden".into(),
        };
        assert_eq!(rejected.status(), Some(403));
        assert_eq!(rejected.path(), Some("/agent/tool"));
        assert!(rejected.to_string().contains("403"));

        let transport = WorkerSeamError::Transport {
            path: "/health".into(),
            detail: "connection refused".into(),
        };
        assert_eq!(transport.status(), None);
        assert!(transport.to_string().contains("unreachable"));

        let malformed = WorkerSeamError::Malformed {
            path: "/models".into(),
            detail: "<html>".into(),
        };
        assert!(malformed.to_string().contains("non-JSON"));
        assert_eq!(malformed.path(), Some("/models"));

        let client = WorkerSeamError::Client("bad url".into());
        assert_eq!(client.path(), None);
        assert!(client.to_string().contains("worker client"));
    }

    #[test]
    fn truncation_counts_characters_not_bytes() {
        // 3 bytes per glyph: a byte-slicing truncate would panic here.
        assert_eq!(truncate("한국어", 10), "한국어");
        assert_eq!(truncate("한국어입니다", 3), "한국어…");
    }
}
