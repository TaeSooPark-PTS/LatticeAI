//! The dry-run routes and the worker delegation, over real sockets.
//!
//! Two things are being proved, and only one of them is about HTTP:
//!
//! * `/rust/ingest/plan` and `/rust/ingest/chunk` answer what they promise and
//!   **write nothing** — the plan test asserts the folder is byte-for-byte
//!   unchanged afterwards, which is the only honest way to test a dry run;
//! * [`WorkerClient`] speaks the body the Python endpoint parses, and reports a
//!   refusal as a refusal rather than swallowing it. The "worker" here is a
//!   throwaway axum app on a loopback port, so nothing depends on a real
//!   install being up.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
use std::net::SocketAddr;
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use axum::routing::{get, post};
use axum::{Json, Router};
use lattice_ingest::api::{router, IngestApiConfig, CHUNK_PATH, PLAN_PATH};
use lattice_ingest::worker::{NoteSubmission, WorkerClient, WorkerError};
use serde_json::{json, Value};

/// Serve `app` on a loopback port and return its origin.
async fn serve(app: Router) -> (String, tokio::task::JoinHandle<()>) {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind loopback");
    let addr: SocketAddr = listener.local_addr().expect("addr");
    let handle = tokio::spawn(async move {
        let _ = axum::serve(listener, app).await;
    });
    (format!("http://{addr}"), handle)
}

fn client() -> reqwest::Client {
    reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(10))
        .build()
        .expect("client")
}

async fn get_json(origin: &str, path_and_query: &str) -> (u16, Value) {
    let response = client()
        .get(format!("{origin}{path_and_query}"))
        .send()
        .await
        .expect("request");
    let status = response.status().as_u16();
    let text = response.text().await.expect("body");
    (status, serde_json::from_str(&text).unwrap_or(Value::Null))
}

async fn post_json(origin: &str, path: &str, body: Value) -> (u16, Value) {
    let response = client()
        .post(format!("{origin}{path}"))
        .header("content-type", "application/json")
        .body(serde_json::to_vec(&body).expect("encode"))
        .send()
        .await
        .expect("request");
    let status = response.status().as_u16();
    let text = response.text().await.expect("body");
    (status, serde_json::from_str(&text).unwrap_or(Value::Null))
}

fn write(root: &Path, relative: &str, contents: &str) {
    let path = root.join(relative);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).expect("mkdir");
    }
    std::fs::write(path, contents).expect("write");
}

/// Every file under `root`, as `(relative path, bytes)` — the "nothing moved"
/// evidence for the dry-run claim.
fn fingerprint(root: &Path) -> Vec<(String, Vec<u8>)> {
    fn walk(root: &Path, current: &Path, out: &mut Vec<(String, Vec<u8>)>) {
        let mut entries: Vec<_> = std::fs::read_dir(current)
            .expect("read_dir")
            .filter_map(Result::ok)
            .collect();
        entries.sort_by_key(std::fs::DirEntry::file_name);
        for entry in entries {
            let path = entry.path();
            if path.is_dir() {
                walk(root, &path, out);
            } else {
                let relative = path
                    .strip_prefix(root)
                    .expect("under root")
                    .display()
                    .to_string();
                out.push((relative, std::fs::read(&path).expect("read")));
            }
        }
    }
    let mut out = Vec::new();
    walk(root, root, &mut out);
    out
}

