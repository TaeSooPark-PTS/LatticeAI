//! End-to-End Product Verification Suite for Review Section 16 (16.1 – 16.4).
//!
//! Verifies across the real Rust workspace crates (`lattice-core`, `lattice-retrieval`,
//! `lattice-ingest`, `lattice-chat`, `lattice-agent`, `lattice-host`):
//! - 16.1 Separated RAG Accuracy (KO/EN Recall@5, MRR, abstention, superseded exclusion, model-swap)
//! - 16.2 Bi-temporal Knowledge Evolution (Jan -> Mar -> May point-in-time + current + Entity Resolution)
//! - 16.3 Cloud Boundary Enforcement (`local_only` zero egress, sensitive exclusion, review staging)
//! - 16.4 Fault Recovery (worker down degradation, mid-indexing restart recovery, approval restart resume)

mod common;

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use axum::routing::post;
use axum::Router;
use common::{client, FakeWorker, FixedProvider, TestGateway};
use lattice_agent::{
    DurableWorkflowStore, StepExecOutput, WorkflowAdvanceOutcome, WorkflowSpec, WorkflowStatus,
    WorkflowStepSpec,
};
use lattice_chat::boundary::{HybridPolicy, NetworkMode};
use lattice_chat::cloud::{
    build_minimal_context_with_policy, ingest_expansion, plan_kg_expansion_rich, CloudTurnResult,
};
use lattice_core::db::{RuntimeConfig, Store};
use lattice_core::embeddings::LocalEmbeddingModel;
use lattice_core::entity_resolution::{apply_entity_consolidation, EntityAliasBook};
use lattice_core::graph_write::types::{EdgeSpec, IngestContentRequest, NodeSpec};
use lattice_core::graph_write::GraphWriter;
use lattice_core::temporal::{query_temporal_timeline, stamp_temporal_fact, TemporalConflictKind};
use lattice_core::worker::{WorkerRetryPolicy, WorkerSeamClient};
use lattice_host::gateway::onedoor::OneDoorState;
use lattice_host::gateway::posture::Posture;
use lattice_host::gateway::GatewayState;
use lattice_ingest::{
    sync_document_incremental, DebouncedWatchConfig, DebouncedWatchEngine, IncrementalSyncRequest,
    RawFsChange, WatchConfig,
};
use lattice_retrieval::rag_eval_grounded::run_separated_rag_evaluation;
use serde_json::{json, Map};

#[test]
fn section_16_1_rag_accuracy_abstention_superseded_and_model_swap() {
    let report = run_separated_rag_evaluation("personal").expect("separated RAG evaluation");

    // Mixed Korean/English Recall@5 & MRR on real LocalEmbeddingModel (`lattice-local-hash-v1`).
    assert!(
        report
            .embedding_model_id
            .starts_with("lattice-local-hash-v1"),
        "embedding_model_id={}",
        report.embedding_model_id
    );
    assert!(
        report.retrieval.recall_at_5 >= 0.99,
        "Recall@5={}",
        report.retrieval.recall_at_5
    );
    assert!(report.retrieval.mrr >= 0.90, "MRR={}", report.retrieval.mrr);
    assert_eq!(
        report.retrieval.superseded_exclusion_rate, 1.0,
        "superseded knowledge must never leak into latest retrieval"
    );
    assert!(
        report.retrieval_invariance_jaccard_at_5 >= 0.99,
        "Gemma / Qwen / Cloud model swap must preserve identical grounded context pack"
    );
    assert_eq!(report.cross_model_abstention_agreement, 1.0);

    for gen in &report.generation_by_model {
        assert_eq!(gen.abstention_accuracy, 1.0, "model={}", gen.model_id);
        assert_eq!(
            gen.hallucination_rejection_rate, 1.0,
            "model={}",
            gen.model_id
        );
        assert_eq!(gen.superseded_citation_leaks, 0, "model={}", gen.model_id);
    }
}

