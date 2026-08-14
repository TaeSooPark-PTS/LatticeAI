//! Fixture seeding for the brain HTTP replay harness.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
#![allow(dead_code, unused_imports)]
use std::collections::HashMap;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use axum::extract::Json;
use axum::http::StatusCode;
use axum::response::IntoResponse;
use axum::routing::post;
use axum::Router;
use lattice_auth::{AuthConfig, AuthState, Clock, OrderedMap};
use lattice_core::db::{RuntimeConfig, Store};
use lattice_core::worker::WorkerSeamClient;
use lattice_retrieval::memory_api::shared::BrainState;
use lattice_retrieval::{
    brain_api, chronicle_api, command_center_api, evidence_api, garden_api, memory_api,
};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

pub(crate) fn seed_users(dir: &Path) {
    let email = "owner@fixture.local";
    let mut owner = OrderedMap::new();
    owner.insert("password", json!("x"));
    owner.insert("name", json!("owner"));
    owner.insert("nickname", json!("owner"));
    owner.insert("role", json!("admin"));
    owner.insert("disabled", json!(false));
    owner.insert("id", json!(lattice_auth::stable_user_id(email)));
    owner.insert("email", json!(email));
    let mut users = OrderedMap::new();
    users.insert(email, serde_json::to_value(owner).unwrap());
    std::fs::write(
        dir.join("users.json"),
        lattice_auth::pyjson::dumps_indent2(&users).expect("users"),
    )
    .expect("write users");
}

pub(crate) fn seed_schema(dir: &Path) {
    let dest = dir.join("knowledge_graph.sqlite");
    let source: PathBuf = [
        env!("CARGO_MANIFEST_DIR"),
        "..",
        "fixtures",
        "http",
        "brain_store.sqlite",
    ]
    .iter()
    .collect();
    if source.exists() {
        // Open the Python-seeded capture store (scripts/gen_http_fixtures_brain.py
        // --seed-store-only). Copy, never mutate the committed file. WAL sidecars
        // stay next to the temp copy.
        std::fs::copy(&source, &dest).expect("copy brain_store.sqlite");
        let conn = rusqlite::Connection::open(&dest).expect("sqlite");
        seed_history_overlay(&conn);
        seed_embedding_padding(&conn);
        seed_vector_jobs_table(&conn);
        seed_review_items(&conn);
        seed_chronicle_padding(&conn);
        return;
    }
    let conn = rusqlite::Connection::open(&dest).expect("sqlite");
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS conversation_messages (
            id INTEGER PRIMARY KEY,
            conversation_id TEXT, role TEXT, content TEXT,
            user_email TEXT, user_nickname TEXT, source TEXT,
            timestamp TEXT, metadata_json TEXT,
            workspace_id TEXT, organization_id TEXT
         );
         CREATE TABLE IF NOT EXISTS nodes (
            id TEXT PRIMARY KEY, type TEXT, title TEXT, summary TEXT,
            metadata_json TEXT, created_at TEXT, updated_at TEXT
         );
         CREATE TABLE IF NOT EXISTS edges (
            id TEXT PRIMARY KEY, from_node TEXT, to_node TEXT, type TEXT,
            weight REAL, metadata_json TEXT, created_at TEXT
         );
         CREATE TABLE IF NOT EXISTS nodes_v2 (
            id TEXT PRIMARY KEY, type TEXT, label TEXT, legacy_type TEXT,
            workspace_id TEXT, created_at TEXT, updated_at TEXT
         );
         CREATE TABLE IF NOT EXISTS edges_v2 (
            id TEXT PRIMARY KEY, source TEXT, target TEXT, type TEXT, created_at TEXT
         );
         CREATE TABLE IF NOT EXISTS chunks (
            id TEXT PRIMARY KEY, source_node TEXT, text TEXT, metadata_json TEXT, created_at TEXT
         );
         CREATE TABLE IF NOT EXISTS knowledge_sources (id TEXT PRIMARY KEY);
         CREATE TABLE IF NOT EXISTS local_file_index (id TEXT PRIMARY KEY, status TEXT);
         CREATE TABLE IF NOT EXISTS graph_meta (key TEXT PRIMARY KEY, value TEXT);
         CREATE TABLE IF NOT EXISTS kg_meta (key TEXT PRIMARY KEY, value TEXT);
         CREATE TABLE IF NOT EXISTS vector_jobs (id INTEGER PRIMARY KEY, status TEXT);
         CREATE TABLE IF NOT EXISTS vector_embeddings (
            item_id TEXT PRIMARY KEY, item_type TEXT, source_node TEXT,
            text_hash TEXT, embedding_dim INTEGER, embedding_model TEXT, indexed_at TEXT
         );
         CREATE TABLE IF NOT EXISTS vector_index_operations (
            id INTEGER PRIMARY KEY, operation TEXT, status TEXT,
            requested_at TEXT, started_at TEXT, completed_at TEXT,
            items_total INT, items_indexed INT, items_skipped INT,
            error_message TEXT, metadata_json TEXT
         );
         CREATE TABLE IF NOT EXISTS ingestion_provenance (
            id TEXT PRIMARY KEY, node_id TEXT, title TEXT, source_type TEXT,
            captured_at TEXT, created_at TEXT, workspace_id TEXT
         );
         CREATE TABLE IF NOT EXISTS workspace_os_state (
            id TEXT PRIMARY KEY, state_json TEXT NOT NULL, updated_at TEXT NOT NULL
         );",
    )
    .expect("schema");
    seed_history_overlay(&conn);
    seed_embedding_padding(&conn);
    seed_vector_jobs_table(&conn);
    seed_capture_graph(&conn);
}

