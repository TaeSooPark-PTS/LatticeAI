[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./RELEASE.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](./docs/ko/RELEASE.md)

# Lattice AI Release Guide

This document is the single checklist and canonical release ledger for building, verifying, and publishing Lattice AI across `npm`, `PyPI`, `VS Code Marketplace`, `Cursor`, `Antigravity`, `Open VSX`, and macOS Apple Silicon DMG releases.

> **CI-gated automatic Release → CD (`.github/workflows/release.yml` → `.github/workflows/publish.yml`)**:
> `.github/workflows/release.yml` triggers automatically when `CI` (`ci.yml`) completes with `success` on `main` (and also supports manual tag pushes or `workflow_dispatch`). It hard-enforces a green `CI` run on the exact commit SHA, validates `scripts/bump_version.py <VERSION> --check`, `scripts/check_current_release_docs.mjs`, and `scripts/check_doc_languages.mjs`, auto-tags any unreleased `package.json` version (skipping ordinary already-tagged commits or `[skip release]`/`[no release]` commits), builds and signs exact-version release artifacts, publishes the GitHub Release, and chains `.github/workflows/publish.yml` (CD) to publish packages to npm, PyPI, VS Code Marketplace, and Open VSX. CD safely skips versions that are already published (`already-published`), so re-running it is idempotent.

> **Exact-version artifact publishing only (never use wildcards)**:
> Manual fallback commands must always name exact-version files:
> - PyPI: `twine upload dist/ltcai-X.Y.Z-py3-none-any.whl dist/ltcai-X.Y.Z.tar.gz`
> - npm: `npm publish ltcai-X.Y.Z.tgz`
> - VS Code Marketplace: `vsce publish --packagePath dist/ltcai-X.Y.Z.vsix`
> - Open VSX: `ovsx publish dist/ltcai-X.Y.Z.vsix`
> - macOS DMG: `src-tauri/target/release/bundle/dmg/Lattice AI_X.Y.Z_aarch64.dmg`

> **Release evidence retention policy (`output/release/`)**: Versioned screenshot and walkthrough GIF evidence is kept for the latest 3 releases only. `npm run release:evidence` automatically runs `scripts/prune_release_evidence.mjs` after capture (`LTCAI_RELEASE_EVIDENCE_KEEP` configures retention), and historical evidence can be regenerated at any time by checking out the corresponding git tag.

## v13.4.2 — Living Synthesis (2026-11-24)

Added **oMLX (`jundot/omlx`)** engine integration with hardware-tuned tiered RAM/SSD KV caching (`--memory-guard balanced`, `--hot-cache-max-size`, `--paged-ssd-cache-dir`, `--max-concurrent-requests 2`, `--initial-cache-blocks 128`) and elevated **`mlx-community/Qwen3.8-27B-4bit`** to recommended & downloadable status on 32GB+ Apple Silicon Macs (`recommended_ram_gb=32.0`).

Release notes: [docs/releases/RELEASE_NOTES_v13.4.2.md](docs/releases/RELEASE_NOTES_v13.4.2.md).

Build artifacts: `dist/ltcai-13.4.2-py3-none-any.whl`, `dist/ltcai-13.4.2.tar.gz`, `ltcai-13.4.2.tgz`, `dist/ltcai-13.4.2.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_13.4.2_aarch64.dmg`.

## v13.4.1 — Living Synthesis (2026-11-23)

Dependency compatibility & maintenance release resolving Dependabot PRs `#58`, `#60`, and `#61` across the full stack:
- **Frontend (`npm` / TypeScript 7.0.2 / Vitest 5.1.1):** Added `typescript` override for `openapi-typescript@7.13.0` peer resolution, updated `tsconfig.json` for TS 7 (`paths` without `baseUrl`) and `frontend/src/vite-env.d.ts` (`vite/client` side-effect CSS types), and preserved 100% Vitest branch/line/function/statement coverage across all 111 test files.
- **Rust Workspace (`rust/` & `src-tauri/`):** Migrated to `sha2` 0.11 / `digest` 0.11 (`hex_lower` / `sha256_text`), `rusqlite` 0.43 (`i64` `ToSql` bindings), `fancy-regex` 0.20 (`Captures<'_, str>`), `axum` 0.8 (native async `FromRequestParts`), `scrypt` 0.12 (3-arg `Params::new`), `zip` 9 (`ZipFile<'_, R>`, `Result<Cow<'_, str>, ZipError>`, `SimpleFileOptions`), `reqwest` 0.13, and `tauri` 2.11.6.

Release notes: [docs/releases/RELEASE_NOTES_v13.4.1.md](docs/releases/RELEASE_NOTES_v13.4.1.md).

Build artifacts: `dist/ltcai-13.4.1-py3-none-any.whl`, `dist/ltcai-13.4.1.tar.gz`, `ltcai-13.4.1.tgz`, `dist/ltcai-13.4.1.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_13.4.1_aarch64.dmg`.

## v13.4.0 — Living Synthesis (2026-11-20)

Universal adaptive execution release uniting the **model-agnostic Universal Adaptive Agent Harness (`rust/lattice-agent/src/kernel/{probe.rs,agentloop/,transcript/,parse/}`) with zero per-model code branches**, **modular native web fetching & Computer Use bridge parity (`rust/lattice-agent/src/tools/web/`, `latticeai/tools/{web.py,computer.py}`)**, **a reproducible 25-task live empirical benchmark on `Qwen2.5-0.5B-Instruct-Q4_K_M` lifting overall pass rate from `28.0%` raw (`24.0%` standard loop) to `84.0%` (`+56.0 pp` / `+200.0%` relative gain)**, and **100% EN <-> KO bidirectional GitHub Markdown parity (`npm run docs:check-languages`)**.

- **Universal Adaptive Agent Harness:** Added 3-turn runtime micro-probing (`probe.rs`), observation-first tool ranking (`ranking.rs`) and bilingual domain classification (`domain.rs`), deterministic UI bounding-box center resolution (`ui_resolve.rs` -> `100.0%` Computer Use), malformed-JSON repair and ChatML completion prefill (`repair.rs`, `action_salvage_text.rs`), and active vs. superseded evidence separation (`rag_grounding.rs`, `summaries.rs` -> `100.0%` RAG Grounding).
- **25-task live benchmark & showcase report:** Orchestrated `rust/lattice-host/examples/harness_bench.rs` via `scripts/benchmark_adaptive_harness.py` against local `llama-server`, achieving **80.0% Tool Calling** (`+60.0 pp`), **80.0% Web Handling** (`+80.0 pp`), **100.0% Computer Use** (`+100.0 pp`), **60.0% Answer Quality** (`+40.0 pp` vs standard), and **100.0% RAG Grounding** (`+40.0 pp` vs raw / `+80.0 pp` vs standard), while reducing average task latency by **63.3%** vs standard loop.
- **100% bilingual documentation parity:** Completed pure-Korean mirrors across root, crate, GitHub, and benchmark docs (`docs/ko/**`) with verified bidirectional language switcher badges.

