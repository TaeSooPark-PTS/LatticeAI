[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./CHANGELOG.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](./ko/CHANGELOG.md)

# Changelog

The top entry is either the current unreleased main-branch work or the current
release line. Older entries are historical and may describe behavior as it
existed at that release.

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

## [12.10.0] - 2026-11-17 — Grounded Self

### Added
- Added deterministic agent transcript grounding (`rust/lattice-agent/src/kernel/transcript/grounding.rs`, `agentloop/verification.rs`, `probe.rs`) catching unread target-file answers and hallucinated `path:line` citations or backticked identifiers on 2B/8GB-tier local models without per-model branches, plus interactive citation proof badges in `frontend/src/features/brain/AnswerProof.tsx` and browser-extension server language sync (`browser-extension/popup.js`).
- Added Brain `SelfModelPanel` (`frontend/src/features/brain/{SelfModelPanel.tsx,SelfModelPanel.test.tsx}`, `frontend/src/api/{selfModel.ts,selfModel.test.ts}`, `rust/lattice-retrieval/src/memory_api/self_model_write.rs`) with 100% Vitest coverage for preferences, habits, decisions, traits, relationships, and inline `self_model_fact` proposal approval/dismissal, plus `PART_OF` edge cascade verification across `edges` and `edges_v2` (`rust/lattice-core/tests/graph_write_schema.rs`) and expanded `cloud_lane.rs` / `rag_eval.rs` suites.
- Added cooperative `499` model download cancellation and automatic post-download semantic embedding activation + adaptive batch reindex drain (`latticeai/services/{model_runtime/download.py,model_loading.py,embedding_recommendation.py}`, `rust/lattice-jobs/src/index_api/engine.rs`, `tests/unit/test_download_cancel_and_embedding_transition.py`).

### Changed
- Extracted crate-internal `rust/lattice-platform/src/http_reply.rs` so `knowledge/` and `toolsurface/` never reach sideways into `workspaceos::project_sessions`, locked all 6 Python worker routers in `scripts/check_server_i18n.mjs` (`0 not yet claimed`), added parallel `cargo nextest` runner (`scripts/run_rust_tests.mjs`), and chained `.github/workflows/publish.yml` (`workflow_call`) from `.github/workflows/release.yml`.

## [12.9.0] - 2026-11-17 — Semantic Guard

### Added
- Added contract-aligned semantic query embedding (`rust/lattice-retrieval/src/query_embed.rs`, `tests/semantic_query_embed.rs`) across `/api/search/hybrid`, `/memory/recall`, and `/chat`, plus pure-Rust `NativeHnswIndex` (`native_hnsw.rs`, `vector_hnsw.rs`), workspace/model-scoped Python HNSW sidecars (`latticeai/core/vector_index/{hnsw,sidecar}.py`, `tests/unit/test_vector_index_sidecar.py`), and `scripts/bench_retrieval_baseline.py`.
- Added server-enforced network boundary & cloud slice guards (`rust/lattice-platform/src/knowledge/network_boundary_guard.rs`, `rust/lattice-chat/src/{boundary.rs,cloud/context.rs}`, `toolsurface/agent_registry.rs`) enforcing `local_only` / `cloud_allowed` consent and sensitivity rules.
- Added authenticated Workspace OS realtime SSE streaming (`rust/lattice-platform/src/workspaceos/realtime.rs`, `frontend/src/hooks/{useRealtimeStream.ts,useRealtimeStream.test.tsx}`) and extracted `frontend/src/hooks/useAppBootstrap.ts` at 100% Vitest coverage.

### Changed
- Hardened Tauri desktop backend (`src-tauri/src/backend.rs`), `bin/ltcai.js` CLI launch & host resolution, `rust/lattice-host` supervisor & gateway guardrails, multi-stage `Dockerfile`, `.latticebrain` v2 archive KDF (`portability/{crypto.rs,kdf.rs}`), and CI / Sidecar E2E / Dependency Audit workflows, and updated `AGENTS.md` for autonomous tag/release/CD execution after green `main` CI.

## [12.8.0] - 2026-11-16 — Trusted Brain