#[tokio::test]
async fn the_plan_route_describes_a_folder_and_changes_nothing() {
    let dir = tempfile::tempdir().expect("tempdir");
    write(dir.path(), "guide.md", "# 제목\n본문입니다. 두 번째 문장.");
    write(dir.path(), "module.py", "def alpha():\n    return 1\n");
    write(dir.path(), "notes.txt", "그냥 산문입니다. 두 문장째.");
    write(dir.path(), "report.pdf", "%PDF-1.4 not really");
    write(dir.path(), "picture.png", "not admitted");
    write(dir.path(), "node_modules/dep.js", "not admitted");
    write(dir.path(), "deep/inner.md", "# 안쪽\n내용");
    let before = fingerprint(dir.path());

    let (origin, handle) = serve(router(IngestApiConfig::default())).await;
    let (status, body) = get_json(
        &origin,
        &format!(
            "{PLAN_PATH}?path={}",
            urlencode(&dir.path().display().to_string())
        ),
    )
    .await;
    assert_eq!(status, 200, "{body}");
    assert_eq!(body["dry_run"], Value::Bool(true));
    assert_eq!(body["matched"], Value::from(5));
    assert_eq!(body["truncated"], Value::Bool(false));

    let files = body["files"].as_array().expect("files");
    let named: std::collections::BTreeMap<&str, &Value> = files
        .iter()
        .map(|file| (file["relative_path"].as_str().expect("relative_path"), file))
        .collect();
    assert_eq!(
        named.keys().copied().collect::<Vec<_>>(),
        vec![
            "deep/inner.md",
            "guide.md",
            "module.py",
            "notes.txt",
            "report.pdf"
        ]
    );
    assert_eq!(named["guide.md"]["strategy"], Value::from("markdown"));
    assert_eq!(named["module.py"]["strategy"], Value::from("code"));
    assert_eq!(named["notes.txt"]["strategy"], Value::from("prose"));
    assert_eq!(named["guide.md"]["chunks"], Value::from(1));
    // Character counts, not byte counts — the same choice the chunker makes.
    assert_eq!(named["guide.md"]["chars"], Value::from(20));
    assert!(
        named["guide.md"]["bytes"].as_u64() > named["guide.md"]["chars"].as_u64(),
        "the file is larger in bytes than in characters, and `chars` is the character count"
    );
    // A PDF is admitted but not counted: extraction is the worker's job.
    assert_eq!(named["report.pdf"]["chunks"], Value::Null);
    assert!(named["report.pdf"]["note"]
        .as_str()
        .expect("note")
        .contains("worker"));
    assert_eq!(body["totals"]["by_strategy"]["markdown"], Value::from(2));

    assert_eq!(fingerprint(dir.path()), before, "a dry run must not write");
    handle.abort();
}

#[tokio::test]
async fn the_plan_route_honours_recursive_and_limit_and_names_bad_fields() {
    let dir = tempfile::tempdir().expect("tempdir");
    for index in 0..6 {
        write(dir.path(), &format!("top-{index}.md"), "x");
    }
    write(dir.path(), "deep/inner.md", "x");
    let (origin, handle) = serve(router(IngestApiConfig::default())).await;
    let encoded = urlencode(&dir.path().display().to_string());

    let (_, all) = get_json(&origin, &format!("{PLAN_PATH}?path={encoded}")).await;
    assert_eq!(all["matched"], Value::from(7));

    let (_, flat) = get_json(
        &origin,
        &format!("{PLAN_PATH}?path={encoded}&recursive=false"),
    )
    .await;
    assert_eq!(flat["matched"], Value::from(6));
    assert_eq!(flat["recursive"], Value::Bool(false));

    let (_, capped) = get_json(&origin, &format!("{PLAN_PATH}?path={encoded}&limit=2")).await;
    assert_eq!(capped["matched"], Value::from(7));
    assert_eq!(capped["reported"], Value::from(2));
    assert_eq!(capped["truncated"], Value::Bool(true));

    for (query, field) in [
        (PLAN_PATH.to_string(), "path"),
        (format!("{PLAN_PATH}?path=   "), "path"),
        (
            format!("{PLAN_PATH}?path={encoded}&recursive=maybe"),
            "recursive",
        ),
        (format!("{PLAN_PATH}?path={encoded}&limit=0"), "limit"),
        (format!("{PLAN_PATH}?path={encoded}&limit=lots"), "limit"),
    ] {
        let (status, body) = get_json(&origin, &query).await;
        assert_eq!(status, 400, "{query}");
        assert_eq!(body["field"], Value::from(field), "{query}");
    }

    let (status, body) = get_json(&origin, &format!("{PLAN_PATH}?path=/nope/not/here")).await;
    assert_eq!(status, 404);
    assert_eq!(body["error"], Value::from("folder_unavailable"));
    handle.abort();
}

