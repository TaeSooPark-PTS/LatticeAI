[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./RELEASE_NOTES.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](./docs/ko/RELEASE_NOTES.md)

# [v14.6.0 - Within Your Scope] (2026-11-28)

`brain_event` automations are now owner-scoped by default (`owner_scope: "self"`). A `"workspace"` scope fires on shared items owned by others, never on private or conversation items, and never across workspaces. Existing multi-user triggers that watched colleagues' uploads must be set to the **Workspace** scope; single-user installs are unaffected. New `source_types` and `title_contains` filters ship with a UI form. Fixed: dismissed or approved synthesis proposals no longer stay `pending` and block re-proposals (stale `effective_status`). Hugging Face revision pinning is now applied on download and load paths (`LATTICEAI_HF_REVISIONS` overrides); silent `TypeError` retries that dropped endpoint, token, or revision were removed. Models already downloaded are not re-pinned, and unpinned repositories still use `main`. The hooks catalog reports honest `executed`, `implementation`, and `not_executed_reason` fields.

See [docs/releases/RELEASE_NOTES_v14.6.0.md](docs/releases/RELEASE_NOTES_v14.6.0.md).

# [v14.5.0 - Living Automations] (2026-11-27)

Shipped live scheduled (`interval`) and event-driven (`brain_event`) automations backed by `workflow_designer/triggers/`, `GraphWriter` post-ingest dispatch, Review Center draft staging, and real `armed` rows on `GET /workflows/api/triggers`; added provenance/trust-aware "latest" ranking and active citation feedback demotion/boost in `lattice-core` and `lattice-retrieval`; added `POST /api/portability/vault` Markdown/Obsidian ZIP export with Settings UI (`431` paths / `474` operations); and added Capture Import Sources UI (Obsidian/Notion/Git/Mail), searchable Conversation Library with Markdown export, workflow run history/replay, export/stop fixes, bulk review actions, search filters, and one-click "not a good source" feedback.

See [docs/releases/RELEASE_NOTES_v14.5.0.md](docs/releases/RELEASE_NOTES_v14.5.0.md).

# [v14.4.0 - Measured Retrieval] (2026-11-27)

First release measured with the real EmbeddingGemma 2 and `BAAI/bge-reranker-v2-m3` weights. Fixed "latest" questions losing the answer to recency sort, workspace scope applied before the top-k cut, reranker warm-up at worker start, and once-only loading of the embedding model on concurrent cold requests; `pip install "ltcai[rag]"` now installs `torchvision`; re-measured and raised the v14.3.0 hash gates (previously measured with the vector lane inactive); and the 3D Living Brain label, WebGL context loss and hover picking updates.

See [docs/releases/RELEASE_NOTES_v14.4.0.md](docs/releases/RELEASE_NOTES_v14.4.0.md).

# [v14.3.0 - Temporal Synthesis] (2026-11-27)

Fixed post-send desktop chat collapse, `Cmd/Ctrl+K` "Today's briefing" cross-route opening, 2D Memory Graph "Show superseded" filtering, and command palette keyboard scrolling; upgraded chat retrieval with IDF/BM25F relevance ranking, true `as_of` valid-time evaluation and year-only parsing, stricter Self-RAG coverage abstention, and CI chat-path ratchet floors (`R@5 0.9194 → >=0.98`, `MRR 0.6122 → >=0.85`, `nDCG@10 0.6826 → >=0.87`, `temporal_ordering` `R@5 0.600 → >=0.99`, `MRR 0.267 → >=0.79`); and hardened multi-user privacy and integrity across share bundle export/import, scoped temporal prefilters, scope-before-`LIMIT` graph/brain/search queries, fail-closed Chronicle viewer filtering, and indexed supersede-on-ingest identity lookups.

See [docs/releases/RELEASE_NOTES_v14.3.0.md](docs/releases/RELEASE_NOTES_v14.3.0.md).

# [v14.2.0 - Temporal Synthesis] (2026-11-27)

Rebuilt retrieval around time: token, AST and parent-child chunking with contextual prefixes; EmbeddingGemma 2 through the new `auto` embedding default and optional `rag` extra; cross-encoder reranking with RRF fallback; two-stage temporal ranking with `sort_by`, time-intent prefilters and `SUPERSEDES` handling; history-aware query rewrite, MMR and a Self-RAG gate in chat; a 3D living brain view, fixed-viewport desktop shell and command palette; the `ltcai search` CLI; and fixes for a multi-user conversation leak, upload and folder ownership, and shared-bundle import. Real EmbeddingGemma 2 and reranker model loading was not verified in the build environment.

See [docs/releases/RELEASE_NOTES_v14.2.0.md](docs/releases/RELEASE_NOTES_v14.2.0.md).

