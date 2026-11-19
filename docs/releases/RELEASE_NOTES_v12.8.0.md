[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./RELEASE_NOTES_v12.8.0.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](../ko/releases/RELEASE_NOTES_v12.8.0.md)

# Lattice AI 12.8.0 — Trusted Brain

> **Status: historical** — point-in-time release note.

2026-11-16

**12.8.0 Trusted Brain** completes end-to-end reliability, security isolation, retrieval precision, Brain portability, frontend maintainability, and release automation upgrades across the Rust host and Python worker.

## What landed

- **Unified Embedding Contract, Granular Ingest Status, & Knowledge Lifecycle:**
  - `rust/lattice-core/src/embedding_contract.rs`, `rust/lattice-core/src/graph_write/lifecycle.rs`, `latticeai/core/embedding_providers/`, and `latticeai/worker_app.py`: Enforce a single canonical `EmbeddingIndexContract` (`provider_id`, `model_id`, `dim`, `normalize`, `dtype`, `version`) with explicit mismatch detection, unified `GraphWriter` deletion cascades, granular `IngestStatus` progress reporting, and `KnowledgeLifecycleState` (`active`, `superseded`, `archived`, `deleted`) provenance tracking.
- **SQLite Pre-LIMIT Retrieval Scoping, Vector Alignment, & Bounded Sidecar Caching:**
  - `rust/lattice-retrieval/src/bm25/`, `vector_alignment.rs`, and `rag_eval.rs`: Push workspace scope and active lifecycle filters into SQL queries before `LIMIT`, validate `vector_search_aligned` dimensionality against the active contract, replace per-query subword retokenization with cached `Bm25SidecarIndex` lookups inside `SidecarCachePool` (`RwLock<Arc<Bm25SidecarIndex>>`, bounded by `DEFAULT_MAX_CACHE_BYTES`), and add retrieval evaluation gates.
- **Fail-Closed Multi-User, Workspace, & Tenant Gateway Isolation:**
  - `rust/lattice-agent/src/tools/sandbox.rs`, `rust/lattice-host/src/gateway/{isolation.rs,agent_bind.rs}`, `rust/lattice-auth/src/enterprise.rs`, and `rust/lattice-host/tests/agent_isolation_e2e.rs`: Derive confined per-user/tenant/workspace `Workspace` roots server-side via `IsolatedRootSpec` and `resolve_agent_scope`, rejecting cross-workspace/cross-tenant spoofing and path/symlink escapes across `/agent`, `/tools/*`, `/mcp/*`, `/skills/*`, `/cu/*`, `/api/automation/*`, and `/api/browser/*` with security audit events.
- **Encrypted `.latticebrain` Archive Portability & Honest PostgreSQL Status:**
  - `rust/lattice-platform/src/knowledge/portability/{archive.rs,crypto.rs,postgres.rs}` and `rust/lattice-platform/tests/portability_archive.rs`: Deliver AEAD-encrypted `.latticebrain` create/inspect/verify/import/restore workflows with symlink and path-traversal rejection, recipient X25519 share export/accept flows, automatic pre-restore backup, and honest `planned` reporting for PostgreSQL migration/Docker orchestration.
- **Frontend Feature Modularization & Repository Cleanup:**
  - Refactored `Library.tsx`, `System.tsx`, and `Capture.tsx` into focused modules under `frontend/src/features/` while preserving 100% Vitest coverage across 108 files and keeping initial JS bundle within budget, and removed stale `scratchpad/` scratch artifacts.
- **CI/CD & macOS Apple Silicon DMG Release Automation:**
  - Added automated PR retrieval/sidecar gates in `.github/workflows/ci.yml` and Apple Silicon Tauri DMG build, codesign/notarization hooks, verification, and unified `SHA256SUMS` publishing in `.github/workflows/release.yml` and `.github/workflows/publish.yml`.

## Artifacts

- `dist/ltcai-12.8.0-py3-none-any.whl`
- `dist/ltcai-12.8.0.tar.gz`
- `ltcai-12.8.0.tgz`
- `dist/ltcai-12.8.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_12.8.0_aarch64.dmg`
