# Lattice AI 13.2.1 — Living Synthesis

> **Status: canonical** — current release note.

2026-11-19

**13.2.1 Living Synthesis** completes a repository-wide structural refactoring and dead-code cleanup across all Rust workspace crates (`lattice-auth`, `lattice-core`, `lattice-ingest`, `lattice-retrieval`, `lattice-agent`, `lattice-platform`, `lattice-host`), the Python worker & model capability registry (`latticeai/`), and the TypeScript/React Studio (`frontend/src/`), alongside Ubuntu `t64` visual CI mirror failover hardening — preserving every public HTTP, MCP, OpenAPI, and SQLite contract at **100% Vitest coverage**.

## What landed

- **Rust Workspace Modularization (`lattice-auth`, `lattice-core`, `lattice-ingest`, `lattice-retrieval`, `lattice-agent`, `lattice-platform`, `lattice-host`):**
  - `lattice-agent`: Split `kernel/agentloop/guided/args.rs` (`guided/suggestions.rs`), `kernel/proposals.rs` (`proposals_store.rs`, `proposals_tests.rs`), `parse/action.rs` (`action_salvage.rs`), `tools/catalog.rs` (`catalog_args.rs`, `catalog_tests.rs`), and `kernel/agentloop/execution/tests.rs` (`tests_governance.rs`).
  - `lattice-platform` & `lattice-host`: Split `toolsurface/agents/mod.rs` (`run_handlers.rs`), `modelops/models_catalog/probe.rs` (`probe_sysinfo.rs`, `probe_tests.rs`), `workspaceos/workspace/{handlers_more,handlers_core}.rs` (`handlers_orgs.rs`, `handlers_snapshots.rs`), `knowledge/portability/{share,crypto}.rs` (`share_bundle.rs`, `crypto_tests.rs`), `knowledge/network_boundary.rs` (`network_boundary_stores.rs`), `governance/workflow_designer/executor.rs` (`executor_steps.rs`, `executor_tests.rs`), `tests/mcp_replay.rs` (`tests/mcp_streamable.rs`), `lattice-host/src/gateway/mod.rs` (`gateway/tests.rs`), and `lattice-host/tests/gateway_onedoor.rs` (`tests/gateway_source_contracts.rs`).
  - `lattice-auth`, `lattice-core`, `lattice-ingest` & `lattice-retrieval`: Extracted `scim.rs`, `graph_write/{vectors_sqlite,vectors_postgres}.rs`, `document_structure_parsers.rs`, `local_files_api/{enrich_sections,ingest_archive}.rs`, `worker_parse.rs`, `bm25/sidecar_sync.rs`, `brain_api/proposal_candidates.rs`, `memory_api/{brief_actions,kg_vector_status,request_parse,self_model_write_tests}.rs`, `rag_eval_grounded_fixtures.rs`, and `history_tests.rs`.
- **Python Modularization (`model_capability_registry.py`, `worker_compute.py`):**
  - Split `latticeai/services/model_capability_registry.py` into `model_capability_types.py`, `model_capability_catalog.py`, and re-exporting `model_capability_registry.py`, and extracted `latticeai/api/worker_compute_render.py` from `latticeai/api/worker_compute.py` with full backward compatibility.
- **Cross-Stack Dead-Code Removal & Frontend `brainData` Split (Vitest 100%):**
  - Removed unreferenced Rust helpers/builders (`DecodeError::column_of`, `graph_skill_briefs`, `Workspace::shared_workspace`, `ModelsCatalogState::with_compat_profiles`, `SetupState::with_runner`, `PluginsState::with_plugins_dir`, `SupervisorConfig::behind_gateway`) and unused Python sidecar delta helpers (`store_vector_count`, `load_store_ids`, `_load_missing_items`).
  - Removed retired orbital Frontend Brain layer components and orphaned exports (`BrainGraphLayer`, `BrainMemoryLayer`, `BrainRelationshipLayer`, `graphLayout`, `ProductCommandCenter`, `GraphStatus`, `RetrievalStatus`, `MemoryStatus`, `canPickFolder`, `INGESTION_STAGE_ORDER`, `DEPTHS`, `MultiHopSynthesis`, `FlagKey`) and split `frontend/src/features/brain/brainData.ts` into `brainDataReaders.ts` and `brainDataReadiness.ts` while maintaining **100% statement, branch, function, and line coverage**.
- **Visual CI Ubuntu `t64` Mirror Resilience (`.github/workflows/ci.yml`):**
  - Batched Ubuntu 24.04/26.04 `t64` Chromium shared-library resolution into a single `apt-cache` pass with `15s` HTTP mirror timeouts, `Acquire::Retries=3`, and automatic `mirrors.ubuntu.com` fallback.

## Artifacts

- `dist/ltcai-13.2.1-py3-none-any.whl`
- `dist/ltcai-13.2.1.tar.gz`
- `ltcai-13.2.1.tgz`
- `dist/ltcai-13.2.1.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_13.2.1_aarch64.dmg`
