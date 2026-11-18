# Changelog

The top entry is either the current unreleased main-branch work or the current
release line. Older entries are historical and may describe behavior as it
existed at that release.

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
- 크레이트 도메인 구조. `lattice-agent` 43파일이 여섯 그룹으로
  (`kernel` / `parse` / `content` / `tools` / `surface` / `prompts`),
  `lattice-platform` 31개 평면 모듈이 일곱 도메인으로
  (`workspaceos` / `toolsurface` / `governance` / `adminops` /
  `knowledge` / `modelops` / `shell`, 100건 `git mv`). 전부 이동이며
  동작 변화는 0 — 골든과 계약 테스트가 재구조 전후로 같은 답을 낸다.
- 크레이트 로컬 지도 2종: `rust/lattice-agent/ARCHITECTURE.md`,
  `rust/lattice-platform/ARCHITECTURE.md`. 각 그룹 `mod.rs`가 무엇이
  속하고 무엇이 절대 들어가면 안 되는지와 불변식을 적는다. 각
  `src/lib.rs`는 호환 맵으로 끝나 기존 임포트 경로가 전부 해석된다.
- `docs/ROADMAP.md` — 우선순위(P1/P2/P3)·난이도·제안 경로가 있는 갭
  로드맵. 이번 릴리스에서 닫힌 항목은 닫힘으로 표시한다.
- GUIDED 프로파일. 번호 메뉴로 액션을 고르고 인자를 한 개씩 받아
  **하네스가 액션 구조체를 조립**한다 — JSON 요구 자체가 없다. 꼬리는
  모든 모드와 같은 `perform_action`이므로 게이트 체인·루프 가드·쓰기 전
  스냅샷·`sanitize_write_content`가 동일하다. 검증은 닫힌 PASS/FAIL +
  이유 한 줄이며 같은 근거·커버리지 게이트로 심사한다.
- 측정 기반 프로파일 프로브(`kernel/probe.rs`). 로드 시 고정 질문 두
  개를 루프 자신의 파서로 채점해 standard/compact/guided를 고르고,
  모델 id + 크레이트 버전으로 캐시한다. `LATTICEAI_AGENT_PROFILE`이
  여전히 최우선, `LATTICEAI_AGENT_PROBE=0`으로 끌 수 있다.
- 미드런 자기강등(`demote_to_guided`) — 측정된 다이얼, 아직 guided가
  아님, 실행 근거 없음의 세 조건에서만, 아래로만.
- 통합 도구 카탈로그(`tools/catalog.rs`) — native + `mcp.<tool>` +
  `skill.<name>`이 하나의 번호 메뉴. 런이 거버넌스하는 이름은 네이티브
  경로(더 엄격), 아닌 이름은 `POST /mcp`와 같은 거버넌스 검사를 지난다.
- `POST /worker/vector/query` — HNSW 사이드카 후보 조회. 워커 allowlist
  19 → **20**.
- `POST /api/ingestion/folder/prune` — 삭제된 파일 정리. `confirm` 없으면
  dry-run, 있으면 `delete_document_tree`(양방향 엣지). SPA에 「삭제된
  파일 정리 (N)」 카드 버튼.
- 섹션 트리 — `Document ←PART_OF— Section ←PART_OF— Section`,
  `Section —HAS_CHUNK→ Chunk`. 제목이 없는 문서는 섹션을 만들지 않는다.
- 파일별 ingest 핑거프린트(경로 + 크기 + mtime + 내용 sha256)를
  `ingestion_provenance.metadata_json`에 저장. 두 번째 테이블 없음.
- 스토어 세대(generation) 에폭 — 복원이 인프로세스로 즉시 반영된다.
- `ltcai[pointer]` extra — 여섯 pyautogui 포인터 도구의 선언된 설치 경로.

### Changed
- `POST /mcp`가 OpenAPI 제품 계약 **안으로** 들어왔다(단일 JSON-RPC 봉투
  오퍼레이션). 네이티브 마운트이므로 워커로 프록시되지 않는다. 문은 네이티브
  **422 오퍼레이션 / 41 패밀리**(11.9.0의 420 + `/mcp` + folder prune).
