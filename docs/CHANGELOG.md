[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./CHANGELOG.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](./ko/CHANGELOG.md)

# Changelog

The top entry is either the current unreleased main-branch work or the current
release line. Older entries are historical and may describe behavior as it
existed at that release.

## [13.8.0] - 2026-11-24 — Living Synthesis

### Added
- **P0.1 Authentic RAG Accuracy & Evaluation Framework (`rust/lattice-retrieval/src/rag_eval_grounded.rs`, `rag_eval_dataset.rs`, `datasets/rag_eval_v1.json`, `scripts/generate_rag_eval_dataset.py`, `scripts/run_real_model_rag_eval.py`):**
  - Separated `EvalExecutionMode` (`FixtureDeterministic`, `RealModelLocal`, `RealModelCloud`, `RetrievalOnly`, `EndToEnd`) and `EvalModelStatus` (`Executed`, `FixtureOnly`, `Skipped`, `Unavailable`) with complete provenance metadata (`EvalRunMetadata`) and added a versioned 320-query benchmark dataset across all 9 required categories (`rag-eval-v1.0.0`).
- **P0.2 Knowledge Graph Data Integrity Checker & Safe Repair (`rust/lattice-core/src/integrity.rs`, `rust/lattice-core/tests/integrity_fault_injection.rs`, `GET /api/brain/integrity`, `POST /api/brain/integrity/repair`):**
  - Added unified corruption/drift/cycle/cross-workspace checker, dry-run blast-radius planner, pre-repair `VACUUM INTO` snapshot backup, safe auto vs. approval-gated repair execution, and full fault-injection tests.
- **P0.3 Entity Resolution Disambiguation, Persistent Audit & Selective Rollback (`rust/lattice-core/src/entity_resolution.rs`, `entity_resolution_ext.rs`, `/api/brain/entities/{candidates,merge,rollback}`):**
  - Fixed type-scoped exact clustering (`(node_type, key)`) to prevent cross-type homonym merges, and added fuzzy + multi-signal scoring, `AutoMerge`/`ReviewCandidate`/`RejectedHomonym` bands, persistent `entity_aliases`/`entity_merge_audit`/`entity_do_not_merge` tables, strict cross-workspace merge prohibition, edge migration, and selective `rollback_entity_merge`.
- **P0.4 TOCTOU-Safe FD Sandbox, Cloud Egress PII/Secret Masking & Prompt-Injection Guard (`rust/lattice-agent/src/tools/safe_io.rs`, `content/untrusted.rs`, `rust/lattice-agent/tests/security_sandbox_toctou.rs`, `rust/lattice-core/src/redact.rs`, `rust/lattice-chat/src/pipeline/answers.rs`):**
  - Implemented handle-relative `openat` + `O_NOFOLLOW` + `O_CLOEXEC` + `fstat` `(st_dev, st_ino)` workspace I/O, compiled `OnceLock` PII + secret redaction applied to cloud prompts, KG context, `LoopTrace`, and `/host/diagnostics`, workspace-scoped `DurableWorkflowStore::load_scoped`, `ingestion_jobs.workspace_id`, symlink escape guards in `fs.rs`/`watch.rs`, sensitive-path denylist in `local_files_api/local.rs`, attached short-flag path validation, and EN/KO untrusted-content framing.
- **P1.1–P1.7 Core Intelligence, Shadow Embeddings, Multi-Agent & Learning Loop (`rust/lattice-core/src/{temporal.rs,embedding_registry.rs,learning_loop.rs}`, `rust/lattice-retrieval/src/bm25/`, `rust/lattice-agent/src/kernel/{workflow.rs,multi_agent.rs}`, `rust/lattice-retrieval/src/brain_api/extensions.rs`):**
  - Aligned bi-temporal fields & `edges_v2` provenance + `GraphWriter::terminate_edge`; added BM25F hub fan-out caps, near-duplicate dedup, lexical phrase reranker, unified `score_explanation`, and `fallbacks`; fixed Python `embedding_index_contract` schema parity and added persistent `EmbeddingModelRegistry` + resumable shadow re-indexing, quality-gated cutover, and instant rollback; added `StepErrorKind`, `PartialSuccess`, `ImpactPreview`, redacted trace export, 5-role least-privilege `MultiAgentSession`, and local-only `knowledge_corrections` learning loop.
