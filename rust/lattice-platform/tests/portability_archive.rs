use std::collections::HashMap;
use std::io::Write;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use lattice_auth::{AuthConfig, AuthState, Clock};
use lattice_core::db::{RuntimeConfig, Store};
use lattice_core::graph_write::GraphWriter;
use lattice_platform::adminops::admin::{
    load_audit_log, load_org_governance_policy, save_org_governance_policy, validate_tenant_scope,
    OrganizationGovernancePolicy,
};
use lattice_platform::governance::review_queue::{self, GovernanceState};
use lattice_platform::knowledge::multimodal::{
    validate_multimodal_contract, Modality, MultimodalContractRequest,
};
use lattice_platform::knowledge::portability::{self, PortabilityState};
use lattice_platform::knowledge::voice::{self, VoiceState};
use rusqlite::params;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

static ENV_LOCK: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

struct BrainNetworkGuard {
    prev: Option<std::ffi::OsString>,
}

impl BrainNetworkGuard {
    fn set(val: &str) -> Self {
        let prev = std::env::var_os("LATTICEAI_BRAIN_NETWORK");
        std::env::set_var("LATTICEAI_BRAIN_NETWORK", val);
        Self { prev }
    }
}

impl Drop for BrainNetworkGuard {
    fn drop(&mut self) {
        match &self.prev {
            Some(v) => std::env::set_var("LATTICEAI_BRAIN_NETWORK", v),
            None => std::env::remove_var("LATTICEAI_BRAIN_NETWORK"),
        }
    }
}

struct TestNode {
    origin: String,
    data_dir: PathBuf,
    db_path: PathBuf,
    blobs_dir: PathBuf,
    _dir: tempfile::TempDir,
    _handle: tokio::task::JoinHandle<()>,
}

impl TestNode {
    async fn start() -> Self {
        let dir = tempfile::tempdir().expect("tempdir");
        let data_dir = dir.path().to_path_buf();
        let mut env = HashMap::new();
        env.insert(
            "LATTICEAI_DATA_DIR".into(),
            data_dir.to_string_lossy().into_owned(),
        );
        env.insert("LATTICEAI_HOST".into(), "127.0.0.1".into());
        let mut auth_cfg = AuthConfig::from_map(&env, None);
        auth_cfg.data_dir = data_dir.clone();
        let auth = AuthState::with_clock(auth_cfg, Clock::system());

        let db_path = data_dir.join("knowledge_graph.sqlite");
        let blobs_dir = data_dir.join("knowledge_graph_blobs");
        let store = Arc::new(Store::open(&db_path).expect("open store"));
        let graph = GraphWriter::open(Arc::clone(&store), &blobs_dir).expect("open graph writer");

        let runtime_cfg =
            RuntimeConfig::resolve(Some(&data_dir.to_string_lossy()), None, None, None);
        let mut pstate = PortabilityState::new(Arc::clone(&auth), runtime_cfg);
        pstate.graph = Some(graph.clone());

        let gstate = GovernanceState::open(Arc::clone(&auth), &data_dir, &data_dir, None);
        let vstate = VoiceState {
            auth: Some(Arc::clone(&auth)),
            graph: Some(graph),
            seam: None,
        };

        let app = portability::router(pstate)
            .merge(review_queue::router(gstate))
            .merge(voice::router_with(vstate));

        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind");
        let addr: SocketAddr = listener.local_addr().expect("addr");
        let handle = tokio::spawn(async move {
            let _ = axum::serve(listener, app.into_make_service()).await;
        });

        Self {
            origin: format!("http://{addr}"),
            data_dir,
            db_path,
            blobs_dir,
            _dir: dir,
            _handle: handle,
        }
    }

    fn client(&self) -> reqwest::Client {
        reqwest::Client::builder()
            .no_proxy()
            .timeout(Duration::from_secs(15))
            .build()
            .expect("http client")
    }

    async fn post_json(&self, path: &str, body: &Value) -> (u16, Value) {
        let resp = self
            .client()
            .post(format!("{}{path}", self.origin))
            .header("content-type", "application/json")
            .body(serde_json::to_vec(body).unwrap())
            .send()
            .await
            .expect("request");
        let status = resp.status().as_u16();
        let json: Value = resp.json().await.expect("json response");
        (status, json)
    }

