[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./RELEASE_NOTES.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](./docs/ko/RELEASE_NOTES.md)

# [v13.7.0 - Living Synthesis] (2026-11-24)

Implemented native Rust `GraphWriter` Obsidian vault (`[[wikilink]]` `LINKS_TO` edges) and Notion/Git/Mail interop bridges in `lattice-ingest`, migrated Vite 8 and Vitest 5 configs to native ESM `.mts`, pruned pre-`v13.0.0` release notes (`v13.0.0+` floor), and modernized `ARCHITECTURE.md` and `docs/ko/ARCHITECTURE.md` to `13.7.0`.

See [docs/releases/RELEASE_NOTES_v13.7.0.md](docs/releases/RELEASE_NOTES_v13.7.0.md).

# [v13.6.0 - Living Synthesis] (2026-11-24)

Eliminated the last TypeScript 5 dependency (`openapi-typescript@7.13.0`, `openapi-ts5` alias, `scripts/run_openapi_typescript.mjs`, and `overrides`) by introducing a zero-dependency native TypeScript 7 (`tsgo`) OpenAPI declaration generator (`scripts/generate_openapi_types.mjs`), and split the five monolithic `tests/unit/test_cov_p1b_remaining*.py` coverage bundles into nine domain-focused unit test modules backed by `tests/unit/_runtime_test_fakes.py`.

See [docs/releases/RELEASE_NOTES_v13.6.0.md](docs/releases/RELEASE_NOTES_v13.6.0.md).

# [v13.5.0 - Living Synthesis] (2026-11-24)

Delivered one-click cross-platform engine installation and **Qwen3.8 27B 4-bit** setup across macOS (`omlx` Homebrew + zero-Homebrew universal2 wheel fallback, `local_mlx`), Windows (`winget` Ollama / llama.cpp / LM Studio + `pip` fallback), and Linux (Ollama install script + `pip` / `vllm`) with real-time available-RAM pressure tuning, upgraded the workspace typechecker to **TypeScript 7.0.2 (`tsgo`)** and `scripts/check_i18n_literals.mjs` to `@babel/parser`, and refactored `engine_adapters.py` and `model_loading.py` while removing dead code and obsolete tests.

See [docs/releases/RELEASE_NOTES_v13.5.0.md](docs/releases/RELEASE_NOTES_v13.5.0.md).

# [v13.4.2 - Living Synthesis] (2026-11-24)

Added first-class **oMLX (`jundot/omlx`)** engine integration with hardware-tuned tiered RAM/SSD KV caching and elevated **`mlx-community/Qwen3.8-27B-4bit`** to recommended and downloadable status on 32GB+ Apple Silicon Macs.

See [docs/releases/RELEASE_NOTES_v13.4.2.md](docs/releases/RELEASE_NOTES_v13.4.2.md).

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

---

# Release Notes

This repository keeps public release history from **13.0.0 through 13.7.0** (the modern `v13.x` Rust-host + domain-grouped architecture era). `SECURITY.md` supports the `13.x` era, and this index follows the same boundary.

## Releases (13.0.0+)

- [v13.5.0 - Living Synthesis](docs/releases/RELEASE_NOTES_v13.5.0.md)
- [v13.4.2 - Living Synthesis](docs/releases/RELEASE_NOTES_v13.4.2.md)
- [v13.4.1 - Living Synthesis](docs/releases/RELEASE_NOTES_v13.4.1.md)
- [v13.7.0 - Living Synthesis](docs/releases/RELEASE_NOTES_v13.7.0.md)
- [v13.6.0 - Living Synthesis](docs/releases/RELEASE_NOTES_v13.6.0.md)
- [v13.5.0 - Living Synthesis](docs/releases/RELEASE_NOTES_v13.5.0.md)
- [v13.4.2 - Living Synthesis](docs/releases/RELEASE_NOTES_v13.4.2.md)
- [v13.4.1 - Living Synthesis](docs/releases/RELEASE_NOTES_v13.4.1.md)
- [v13.4.0 - Living Synthesis](docs/releases/RELEASE_NOTES_v13.4.0.md)
- [v13.3.1 - Living Synthesis](docs/releases/RELEASE_NOTES_v13.3.1.md)
- [v13.3.0 - Living Synthesis](docs/releases/RELEASE_NOTES_v13.3.0.md)
- [v13.2.1 - Living Synthesis](docs/releases/RELEASE_NOTES_v13.2.1.md)
- [v13.2.0 - Living Synthesis](docs/releases/RELEASE_NOTES_v13.2.0.md)
- [v13.1.0 - Living Synthesis](docs/releases/RELEASE_NOTES_v13.1.0.md)
- [v13.0.0 - Sovereign Synthesis](docs/releases/RELEASE_NOTES_v13.0.0.md)

## Canonical History

The canonical per-release history is maintained in:

- [RELEASE.md](RELEASE.md)
- [docs/CHANGELOG.md](docs/CHANGELOG.md)