- **P2 & P3 Product Usability, Desktop Watchdog & Org Governance (`rust/lattice-ingest/`, `rust/lattice-host/`, `rust/lattice-platform/`, `latticeai/models/model_providers.py`, `frontend/src/features/library/`):**
  - Consumed `DebouncedWatchEngine::poll_batch` directly with `moved_from_uri` relocation and per-watch `paused` support; surfaced `Origin Country → Vendor → Execution` hierarchy in `ModelsPanel.tsx`; added worker health-watchdog & redacted `GET /host/diagnostics`; added `GET /api/brain/clusters`, `GET /api/brain/gaps`, `Modality::Video`, `prune_audit_events_before`, and offboarding revocation receipts in `orgs::remove_member`.

## [13.7.2] - 2026-11-24 — Living Synthesis

### Changed
- **Canonical Domain Module Paths (`rust/lattice-agent`, `rust/lattice-platform`, `rust/lattice-host`, `rust/lattice-chat`, `rust/lattice-retrieval`):**
  - Migrated all 77+ internal callers and integration tests across the Rust workspace to import directly from canonical domain paths (`lattice_platform::<domain>::*`, `lattice_agent::<group>::*`).

### Removed
- **Obsolete Crate-Root Compatibility Re-export Maps & Scaffold Stub (`rust/lattice-agent/src/lib.rs`, `rust/lattice-platform/src/lib.rs`, `rust/lattice-chat/src/lib.rs`):**
  - Removed the flat crate-root `pub use` compatibility maps from `lattice-agent` and `lattice-platform` so the public API surface is strictly domain-scoped.
  - Removed the unused `lattice_chat::crate_ready()` scaffold stub.

## [13.7.1] - 2026-11-24 — Living Synthesis

### Removed
- **Dead Legacy `forward()` Fallbacks & Compatibility Scaffolding (`rust/lattice-ingest`, `rust/lattice-platform`, `rust/lattice-retrieval`):**
  - Removed legacy `forward()` fallbacks in `POST /api/ingestion/jobs/:job_id/resume`, `POST /api/ingestion/folder`, `POST /knowledge-graph/local/index`, and `POST /api/browser/read-url`; all endpoints now write natively via `GraphWriter` / `NoteIngestor` and return HTTP `503` consistently when `GraphWriter` is unwired.
  - Deleted unused `local_files_api::forward`, `browser_api::forward`, `local_files_api::require_seam`, `local_files_api::http::seam_error`, `search_api::require_seam`, and `lattice_platform::crate_ready()`.
- **Pre-`13.7.0` Review/Plan/Legacy Docs (`docs/`, `docs/ko/`):**
  - Deleted pre-`13.7.0` review reports (`CODE_REVIEW_2026-11-20.md`, `reviews/CODEBASE_REVIEW_AND_IMPROVEMENT_ROADMAP.md`), obsolete `v11.x` migration plans, and `LEGACY_COMPATIBILITY.md` (and Korean mirrors) while preserving all benchmark documents (`ADAPTIVE_HARNESS_BENCHMARK.md`, `BENCHMARKS.md`, `PERFORMANCE.md`).

### Added
- **Git Interop & Unwired-`GraphWriter` 503 Integration Tests (`rust/lattice-ingest/tests/local_files_replay.rs`):**
  - Added `source: "git"` integration test against a temporary `git init` repository (`dry_run` + live commit ingestion) and `unwired_graph_writer_returns_503_consistently` verifying HTTP `503` across all ingestion endpoints.

## [13.7.0] - 2026-11-24 — Living Synthesis

### Added
- **Native Rust Obsidian & Interop Bridges (`rust/lattice-ingest/src/local_files_api/interop.rs`, `rust/lattice-ingest/src/local_files_api/ingest.rs`, `rust/lattice-ingest/tests/local_files_replay.rs`):**
  - Replaced legacy forwarding to retired Python worker routes with native `GraphWriter` ingestion for Obsidian vaults (`[[wikilink]]` `LINKS_TO` edges, `#tags`, `dry_run` preview) and `notion` (directory & `.zip` export), `git` (commit log & changed-file stats), and `mail` (`.eml` & `.ics` `VEVENT`) interop bridges.