#[tokio::test]
async fn the_chunk_route_is_the_pure_chunker_with_ids() {
    let (origin, handle) = serve(router(IngestApiConfig::default())).await;

    let (status, body) = post_json(
        &origin,
        CHUNK_PATH,
        json!({
            "text": "# 안내서\n첫 문장입니다. 두 번째 문장입니다.",
            "filename": "guide.md",
            "source_node_id": "file:guide",
        }),
    )
    .await;
    assert_eq!(status, 200, "{body}");
    assert_eq!(body["dry_run"], Value::Bool(true));
    assert_eq!(body["strategy"], Value::from("markdown"));
    assert_eq!(body["requested_strategy"], Value::Null);
    assert_eq!(body["chunk_count"], Value::from(1));
    let chunk = &body["chunks"][0];
    assert_eq!(chunk["meta"]["heading_path"], Value::from("안내서"));
    assert_eq!(chunk["meta"]["start_char"], Value::from(0));
    assert!(chunk["chunk_id"]
        .as_str()
        .expect("id")
        .starts_with("chunk:"));
    assert!(chunk["len_bytes"].as_u64() > chunk["len_chars"].as_u64());
    assert_eq!(body["text_hash"].as_str().expect("hash").len(), 64);

    // An explicit strategy overrides the filename, and small windows work.
    let (_, plain) = post_json(
        &origin,
        CHUNK_PATH,
        json!({"text": "가나다라마바사", "filename": "guide.md", "strategy": "plain", "size": 3, "overlap": 1}),
    )
    .await;
    assert_eq!(plain["strategy"], Value::from("plain"));
    assert_eq!(plain["requested_strategy"], Value::from("plain"));
    assert_eq!(plain["chunk_count"], Value::from(3));
    assert_eq!(plain["chunks"][1]["text"], Value::from("다라마"));
    assert_eq!(plain["chunks"][1]["meta"]["start_char"], Value::from(2));

    // Whitespace-only text is zero chunks, not an error.
    let (status, empty) = post_json(&origin, CHUNK_PATH, json!({"text": "   \n "})).await;
    assert_eq!(status, 200);
    assert_eq!(empty["chunk_count"], Value::from(0));
    handle.abort();
}

#[tokio::test]
async fn the_chunk_route_refuses_what_it_cannot_chunk() {
    let config = IngestApiConfig {
        max_chunk_chars: 16,
        ..IngestApiConfig::default()
    };
    let (origin, handle) = serve(router(config)).await;
    for (body, field) in [
        (json!({}), "text"),
        (json!({"text": null}), "text"),
        (json!({"text": 7}), "text"),
        (json!({"text": "가".repeat(17)}), "text"),
        (json!({"text": "ok", "size": "big"}), "size"),
        (json!({"text": "ok", "overlap": 1.5}), "overlap"),
    ] {
        let (status, answer) = post_json(&origin, CHUNK_PATH, body).await;
        assert_eq!(status, 400, "{answer}");
        assert_eq!(answer["field"], Value::from(field), "{answer}");
    }
    let (status, answer) = post_json(&origin, CHUNK_PATH, json!([1, 2])).await;
    assert_eq!(status, 400, "{answer}");
    handle.abort();
}

