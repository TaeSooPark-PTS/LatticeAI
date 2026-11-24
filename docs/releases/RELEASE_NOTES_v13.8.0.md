[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./RELEASE_NOTES_v13.8.0.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](../ko/releases/RELEASE_NOTES_v13.8.0.md)

# Lattice AI v13.8.0 — Living Synthesis (Digital Brain Platform Hardening)

Release date: **2026-11-24**

## Highlights

### P0 — Trust, Evaluation Honesty, Data Integrity, Entity Resolution & Security
- **Authentic RAG Evaluation Framework (`rust/lattice-retrieval/src/rag_eval_grounded.rs`, `rag_eval_dataset.rs`, `datasets/rag_eval_v1.json`, `scripts/run_real_model_rag_eval.py`):**
  - Strictly separates `FixtureDeterministic`, `RealModelLocal`, `RealModelCloud`, `RetrievalOnly`, and `EndToEnd` execution modes with explicit `Executed` / `FixtureOnly` / `Skipped` / `Unavailable` status markers and complete run provenance (`dataset_version`, `prompt_version`, `embedding_model`, `provider`, `execution_location`).
  - Added a versioned **320-query RAG evaluation benchmark dataset** (`datasets/rag_eval_v1.json`, `rag-eval-v1.0.0`) spanning all 9 required categories with verified `0` workspace-boundary leaks and `100%` superseded exclusion.
- **Knowledge Graph Data Integrity & Safe Repair (`rust/lattice-core/src/integrity.rs`, `rust/lattice-core/tests/integrity_fault_injection.rs`, `GET /api/brain/integrity`, `POST /api/brain/integrity/repair`):**
  - Unified checker covering SQLite `integrity_check`/`foreign_key_check`, dangling edges (`edges`/`edges_v2`/`edge_occurrences`), orphan chunks/vectors/provenance, missing provenance, cross-workspace edge leaks, duplicate natural keys, missing local file paths, secondary index drift (`nodes_v2`/`node_fts`/`vector_embeddings`), and DAG relationship cycles.
  - Supports dry-run blast-radius previews, automatic `VACUUM INTO` pre-repair snapshot backups, safe auto-repairs vs. approval-gated repairs, and a fault-injection test suite.
- **Entity Resolution & Safe Deduplication (`rust/lattice-core/src/entity_resolution.rs`, `entity_resolution_ext.rs`, `/api/brain/entities/{candidates,merge,rollback}`):**
  - Fixed type-scoped exact matching so homonyms across incompatible entity types never auto-merge (`Person:"Apple"` vs `Organization:"Apple"`).
  - Added fuzzy matching, multi-signal disambiguation scoring, `AutoMerge` vs. `ReviewCandidate` vs. `RejectedHomonym` bands, persistent `entity_aliases`/`entity_merge_audit`/`entity_do_not_merge` tables, strict cross-workspace merge prohibition, edge reference migration, and selective per-merge rollback (`rollback_entity_merge`).
- **Security, TOCTOU/Symlink Sandbox & Cloud Egress PII/Secret Masking (`rust/lattice-agent/src/tools/safe_io.rs`, `content/untrusted.rs`, `rust/lattice-core/src/redact.rs`, `rust/lattice-chat/src/pipeline/answers.rs`):**
  - Implemented OS-level handle-relative fd-confined I/O (`openat` + `O_NOFOLLOW` + `O_CLOEXEC` + `fstat` root `(st_dev, st_ino)` verification) across `write_file`, `edit_file`, `todo_write`, `create_web_project`, `create_document`, `save_note`, and `package_script`, plus real symlink/TOCTOU swap tests (`security_sandbox_toctou.rs`).
  - Promoted compiled `OnceLock` PII + secret redaction into `lattice-core::redact` and applied it to BOTH outbound cloud user prompts (`&question`) and KG context (`compact_text`), `LoopTrace`, and `/host/diagnostics`.
  - Added workspace-scoped `DurableWorkflowStore::load_scoped`, `ingestion_jobs.workspace_id`, symlink escape guards across `fs.rs` and `watch.rs`, sensitive-path read denylist in `local_files_api/local.rs`, attached short-flag path validation (`rg -f/etc/passwd`), and multilingual EN/KO untrusted-content prompt-injection framing.

