//! `WorkerSeamClient` against a real worker — a small axum server on a real
//! loopback port.
//!
//! A mock would prove the client calls a function; this proves it speaks HTTP:
//! that the JSON body arrives as the worker's handler sees it, that a refusal
//! keeps its status, that an event stream is not buffered on the way through,
//! and that a configured header actually rides along.

use std::net::SocketAddr;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use axum::extract::State;
use axum::http::{HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::Router;
use futures_core::Stream;
use serde_json::{json, Value};
use tokio::sync::mpsc;

use lattice_core::worker::{WorkerSeamClient, WorkerSeamError};

/// What the stub saw, so a test can assert on the request and not only the
/// answer.
#[derive(Default)]
struct Seen {
    calls: AtomicUsize,
    last_body: std::sync::Mutex<Value>,
    last_headers: std::sync::Mutex<Vec<(String, String)>>,
}

/// A worker that answers on a port the OS picked, shut down when dropped.
struct FakeWorker {
    origin: String,
    seen: Arc<Seen>,
    shutdown: Option<tokio::sync::oneshot::Sender<()>>,
    handle: Option<tokio::task::JoinHandle<()>>,
}

impl FakeWorker {
    async fn start() -> Self {
        let seen = Arc::new(Seen::default());
        let app = Router::new()
            .route("/echo", post(echo))
            .route("/status", get(status))
            .route("/refuse", post(refuse))
            .route("/not-json", post(not_json))
            .route("/slow", post(slow))
            .route("/sse", post(sse))
            .with_state(Arc::clone(&seen));
        let listener = tokio::net::TcpListener::bind(SocketAddr::from(([127, 0, 0, 1], 0)))
            .await
            .expect("bind loopback");
        let port = listener.local_addr().expect("addr").port();
        let (shutdown, is_shutdown) = tokio::sync::oneshot::channel();
        let handle = tokio::spawn(async move {
            let _ = axum::serve(listener, app)
                .with_graceful_shutdown(async {
                    let _ = is_shutdown.await;
                })
                .await;
        });
        Self {
            origin: format!("http://127.0.0.1:{port}"),
            seen,
            shutdown: Some(shutdown),
            handle: Some(handle),
        }
    }

    fn client(&self) -> WorkerSeamClient {
        WorkerSeamClient::new(&self.origin).expect("client")
    }
}

impl Drop for FakeWorker {
    fn drop(&mut self) {
        if let Some(shutdown) = self.shutdown.take() {
            let _ = shutdown.send(());
        }
        if let Some(handle) = self.handle.take() {
            handle.abort();
        }
    }
}

fn record(seen: &Seen, headers: &HeaderMap, body: Value) {
    seen.calls.fetch_add(1, Ordering::SeqCst);
    *seen.last_body.lock().expect("body lock") = body;
    *seen.last_headers.lock().expect("header lock") = headers
        .iter()
        .map(|(name, value)| {
            (
                name.as_str().to_string(),
                value.to_str().unwrap_or_default().to_string(),
            )
        })
        .collect();
}

async fn echo(State(seen): State<Arc<Seen>>, headers: HeaderMap, body: String) -> Response {
    let parsed: Value = serde_json::from_str(&body).unwrap_or(Value::Null);
    record(&seen, &headers, parsed.clone());
    ([("content-type", "application/json")], body).into_response()
}

async fn status(State(seen): State<Arc<Seen>>, headers: HeaderMap) -> Response {
    record(&seen, &headers, Value::Null);
    (
        [("content-type", "application/json")],
        r#"{"ok":true,"access":{"require_auth":false}}"#,
    )
        .into_response()
}

async fn refuse() -> Response {
    (
        StatusCode::FORBIDDEN,
        [("content-type", "application/json")],
        r#"{"detail":"권한이 없습니다"}"#,
    )
        .into_response()
}

async fn not_json() -> Response {
    (
        StatusCode::OK,
        [("content-type", "text/html")],
        "<html>worker booting</html>",
    )
        .into_response()
}

async fn slow() -> Response {
    tokio::time::sleep(Duration::from_millis(500)).await;
    ([("content-type", "application/json")], "{}").into_response()
}

/// Two events with a real gap between them: a client that buffered the body
/// would only see them both at the end.
async fn sse(State(seen): State<Arc<Seen>>, headers: HeaderMap, body: String) -> Response {
    record(
        &seen,
        &headers,
        serde_json::from_str(&body).unwrap_or(Value::Null),
    );
    let (tx, rx) = mpsc::channel::<Result<Vec<u8>, std::io::Error>>(4);
    tokio::spawn(async move {
        let _ = tx.send(Ok(b"data: first\n\n".to_vec())).await;
        tokio::time::sleep(Duration::from_millis(250)).await;
        let _ = tx.send(Ok(b"data: second\n\n".to_vec())).await;
    });
    (
        [("content-type", "text/event-stream")],
        axum::body::Body::from_stream(ChannelStream(rx)),
    )
        .into_response()
}

/// `tokio_stream::wrappers::ReceiverStream`, hand-rolled rather than adding a
/// dependency for one test.
struct ChannelStream(mpsc::Receiver<Result<Vec<u8>, std::io::Error>>);

impl Stream for ChannelStream {
    type Item = Result<Vec<u8>, std::io::Error>;

    fn poll_next(
        mut self: std::pin::Pin<&mut Self>,
        cx: &mut std::task::Context<'_>,
    ) -> std::task::Poll<Option<Self::Item>> {
        self.0.poll_recv(cx)
    }
}

#[tokio::test]
async fn a_post_round_trips_the_body_the_worker_sees() {
    let worker = FakeWorker::start().await;
    let client = worker.client();
    let sent = json!({"tool": "read_file", "args": {"path": "노트.md"}});
    let answer = client.post_json("/echo", &sent).await.expect("post");
    assert_eq!(answer, sent, "the worker's answer must arrive unchanged");
    assert_eq!(
        *worker.seen.last_body.lock().expect("body"),
        sent,
        "the worker must receive the body verbatim, UTF-8 included"
    );
    let headers = worker.seen.last_headers.lock().expect("headers").clone();
    assert!(
        headers
            .iter()
            .any(|(name, value)| name == "content-type" && value == "application/json"),
        "the workspace pins reqwest without the json feature, so the header is \
         set by hand and must actually be there: {headers:?}"
    );
}

#[tokio::test]
async fn a_get_reads_json_and_a_configured_header_rides_along() {
    let worker = FakeWorker::start().await;
    let client = worker.client().with_header("cookie", "lattice_session=abc");
    let answer = client.get_json("/status").await.expect("get");
    assert_eq!(answer["ok"], Value::Bool(true));
    let headers = worker.seen.last_headers.lock().expect("headers").clone();
    assert!(
        headers
            .iter()
            .any(|(name, value)| name == "cookie" && value == "lattice_session=abc"),
        "with_header is the auth seam; if it does not reach the worker, every \
         authenticated delegation 401s: {headers:?}"
    );
}

#[tokio::test]
async fn a_refusal_keeps_its_status_and_its_body() {
    let worker = FakeWorker::start().await;
    let err = worker
        .client()
        .post_json("/refuse", &json!({}))
        .await
        .unwrap_err();
    assert_eq!(
        err.status(),
        Some(403),
        "a caller mirroring the worker's verdict needs the number, not prose"
    );
    assert_eq!(err.path(), Some("/refuse"));
    assert!(
        err.to_string().contains("권한이 없습니다"),
        "the worker's own message must survive: {err}"
    );
}

#[tokio::test]
async fn html_from_a_booting_worker_is_a_malformed_answer_not_a_panic() {
    let worker = FakeWorker::start().await;
    let err = worker
        .client()
        .post_json("/not-json", &json!({}))
        .await
        .unwrap_err();
    assert!(matches!(err, WorkerSeamError::Malformed { .. }));
    assert!(err.to_string().contains("non-JSON"));
}

#[tokio::test]
async fn an_unreachable_worker_is_a_transport_failure_with_the_path() {
    // Port 1 on loopback: nothing listens, and the connect timeout bounds it.
    let client = WorkerSeamClient::new("http://127.0.0.1:1").expect("client");
    let err = client.get_json("/health").await.unwrap_err();
    assert!(matches!(err, WorkerSeamError::Transport { .. }));
    assert_eq!(err.path(), Some("/health"));
    assert_eq!(err.status(), None);
}

#[tokio::test]
async fn the_timeout_is_the_callers_to_set() {
    let worker = FakeWorker::start().await;
    let err = worker
        .client()
        .with_timeout(Duration::from_millis(80))
        .post_json("/slow", &json!({}))
        .await
        .unwrap_err();
    assert!(matches!(err, WorkerSeamError::Transport { .. }));

    // And no retry happened behind the caller's back.
    assert_eq!(
        worker.seen.calls.load(Ordering::SeqCst),
        0,
        "/slow never records; a retry would show up as a second connection, \
         which this client deliberately never makes"
    );
}

#[tokio::test]
async fn an_event_stream_arrives_in_pieces_rather_than_at_the_end() {
    let worker = FakeWorker::start().await;
    let upstream = worker
        .client()
        .stream_sse("/sse", &json!({"stream": true}))
        .await
        .expect("stream");
    assert_eq!(upstream.status(), reqwest::StatusCode::OK);
    assert_eq!(upstream.content_type(), Some("text/event-stream"));
    assert!(upstream.headers().get("content-type").is_some());

    let started = Instant::now();
    let mut stream = Box::pin(upstream.into_byte_stream());
    let first = next_chunk(&mut stream).await.expect("first event");
    let first_at = started.elapsed();
    assert_eq!(first, b"data: first\n\n");
    assert!(
        first_at < Duration::from_millis(200),
        "the first event took {first_at:?}; the body was buffered, which would \
         make every ported SSE route arrive all at once at the end"
    );
    let second = next_chunk(&mut stream).await.expect("second event");
    assert_eq!(second, b"data: second\n\n");
    assert!(started.elapsed() >= Duration::from_millis(200));

    assert_eq!(
        *worker.seen.last_body.lock().expect("body"),
        json!({"stream": true}),
        "VS Code signals streaming in the body, never with an Accept header"
    );
}

#[tokio::test]
async fn a_refused_stream_is_an_error_before_any_bytes_flow() {
    // A 401 delivered as an event stream would otherwise reach the browser as
    // an empty, successful-looking chat.
    let worker = FakeWorker::start().await;
    let err = worker
        .client()
        .stream_sse("/refuse", &json!({}))
        .await
        .expect_err("a non-2xx stream must not become a stream");
    assert_eq!(err.status(), Some(403));
}

async fn next_chunk<S>(stream: &mut std::pin::Pin<Box<S>>) -> Option<Vec<u8>>
where
    S: Stream<Item = Result<bytes::Bytes, reqwest::Error>> + ?Sized,
{
    std::future::poll_fn(|cx| stream.as_mut().poll_next(cx))
        .await
        .map(|chunk| chunk.expect("chunk").to_vec())
}