/// Health's embedding_coverage counts every node (visible or not). The
/// original capture had 64 embeddable items / 63 ready; the committed store
/// has 62 / 61. Two Preference rows (not in GRAPH_VISIBLE_TYPES) pad the
/// index without changing the 36-node sample the other dimensions read.
pub(crate) fn seed_embedding_padding(conn: &rusqlite::Connection) {
    let ready: i64 = conn
        .query_row("SELECT COUNT(*) FROM vector_embeddings", [], |row| {
            row.get(0)
        })
        .unwrap_or(0);
    if ready >= 63 {
        return;
    }
    let stamp = "2026-08-14T12:00:00";
    let model = "lattice-local-hash-v1:384";
    let needed = 63 - ready;
    let has_raw = table_has_column(conn, "nodes", "raw_json");
    let has_blob = table_has_column(conn, "vector_embeddings", "embedding");
    // Preference nodes are not GRAPH_VISIBLE, so the 36-node sample stays put.
    for i in 0..needed {
        let id = format!("p-pad-{i:02}");
        let title = format!("Preference pad {i:02}");
        let text = lattice_core::clean_text(&format!("{title}\n\n"));
        let hash = format!("{:x}", Sha256::digest(text.as_bytes()));
        if has_raw {
            conn.execute(
                "INSERT OR IGNORE INTO nodes(id, type, title, summary, metadata_json, raw_json, created_at, updated_at)
                 VALUES (?, 'Preference', ?, '', '{}', '{}', ?, ?)",
                rusqlite::params![&id, title, stamp, stamp],
            )
            .expect("pad node");
        } else {
            conn.execute(
                "INSERT OR IGNORE INTO nodes(id, type, title, summary, metadata_json, created_at, updated_at)
                 VALUES (?, 'Preference', ?, '', '{}', ?, ?)",
                rusqlite::params![&id, title, stamp, stamp],
            )
            .expect("pad node");
        }
        if has_blob {
            conn.execute(
                "INSERT OR IGNORE INTO vector_embeddings(item_id, item_type, source_node, text_hash,
                    embedding, embedding_dim, embedding_model, metadata_json, indexed_at)
                 VALUES (?, 'node', ?, ?, ?, 384, ?, '{}', ?)",
                rusqlite::params![&id, &id, hash, vec![0u8; 16], model, stamp],
            )
            .expect("pad embedding");
        } else {
            conn.execute(
                "INSERT OR IGNORE INTO vector_embeddings(item_id, item_type, source_node, text_hash,
                    embedding_dim, embedding_model, indexed_at)
                 VALUES (?, 'node', ?, ?, 384, ?, ?)",
                rusqlite::params![&id, &id, hash, model, stamp],
            )
            .expect("pad embedding");
        }
    }
}

