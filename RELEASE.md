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

## v14.6.0 — Within Your Scope (2026-11-28)

`brain_event` automations are now owner-scoped by default (`owner_scope: "self"`). A `"workspace"` scope fires on shared items owned by others, never on private or conversation items, and never across workspaces. Existing multi-user triggers that watched colleagues' uploads must be set to the **Workspace** scope; single-user installs are unaffected. New `source_types` and `title_contains` filters ship with a UI form. Fixed: dismissed or approved synthesis proposals no longer stay `pending` and block re-proposals (stale `effective_status`). Hugging Face revision pinning is now applied on download and load paths (`LATTICEAI_HF_REVISIONS` overrides); silent `TypeError` retries that dropped endpoint, token, or revision were removed. Models already downloaded are not re-pinned, and unpinned repositories still use `main`. The hooks catalog reports honest `executed`, `implementation`, and `not_executed_reason` fields. UI: the command palette opens conversation hits and has a top-bar button; search results are actionable; a memory manager (view, add, search, forget); an honest "Save to Brain" status; a document library with filter and preview; run history filters including cancelled, show more, and an error state. Tests add an HNSW churn recall test. Known gap: private documents inside a workspace are not implemented (see docs/ROADMAP.md). This release does not add private uploads and makes no such claim.

Release notes: [docs/releases/RELEASE_NOTES_v14.6.0.md](docs/releases/RELEASE_NOTES_v14.6.0.md).

Build artifacts: `dist/ltcai-14.6.0-py3-none-any.whl`, `dist/ltcai-14.6.0.tar.gz`, `ltcai-14.6.0.tgz`, `dist/ltcai-14.6.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_14.6.0_aarch64.dmg`.

## v14.5.0 — Living Automations (2026-11-27)

Enabled `interval` and `brain_event` automations now arm and execute via `workflow_designer/triggers/`, dispatch on non-duplicate `GraphWriter` ingests, stage drafts in Review Center, and return real `armed` rows on `GET /workflows/api/triggers`. Added provenance/trust-aware "latest" ranking (`temporal/trust.rs`, `frontmatter.rs`, legacy-schema-safe SQLite reads) and active citation feedback ranking adjustments (`correction_signals.rs`, `temporal/signals.rs`). Shipped `POST /api/portability/vault` Markdown/Obsidian ZIP export with Settings UI (`431` paths / `474` operations), unified Capture Import Sources UI (Obsidian/Notion/Git/Mail), searchable Conversation Library with Markdown export, workflow run history/replay, export-download and stop-button fixes, bulk review actions, date/historical search filters, and one-click "not a good source" feedback.

Release notes: [docs/releases/RELEASE_NOTES_v14.5.0.md](docs/releases/RELEASE_NOTES_v14.5.0.md).

Build artifacts: `dist/ltcai-14.5.0-py3-none-any.whl`, `dist/ltcai-14.5.0.tar.gz`, `ltcai-14.5.0.tgz`, `dist/ltcai-14.5.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_14.5.0_aarch64.dmg`.

## v14.4.0 — Measured Retrieval (2026-11-27)

First release measured with the real EmbeddingGemma 2 and `BAAI/bge-reranker-v2-m3` weights. Fixed the defects that measurement exposed: "latest" questions keep the answer (recency only reorders the answer), workspace scope is applied before the top-k cut in the no-worker vector lane, hybrid search, memory recall and the HNSW index, `LATTICEAI_RERANK_CANDIDATES` up to 64 works, the reranker is warmed at worker start, and concurrent cold requests load the embedding model once. `pip install "ltcai[rag]"` installs `torchvision`. The v14.3.0 hash gates, which were measured with the vector lane inactive, are re-measured with the seeding fixed and raised, and the chat-path and nightly evaluations use the product retriever. 3D Living Brain labels follow the label mode, WebGL context loss pauses and resumes rendering, and hover picking runs at most once per frame.

Release notes: [docs/releases/RELEASE_NOTES_v14.4.0.md](docs/releases/RELEASE_NOTES_v14.4.0.md).

