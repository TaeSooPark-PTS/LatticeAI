[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./RELEASE_NOTES_v13.9.0.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](../ko/releases/RELEASE_NOTES_v13.9.0.md)

# Lattice AI v13.9.0 — Living Synthesis (2026-11-24)

## Highlights

### Security & Integrity Hardening (PR #69 Review Resolutions)
1. **Workspace-Scoped Integrity Checks & Repairs (`rust/lattice-core/src/integrity/{mod.rs,repair.rs}`):** `check_graph_integrity_scoped` and `execute_integrity_repair` (both `dry_run: true` and `dry_run: false`) are strictly restricted to the authorized workspace scope; cross-workspace or DB-wide mutations require explicit admin authorization (`require_admin`).
2. **Single-Writer Pool Enforcement (`rust/lattice-core/src/db.rs`, `rust/lattice-retrieval/src/brain_api/extensions.rs`):** Eliminated secondary direct SQLite connections and routed all Brain repair, entity merge/rollback, knowledge correction, and embedding registry writes through `Store::write_conn`.
3. **Cross-Workspace Target Ownership Validation (`entity_resolution_ext.rs`, `learning_loop.rs`, `brain_api/extensions.rs`):** Every node ID, edge ID, correction ID, and entity-merge record ID is validated against the authorized workspace before mutation, rejecting cross-workspace targets with HTTP `403`.
4. **Windows Handle-Pinned Sandbox I/O (`rust/lattice-agent/src/tools/safe_io.rs`):** Replaced non-Unix leaf-only checks with component-by-component handle-pinned traversal (`CreateFileW` + `FILE_FLAG_OPEN_REPARSE_POINT` + `GetFileInformationByHandle` + `GetFinalPathNameByHandleW`).
5. **Real-Model RAG Grounding Gate (`scripts/run_real_model_rag_eval.py`):** Added `score_grounded_answer` to validate generated claims and citations against retrieved passages, penalizing unsupported claims and fabricated citations in `claim_support_rate`.
6. **Posture-Gated Diagnostics (`rust/lattice-host/src/gateway/mod.rs`):** Moved `GET /host/diagnostics` inside the `require_open_posture`-protected `native` router.

### Schema Unification, Interactive Brain UI & Nightly CI
- Folded all 16 Digital Brain extension routes directly into `brain_api::MOUNTED` and `rust/fixtures/openapi/memory_brain.json`, removed the temporary split mount in `lattice-host`, and regenerated `frontend/openapi.json` and `frontend/src/api/openapi.ts`.
- Built five interactive React Brain Governance panels (`IntegrityRepairPanel`, `EntityMergePanel`, `KnowledgeCorrectionsPanel`, `EmbeddingRegistryPanel`, `ClustersGapsPanel`) wired into `Brain.tsx` with EN/KO i18n and Vitest tests.
- Added `.github/workflows/nightly-rag-eval.yml` for scheduled daily real-model RAG evaluation with graceful `skipped`/`unavailable` reporting when endpoints are unset.