    async fn get_json(&self, path: &str) -> (u16, Value) {
        let resp = self
            .client()
            .get(format!("{}{path}", self.origin))
            .send()
            .await
            .expect("request");
        let status = resp.status().as_u16();
        let json: Value = resp.json().await.expect("json response");
        (status, json)
    }

    fn seed_rich_graph(&self) {
        std::fs::create_dir_all(&self.blobs_dir).expect("blobs dir");
        std::fs::write(
            self.blobs_dir.join("artifact.bin"),
            b"lattice-blob-bytes-v1",
        )
        .expect("write blob");

        let conn = lattice_core::db::open_read_write(&self.db_path).expect("rw db");
        conn.execute(
            "INSERT INTO nodes (id, type, title, summary, metadata_json, raw_json, created_at, updated_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            params![
                "node-alpha",
                "Concept",
                "Encrypted Archive Design Alpha",
                "Confidential summary alpha",
                r#"{"workspace_id":"personal","tag":"core"}"#,
                r#"{"origin":"unit"}"#,
                "2026-11-15T00:00:00Z",
                "2026-11-15T00:00:00Z",
            ],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO nodes (id, type, title, summary, metadata_json, raw_json, created_at, updated_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            params![
                "node-beta",
                "Document",
                "Portability Spec Beta",
                "Vector & provenance specification",
                r#"{"workspace_id":"personal"}"#,
                r#"{}"#,
                "2026-11-15T00:01:00Z",
                "2026-11-15T00:01:00Z",
            ],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO edges (id, from_node, to_node, type, weight, metadata_json, created_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            params![
                "edge-1",
                "node-alpha",
                "node-beta",
                "REFERENCES",
                0.92,
                r#"{"confidence":0.95}"#,
                "2026-11-15T00:02:00Z",
            ],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO chunks (id, source_node, text, metadata_json, created_at)
             VALUES (?1, ?2, ?3, ?4, ?5)",
            params![
                "chunk-1",
                "node-beta",
                "Authenticated encryption preserves provenance and vectors.",
                r#"{"idx":0}"#,
                "2026-11-15T00:02:00Z",
            ],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO knowledge_sources (id, root_path, os_type, drive_id, label, status, include_ocr, watch_enabled, consent_json, created_at, updated_at, last_scanned_at)
             VALUES (?1, ?2, ?3, NULL, ?4, ?5, 1, 0, ?6, ?7, ?8, NULL)",
            params![
                "src-1",
                "/tmp/docs-alpha",
                "macos",
                "Engineering Docs",
                "indexed",
                r#"{"accepted":true}"#,
                "2026-11-15T00:00:00Z",
                "2026-11-15T00:02:00Z",
            ],
        )
        .unwrap();
        let vec_blob: Vec<u8> = [0.25f32, -0.5f32, 0.75f32, 0.125f32]
            .iter()
            .flat_map(|f| f.to_le_bytes())
            .collect();
        conn.execute(
            "INSERT INTO vector_embeddings (item_id, item_type, source_node, text_hash, embedding, embedding_dim, embedding_model, metadata_json, indexed_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            params![
                "chunk-1",
                "chunk",
                "node-beta",
                "hash-chunk-1",
                vec_blob,
                4,
                "mlx-community/test-embed",
                r#"{"provider":"mlx"}"#,
                "2026-11-15T00:03:00Z",
            ],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO ingestion_provenance (id, node_id, source_type, source_uri, content_hash, title, pipeline, owner, workspace_id, captured_at, modified_at, embedded, linked, duplicate, agent_used, chunk_count, permissions_json, metadata_json, created_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, 1, 1, 0, NULL, 1, ?12, ?13, ?14)",
            params![
                "prov-1",
                "node-beta",
                "file",
                "file:///tmp/docs-alpha/spec.md",
                "sha256-spec",
                "Portability Spec Beta",
                "native_ingest",
                "alice@example.com",
                "personal",
                "2026-11-15T00:01:00Z",
                "2026-11-15T00:01:00Z",
                r#"{"visibility":"private"}"#,
                r#"{"verified":true}"#,
                "2026-11-15T00:03:00Z",
            ],
        )
        .unwrap();
    }