Build artifacts: `dist/ltcai-14.4.0-py3-none-any.whl`, `dist/ltcai-14.4.0.tar.gz`, `ltcai-14.4.0.tgz`, `dist/ltcai-14.4.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_14.4.0_aarch64.dmg`.

## v14.3.0 — Temporal Synthesis (2026-11-27)

Fixed post-send desktop chat collapse, `Cmd/Ctrl+K` "Today's briefing" cross-route drawer opening, 2D Memory Graph "Show superseded" filtering, and command palette keyboard scrolling; upgraded chat retrieval with IDF/BM25F relevance ranking, true `as_of` point-in-time valid-time filtering and year-only parsing, stricter Self-RAG coverage abstention, and CI chat-path ratchet floors (`R@5 0.9194 → >=0.98`, `MRR 0.6122 → >=0.85`, `nDCG@10 0.6826 → >=0.87`, `temporal_ordering` `R@5 0.600 → >=0.99`, `MRR 0.267 → >=0.79`); and closed six multi-user privacy and integrity findings across share bundle export/import, scoped temporal prefilters, scope-before-`LIMIT` graph/brain/search queries, fail-closed Chronicle viewer filtering, and indexed supersede-on-ingest identity lookups.

Release notes: [docs/releases/RELEASE_NOTES_v14.3.0.md](docs/releases/RELEASE_NOTES_v14.3.0.md).

Build artifacts: `dist/ltcai-14.3.0-py3-none-any.whl`, `dist/ltcai-14.3.0.tar.gz`, `ltcai-14.3.0.tgz`, `dist/ltcai-14.3.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_14.3.0_aarch64.dmg`.

## v14.2.0 — Temporal Synthesis (2026-11-27)

Rebuilt retrieval around time: token, AST and parent-child chunking with contextual prefixes; EmbeddingGemma 2 through the new `auto` embedding default and optional `rag` extra; cross-encoder reranking with RRF fallback; two-stage temporal ranking with `sort_by`, time-intent prefilters and `SUPERSEDES` handling; history-aware query rewrite, MMR and a Self-RAG gate in chat; a 3D living brain view, fixed-viewport desktop shell and command palette; the `ltcai search` CLI; and fixes for a multi-user conversation leak, upload and folder ownership, and shared-bundle import. Real EmbeddingGemma 2 and reranker model loading was not verified in the build environment.

Release notes: [docs/releases/RELEASE_NOTES_v14.2.0.md](docs/releases/RELEASE_NOTES_v14.2.0.md).

Build artifacts: `dist/ltcai-14.2.0-py3-none-any.whl`, `dist/ltcai-14.2.0.tar.gz`, `ltcai-14.2.0.tgz`, `dist/ltcai-14.2.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_14.2.0_aarch64.dmg`.

## v14.1.2 — Living Synthesis (2026-11-25)

Enforced a repository-wide zero-comments standard across all tracked source files: stripped all human-written inline comments, block comments, docstrings, JSDoc, and suppression directives (`# noqa`, `# type: ignore`, `// @ts-expect-error`, `// eslint-disable`, `/* @vite-ignore */`, etc.) while preserving only machine-parsed directives (`#!/usr/bin/env ...` and `/// <reference ... />`); resolved every surfaced lint, type-check, and Clippy issue directly in source code without weakening any global config; added `npm run check:no-comments` (`scripts/check_no_comments.mjs`) to `npm run lint` and `.github/workflows/ci.yml`; and documented the permanent no-comments policy in `AGENTS.md`, `CONTRIBUTING.md`, and their Korean mirrors.

Release notes: [docs/releases/RELEASE_NOTES_v14.1.2.md](docs/releases/RELEASE_NOTES_v14.1.2.md).

Build artifacts: `dist/ltcai-14.1.2-py3-none-any.whl`, `dist/ltcai-14.1.2.tar.gz`, `ltcai-14.1.2.tgz`, `dist/ltcai-14.1.2.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_14.1.2_aarch64.dmg`.

## v14.1.1 — Living Synthesis (2026-11-25)