### Changed
- **Native ESM Vite 8 & Vitest 5 Configs (`vite.config.mts`, `vitest.config.mts`, `tsconfig.json`):**
  - Migrated `vite.config.ts` and `vitest.config.ts` to `.mts` using `fileURLToPath` + `readFileSync`, eliminating Vite 8's `configLoader: 'native'` deprecation warning.
- **`v13.0.0+` Release Ledger & Modernized `ARCHITECTURE.md`:**
  - Pruned all pre-`v13.0.0` (`v12.x`) release notes and updated `scripts/check_current_release_docs.mjs` and `tests/unit/test_server_app_decomposition.py` to enforce the `v13.0.0+` floor.
  - Modernized `ARCHITECTURE.md` and `docs/ko/ARCHITECTURE.md` concisely to `13.7.0`, removing stale pre-v13 migration history.

## [13.6.0] - 2026-11-24 — Living Synthesis

### Changed
- **Zero-TS5 Native TypeScript 7 OpenAPI Codegen (`scripts/generate_openapi_types.mjs`, `scripts/check_openapi_drift.mjs`, `package.json`, `package-lock.json`):**
  - Replaced `openapi-typescript@7.13.0`, the `openapi-ts5` (`npm:typescript@^5.9.3`) alias, `scripts/run_openapi_typescript.mjs`, and the `package.json` `overrides` workaround with a deterministic zero-dependency ESM OpenAPI 3.x-to-TypeScript declaration generator (`scripts/generate_openapi_types.mjs`).
  - Restored `typecheck:frontend` directly to `npx tsc -p tsconfig.json --noEmit` on `typescript@7.0.2` (`tsgo`) with zero TypeScript 5 dependencies remaining in `package-lock.json`.
- **Domain-Modularized Unit Test Suite (`tests/unit/_runtime_test_fakes.py`, `tests/unit/test_*.py`):**
  - Split the five monolithic `tests/unit/test_cov_p1b_remaining*.py` coverage bundles (3,379 lines) into nine focused domain unit test modules (`170–449` lines each) sharing `_Done`, `_Popen`, `_UrlResp`, and `_fast_deadline` via `tests/unit/_runtime_test_fakes.py`, preserving all 45 tests and `>= 96%` Python line coverage.

## [13.5.0] - 2026-11-24 — Living Synthesis

### Added
- **Cross-Platform One-Way Qwen3.8 27B 4-bit Setup & Memory-Pressure Tuning (`latticeai/services/engine_adapters.py`, `latticeai/services/model_engines.py`, `latticeai/services/model_runtime/`, `rust/lattice-platform/src/modelops/`, `frontend/src/features/library/ModelsPanel.tsx`):**
  - Added OS-aware `resolve_engine_installer()` supporting Windows `winget` (`Ollama.Ollama`, `ggml.llamacpp`, `ElementLabs.LMStudio`) + `pip` `llama-cpp-python[server]` fallback, Linux `curl` Ollama installer + `pip` fallbacks, and macOS Apple Silicon `omlx` Homebrew + zero-Homebrew GitHub Release universal2 wheel fallback (`~/.omlx/venv`).
  - Added automatic cross-platform routing (`resolve_cross_platform_model_request`, `_recommended_with_engine_options`, Rust `probe.rs`, `scan.rs`, `probe_sysinfo.rs` Windows RAM & `winget` detection), `unsloth/Qwen3.8-27B-GGUF` public mirror fallback, and default `HF_XET_HIGH_PERFORMANCE=1` high-speed transfers.
  - Added live available-RAM pressure detection (`host_available_ram_gb`) across macOS, Linux, and Windows so `omlx_serve_args` dynamically switches to `--memory-guard safe`, `--hot-cache-max-size 1GB`, and `--max-concurrent-requests 1` when concurrent apps tighten 32GB memory, plus GPU/quantized-KV `llamacpp_serve_args`.
  - Connected automatic plan confirmation token binding in `status.install_engine` and `install_required` consent gating in `ModelsPanel.tsx` for true single-click engine installation, download, server startup, and model load.

