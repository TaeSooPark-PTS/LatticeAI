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

## v13.7.2 — Living Synthesis (2026-11-24)

Migrated all callers across the Rust workspace (`lattice-host`, `lattice-chat`, `lattice-platform`, `lattice-agent`, `lattice-retrieval`, `src-tauri`, and all integration tests) to canonical domain module paths (`lattice_platform::<domain>::*`, `lattice_agent::<group>::*`), removed the obsolete flat crate-root `pub use` compatibility maps from `lattice-agent` and `lattice-platform`, and removed the last unused `lattice_chat::crate_ready()` scaffold stub.

Release notes: [docs/releases/RELEASE_NOTES_v13.7.2.md](docs/releases/RELEASE_NOTES_v13.7.2.md).

Build artifacts: `dist/ltcai-13.7.2-py3-none-any.whl`, `dist/ltcai-13.7.2.tar.gz`, `ltcai-13.7.2.tgz`, `dist/ltcai-13.7.2.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_13.7.2_aarch64.dmg`.

## v13.7.1 — Living Synthesis (2026-11-24)

Removed all remaining legacy `forward()` fallbacks and dead compatibility shims across `lattice-ingest`, `lattice-platform`, and `lattice-retrieval` (returning HTTP `503` consistently when `GraphWriter` is unwired), added native `source: "git"` and unwired-`GraphWriter` 503 integration tests in `local_files_replay.rs`, and pruned pre-`13.7.0` review/plan/legacy docs while preserving all benchmark documents.

Release notes: [docs/releases/RELEASE_NOTES_v13.7.1.md](docs/releases/RELEASE_NOTES_v13.7.1.md).

Build artifacts: `dist/ltcai-13.7.1-py3-none-any.whl`, `dist/ltcai-13.7.1.tar.gz`, `ltcai-13.7.1.tgz`, `dist/ltcai-13.7.1.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_13.7.1_aarch64.dmg`.

## v13.7.0 — Living Synthesis (2026-11-24)

Implemented native Rust `GraphWriter` ingestion for Obsidian vaults (`[[wikilink]]` `LINKS_TO` edges) and `notion`/`git`/`mail` interop bridges in `lattice-ingest`, migrated `vite.config.ts`/`vitest.config.ts` to native ESM `.mts` (zero Vite 8 `configLoader: 'native'` warnings), pruned pre-`v13.0.0` release notes (`v13.0.0+` floor), and modernized `ARCHITECTURE.md` and `docs/ko/ARCHITECTURE.md` to `13.7.0`.

Release notes: [docs/releases/RELEASE_NOTES_v13.7.0.md](docs/releases/RELEASE_NOTES_v13.7.0.md).

Build artifacts: `dist/ltcai-13.7.0-py3-none-any.whl`, `dist/ltcai-13.7.0.tar.gz`, `ltcai-13.7.0.tgz`, `dist/ltcai-13.7.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_13.7.0_aarch64.dmg`.

## v13.6.0 — Living Synthesis (2026-11-24)

Replaced `openapi-typescript@7.13.0` and the `openapi-ts5` alias with a zero-dependency native TypeScript 7 (`tsgo`) OpenAPI declaration generator (`scripts/generate_openapi_types.mjs`), removing all TypeScript 5 packages from `package-lock.json`, and split the five monolithic `tests/unit/test_cov_p1b_remaining*.py` coverage bundles into nine focused domain unit test modules backed by `tests/unit/_runtime_test_fakes.py`.

Release notes: [docs/releases/RELEASE_NOTES_v13.6.0.md](docs/releases/RELEASE_NOTES_v13.6.0.md).

Build artifacts: `dist/ltcai-13.6.0-py3-none-any.whl`, `dist/ltcai-13.6.0.tar.gz`, `ltcai-13.6.0.tgz`, `dist/ltcai-13.6.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_13.6.0_aarch64.dmg`.

## v13.5.0 — Living Synthesis (2026-11-24)

Cross-platform one-click engine installation and **Qwen3.8 27B 4-bit** setup across **macOS** (`omlx` Homebrew + zero-Homebrew universal2 wheel fallback, `local_mlx`), **Windows** (`winget` Ollama / llama.cpp / LM Studio + `pip` `llama-cpp-python[server]` fallback), and **Linux** (Ollama official install script + `pip` / `vllm`), live available-RAM pressure tuning (`host_available_ram_gb`, `omlx_serve_args`, `llamacpp_serve_args`), workspace upgrade to **TypeScript 7.0.2 (`tsgo`)** with `@babel/parser` i18n AST checking, and modular `engine_adapters.py` / `model_loading.py` refactoring with dead-code and obsolete-test removal.

Release notes: [docs/releases/RELEASE_NOTES_v13.5.0.md](docs/releases/RELEASE_NOTES_v13.5.0.md).

Build artifacts: `dist/ltcai-13.5.0-py3-none-any.whl`, `dist/ltcai-13.5.0.tar.gz`, `ltcai-13.5.0.tgz`, `dist/ltcai-13.5.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_13.5.0_aarch64.dmg`.

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