    fn count_table(&self, table: &str) -> u64 {
        let conn = lattice_core::db::open_read_only(&self.db_path).expect("ro db");
        conn.query_row(&format!("SELECT COUNT(*) FROM {table}"), [], |r| {
            r.get::<_, i64>(0)
        })
        .unwrap() as u64
    }
}

#[tokio::test]
async fn archive_create_inspect_verify_restore_roundtrip_equivalence() {
    let node = TestNode::start().await;
    node.seed_rich_graph();

    let archive_path = node.data_dir.join("exports/full.latticebrain");
    let (status, created) = node
        .post_json(
            "/api/knowledge-graph/archive",
            &json!({
                "passphrase": "correct-horse-battery-staple",
                "path": archive_path.to_string_lossy(),
            }),
        )
        .await;
    assert_eq!(status, 200, "create failed: {created}");
    assert_eq!(created["status"], json!("created"));
    assert_eq!(created["encrypted"], json!(true));
    assert_eq!(created["counts"]["nodes"], json!(2));
    assert_eq!(created["counts"]["edges"], json!(1));
    assert_eq!(created["counts"]["chunks"], json!(1));
    assert_eq!(created["counts"]["vectors"], json!(1));
    assert_eq!(created["counts"]["provenance"], json!(1));
    assert_eq!(created["counts"]["knowledge_sources"], json!(1));
    assert_eq!(created["counts"]["blobs"], json!(1));
    assert!(archive_path.exists());

    let outer_file = std::fs::File::open(&archive_path).unwrap();
    let mut outer_zip = zip::ZipArchive::new(outer_file).unwrap();
    let mut enc_bytes = Vec::new();
    std::io::Read::read_to_end(
        &mut outer_zip.by_name("payload.enc").unwrap(),
        &mut enc_bytes,
    )
    .unwrap();
    assert!(
        !String::from_utf8_lossy(&enc_bytes).contains("Encrypted Archive Design Alpha"),
        "archive payload must not expose plaintext titles"
    );

    let (status, inspected) = node
        .post_json(
            "/api/knowledge-graph/archive/inspect",
            &json!({"path": archive_path.to_string_lossy(), "passphrase": null}),
        )
        .await;
    assert_eq!(status, 200, "inspect failed: {inspected}");
    assert_eq!(inspected["format"], json!("latticeai.kg.archive"));
    assert_eq!(inspected["format_version"], json!(1));
    assert_eq!(inspected["encrypted"], json!(true));
    assert_eq!(inspected["verified"], json!(false));
    assert_eq!(inspected["nodes"], json!(2));
    assert_eq!(inspected["vectors"], json!(1));
    assert_eq!(inspected["provenance"], json!(1));
    assert!(inspected["created_at"].as_str().is_some());

    let (status, verified) = node
        .post_json(
            "/api/knowledge-graph/archive/verify",
            &json!({
                "path": archive_path.to_string_lossy(),
                "passphrase": "correct-horse-battery-staple",
            }),
        )
        .await;
    assert_eq!(status, 200, "verify failed: {verified}");
    assert_eq!(verified["valid"], json!(true));
    assert_eq!(verified["verified"], json!(true));
    assert_eq!(verified["signature_verified"], json!(true));
    assert_eq!(verified["integrity_check"], json!("ok"));

    {
        let conn = lattice_core::db::open_read_write(&node.db_path).unwrap();
        for tbl in [
            "vector_embeddings",
            "chunks",
            "edges",
            "ingestion_provenance",
            "knowledge_sources",
            "nodes",
        ] {
            conn.execute(&format!("DELETE FROM {tbl}"), []).unwrap();
        }
        conn.execute(
            "INSERT INTO nodes (id, type, title, summary, metadata_json, raw_json, created_at, updated_at)
             VALUES ('decoy-node', 'Note', 'Temporary Before Restore', '', '{}', '{}', '2026-11-15T01:00:00Z', '2026-11-15T01:00:00Z')",
            [],
        )
        .unwrap();
        let _ = std::fs::remove_dir_all(&node.blobs_dir);
    }
    assert_eq!(node.count_table("nodes"), 1);
    assert_eq!(node.count_table("vector_embeddings"), 0);

    let (status, restored) = node
        .post_json(
            "/api/knowledge-graph/archive/restore",
            &json!({
                "path": archive_path.to_string_lossy(),
                "passphrase": "correct-horse-battery-staple",
                "dry_run": false,
            }),
        )
        .await;
    assert_eq!(status, 200, "restore failed: {restored}");
    assert_eq!(restored["restored"], json!(true));
    assert_eq!(restored["integrity_check"], json!("ok"));
    assert_eq!(restored["nodes"], json!(2));
    assert_eq!(restored["edges"], json!(1));
    assert_eq!(restored["chunks"], json!(1));
    assert_eq!(restored["vectors"], json!(1));
    assert_eq!(restored["provenance"], json!(1));
    assert_eq!(restored["knowledge_sources"], json!(1));
    assert_eq!(restored["blobs"], json!(1));

    let pre_backup = restored["pre_restore_backup"]
        .as_str()
        .expect("pre_restore_backup path");
    assert!(
        Path::new(pre_backup).exists(),
        "pre-restore backup must exist"
    );

    let conn = lattice_core::db::open_read_only(&node.db_path).unwrap();
    let title: String = conn
        .query_row("SELECT title FROM nodes WHERE id='node-alpha'", [], |r| {
            r.get(0)
        })
        .unwrap();
    assert_eq!(title, "Encrypted Archive Design Alpha");
    let vec_model: String = conn
        .query_row(
            "SELECT embedding_model FROM vector_embeddings WHERE item_id='chunk-1'",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert_eq!(vec_model, "mlx-community/test-embed");
    let prov_hash: String = conn
        .query_row(
            "SELECT content_hash FROM ingestion_provenance WHERE id='prov-1'",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert_eq!(prov_hash, "sha256-spec");
    assert_eq!(
        std::fs::read(node.blobs_dir.join("artifact.bin")).unwrap(),
        b"lattice-blob-bytes-v1"
    );

    let (status, merged) = node
        .post_json(
            "/api/knowledge-graph/archive/import",
            &json!({
                "path": archive_path.to_string_lossy(),
                "passphrase": "correct-horse-battery-staple",
                "mode": "merge",
                "dry_run": false,
            }),
        )
        .await;
    assert_eq!(status, 200, "merge import failed: {merged}");
    assert_eq!(merged["imported"], json!(true));
    assert_eq!(merged["nodes"], json!(2));
}

#[tokio::test]
async fn archive_rejects_wrong_password_tampering_and_unsupported_version() {
    let node = TestNode::start().await;
    node.seed_rich_graph();

    let archive_path = node.data_dir.join("exports/sec.latticebrain");
    let (status, _) = node
        .post_json(
            "/api/knowledge-graph/archive",
            &json!({"passphrase": "right-pass", "path": archive_path.to_string_lossy()}),
        )
        .await;
    assert_eq!(status, 200);

    let (st_v, body_v) = node
        .post_json(
            "/api/knowledge-graph/archive/verify",
            &json!({"path": archive_path.to_string_lossy(), "passphrase": "wrong-password"}),
        )
        .await;
    assert_eq!(st_v, 400, "wrong password verify must fail: {body_v}");
    let (st_r, body_r) = node
        .post_json(
            "/api/knowledge-graph/archive/restore",
            &json!({"path": archive_path.to_string_lossy(), "passphrase": "wrong-password"}),
        )
        .await;
    assert_eq!(st_r, 400, "wrong password restore must fail: {body_r}");

    let tampered_path = node.data_dir.join("exports/tampered.latticebrain");
    rewrite_outer_zip_entry(&archive_path, &tampered_path, "payload.enc", |bytes| {
        if let Some(first) = bytes.first_mut() {
            *first ^= 0x01;
        }
    });
    let (st_t, body_t) = node
        .post_json(
            "/api/knowledge-graph/archive/verify",
            &json!({"path": tampered_path.to_string_lossy(), "passphrase": "right-pass"}),
        )
        .await;
    assert_eq!(st_t, 400, "tampered bytes must fail verify: {body_t}");

    let future_path = node.data_dir.join("exports/v99.latticebrain");
    rewrite_outer_zip_entry(&archive_path, &future_path, "header.json", |bytes| {
        let mut h: Value = serde_json::from_slice(bytes).unwrap();
        h["format_version"] = json!(99);
        *bytes = serde_json::to_vec(&h).unwrap();
    });
    let (st_u1, body_u1) = node
        .post_json(
            "/api/knowledge-graph/archive/inspect",
            &json!({"path": future_path.to_string_lossy()}),
        )
        .await;
    assert_eq!(st_u1, 400, "v99 inspect must reject: {body_u1}");
    let (st_u2, body_u2) = node
        .post_json(
            "/api/knowledge-graph/archive/verify",
            &json!({"path": future_path.to_string_lossy(), "passphrase": "right-pass"}),
        )
        .await;
    assert_eq!(st_u2, 400, "v99 verify must reject: {body_u2}");
}

#[tokio::test]
async fn archive_rejects_path_traversal_and_symlink_escapes() {
    let node = TestNode::start().await;
    node.seed_rich_graph();

    let evil_outer = node.data_dir.join("exports/evil-outer.latticebrain");
    std::fs::create_dir_all(evil_outer.parent().unwrap()).unwrap();
    {
        let file = std::fs::File::create(&evil_outer).unwrap();
        let mut zip = zip::ZipWriter::new(file);
        let opts = zip::write::SimpleFileOptions::default();
        zip.start_file("../../escape.txt", opts).unwrap();
        zip.write_all(b"evil").unwrap();
        zip.finish().unwrap();
    }
    let (st1, b1) = node
        .post_json(
            "/api/knowledge-graph/archive/inspect",
            &json!({"path": evil_outer.to_string_lossy()}),
        )
        .await;
    assert_eq!(st1, 400, "outer traversal must be rejected: {b1}");

    let symlink_outer = node.data_dir.join("exports/symlink-outer.latticebrain");
    {
        let file = std::fs::File::create(&symlink_outer).unwrap();
        let mut zip = zip::ZipWriter::new(file);
        let sym_opts = zip::write::SimpleFileOptions::default().unix_permissions(0o120_777);
        zip.start_file("header.json", sym_opts).unwrap();
        zip.write_all(b"/etc/passwd").unwrap();
        zip.finish().unwrap();
    }
    let (st2, b2) = node
        .post_json(
            "/api/knowledge-graph/archive/verify",
            &json!({"path": symlink_outer.to_string_lossy(), "passphrase": "pw"}),
        )
        .await;
    assert_eq!(st2, 400, "symlink entry must be rejected: {b2}");
}

#[tokio::test]
async fn share_export_recipient_key_import_and_accept_roundtrip() {
    let _env_lock = ENV_LOCK.lock().await;
    let _net_guard = BrainNetworkGuard::set("1");

    let sender = TestNode::start().await;
    let receiver = TestNode::start().await;

    {
        let conn = lattice_core::db::open_read_write(&sender.db_path).unwrap();
        conn.execute(
            "INSERT INTO nodes (id, type, title, summary, metadata_json, raw_json, created_at, updated_at)
             VALUES ('share-n1', 'Concept', 'Shared Graph Idea', 'Safe to share', '{\"workspace_id\":\"personal\"}', '{}', '2026-11-15T00:00:00Z', '2026-11-15T00:00:00Z')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO nodes (id, type, title, summary, metadata_json, raw_json, created_at, updated_at)
             VALUES ('share-n2', 'Concept', 'Neighbor Node', 'Connected', '{\"workspace_id\":\"personal\"}', '{}', '2026-11-15T00:00:00Z', '2026-11-15T00:00:00Z')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO nodes (id, type, title, summary, metadata_json, raw_json, created_at, updated_at)
             VALUES ('blocked-secret', 'Secret', 'API Secret Key', 'Never share', '{\"workspace_id\":\"personal\"}', '{}', '2026-11-15T00:00:00Z', '2026-11-15T00:00:00Z')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO nodes (id, type, title, summary, metadata_json, raw_json, created_at, updated_at)
             VALUES ('blocked-local', 'Concept', 'Local Only Note', 'Private', '{\"local_only\":true,\"workspace_id\":\"personal\"}', '{}', '2026-11-15T00:00:00Z', '2026-11-15T00:00:00Z')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO edges (id, from_node, to_node, type, weight, metadata_json, created_at)
             VALUES ('share-e1', 'share-n1', 'share-n2', 'RELATED_TO', 1.0, '{}', '2026-11-15T00:00:00Z')",
            [],
        )
        .unwrap();
    }

    let (rk_st, rk_body) = receiver
        .get_json("/api/knowledge-graph/share/recipient-key")
        .await;
    assert_eq!(rk_st, 200, "recipient-key failed: {rk_body}");
    assert_eq!(rk_body["available"], json!(true));
    let recv_pubkey = rk_body["public_key"]
        .as_str()
        .expect("recipient public_key");

    let (se_st, se_body) = sender
        .post_json(
            "/api/knowledge-graph/share/export",
            &json!({
                "node_ids": ["share-n1", "blocked-secret", "blocked-local"],
                "include_neighbors": true,
            }),
        )
        .await;
    assert_eq!(se_st, 200, "share_export failed: {se_body}");
    assert_eq!(se_body["counts"]["nodes"], json!(2));
    assert_eq!(se_body["counts"]["edges"], json!(1));
    assert_eq!(se_body["excluded_local_only"], json!(2));
    assert_eq!(se_body["signature_verified"], json!(true));

    let sealed_path = sender.data_dir.join("exports/for-receiver.latticebrain");
    let (sa_st, sa_body) = sender
        .post_json(
            "/api/knowledge-graph/share/archive",
            &json!({
                "node_ids": ["share-n1"],
                "include_neighbors": true,
                "recipient_public_key": recv_pubkey,
                "path": sealed_path.to_string_lossy(),
            }),
        )
        .await;
    assert_eq!(sa_st, 200, "share_archive failed: {sa_body}");
    assert_eq!(sa_body["encryption_mode"], json!("recipient_public_key"));

    let (si_st, si_body) = receiver
        .post_json(
            "/api/knowledge-graph/share/import",
            &json!({
                "path": sealed_path.to_string_lossy(),
                "workspace_id": "acme-org",
            }),
        )
        .await;
    assert_eq!(si_st, 200, "share_import failed: {si_body}");
    assert_eq!(si_body["staged"], json!(true));
    assert_eq!(si_body["status"], json!("pending_review"));
    assert_eq!(si_body["signature_verified"], json!(true));
    let proposal_id = si_body["proposal_id"].as_str().expect("proposal_id");
    assert_eq!(
        receiver.count_table("nodes"),
        0,
        "share_import must never auto-merge before governance review"
    );

    let (bad_ws_st, _) = receiver
        .post_json(
            &format!("/api/knowledge-graph/share/proposals/{proposal_id}/accept"),
            &json!({"workspace_id": "other-tenant"}),
        )
        .await;
    assert_eq!(bad_ws_st, 404, "wrong tenant must not accept proposal");
    assert_eq!(receiver.count_table("nodes"), 0);

    let (acc_st, acc_body) = receiver
        .post_json(
            &format!("/api/knowledge-graph/share/proposals/{proposal_id}/accept"),
            &json!({"workspace_id": "acme-org"}),
        )
        .await;
    assert_eq!(acc_st, 200, "share_accept failed: {acc_body}");
    assert_eq!(acc_body["accepted"], json!(true));
    assert_eq!(acc_body["status"], json!("approved"));
    assert_eq!(receiver.count_table("nodes"), 2);
    assert_eq!(receiver.count_table("edges"), 1);

    let mut bad_bundle = se_body["bundle"].clone();
    bad_bundle["nodes"][0]["title"] = json!("Tampered Title");
    let (tamper_st, tamper_body) = receiver
        .post_json(
            "/api/knowledge-graph/share/import",
            &json!({"artifact": bad_bundle}),
        )
        .await;
    assert_eq!(
        tamper_st, 400,
        "tampered subgraph signature must fail: {tamper_body}"
    );
}

#[tokio::test]
async fn postgres_migration_and_docker_honestly_report_planned() {
    let node = TestNode::start().await;
    node.seed_rich_graph();

    let (d1_st, d1) = node
        .post_json(
            "/api/brain/storage/postgres/docker",
            &json!({"consent": false, "dry_run": true}),
        )
        .await;
    assert_eq!(d1_st, 200);
    assert_eq!(d1["status"], json!("consent_required"));
    assert_eq!(d1["started"], json!(false));

    let (d2_st, d2) = node
        .post_json(
            "/api/brain/storage/postgres/docker",
            &json!({"consent": true, "dry_run": false}),
        )
        .await;
    assert_eq!(d2_st, 200);
    assert_eq!(d2["status"], json!("planned"));
    assert_eq!(d2["started"], json!(false));
    assert_eq!(d2["executed"], json!(false));
    assert_eq!(d2["live_orchestration_supported"], json!(false));

    let (m_st, m) = node
        .post_json(
            "/api/brain/storage/migrate-postgres",
            &json!({
                "dsn": "postgresql://localhost/lattice_brain",
                "dry_run": false,
            }),
        )
        .await;
    assert_eq!(m_st, 200);
    assert_eq!(m["status"], json!("planned"));
    assert_eq!(m["executed"], json!(false));
    assert_eq!(m["migrated"], json!(false));
    assert_eq!(m["dry_run"], json!(true));
    assert_eq!(m["live_migration_supported"], json!(false));
    assert!(m["total_rows"].as_i64().unwrap_or(0) >= 6);
}

#[tokio::test]
async fn p3_enterprise_tenant_isolation_and_multimodal_contracts() {
    let node = TestNode::start().await;

    let mut policy = OrganizationGovernancePolicy::default_for("org-alpha");
    policy.enforce_tenant_isolation = true;
    policy.allow_multimodal_capture = false;
    save_org_governance_policy(&node.data_dir, "org-alpha", &policy, Some("admin@acme.com"))
        .expect("save policy");
    let loaded = load_org_governance_policy(&node.data_dir, "org-alpha");
    assert!(!loaded.allow_multimodal_capture);

    let err = validate_tenant_scope(
        &node.data_dir,
        Some("org-alpha"),
        Some("org-beta"),
        Some("admin@acme.com"),
        "read_workspace_graph",
    )
    .expect_err("cross-tenant access must be denied");
    assert!(err.contains("Cross-tenant access denied"));

    let denied = validate_multimodal_contract(
        &node.data_dir,
        &MultimodalContractRequest {
            modality: Modality::Speech,
            mime_type: "audio/wav".into(),
            payload_bytes: 2048,
            user_email: Some("admin@acme.com".into()),
            workspace_id: Some("org-alpha".into()),
            cloud_fallback: false,
            expected_embedding_dim: None,
        },
        false,
        true,
    );
    assert_eq!(denied.unwrap_err().code, "org_policy_denied");

    let (v_st, v_body) = node
        .post_json(
            "/api/capture/voice",
            &json!({
                "title": "bad audio",
                "audio_b64": "AAAA",
                "mime_type": "application/octet-stream",
            }),
        )
        .await;
    assert_eq!(v_st, 400, "invalid audio MIME must be rejected: {v_body}");
    assert_eq!(v_body["code"], json!("unsupported_mime_type"));

    let audit = load_audit_log(&node.data_dir.join("audit_log.json"));
    assert!(
        audit
            .iter()
            .any(|e| e.get("event_type").and_then(Value::as_str) == Some("tenant_isolation_denied")),
        "audit log should record tenant_isolation_denied: {audit:?}"
    );
    assert!(
        audit
            .iter()
            .any(|e| e.get("event_type").and_then(Value::as_str) == Some("org_policy_updated")),
        "audit log should record org_policy_updated"
    );
}

fn rewrite_outer_zip_entry(
    src: &Path,
    dst: &Path,
    target_entry: &str,
    mut mutate: impl FnMut(&mut Vec<u8>),
) {
    let f = std::fs::File::open(src).unwrap();
    let mut zr = zip::ZipArchive::new(f).unwrap();
    let mut entries: Vec<(String, Vec<u8>)> = Vec::new();
    for i in 0..zr.len() {
        let mut entry = zr.by_index(i).unwrap();
        let name = entry.name().expect("entry name").into_owned();
        let mut buf = Vec::new();
        std::io::Read::read_to_end(&mut entry, &mut buf).unwrap();
        if name == target_entry {
            mutate(&mut buf);
        }
        entries.push((name, buf));
    }
    let out = std::fs::File::create(dst).unwrap();
    let mut zw = zip::ZipWriter::new(out);
    let opts =
        zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Stored);
    for (name, buf) in entries {
        zw.start_file(name, opts).unwrap();
        zw.write_all(&buf).unwrap();
    }
    zw.finish().unwrap();
    let _ = Sha256::new();
}