pub(crate) fn seed_review_items(conn: &rusqlite::Connection) {
    let Ok(raw) = conn.query_row(
        "SELECT state_json FROM workspace_os_state WHERE id='current'",
        [],
        |row| row.get::<_, String>(0),
    ) else {
        return;
    };
    let Ok(mut doc) = serde_json::from_str::<Value>(&raw) else {
        return;
    };
    let mut items = vec![
        json!({
            "id": "review-fixture-python",
            "status": "pending",
            "title": "나에 대한 새 사실: 파이썬",
            "summary": "대화에서 '파이썬'를 읽었습니다. 내 프로필(선호)에 추가할까요? 승인하기 전에는 저장되지 않습니다.",
            "source": "kg_change_digest",
            "kind": "self_model_fact",
            "workspace_id": "personal",
        }),
        json!({
            "id": "review-fixture-habit",
            "status": "pending",
            "title": "나에 대한 새 사실: 매일 아침 회의록을 정리합니다",
            "summary": "대화에서 '매일 아침 회의록을 정리합니다'를 읽었습니다. 내 프로필(습관)에 추가할까요? 승인하기 전에는 저장되지 않습니다.",
            "source": "kg_change_digest",
            "kind": "self_model_fact",
            "workspace_id": "personal",
        }),
        json!({
            "id": "review-fixture-fusion",
            "status": "pending",
            "title": "나에 대한 새 사실: keep alpha fusion",
            "summary": "대화에서 'keep alpha fusion'를 읽었습니다. 내 프로필(결정)에 추가할까요? 승인하기 전에는 저장되지 않습니다.",
            "source": "kg_change_digest",
            "kind": "self_model_fact",
            "workspace_id": "personal",
        }),
    ];
    // Command-center briefing was captured after synthesize had opened 11
    // more items. Those are not `kg_change_digest`, so proactive-brief
    // still reports the three self-model facts.
    for i in 0..11 {
        items.push(json!({
            "id": format!("review-fixture-briefing-{i:02}"),
            "status": "pending",
            "title": format!("briefing pad {i:02}"),
            "summary": "seeded so GET /api/command/briefing pending=14",
            "source": "briefing_pad",
            "kind": "suggestion",
            "workspace_id": "personal",
        }));
    }
    if let Some(object) = doc.as_object_mut() {
        object.insert("review_items".into(), Value::Array(items));
    }
    let text = serde_json::to_string(&doc).expect("wsos");
    conn.execute(
        "UPDATE workspace_os_state SET state_json=? WHERE id='current'",
        [text],
    )
    .expect("wsos update");
}

/// Self-model nodes written after `--seed-store-only` persist (briefing /
/// synthesize later phases). Chronicle entities count them.
pub(crate) fn seed_chronicle_padding(conn: &rusqlite::Connection) {
    let stamp = "2026-08-14T12:00:00";
    let _ = conn.execute(
        "INSERT OR IGNORE INTO nodes_v2(
            id, type, legacy_type, label, summary, attrs,
            workspace_id, visibility, created_at, updated_at, importance_score
         ) VALUES (
            'self:root', 'SELF', 'Self', '나', '', '{}',
            'personal', 'private', ?, ?, 0.0)",
        rusqlite::params![stamp, stamp],
    );
    let _ = conn.execute(
        "INSERT OR IGNORE INTO nodes_v2(
            id, type, legacy_type, label, summary, attrs,
            workspace_id, visibility, created_at, updated_at, importance_score
         ) VALUES (
            'self:preference:e28d88323aff', 'PREFERENCE', 'Preference',
            '답변은 한국어로 받고 싶습니다', '', '{}',
            'personal', 'private', ?, ?, 0.0)",
        rusqlite::params![stamp, stamp],
    );
    let connections: i64 = conn
        .query_row("SELECT COUNT(*) FROM edges_v2", [], |row| row.get(0))
        .unwrap_or(0);
    if connections < 69 {
        let _ = conn.execute(
            "INSERT OR IGNORE INTO edges_v2(
                id, source, target, type, legacy_type, weight, confidence,
                evidence, metadata, created_by, created_at
             ) VALUES (
                'self-related',
                'self:root', 'self:preference:e28d88323aff',
                'RELATED', '', 1.0, 1.0, '[]', '{}', 'user', ?
             )",
            [stamp],
        );
    }
}

pub(crate) fn seed_vector_jobs_table(conn: &rusqlite::Connection) {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS vector_jobs (
            id INTEGER PRIMARY KEY,
            status TEXT
         );",
    )
    .expect("vector_jobs");
}

pub(crate) fn table_has_column(conn: &rusqlite::Connection, table: &str, column: &str) -> bool {
    conn.prepare(&format!("PRAGMA table_info({table})"))
        .ok()
        .and_then(|mut stmt| {
            let names: Vec<String> = stmt
                .query_map([], |row| row.get::<_, String>(1))
                .ok()?
                .filter_map(Result::ok)
                .collect();
            Some(names.iter().any(|name| name == column))
        })
        .unwrap_or(false)
}