# [v14.1.2 - Living Synthesis] (2026-11-25)

Enforced a repository-wide zero-comments standard across every tracked source language: stripped all human-written inline comments, block comments, docstrings, JSDoc, and suppression directives while preserving only machine-parsed shebangs and `/// <reference ... />` directives; resolved every surfaced Ruff, mypy, TypeScript, and Rust Clippy issue directly in source code without weakening any global lint or type-check configuration; wired `npm run check:no-comments` (`scripts/check_no_comments.mjs`) into `npm run lint` and `.github/workflows/ci.yml`; and documented the permanent no-comments policy in `AGENTS.md`, `CONTRIBUTING.md`, and Korean mirrors.

See [docs/releases/RELEASE_NOTES_v14.1.2.md](docs/releases/RELEASE_NOTES_v14.1.2.md).

# [v14.1.1 - Living Synthesis] (2026-11-25)

Completed the zero-technical-debt pass across Python formatting, McCabe complexity, server i18n ratcheting, and container hygiene: formatted all 219 Python files with `ruff format .` in a dedicated commit (`55ba57c381a433066fd94434c3a6250c4a1beb59`) recorded in `.git-blame-ignore-revs` and enforced `ruff format --check .` in `npm run lint:python` / CI; decomposed `create_models_router` and `create_worker_compute_router`, removed all remaining `"C901"` per-file ignores in `pyproject.toml` (`max-complexity = 20` enforced globally with zero exemptions), and split `/worker/extract` tests into `tests/unit/test_worker_compute_extract.py`; closed `scripts/check_server_i18n.mjs` to `0 unclaimed` modules by explicitly registering `worker_compute_render.py` and `worker_resilience.py` in `NON_ROUTER_HELPERS` and failing closed on any future unclassified `latticeai/api/*.py` module; and added explicit `--chown=lattice:lattice --chmod=0755` to the `lattice-host` runtime copy in `Dockerfile`.

See [docs/releases/RELEASE_NOTES_v14.1.1.md](docs/releases/RELEASE_NOTES_v14.1.1.md).

# [v14.1.0 - Living Synthesis] (2026-11-25)

Resolved critical v14.0.0 regressions and hardened full-stack static analysis and documentation: unified Rust ↔ Python timestamps onto offset-free UTC (`YYYY-MM-DDTHH:MM:SS`) with multi-timezone regression tests; pinned production Tauri builds strictly to port `4825` via `PortPolicy::PinnedProduction` with fail-fast diagnostics; narrowed all broad Python `except Exception` catches, enabled Ruff `"BLE"`, lowered McCabe `max-complexity` to `20`, and minimized Bandit ignores; introduced `rust/lattice-auth/src/messages.rs` + `CsrfReason` and direct `BackendStatus` returns for infallible Tauri commands; enabled TypeScript 7 `"noUncheckedIndexedAccess": true` across 68 frontend files (457 errors fixed at 100% Vitest coverage across 113 files / 1,852 tests); expanded Playwright `visual` smoke CI to run on pull requests as well as `main`; and refreshed all repository Markdown docs and Korean mirrors to `v14.1.0`.

See [docs/releases/RELEASE_NOTES_v14.1.0.md](docs/releases/RELEASE_NOTES_v14.1.0.md).

# [v14.0.0 - Living Synthesis] (2026-11-25)

Delivered full-stack security, concurrency, desktop/extension, and CI/CD hardening: versioned `$scrypt$` `Result`-based hashing, bounded rate limiting with unified HTTP `429` + `Retry-After` and English CSRF denials in `lattice-auth`; pure-Rust UTC clock eliminating all `unsafe` `libc` calls in `lattice-core`; non-blocking `spawn_blocking` filesystem/session I/O, bounded HNSW/cloud-budget caches, fail-closed proxy client, and `EnvOverlayProbe` across Rust crates; platform-guarded MLX extras, `ContextVar` LLM router DI, and re-enabled Ruff security rules in Python; strict TypeScript 7 compiler flags and modular `AppRouteView.tsx` at 100% Vitest coverage; serialized Tauri gateway lifecycle with pinned `127.0.0.1:4825` CSP/capabilities; hardened VS Code and browser extensions; real `windows-latest` Rust CI, `macos-latest` Tauri CI, and digest-pinned multi-stage Docker smoke CI; and modernized `14.0.0` architecture documentation.

See [docs/releases/RELEASE_NOTES_v14.0.0.md](docs/releases/RELEASE_NOTES_v14.0.0.md).

# [v13.9.0 - Living Synthesis] (2026-11-24)