#[test]
fn section_16_2_bitemporal_jan_mar_may_evolution_and_entity_resolution() {
    let dir = tempfile::tempdir().expect("tempdir");
    let store = Arc::new(Store::open(&dir.path().join("knowledge_graph.sqlite")).expect("store"));
    let writer = GraphWriter::open(Arc::clone(&store), dir.path().join("blobs")).expect("writer");

    // Seed Jan decision (Korean entity alias), Mar policy change (English canonical name),
    // and May scoped exception on a real production GraphWriter schema!
    writer
        .upsert_nodes(&[
            NodeSpec {
                id: "dec:jan_2026".into(),
                node_type: "Decision".into(),
                title: "프로젝트 알파 배포 승인 정책 (1월)".into(),
                summary: "모든 프로덕션 배포는 수동 승인을 거친다.".into(),
                workspace_id: Some("personal".into()),
                ..Default::default()
            },
            NodeSpec {
                id: "dec:mar_2026".into(),
                node_type: "Decision".into(),
                title: "Project Alpha 배포 승인 정책 개정 (3월)".into(),
                summary: "평일 프로덕션 배포는 자동 파이프라인으로 전환한다.".into(),
                workspace_id: Some("personal".into()),
                ..Default::default()
            },
            NodeSpec {
                id: "dec:may_2026".into(),
                node_type: "Decision".into(),
                title: "Project Alpha 금요일 배포 예외 조항 (5월)".into(),
                summary: "금요일 오후 배포에 한해 수동 승인을 유지한다.".into(),
                workspace_id: Some("personal".into()),
                ..Default::default()
            },
            NodeSpec {
                id: "ent:ko_alpha".into(),
                node_type: "Project".into(),
                title: "프로젝트 알파".into(),
                summary: "핵심 제품 이니셔티브".into(),
                workspace_id: Some("personal".into()),
                ..Default::default()
            },
            NodeSpec {
                id: "ent:en_alpha".into(),
                node_type: "Project".into(),
                title: "Project Alpha".into(),
                summary: "Core product initiative".into(),
                workspace_id: Some("personal".into()),
                ..Default::default()
            },
        ])
        .expect("upsert nodes");

    writer
        .upsert_edges(&[EdgeSpec {
            from_node: "dec:may_2026".into(),
            to_node: "dec:mar_2026".into(),
            edge_type: "REFINES".into(),
            weight: 1.0,
            metadata: Map::new(),
            legacy_label: None,
        }])
        .expect("upsert REFINES edge");

    let mut alias_book = EntityAliasBook::new();
    alias_book.register_aliases("Project Alpha", ["프로젝트 알파"]);

    store
        .with_write_txn(|txn| {
            stamp_temporal_fact(
                txn,
                "dec:jan_2026",
                "Project Alpha deploy policy",
                "2026-01-10T00:00:00Z",
                Some("2026-03-15T00:00:00Z"),
                "2026-01-10T00:00:00Z",
                None,
            )?;
            stamp_temporal_fact(
                txn,
                "dec:mar_2026",
                "Project Alpha deploy policy",
                "2026-03-15T00:00:00Z",
                None,
                "2026-03-15T00:00:00Z",
                Some("dec:jan_2026"),
            )?;
            stamp_temporal_fact(
                txn,
                "dec:may_2026",
                "Project Alpha deploy policy",
                "2026-05-20T00:00:00Z",
                None,
                "2026-05-20T00:00:00Z",
                None,
            )?;
            let er = apply_entity_consolidation(
                txn,
                Some("personal"),
                &alias_book,
                "2026-05-20T00:00:00Z",
            )?;
            assert_eq!(er.clusters_with_merges, 1);
            assert_eq!(er.same_as_edges_upserted, 1);
            Ok(())
        })
        .expect("stamp temporal + ER");

    store
        .with_read_conn(|conn| {
            // 1. Point-in-time Feb 2026 -> ONLY Jan decision active!
            let feb = query_temporal_timeline(
                conn,
                Some("personal"),
                "deploy policy",
                Some("2026-02-01T00:00:00Z"),
            )?;
            assert_eq!(feb.active_facts.len(), 1);
            assert_eq!(feb.active_facts[0].node_id, "dec:jan_2026");

            // 2. Point-in-time Apr 2026 -> Mar active, Jan superseded, May not yet reached!
            let apr = query_temporal_timeline(
                conn,
                Some("personal"),
                "deploy policy",
                Some("2026-04-01T00:00:00Z"),
            )?;
            assert_eq!(apr.active_facts.len(), 1);
            assert_eq!(apr.active_facts[0].node_id, "dec:mar_2026");
            assert_eq!(apr.historical_facts.len(), 1);
            assert_eq!(apr.unreached_facts.len(), 1);

            // 3. Current state -> Mar + May active together; TemporalEvolution + ComplementaryRefinement!
            let current = query_temporal_timeline(conn, Some("personal"), "deploy policy", None)?;
            let active_ids: Vec<&str> = current
                .active_facts
                .iter()
                .map(|f| f.node_id.as_str())
                .collect();
            assert!(active_ids.contains(&"dec:mar_2026"));
            assert!(active_ids.contains(&"dec:may_2026"));
            assert!(!active_ids.contains(&"dec:jan_2026"));
            assert!(current
                .classifications
                .iter()
                .any(|c| c.kind == TemporalConflictKind::TemporalEvolution));
            assert!(current
                .classifications
                .iter()
                .any(|c| c.kind == TemporalConflictKind::ComplementaryRefinement));
            assert!(!current
                .classifications
                .iter()
                .any(|c| c.kind == TemporalConflictKind::ActiveContradiction));
            Ok(())
        })
        .expect("temporal assertions");
}