Release notes: [docs/releases/RELEASE_NOTES_v13.4.0.md](docs/releases/RELEASE_NOTES_v13.4.0.md). Benchmark: [docs/ADAPTIVE_HARNESS_BENCHMARK.md](docs/ADAPTIVE_HARNESS_BENCHMARK.md).

Build artifacts: `dist/ltcai-13.4.0-py3-none-any.whl`, `dist/ltcai-13.4.0.tar.gz`, `ltcai-13.4.0.tgz`, `dist/ltcai-13.4.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_13.4.0_aarch64.dmg`.

## v13.3.1 — Living Synthesis (2026-11-19)

Polish and completeness release uniting **two-phase async incremental chunk synchronization for semantic embedding contracts (`plan_incremental_sync` + async `/worker/embed` + `commit_incremental_sync`, `watch_deliver.rs`)**, **`AnswerProof.tsx` claim verification telemetry (`claim_verification`) and `BrainIntelligencePanel.tsx` entity resolution & temporal timeline summary cards (full Korean/English i18n at 100% Vitest coverage)**, and **weekly full-stack Dependabot monitoring (`.github/dependabot.yml`)**.

- **Two-phase async semantic incremental sync:** Split `incremental_sync.rs` into `plan_incremental_sync` and `commit_incremental_sync` and extracted `watch_deliver.rs`, so both local hash and semantic embedding contracts re-embed only modified chunks asynchronously via `/worker/embed` while reusing unchanged chunk vectors (verified end to end in `vault_watch_bridge.rs`).
- **Claim verification, entity resolution & temporal timeline UI:** Added verified-claim rate, citation accuracy, numeric/polarity mismatch, superseded-source citation, and abstention-recommendation badges to `AnswerProof.tsx`, and wired `entity_resolution` (scanned, clusters, candidates/`SAME_AS` links) and `temporal_timeline` (active, historical, not-yet-valid facts, conflict alerts) summary cards into `BrainIntelligencePanel.tsx` with complete Korean/English localization and 100% Vitest coverage.
- **Automated supply-chain monitoring:** Added `.github/dependabot.yml` for weekly dependency security checks across Python (`pip`), Node (`npm`, `vscode-extension`), Rust (`rust`, `src-tauri`), and GitHub Actions.

Release notes: [docs/releases/RELEASE_NOTES_v13.3.1.md](docs/releases/RELEASE_NOTES_v13.3.1.md).

Build artifacts: `dist/ltcai-13.3.1-py3-none-any.whl`, `dist/ltcai-13.3.1.tar.gz`, `ltcai-13.3.1.tgz`, `dist/ltcai-13.3.1.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_13.3.1_aarch64.dmg`.

## v13.3.0 — Living Synthesis (2026-11-19)

Production wiring and hardening release integrating **live production wiring of core v13.2.0 engines (`post_idempotent_compute`, `verify_grounding_and_abstention`, `entity_resolution` & `temporal`, `DebouncedWatchEngine` & `sync_document_incremental`, `DurableWorkflowStore`)**, **`worker_resilience.py` concurrency/cancellation/streaming-timeout hardening and `ingest.rs` temp-file collision & overlay-error exposure**, and **zero non-test `.unwrap()` calls across the Rust workspace, unification of 7 duplicated `unsafe libc` time blocks into `clock.rs`, and zero handwritten production files exceeding 800 lines across the stack (maintaining 100% Vitest coverage)**.

- **Core Brain & workflow engines wired to live paths:** Connected `WorkerSeamClient::post_idempotent_compute` (`/worker/{parse,extract,embed,render/*}` and single-attempt coalescing in `query_embed.rs`), `claim_verification` telemetry inside `assess_answer_grounding` (`helpers.rs`), `entity_resolution` & `temporal_timeline` inside `BrainIntelligenceService::consolidate` (`consistency.rs`), non-blocking `DebouncedWatchEngine` + `sync_document_incremental` (`watch_bridge.rs`), and atomic `DurableWorkflowStore` checkpoints (`executor.rs`) directly to live traffic paths.
- **Worker resilience & ingest safety hardening:** Added structured `504`/`503` responses for coalesced waiters in `worker_resilience.py`, included response-body streaming inside the unary timeout, released `_inflight` immediately on `CancelledError` with regression tests, and introduced atomic-counter temp filenames and `structure_overlay_warning` surfacing in `local_files_api/ingest.rs`.
- **Safety & modularization sweep:** Removed the final 9 non-test `.unwrap()` calls in `rust/lattice-platform/src/` (0 non-test `.unwrap()` workspace-wide), consolidated 7 duplicated `unsafe libc` local-time blocks into a single safe API in `rust/lattice-core/src/graph_write/clock.rs` (dropping unused `libc` from `lattice-retrieval`), and split `provider.rs`, `intents.rs`, `helpers.rs`, and `extraction.py` so zero handwritten production files exceed 800 lines.

Release notes: [docs/releases/RELEASE_NOTES_v13.3.0.md](docs/releases/RELEASE_NOTES_v13.3.0.md).

Build artifacts: `dist/ltcai-13.3.0-py3-none-any.whl`, `dist/ltcai-13.3.0.tar.gz`, `ltcai-13.3.0.tgz`, `dist/ltcai-13.3.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_13.3.0_aarch64.dmg`.

## v13.2.1 — Living Synthesis (2026-11-19)

Maintenance and architecture-cleanup release delivering **full-stack modularization refactoring (`lattice-auth`, `lattice-core`, `lattice-ingest`, `lattice-retrieval`, `lattice-agent`, `lattice-platform`, `lattice-host`, `model_capability_registry.py`, `worker_compute.py`, `brainData.ts`)**, **removal of unused dead code and retired legacy layers across Rust, Python, and Frontend (maintaining 100% Vitest coverage)**, and **Ubuntu `t64` visual-regression CI mirror failover hardening**.