### Changed
- **TypeScript 7 (`tsgo`) & `@babel/parser` AST Migration (`package.json`, `scripts/check_i18n_literals.mjs`, `scripts/run_openapi_typescript.mjs`):**
  - Upgraded root `typescript` from `^5.9.3` to `^7.0.2`, migrated `scripts/check_i18n_literals.mjs` from the TypeScript 5 compiler AST API to `@babel/parser`, and isolated `openapi-typescript` behind `scripts/run_openapi_typescript.mjs`.
- **Modular Refactoring & Dead-Code Cleanup (`latticeai/services/{engine_adapters.py,model_engines.py,model_loading.py,model_catalog.py}`, `latticeai/core/policy.py`, `frontend/src/{routes.ts,lib/folderPicker.ts}`, `rust/Cargo.toml`):**
  - Extracted `engine_adapters.py` (`EngineAdapter` registry) from `model_engines.py` and unified duplicated blocking/SSE 7-way engine preparation logic in `model_loading.py` (`716 -> 492` lines).
  - Removed orphaned `recommend_models_for_pc` (`model_catalog.py`), unused `capabilities_for_role` (`policy.py`), unused `routeAliases` (`routes.ts`), unused `hasBrowserFolderPicker` (`folderPicker.ts`), unused workspace crate `walkdir` (`rust/Cargo.toml`), and redundant `tests/unit/test_model_loading_deps.py`.

## [13.4.2] - 2026-11-24 — Living Synthesis

### Added
- **oMLX Engine & Qwen3.8 27B 4-bit Support (`latticeai/models/model_providers.py`, `latticeai/services/model_engines.py`, `latticeai/services/model_runtime/`, `latticeai/services/model_capability_catalog.py`):**
  - Integrated `omlx` (`jundot/omlx`) as a first-class local server engine with hardware-tuned KV cache & memory-guard flags (`ensure_omlx_server`, `omlx_serve_args`), Hugging Face cache `org--repo` normalization, and `/engines` status reporting.
  - Lowered `mlx-community/Qwen3.8-27B-4bit` `recommended_ram_gb` from `48.0` to `32.0` after Apple M5 32GB verification and exposed `omlx` models in both recommended and downloadable catalogs.

## [13.4.1] - 2026-11-23 — Living Synthesis

### Fixed
- **Dependency Major/Breaking Upgrades (`#58`, `#60`, `#61`):**
  - **Frontend (`package.json`, `tsconfig.json`, `frontend/src/vite-env.d.ts`):** Resolved `openapi-typescript@7.13.0` peer dependency conflict with `typescript@7.0.2` via `overrides`, migrated `tsconfig.json` away from removed `baseUrl` to relative `paths`, added `vite/client` reference types for CSS side-effect imports, and maintained 100% Vitest 5.1.1 coverage across all 111 test files.
  - **Rust Workspace (`rust/`, `src-tauri/`):** Updated `lattice-core`, `lattice-auth`, `lattice-agent`, `lattice-chat`, `lattice-ingest`, `lattice-retrieval`, and `lattice-platform` for `sha2` 0.11 / `digest` 0.11, `rusqlite` 0.43, `fancy-regex` 0.20, `axum` 0.8, `scrypt` 0.12, `zip` 9, `reqwest` 0.13, and `tauri` 2.11.6.

## [13.4.0] - 2026-11-20 — Living Synthesis

### Added
- **Universal Adaptive Agent Harness (`rust/lattice-agent/src/kernel/`):**
  - Added 3-turn runtime micro-probing (`probe.rs`) scoring action contract fidelity, menu selection, and grounding truthfulness (`agent_profile_probe.json` cache with runtime demotion updates) with zero per-model code branches.
  - Added observation-first tool ranking (`guided/ranking.rs`) and bilingual (`en`/`ko`) domain classification (`guided/domain.rs`, `guided/args.rs`, `guided/suggestions.rs`) so inspection actions (`computer_ui_inspect`, `web_search`, `web_fetch`, `read_file`) execute ahead of blind actuation.
  - Added deterministic UI element bounding-box center resolution (`execution/ui_resolve.rs`) computing exact click coordinates `(x + w/2, y + h/2)` for named controls, plus malformed-JSON repair and ChatML completion prefill (`execution/{repair.rs,mod.rs}`, `parse/action_salvage_text.rs`).
  - Added active vs. superseded evidence separation (`transcript/rag_grounding.rs`, `transcript/summaries.rs`) isolating `[ACTIVE GROUNDED EVIDENCE]` facts from superseded knowledge nodes and enforcing fail-closed abstention.
