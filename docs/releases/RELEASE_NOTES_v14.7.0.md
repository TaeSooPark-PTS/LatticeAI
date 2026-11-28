[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./RELEASE_NOTES_v14.7.0.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](../ko/releases/RELEASE_NOTES_v14.7.0.md)

# Lattice AI v14.7.0 — Private Scope, Verified Index (2026-11-28)

`v14.7.0` ships per-owner private documents inside shared workspaces, promotes native HNSW as the default vector backend after benchmark verification on `datasets/rag_eval_v1.json`, completes Hugging Face revision and SHA-256 supply-chain verification, strengthens small-model multi-source synthesis, adds an opt-in Self-Model LLM refiner, and delivers a Brain Network share UI.

## Highlights

### 1. Private Documents Inside a Workspace
- Added per-document `visibility` (`workspace` default or `private`) across `lattice-core` (`viewer_scope.rs`, `graph_write/`), `lattice-retrieval` (`bm25/scoring.rs`, `vector_alignment.rs`, `knowledge_graph_api/`), `lattice-ingest` (`/upload/document` multipart field & `x-lattice-visibility` header), and `lattice-platform` (`share_bundle.rs`, `security_dashboard/`).
- Private documents and notes use owner-scoped node and concept IDs so identical content uploaded by two users never cross-deduplicates, and foreign private documents are excluded across search, vectors, graph neighbors, file listings, exports, and admin file inspectors.
- Added workspace vs. private visibility selectors and API plumbing in `BrainIngestionPanel`, `BrainIngestionDock`, `useBrainIngestion`, `uploadDocument`, and `ingestNote`.

### 2. Default `LATTICEAI_VECTOR_INDEX=hnsw` Verified Against `rag_eval_v1.json`
- Added `rust/lattice-retrieval/tests/hnsw_rag_eval_recall.rs` benchmarking `NativeHnswIndex` against brute-force cosine search on the `rag_eval_v1` corpus (40 documents, 320 queries, 5 ingest/delete/revise churn rounds): `baseline=1.0000`, `churn_rounds=[0.9990625, 0.9984375, 0.9978125, 0.99625, 0.9965625]` (all well above the `0.90` floor).
- Flipped `LATTICEAI_VECTOR_INDEX` default from `brute` to `hnsw` across `lattice-retrieval`, `lattice-jobs`, `lattice-platform` feature metadata, and `lattice-core` localized descriptions.

### 3. Hugging Face Revision Pinning Completion & SHA-256 Integrity Checks
- Completed runtime revision pinning and SHA-256 verification across `latticeai/core/hf_revisions.py`, `latticeai/models/router/local_models.py`, `latticeai/core/embedding_providers/{autodetect,text}.py`, and `latticeai/services/model_runtime/download.py`.
- Supports verified Hub commit pins, LFS SHA-256 digest checks with corrupt-file cleanup, `PINNED_HF_SHA256` / `LATTICEAI_HF_SHA256`, `LATTICEAI_HF_REQUIRE_PINNED`, `LATTICEAI_HF_ALLOW_UNPINNED`, and `LATTICEAI_HF_REQUIRE_SHA256`.

### 4. Small-Model Synthesis Quality & Optional Self-Model LLM Refiner
- **Small-model synthesis (`rust/lattice-agent/src/kernel/agentloop/synthesis.rs`):** Builds numbered `[S1]`..`[Sn]` evidence scaffolds for decomposed profiles, checks coverage across collected sources, rejects echoed prompt instructions and stub answers, runs one targeted refinement pass in `execution/mod.rs`, and fails closed to `NEEDS_REVIEW` in `verification.rs` with zero per-model branches.
- **Optional Self-Model LLM refiner (`rust/lattice-retrieval/src/memory_api/self_model_refiner.rs`):** Adds opt-in `LATTICEAI_SELF_MODEL_LLM_REFINER=1` refinement alongside deterministic phrase extraction in `/api/memory/self-model/propose`, strictly proposal-first through Review Center.
- **Regression test:** Added `rust/lattice-retrieval/src/brain_api/desk_tests.rs` locking `desk::open_keys_in` snoozed-suppression behavior.

### 5. Brain Network Share UI
- Added `frontend/src/api/brainNetwork.ts` and `BrainNetworkPanel.tsx` inside `BrainGovernanceHub` showing opt-in status (`LATTICEAI_BRAIN_NETWORK=1`), local Ed25519 device identity/fingerprint, peer pairing/unpairing/push controls, signed subgraph bundle export, and Review Center staged import.

## Verification
- `rag_eval_v1` HNSW recall@10 benchmark: baseline `1.0000`, 5 churn rounds `[0.9990625, 0.9984375, 0.9978125, 0.99625, 0.9965625]`.
- Verified across full Rust lint/test (`cargo fmt`, `clippy -D warnings`, `cargo-nextest`), Vitest 100% coverage (`134` files, `2,055` tests), Python pytest + coverage (`>= 90%`), TypeScript `typecheck`, OpenAPI check, integration/CLI/extension suites, Playwright visual smoke, and package builds.

## Build Artifacts
- `dist/ltcai-14.7.0-py3-none-any.whl`
- `dist/ltcai-14.7.0.tar.gz`
- `ltcai-14.7.0.tgz`
- `dist/ltcai-14.7.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_14.7.0_aarch64.dmg`