/// Command-search conversation hits come from two chat turns the HTTP
/// capture recorded *after* seed. Overlay them onto the Python store (and
/// onto the fallback schema) so those cases stay green without replaying
/// the whole chat family.
pub(crate) fn seed_history_overlay(conn: &rusqlite::Connection) {
    let has_hash: bool = conn
        .prepare("PRAGMA table_info(conversation_messages)")
        .ok()
        .and_then(|mut stmt| {
            let names: Vec<String> = stmt
                .query_map([], |row| row.get::<_, String>(1))
                .ok()?
                .filter_map(Result::ok)
                .collect();
            Some(names.iter().any(|name| name == "message_hash"))
        })
        .unwrap_or(false);
    if has_hash {
        // Python store: chat captures already wrote the rows (with hashes).
        return;
    }
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS conversation_messages (
            id INTEGER PRIMARY KEY,
            conversation_id TEXT, role TEXT, content TEXT,
            user_email TEXT, user_nickname TEXT, source TEXT,
            timestamp TEXT, metadata_json TEXT,
            workspace_id TEXT, organization_id TEXT
         );",
    )
    .expect("history table");
    let already: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM conversation_messages WHERE conversation_id IN ('conv-chat-1','conv-chat-2')",
            [],
            |row| row.get(0),
        )
        .unwrap_or(0);
    if already > 0 {
        return;
    }
    conn.execute(
        "INSERT INTO conversation_messages
         (conversation_id, role, content, user_email, timestamp, workspace_id)
         VALUES
         ('conv-chat-1', 'assistant', '현재 페이지 URL: https://example.com/page',
          'owner@fixture.local', '2026-08-14T10:00:00', 'personal'),
         ('conv-chat-2', 'assistant', '현재 페이지 URL: https://example.com/other',
          'owner@fixture.local', '2026-08-14T10:01:00', 'personal')",
        [],
    )
    .expect("history");
}

