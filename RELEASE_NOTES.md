[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./RELEASE_NOTES.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](./docs/ko/RELEASE_NOTES.md)

# [v13.4.1 - Living Synthesis] (2026-11-23)

Resolved major/breaking dependency upgrades across frontend (`typescript@7.0.2`, `vitest@5.1.1`, `jsdom@31`, `@testing-library/jest-dom@8`), Rust workspace (`axum@0.8`, `rusqlite@0.43`, `sha2@0.11`, `scrypt@0.12`, `fancy-regex@0.20`, `zip@9`, `reqwest@0.13`, `ed25519-dalek@3`, `notify@8`), and Tauri desktop (`tauri@2.11.6`, `plist@1.8.0`, `time@0.3.55`), maintaining 100% Vitest coverage and full workspace compilation.

See [docs/releases/RELEASE_NOTES_v13.4.1.md](docs/releases/RELEASE_NOTES_v13.4.1.md).

# [v13.4.0 - Living Synthesis] (2026-11-20)

Delivered the model-agnostic Universal Adaptive Agent Harness (`probe.rs` 3-turn micro-probing, observation-first tool ranking & bilingual domain classification, deterministic UI bounding-box center resolution `ui_resolve.rs`, malformed-JSON repair & ChatML completion prefill, active vs. superseded evidence separation `rag_grounding.rs`), modular native web fetching & Computer Use bridge parity, a reproducible 25-task live empirical benchmark on `Qwen2.5-0.5B-Instruct-Q4_K_M` lifting overall task pass rate from `28.0%` raw (`24.0%` standard loop) to `84.0%` (`+56.0 pp` / `+200.0%` relative gain, `100.0%` Computer Use, `100.0%` RAG Grounding, `-63.3%` latency), and 100% EN <-> KO bidirectional GitHub Markdown parity.

See [docs/releases/RELEASE_NOTES_v13.4.0.md](docs/releases/RELEASE_NOTES_v13.4.0.md) and [docs/ADAPTIVE_HARNESS_BENCHMARK.md](docs/ADAPTIVE_HARNESS_BENCHMARK.md).

# [v13.3.1 - Living Synthesis] (2026-11-19)

Delivered two-phase async incremental chunk synchronization for semantic embedding contracts (`plan_incremental_sync` + async `/worker/embed` + `commit_incremental_sync`, `watch_deliver.rs`), wired `AnswerProof.tsx` claim verification telemetry (`claim_verification`) and `BrainIntelligencePanel.tsx` entity resolution & temporal timeline summary cards (full Korean/English i18n at 100% Vitest coverage), and enabled weekly full-stack `.github/dependabot.yml` dependency monitoring.

See [docs/releases/RELEASE_NOTES_v13.3.1.md](docs/releases/RELEASE_NOTES_v13.3.1.md).

# [v13.3.0 - Living Synthesis] (2026-11-19)

Wired core v13.2.0 subsystems (`post_idempotent_compute`, `verify_grounding_and_abstention`, `entity_resolution` & `temporal`, `DebouncedWatchEngine` & `sync_document_incremental`, `DurableWorkflowStore`) into live HTTP, chat, watch, and workflow execution paths; fixed `worker_resilience.py` concurrency, cancellation, and streaming timeout bugs as well as `ingest.rs` temporary-file collisions and silent structure-overlay failures; achieved zero non-test `.unwrap()` calls across the Rust workspace, consolidated 7 duplicated `unsafe libc` local-time blocks into `clock.rs`, and eliminated every handwritten production file over 800 lines across the stack (maintaining 100% Vitest coverage).

See [docs/releases/RELEASE_NOTES_v13.3.0.md](docs/releases/RELEASE_NOTES_v13.3.0.md).

# [v13.2.1 - Living Synthesis] (2026-11-19)