- **Cross-crate Rust modularization:** Split large files into cohesive submodules across `lattice-agent` (`guided/suggestions.rs`, `proposals_store.rs`, `action_salvage.rs`, `catalog_args.rs`, `tests_governance.rs`), `lattice-platform` (`run_handlers.rs`, `probe_sysinfo.rs`, `handlers_orgs.rs`, `handlers_snapshots.rs`, `share_bundle.rs`, `network_boundary_stores.rs`, `executor_steps.rs`, `mcp_streamable.rs`), `lattice-host` (`gateway/tests.rs`, `gateway_source_contracts.rs`), and `lattice-auth`, `lattice-core`, `lattice-ingest`, and `lattice-retrieval`.
- **Python & Frontend modularization and dead-code removal:** Split `model_capability_registry.py` (`model_capability_types.py`, `model_capability_catalog.py`), `worker_compute.py` (`worker_compute_render.py`), and `brainData.ts` (`brainDataReaders.ts`, `brainDataReadiness.ts`), and removed unreferenced helpers and legacy orbital Brain layers (`BrainGraphLayer`, `BrainMemoryLayer`, `BrainRelationshipLayer`, `graphLayout`, etc.) while keeping 100% Vitest coverage.
- **Visual-regression CI resilience:** Consolidated Ubuntu `t64` package discovery into a single pass with a `15s` mirror timeout and automatic `mirrors.ubuntu.com` failover.

Release notes: [docs/releases/RELEASE_NOTES_v13.2.1.md](docs/releases/RELEASE_NOTES_v13.2.1.md).

Build artifacts: `dist/ltcai-13.2.1-py3-none-any.whl`, `dist/ltcai-13.2.1.tar.gz`, `ltcai-13.2.1.tgz`, `dist/ltcai-13.2.1.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_13.2.1_aarch64.dmg`.

## v13.2.0 — Living Synthesis (2026-11-19)

Roadmap completion release integrating **bi-temporal Knowledge Graph queries and non-destructive Korean/English entity resolution (`temporal.rs`, `entity_resolution.rs`)**, **claim-level grounding verification/abstention (`grounding.rs`) and separated model-agnostic RAG evaluation (`rag_eval_grounded.rs`)**, **OS-event debounced watching, chunk-level incremental re-embedding, and table/page/OCR structure-preserving ingestion (`incremental_watch.rs`, `incremental_sync.rs`, `document_structure.rs`)**, **a durable DAG agent workflow engine with atomic checkpoints, idempotency ledger, approval-across-restart recovery, and rollback (`kernel/workflow.rs`)**, and **worker idempotent retry/timeout isolation (`worker.rs`, `worker_resilience.py`), cloud secret-file blocking (`boundary.rs`), honest graph status probes (`onedoor.rs`, `audit.rs`), and the Section 16 end-to-end verification suite (`roadmap_section16_suite.rs`)**.

- **Bi-temporal knowledge evolution, entity resolution & grounded RAG:** `temporal.rs` and `entity_resolution.rs` provide point-in-time valid-time (`valid_from`..`valid_until`) and transaction-time queries alongside reversible `SAME_AS` Korean/English alias consolidation; `grounding.rs` and `rag_eval_grounded.rs` enforce claim-level numeric/polarity/superseded-leak checks, automatic abstention on insufficient evidence, and local/cloud model-swap invariance.
- **Event-driven incremental sync, multimodal structure ingestion & durable DAG workflows:** `incremental_watch.rs`, `incremental_sync.rs`, and `document_structure.rs` implement debounced watching, changed-chunk-only re-embedding, and table (`table_structured`), page, caption, and OCR preservation; `kernel/workflow.rs` provides per-step checkpoints, SHA-256 idempotency ledger replay, constant-time approval-token restart recovery, pre-mutation file snapshot rollback, and `USED_BY_AGENT`/`GENERATED_BY` provenance edges.
- **Worker resilience, cloud secret boundary, honest telemetry & Section 16 suite:** Completed `WorkerSeamClient::post_idempotent_json` + `worker_resilience.py` idempotent retry and timeout isolation, `.env`/private-key cloud egress blocking (`boundary.rs`), separated `empty`/`ok`/`unavailable` graph status probes (`onedoor.rs`, `audit.rs`), and full verification across `roadmap_section16_suite.rs` (16.1 separated RAG, 16.2 Jan→Mar→May temporal evolution, 16.3 cloud boundary, 16.4 fault/interrupted-index/approval-restart recovery).

Release notes: [docs/releases/RELEASE_NOTES_v13.2.0.md](docs/releases/RELEASE_NOTES_v13.2.0.md).

Build artifacts: `dist/ltcai-13.2.0-py3-none-any.whl`, `dist/ltcai-13.2.0.tar.gz`, `ltcai-13.2.0.tgz`, `dist/ltcai-13.2.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_13.2.0_aarch64.dmg`.

## v13.1.0 — Living Synthesis (2026-11-18)

Major user-facing upgrade integrating **the interactive Knowledge Graph inspector, 2-Hop neighborhood reasoning synthesis (`neighborhood_synthesis.rs`, `GraphNodeInspector.tsx`, `MultiHopSynthesisView.tsx`), and one-click contradiction resolution (`ContradictionInspector.tsx`)**, **one-click chat promotion (`Save to Brain`), inline staged change-proposal diff/approval (`InlineStagedProposals.tsx`), live agent tool indicators, and source-modal graph jumps (`AnswerProof.tsx`)**, and **embedding backlog progress bars with immediate indexing (`POST /api/index/drain`), `stale_embedder` preservation (`health.rs`), model download byte progress and cooperative cancellation (`InstallScreen.tsx`, `ModelsPanel.tsx`), `⌘K` node focus, reactive workspace cache invalidation, and leak-free Korean/English localization (100% Vitest)**.

- **Interactive Knowledge Graph & contradiction cleanup:** `/api/graph/node` (`include_reasoning=true`) synthesizes 1-hop/2-hop reasoning chains, bridge concepts, and conflicting edges in a single scoped SQLite query; `GraphNodeInspector.tsx`, `DigitalBrainExplorer.tsx`, and `ContradictionInspector.tsx` deliver neighbor pivoting, conflict filters, `/` and `Esc` shortcuts, contradiction proposal staging, and one-click resolution (`keep_old`, `replace`, `keep_both_temporal`).
- **Actionable chat & agent workspace UX:** Added one-click **Save to Brain** ingestion under chat replies, inline diff inspection and approve/reject controls immediately after agent change proposals, live active-tool labels during runs, graph navigation inside source modals, and consent-gated local original image previews.
- **Everyday usability, download cancellation & bilingual completeness:** Added `VectorFreshnessNotice` progress bars and **Index now** drain controls, `MB/GB` and filename display plus cooperative cancellation (`AbortController` + `unloadModel`) across onboarding and Model Library downloads, Command Palette knowledge-node focus and language switching, instant query invalidation on workspace switches, and full Korean/English error localization.