- **Native Web Harness & Computer Use Bridge (`rust/lattice-agent/src/tools/web/{mod.rs,html.rs,tests.rs}`, `latticeai/tools/{web.py,computer.py}`):**
  - Modularized native Rust web fetching and HTML extraction and aligned Python worker web/computer-use tool schemas and bridge parity (`tests/unit/test_web_and_cu_tools.py`, `rust/lattice-platform/tests/mcp_replay.rs`).
- **25-Task Empirical Benchmark Suite & Documentation (`scripts/benchmark_adaptive_harness.py`, `rust/lattice-host/examples/harness_bench.rs`, `docs/ADAPTIVE_HARNESS_BENCHMARK.md`, `docs/ko/ADAPTIVE_HARNESS_BENCHMARK.md`):**
  - Added a reproducible 3-arm empirical harness benchmark (`tests/unit/test_adaptive_harness_benchmark.py`, `rust/lattice-agent/tests/adaptive_harness.rs`) demonstrating `28.0%` raw / `24.0%` uncalibrated standard -> `84.0%` (`+56.0 pp` vs raw / `+200.0%` relative gain, `100.0%` Computer Use, `100.0%` RAG Grounding, `80.0%` Tool Calling, `80.0%` Web Handling, `-63.3%` latency vs standard) on local `Qwen2.5-0.5B-Instruct-Q4_K_M`.
- **100% EN <-> KO Bidirectional Documentation Parity (`docs/ko/**`, `scripts/check_doc_languages.mjs`):**
  - Completed pure-Korean mirrors and bidirectional language switcher badges across all canonical GitHub Markdown docs (`npm run docs:check-languages` 100% parity gate).

## [13.3.1] - 2026-11-19 — Living Synthesis

### Added
- Split `rust/lattice-ingest/src/incremental_sync.rs` into two-phase `plan_incremental_sync` + `commit_incremental_sync` (`IncrementalSyncPlan`) and extracted `rust/lattice-ingest/src/local_files_api/watch_deliver.rs` (`try_incremental_sync_async`) so watched-folder edits under semantic `EmbeddingIndexContract`s run async `/worker/embed` only for modified chunks and reuse cached vectors for unchanged sections (verified end-to-end in `rust/lattice-ingest/tests/vault_watch_bridge.rs`).
- Added claim-level verification telemetry (`MessageClaimVerification`, `parseGrounding` parsing, and bilingual `ClaimVerificationStrip` in `frontend/src/features/brain/AnswerProof.tsx` + `BrainConversation.tsx`) surfacing claim support %, citation precision %, numeric/polarity mismatches, superseded source leaks, and abstention recommendations at 100% Vitest coverage.
- Added bilingual `ConsolidationEpistemicSummary` in `frontend/src/features/brain/BrainIntelligencePanel.tsx` surfacing `entity_resolution` (scanned entities, merge clusters, preview candidates / applied `SAME_AS` edges) and `temporal_timeline` (active, historical, unreached facts, and active temporal conflict badge) at 100% Vitest coverage.
- Added `.github/dependabot.yml` for weekly dependency monitoring across `pip`, `npm` (root + `vscode-extension`), `cargo` (`rust` + `src-tauri`), and `github-actions`.

## [13.3.0] - 2026-11-19 — Living Synthesis

