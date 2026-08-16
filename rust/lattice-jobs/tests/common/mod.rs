//! A pure-Rust fake worker, style-matched to `lattice-host/tests/common`.
//!
//! Pure Rust on purpose: CI must not need a Python interpreter to prove that
//! the scheduler calls the right endpoint with the right body and behaves when
//! the answer is a 500.

use std::collections::HashMap;
use std::net::SocketAddr;
use std::sync::{Arc, Mutex};
use std::time::Duration;

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
            .map(|value| value.as_str())
    }

    pub fn body_text(&self) -> String {
        String::from_utf8_lossy(&self.body).into_owned()
    }
}

/// What the fake worker answers. Every field is swappable mid-test, which is
/// how "the worker fell over, then came back" is expressed.
#[derive(Debug, Clone)]
pub struct Behaviour {
    pub drain_status: u16,
    pub drain_body: String,
    pub jobs_status: u16,
    pub jobs_body: String,
    pub resume_status: u16,
    pub resume_body: String,
}

impl Default for Behaviour {
    fn default() -> Self {
        Self {
            drain_status: 200,
            drain_body: r#"{"claimed":2,"indexed":1,"retried":1,"failed":0,"detail":null,
                           "limit":25,"scope":"machine",
                           "queue":{"available":true,"counts":{"pending":0,"running":0,
                                    "done":2,"failed":0},"pending":0}}"#
                .to_string(),
            jobs_status: 200,
            jobs_body: r#"{"jobs":[]}"#.to_string(),
            resume_status: 200,
            resume_body: r#"{"status":"resuming","job_id":"j","remaining":3}"#.to_string(),
        }
    }
}

/// A minimal HTTP/1.1 server standing in for the Python worker.
pub struct FakeWorker {
    addr: SocketAddr,
    behaviour: Arc<Mutex<Behaviour>>,
    requests: Arc<Mutex<Vec<RecordedRequest>>>,
    handle: JoinHandle<()>,
}

impl FakeWorker {
    /// Bind an ephemeral loopback port and start serving the defaults.
    pub async fn start() -> Self {
        Self::start_with(Behaviour::default()).await
    }

    /// Same, with a chosen behaviour.
    pub async fn start_with(behaviour: Behaviour) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind fake worker");
        let addr = listener.local_addr().expect("fake worker addr");
        let behaviour = Arc::new(Mutex::new(behaviour));
        let requests = Arc::new(Mutex::new(Vec::new()));
        let handle = tokio::spawn(accept_loop(
            listener,
            Arc::clone(&behaviour),
            Arc::clone(&requests),
        ));
        Self {
            addr,
            behaviour,
            requests,
            handle,
        }
    }

    pub fn origin(&self) -> String {
        format!("http://{}", self.addr)
    }

    /// Mutate what the worker answers from now on.
    pub fn set(&self, change: impl FnOnce(&mut Behaviour)) {
        let mut behaviour = self.behaviour.lock().expect("behaviour");
        change(&mut behaviour);
    }

    pub fn requests(&self) -> Vec<RecordedRequest> {
        self.requests.lock().expect("requests").clone()
    }

    /// Every request whose path is exactly `path`.
    pub fn requests_to(&self, path: &str) -> Vec<RecordedRequest> {
        self.requests()
            .into_iter()
            .filter(|request| request.path() == path)
            .collect()
    }

    pub fn count_to(&self, path: &str) -> usize {
        self.requests_to(path).len()
    }

    /// Stop listening; further connections are refused.
    pub fn shutdown(self) {
        self.handle.abort();
    }
}

async fn accept_loop(
    listener: TcpListener,
    behaviour: Arc<Mutex<Behaviour>>,
    requests: Arc<Mutex<Vec<RecordedRequest>>>,
) {
    loop {
        let Ok((stream, _)) = listener.accept().await else {
            return;
        };
        let behaviour = Arc::clone(&behaviour);
        let requests = Arc::clone(&requests);
        tokio::spawn(async move {
            handle_connection(stream, behaviour, requests).await;
        });
    }
}