/// The capture sandbox's health report sampled 36 visible nodes / 42 edges
/// and graded embedding coverage at 63 ready + 1 pending. The Python seed
/// ran the full ingest pipeline; here we write the same *counts* so the
/// read-only Brain routes see the fixture's numbers.
pub(crate) fn seed_capture_graph(conn: &rusqlite::Connection) {
    let stamp = "2026-08-14T12:00:00";
    let model = "lattice-local-hash-v1:384";
    // First eight ids must sort first (ORDER BY updated_at DESC, id ASC).
    let visible: &[(&str, &str, &str)] = &[
        ("computer:4a0a339b0c6d:@hostname", "Computer", "host"),
        (
            "concept:4a0a339b0c6d:fixture-note.md",
            "Concept",
            "fixture-note.md",
        ),
        (
            "concept:4a0a339b0c6d:fixture-report.docx",
            "Concept",
            "fixture-report.docx",
        ),
        ("concept:4a0a339b0c6d:lattice", "Concept", "Lattice"),
        ("concept:4a0a339b0c6d:ranking", "Concept", "Ranking"),
        ("concept:4a0a339b0c6d:rust", "Concept", "Rust"),
        ("concept:4a0a339b0c6d:url", "Concept", "URL"),
        ("concept:4a0a339b0c6d:가중치", "Concept", "가중치"),
        ("concept:zz-extra-a", "Concept", "Extra A"),
        ("concept:zz-extra-b", "Concept", "Extra B"),
        ("concept:zz-extra-c", "Concept", "Extra C"),
        ("concept:zz-extra-d", "Concept", "Extra D"),
        (
            "webdoc:8d1cf3b813efa206bad0a854",
            "Document",
            "Hybrid retrieval decision",
        ),
        (
            "webdoc:b4d2f4f2e3557369d4b546cc",
            "Document",
            "Contradiction seed",
        ),
        (
            "webdoc:d2c86342c46b9109f94f5486",
            "Document",
            "Rust 이관 회의록",
        ),
        (
            "local-file:e12370d06b45a320a35ee274",
            "Document",
            "회의록.md",
        ),
        (
            "file:9f033e8b2619a947d73cfadd",
            "Document",
            "fixture-report.docx",
        ),
        ("doc:fill-00", "Document", "Doc 00"),
        ("doc:fill-01", "Document", "Doc 01"),
        ("doc:fill-02", "Document", "Doc 02"),
        ("doc:fill-03", "Document", "Doc 03"),
        ("doc:fill-04", "Document", "Doc 04"),
        (
            "conversation:4a0a339b0c6d:conv-chat-1",
            "Chat",
            "conv-chat-1",
        ),
        (
            "conversation:4a0a339b0c6d:conv-chat-2",
            "Chat",
            "conv-chat-2",
        ),
        ("conversation:4a0a339b0c6d:conv-fixture-1", "Chat", "chat-1"),
        ("conversation:4a0a339b0c6d:conv-fixture-2", "Chat", "chat-2"),
        (
            "source:0fd118756d7d34aca17cf762",
            "Source",
            "fixture-report.docx",
        ),
        (
            "source:727bc16c7e6f6d8457422188",
            "Source",
            "Contradiction seed",
        ),
        ("source:fill-00", "Source", "Source 00"),
        ("source:fill-01", "Source", "Source 01"),
        (
            "decision:1875f9b3776c4733d6c0d63f",
            "Decision",
            "Decision A",
        ),
        (
            "decision:1a3d37580afc4a3381ab7226",
            "Decision",
            "Decision B",
        ),
        (
            "decision:4f6dbf62b0a72ef7f6fb0a07",
            "Decision",
            "Decision C",
        ),
        (
            "person:4a0a339b0c6d:owner@fixture.local",
            "Person",
            "owner@fixture.local",
        ),
        ("folder:5602751f0aaa1eb8af29b566", "Folder", "corpus"),
        ("task:fill-00", "Task", "Task 00"),
    ];
    assert_eq!(visible.len(), 36);
    let mut hashes = Vec::new();
    for (id, kind, title) in visible {
        let text = lattice_core::clean_text(&format!("{title}\n\n"));
        let hash = format!("{:x}", Sha256::digest(text.as_bytes()));
        hashes.push((id.to_string(), hash));
        conn.execute(
            "INSERT INTO nodes(id, type, title, summary, metadata_json, created_at, updated_at)
             VALUES (?, ?, ?, '', '{}', ?, ?)",
            rusqlite::params![id, kind, title, stamp, stamp],
        )
        .expect("visible node");
        conn.execute(
            "INSERT INTO nodes_v2(id, type, label, legacy_type, workspace_id, created_at, updated_at)
             VALUES (?, ?, ?, ?, 'personal', ?, ?)",
            rusqlite::params![id, kind, title, kind, stamp, stamp],
        )
        .expect("visible v2");
    }
    for i in 0..28 {
        let title = format!("Preference {i:02}");
        let text = lattice_core::clean_text(&format!("{title}\n\n"));
        let hash = format!("{:x}", Sha256::digest(text.as_bytes()));
        hashes.push((format!("p{i:02}"), hash));
        conn.execute(
            "INSERT INTO nodes(id, type, title, summary, metadata_json, created_at, updated_at)
             VALUES (?, 'Preference', ?, '', '{}', ?, ?)",
            rusqlite::params![format!("p{i:02}"), title, stamp, stamp],
        )
        .expect("preference");
        conn.execute(
            "INSERT INTO nodes_v2(id, type, label, legacy_type, workspace_id, created_at, updated_at)
             VALUES (?, 'Preference', ?, 'Preference', 'personal', ?, ?)",
            rusqlite::params![format!("p{i:02}"), title, stamp, stamp],
        )
        .expect("preference v2");
    }
    let ids: Vec<&str> = visible.iter().map(|(id, _, _)| *id).collect();
    let person = "person:4a0a339b0c6d:owner@fixture.local";
    let doc_rust = "webdoc:d2c86342c46b9109f94f5486";
    let doc_contr = "webdoc:b4d2f4f2e3557369d4b546cc";
    let src_contr = "source:727bc16c7e6f6d8457422188";
    let folder = "folder:5602751f0aaa1eb8af29b566";
    let star = [
        doc_rust, doc_contr, src_contr, folder, ids[0], ids[1], ids[2], ids[3], ids[4], ids[5],
        ids[6],
    ];
    let mut pairs: Vec<(&str, &str)> = star.iter().map(|other| (person, *other)).collect();
    // Hub-hub edges to land the fixture degrees 11 / 5 / 4 / 4 / 4.
    pairs.extend_from_slice(&[
        (doc_rust, doc_contr),
        (doc_rust, src_contr),
        (doc_rust, folder),
        (doc_contr, src_contr),
        (doc_contr, folder),
        (src_contr, folder),
        (doc_rust, ids[7]),
    ]);
    let remaining: Vec<&str> = ids
        .iter()
        .copied()
        .filter(|id| *id != person && !star.contains(id))
        .collect();
    for window in remaining.windows(2) {
        pairs.push((window[0], window[1]));
    }
    if let Some(first) = remaining.first() {
        pairs.push((ids[0], first));
    }
    assert_eq!(pairs.len(), 42, "garden frequent degrees need 42 edges");
    for (edge_i, (from, to)) in pairs.iter().enumerate() {
        conn.execute(
            "INSERT INTO edges(id, from_node, to_node, type, weight, metadata_json, created_at)
             VALUES (?, ?, ?, 'RELATED_TO', 1.0, '{}', ?)",
            rusqlite::params![format!("e{edge_i:02}"), from, to, stamp],
        )
        .expect("edge");
        conn.execute(
            "INSERT INTO edges_v2(id, source, target, type, created_at)
             VALUES (?, ?, ?, 'RELATED_TO', ?)",
            rusqlite::params![format!("e{edge_i:02}"), from, to, stamp],
        )
        .expect("edge v2");
    }
    for (id, hash) in hashes.iter().take(63) {
        conn.execute(
            "INSERT INTO vector_embeddings(item_id, item_type, source_node, text_hash,
                embedding_dim, embedding_model, indexed_at)
             VALUES (?, ?, ?, ?, 384, ?, ?)",
            rusqlite::params![id, "node", id, hash, model, stamp],
        )
        .expect("embedding");
    }
}