### Added
- Added `WorkerSeamClient::post_idempotent_compute` and `post_idempotent_compute_with_policy` (`rust/lattice-core/src/worker.rs`) and wired deterministic SHA-256 `x-lattice-idempotency-key` compute retries across `/worker/parse`, `/worker/extract`, `/worker/embed`, and `/worker/render/*` (`enrich.rs`, `garden_api/process.rs`, `knowledge_graph_api/ingest.rs`, `query_embed.rs`, `tools/meta.rs`, `security_dashboard/export.rs`, `lattice-chat/src/{intents,turn,worker}.rs`).
- Wired `lattice_retrieval::grounding::verify_grounding_and_abstention` into `assess_answer_grounding` (`rust/lattice-chat/src/helpers.rs`) to emit `claim_verification` telemetry alongside existing grounding verdicts.
- Wired `lattice_core::entity_resolution` and `lattice_core::temporal::query_temporal_timeline` into `BrainIntelligenceService::consolidate` (`rust/lattice-retrieval/src/brain_api/consistency.rs`), non-blocking `DebouncedWatchEngine` + `sync_document_incremental` into `rust/lattice-ingest/src/local_files_api/watch_bridge.rs`, and `DurableWorkflowStore` atomic checkpoints into `rust/lattice-platform/src/governance/workflow_designer/executor.rs`.
- Added safe local-wall-time wrappers (`local_wall_parts`, `local_iso_for`, `naive_local_secs`, `local_offset_for_naive_wall`) in `rust/lattice-core/src/graph_write/clock.rs` and removed 6 duplicated `unsafe libc` blocks across `lattice-platform` and `lattice-retrieval` (dropping the unused `libc` dependency from `rust/lattice-retrieval/Cargo.toml`).

### Changed
- Eliminated all 9 remaining non-test `.unwrap()` calls across `rust/lattice-platform/src/` (`0` non-test `.unwrap()` calls remaining across the Rust workspace).
- Split the last remaining >800-line hand-written production source files (`rust/lattice-chat/src/{cloud/provider.rs,intents.rs,helpers.rs}` → `provider_tests.rs`, `intents_tests.rs`, `helpers_tests.rs`; `lattice_brain/graph/_kg_common/extraction.py` → `extraction_triples.py`) for `0` non-test production files >800 lines across Rust, Python, and Frontend.

### Fixed
- Fixed `latticeai/api/worker_resilience.py` coalesced-waiter error handling (`504`/`503` JSON), response body-streaming timeout coverage, and `BaseException`/`asyncio.CancelledError` `_inflight` release with regression coverage in `tests/unit/test_worker_resilience.py`.
- Fixed nanosecond temp-file collision risk (`{pid}-{nanos}-{seq}`) and silent `persist_structure_and_multimodal_overlay` error swallowing (`structure_overlay_warning`) in `rust/lattice-ingest/src/local_files_api/ingest.rs`, and corrected inaccurate filenames in `docs/releases/RELEASE_NOTES_v13.2.1.md`.

## [13.2.1] - 2026-11-19 — Living Synthesis

### Changed
- Modularized high-density Rust modules across `lattice-auth` (`scim.rs`), `lattice-core` (`graph_write/{projection_v2,vectors_contract}.rs`), `lattice-ingest` (`document_structure_parsers.rs`, `local_files_api/{jobs_store,upload_parse}.rs`, `worker_tests.rs`), `lattice-retrieval` (`bm25/sidecar_subword.rs`, `brain_api/proposal_candidates.rs`, `memory_api/{brief_actions,kg_vector_status,request_parse,self_model_write_tests}.rs`, `rag_eval_grounded_fixtures.rs`, `history_tests.rs`), `lattice-agent` (`guided/suggestions.rs`, `proposals_{store,tests}.rs`, `parse/action_salvage.rs`, `tools/catalog_{args,tests}.rs`, `execution/tests_governance.rs`), `lattice-platform` (`agents/run_handlers.rs`, `probe_{sysinfo,tests}.rs`, `handlers_{orgs,snapshots}.rs`, `share_bundle.rs`, `network_boundary_stores.rs`, `executor_{steps,tests}.rs`, `crypto_tests.rs`, `tests/mcp_streamable.rs`), and `lattice-host` (`gateway/tests.rs`, `tests/gateway_source_contracts.rs`).
- Modularized `latticeai/services/model_capability_registry.py` (`model_capability_types.py`, `model_capability_catalog.py`), `latticeai/api/worker_compute.py` (`worker_compute_render.py`), and `frontend/src/features/brain/brainData.ts` (`brainDataReaders.ts`, `brainDataReadiness.ts`) with full re-export compatibility.
- Removed unreferenced helpers, builders, and retired UI layers across Rust (`lattice-agent`, `lattice-platform`, `lattice-host`), Python (`latticeai/core/vector_index/sidecar.py`), and Frontend (`BrainGraphLayer`, `BrainMemoryLayer`, `BrainRelationshipLayer`, `graphLayout`, `ProductCommandCenter`, `GraphStatus`, `RetrievalStatus`, `MemoryStatus`, `canPickFolder`, `INGESTION_STAGE_ORDER`, `DEPTHS`, `MultiHopSynthesis`, `FlagKey`) while maintaining 100% Vitest coverage.