/// A stand-in worker that records what it was asked to ingest.
fn fake_worker(seen: Arc<Mutex<Vec<Value>>>, status: u16) -> Router {
    Router::new()
        .route(
            "/knowledge-graph/ingest",
            post(move |Json(body): Json<Value>| {
                let seen = seen.clone();
                async move {
                    seen.lock().expect("lock").push(body);
                    (
                        axum::http::StatusCode::from_u16(status).expect("status"),
                        Json(json!({"status": "ok", "duplicate": false, "node_id": "webdoc:abc"})),
                    )
                }
            }),
        )
        .route("/health", get(|| async { "ok" }))
}

#[tokio::test]
async fn delegation_sends_the_body_the_python_endpoint_parses() {
    let seen = Arc::new(Mutex::new(Vec::new()));
    let (origin, handle) = serve(fake_worker(seen.clone(), 200)).await;
    let client = WorkerClient::new(&origin)
        .expect("client")
        .with_header("x-lattice-test", "1");

    let note = NoteSubmission::from_watched_file(
        Path::new("/brain/notes"),
        "회의/2026-08-11.md",
        "결정 사항을 정리했습니다.",
        Some("watch_abc"),
    );
    let answer = client.submit_note(&note).await.expect("delegation");
    assert_eq!(answer["status"], Value::from("ok"));

    let recorded = seen.lock().expect("lock").clone();
    assert_eq!(recorded.len(), 1);
    let body = &recorded[0];
    assert_eq!(body["type"], Value::from("note"));
    assert_eq!(body["title"], Value::from("2026-08-11.md"));
    assert_eq!(body["content"], Value::from("결정 사항을 정리했습니다."));
    assert_eq!(
        body["metadata"]["relative_path"],
        Value::from("회의/2026-08-11.md")
    );
    assert_eq!(body["metadata"]["watch_id"], Value::from("watch_abc"));
    assert_eq!(body["metadata"]["folder_watch"], Value::Bool(true));
    handle.abort();
}

#[tokio::test]
async fn a_refusal_is_reported_as_a_refusal() {
    let seen = Arc::new(Mutex::new(Vec::new()));
    let (origin, handle) = serve(fake_worker(seen, 403)).await;
    let client = WorkerClient::new(&origin).expect("client");
    let note = NoteSubmission::from_watched_file(Path::new("/root"), "a.md", "x", None);
    match client.submit_note(&note).await {
        Err(WorkerError::Rejected { status, detail }) => {
            assert_eq!(status, 403);
            assert!(!detail.is_empty());
        }
        other => panic!("expected a rejection, got {other:?}"),
    }
    handle.abort();
}

#[tokio::test]
async fn an_unreachable_worker_is_an_error_not_a_silent_success() {
    // Bind and immediately drop, so the port is almost certainly closed.
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind");
    let addr = listener.local_addr().expect("addr");
    drop(listener);
    let client = WorkerClient::new(format!("http://{addr}")).expect("client");
    let note = NoteSubmission::from_watched_file(Path::new("/root"), "a.md", "x", None);
    assert!(matches!(
        client.submit_note(&note).await,
        Err(WorkerError::Transport(_))
    ));
}

#[tokio::test]
async fn a_worker_that_answers_with_html_is_malformed_not_ok() {
    let app = Router::new().route(
        "/knowledge-graph/ingest",
        post(|| async { "<html>login</html>" }),
    );
    let (origin, handle) = serve(app).await;
    let client = WorkerClient::new(&origin).expect("client");
    let note = NoteSubmission::from_watched_file(Path::new("/root"), "a.md", "x", None);
    assert!(matches!(
        client.submit_note(&note).await,
        Err(WorkerError::Malformed(_))
    ));
    handle.abort();
}

/// Percent-encode the few characters a temp path can contain that a query
/// string would otherwise eat. Small on purpose: a full encoder is not the
/// thing under test.
fn urlencode(raw: &str) -> String {
    let mut out = String::with_capacity(raw.len());
    for byte in raw.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'.' | b'_' | b'~' | b'/' => {
                out.push(byte as char);
            }
            other => out.push_str(&format!("%{other:02X}")),
        }
    }
    out
}