Release notes: [docs/releases/RELEASE_NOTES_v13.1.0.md](docs/releases/RELEASE_NOTES_v13.1.0.md).

Build artifacts: `dist/ltcai-13.1.0-py3-none-any.whl`, `dist/ltcai-13.1.0.tar.gz`, `ltcai-13.1.0.tgz`, `dist/ltcai-13.1.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_13.1.0_aarch64.dmg`.

## v13.0.0 — Sovereign Synthesis (2026-11-18)

Major release integrating **enforced cross-domain decoupling (`lattice_core::redact`, `lattice_agent::seam`, `domain_boundaries.rs`) and unified Rust server i18n (`lattice-auth` → `lattice-core::messages`)**, **multi-hop (2-Hop) Knowledge Graph reasoning paths, bridge concepts, and contradiction synthesis (`reasoning_paths.rs`, `MultiHopSynthesisView.tsx`)**, **single-transaction review store mutations, one-click reversible folder reorganization undo (`reversible_reorganization.rs`, `ReviewCard.tsx`), and durable conversation document sessions (`DocumentSessions::with_data_dir`)**, and **frontend API module splitting (`streaming.ts`, `transfers.ts`, 100% Vitest) plus Tauri backend modularization**.

- **Cross-domain separation & static boundary enforcement:** Unified secret redaction in `lattice_core::redact`, extracted `lattice_agent::seam::worker` so `kernel/` and `tools/` never reference the upper HTTP `surface/`, removed `modelops` → `adminops` lateral coupling, and added static layering tests in `domain_boundaries.rs`.
- **Multi-hop (2-Hop) GraphRAG reasoning synthesis:** `bm25/reasoning_paths.rs` and `fusion.rs` compute `Seed → Bridge → Target` 2-hop reasoning chains, `bridge_concepts`, and conflict/supersession edges (`CONTRADICTS`, `SUPERSEDES`), rendered directly in Brain hybrid search (`MultiHopSynthesisView.tsx`).
- **Atomic governance, reversible folder undo, session durability & modularization:** Unified review + timeline mutations in a single `WorkspaceOsStore` transaction, auto-generated reverse undo proposals on `folder_reorganization` approval with one-click UI rollback, persisted `chat_document_sessions.json` across restarts, consolidated `lattice-auth` messages (`check_server_i18n.mjs` verifying 146 Rust messages), and split `client.ts` and `src-tauri/src/backend.rs`.

Release notes: [docs/releases/RELEASE_NOTES_v13.0.0.md](docs/releases/RELEASE_NOTES_v13.0.0.md).

Build artifacts: `dist/ltcai-13.0.0-py3-none-any.whl`, `dist/ltcai-13.0.0.tar.gz`, `ltcai-13.0.0.tgz`, `dist/ltcai-13.0.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_13.0.0_aarch64.dmg`.

## v12.10.0 — Grounded Self (2026-11-17)

Release combining **deterministic agent transcript grounding verification (`grounding.rs`) and answer citation proof (`AnswerProof.tsx`)**, **the Brain Self-Model panel (`SelfModelPanel.tsx`) and Knowledge Graph `PART_OF` edge cascade deletion parity (`edges`, `edges_v2`)**, **cooperative model download cancellation (`499`) with automatic post-download semantic embedding transition and reindex draining**, and **`http_reply.rs` cross-domain decoupling, 6/6 Python worker router i18n lock, parallel `cargo nextest`, and chained `release.yml` → `publish.yml` CD automation**.

- **Transcript grounding verification & citation proof:** `rust/lattice-agent/src/kernel/transcript/grounding.rs` blocks unverified file claims and hallucinated `path:line` or symbol citations without per-model branching, while `AnswerProof.tsx` and the browser extension (`popup.js`) surface citation proof badges and server language sync.
- **Self-Model panel & `PART_OF` cascade parity:** Connected `SelfModelPanel.tsx` (100% Vitest) and `self_model_write.rs` for managing preferences, habits, decisions, traits, and relationships with inline approve/dismiss, and verified v1/v2 cascade deletion, aligned vector search, and cloud-boundary E2E across `graph_write_schema.rs`, `native_hnsw.rs`, `vector_alignment.rs`, and `cloud_lane.rs`.
- **Cooperative cancellation, automatic semantic transition & parallel Rust tests/chained CD:** Supported `499` download cancellation and automatic semantic embedder activation + batch reindexing across `download.py`, `embedding_recommendation.py`, and `index_api/engine.rs`; extracted `http_reply.rs`, locked i18n across all 6 Python worker routers, enabled parallel `cargo nextest` in `scripts/run_rust_tests.mjs`, and chained `.github/workflows/{release,publish}.yml`.

Release notes: [docs/releases/RELEASE_NOTES_v12.10.0.md](docs/releases/RELEASE_NOTES_v12.10.0.md).

Build artifacts: `dist/ltcai-12.10.0-py3-none-any.whl`, `dist/ltcai-12.10.0.tar.gz`, `ltcai-12.10.0.tgz`, `dist/ltcai-12.10.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_12.10.0_aarch64.dmg`.

## v12.9.0 — Semantic Guard (2026-11-17)

Release combining **semantic query embedding contract alignment (`query_embed.rs`), pure-Rust native HNSW (`NativeHnswIndex`), and scoped Python HNSW sidecars**, **fail-closed network boundary and cloud context isolation (`network_boundary_guard.rs`, `lattice-chat/src/boundary.rs`)**, **desktop (Tauri), CLI, host supervisor, and multi-stage Docker guardrails**, **real-time Workspace OS SSE (`/api/realtime/stream`) and `useAppBootstrap` extraction**, and **strengthened `.latticebrain` v2 archive KDF and expanded CI gates**.

- **Semantic query alignment & native/sidecar HNSW:** Aligned `/api/search/hybrid`, `/memory/recall`, and `/chat` query embeddings to the active `EmbeddingIndexContract` across `rust/lattice-retrieval/src/{query_embed.rs,native_hnsw.rs,vector_hnsw.rs}` and `latticeai/core/vector_index/{hnsw.py,sidecar.py}`, delivering pure-Rust HNSW and `(workspace_id, model_id, dim)`-scoped sidecar persistence.
- **Network boundary isolation & desktop/CLI/Docker guardrails:** Enforced server-side `local_only`/`cloud_allowed` consent and sensitivity filtering in `network_boundary_guard.rs` and `lattice-chat/src/{boundary.rs,cloud/context.rs}`, and hardened process lifecycle and container guardrails across `src-tauri/src/backend.rs`, `bin/ltcai.js`, `rust/lattice-host`, and `Dockerfile`.
- **Real-time SSE stream, v2 KDF & CI gates:** Connected real-time event streaming via `workspaceos/realtime.rs` and `useRealtimeStream.ts` (100% Vitest), extracted `useAppBootstrap.ts`, upgraded `portability/{crypto.rs,kdf.rs}` to v2 KDF (preserving v1 decryption compatibility), and strengthened `.github/workflows/{ci,dependency-audit,e2e-sidecar}.yml`.