### Fixed
- Hardened `.github/workflows/ci.yml` visual regression setup to batch Ubuntu `t64` Chromium shared-library lookups with `15s` mirror timeouts and `mirrors.ubuntu.com` failover.

## [13.2.0] - 2026-11-19 — Living Synthesis

### Added
- Added bi-temporal knowledge timeline & conflict evolution classification (`rust/lattice-core/src/temporal.rs`: `valid_from`..`valid_until`, `recorded_at`/`superseded_at`, `query_temporal_timeline`, `TemporalEvolution` vs. `ComplementaryRefinement` vs. `ActiveContradiction`) and non-destructive, reversible canonical entity resolution (`rust/lattice-core/src/entity_resolution.rs`: `EntityAliasBook`, Korean particle stripping, `SAME_AS` edges, `rollback_entity_consolidation`).
- Added claim-level evidence grounding, superseded-leak rejection & fail-closed bilingual abstention (`rust/lattice-retrieval/src/grounding.rs`) plus separated Retrieval vs. Generation RAG benchmarking & model-swap invariance evaluation (`rust/lattice-retrieval/src/rag_eval_grounded.rs`) over `LocalEmbeddingModel` (`lattice-local-hash-v1`).
- Added event-driven debounced filesystem watching (`rust/lattice-ingest/src/incremental_watch.rs`), chunk-level incremental re-embedding & move relocation (`rust/lattice-ingest/src/incremental_sync.rs`), and structured table/page/slide/sheet/caption & optional local OCR/vision ingestion (`rust/lattice-ingest/src/document_structure.rs`, `local_files_api/ingest.rs`).
- Added durable checkpointed DAG agent workflow execution (`rust/lattice-agent/src/kernel/workflow.rs`) with acyclic validation, parallel read-only / serial mutating waves, SHA-256 idempotency ledger replay, mid-step crash reconciliation, constant-time `WaitingApproval` pause/resume across restarts, pre-mutation file rollback snapshots, and Brain `USED_BY_AGENT` / `GENERATED_BY` provenance export.
- Added opt-in idempotent worker seam retries (`rust/lattice-core/src/worker.rs`: `WorkerSeamClient::post_idempotent_json`, `WorkerRetryPolicy`), stateless Python compute timeout isolation & idempotent replay coalescing (`latticeai/api/worker_resilience.py`, `worker_profile.py`, `tests/unit/test_worker_resilience.py`), and the Section 16 End-to-End Product Verification Suite (`rust/lattice-host/tests/roadmap_section16_suite.rs`, covering 16.1–16.4).

### Changed
- Updated `rust/lattice-core/src/db.rs` write-ownership documentation to match native Rust `GraphWriter` single-writer ownership alongside stateless Python compute.

### Fixed
- Blocked secret-file sources (`.env`, `.env.*`, `.pem`, `.key`, `.p12`, `.pfx`, `id_rsa*`, `id_ed25519*`, `credentials.json`, while exempting `.example`/`.sample`/`.template`) from outbound cloud context payloads in `rust/lattice-chat/src/boundary.rs`, and distinguished `empty`, `ok`, and SQL-error `unavailable` graph health states in `rust/lattice-host/src/gateway/onedoor.rs` and `rust/lattice-platform/src/adminops/admin/{audit,handlers}.rs`.

## [13.1.0] - 2026-11-18 — Living Synthesis