Completed full-stack modularization refactoring across the Rust workspace (`lattice-auth`, `lattice-core`, `lattice-ingest`, `lattice-retrieval`, `lattice-agent`, `lattice-platform`, `lattice-host`), Python (`model_capability_registry.py`, `worker_compute.py`), and Frontend (`brainData.ts`); removed unreferenced dead code and retired legacy orbital Brain layers across the stack (maintaining 100% Vitest coverage); and hardened Ubuntu `t64` visual-regression CI mirror failover.

See [docs/releases/RELEASE_NOTES_v13.2.1.md](docs/releases/RELEASE_NOTES_v13.2.1.md).

# [v13.2.0 - Living Synthesis] (2026-11-19)

Delivered bi-temporal Knowledge Graph point-in-time queries and non-destructive Korean/English entity resolution (`temporal.rs`, `entity_resolution.rs`), claim-level grounding verification and abstention (`grounding.rs`) with separated model-agnostic RAG evaluation (`rag_eval_grounded.rs`), OS-event debounced watching, chunk-level incremental re-embedding, and table/page/OCR structure-preserving ingestion (`incremental_watch.rs`, `incremental_sync.rs`, `document_structure.rs`), a durable DAG agent workflow engine with atomic checkpoints, SHA-256 idempotency ledger, approval-across-restart recovery, and rollback (`kernel/workflow.rs`), plus worker idempotent retry and timeout isolation (`worker.rs`, `worker_resilience.py`), cloud secret-file blocking (`boundary.rs`), honest graph status probes (`onedoor.rs`, `audit.rs`), and the Section 16 end-to-end product verification suite (`roadmap_section16_suite.rs`, 16.1–16.4).

See [docs/releases/RELEASE_NOTES_v13.2.0.md](docs/releases/RELEASE_NOTES_v13.2.0.md).

# [v13.1.0 - Living Synthesis] (2026-11-18)

Added the interactive Knowledge Graph node inspector, single-query 2-Hop neighborhood reasoning synthesis (`neighborhood_synthesis.rs`, `GraphNodeInspector.tsx`, `MultiHopSynthesisView.tsx`), conflict filtering and one-click contradiction staging/resolution (`ContradictionInspector.tsx`), one-click **Save to Brain** chat promotion, inline staged change-proposal diff approval/rejection (`InlineStagedProposals.tsx`), live active-tool step indicators, source-modal graph jumps (`AnswerProof.tsx`), embedding backlog progress bars with **Index now** drain (`POST /api/index/drain`) and `stale_embedder` preservation (`health.rs`), model download byte/filename progress and cooperative cancellation (`InstallScreen.tsx`, `ModelsPanel.tsx`), `⌘K` knowledge-node focus, reactive workspace cache invalidation, and zero-leak Korean/English localization (100% Vitest coverage).

See [docs/releases/RELEASE_NOTES_v13.1.0.md](docs/releases/RELEASE_NOTES_v13.1.0.md).

# [v13.0.0 - Sovereign Synthesis] (2026-11-18)

Enforced cross-domain decoupling (`lattice_core::redact`, `lattice_agent::seam::worker`, `modelops` → `http_reply`, `domain_boundaries.rs`) and unified Rust server i18n (`lattice-auth` → `lattice_core::messages`, 146 catalog messages verified by `check_server_i18n.mjs`), added 2-Hop Knowledge Graph reasoning chains, bridge concepts, and contradiction synthesis (`reasoning_paths.rs`, `MultiHopSynthesisView.tsx`), introduced single-transaction review store mutations, one-click reversible folder reorganization undo (`reversible_reorganization.rs`, `ReviewCard.tsx`), and durable conversation document sessions (`chat_document_sessions.json`), and modularized frontend API modules (`streaming.ts`, `transfers.ts`, 100% Vitest) and the Tauri backend.

See [docs/releases/RELEASE_NOTES_v13.0.0.md](docs/releases/RELEASE_NOTES_v13.0.0.md).

# [v12.10.0 - Grounded Self] (2026-11-17)