pub(crate) fn fact_id(kind: &str, text: &str) -> String {
    let digest = Sha256::digest(format!("{kind}|{}", text.to_lowercase()).as_bytes());
    let hex: String = digest.iter().map(|b| format!("{b:02x}")).collect();
    format!("self:{kind}:{}", &hex[..12])
}

pub(crate) async fn spawn_fake_worker() -> String {
    async fn mutate(Json(body): Json<Value>) -> impl IntoResponse {
        let op = body.get("op").and_then(Value::as_str).unwrap_or("");
        let args = body.get("args").cloned().unwrap_or(json!({}));
        let result = match op {
            "rebuild_vector_index" => json!({
                "status": "completed",
                "operation_id": "vector-op:fixture",
                "full": false,
                "items_total": 64,
                "items_indexed": 0,
                "items_skipped": 64,
                "duration_ms": 1,
                "embedding_model": "lattice-local-hash-v1:384",
                "embedding_dim": 384
            }),
            "self_model_upsert" => {
                let kind = args
                    .get("kind")
                    .and_then(Value::as_str)
                    .unwrap_or("preference");
                let text = args.get("text").and_then(Value::as_str).unwrap_or("");
                let normalised = text
                    .trim()
                    .trim_end_matches(['.', '。', '!', '?', '！', '？', ',', '、']);
                json!({
                    "id": fact_id(kind, normalised),
                    "kind": kind,
                    "type": "Preference",
                    "text": normalised,
                    "origin": "user",
                    "confidence": 1.0,
                    "signal": "user_edit",
                    "workspace_id": args.get("workspace_id").cloned().unwrap_or(json!("personal"))
                })
            }
            "self_model_delete" => json!({"deleted": false}),
            "self_model_propose" => json!({
                "available": true,
                "proposed": [],
                "proposed_count": 0
            }),
            "self_model_apply" => {
                return (
                    StatusCode::NOT_FOUND,
                    Json(json!({"detail": "not-a-proposal"})),
                )
                    .into_response();
            }
            _ => json!({"ok": true}),
        };
        (StatusCode::OK, Json(json!({"op": op, "result": result}))).into_response()
    }

    async fn ingest(Json(_body): Json<Value>) -> impl IntoResponse {
        Json(json!({
            "status": "ok",
            "node_id": "garden-node",
            "provenance_id": "garden-prov",
            "duplicate": false
        }))
    }

    let app = Router::new()
        .route("/worker/graph/mutate", post(mutate))
        .route("/knowledge-graph/ingest", post(ingest));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("worker bind");
    let addr = listener.local_addr().expect("addr");
    tokio::spawn(async move {
        let _ = axum::serve(listener, app.into_make_service()).await;
    });
    format!("http://{addr}")
}