### Added
- Added scoped Knowledge Graph neighborhood multi-hop synthesis (`rust/lattice-retrieval/src/neighborhood_synthesis.rs`, `GET`/`POST /api/graph/node` `include_reasoning=true`), interactive `GraphNodeInspector` (`frontend/src/pages/brain/GraphNodeInspector.tsx`), hop/conflict filter pills, confidence bars & clickable node navigation in `MultiHopSynthesisView.tsx`, toolbar conflict-only filtering & `/` + `Escape` shortcuts in `DigitalBrainExplorer.tsx`, and interactive contradiction staging/resolution (`ContradictionInspector.tsx`) in `BrainIntelligencePanel.tsx`.
- Added one-click **Save to Brain** assistant insight promotion (`BrainConversation.tsx`), inline staged proposal diff inspection & approval/rejection (`InlineStagedProposals.tsx`), contextual `pendingChatPrompt` handoff (`BrainHome.tsx`), live active-tool indicators in `AgentStepTimeline.tsx`, and one-click Knowledge Graph jump + modal visual evidence preview in `SourceChunkModal` (`AnswerProof.tsx`).
- Added live `embedded / total` progress visualization & one-click **Index now** embedding drain (`POST /api/index/drain`) in `VectorFreshnessNotice` (`BrainSignals.tsx`), human-readable byte/file metrics & cooperative `AbortController` + `unloadModel` download cancellation in `InstallScreen.tsx` and `ModelsPanel.tsx` (`useLibraryModels.ts`), `⌘K` Knowledge Graph node focus & quick language/graph actions in `CommandPalette.tsx`, and live query invalidation on workspace switch in `WorkspaceProfileSwitcher.tsx`.

### Fixed
- Preserved `stale_embedder` status in `rust/lattice-retrieval/src/brain_api/health.rs` (`GET /api/brain/vector-freshness`) so embedder swap recovery notices work end-to-end against the native Rust server, stopped click propagation on inline original-image consent buttons in `AnswerProof.tsx`, and localized fallback error formatting in `InstallScreen.tsx` with zero English leaks in Korean mode at 100% Vitest coverage.

## [13.0.0] - 2026-11-18 — Sovereign Synthesis

### Added
- Added multi-hop Knowledge Graph reasoning chain & neighborhood contradiction/supersession synthesis (`rust/lattice-retrieval/src/bm25/reasoning_paths.rs`, `graph_rag.rs`, `fusion.rs`) plus interactive 2-Hop Synthesis UI (`frontend/src/features/brain/{MultiHopSynthesisView.tsx,MultiHopSynthesisView.test.tsx}`, `Brain.tsx`, `latticeApi.hybridSearchGraphRag`) with 100% Vitest coverage.
- Added automatic reverse `folder_reorganization` undo staging (`is_undo`, `undo_proposal_id`) in `rust/lattice-platform/src/governance/change_proposals.rs` (`tests/reversible_reorganization.rs`) and 1-Click Undo + Rollback badge UI in `frontend/src/features/review/ReviewCard.tsx`.
- Added durable conversation document session persistence (`chat_document_sessions.json`) in `rust/lattice-chat/src/{documents.rs,state.rs}` and static crate/domain layering enforcement (`rust/lattice-platform/tests/domain_boundaries.rs`).

### Changed
- Promoted canonical `OnceLock`-compiled secret redaction to `rust/lattice-core/src/redact.rs`, extracted `rust/lattice-agent/src/seam/worker.rs` so `kernel/` and `tools/` never import `surface/`, severed `modelops` → `adminops` coupling via `http_reply.rs`, and migrated `lattice-chat` to canonical `lattice_agent::{kernel,parse,content,tools,seam}` paths.
- Collapsed `review_items` mutations and `timeline` appends into a single atomic `WorkspaceOsStore` transaction (`update_state_with_review_event`, `try_update_state_with_review_event`), unified `lattice-auth::messages` onto `lattice_core::messages` (`check_server_i18n.mjs` verifying 146 Rust messages), and split `frontend/src/api/client.ts` (`streaming.ts`, `transfers.ts`) and `src-tauri/src/backend.rs` (`backend_tests.rs`).