Added deterministic agent transcript grounding verification (`grounding.rs`), interactive citation proof badges (`AnswerProof.tsx`), and browser-extension language synchronization; delivered the Brain Self-Model panel (`SelfModelPanel.tsx`, 100% Vitest) and Knowledge Graph `PART_OF` edge cascade deletion parity across `edges` and `edges_v2`; added cooperative `499` model download cancellation with automatic post-download semantic embedding transition and batch reindex draining; decoupled `http_reply.rs`, locked Python worker server i18n across 6/6 routers, enabled parallel `cargo nextest` (`scripts/run_rust_tests.mjs`), and chained `release.yml` → `publish.yml` CD automation.

See [docs/releases/RELEASE_NOTES_v12.10.0.md](docs/releases/RELEASE_NOTES_v12.10.0.md).

# [v12.9.0 - Semantic Guard] (2026-11-17)

Aligned query-time semantic embeddings with the active `EmbeddingIndexContract` (`query_embed.rs`), added pure-Rust `NativeHnswIndex` and workspace/model-scoped Python HNSW sidecars, enforced fail-closed `network_boundary_guard.rs` and `lattice-chat` cloud-boundary isolation, hardened desktop (Tauri), `LTCAI` CLI, host supervisor, and multi-stage Docker runtime guardrails, shipped real-time Workspace OS SSE (`/api/realtime/stream`, `useRealtimeStream.ts`) and `useAppBootstrap.ts` extraction, and strengthened v2 `.latticebrain` archive KDF alongside CI/Sidecar/Audit gates.

See [docs/releases/RELEASE_NOTES_v12.9.0.md](docs/releases/RELEASE_NOTES_v12.9.0.md).

# [v12.8.0 - Trusted Brain] (2026-11-16)

Unified `EmbeddingIndexContract`, `KnowledgeLifecycleState`, and granular `IngestStatus`; pushed workspace scoping before SQLite `LIMIT` with aligned vector search (`vector_search_aligned`) and bounded `SidecarCachePool`; enforced server-side `IsolatedRootSpec` and `resolve_agent_scope` fail-closed multi-user/tenant isolation; delivered encrypted `.latticebrain` archives and honest PostgreSQL `planned` reporting; modularized `frontend/src/features/`; and added PR retrieval gates and automated macOS DMG CI/CD builds.

See [docs/releases/RELEASE_NOTES_v12.8.0.md](docs/releases/RELEASE_NOTES_v12.8.0.md).

# [v12.7.0 - Fast Index] (2026-11-16)

Added the Pre-Tokenized BM25F Sidecar (`Bm25SidecarIndex`), SQLite `bm25_node_stats` persistence, and inverted posting-list & GraphRAG adjacency caches for O(1) lookup without query-time re-tokenization, alongside a product-first `README.md` overhaul and `v12.0.0+` release-history cleanup. One Door remains 422 operations / 41 families, worker remains 20 routes, and `GraphWriter` remains the sole graph writer.

See [docs/releases/RELEASE_NOTES_v12.7.0.md](docs/releases/RELEASE_NOTES_v12.7.0.md).

# [v12.6.0 - Hybrid Graph] (2026-11-15)