Completed the zero-technical-debt pass across Python formatting, complexity enforcement, server i18n ratcheting, and container hygiene: formatted all 219 Python files with `ruff format .` in a dedicated format-only commit (`55ba57c381a433066fd94434c3a6250c4a1beb59`), recorded it in `.git-blame-ignore-revs` for clean `git blame`, and wired `ruff format --check .` into `npm run lint:python` and CI; decomposed `create_models_router` (`latticeai/api/models.py`) and `create_worker_compute_router` (`latticeai/api/worker_compute.py`) into focused module-level helpers, removed all remaining `"C901"` per-file ignores in `pyproject.toml` so McCabe `max-complexity = 20` is enforced globally with zero exemptions, and split `/worker/extract` tests into `tests/unit/test_worker_compute_extract.py` under the 1,000-line ceiling; audited and registered `worker_compute_render.py` and `worker_resilience.py` in `NON_ROUTER_HELPERS` and tightened `scripts/check_server_i18n.mjs` to fail closed (`exit 1`) on any future unclaimed `latticeai/api/*.py` module (`0 unclaimed`); and added explicit `--chown=lattice:lattice --chmod=0755` to the `lattice-host` binary copy in `Dockerfile`.

Release notes: [docs/releases/RELEASE_NOTES_v14.1.1.md](docs/releases/RELEASE_NOTES_v14.1.1.md).

Build artifacts: `dist/ltcai-14.1.1-py3-none-any.whl`, `dist/ltcai-14.1.1.tar.gz`, `ltcai-14.1.1.tgz`, `dist/ltcai-14.1.1.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_14.1.1_aarch64.dmg`.

## v14.1.0 — Living Synthesis (2026-11-25)

Resolved both critical v14.0.0 regressions and completed full-stack static-analysis and documentation hardening: unified Rust ↔ Python timestamps onto offset-free UTC (`YYYY-MM-DDTHH:MM:SS`) across `clock.rs`, `lattice_brain/utils.py`, and `_kg_fsutil.py` with zero-skew regression tests under `Asia/Seoul`, `America/New_York`, and `UTC`; introduced `PortPolicy` (`PinnedProduction` vs. `AllowDynamicFallback`) in `src-tauri` so production desktop builds fail fast on port `4825` conflicts instead of silently drifting off the pinned CSP/capability origin; narrowed all broad `except Exception` / `# noqa: BLE001` catches across Python, enabled Ruff `"BLE"` globally, lowered McCabe `max-complexity` from `30` to `20`, and minimized Bandit per-file ignores while keeping production `S608` active; moved CSRF error strings into `rust/lattice-auth/src/messages.rs` with a structured `CsrfReason` enum and simplified infallible Tauri commands (`backend_status`, `shutdown_backend`) to return `BackendStatus` directly; enabled TypeScript 7 `"noUncheckedIndexedAccess": true` across 68 frontend files (457 errors resolved at 100% Vitest coverage across 113 files / 1,852 tests); enabled the Playwright `visual` smoke job on pull requests as well as pushes to `main`; and refreshed all canonical, reference, and Korean mirror Markdown documentation to `v14.1.0`.

Release notes: [docs/releases/RELEASE_NOTES_v14.1.0.md](docs/releases/RELEASE_NOTES_v14.1.0.md).

Build artifacts: `dist/ltcai-14.1.0-py3-none-any.whl`, `dist/ltcai-14.1.0.tar.gz`, `ltcai-14.1.0.tgz`, `dist/ltcai-14.1.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_14.1.0_aarch64.dmg`.

## v14.0.0 — Living Synthesis (2026-11-25)