#[tokio::test]
async fn section_16_3_cloud_boundary_local_only_zero_egress_sensitive_exclusion_and_staging() {
    let cloud_hits = Arc::new(AtomicUsize::new(0));
    let hits_clone = Arc::clone(&cloud_hits);
    let mock_cloud = Router::new().route(
        "/chat/completions",
        post(move || {
            let hits_clone = Arc::clone(&hits_clone);
            async move {
                hits_clone.fetch_add(1, Ordering::SeqCst);
                let body = format!(
                    "data: {}

data: [DONE]

",
                    json!({"choices":[{"delta":{"content":"Rust 아키텍처는 로컬 우선으로 설계되었습니다."}}]})
                );
                axum::response::Response::builder()
                    .status(200)
                    .header("content-type", "text/event-stream")
                    .body(axum::body::Body::from(body))
                    .unwrap()
            }
        }),
    );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind mock cloud");
    let cloud_origin = format!("http://{}", listener.local_addr().expect("addr"));
    tokio::spawn(async move {
        let _ = axum::serve(listener, mock_cloud).await;
    });

    let scratch = tempfile::tempdir().expect("tempdir");
    let data_dir = scratch.path().join("data");
    let agent_root = scratch.path().join("agent_workspace");
    std::fs::create_dir_all(&data_dir).expect("data_dir");
    std::fs::create_dir_all(&agent_root).expect("agent_root");

    // Seed public, `local_only`, and `.env` sensitive nodes into the real Brain SQLite DB!
    let store = Arc::new(Store::open(&data_dir.join("knowledge_graph.sqlite")).expect("store"));
    let writer = GraphWriter::open(Arc::clone(&store), data_dir.join("blobs")).expect("writer");

    let public_doc = writer
        .ingest_content(&IngestContentRequest {
            source_type: "note".into(),
            title: "Rust 아키텍처 개요".into(),
            text: "Rust 아키텍처는 로컬 SQLite와 BM25 하이브리드 검색을 제공합니다.".into(),
            source_uri: Some("/vault/rust_arch.md".into()),
            node_type: Some("Document".into()),
            ..Default::default()
        })
        .expect("public doc");
    let _ = writer.write_vectors(&public_doc.node_id);

    let mut private_meta = Map::new();
    private_meta.insert("local_only".into(), json!(true));
    let private_doc = writer
        .ingest_content(&IngestContentRequest {
            source_type: "note".into(),
            title: "Rust 내부 마스터 보안코드".into(),
            text: "Rust 마스터 보안코드는 TOP-SECRET-LOCAL-ONLY 입니다.".into(),
            source_uri: Some("/vault/private_keys.md".into()),
            metadata: private_meta,
            node_type: Some("Document".into()),
            ..Default::default()
        })
        .expect("private doc");
    let _ = writer.write_vectors(&private_doc.node_id);

    let dotenv_doc = writer
        .ingest_content(&IngestContentRequest {
            source_type: "note".into(),
            title: "Rust 환경변수 설정".into(),
            text: "Rust 운영 비밀 토큰 sk-abcdef1234567890abcdef1234567890".into(),
            source_uri: Some("/vault/.env.production".into()),
            node_type: Some("Document".into()),
            ..Default::default()
        })
        .expect("dotenv doc");
    let _ = writer.write_vectors(&dotenv_doc.node_id);

    // Configure cloud provider, starting STRICTLY in `local_only` mode!
    std::fs::write(
        data_dir.join("cloud_provider.json"),
        json!({
            "mode": "api_key",
            "provider": "openai_compatible",
            "model": "mock-model",
            "base_url": cloud_origin,
            "api_key": "test-key",
        })
        .to_string(),
    )
    .expect("cloud provider");
    std::fs::write(
        data_dir.join("network_boundary.json"),
        json!({"default": "local_only"}).to_string(),
    )
    .expect("local_only boundary");

    let worker = FakeWorker::start().await;
    let config = RuntimeConfig::resolve(
        Some(&data_dir.to_string_lossy()),
        None,
        Some(&worker.origin()),
        None,
    );
    let loop_config = lattice_agent::LoopConfig {
        worker_origin: worker.origin(),
        runs_dir: scratch.path().join("rust_agent_runs"),
        client: Some(client()),
        proposals: None,
        hooks: None,
        external: None,
    };
    let product = OneDoorState::open_with_config(
        config,
        &worker.origin(),
        client(),
        &agent_root,
        loop_config,
    )
    .expect("product");
    let gw_state = GatewayState::new(Arc::new(FixedProvider::new(worker.origin(), worker.port())))
        .expect("gw")
        .with_db_path(data_dir.join("knowledge_graph.sqlite"))
        .with_agent_root(&agent_root)
        .with_pinned_posture(Posture::Open)
        .with_product(Arc::new(product));
    let gateway = TestGateway::start_with_state(gw_state).await;

    // 1. `local_only` mode -> ZERO outbound calls to cloud endpoint!
    let _resp = client()
        .post(gateway.url("/chat"))
        .header("content-type", "application/json")
        .body(json!({"message": "Rust 아키텍처 알려줘", "network_mode": "local_only"}).to_string())
        .send()
        .await
        .expect("local_only chat");
    assert_eq!(
        cloud_hits.load(Ordering::SeqCst),
        0,
        "local_only mode must NEVER make external network calls"
    );

    // 2. Under `cloud_allowed`, verify sensitive (`local_only` + `.env.production`) nodes are stripped!
    let embedder = LocalEmbeddingModel::new(384);
    store
        .with_read_conn(|conn| {
            let ctx = build_minimal_context_with_policy(
                conn,
                &embedder,
                "Rust 아키텍처 보안코드 환경변수",
                NetworkMode::CloudAllowed,
                12,
                None,
                1_700_000_000.0,
                &HybridPolicy::default(),
            );
            assert!(
                ctx.compact_text.contains("Rust 아키텍처 개요"),
                "public node should be eligible: {}",
                ctx.compact_text
            );
            assert!(
                !ctx.compact_text.contains("TOP-SECRET-LOCAL-ONLY"),
                "local_only node leaked into cloud context: {}",
                ctx.compact_text
            );
            assert!(
                !ctx.compact_text.contains("sk-abcdef"),
                ".env sensitive node leaked into cloud context: {}",
                ctx.compact_text
            );
            assert!(!ctx.node_ids.contains(&private_doc.node_id));
            assert!(!ctx.node_ids.contains(&dotenv_doc.node_id));
            Ok(())
        })
        .expect("context sensitivity check");

    // 3. Cloud expansion NEVER auto-commits to KG (`auto_commit == false`, staged for review)!
    let plan = plan_kg_expansion_rich(&CloudTurnResult {
        user_message: "Rust 아키텍처 알려줘".into(),
        answer_text: "Rust 아키텍처는 로컬 SQLite 하이브리드 검색과 보안 경계를 결합합니다.".into(),
        sent_node_ids: vec![public_doc.node_id],
        provider: "openai_compatible".into(),
        model: "mock-model".into(),
    });
    assert!(!plan.auto_commit, "cloud expansion must never auto_commit");
    let mut malicious_committing = plan.clone();
    malicious_committing.auto_commit = true;
    let refused = ingest_expansion(&malicious_committing, None, None, None);
    assert_eq!(refused["written_nodes"], 0);
    assert!(refused["write_error"]
        .as_str()
        .unwrap_or("")
        .contains("GRAPH_MUTATION_OPS"));

    gateway.stop().await;
    worker.shutdown();
}

