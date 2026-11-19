[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./RELEASE_NOTES_v13.3.0.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](../ko/releases/RELEASE_NOTES_v13.3.0.md)

# Lattice AI 13.3.0 — Living Synthesis

> **Status: historical** — preserved release note for v13.3.0.

2026-11-19

**13.3.0 Living Synthesis** completes the end-to-end production wiring and hardening of every v13.2.0 subsystem across the Rust workspace and Python worker — fixing async concurrency and cancellation bugs in `worker_resilience.py`, wiring `post_idempotent_compute`, `verify_grounding_and_abstention`, `entity_resolution` + `temporal::query_temporal_timeline`, `DebouncedWatchEngine` + `sync_document_incremental`, and `DurableWorkflowStore` directly into live HTTP/chat/watch/workflow paths, eliminating every remaining non-test `.unwrap()` in Rust, consolidating all 7 duplicated `unsafe { libc::localtime_r / gmtime_r / mktime }` blocks into a single safe `lattice-core` clock wrapper, and reducing all hand-written non-test production source files across Rust, Python, and Frontend to ≤800 lines at **100% Vitest coverage**.

## What landed

- **Live Production Wiring of Core v13.2.0 Subsystems:**
  - **Idempotent Compute Seam (`rust/lattice-core/src/worker.rs`, `lattice-ingest`, `lattice-retrieval`, `lattice-chat`, `lattice-platform`):** Added `WorkerSeamClient::post_idempotent_compute` and `post_idempotent_compute_with_policy` (deterministic SHA-256 `x-lattice-idempotency-key` per `(path, body)`), wired across `/worker/parse`, `/worker/extract`, `/worker/embed`, and `/worker/render/*` (using single-attempt coalescing with `max_retries: 0` on interactive query-time embedding in `query_embed.rs` to preserve instant lexical fallback).
  - **Claim-Level Grounding Telemetry (`rust/lattice-chat/src/helpers.rs`):** Wired `lattice_retrieval::grounding::verify_grounding_and_abstention` directly into `assess_answer_grounding`, attaching structured `claim_verification` (`claim_support_ratio`, `citation_precision`, `citation_recall`, `superseded_citation_leaks`, `numeric_mismatches`, `polarity_mismatches`, `should_abstain`, `abstention_reason`) alongside existing grounding verdicts.
  - **Bi-Temporal & Entity Resolution Consolidation (`rust/lattice-retrieval/src/brain_api/consistency.rs`):** Wired `lattice_core::entity_resolution` (`resolve_entities_in_conn`, `apply_entity_consolidation`, `EntityAliasBook`) and `lattice_core::temporal::query_temporal_timeline` into `BrainIntelligenceService::consolidate` (`entity_resolution` and `temporal_timeline` payloads, applying reversible `SAME_AS` consolidation when `apply=true`).
  - **OS-Event Debounced Watch & Incremental Chunk Sync (`rust/lattice-ingest/src/local_files_api/watch_bridge.rs`):** Wired `DebouncedWatchEngine` into the background poller via non-blocking `spawn_blocking` ticks and wired `sync_document_incremental` for tracked file updates without async runtime deadlocks.
  - **Durable Workflow Checkpointing (`rust/lattice-platform/src/governance/workflow_designer/executor.rs`):** Wired `lattice_agent::kernel::workflow::DurableWorkflowStore` to checkpoint workflow runs atomically under `{data_dir}/durable_workflows/{run_id}.json`.
- **Worker Resilience Concurrency & Ingest Hardening:**
  - **Python Compute Middleware (`latticeai/api/worker_resilience.py`, `tests/unit/test_worker_resilience.py`):** Wrapped coalesced waiters (`asyncio.wait_for(asyncio.shield(...))`) in structured `504`/`503` JSON error handling, brought response `body_iterator` streaming inside the unary timeout budget, and ensured `BaseException` / `asyncio.CancelledError` always releases `_inflight[scope_key]`.
  - **Upload Ingest Safety (`rust/lattice-ingest/src/local_files_api/ingest.rs`):** Replaced nanosecond-only temporary filenames with collision-free `{pid}-{nanos}-{seq}` atomic counters and surfaced `persist_structure_and_multimodal_overlay` failures via `structure_overlay_warning` instead of silently discarding errors.
- **Zero Non-Test `.unwrap()`, Single `libc` Time Wrapper & ≤800-Line Production Modularity:**
  - Eliminated all 9 remaining non-test `.unwrap()` calls across `rust/lattice-platform/src/` (`admin/handlers.rs`, `automation.rs`, `portability/archive.rs`, `setup/demo.rs`, `agent_registry.rs`, `workspaceos/features.rs`) — reaching **0** non-test `.unwrap()` calls across the Rust workspace.
  - Consolidated all 7 duplicated `unsafe { libc::localtime_r / gmtime_r / mktime }` blocks into a single safe API in `rust/lattice-core/src/graph_write/clock.rs` (`local_wall_parts`, `local_iso_for`, `naive_local_secs`, `local_offset_for_naive_wall`) and removed the unused `libc` dependency from `rust/lattice-retrieval/Cargo.toml`.
  - Split the final >800-line non-test production files (`rust/lattice-chat/src/{cloud/provider.rs,intents.rs,helpers.rs}` → `provider_tests.rs`, `intents_tests.rs`, `helpers_tests.rs`; `lattice_brain/graph/_kg_common/extraction.py` → `extraction_triples.py`), bringing non-test hand-written production files >800 lines to **0** across Rust, Python, and Frontend, and corrected inaccurate filenames in `docs/releases/RELEASE_NOTES_v13.2.1.md`.

## Artifacts

- `dist/ltcai-13.3.0-py3-none-any.whl`
- `dist/ltcai-13.3.0.tar.gz`
- `ltcai-13.3.0.tgz`
- `dist/ltcai-13.3.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_13.3.0_aarch64.dmg`