### Added
- Added canonical `EmbeddingIndexContract` (`rust/lattice-core/src/embedding_contract.rs`), granular `IngestStatus`, `KnowledgeLifecycleState`/`KnowledgeLifecycleMetadata` (`lifecycle.rs`), and unified `GraphWriter` deletion cascades across Rust core and Python embedding workers.
- Added SQLite workspace/lifecycle pre-`LIMIT` filtering, `vector_search_aligned` (`rust/lattice-retrieval/src/vector_alignment.rs`), bounded `SidecarCachePool` (`DEFAULT_MAX_CACHE_BYTES`) with subword `Bm25SidecarIndex` caching, and `rag_eval.rs` retrieval gates.
- Added server-derived fail-closed isolation (`IsolatedRootSpec`, `resolve_agent_scope`, `IsolationState`, `AgentBindState`) across `/agent`, `/tools/*`, `/mcp/*`, `/skills/*`, `/cu/*`, `/api/automation/*`, and `/api/browser/*` with `agent_isolation_e2e` and enterprise audit coverage.
- Added AEAD-encrypted `.latticebrain` archive portability (`archive.rs`, `crypto.rs`) with path/symlink traversal rejection, X25519 share export/accept governance, pre-restore backup, and honest `planned` PostgreSQL reporting (`postgres.rs`).
- Added automated PR retrieval/sidecar gates in `.github/workflows/ci.yml` and Apple Silicon Tauri DMG build, codesign/notarization hooks, and `SHA256SUMS` publishing in `.github/workflows/{release,publish}.yml`.

### Changed
- Refactored `Library.tsx`, `System.tsx`, and `Capture.tsx` into modular slices under `frontend/src/features/` while maintaining 100% Vitest coverage across 108 files, and removed obsolete `scratchpad/` files.

## [12.7.0] - 2026-11-16 — Fast Index

### Added
- Added SQLite `bm25_node_stats` sidecar persistence in `rust/lattice-core/src/bm25_stats.rs`, `db/tables.rs`, and `graph_write/{mod,primitives,documents,curation,provenance}.rs` so `GraphWriter` persists pre-tokenized field lengths (`dl_title`, `dl_tags`, `dl_body`, `dl_meta`) and term frequencies (`tf_json`) on upserts/sensitivity updates and cleans them up on deletes, curation, and replace-imports while safely skipping missing tables on read-only/frozen golden databases.
- Added `Bm25SidecarIndex` and inverted posting-list / GraphRAG adjacency caching (`rust/lattice-retrieval/src/bm25/{sidecar,sidecar_cache,scoring,graph_rag,fusion,tokenizer,mod,tests}.rs`, `service_hybrid.rs`) with incremental `(node_id, updated_at, content_hash)` sync, O(1) field-length & term-frequency lookup, `expand_knowledge_graph_with_sidecar`, and `score_and_expand_with_sidecar`.

### Changed
- Redesigned `README.md` into a product-first overview with quick start, product tour, documentation hub, honest boundaries, and `v12.0.0+` release history.
- Pruned pre-v12 release notes (`v9.*`–`v11.*`) and pre-v12 review/plan snapshots locally and on GitHub Releases, and updated `scripts/check_current_release_docs.mjs` and `tests/unit/test_server_app_decomposition.py` to enforce the `v12.0.0+` release-history floor.

## [12.6.0] - 2026-11-15 — Hybrid Graph

### Added
- Implemented 3-way **BM25F + Vector + Knowledge Graph RRF Hybrid Indexing & GraphRAG Retrieval** in `rust/lattice-retrieval/src/bm25/`, `service_hybrid.rs`, `hybrid.rs`, and `search_api/handlers.rs` with calibrated Okapi BM25F (`k1=1.2, b=0.75`), Korean particle stripping, CJK character bigrams, 1-hop/2-hop Knowledge Graph structural expansion (`RELATES_TO`, `MENTIONS`, `PART_OF`, shared concept nodes, `[[wikilinks]]`, `#tags`), and automatic zero-hit rescue fallback on `/api/search/hybrid`.
- Upgraded graph-aware agent skills and MCP tools (`knowledge_search`, `obsidian_search`, `knowledge_graph_search`, `knowledge_graph_context`, `GRAPH_SKILL_BRIEFS`) in `rust/lattice-platform/src/toolsurface/tools/knowledge/` and `rust/lattice-agent` with `mode`, `hops`, `include_graph`, and extracted `subgraph` payloads while preserving the frozen 422 operations / 41 families contract.
- Added unauthenticated public mirror and quantized GGUF/MLX/ONNX fallback support (`mlx-community/embeddinggemma-2-4bit`, `mlx-community/embeddinggemma-2-8bit`, `ggml-org/embeddinggemma-2-GGUF`, `onnx-community/embeddinggemma-300m-ONNX`) plus `HF_ENDPOINT` custom mirror resolution in `latticeai/services/embedding_recommendation.py`, `model_runtime/download.py`, `model_loading.py`, and `rust/lattice-platform/src/modelops/models_catalog/embedding.rs`.
- Added real-time SSE download progress streaming (`downloaded_bytes`, `total_bytes`, `percent`, `speed_bps`, `speed_mbps`, `eta_seconds`, `current_file`, `gated_fallback`) across Python download runtime, Rust `modelops`, and Frontend (`ProductFlow.tsx`, `InstallScreen.tsx`, `Library.tsx`, `ko`/`en` i18n) with 100% Vitest coverage.
- Added `scripts/check_ubuntu_runner_compat.mjs` and Debian/Ubuntu `t64` transition package fallback chains (`libasound2t64 || libasound2`, `libatk1.0-0t64 || libatk1.0-0`, `libatk-bridge2.0-0t64 || libatk-bridge2.0-0`, `libcups2t64 || libcups2`, `libgtk-3-0t64 || libgtk-3-0`, `libwebkit2gtk-4.1-0 || webkit2gtk-4.1`) across `.github/workflows/*.yml`.
- Refactored `scripts/bump_version.py` into the modular `scripts/bump_version/` CLI package (`__init__`, `targets`, `openapi`, `cargo`, `markers`, `cli`) with `all`, `openapi`, `cargo`, and `markers` subcommands and `--check` support.

