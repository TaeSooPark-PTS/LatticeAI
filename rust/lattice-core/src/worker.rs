use std::collections::BTreeMap;
use std::time::Duration;

use bytes::Bytes;
use futures_core::Stream;
use serde_json::Value;

pub const DEFAULT_TIMEOUT: Duration = Duration::from_secs(30);

pub const DEFAULT_CONNECT_TIMEOUT: Duration = Duration::from_secs(5);

const ERROR_BODY_CHARS: usize = 400;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum WorkerSeamError {
    Client(String),
    Transport {
        path: String,
        detail: String,
    },
    Rejected {
        path: String,
        status: u16,
        detail: String,
    },
    Malformed {
        path: String,
        detail: String,
    },
}

impl WorkerSeamError {
    pub fn status(&self) -> Option<u16> {
        match self {
            WorkerSeamError::Rejected { status, .. } => Some(*status),
            _ => None,
        }
    }

    pub fn path(&self) -> Option<&str> {
        match self {
            WorkerSeamError::Client(_) => None,
            WorkerSeamError::Transport { path, .. }
            | WorkerSeamError::Rejected { path, .. }
            | WorkerSeamError::Malformed { path, .. } => Some(path),
        }
    }

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

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct WorkerRetryPolicy {
    pub max_retries: usize,
    pub initial_backoff: Duration,
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

#[derive(Debug, Clone)]
pub struct WorkerSeamClient {
    client: reqwest::Client,
    origin: String,
    headers: BTreeMap<String, String>,
    timeout: Duration,
}

impl WorkerSeamClient {
    pub fn new(origin: impl AsRef<str>) -> Result<Self, WorkerSeamError> {
        let client = reqwest::Client::builder()
            .no_proxy()
            .connect_timeout(DEFAULT_CONNECT_TIMEOUT)
            .build()
            .map_err(|error| WorkerSeamError::Client(error.to_string()))?;
        Ok(Self::with_client(client, origin))
    }

    pub fn with_client(client: reqwest::Client, origin: impl AsRef<str>) -> Self {
        Self {
            client,
            origin: origin.as_ref().trim().trim_end_matches('/').to_string(),
            headers: BTreeMap::new(),
            timeout: DEFAULT_TIMEOUT,
        }
    }

    pub fn with_header(mut self, name: impl Into<String>, value: impl Into<String>) -> Self {
        self.headers.insert(name.into(), value.into());
        self
    }

    pub fn with_timeout(mut self, timeout: Duration) -> Self {
        self.timeout = timeout;
        self
    }

    pub fn origin(&self) -> &str {
        &self.origin
    }

    pub fn timeout(&self) -> Duration {
        self.timeout
    }

    pub fn url(&self, path: &str) -> String {
        if path.starts_with('/') {
            format!("{}{path}", self.origin)
        } else {
            format!("{}/{path}", self.origin)
        }
    }

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

    pub async fn post_idempotent_compute(
        &self,
        path: &str,
        body: &Value,
    ) -> Result<Value, WorkerSeamError> {
        self.post_idempotent_compute_with_policy(path, body, WorkerRetryPolicy::default())
            .await
    }

    pub async fn post_idempotent_compute_with_policy(
        &self,
        path: &str,
        body: &Value,
        policy: WorkerRetryPolicy,
    ) -> Result<Value, WorkerSeamError> {
        let payload =
            serde_json::to_vec(body).map_err(|error| WorkerSeamError::Client(error.to_string()))?;
        let mut key_input = Vec::with_capacity(path.len() + 1 + payload.len());
        key_input.extend_from_slice(path.as_bytes());
        key_input.push(b'\n');
        key_input.extend_from_slice(&payload);
        let digest = crate::sha256_bytes(&key_input);
        let key = format!("compute-{}", &digest[..32]);
        self.post_idempotent_json(path, &key, body, policy).await
    }

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

    pub async fn get_json(&self, path: &str) -> Result<Value, WorkerSeamError> {
        let request = self
            .request(reqwest::Method::GET, path)
            .timeout(self.timeout);
        read_json(request, path).await
    }

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

#[derive(Debug)]
pub struct SseUpstream {
    response: reqwest::Response,
}

impl SseUpstream {
    pub fn status(&self) -> reqwest::StatusCode {
        self.response.status()
    }

    pub fn headers(&self) -> &reqwest::header::HeaderMap {
        self.response.headers()
    }

    pub fn content_type(&self) -> Option<&str> {
        self.response
            .headers()
            .get(reqwest::header::CONTENT_TYPE)
            .and_then(|value| value.to_str().ok())
    }

    pub fn into_byte_stream(self) -> impl Stream<Item = Result<Bytes, reqwest::Error>> {
        self.response.bytes_stream()
    }

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