- `/setup/install`이 실제로 설치한다 — 요청이 이름을 댄 항목이 **서버가
  도출한** allowlist에 있을 때만. 목록 밖 이름은 거절. 기본은 수동 유지.
- 한국어 조사 스트리핑 2단계 + 근거 게이트 + 포함관계 중복 제거.
- 추출이 `PART_OF`/`CONTRADICTS`를 실제로 생산하고, 엣지는 방향과 타입을
  갖고 쓰이며, 근거는 분류되어 저장된다.
- 임베딩 자동 감지 — 다운로드된 실모델이 있으면 채택, 해시는 폴백으로
  표기. 벡터 정체성은 `(model, dim)`으로 필터.
- 임베딩이 쓰기 트랜잭션 **앞으로** 이동(드레인 ~66 → ~1,300 items/s),
  드레인 스케줄러 적응형(백로그 991건 40분 → 15.3초).
- 무변경 재인덱스 33s → **0.26s**(낭비율 1.00 → 0.00), 첫 인덱싱 25.8s
  → 7.2s. vault-watch가 바뀌지 않은 노트를 스킵.
- `LATTICEAI_VECTOR_INDEX=hnsw`가 실제 검색 경로 — 사이드카가 `k*8`
  후보(최대 200)를 주고 Rust가 브루트와 같은 코사인으로 정확 재스코어,
  이름은 `hnsw+rescore`. HNSW 사이드카는 증분 append. **기본은 여전히
  `brute`**, 실패는 사유를 실은 폴백.
- `Completion::prefix`가 완성의 첫 글자를 강제(compact는
  `{"thoughts": "`). 토큰 단위 JSON 문법은 검토 후 채택하지 않음.
- 프론트: ErrorBoundary가 라우트와 헤비 패널 전면에, Act/Brain 서브라우트
  lazy 분할, 권한 모드 diff 프리뷰와 위험 토글 ack. 번들 104.2 / 150 KiB.

### Fixed
- RAG 인용 지시가 에이전트 프롬프트로 새던 문제 — 도구를 불러야 할 턴에
  모델이 출처 표기를 하고 있었다.
- 모델 로딩의 이름 로스터 게이트 — Qwen AWQ 빌드가 로드 자체가 불가능
  했다.
- 베낄 수 있는 워크드 예제 — `write_file` content가 우리 예제 상수와
  글자 그대로 같으면 `COPIED_EXAMPLE`로 기록하고 쓰지 않는다
  (fail-closed). 에코된 지시문 줄은 guided 답변에서 제거.
- `<|channel>` 한-파이프 제어 프레임이 본문으로 새던 문제.
- 복원 뒤 오래된 커넥션이 복원 전 바이트를 주던 문제(에폭으로 해소).

### Known issues
- 작은 모델의 *내용 품질*은 여전히 fail-closed 게이트가 잡는다. 기계적
  실행(요청한 파일 쓰기, DONE 도달)은 guided로 안정됐고 0.5B도 3.9초에
  실파일로 완주하지만, 요약의 질은 critic에서 떨어져
  FAILED/NEEDS_REVIEW로 끝날 수 있다. 매트릭스는 18개 중 11개 통과에서
  개선 중이며 최종 수치는 릴리스 시점 `MATRIX_TABLE` 기준.
- `api_key` 클라우드 경로는 모의 서버만 검증(과금 0 정책). 라이브
  검증은 `cli_oauth`.
- DMG는 ad-hoc 서명(=미서명).
- 검색 기본은 brute. `hnsw`는 opt-in.
- watch는 자동 삭제하지 않는다 — 정리는 명시적 prune으로만.
- 크레이트 재구조는 이동이지 결합 해소가 아니다. 도메인 선을 넘는 네
  결합은 `lattice-platform`의 해당 `mod.rs`에 이름과 함께 적혀 있다.
- `GraphWriter::delete_node`는 여전히 `PART_OF`를 남긴다. prune은 그것을
  쓰지 않고 `delete_document_tree`를 쓴다.