Release notes: [docs/releases/RELEASE_NOTES_v12.9.0.md](docs/releases/RELEASE_NOTES_v12.9.0.md).

Build artifacts: `dist/ltcai-12.9.0-py3-none-any.whl`, `dist/ltcai-12.9.0.tar.gz`, `ltcai-12.9.0.tgz`, `dist/ltcai-12.9.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_12.9.0_aarch64.dmg`.

## v12.8.0 — Trusted Brain (2026-11-16)

Release unifying **embedding contracts and knowledge lifecycle tracking**, **pre-`LIMIT` SQLite workspace scoping, vector dimension alignment, and bounded sidecar caches**, **fail-closed user/workspace/tenant gateway isolation**, **encrypted `.latticebrain` archives and honest PostgreSQL `planned` reporting**, **frontend feature modularization**, and **PR retrieval gates plus macOS DMG CI/CD automation**.

- **Embedding contracts & knowledge lifecycle:** Enforced `EmbeddingIndexContract`, granular `IngestStatus`, unified `GraphWriter` deletion, and `KnowledgeLifecycleState` across `rust/lattice-core/src/embedding_contract.rs`, `graph_write/lifecycle.rs`, `latticeai/core/embedding_providers/`, and `latticeai/worker_app.py`.
- **Retrieval scoping, alignment, caching & gateway isolation:** Applied pre-`LIMIT` SQL filtering, `vector_search_aligned`, and LRU-bounded `SidecarCachePool` (`DEFAULT_MAX_CACHE_BYTES`) in `rust/lattice-retrieval`, and verified fail-closed multi-user/tenant/workspace isolation and audit logging via `IsolatedRootSpec` and `resolve_agent_scope` across `rust/lattice-agent`, `lattice-host`, and `lattice-auth`.
- **Portability archives, UI modularization & macOS DMG automation:** Delivered AEAD-encrypted `.latticebrain` verification/restore and X25519 sharing in `rust/lattice-platform/src/knowledge/portability/`, split `Library.tsx`, `System.tsx`, and `Capture.tsx` into `frontend/src/features/` (100% Vitest), and added retrieval gates and Apple Silicon DMG build/signing/`SHA256SUMS` upload to `.github/workflows/{ci,release,publish}.yml`.

Release notes: [docs/releases/RELEASE_NOTES_v12.8.0.md](docs/releases/RELEASE_NOTES_v12.8.0.md).

Build artifacts: `dist/ltcai-12.8.0-py3-none-any.whl`, `dist/ltcai-12.8.0.tar.gz`, `ltcai-12.8.0.tgz`, `dist/ltcai-12.8.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_12.8.0_aarch64.dmg`.

## v12.7.0 — Fast Index (2026-11-16)

Release introducing the **Pre-Tokenized BM25F Sidecar (`Bm25SidecarIndex`)**, SQLite `bm25_node_stats` persistence, **inverted posting-list and GraphRAG adjacency caches**, and **a product-first `README.md` overhaul with `v12.0.0+` release history cleanup**. One Door remains **422 operations / 41 families**, the worker remains **20 routes**, and `GraphWriter` remains the sole writer.

- **Pre-Tokenized BM25F Sidecar & SQLite `bm25_node_stats` persistence:** Registered the `bm25_node_stats` schema and `TableOwnership` across `rust/lattice-core/src/bm25_stats.rs`, `db/tables.rs`, and `graph_write/{mod,primitives,documents,curation,provenance}.rs`; enabled `persist_bm25_sidecar` in `GraphWriter::open` to pre-persist field lengths (`dl_title`, `dl_tags`, `dl_body`, `dl_meta`) and term frequencies (`tf_json`) on write and clean them automatically on deletion, curation, or re-import while safely skipping frozen/read-only golden databases lacking the table.
- **O(1) sidecar index & GraphRAG adjacency caches:** Delivered incremental `(node_id, updated_at, content_hash)` synchronization, O(1) field-length and term-frequency lookup, inverted posting candidate scoring, and `title_to_doc_idxs`/`wikilink_to_doc_idxs`/`tag_to_doc_idxs`/`edge_adj` caches for `expand_knowledge_graph_with_sidecar` and `score_and_expand_with_sidecar` across `rust/lattice-retrieval/src/bm25/` and `service_hybrid.rs`.
- **Product-first `README.md` & `v12.0.0+` release cleanup:** Rebuilt `README.md` around product positioning, Quick Start, and an 8-panel Product Tour, pruned pre-12.0 release notes from local docs and GitHub Releases, and enforced the `v12.0.0+` floor in `scripts/check_current_release_docs.mjs`.

Release notes: [docs/releases/RELEASE_NOTES_v12.7.0.md](docs/releases/RELEASE_NOTES_v12.7.0.md).

Build artifacts: `dist/ltcai-12.7.0-py3-none-any.whl`, `dist/ltcai-12.7.0.tar.gz`, `ltcai-12.7.0.tgz`, `dist/ltcai-12.7.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_12.7.0_aarch64.dmg`.

## v12.6.0 — Hybrid Graph (2026-11-15)

Release combining **3-way BM25F + Vector + Knowledge Graph RRF hybrid indexing and search**, graph-aware agent skills and MCP search tools, **multi-scale subword `FallbackTextEmbedder`**, **unauthenticated EmbeddingGemma 2 public mirrors and quantized (GGUF/MLX/ONNX) fallbacks**, **real-time SSE download speed (`MB/s`) and ETA streaming UI**, **Pillow 13+ legacy removal**, **Ubuntu 24.04/26.04 `t64` and Chromium CI resilience**, and **the modular `scripts/bump_version/` CLI**. One Door remains **422 operations / 41 families**, the worker remains **20 routes**, and `GraphWriter` remains the sole writer.

