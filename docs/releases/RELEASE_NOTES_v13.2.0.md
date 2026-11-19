# Lattice AI 13.2.0 — Living Synthesis

> **Status: historical** — point-in-time release note.

2026-11-19

**13.2.0 Living Synthesis** completes the v13.2.0 roadmap reliability, epistemic grounding, incremental ingestion, durable agent execution, and fault-isolation upgrades across `lattice-core`, `lattice-retrieval`, `lattice-ingest`, `lattice-agent`, `lattice-chat`, `lattice-host`, `lattice-platform`, and the stateless Python compute worker — locked end-to-end by the **Section 16 Product Verification Suite** (`rust/lattice-host/tests/roadmap_section16_suite.rs`).

## What landed

- **Bi-Temporal Knowledge Graph & Canonical Entity Resolution (`temporal.rs`, `entity_resolution.rs`, `db.rs`):**
  - `rust/lattice-core/src/temporal.rs`: Tracks both real-world **valid time** (`valid_from`..`valid_until`) and Brain **transaction time** (`recorded_at`, `superseded_at`), supporting point-in-time and current-state timeline queries (`query_temporal_timeline`, `stamp_temporal_fact`) and distinguishing `TemporalEvolution` and `ComplementaryRefinement` (`REFINES`) from `ActiveContradiction`.
  - `rust/lattice-core/src/entity_resolution.rs`: Non-destructive, provenance-preserving canonical entity consolidation (`EntityAliasBook`, Korean postposition stripping, cross-lingual alias clusters, `SAME_AS` edges, and reversible `rollback_entity_consolidation`).
  - `rust/lattice-core/src/db.rs`: Synchronized database write-ownership documentation to reflect native Rust `GraphWriter` single-writer ownership alongside the stateless Python compute worker.
- **Claim-Level Evidence Grounding, Abstention & Separated RAG Benchmark (`grounding.rs`, `rag_eval_grounded.rs`):**
  - `rust/lattice-retrieval/src/grounding.rs`: Deterministic claim-level evidence grounding and citation verification (`verify_grounding_and_abstention`), catching unsupported numbers, polarity inversions, and superseded-citation leaks while failing closed (`AbstentionReason`) with bilingual `ko`/`en` abstention messages when active evidence is missing or misaligned.
  - `rust/lattice-retrieval/src/rag_eval_grounded.rs`: Separated Retrieval vs. Generation RAG benchmark (`run_separated_rag_evaluation`) over the production `LocalEmbeddingModel` (`lattice-local-hash-v1`), asserting Korean/English/Mixed `Recall@5`, `NDCG@10`, `MRR`, `1.0` superseded exclusion, `1.0` abstention accuracy, and model-swap context invariance across `gemma-4-local`, `qwen-local`, and `cloud-frontier`.
- **Event-Driven Incremental Sync & Structured Multimodal Ingestion (`incremental_watch.rs`, `incremental_sync.rs`, `document_structure.rs`):**
  - `rust/lattice-ingest/src/incremental_watch.rs`: OS notification-driven `DebouncedWatchEngine` (`notify::RecommendedWatcher`) with burst debounce coalescing, bounded batching (`max_batch_size`), SHA-256 rename/move correlation, and periodic `WatchScanner` snapshot reconciliation.
  - `rust/lattice-ingest/src/incremental_sync.rs`: Chunk-level incremental re-embedding (`sync_document_incremental`) reusing unchanged `(text_hash, model_id, dimension)` vectors, relocating moved files with bi-temporal `SUPERSEDES` lineage, and retiring deleted chunks immediately.
  - `rust/lattice-ingest/src/document_structure.rs` and `local_files_api/ingest.rs`: Preserves heading hierarchies, pages/slides/sheets, header-anchored `table_structured` chunks, captions, and optional local `OcrVisionProvider` `ImageText` nodes during file upload and ingestion.
- **Durable Checkpointed DAG Agent Workflow Engine (`kernel/workflow.rs`):**
  - `rust/lattice-agent/src/kernel/workflow.rs`: Atomic temp-file-and-rename `DurableWorkflowStore` with acyclic topological validation (`WorkflowSpec::validate_dag`), parallel read-only vs. serial mutating `ExecutionWave` scheduling, SHA-256 `idempotency_ledger` replay, mid-step crash reconciliation, constant-time `WaitingApproval` token pause/resume across restarts, pre-mutation file rollback snapshots, and Brain `USED_BY_AGENT` / `GENERATED_BY` provenance export.
- **Worker Seam Resilience, Cloud Secret-File Guard & Honest Graph Telemetry (`worker.rs`, `worker_resilience.py`, `boundary.rs`, `onedoor.rs`, `audit.rs`):**
  - `rust/lattice-core/src/worker.rs` and `latticeai/api/worker_resilience.py` (`worker_profile.py`, `tests/unit/test_worker_resilience.py`): Opt-in `WorkerSeamClient::post_idempotent_json` with bounded `WorkerRetryPolicy` and `x-lattice-idempotency-key`, paired with Python `WorkerIdempotencyStore` in-flight coalescing, replay caching, and unary compute timeout isolation (`LATTICEAI_WORKER_COMPUTE_TIMEOUT_S`).
  - `rust/lattice-chat/src/boundary.rs`: Blocks secret source files (`.env`, `.env.*`, `.pem`, `.key`, `.p12`, `.pfx`, `id_rsa*`, `id_ed25519*`, `credentials.json`, while exempting `.example`/`.sample`/`.template`) from outbound cloud context payloads.
  - `rust/lattice-host/src/gateway/onedoor.rs` and `rust/lattice-platform/src/adminops/admin/{audit,handlers}.rs`: `probe_store_graph_stats` explicitly distinguishes uninitialized (`empty`), healthy (`ok`), and SQL failure (`unavailable`) states instead of reporting `0` nodes on broken queries.
- **Section 16 End-to-End Product Verification Suite (`rust/lattice-host/tests/roadmap_section16_suite.rs`):**
  - **16.1** Separated RAG accuracy (`Recall@5 >= 0.99`, `MRR >= 0.90`), `1.0` superseded exclusion, `1.0` unanswerable abstention, and Gemma/Qwen/Cloud model-swap invariance.
  - **16.2** Bi-temporal `Jan → Mar → May` point-in-time and current-state policy evolution plus cross-lingual `Project Alpha` / `프로젝트 알파` entity consolidation.
  - **16.3** Cloud boundary zero egress in `local_only`, sensitive/`local_only`/`.env.production` exclusion in `cloud_allowed`, and review-staged cloud KG expansion (`auto_commit == false`).
  - **16.4** Fault recovery under unreachable Python worker (fast fail + native SQLite search `200`), mid-indexing crash recovery (0 duplicates, 0 lost files), and `WaitingApproval` workflow resume across full restarts without re-running completed steps.

## Artifacts

- `dist/ltcai-13.2.0-py3-none-any.whl`
- `dist/ltcai-13.2.0.tar.gz`
- `ltcai-13.2.0.tgz`
- `dist/ltcai-13.2.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_13.2.0_aarch64.dmg`
