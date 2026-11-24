[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./RELEASE_NOTES_v13.7.1.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](../ko/releases/RELEASE_NOTES_v13.7.1.md)

# Lattice AI v13.7.1 — Living Synthesis (2026-11-24)

> **Status: historical** — archived release note for v13.7.1 (Legacy Fallback Purge, Native Git Interop Coverage & Pre-13.7 Doc Cleanup).

Removes all remaining dead `forward()` fallbacks and legacy compatibility scaffolding from `lattice-ingest`, `lattice-platform`, and `lattice-retrieval` so every ingestion endpoint consistently returns HTTP `503 Service Unavailable` when `GraphWriter` is unwired; adds an end-to-end `source: "git"` integration test and unwired-`GraphWriter` 503 contract test in `local_files_replay.rs`; and prunes pre-`13.7.0` review, plan, and legacy-compatibility docs while preserving all benchmark documents.

## What Changed

- **Legacy `forward()` Fallback & Compatibility Purge (`rust/lattice-ingest`, `rust/lattice-platform`, `rust/lattice-retrieval`):**
  - Removed dead `forward()` fallbacks from `POST /api/ingestion/jobs/:job_id/resume`, `POST /api/ingestion/folder`, `POST /knowledge-graph/local/index`, and `POST /api/browser/read-url`, routing `read-url` and `local/index` natively through `GraphWriter` / `NoteIngestor` and returning HTTP `503` consistently when `GraphWriter` is absent.
  - Deleted dead `local_files_api::forward`, `browser_api::forward`, `local_files_api::require_seam`, `local_files_api::http::seam_error`, `search_api::require_seam`, and the unused `lattice_platform::crate_ready()` scaffold stub.
- **Git Interop & 503 Contract Integration Tests (`rust/lattice-ingest/tests/local_files_replay.rs`):**
  - Added an integration test initializing a temporary `git init` repository with sample files and commits, verifying both `dry_run: true` and live `source: "git"` ingestion through `GraphWriter`.
  - Added `unwired_graph_writer_returns_503_consistently` verifying HTTP `503` across `/api/ingestion/folder`, `/api/ingestion/obsidian`, `/api/ingestion/interop`, and `/knowledge-graph/local/index`.
- **Pre-`13.7.0` Review/Plan/Legacy Documentation Cleanup (`docs/`, `docs/ko/`):**
  - Deleted pre-`13.7.0` review reports (`CODE_REVIEW_2026-11-20.md`, `reviews/CODEBASE_REVIEW_AND_IMPROVEMENT_ROADMAP.md`), obsolete `v11.x` migration plans, and `LEGACY_COMPATIBILITY.md` (and Korean mirrors) while explicitly preserving all benchmark documents (`ADAPTIVE_HARNESS_BENCHMARK.md`, `BENCHMARKS.md`, `PERFORMANCE.md`).
  - Synchronized all canonical EN/KO documentation and exact artifact references to `13.7.1`.

## Exact Build Artifacts

- `dist/ltcai-13.7.1-py3-none-any.whl`
- `dist/ltcai-13.7.1.tar.gz`
- `ltcai-13.7.1.tgz`
- `dist/ltcai-13.7.1.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_13.7.1_aarch64.dmg`