### P1 — Temporal Knowledge, Relationship Intelligence, Hybrid Retrieval, Shadow Embeddings, Agent Reliability, Multi-Agent & Learning Loop
- **Temporal Knowledge & Relationship Intelligence (`rust/lattice-core/src/temporal.rs`, `graph_write/projection_v2.rs`, `graph_write/documents.rs`):** Unified `valid_from`/`valid_to`/`observed_at`/`source_timestamp`/`updated_at`, synchronized `nodes_v2` validity windows, populated `edges_v2` evidence/confidence/status, and added `GraphWriter::terminate_edge` + `GET /api/brain/entities/{id}/timeline` + `GET /api/brain/relationships/evolution`.
- **Hybrid Retrieval (`rust/lattice-retrieval/src/bm25/`):** Added hub fan-out & edge traversal budgets, near-duplicate content deduplication, deterministic lexical phrase reranker, unified `score_explanation` (per-channel ranks + RRF + rerank boost), and structured `fallbacks` reporting.
- **Embedding Model Registry & Shadow Index (`rust/lattice-core/src/embedding_registry.rs`, `latticeai/services/embedding_recommendation.py`):** Fixed the Python-to-Rust `embedding_index_contract` schema mismatch and added persistent `EmbeddingModelSpec` registry, space-compatibility guards, resumable shadow re-indexing (`vector_embeddings_shadow`), quality-gated atomic cutover, and instant rollback.
- **Agent Runtime Reliability & Multi-Agent Collaboration (`rust/lattice-agent/src/kernel/workflow.rs`, `multi_agent.rs`, `trace.rs`):** Added `StepErrorKind` (retryable vs. non-retryable), `WorkflowStatus::PartialSuccess`, `ImpactPreview`, redacted trace export bundles, and `MultiAgentSession` enforcing least-privilege roles (`Supervisor`, `Planner`, `Researcher`, `Executor`, `Reviewer`), isolated private scratchpads, redacted shared blackboard, conflict escalation, hierarchical budgets, and handoff cycle prevention.
- **Knowledge Learning Loop (`rust/lattice-core/src/learning_loop.rs`, `/api/brain/corrections*`):** Added `knowledge_corrections` supporting `entity_link`, `fact`, `relationship`, and `citation` corrections with `pending`/`confirmed`/`rejected` states, before/after audit snapshots, selective FTS5 re-indexing, revert, and hard-enforced `external_training_upload_allowed = 0`.

### P2 & P3 — Ingestion, Model Provenance UX, Desktop Watchdog, Advanced KG & Org Governance
- **Automated File Ingestion (`rust/lattice-ingest/src/local_files_api/{watch_bridge.rs,watch_deliver.rs}`):** Consumes `DebouncedWatchEngine::poll_batch` directly with `moved_from_uri` move relocation and per-watch `paused` support.
- **Model Provenance UX (`latticeai/models/model_providers.py`, `frontend/src/features/library/{libraryDomain.ts,ModelsPanel.tsx}`):** Expanded origin country/vendor/execution metadata across 12 model families and surfaced the `Origin Country → Vendor → Local PC vs. Internet` provenance hierarchy in the Model Library UI.
- **Desktop Reliability (`rust/lattice-host/src/supervisor/monitor.rs`, `gateway/routes.rs`):** Added startup health-timeout child termination, consecutive health-failure watchdog auto-restart, and PII/secret-redacted `GET /host/diagnostics`.
- **Advanced KG, Multimodal & Org Governance (`rust/lattice-retrieval/src/brain_api/extensions.rs`, `rust/lattice-platform/src/knowledge/multimodal.rs`, `adminops/admin/audit.rs`, `workspaceos/workspace/orgs.rs`):** Added `GET /api/brain/clusters` (label-propagation topic clustering), `GET /api/brain/gaps` (knowledge gaps detector), `Modality::Video` contract, `prune_audit_events_before` retention enforcement, and offboarding revocation receipts in `orgs::remove_member`.

## Exact Release Artifacts
- `dist/ltcai-13.8.0-py3-none-any.whl`
- `dist/ltcai-13.8.0.tar.gz`
- `ltcai-13.8.0.tgz`
- `dist/ltcai-13.8.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_13.8.0_aarch64.dmg`