### Changed
- Upgraded offline zero-download `FallbackTextEmbedder` (`768d`, `lattice-fallback-subword-v2:768`) in `latticeai/core/embedding_providers/text.py` and `rust/lattice-core/src/embeddings.rs` to use multi-scale `3..5`-gram subwords, CJK n-grams, Korean particle stripping & Jamo lead pairs, word bigrams, 2-probe signed Blake2b projection, and L2 normalization.
- Removed the legacy Pillow `<=12` `diff.getdata()` fallback in `scripts/check_screenshot_pixel_delta.py` in favor of Pillow 13+'s `diff.get_flattened_data()`.

## [12.5.0] - 2026-11-11 — Deep Vector

### Added
- Added native Hugging Face embedding provider for Google EmbeddingGemma 2 (`google/embeddinggemma-2`, `embeddinggemma-2`, `embeddinggemma-2-4bit`, `embeddinggemma-2-8bit`, `embeddinggemma-300m`, 768-dim) with mean-pooling, L2 normalization, task prefixes, and fallback protection in `latticeai/core/embedding_providers/text.py`.
- Added hardware-aware PC-tailored embedding recommendations (`recommend_embedding_for_host`) in `rust/lattice-platform/src/modelops/models_catalog/embedding.rs` and `latticeai/services/embedding_recommendation.py` with memory reservation (0.5GB–1.5GB for Gemma 2, 0.3GB for 300M) before LLM selection.
- Added PC-tailored EmbeddingGemma 2 recommendation card in `ProductFlow.tsx` (i18n supported) and surfaced Google EmbeddingGemma 2 in `Library.tsx` with 1-click prepare/download.
- Added comprehensive visual E2E test suite `tests/visual/v3.all_buttons_inspection.spec.js` asserting all routes, controls, tabs, and buttons operate with 0 errors.

### Changed
- Migrated `scripts/check_screenshot_pixel_delta.py` from Pillow 14-deprecated `diff.getdata()` to `diff.get_flattened_data()`.
- Upgraded all GitHub Actions workflows (`.github/workflows/*.yml`) to Node 24 native major versions with `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: "true"`.
- Automated OpenAPI specification and fixture version synchronization across 49 targets in `scripts/bump_version.py`.

## [12.4.0] - 2026-11-11 — Sharp Weight

### Added
- Added `mlx-community/Qwen3.8-27B-4bit` (`16.1GB`, `qwen3_5`) to the local
  model capability registry, multi-engine GGUF/HF routes
  (`ggml-org/Qwen3.8-27B-GGUF:Q4_K_M`, `Qwen/Qwen3.8-27B`), `together` and
  `openrouter` cloud catalogs, and Rust `CATALOG_HINTS`.
