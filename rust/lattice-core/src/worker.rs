//! The worker seam, from the gateway's side.
//!
//! Since v11.6.0 Wave 3, `lattice-host` is the product server and owns all
//! SQLite and state-file writes natively (`GraphWriter`, `WorkspaceOsStore`).
//! The Python worker keeps only stateless compute — LLM inference, the document
//! parser/render matrix, entity/triple extraction, ASR, and embedding
//! production. Everything a Rust route needs from that compute box goes over
//! loopback HTTP, and this is the client every crate uses to make the call.
//!
//! It is deliberately shapeless: `post_json`, `get_json`, `stream_sse`, plus
//! opt-in `post_idempotent_json` for stateless compute calls that carry an
//! idempotency key across transient worker restarts.
//!
//! ## Design rules
//!
//! * **No implicit retries on `post_json`.** A drain tick backs off, a chat
//!   turn fails the turn, an admin read shows an error — callers that want
//!   bounded retry over stateless compute opt into [`WorkerSeamClient::post_idempotent_json`]
//!   with an explicit [`WorkerRetryPolicy`] and idempotency key.
//! * **No authentication of its own.** A deployment with auth on supplies the
//!   credential via [`WorkerSeamClient::with_header`]; nothing here invents one.
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

    /// Whether the failure represents a transient worker unavailability
    /// (transport drop/timeout or 429/502/503/504 overload/restart) suitable
    /// for bounded idempotent retry.
    pub fn is_transient(&self) -> bool {
        match self {
            WorkerSeamError::Transport { .. } => true,
            WorkerSeamError::Rejected { status, .. } => {
                matches!(*status, 429 | 502 | 503 | 504)
            }
            WorkerSeamError::Client(_) | WorkerSeamError::Malformed { .. } => false,
        }
    }
}

/// Bounded retry policy for stateless idempotent worker compute endpoints.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct WorkerRetryPolicy {
    /// Maximum additional retry attempts after the initial call (`0` = single attempt).
    pub max_retries: usize,
    /// Base backoff before the first retry; doubles on each attempt up to `max_backoff`.
    pub initial_backoff: Duration,
    /// Upper bound on per-attempt sleep duration.
    pub max_backoff: Duration,
}

impl Default for WorkerRetryPolicy {
    fn default() -> Self {
        Self {
            max_retries: 2,
            initial_backoff: Duration::from_millis(50),
            max_backoff: Duration::from_millis(500),
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

    /// `POST {origin}{path}` with an explicit `x-lattice-idempotency-key` header
    /// and bounded retry on transient worker failures (`is_transient()`).
    pub async fn post_idempotent_json(
        &self,
        path: &str,
        idempotency_key: &str,
        body: &Value,
        policy: WorkerRetryPolicy,
    ) -> Result<Value, WorkerSeamError> {
        let payload =
            serde_json::to_vec(body).map_err(|error| WorkerSeamError::Client(error.to_string()))?;
        let mut attempt = 0usize;
        let mut backoff = policy.initial_backoff;
        loop {
            let mut request = self
                .request(reqwest::Method::POST, path)
                .header("content-type", "application/json")
                .timeout(self.timeout)
                .body(payload.clone());
            if !idempotency_key.trim().is_empty() {
                request = request.header("x-lattice-idempotency-key", idempotency_key.trim());
            }
            match read_json(request, path).await {
                Ok(val) => return Ok(val),
                Err(err) if attempt < policy.max_retries && err.is_transient() => {
                    attempt += 1;
                    if !backoff.is_zero() {
                        tokio::time::sleep(backoff).await;
                    }
                    backoff = backoff.saturating_mul(2).min(policy.max_backoff);
                }
                Err(err) => return Err(err),
            }
        }
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

    #[tokio::test]
    async fn idempotent_post_recovers_from_transient_503_and_stops_on_400() {
        use std::sync::atomic::{AtomicUsize, Ordering};
        use std::sync::Arc;
        use tokio::io::{AsyncReadExt, AsyncWriteExt};
        use tokio::net::TcpListener;

        let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind");
        let addr = listener.local_addr().expect("addr");
        let hits = Arc::new(AtomicUsize::new(0));
        let hits_server = Arc::clone(&hits);

        tokio::spawn(async move {
            loop {
                let Ok((mut socket, _)) = listener.accept().await else {
                    break;
                };
                let n = hits_server.fetch_add(1, Ordering::SeqCst);
                let mut buf = [0u8; 2048];
                let read_n = socket.read(&mut buf).await.unwrap_or(0);
                let req_str = String::from_utf8_lossy(&buf[..read_n]);
                assert!(
                    req_str
                        .to_ascii_lowercase()
                        .contains("x-lattice-idempotency-key: job-123"),
                    "missing idempotency header in request: {req_str}"
                );
                let response = match n {
                    0 => {
                        "HTTP/1.1 503 Service Unavailable\r\nContent-Length: 12\r\nConnection: close\r\n\r\nrestarting.."
                    }
                    1 => {
                        "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 11\r\nConnection: close\r\n\r\n{\"ok\":true}"
                    }
                    _ => {
                        "HTTP/1.1 400 Bad Request\r\nContent-Length: 11\r\nConnection: close\r\n\r\nbad payload"
                    }
                };
                let _ = socket.write_all(response.as_bytes()).await;
            }
        });

        let client = WorkerSeamClient::new(format!("http://{addr}")).expect("client");
        let policy = WorkerRetryPolicy {
            max_retries: 2,
            initial_backoff: Duration::from_millis(5),
            max_backoff: Duration::from_millis(20),
        };
        let ok = client
            .post_idempotent_json(
                "/worker/embed",
                "job-123",
                &serde_json::json!({"q": "hi"}),
                policy,
            )
            .await
            .expect("retry succeeds on second attempt");
        assert_eq!(ok["ok"], true);
        assert_eq!(hits.load(Ordering::SeqCst), 2);

        // Non-transient 400 must fail immediately without burning retries.
        let err = client
            .post_idempotent_json(
                "/worker/embed",
                "job-123",
                &serde_json::json!({"q": "bad"}),
                policy,
            )
            .await
            .expect_err("400 should not retry");
        assert!(!err.is_transient());
        assert_eq!(err.status(), Some(400));
        assert_eq!(hits.load(Ordering::SeqCst), 3);
    }
}