- **3-Way BM25F + Vector + Knowledge Graph RRF hybrid search:** Implemented field-weighted Okapi BM25F (`k1=1.2, b=0.75`, Korean particle stripping, CJK bigrams, identifier subwords), 1-hop/2-hop Knowledge Graph structural expansion (`RELATES_TO`, `MENTIONS`, `PART_OF`, shared concept nodes, `[[wikilinks]]`, `#tags`), 3-way RRF fusion, and `/api/search/hybrid` zero-hit rescue fallback across `rust/lattice-retrieval/src/bm25/`, `service_hybrid.rs`, `hybrid.rs`, and `search_api/handlers.rs`.
- **Graph skills and agent/MCP graph tools:** Added `mode` (`hybrid`/`bm25f`/`graph`/`graphrag`/`subgraph`), `hops`, and `include_graph` subgraph extraction to `knowledge_search`/`obsidian_search` plus `knowledge_graph_search`, `knowledge_graph_context`, and `GRAPH_SKILL_BRIEFS` in `rust/lattice-platform/src/toolsurface/tools/knowledge/` and `rust/lattice-agent`.
- **Multi-scale subword `FallbackTextEmbedder` (#13.1):** Upgraded the offline embedder (`768d`, `lattice-fallback-subword-v2:768`) in `latticeai/core/embedding_providers/text.py` and `rust/lattice-core/src/embeddings.rs` with `3..5-gram` subwords, Korean particle-stripped syllables and choseong pairs, CJK n-grams, 2-probe signed Blake2b projection, and L2 normalization.
- **Unauthenticated EmbeddingGemma 2 public mirrors & quantized fallbacks (#14.2):** Automatically falls back to `mlx-community/embeddinggemma-2-4bit`, `mlx-community/embeddinggemma-2-8bit`, `ggml-org/embeddinggemma-2-GGUF`, and `onnx-community/embeddinggemma-300m-ONNX` when `HF_TOKEN` is unset or a gated `401`/`403` occurs, with custom `HF_ENDPOINT` support.
- **Real-time SSE download progress UI (#15.2):** Streamed `downloaded_bytes`, `total_bytes`, `percent`, `speed_bps`, `eta_seconds`, and `gated_fallback` over SSE (`text/event-stream`) from `download.py` and `rust/lattice-platform/src/modelops` into `ProductFlow.tsx`, `InstallScreen.tsx`, and `Library.tsx` (100% Vitest coverage).
- **Pillow 13+ cleanup (#13.2), Ubuntu 24.04/26.04 `t64` CI resilience (#14.1) & `scripts/bump_version/` CLI (#15.1):** Removed `diff.getdata()` legacy code from `scripts/check_screenshot_pixel_delta.py`, added `t64` package fallback and Chromium verification in `.github/workflows/*.yml` and `scripts/check_ubuntu_runner_compat.mjs`, and modularized `scripts/bump_version/` (`all`, `openapi`, `cargo`, `markers` subcommands).

Release notes: [docs/releases/RELEASE_NOTES_v12.6.0.md](docs/releases/RELEASE_NOTES_v12.6.0.md).

Build artifacts: `dist/ltcai-12.6.0-py3-none-any.whl`, `dist/ltcai-12.6.0.tar.gz`, `ltcai-12.6.0.tgz`, `dist/ltcai-12.6.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_12.6.0_aarch64.dmg`.

## v12.5.0 — Deep Vector (2026-11-11)

Release natively integrating **Google EmbeddingGemma 2** (`google/embeddinggemma-2`, 768-dim) from Hugging Face and introducing a hardware-aware (RAM/VRAM) model recommendation system that reserves embedding memory upfront before selecting the optimal LLM. Also completed Pillow 13+ `get_flattened_data()` migration, native Node 24 upgrades across GitHub Actions workflows, and automated synchronization of 49 OpenAPI specification and fragment targets in `scripts/bump_version.py`. One Door remains **422 operations / 41 families**, the worker remains **20 routes**, and `GraphWriter` remains the sole writer.

- **Native Google EmbeddingGemma 2 integration:** Implemented Hugging Face embedding providers (`google/embeddinggemma-2`, `embeddinggemma-2`, `embeddinggemma-2-4bit`, `embeddinggemma-2-8bit`, `embeddinggemma-300m`, 768-dim) in `latticeai/core/embedding_providers/text.py` with mean pooling, L2 normalization, task prefixes, and deterministic offline hash fallback.
- **Hardware-tailored embedding recommendation system:** Modularized `rust/lattice-platform/src/modelops/models_catalog/embedding.rs` and `latticeai/services/embedding_recommendation.py` to recommend EmbeddingGemma 2 (0.5GB–1.5GB RAM reserved) on 8GB+ hosts and EmbeddingGemma 300M (0.3GB RAM reserved) on smaller hosts, deducting reserved memory from `effective_ram_gb` before recommending an LLM.
- **Onboarding & Model Library embedding UI:** Added localized hardware-tailored EmbeddingGemma 2 recommendation cards to `ProductFlow.tsx` and a recommended embedding section with one-click setup to `Library.tsx`.
- **Pillow 13+ API migration & CI/CD modernization:** Replaced deprecated `diff.getdata()` with `diff.get_flattened_data()` in `scripts/check_screenshot_pixel_delta.py` and upgraded all 7 GitHub Actions workflows to Node 24 with `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: "true"`.
- **Automated OpenAPI spec & fragment synchronization:** Expanded `scripts/bump_version.py` to update 49 targets across `frontend/openapi.json`, `rust/fixtures/openapi/_envelope.json`, 13 family fragments, and `_index.json` (recomputing SHA-256 hashes).
- **Full interactive UI verification:** Verified every route and button with Playwright E2E suite `tests/visual/v3.all_buttons_inspection.spec.js` with 0 console errors and 0 server errors.

Release notes: [docs/releases/RELEASE_NOTES_v12.5.0.md](docs/releases/RELEASE_NOTES_v12.5.0.md).

Build artifacts: `dist/ltcai-12.5.0-py3-none-any.whl`, `dist/ltcai-12.5.0.tar.gz`, `ltcai-12.5.0.tgz`, `dist/ltcai-12.5.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_12.5.0_aarch64.dmg`.

## v12.4.0 — Sharp Weight (2026-11-11)

Release adding Hugging Face-verified **Qwen 3.8 27B 4-bit** (`mlx-community/Qwen3.8-27B-4bit`) and **Gemma 4 26B A4B QAT / QAT-OptiQ 4-bit** (`mlx-community/gemma-4-26B-A4B-it-qat-4bit`, `mlx-community/gemma-4-26B-A4B-it-qat-OptiQ-4bit`) to recommendation and download catalogs and closing all Python (`pillow>=13,<14`) and npm dependency security advisories. One Door remains **422 operations / 41 families**, the worker remains **20 routes**, and `GraphWriter` remains the sole writer.

- **Qwen 3.8 27B 4-bit & multi-engine routing:** Added `mlx-community/Qwen3.8-27B-4bit` (`16.1GB`, `qwen3_5`) to the large tier and wired `ggml-org/Qwen3.8-27B-GGUF:Q4_K_M` and `Qwen/Qwen3.8-27B` across 5 local engines (`local_mlx`, `ollama`, `vllm`, `lmstudio`, `llamacpp`), cloud providers (`together`, `openrouter`), and Rust `CATALOG_HINTS`.
- **Gemma 4 26B A4B QAT & QAT-OptiQ 4-bit:** Added Google official QAT-based `mlx-community/gemma-4-26B-A4B-it-qat-4bit` (`15.6GB`, routing to `google/gemma-4-26B-A4B-it-qat-q4_0-gguf:Q4_0`) and sensitivity-mixed 4/8-bit `mlx-optiq` build `mlx-community/gemma-4-26B-A4B-it-qat-OptiQ-4bit` (`21.9GB`).
- **Python & npm security advisory closure and runtime hardening:** Upgraded to `pillow>=13,<14` to resolve 39 `Pillow 12.3.0` CVEs, reached 0 npm audit advisories in root and `vscode-extension`, added HTTP(S) scheme validation to `model_engines`, wired injected clock (`state.auth.clock()`) into `lattice-platform` `admin_log_retention`, and scoped `frontend/src/styles.css` with `@import "tailwindcss" source("..");`.

Release notes: [docs/releases/RELEASE_NOTES_v12.4.0.md](docs/releases/RELEASE_NOTES_v12.4.0.md).

Build artifacts: `dist/ltcai-12.4.0-py3-none-any.whl`, `dist/ltcai-12.4.0.tar.gz`, `ltcai-12.4.0.tgz`, `dist/ltcai-12.4.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_12.4.0_aarch64.dmg`.

## v12.3.0 — Live Wire (2026-10-19)

Release aligning live Hugging Face quantization tags, GGUF routing, and cloud provider defaults, and verifying small-model tool/MCP argument recovery plus HTML creation, inspection, and download end to end on a real local model (`mlx-community/gemma-4-e2b-it-4bit`). One Door remains **422 operations / 41 families**, the worker remains **20 routes**, and `GraphWriter` remains the sole writer.

- **Hugging Face GGUF tags & routing accuracy:** Aligned `ggml-org/gemma-4-*-GGUF` to `Q4_0` and `ggml-org/gpt-oss-20b-GGUF` to `MXFP4`, and connected `Qwen3.5-9B-MLX-4bit` (`unsloth/Qwen3.5-9B-GGUF`) and `Qwen3.6-35B-A3B-4bit` (`ggml-org/Qwen3.6-35B-A3B-GGUF`) GGUF routes to `ollama`, `lmstudio`, and `llamacpp`.
- **Cloud & local provider defaults synced:** Updated `xai` default to `grok-4.6`, `together` to `Qwen/Qwen3.6-27B`, and `openrouter` to `qwen/qwen3.6-35b-a3b` / `x-ai/grok-4.6`, and removed nonexistent `mlx-community/bge-m3` from the local MLX catalog.
- **Auxiliary GGUF filtering, `--mmproj` loading & shard verification:** Filtered `mmproj-`/`mtp-`/`dflash-`/`eagle3-`/`imatrix` GGUFs out of primary weight selection, downloaded companion `mmproj-*.gguf` files for `llama-server --mmproj`, and validated shard completeness in `model.safetensors.index.json`.
- **Small-model tool/MCP recovery:** Expanded `KNOWN_ACTIONS`, recovered stringified `arguments`, unwrapped OpenAI `tool_calls` arrays, extracted fenced HTML blocks (`fenced_content_block`), and applied `adopt_named_args` (`html`/`code`/`source`/`data` → `content`) inside `perform_action`.
- **HTML structure inspection & RFC 5987 UTF-8 download:** `POST /tools/inspect_html` extracts `title`, `links`, `scripts`, `stylesheets`, `images`, `forms`, and `headings`, and `GET /tools/download` provides UTF-8 path decoding with `filename*=UTF-8''...` headers.

Release notes: [docs/releases/RELEASE_NOTES_v12.3.0.md](docs/releases/RELEASE_NOTES_v12.3.0.md).

Build artifacts: `dist/ltcai-12.3.0-py3-none-any.whl`, `dist/ltcai-12.3.0.tar.gz`, `ltcai-12.3.0.tgz`, `dist/ltcai-12.3.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_12.3.0_aarch64.dmg`.

## v12.2.1 — True Count (2026-08-29)

Patch ensuring search, skip, watch, and small-model answers count *only what actually exists*. One Door remains **422 operations / 41 families**, the worker remains **20 routes**, and `GraphWriter` remains the sole writer.

- **HNSW sidecar `COUNT` + delta:** Warm queries no longer scan the full vector table; only newly added rows are fetched and appended.
- **`SkipByHash` re-stamps unchanged files:** A `touch` with identical bytes becomes a stamp-skip on the next scan.
- **Files removed during watch are removed from the graph:** Disk files are untouched; folder ingest still reports missing files without auto-deleting.
- **Thin summaries fill from read files:** Unverified claims route to the review queue.
- **Compact 9-line menu keeps MCP core:** `api_key` probes liveness via `GET /models` without billed completions and closes on failure.
- **Hash search discloses that it is hash-based:** First run and Model Library guide users toward installing a semantic embedding model.

Per-release notes are consolidated under [docs/releases/](docs/releases/). This release: [docs/releases/RELEASE_NOTES_v12.2.1.md](docs/releases/RELEASE_NOTES_v12.2.1.md).

Build artifacts: `dist/ltcai-12.2.1-py3-none-any.whl`, `dist/ltcai-12.2.1.tar.gz`, `ltcai-12.2.1.tgz`, `dist/ltcai-12.2.1.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_12.2.1_aarch64.dmg`.

Honest disclosure: hash embeddings remain the zero-download default (now disclosed candidly), DMG builds are ad-hoc signed when no Apple Developer ID is configured, and VS Code Marketplace verification may take a few minutes after publication.

## v12.2.0 — Small Voice (2026-08-29)

Release enabling 2B-class local models to select MCP tools, skills, and native actions reliably, and surfacing those workflows directly on the Home screen. One Door remains **422 operations / 41 families**, and the worker remains **20 routes**.

- **Unified tool-call envelopes:** `name`/`arguments`, Qwen `<function=…>` XML, and `Action: mcp.grep` normalize to the same dispatch call; the compact action list fits in 9 lines.
- **Skill `when` matching:** When a user prompt matches a skill's "use when" clause, the runtime loads its instructions first without requiring the skill name.
- **Request-derived missing arguments:** Quoted search terms fill missing `pattern` arguments automatically.
- **Home composer chips:** Added plain-language starter chips (find folder, read & summarize file, recall memory, review skill) alongside a matching Settings summary card.

The 12.1.0 Fast Path overlapping ingest pipeline remains intact. Full details: [docs/releases/RELEASE_NOTES_v12.2.0.md](docs/releases/RELEASE_NOTES_v12.2.0.md).

Build artifacts: `dist/ltcai-12.2.0-py3-none-any.whl`, `dist/ltcai-12.2.0.tar.gz`, `ltcai-12.2.0.tgz`, `dist/ltcai-12.2.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_12.2.0_aarch64.dmg`.

## v12.1.0 — Fast Path (2026-08-29)

Release overlapping the disk-to-recall ingestion pipeline while keeping the single `GraphWriter` engine intact by removing seam serialization and write-lock bottlenecks. One Door remains **422 operations / 41 families**, and the worker remains **20 routes**.

- **Overlapped folder & watch ingest pipeline:** Up to 4 files overlap `parse` / `extract` / `embed`; stamp-skipped files bypass the pool; writes still serialize through `GraphWriter`.
- **Single `/worker/embed` call per note:** Document and chunk vectors share one `texts` payload; extraction runs alongside rather than blocking embedding; `write_vectors_with` stores those vectors directly without redundant hash re-embedding.
- **Rebuild computes embeddings outside the write lock:** Matches the 12.0.0 drain architecture.
- **Auto-HNSW search:** Default env remains `brute`. At 512+ rows with a worker seam attached, search tries `hnsw+rescore` first and falls back to exact `brute` scanning on failure; golden outputs remain byte-identical.
- **CI fixes:** Fixed weekly `pip-audit` failing on clean runs without a report file and weekly Postgres jobs referencing removed test files; `ltcai[postgres]` remains an empty extra alias.

Honest disclosure: small-model content quality, mock-only `api_key` validation, ad-hoc DMG signing, no auto-delete on watch, and default semantic embedding rollout remain open items. Full details: [docs/releases/RELEASE_NOTES_v12.1.0.md](docs/releases/RELEASE_NOTES_v12.1.0.md).

Build artifacts: `dist/ltcai-12.1.0-py3-none-any.whl`, `dist/ltcai-12.1.0.tar.gz`, `ltcai-12.1.0.tgz`, `dist/ltcai-12.1.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_12.1.0_aarch64.dmg`. Wildcard uploads are never used.

## v12.0.0 — Open House (2026-08-18)

Foundational architecture release regrouping the two largest Rust crates by domain (all `git mv`, zero behavior change), rewriting contributor onboarding docs, and closing four previously documented gaps. One Door serves **422 native operations / 41 families** (adding `POST /mcp` and folder-reorganization routes), and the worker serves **20 routes** (adding `POST /worker/vector/query`).

- **Complexity management:** Regrouped 43 files in `lattice-agent` into six groups (`kernel`/`parse`/`content`/`tools`/`surface`/`prompts`) and 31 flat modules in `lattice-platform` into seven domains (`workspaceos`/`toolsurface`/`governance`/`adminops`/`knowledge`/`modelops`/`shell`) via 100 `git mv` moves. Added crate-local `ARCHITECTURE.md` maps, ownership invariants in each `mod.rs`, and compatibility re-exports in `src/lib.rs`. Rewrote `docs/DEVELOPMENT.md` for 10-minute contributor onboarding and created `docs/ROADMAP.md`.
- **Four documented gaps closed:** Live in-process restore via store generation epochs (no restart required); consent-gated `/setup/install` running `brew`/`pip`/`uv` only for items on a **server-derived allowlist** (manual remains default); `POST /mcp` brought inside the OpenAPI contract as a native single JSON-RPC envelope operation; pointer tools declared as `pip install "ltcai[pointer]"`.
- **GraphRAG quality:** Two-stage Korean particle stripping + grounding gate, containment deduplication, directional typed edges (`PART_OF`, `CONTRADICTS`), section trees (`Document ←PART_OF— Section —HAS_CHUNK→ Chunk`, 549/555 triples carrying section provenance), and automatic real-embedder detection with honest hash fallback disclosure and `(model, dim)` identity filtering.
- **GraphRAG performance:** Unchanged re-index 33s → **0.26s** (waste ratio 1.00 → 0.00 via `ingestion_provenance` fingerprints), initial indexing 25.8s → 7.2s, drain ~66 → ~1,300 items/s, 991-item backlog 40m → 15.3s (adaptive scheduler), opt-in `LATTICEAI_VECTOR_INDEX=hnsw` (`hnsw+rescore`, default remains `brute`), vault-watch diff skipping, and confirmed deleted-file cleanup (`POST /api/ingestion/folder/prune` dry-run/confirm using `delete_document_tree` with zero dangling edges).
- **Universal small-model harness (zero per-model hacks):** `GUIDED` mode removes JSON formatting entirely (numbered menu → one argument at a time → harness assembles action into the shared `perform_action` gate chain), measured probing selects `standard`/`compact`/`guided` cached by model ID + crate version, downward-only mid-run self-demotion, unified catalog (native + `mcp.*` + `skill.*`), and prefix forcing + stop sequences. Qwen **0.5B reached DONE in guided mode** (3.9s on real files).
- **Frontend:** Route and heavy-panel `ErrorBoundary` coverage with retry, lazy splitting for Act/Brain sub-routes, expanded preview patterns (permission-mode diff preview, risky-toggle acknowledgment), bundle at **104.2 / 150 KiB**.
- **Bug fixes:** Fixed RAG citation prompt leakage into agent runs, model-loading roster gate blocking Qwen AWQ, fail-closed `COPIED_EXAMPLE` detection for verbatim prompt copying, and `<|channel>` control-frame leaks.
- **Honest disclosure:** Small-model *content* quality is guarded by fail-closed verification, `api_key` cloud paths remain mock-verified (zero-billing policy), DMG is ad-hoc signed, default search is `brute` (`hnsw` is opt-in), watch does not auto-delete, and crate regrouping moved files rather than dissolving all internal coupling.

Build artifacts: `dist/ltcai-12.0.0-py3-none-any.whl`, `dist/ltcai-12.0.0.tar.gz`, `ltcai-12.0.0.tgz`, `dist/ltcai-12.0.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_12.0.0_aarch64.dmg`. Wildcard uploads are never used.

Full details: [docs/releases/RELEASE_NOTES_v12.0.0.md](docs/releases/RELEASE_NOTES_v12.0.0.md).