#[tokio::test]
async fn section_16_4_fault_recovery_worker_down_interrupted_indexing_and_approval_restart() {
    // --- 16.4A: Python worker offline/unreachable -> Gateway & Seam degrade fast without hanging! ---
    let dead_listener = std::net::TcpListener::bind("127.0.0.1:0").expect("ephemeral port");
    let dead_port = dead_listener.local_addr().expect("addr").port();
    drop(dead_listener); // Port is guaranteed closed!
    let dead_origin = format!("http://127.0.0.1:{dead_port}");

    let seam = WorkerSeamClient::new(&dead_origin)
        .expect("seam client")
        .with_timeout(Duration::from_millis(400));
    let started = Instant::now();
    let err = seam
        .post_idempotent_json(
            "/worker/embed",
            "idem-worker-down-1",
            &json!({"texts": ["hello"]}),
            WorkerRetryPolicy {
                max_retries: 1,
                initial_backoff: Duration::from_millis(25),
                max_backoff: Duration::from_millis(50),
            },
        )
        .await
        .expect_err("dead worker must fail fast");
    assert!(err.is_transient());
    assert!(
        started.elapsed() < Duration::from_secs(3),
        "gateway worker seam must never hang when Python worker is down"
    );

    // Local gateway native SQLite search still answers 200 while worker is down!
    let scratch = tempfile::tempdir().expect("tempdir");
    let data_dir = scratch.path().join("data");
    let agent_root = scratch.path().join("agent_workspace");
    std::fs::create_dir_all(&data_dir).expect("data_dir");
    std::fs::create_dir_all(&agent_root).expect("agent_root");

    // Bootstrap empty graph schema before starting read-only gateway search check.
    {
        let boot_store =
            Arc::new(Store::open(&data_dir.join("knowledge_graph.sqlite")).expect("boot store"));
        let _ = GraphWriter::open(boot_store, data_dir.join("blobs")).expect("boot writer");
    }

    let config = RuntimeConfig::resolve(
        Some(&data_dir.to_string_lossy()),
        None,
        Some(&dead_origin),
        None,
    );
    let loop_config = lattice_agent::LoopConfig {
        worker_origin: dead_origin.clone(),
        runs_dir: scratch.path().join("rust_agent_runs"),
        client: Some(client()),
        proposals: None,
        hooks: None,
        external: None,
    };
    let product =
        OneDoorState::open_with_config(config, &dead_origin, client(), &agent_root, loop_config)
            .expect("product");
    let gw_state = GatewayState::new(Arc::new(FixedProvider::new(dead_origin, dead_port)))
        .expect("gw")
        .with_db_path(data_dir.join("knowledge_graph.sqlite"))
        .with_agent_root(&agent_root)
        .with_pinned_posture(Posture::Open)
        .with_product(Arc::new(product));
    let gateway = TestGateway::start_with_state(gw_state).await;

    let search_res = client()
        .get(gateway.url("/rust/search/hybrid?q=architecture"))
        .send()
        .await
        .expect("native search while worker dead");
    assert_eq!(search_res.status(), 200);
    gateway.stop().await;

    // --- 16.4B: Mid-indexing interruption + restart recovers with 0 duplicates & 0 lost files! ---
    let vault_dir = scratch.path().join("vault");
    std::fs::create_dir_all(&vault_dir).expect("vault_dir");
    let mut watch_engine = DebouncedWatchEngine::new(
        &vault_dir,
        DebouncedWatchConfig {
            debounce_window_ms: 50,
            max_batch_size: 10,
            reconcile_interval_ms: 1_000,
            watch: WatchConfig::default(),
        },
        100,
    );

    for (name, body) in [
        (
            "doc1.md",
            "# Doc 1
First durable note body for recovery.",
        ),
        (
            "doc2.md",
            "# Doc 2
Second durable note body for recovery.",
        ),
        (
            "doc3.md",
            "# Doc 3
Third durable note body for recovery.",
        ),
    ] {
        std::fs::write(vault_dir.join(name), body).expect("write vault doc");
        watch_engine.record_change(RawFsChange::Touched(name.into()), 120);
    }
    let batch = watch_engine.poll_batch(200, false);
    assert_eq!(batch.upserts.len(), 3);

    let embedder = LocalEmbeddingModel::new(384);
    let contract = embedder.contract();

    // Crash after indexing ONLY first 2 of the 3 files!
    {
        let store1 =
            Arc::new(Store::open(&data_dir.join("knowledge_graph.sqlite")).expect("store1"));
        let writer1 = GraphWriter::open(store1, data_dir.join("blobs")).expect("writer1");
        for rel in &batch.upserts[..2] {
            let text = std::fs::read_to_string(vault_dir.join(rel)).expect("read");
            sync_document_incremental(
                &writer1,
                &IncrementalSyncRequest {
                    title: rel.clone(),
                    text,
                    source_uri: format!("/vault/{rel}"),
                    moved_from_uri: None,
                    workspace_id: Some("personal".into()),
                    owner: None,
                    modified_at: Some("2026-11-19T05:00:00Z".into()),
                    metadata: Map::new(),
                },
                &contract,
                |texts| Ok(texts.iter().map(|t| embedder.embed(t)).collect()),
            )
            .expect("pre-crash sync");
        }
    }

    // Restart process: re-run incremental sync across ALL 3 files!
    let store2 = Arc::new(Store::open(&data_dir.join("knowledge_graph.sqlite")).expect("store2"));
    let writer2 = GraphWriter::open(Arc::clone(&store2), data_dir.join("blobs")).expect("writer2");
    let mut dup_skips = 0usize;
    let mut fresh_writes = 0usize;
    for rel in &batch.upserts {
        let text = std::fs::read_to_string(vault_dir.join(rel)).expect("read");
        let receipt = sync_document_incremental(
            &writer2,
            &IncrementalSyncRequest {
                title: rel.clone(),
                text,
                source_uri: format!("/vault/{rel}"),
                moved_from_uri: None,
                workspace_id: Some("personal".into()),
                owner: None,
                modified_at: Some("2026-11-19T05:01:00Z".into()),
                metadata: Map::new(),
            },
            &contract,
            |texts| Ok(texts.iter().map(|t| embedder.embed(t)).collect()),
        )
        .expect("post-restart sync");
        if receipt.duplicate && receipt.embedder_calls == 0 {
            dup_skips += 1;
        } else {
            fresh_writes += 1;
        }
    }
    assert_eq!(
        dup_skips, 2,
        "already-committed files must skip re-embedding"
    );
    assert_eq!(
        fresh_writes, 1,
        "uncommitted file must complete on recovery"
    );

    let doc_count: i64 = store2
        .with_read_conn(|conn| {
            Ok(conn.query_row(
                "SELECT COUNT(*) FROM nodes WHERE type = 'Document'",
                [],
                |r| r.get(0),
            )?)
        })
        .expect("doc count");
    assert_eq!(
        doc_count, 3,
        "exact 3 documents with zero duplicates or losses"
    );

    // --- 16.4C: Agent WaitingApproval survives full restart and resumes idempotently! ---
    let wf_dir = scratch.path().join("durable_workflows");
    let output_note = scratch.path().join("approved_summary.md");
    let pre_pause_runs = AtomicUsize::new(0);

    let approval_token = {
        let wf_store = DurableWorkflowStore::new(&wf_dir);
        wf_store
            .create(
                WorkflowSpec {
                    workflow_id: "wf_sec16_resume".into(),
                    goal: "Read Brain & write approved summary".into(),
                    user_email: Some("owner@local".into()),
                    workspace_id: Some("personal".into()),
                    rollback_on_failure: true,
                    steps: vec![
                        WorkflowStepSpec {
                            step_id: "read_brain".into(),
                            title: "Read Brain".into(),
                            action: "search".into(),
                            args: json!({"q": "recovery"}),
                            depends_on: vec![],
                            mutating: false,
                            requires_approval: false,
                            max_retries: 0,
                            rollback_path: None,
                            consulted_node_ids: vec!["doc:1".into()],
                        },
                        WorkflowStepSpec {
                            step_id: "write_report".into(),
                            title: "Write report".into(),
                            action: "write_file".into(),
                            args: json!({"path": "approved_summary.md"}),
                            depends_on: vec!["read_brain".into()],
                            mutating: true,
                            requires_approval: true,
                            max_retries: 0,
                            rollback_path: Some(output_note.clone()),
                            consulted_node_ids: vec![],
                        },
                    ],
                },
                5_000.0,
            )
            .expect("create wf");
        let paused = wf_store
            .run_until_yield("wf_sec16_resume", 5_005.0, |_step| {
                pre_pause_runs.fetch_add(1, Ordering::SeqCst);
                Ok(StepExecOutput {
                    value: json!({"hits": 3}),
                    generated_artifacts: vec![],
                })
            })
            .expect("pause at approval");
        match paused {
            WorkflowAdvanceOutcome::PausedForApproval { approval_token, .. } => approval_token,
            other => panic!("expected PausedForApproval, got {other:?}"),
        }
    };
    assert_eq!(pre_pause_runs.load(Ordering::SeqCst), 1);

    // Restart: brand new DurableWorkflowStore instance reloads state, approves, and finishes!
    let restarted_wf_store = DurableWorkflowStore::new(&wf_dir);
    restarted_wf_store
        .approve_step(
            "wf_sec16_resume",
            "write_report",
            &approval_token,
            Some("owner@local"),
            5_020.0,
        )
        .expect("approve after restart");
    let finished = restarted_wf_store
        .run_until_yield("wf_sec16_resume", 5_025.0, |step| {
            if step.step_id == "read_brain" {
                pre_pause_runs.fetch_add(1, Ordering::SeqCst);
            } else {
                std::fs::write(
                    &output_note,
                    "# Approved Summary
Recovered cleanly.
",
                )
                .expect("write approved output");
            }
            Ok(StepExecOutput {
                value: json!({"written": true}),
                generated_artifacts: vec!["approved_summary.md".into()],
            })
        })
        .expect("resume to completion");
    assert_eq!(
        finished,
        WorkflowAdvanceOutcome::Completed {
            workflow_id: "wf_sec16_resume".into()
        }
    );
    assert_eq!(
        pre_pause_runs.load(Ordering::SeqCst),
        1,
        "completed step before pause must never re-execute after restart"
    );
    let loaded = restarted_wf_store
        .load("wf_sec16_resume", 5_030.0)
        .expect("load")
        .expect("record");
    assert_eq!(loaded.status, WorkflowStatus::Completed);
    assert!(output_note.is_file());
}