Resolved all six PR #69 security and integrity findings (strict workspace-scoped integrity repairs with admin-gated global mode, shared `Store` single-writer routing for all Brain mutations, workspace ownership verification for every node/edge/correction/merge target, Windows handle-pinned component-by-component TOCTOU-safe I/O, claim-level grounding & fabricated-citation penalties in real-model RAG eval, and posture-gating `/host/diagnostics`), folded all 16 Brain extension endpoints into canonical `brain_api::MOUNTED` and `memory_brain.json` with regenerated `frontend/src/api/openapi.ts`, shipped five dedicated interactive React Brain Governance UI panels with Vitest tests, and added `.github/workflows/nightly-rag-eval.yml`.

See [docs/releases/RELEASE_NOTES_v13.9.0.md](docs/releases/RELEASE_NOTES_v13.9.0.md).

# [v13.8.0 - Living Synthesis] (2026-11-24)

Delivered the complete Digital Brain Platform hardening roadmap (P0–P3): authentic RAG evaluation separating deterministic fixtures from live local/cloud models with a versioned 320-query dataset (`datasets/rag_eval_v1.json`), unified KG integrity checker with dry-run blast-radius previews, auto-backup, and fault-injection tests, homonym-safe fuzzy Entity Resolution with persistent audit trail and selective rollback, OS-level fd-confined `O_NOFOLLOW` TOCTOU-safe sandbox & PII/secret-redacted cloud egress, Embedding Model Registry with shadow re-indexing/cutover/rollback, least-privilege 5-role Multi-Agent orchestrator, Knowledge Learning Loop, incremental watch move/pause delivery, `Origin Country → Vendor → Execution` Model Library UX, desktop health-watchdog + redacted `/host/diagnostics`, topic clustering, knowledge gap detection, `Modality::Video`, audit retention pruning, and offboarding revocation receipts.

See [docs/releases/RELEASE_NOTES_v13.8.0.md](docs/releases/RELEASE_NOTES_v13.8.0.md).

# [v13.7.2 - Living Synthesis] (2026-11-24)

Migrated all Rust workspace callers (`lattice-host`, `lattice-chat`, `lattice-platform`, `lattice-agent`, `lattice-retrieval`, `src-tauri`, and all integration tests) to canonical domain module paths (`lattice_platform::<domain>::*`, `lattice_agent::<group>::*`), removed the obsolete flat crate-root `pub use` compatibility maps from `lattice-agent` and `lattice-platform`, and removed the last unused `lattice_chat::crate_ready()` scaffold stub.

See [docs/releases/RELEASE_NOTES_v13.7.2.md](docs/releases/RELEASE_NOTES_v13.7.2.md).

# [v13.7.1 - Living Synthesis] (2026-11-24)

Purged all remaining legacy `forward()` fallbacks and dead compatibility scaffolding in `lattice-ingest`, `lattice-platform`, and `lattice-retrieval` (returning HTTP `503` consistently when `GraphWriter` is unwired), added native `source: "git"` and unwired-`GraphWriter` 503 integration tests in `local_files_replay.rs`, and pruned pre-`13.7.0` review/plan/legacy docs while preserving all benchmark documents.

See [docs/releases/RELEASE_NOTES_v13.7.1.md](docs/releases/RELEASE_NOTES_v13.7.1.md).

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

This repository keeps public release history from **13.0.0 through 14.6.0** (the modern Rust-host + domain-grouped architecture era). `SECURITY.md` supports the `14.x` and `13.x` eras, and this index follows the same boundary.

## Releases (13.0.0+)

- [v14.4.0 - Measured Retrieval](docs/releases/RELEASE_NOTES_v14.4.0.md)
- [v14.3.0 - Temporal Synthesis](docs/releases/RELEASE_NOTES_v14.3.0.md)
- [v14.2.0 - Temporal Synthesis](docs/releases/RELEASE_NOTES_v14.2.0.md)
- [v14.1.2 - Living Synthesis](docs/releases/RELEASE_NOTES_v14.1.2.md)
- [v14.1.1 - Living Synthesis](docs/releases/RELEASE_NOTES_v14.1.1.md)
- [v14.1.0 - Living Synthesis](docs/releases/RELEASE_NOTES_v14.1.0.md)
- [v14.0.0 - Living Synthesis](docs/releases/RELEASE_NOTES_v14.0.0.md)
- [v13.9.0 - Living Synthesis](docs/releases/RELEASE_NOTES_v13.9.0.md)
- [v13.8.0 - Living Synthesis](docs/releases/RELEASE_NOTES_v13.8.0.md)
- [v13.7.2 - Living Synthesis](docs/releases/RELEASE_NOTES_v13.7.2.md)
- [v13.7.1 - Living Synthesis](docs/releases/RELEASE_NOTES_v13.7.1.md)
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