Completed full-stack v14.0.0 security, concurrency, packaging, and CI/CD hardening: upgraded `lattice-auth` to versioned `$scrypt$` `Result`-based hashing, bounded rate limiting with unified HTTP `429` + `Retry-After` responses, and standardized English CSRF denials; eliminated all `unsafe` `libc` clock calls from `lattice-core` with pure-Rust UTC calendar arithmetic; offloaded blocking filesystem/session I/O in `lattice-ingest`/`lattice-chat` to `spawn_blocking`, bounded `cloud/budget` and `NativeHnswIndex` caches, hardened `lattice-jobs` partial-drain stats and `/host/jobs` loopback/Bearer guards, and made `lattice-host` proxy creation fail-closed without `std::env::set_var`; platform-guarded Apple Silicon MLX extras and re-enabled Ruff security rules in `pyproject.toml`; enabled strict TypeScript 7 compiler flags and extracted `AppRouteView.tsx` at 100% Vitest coverage; serialized Tauri gateway lifecycle with pinned `127.0.0.1:4825` CSP/capabilities; hardened VS Code and browser extensions; added real `windows-latest` Rust workspace tests, `macos-latest` Tauri tests, and digest-pinned multi-stage `Dockerfile` smoke checks in CI; and modernized all architecture documentation (`ARCHITECTURE.md`, `docs/architecture.md`, Korean mirrors, and crate maps) to `14.0.0`.

Release notes: [docs/releases/RELEASE_NOTES_v14.0.0.md](docs/releases/RELEASE_NOTES_v14.0.0.md).

Build artifacts: `dist/ltcai-14.0.0-py3-none-any.whl`, `dist/ltcai-14.0.0.tar.gz`, `ltcai-14.0.0.tgz`, `dist/ltcai-14.0.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_14.0.0_aarch64.dmg`.

## v13.9.0 — Living Synthesis (2026-11-24)

Resolved all six PR #69 security and integrity findings (strict workspace-scoped integrity checks and repairs with admin-gated global mode, routing all Brain writes through the shared `Store` single-writer pool, workspace ownership validation for all node/edge/correction/merge targets, Windows handle-pinned component-by-component TOCTOU-safe I/O, claim-level grounding & fabricated-citation penalty gate in `run_real_model_rag_eval.py`, and posture-gating `/host/diagnostics`), folded all 16 Digital Brain extension routes directly into `brain_api::MOUNTED` and `memory_brain.json` with regenerated `frontend/src/api/openapi.ts`, built five dedicated interactive React Brain Governance panels with Vitest coverage, and wired `.github/workflows/nightly-rag-eval.yml`.

Release notes: [docs/releases/RELEASE_NOTES_v13.9.0.md](docs/releases/RELEASE_NOTES_v13.9.0.md).

Build artifacts: `dist/ltcai-13.9.0-py3-none-any.whl`, `dist/ltcai-13.9.0.tar.gz`, `ltcai-13.9.0.tgz`, `dist/ltcai-13.9.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_13.9.0_aarch64.dmg`.

## v13.8.0 — Living Synthesis (2026-11-24)

Completed the full Digital Brain Platform hardening roadmap (P0–P3): authentic RAG evaluation separating deterministic fixtures from live local/cloud model runs with a versioned 320-query benchmark dataset (`datasets/rag_eval_v1.json`), unified Knowledge Graph integrity checker & fault-injection repair engine (`lattice-core::integrity`), type-scoped/fuzzy Entity Resolution with persistent audit trail & selective rollback (`entity_resolution_ext`), OS-level fd-confined `O_NOFOLLOW` TOCTOU-safe sandbox (`safe_io.rs`) & PII/secret-redacted cloud egress, Embedding Model Registry with shadow re-indexing/cutover/rollback, least-privilege 5-role Multi-Agent orchestrator (`multi_agent.rs`), Knowledge Learning Loop (`learning_loop.rs`), incremental OS watch move/pause delivery, `Origin Country → Vendor → Execution` Model Library UX, worker health-watchdog + redacted `/host/diagnostics`, topic clusters/gaps, `Modality::Video`, audit retention pruning, and offboarding revocation receipts.

Release notes: [docs/releases/RELEASE_NOTES_v13.8.0.md](docs/releases/RELEASE_NOTES_v13.8.0.md).

Build artifacts: `dist/ltcai-13.8.0-py3-none-any.whl`, `dist/ltcai-13.8.0.tar.gz`, `ltcai-13.8.0.tgz`, `dist/ltcai-13.8.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_13.8.0_aarch64.dmg`.

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