async fn handle_connection(
    mut stream: TcpStream,
    behaviour: Arc<Mutex<Behaviour>>,
    requests: Arc<Mutex<Vec<RecordedRequest>>>,
) {
    let Some(request) = read_request(&mut stream).await else {
        return;
    };
    let path = request.path().to_string();
    requests.lock().expect("requests").push(request);

    let answer = behaviour.lock().expect("behaviour").clone();
    let (status, body) = if path == "/api/index/drain" {
        (answer.drain_status, answer.drain_body)
    } else if path == "/api/ingestion/jobs" {
        (answer.jobs_status, answer.jobs_body)
    } else if path.starts_with("/api/ingestion/jobs/") && path.ends_with("/resume") {
        (answer.resume_status, answer.resume_body)
    } else {
        (404, format!("{{\"detail\":\"no route {path}\"}}"))
    };
    respond(&mut stream, status, &body).await;
    let _ = stream.shutdown().await;
}

async fn read_request(stream: &mut TcpStream) -> Option<RecordedRequest> {
    let mut buffer: Vec<u8> = Vec::with_capacity(1024);
    let mut chunk = vec![0u8; 16 * 1024];
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
            let read =
                match tokio::time::timeout(Duration::from_millis(500), stream.read(&mut chunk))
                    .await
                {
                    Ok(Ok(0)) | Err(_) => break,
                    Ok(Ok(read)) => read,
                    Ok(Err(_)) => break,
                };
            body.extend_from_slice(&chunk[..read]);
        }
        body.truncate(length);
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

async fn respond(stream: &mut TcpStream, status: u16, body: &str) {
    let reason = match status {
        200 => "OK",
        401 => "Unauthorized",
        404 => "Not Found",
        500 => "Internal Server Error",
        503 => "Service Unavailable",
        _ => "Unknown",
    };
    let head = format!(
        "HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\n\
         Content-Length: {}\r\nConnection: close\r\n\r\n",
        body.len()
    );
    let _ = stream.write_all(head.as_bytes()).await;
    let _ = stream.write_all(body.as_bytes()).await;
    let _ = stream.flush().await;
}

/// An HTTP client that never consults system proxies.
pub fn client() -> reqwest::Client {
    reqwest::Client::builder()
        .no_proxy()
        .build()
        .expect("test client")
}

/// Parse a response body as JSON (reqwest is pinned without its `json` feature).
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

/// A `vector_jobs` fixture built with the Python DDL, verbatim.
pub fn queue_fixture(dir: &std::path::Path, rows: &[(&str, &str)]) -> std::path::PathBuf {
    let path = dir.join("knowledge_graph.sqlite");
    let conn = rusqlite::Connection::open(&path).expect("open fixture");
    conn.execute_batch(
        "PRAGMA journal_mode=WAL;
         CREATE TABLE IF NOT EXISTS vector_jobs (
           node_id TEXT PRIMARY KEY,
           status TEXT NOT NULL DEFAULT 'pending',
           attempts INTEGER NOT NULL DEFAULT 0,
           detail TEXT,
           created_at TEXT NOT NULL,
           updated_at TEXT NOT NULL
         );",
    )
    .expect("fixture ddl");
    for (node_id, status) in rows {
        conn.execute(
            "INSERT INTO vector_jobs(node_id, status, attempts, detail, created_at, updated_at)
             VALUES (?1, ?2, 0, NULL, '2026-08-11T00:00:00', '2026-08-11T00:00:00')",
            rusqlite::params![node_id, status],
        )
        .expect("fixture row");
    }
    path
}

/// The jobs list payload for `GET /api/ingestion/jobs`.
pub fn jobs_payload(jobs: &[(&str, &str, u64, u64)]) -> String {
    let entries: Vec<String> = jobs
        .iter()
        .map(|(job_id, status, total, processed)| {
            format!(
                r#"{{"job_id":"{job_id}","status":"{status}","total":{total},
                     "processed":{processed},"failed":0,"errors":[],
                     "created_at":"2026-08-11T00:00:00","updated_at":"2026-08-11T00:00:00"}}"#
            )
        })
        .collect();
    format!("{{\"jobs\":[{}]}}", entries.join(","))
}
