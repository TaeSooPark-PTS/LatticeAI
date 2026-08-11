//! Shared test harness: a pure-Rust fake worker plus a gateway runner.
//!
//! Pure Rust on purpose — CI must not need a python interpreter to prove the
//! host's proxying and supervision behaviour.

#![allow(dead_code)]

use std::collections::HashMap;
use std::net::SocketAddr;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use lattice_host::gateway::{bind_loopback, serve_gateway, GatewayState, StatusProvider};
use lattice_host::supervisor::WorkerStatus;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio::task::JoinHandle;

/// One request as the fake worker saw it.
#[derive(Debug, Clone)]
pub struct RecordedRequest {
    pub method: String,
    pub target: String,
    pub headers: HashMap<String, String>,
    pub body: Vec<u8>,
}

impl RecordedRequest {
    pub fn path(&self) -> &str {
        self.target.split('?').next().unwrap_or("/")
    }

    pub fn query(&self) -> Option<&str> {
        self.target.split_once('?').map(|(_, query)| query)
    }

    pub fn header(&self, name: &str) -> Option<&str> {
        self.headers
            .get(&name.to_ascii_lowercase())
            .map(|s| s.as_str())
    }

    pub fn body_text(&self) -> String {
        String::from_utf8_lossy(&self.body).into_owned()
    }
}

/// A minimal HTTP/1.1 server standing in for the Python worker.
///
/// Routes: `/health` (200 or 503 depending on [`FakeWorker::set_healthy`]),
/// `/echo` (mirrors the request body), `/sse` (two events with a gap between
/// them, so buffering is observable), anything else → 200 text.
pub struct FakeWorker {
    addr: SocketAddr,
    healthy: Arc<AtomicBool>,
    requests: Arc<Mutex<Vec<RecordedRequest>>>,
    handle: JoinHandle<()>,
}

impl FakeWorker {
    /// Bind on an ephemeral loopback port and start serving.
    pub async fn start() -> Self {
        Self::start_with_health(true).await
    }

    /// Same, but choose the initial `/health` answer.
    pub async fn start_with_health(healthy: bool) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind fake worker");
        let addr = listener.local_addr().expect("fake worker addr");
        let healthy = Arc::new(AtomicBool::new(healthy));
        let requests = Arc::new(Mutex::new(Vec::new()));
        let handle = tokio::spawn(accept_loop(
            listener,
            Arc::clone(&healthy),
            Arc::clone(&requests),
        ));
        Self {
            addr,
            healthy,
            requests,
            handle,
        }
    }

    pub fn port(&self) -> u16 {
        self.addr.port()
    }

    pub fn origin(&self) -> String {
        format!("http://{}", self.addr)
    }

    pub fn set_healthy(&self, healthy: bool) {
        self.healthy.store(healthy, Ordering::SeqCst);
    }

    pub fn requests(&self) -> Vec<RecordedRequest> {
        self.requests.lock().expect("requests").clone()
    }

    pub fn request_count(&self) -> usize {
        self.requests.lock().expect("requests").len()
    }

    /// Stop listening; further connections are refused.
    pub fn shutdown(self) {
        self.handle.abort();
    }
}

async fn accept_loop(
    listener: TcpListener,
    healthy: Arc<AtomicBool>,
    requests: Arc<Mutex<Vec<RecordedRequest>>>,
) {
    loop {
        let Ok((stream, _)) = listener.accept().await else {
            return;
        };
        let healthy = Arc::clone(&healthy);
        let requests = Arc::clone(&requests);
        tokio::spawn(async move {
            handle_connection(stream, healthy, requests).await;
        });
    }
}