Delivered 3-way BM25F + Vector + Knowledge Graph RRF hybrid indexing and search, graph-aware agent skills and MCP tools, multi-scale subword `FallbackTextEmbedder` (#13.1), Pillow 13+ legacy cleanup (#13.2), Ubuntu 24.04/26.04 `t64` and Chromium CI resilience (#14.1), unauthenticated EmbeddingGemma 2 public mirrors and GGUF/MLX/ONNX fallbacks (#14.2), the modular `scripts/bump_version/` CLI (#15.1), and real-time SSE download speed and ETA visualization in `ProductFlow.tsx` and `Library.tsx` (#15.2). One Door remains 422 / 41, worker remains 20, and `GraphWriter` remains the sole writer.

See [docs/releases/RELEASE_NOTES_v12.6.0.md](docs/releases/RELEASE_NOTES_v12.6.0.md).

# [v12.5.0 - Deep Vector] (2026-11-11)

Integrated Google EmbeddingGemma 2 (768-dim) natively, introduced a hardware-aware (RAM/VRAM) PC-tailored embedding recommendation system with upfront embedding memory reservation, migrated to Pillow 13+ `get_flattened_data()`, upgraded GitHub Actions workflows to native Node 24, and expanded `scripts/bump_version.py` to synchronize 49 OpenAPI specification and fragment targets automatically. One Door remains 422 / 41, worker remains 20, and `GraphWriter` remains the sole writer.

See [docs/releases/RELEASE_NOTES_v12.5.0.md](docs/releases/RELEASE_NOTES_v12.5.0.md).

# [v12.4.0 - Sharp Weight] (2026-11-11)

Added Hugging Face-verified **Qwen 3.8 27B 4-bit** (`mlx-community/Qwen3.8-27B-4bit`) and **Gemma 4 26B A4B QAT / QAT-OptiQ 4-bit** (`mlx-community/gemma-4-26B-A4B-it-qat-4bit`, `mlx-community/gemma-4-26B-A4B-it-qat-OptiQ-4bit`) to recommendation/download catalogs and multi-engine routing, reached zero Python (`pillow>=13,<14`) and npm security advisories, added `model_engines` URL scheme validation, and scoped Tailwind CSS v4 source scanning. One Door remains 422 / 41, worker remains 20, and `GraphWriter` remains the sole writer.

See [docs/releases/RELEASE_NOTES_v12.4.0.md](docs/releases/RELEASE_NOTES_v12.4.0.md).

# [v12.3.0 - Live Wire] (2026-10-19)

Verified Hugging Face repository quantization tags (`Q4_0`, `MXFP4`) and GGUF routing, added auxiliary GGUF filtering, companion `--mmproj` loading, and shard completeness verification, strengthened small-model tool/MCP argument recovery and fenced-code HTML extraction, and verified `POST /tools/inspect_html` structure analysis and RFC 5987 UTF-8 downloads end to end on a real local model (`mlx-community/gemma-4-e2b-it-4bit`). One Door remains 422 / 41, worker remains 20, and `GraphWriter` remains the sole writer.

See [docs/releases/RELEASE_NOTES_v12.3.0.md](docs/releases/RELEASE_NOTES_v12.3.0.md).

# [v12.2.1 - True Count] (2026-08-29)

Search, skip, watch, and small-model summaries now count only what is actually present. One Door remains 422 / 41, worker remains 20, and `GraphWriter` remains the sole writer. Per-release note files were consolidated under `docs/releases/`.

See [docs/releases/RELEASE_NOTES_v12.2.1.md](docs/releases/RELEASE_NOTES_v12.2.1.md).

# [v12.2.0 - Small Voice] (2026-08-29)

2B-class local models can select MCP tools, skills, and native actions without strict JSON formatting, and the Home composer offers those workflows in plain language. One Door remains 422 / 41, worker remains 20, and the 12.1.0 Fast Path overlapping ingest pipeline remains intact.

See [docs/releases/RELEASE_NOTES_v12.2.0.md](docs/releases/RELEASE_NOTES_v12.2.0.md).

# [v12.1.0 - Fast Path] (2026-08-29)

Folder ingestion and vault-watch overlap file processing, each note calls `/worker/embed` in a single request, rebuild hashes outside the write lock, and Brains with 512+ rows attempt `hnsw+rescore` first whenever the worker seam is attached. One Door remains 422 / 41, worker remains 20. Fixed weekly `pip-audit` and Postgres CI failures on clean runs.

See [docs/releases/RELEASE_NOTES_v12.1.0.md](docs/releases/RELEASE_NOTES_v12.1.0.md).

# [v12.0.0 - Open House] (2026-08-18)

Regrouped the two largest Rust crates by domain — 43 files in `lattice-agent` into six groups (`kernel`/`parse`/`content`/`tools`/`surface`/`prompts`) and 31 flat modules in `lattice-platform` into seven domains (100 `git mv` moves, zero behavior changes). Added crate-local `ARCHITECTURE.md` maps, rewrote `docs/DEVELOPMENT.md` for contributor onboarding, and created `docs/ROADMAP.md`. Closed four previously documented gaps: live in-process restore via store epochs (no restart), consent-gated `/setup/install` against a server-derived allowlist, `POST /mcp` inside the OpenAPI contract (native mount), and `ltcai[pointer]` packaging. GraphRAG gained Korean particle awareness, section trees, typed edges, 33s → 0.26s unchanged re-indexing, ~66 → ~1,300 items/s draining, 991-item backlog drain in 15.3s, and opt-in `hnsw+rescore` (with `brute` remaining the default). GUIDED mode removed the JSON requirement for small models, letting Qwen 0.5B reach DONE on real files in 3.9s. Remaining limitations are documented candidly in the release notes.

See [docs/releases/RELEASE_NOTES_v12.0.0.md](docs/releases/RELEASE_NOTES_v12.0.0.md).

---

# Release Notes

This repository keeps public release history from **12.0.0 through 13.4.1** (the modern `v12.x`–`v13.x` Rust-host + domain-grouped architecture era). `SECURITY.md` supports the `13.x` and `12.x` eras, and this index follows the same boundary.

## Releases (12.0.0+)

- [v13.4.1 - Living Synthesis](docs/releases/RELEASE_NOTES_v13.4.1.md)
- [v13.4.0 - Living Synthesis](docs/releases/RELEASE_NOTES_v13.4.0.md)
- [v13.3.1 - Living Synthesis](docs/releases/RELEASE_NOTES_v13.3.1.md)
- [v13.3.0 - Living Synthesis](docs/releases/RELEASE_NOTES_v13.3.0.md)
- [v13.2.1 - Living Synthesis](docs/releases/RELEASE_NOTES_v13.2.1.md)
- [v13.2.0 - Living Synthesis](docs/releases/RELEASE_NOTES_v13.2.0.md)
- [v13.1.0 - Living Synthesis](docs/releases/RELEASE_NOTES_v13.1.0.md)
- [v13.0.0 - Sovereign Synthesis](docs/releases/RELEASE_NOTES_v13.0.0.md)
- [v12.10.0 - Grounded Self](docs/releases/RELEASE_NOTES_v12.10.0.md)
- [v12.9.0 - Semantic Guard](docs/releases/RELEASE_NOTES_v12.9.0.md)
- [v12.8.0 - Trusted Brain](docs/releases/RELEASE_NOTES_v12.8.0.md)
- [v12.7.0 - Fast Index](docs/releases/RELEASE_NOTES_v12.7.0.md)
- [v12.6.0 - Hybrid Graph](docs/releases/RELEASE_NOTES_v12.6.0.md)
- [v12.5.0 - Deep Vector](docs/releases/RELEASE_NOTES_v12.5.0.md)
- [v12.4.0 - Sharp Weight](docs/releases/RELEASE_NOTES_v12.4.0.md)
- [v12.3.0 - Live Wire](docs/releases/RELEASE_NOTES_v12.3.0.md)
- [v12.2.1 - True Count](docs/releases/RELEASE_NOTES_v12.2.1.md)
- [v12.2.0 - Small Voice](docs/releases/RELEASE_NOTES_v12.2.0.md)
- [v12.1.0 - Fast Path](docs/releases/RELEASE_NOTES_v12.1.0.md)
- [v12.0.0 - Open House](docs/releases/RELEASE_NOTES_v12.0.0.md)

## Canonical History

The canonical per-release history is maintained in:

- [RELEASE.md](RELEASE.md)
- [docs/CHANGELOG.md](docs/CHANGELOG.md)
