//! The dry-run routes and the note ingest, over real sockets.
//!
//! Two things are being proved, and only one of them is about HTTP:
//!
//! * `/rust/ingest/plan` and `/rust/ingest/chunk` answer what they promise and
//!   **write nothing** — the plan test asserts the folder is byte-for-byte
//!   unchanged afterwards, which is the only honest way to test a dry run;
//! * [`NoteIngestor`] writes a watched note through `GraphWriter` in this
//!   process, asks the worker only for the compute it owns, and **never** asks
//!   it to write. The "worker" here is a throwaway axum app on a loopback port
//!   that mounts `/knowledge-graph/ingest` as a tripwire: v11.6.0 removed that
//!   route from the worker's allowlist while this crate still posted to it, so
//!   the regression to guard is a request that should not exist rather than a
//!   reply that should.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
use std::net::SocketAddr;
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use axum::routing::{get, post};
use axum::{Json, Router};
use lattice_core::db::Store;
use lattice_core::graph_write::GraphWriter;
use lattice_core::worker::WorkerSeamClient;
use lattice_ingest::api::{router, IngestApiConfig, CHUNK_PATH, PLAN_PATH};
use lattice_ingest::worker::{NoteIngestError, NoteIngestor, NoteSubmission};
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

/// Every path the stand-in worker was asked for, in order.
#[derive(Clone, Default)]
struct SeamLog {
    paths: Arc<Mutex<Vec<String>>>,
}

impl SeamLog {
    fn hit(&self, path: &str) {
        self.paths.lock().expect("lock").push(path.to_string());
    }

    fn count(&self, path: &str) -> usize {
        self.paths
            .lock()
            .expect("lock")
            .iter()
            .filter(|seen| *seen == path)
            .count()
    }
}

/// A stand-in worker: the two compute seams, plus the retired write door as a
/// tripwire. `/knowledge-graph/ingest` is mounted **and answers 200**, so a
/// request to it would look like a success from the client's side — which is
/// exactly why "nobody asked" has to be asserted rather than inferred.
fn fake_worker(seams: SeamLog, embedder: Option<(String, usize)>) -> Router {
    Router::new()
        .route(
            "/worker/extract",
            post({
                let seams = seams.clone();
                move |Json(_body): Json<Value>| {
                    let seams = seams.clone();
                    async move {
                        seams.hit("/worker/extract");
                        Json(json!({
                            "concepts": [{"text": "회의록", "node_type": "Concept"}],
                            "triples": [],
                            "semantic": [],
                        }))
                    }
                }
            }),
        )
        .route(
            "/worker/embed",
            post({
                let seams = seams.clone();
                move |Json(body): Json<Value>| {
                    let seams = seams.clone();
                    let embedder = embedder.clone();
                    async move {
                        seams.hit("/worker/embed");
                        let native = lattice_core::embeddings::LocalEmbeddingModel::from_env();
                        let (model_id, dim) = embedder
                            .unwrap_or_else(|| (native.model_id().to_string(), native.dim()));
                        let vectors: Vec<Vec<f64>> = body["texts"]
                            .as_array()
                            .cloned()
                            .unwrap_or_default()
                            .iter()
                            .map(|text| {
                                let mut values = native.embed(text.as_str().unwrap_or(""));
                                values.resize(dim, 0.0);
                                values
                            })
                            .collect();
                        Json(json!({
                            "vectors": vectors,
                            "dim": dim,
                            "model_id": model_id,
                            "kind": body["kind"].as_str().unwrap_or("passage"),
                        }))
                    }
                }
            }),
        )
        .route(
            "/knowledge-graph/ingest",
            post({
                let seams = seams.clone();
                move |Json(_body): Json<Value>| {
                    let seams = seams.clone();
                    async move {
                        seams.hit("/knowledge-graph/ingest");
                        Json(json!({"status": "ok", "duplicate": false, "node_id": "webdoc:abc"}))
                    }
                }
            }),
        )
        .route("/health", get(|| async { "ok" }))
}

fn writer(dir: &Path) -> GraphWriter {
    let store = Arc::new(Store::open(&dir.join("knowledge_graph.sqlite")).expect("store"));
    GraphWriter::open(store, dir.join("knowledge_graph_blobs")).expect("writer")
}