async fn read_request(stream: &mut TcpStream) -> Option<RecordedRequest> {
    let mut buffer: Vec<u8> = Vec::with_capacity(1024);
    let mut chunk = vec![0u8; 64 * 1024];
    let header_end = loop {
        if let Some(index) = find_subsequence(&buffer, b"\r\n\r\n") {
            break index;
        }
        let read = stream.read(&mut chunk).await.ok()?;
        if read == 0 {
            return None;
        }
        buffer.extend_from_slice(&chunk[..read]);
    };

    let head = String::from_utf8_lossy(&buffer[..header_end]).into_owned();
    let mut lines = head.split("\r\n");
    let request_line = lines.next()?;
    let mut parts = request_line.split_whitespace();
    let method = parts.next()?.to_string();
    let target = parts.next()?.to_string();

    let mut headers = HashMap::new();
    for line in lines {
        if let Some((name, value)) = line.split_once(':') {
            headers.insert(name.trim().to_ascii_lowercase(), value.trim().to_string());
        }
    }

    let mut body = buffer[header_end + 4..].to_vec();
    if let Some(length) = headers
        .get("content-length")
        .and_then(|value| value.parse::<usize>().ok())
    {
        while body.len() < length {
            let read = stream.read(&mut chunk).await.ok()?;
            if read == 0 {
                break;
            }
            body.extend_from_slice(&chunk[..read]);
        }
        body.truncate(length);
    } else if headers.contains_key("transfer-encoding") {
        // Chunked request body: read until the terminating zero-length chunk.
        // Only the freshly read tail is scanned, so a multi-megabyte upload
        // stays linear.
        loop {
            let read =
                match tokio::time::timeout(Duration::from_millis(500), stream.read(&mut chunk))
                    .await
                {
                    Ok(Ok(0)) | Err(_) => break,
                    Ok(Ok(read)) => read,
                    Ok(Err(_)) => break,
                };
            let tail_start = body.len().saturating_sub(4);
            body.extend_from_slice(&chunk[..read]);
            if find_subsequence(&body[tail_start..], b"0\r\n\r\n").is_some() {
                break;
            }
        }
        body = dechunk(&body);
    }

    Some(RecordedRequest {
        method,
        target,
        headers,
        body,
    })
}

fn find_subsequence(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    haystack
        .windows(needle.len())
        .position(|window| window == needle)
}

fn dechunk(raw: &[u8]) -> Vec<u8> {
    let mut out = Vec::new();
    let mut rest = raw;
    while let Some(index) = find_subsequence(rest, b"\r\n") {
        let size = std::str::from_utf8(&rest[..index])
            .ok()
            .and_then(|text| usize::from_str_radix(text.trim(), 16).ok());
        let Some(size) = size else { break };
        rest = &rest[index + 2..];
        if size == 0 || rest.len() < size {
            break;
        }
        out.extend_from_slice(&rest[..size]);
        rest = &rest[size..];
        if rest.starts_with(b"\r\n") {
            rest = &rest[2..];
        }
    }
    out
}

async fn handle_connection(
    mut stream: TcpStream,
    healthy: Arc<AtomicBool>,
    requests: Arc<Mutex<Vec<RecordedRequest>>>,
) {
    let Some(request) = read_request(&mut stream).await else {
        return;
    };
    let path = request.path().to_string();
    requests.lock().expect("requests").push(request.clone());

    match path.as_str() {
        "/health" => {
            if healthy.load(Ordering::SeqCst) {
                respond(
                    &mut stream,
                    200,
                    "application/json",
                    b"{\"status\":\"ok\"}",
                    &[],
                )
                .await;
            } else {
                respond(
                    &mut stream,
                    503,
                    "application/json",
                    b"{\"status\":\"starting\"}",
                    &[],
                )
                .await;
            }
        }
        "/echo" => {
            let content_type = request
                .header("content-type")
                .unwrap_or("application/octet-stream")
                .to_string();
            respond(
                &mut stream,
                200,
                &content_type,
                &request.body,
                &[("x-echo-method", request.method.as_str())],
            )
            .await;
        }
        "/sse" => {
            let head = "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\
                        Cache-Control: no-cache\r\nConnection: close\r\n\r\n";
            if stream.write_all(head.as_bytes()).await.is_err() {
                return;
            }
            let _ = stream.write_all(b"data: first\n\n").await;
            let _ = stream.flush().await;
            tokio::time::sleep(Duration::from_millis(400)).await;
            let _ = stream.write_all(b"data: second\n\n").await;
            let _ = stream.flush().await;
        }
        "/teapot" => {
            respond(&mut stream, 418, "text/plain", b"short and stout", &[]).await;
        }
        other => {
            let body = format!("worker saw {other}");
            respond(
                &mut stream,
                200,
                "text/plain",
                body.as_bytes(),
                &[("x-fake-worker", "1"), ("set-cookie", "a=1")],
            )
            .await;
        }
    }
    let _ = stream.shutdown().await;
}