- Added `mlx-community/gemma-4-26B-A4B-it-qat-4bit` (`15.6GB`, routed to
  Google's official QAT GGUF `google/gemma-4-26B-A4B-it-qat-q4_0-gguf:Q4_0` and
  `google/gemma-4-26B-A4B-it-qat-q4_0-unquantized`) and
  `mlx-community/gemma-4-26B-A4B-it-qat-OptiQ-4bit` (`21.9GB`, sensitivity-guided
  mixed 4-bit/8-bit `mlx-optiq` build on Google's QAT base) to the capability
  registry, engine routes, and Rust `CATALOG_HINTS`.

### Changed
- Upgraded `pillow` dependency constraint to `>=13,<14` in `pyproject.toml` and
  `requirements.txt` (closing 39 CVEs in `pillow 12.3.0`), and synchronized
  `reportlab>=4,<5` and `huggingface_hub>=1.0,<2` in `requirements.txt`.
- Updated npm dependencies and lockfiles in root and `vscode-extension`
  (`vitest` and `@vitest/coverage-v8` `^4.1.12`, `glob` `^14.0.4`, `minimatch`
  `^11.0.7`) so `npm audit` reports 0 vulnerabilities.

### Fixed
- Hardened `_json_request` and `get_openai_compatible_server_models` in
  `latticeai/services/model_engines.py` to reject non-`http(s)://` URL schemes.
- Fixed `admin_log_retention` in `rust/lattice-platform/src/adminops/admin/handlers.rs`
  to read `state.auth.clock().now()` instead of real wall-clock `unix_now()`,
  eliminating wall-clock drift against deterministic audit fixtures.
- Scoped `@import "tailwindcss" source("..");` in `frontend/src/styles.css` so
  `@tailwindcss/oxide` only scans `frontend/` instead of walking `rust/target`.

## [12.3.0] - 2026-10-19 — Live Wire

### Added
- Full HTML structure parser in `POST /tools/inspect_html`: extracts `title`,
  `links`, `scripts`, `stylesheets`, `images`, `forms`, and `headings`
  (`h1`–`h6`) with `MAX_FILE_BYTES` enforcement.
- RFC 5987 `filename*=UTF-8''...` `Content-Disposition` attachment headers and
  UTF-8 percent-decoding in `GET /tools/download`.
- Verified GGUF routing for `mlx-community/Qwen3.5-9B-MLX-4bit`
  (`unsloth/Qwen3.5-9B-GGUF`) and `mlx-community/Qwen3.6-35B-A3B-4bit`
  (`ggml-org/Qwen3.6-35B-A3B-GGUF`) across `ollama`, `lmstudio`, and `llamacpp`.
- Companion `mmproj-*.gguf` download and `--mmproj` flag in
  `ensure_llamacpp_server`, plus `is_auxiliary_gguf` filtering (`mmproj-`,
  `mtp-`, `dflash-`, `eagle3-`, `imatrix`) and `_shards_complete` verification
  for `model.safetensors.index.json`.
- Small-model tool/MCP calling tolerances in `lattice-agent`: expanded
  `KNOWN_ACTIONS`, stringified `arguments` recovery (trailing comma, truncation,
  Python dict literal), OpenAI `tool_calls` array unwrapping, fenced HTML/code
  block salvage for `write_file`/`local_write`, and `adopt_named_args` in
  `perform_action`.

### Changed
- `_gguf_routes` now accepts per-repository `ollama_tag` (`Q4_0` for
  `ggml-org/gemma-4-*-GGUF`, `MXFP4` for `ggml-org/gpt-oss-20b-GGUF`).
- Cloud and local provider defaults updated to current models (`grok-4.6`,
  `Qwen/Qwen3.6-27B`, `qwen/qwen3.6-35b-a3b`, `x-ai/grok-4.6`,
  `hf.co/ggml-org/gemma-4-12B-it-GGUF:Q4_0`, `google/gemma-4-12B-it`).

### Fixed
- Removed nonexistent `mlx-community/bge-m3` from `LOCAL_MLX_MODELS` while
  retaining `bge-m3` in `OLLAMA_EMBEDDING_MODELS`.
- Fixed Latin-1 byte-to-char corruption on non-ASCII percent-encoded paths in
  `/tools/download`.

## [12.2.1] - 2026-08-29 — True Count

### Added
- HNSW sidecar COUNT+delta load: a warm query never dumps `vector_embeddings`;
  growth fetches missing ids only and appends.
- Skip-by-hash restamps size/mtime on the existing provenance row via
  `GraphWriter`.
- Watch prune: a vanished watched file is removed from the graph
  (`delete_document_tree`, confirm=true). Disk is never deleted.
- `complete_a_summary`: a thin "I summarized" answer is filled from the
  `read_file` / `mcp.read_file` result already on the transcript.
- Compact catalog CORE now keeps `mcp.grep`, `mcp.list_dir`, `mcp.read_file`,
  `mcp.knowledge_search` through the nine-row cap.
- `api_key` cloud `GET /models` probe (no completion). Unreachable keys
  fail-close status.
- First-run and library copy name hash search as hash, with a path to a
  meaning model.

### Changed
- Per-version `RELEASE_NOTES_v*.md` files live in `docs/releases/`.
- README is user-first: install, first five minutes, then the release.

### Fixed
- Loaded HNSW graphs can append a delta instead of rebuilding from the
  incoming set alone (which wiped live labels).

## [12.2.0] - 2026-08-29 — Small Voice

### Added
- Function-calling envelope parse: `name`/`arguments`, nested `function`,
  Qwen `<function=…><parameter=…>`, `Action: mcp.grep`.
- Compact executor lists cap at the guided menu size (9). Skills whose
  `when` line matches the request are consulted without naming `skill.`.
- A quoted search term fills a missing MCP/native `pattern`/`query`.
- Home composer chips for folder search, read-and-summarize, memory, and
  skill review. Settings card states the same. MCP/skill timeline rows
  get a plain-language prefix.

### Changed
- Compact prompt names the function-calling envelope 2B models emit.

### Fixed
- Labeled salvage refused dotted `mcp.` / `skill.` names as chatter.

## [12.1.0] - 2026-08-29 — Fast Path

### Added
- Folder ingest and vault-watch overlap up to four files (`INGEST_INFLIGHT`).
  Stamp-skip never enters the pool.
- One `POST /worker/embed` per note: document text then every chunk.
  Extract runs beside embed, not in front of it.
- Default brute search tries `hnsw+rescore` when the store has 512+ vectors
  and a worker sidecar is bound; failure keeps the original `brute` index
  block.

### Changed
- `rebuild_vector_index` embeds outside the write transaction.
- `write_vectors_with` files the ingest-time supplied vectors so the hash
  model does not re-derive them.
- Empty `ltcai[postgres]` extra alias; the live Postgres writer left in 11.6.0.

### Fixed
- Weekly pip-audit failed on a missing markdown report when there were no
  findings.
- Weekly Postgres integration job failed because
  `tests/integration/test_postgres_migration_live.py` was gone.

## [12.0.0] - 2026-08-18 — Open House

### Added
- Crate domain structure: grouped 43 files in `lattice-agent` into six purpose-based groups (`kernel` / `parse` / `content` / `tools` / `surface` / `prompts`), and reorganized 31 flat modules in `lattice-platform` into seven domains (`workspaceos` / `toolsurface` / `governance` / `adminops` / `knowledge` / `modelops` / `shell`, 100 `git mv` operations). All moves are pure renames with zero behavior change — golden and contract tests return identical results before and after restructuring.
- Two crate-local architecture maps: `rust/lattice-agent/ARCHITECTURE.md` and `rust/lattice-platform/ARCHITECTURE.md`. Each group's `mod.rs` documents what belongs inside, what must never enter, and its invariants. Each `src/lib.rs` ends with a compatibility map so all existing import paths continue to resolve.
- `docs/ROADMAP.md` — a prioritized gap roadmap with priority levels (P1/P2/P3), effort estimates, and proposed paths; items resolved in this release are explicitly marked closed.
- `GUIDED` profile: selects actions from a numbered menu and collects arguments one per turn so **the harness assembles the action struct** — removing JSON formatting requirements entirely. The execution tail runs the same `perform_action` as every other mode, preserving the gate chain, loop guard, pre-write snapshot, and `sanitize_write_content`. Verification uses a closed `PASS`/`FAIL` plus one-line reason judged by the same evidence and coverage gates.
- Measurement-based profile probe (`kernel/probe.rs`): scores two fixed prompts at load time using the loop's own parser to select `standard` / `compact` / `guided`, caching the verdict per model ID and crate version. `LATTICEAI_AGENT_PROFILE` remains highest priority, and `LATTICEAI_AGENT_PROBE=0` disables probing.
- Mid-run downward self-demotion (`demote_to_guided`): demotes downward only when three conditions hold — the dial was measured, it is not already `guided`, and no execution evidence exists yet.
- Unified tool catalog (`tools/catalog.rs`): merges native tools, `mcp.<tool>`, and `skill.<name>` into a single numbered menu. Names governed by the run take the stricter native path; ungoverned names pass through the same governance check as `POST /mcp`.
- `POST /worker/vector/query` — HNSW sidecar candidate lookup, expanding the worker allowlist from 19 to **20** routes.
- `POST /api/ingestion/folder/prune` — deleted-file cleanup. Without `confirm` it runs a dry-run; with `confirm` it deletes document subtrees via `delete_document_tree` (clearing bidirectional edges). Surfaced in the SPA as a "Clean up deleted files (N)" card button.
- Section tree — `Document ←PART_OF— Section ←PART_OF— Section` and `Section —HAS_CHUNK→ Chunk`. Documents without headings do not fabricate synthetic section nodes.
- Per-file ingest fingerprint (path + size + mtime + content SHA-256) stored in `ingestion_provenance.metadata_json` without introducing a second registry table.
- Store generation epoch — database restores take effect live in-process without restarting the host.
- `ltcai[pointer]` extra — declared installation path for the six `pyautogui` pointer tools.

### Changed
- Brought `POST /mcp` **inside** the OpenAPI product contract as a single JSON-RPC envelope operation. Because it is natively mounted, it is never proxied to the worker. The native door now exposes **422 operations across 41 families** (11.9.0's 420 plus `/mcp` and folder prune).
- `/setup/install` now performs real installations only when the requested item appears on a **server-derived** allowlist; items outside the allowlist are rejected, and manual setup remains the default.
- Added two-stage Korean particle (josa) stripping, an evidence gate, and containment deduplication for hybrid recall.
- Extraction now actively produces `PART_OF` and `CONTRADICTS` relations; edges are written directed and typed with classified evidence provenance.
- Embedding auto-detection adopts a downloaded real embedding model when present and explicitly labels hash embeddings as fallback; vector identity filters by `(model, dim)`.
- Moved embedding generation **ahead of** the write transaction (increasing drain throughput from ~66 to ~1,300 items/s) and made the drain scheduler adaptive (draining a 991-item backlog in 15.3 seconds instead of ~40 minutes).
- Reduced unchanged folder re-indexing from 33s to **0.26s** (waste ratio 1.00 → 0.00) and initial indexing from 25.8s to 7.2s; vault-watch now skips unchanged notes.
- Made `LATTICEAI_VECTOR_INDEX=hnsw` a real search path: the sidecar returns `k * 8` candidates (capped at 200) and Rust rescores them with the exact cosine used by `brute` (`hnsw+rescore`). The HNSW sidecar supports incremental append; **`brute` remains the default**, and failures fall back with an explicit reason.
- Added `Completion::prefix` to force opening completion bytes (`{"thoughts": "` under `compact`). Token-level JSON grammar constraints were evaluated and not adopted.
- Frontend: added `ErrorBoundary` across all routes and heavy panels, lazy-split Act and Brain sub-routes, and added permission-mode diff previews with risky toggle acknowledgements. Initial JS bundle size: 104.2 / 150 KiB gzip.

### Fixed
- Fixed RAG citation instructions leaking into the agent prompt, which caused models to emit citations on turns where they needed to invoke a tool.
- Fixed the name-roster gate in model loading that blocked Qwen AWQ builds from loading altogether.
- Fixed copyable worked examples: if `write_file` content verbatim-matches the built-in example constant, it is recorded as `COPIED_EXAMPLE` and never written (fail-closed); echoed instruction lines are stripped from guided answers.
- Fixed single-pipe `<|channel>` control frames leaking into generated output bodies.
- Fixed stale pooled SQLite connections returning pre-restore bytes after a restore (resolved via the store generation epoch).

### Known issues
- Small-model *content quality* remains guarded by fail-closed gates. Mechanical execution (writing the requested file and reaching `DONE`) is stabilized under `guided` — even a 0.5B model completes with a real file in 3.9s — while thin summaries can still be rejected by the critic as `FAILED` / `NEEDS_REVIEW`. The cross-model matrix is improving from 11 of 18 passing (refer to the release-time `MATRIX_TABLE` for final counts).
- The `api_key` cloud path was verified against a mock server only (zero-billing policy); live verification covered `cli_oauth`.
- The macOS DMG is ad-hoc signed (unsigned by Developer ID) when signing secrets are absent.
- Default vector search remains `brute`; `hnsw` is opt-in.
- Watch mode does not auto-delete; cleanup is performed only through the explicit prune endpoint.
- Crate restructuring is a module move, not a full decoupling; the four cross-domain couplings in `lattice-platform` are explicitly documented in their respective `mod.rs` files.
- `GraphWriter::delete_node` still leaves `PART_OF` edges intact; folder prune uses `delete_document_tree` instead.