#[tokio::test]
async fn a_watched_note_is_written_here_and_never_posted_to_the_worker() {
    let seams = SeamLog::default();
    let (origin, handle) = serve(fake_worker(seams.clone(), None)).await;
    let dir = tempfile::tempdir().expect("tempdir");
    let graph = writer(dir.path());
    let ingestor = NoteIngestor::new(graph.clone())
        .with_worker_origin(&origin)
        .expect("seam");

    let note = NoteSubmission::from_watched_file(
        Path::new("/brain/notes"),
        "회의/2026-08-11.md",
        "결정 사항을 정리했습니다.",
        Some("watch_abc"),
    );
    let receipt = ingestor
        .ingest_note(&note, Some("owner@lattice.test"), Some("personal"))
        .await
        .expect("the note lands");
    assert!(receipt.node_id.starts_with("webdoc:"), "{receipt:?}");
    assert!(receipt.provenance_id.is_some());
    assert!(receipt.indexed, "the seam's embedder is the native one");

    // The whole point: the compute seams were asked, the write door was not.
    assert_eq!(seams.count("/worker/extract"), 1);
    assert_eq!(
        seams.count("/worker/embed"),
        2,
        "once for the document vector, once for every chunk in a single batch \
         — the `enrich` chain the upload door uses, not a second copy of it"
    );
    assert_eq!(
        seams.count("/knowledge-graph/ingest"),
        0,
        "the graph write is native; a request here means the watcher is posting \
         into a route the worker stopped serving in v11.6.0"
    );

    // …and the note really is in the Brain, concept and vector included.
    let node_id = receipt.node_id.clone();
    let (documents, concepts, vectors): (i64, i64, i64) = graph
        .store()
        .with_read_conn(|conn| {
            let documents = conn
                .query_row(
                    "SELECT COUNT(*) FROM nodes WHERE id = ?1 AND type = 'Document'",
                    [&node_id],
                    |row| row.get(0),
                )
                .unwrap_or(0);
            let concepts = conn
                .query_row(
                    "SELECT COUNT(*) FROM nodes WHERE type = 'Concept'",
                    [],
                    |row| row.get(0),
                )
                .unwrap_or(0);
            let vectors = conn
                .query_row(
                    "SELECT COUNT(*) FROM vector_embeddings WHERE item_id = ?1",
                    [&node_id],
                    |row| row.get(0),
                )
                .unwrap_or(0);
            Ok((documents, concepts, vectors))
        })
        .expect("read");
    assert_eq!(documents, 1, "the watched note became a Document");
    assert_eq!(concepts, 1, "the extract seam's concept was attached");
    assert!(vectors >= 1, "the note is searchable by vector");
    handle.abort();
}

#[tokio::test]
async fn a_divergent_embedder_supplies_its_vector_but_licenses_no_native_sync() {
    // W2 §4: `similarity()` raises on a width mismatch, so a native re-embed
    // under a model the provider does not use silently kills vector search.
    // The supplied row is still filed; the incremental native sync is not run.
    let seams = SeamLog::default();
    let (origin, handle) = serve(fake_worker(
        seams.clone(),
        Some(("other-model:8".into(), 8)),
    ))
    .await;
    let dir = tempfile::tempdir().expect("tempdir");
    let ingestor = NoteIngestor::new(writer(dir.path()))
        .with_worker_origin(&origin)
        .expect("seam");
    let note = NoteSubmission::from_watched_file(Path::new("/root"), "a.md", "본문", None);
    let receipt = ingestor
        .ingest_note(&note, None, None)
        .await
        .expect("the note still lands");
    assert!(
        !receipt.indexed,
        "a provider the native embedder cannot reproduce is backlog, not a write"
    );
    assert_eq!(seams.count("/knowledge-graph/ingest"), 0);
    handle.abort();
}

#[tokio::test]
async fn an_unreachable_worker_costs_the_note_its_concepts_not_its_place() {
    // Bind and immediately drop, so the port is almost certainly closed. Before
    // v11.7.0 this was the *whole* ingest path, and an unreachable worker meant
    // the note was gone.
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind");
    let addr = listener.local_addr().expect("addr");
    drop(listener);
    let dir = tempfile::tempdir().expect("tempdir");
    let graph = writer(dir.path());
    let ingestor = NoteIngestor::new(graph.clone())
        .with_worker_origin(format!("http://{addr}"))
        .expect("seam");
    let note = NoteSubmission::from_watched_file(Path::new("/root"), "a.md", "본문", None);
    let receipt = ingestor
        .ingest_note(&note, None, None)
        .await
        .expect("the note lands anyway");
    let concepts: i64 = graph
        .store()
        .with_read_conn(|conn| {
            Ok(conn
                .query_row(
                    "SELECT COUNT(*) FROM nodes WHERE type = 'Concept'",
                    [],
                    |row| row.get(0),
                )
                .unwrap_or(0))
        })
        .expect("read");
    assert_eq!(concepts, 0, "no seam, no concepts");
    assert!(!receipt.node_id.is_empty(), "…but the note is in the Brain");
}

#[tokio::test]
async fn an_empty_note_is_refused_before_anything_is_written() {
    let dir = tempfile::tempdir().expect("tempdir");
    let graph = writer(dir.path());
    let ingestor = NoteIngestor::new(graph.clone());
    let blank = NoteSubmission::from_watched_file(Path::new("/root"), "a.md", "\t \n", None);
    assert!(matches!(
        ingestor.ingest_note(&blank, None, None).await,
        Err(NoteIngestError::Empty)
    ));
    let nodes: i64 = graph
        .store()
        .with_read_conn(|conn| {
            Ok(conn
                .query_row("SELECT COUNT(*) FROM nodes", [], |row| row.get(0))
                .unwrap_or(0))
        })
        .expect("read");
    assert_eq!(nodes, 0);
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