async fn respond(
    stream: &mut TcpStream,
    status: u16,
    content_type: &str,
    body: &[u8],
    extra: &[(&str, &str)],
) {
    let reason = match status {
        200 => "OK",
        418 => "I'm a teapot",
        503 => "Service Unavailable",
        _ => "Unknown",
    };
    let mut head = format!(
        "HTTP/1.1 {status} {reason}\r\nContent-Type: {content_type}\r\n\
         Content-Length: {}\r\nConnection: close\r\n",
        body.len()
    );
    for (name, value) in extra {
        head.push_str(&format!("{name}: {value}\r\n"));
    }
    head.push_str("\r\n");
    let _ = stream.write_all(head.as_bytes()).await;
    let _ = stream.write_all(body).await;
    let _ = stream.flush().await;
}

/// A [`StatusProvider`] that points at a fixed origin — lets the gateway be
/// tested without a supervisor.
pub struct FixedProvider {
    origin: String,
    port: u16,
}

impl FixedProvider {
    pub fn new(origin: String, port: u16) -> Self {
        Self { origin, port }
    }
}

impl StatusProvider for FixedProvider {
    fn status(&self) -> WorkerStatus {
        let mut status = WorkerStatus::idle(self.port, true);
        status.command = Some("fake worker".into());
        status
    }

    fn worker_origin(&self) -> String {
        self.origin.clone()
    }
}

/// A gateway bound to an ephemeral loopback port, served in the background.
pub struct TestGateway {
    pub base: String,
    shutdown: Option<tokio::sync::oneshot::Sender<()>>,
    handle: JoinHandle<()>,
}

impl TestGateway {
    /// A gateway whose native search routes read the environment-resolved
    /// store (`LATTICEAI_DATA_DIR`, else `~/.ltcai`).
    pub async fn start(provider: Arc<dyn StatusProvider>) -> Self {
        Self::serve(GatewayState::new(provider).expect("gateway state")).await
    }

    /// A gateway pinned to one store, for the cases where the answer must not
    /// depend on what happens to exist in the developer's home directory.
    pub async fn start_with_db(
        provider: Arc<dyn StatusProvider>,
        db: impl Into<std::path::PathBuf>,
    ) -> Self {
        Self::serve(
            GatewayState::new(provider)
                .expect("gateway state")
                .with_db_path(db),
        )
        .await
    }

    async fn serve(state: GatewayState) -> Self {
        let state = Arc::new(state);
        let listener = bind_loopback("127.0.0.1:0".parse().expect("addr"))
            .await
            .expect("bind gateway");
        let addr = listener.local_addr().expect("gateway addr");
        let (tx, rx) = tokio::sync::oneshot::channel();
        let handle = tokio::spawn(async move {
            let _ = serve_gateway(listener, state, async {
                let _ = rx.await;
            })
            .await;
        });
        Self {
            base: format!("http://{addr}"),
            shutdown: Some(tx),
            handle,
        }
    }

    pub fn url(&self, path: &str) -> String {
        format!("{}{}", self.base, path)
    }

    pub async fn stop(mut self) {
        if let Some(tx) = self.shutdown.take() {
            let _ = tx.send(());
        }
        let _ = tokio::time::timeout(Duration::from_secs(5), self.handle).await;
    }
}

/// An HTTP client that never consults system proxies.
pub fn client() -> reqwest::Client {
    reqwest::Client::builder()
        .no_proxy()
        .build()
        .expect("test client")
}

/// Parse a response body as JSON.
///
/// Hand-rolled because the workspace pins `reqwest` without its `json`
/// feature — the proxy streams bytes and never needs to deserialise.
pub async fn json(response: reqwest::Response) -> serde_json::Value {
    let text = response.text().await.expect("response body");
    serde_json::from_str(&text).unwrap_or_else(|err| panic!("not JSON ({err}): {text}"))
}

/// Poll `condition` until it is true or `deadline` elapses.
pub async fn wait_until<F>(deadline: Duration, mut condition: F) -> bool
where
    F: FnMut() -> bool,
{
    let started = std::time::Instant::now();
    while started.elapsed() < deadline {
        if condition() {
            return true;
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    condition()
}
