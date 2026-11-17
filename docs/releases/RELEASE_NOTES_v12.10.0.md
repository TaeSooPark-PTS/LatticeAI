# Lattice AI 12.10.0 — Grounded Self

> **Status: historical** — point-in-time release note.

2026-11-17

**12.10.0 Grounded Self** adds deterministic agent transcript grounding & interactive citation proof badges, a dedicated Brain Self-Model panel with Review Center proposal approval, Knowledge Graph `PART_OF` cascade parity across `edges` and `edges_v2`, cooperative model download cancellation (`499`) with automatic post-download semantic embedding transition & reindex drain, crate-internal `http_reply.rs` transport decoupling, 100% Python worker server-i18n lock (`6/6` routers), parallel `cargo nextest` test execution, and chained `release.yml` → `publish.yml` CD automation.

## What landed

- **Deterministic Transcript Grounding & Citation Proof (`rust/lattice-agent`, `AnswerProof.tsx`, `browser-extension`):**
  - `rust/lattice-agent/src/kernel/transcript/grounding.rs`, `transcript.rs`, `agentloop/verification.rs`, and `probe.rs`: Catch unread target-file answers and hallucinated `path:line` citations or backticked identifiers on 2B/8GB-class local models without per-model branching.
  - `frontend/src/features/brain/AnswerProof.tsx`, `CitationVisual.test.tsx`, and `browser-extension/popup.js`: Surface interactive citation/grounding proof details in Brain answers and synchronize server language settings in the browser extension popup.
- **Brain Self-Model Panel & Knowledge Graph `PART_OF` Cascade (`SelfModelPanel.tsx`, `lattice-retrieval`, `lattice-core`):**
  - `frontend/src/features/brain/{SelfModelPanel.tsx,SelfModelPanel.test.tsx}`, `frontend/src/api/{selfModel.ts,selfModel.test.ts}`, `frontend/src/i18n/brain/selfModel.ts`, and `rust/lattice-retrieval/src/memory_api/self_model_write.rs`: Deliver a dedicated Self-Model card in Brain for managing preferences, habits, decisions, traits, and relationships plus inline approval/dismissal of pending `self_model_fact` proposals at 100% Vitest coverage.
  - `rust/lattice-core/tests/graph_write_schema.rs`, `rust/lattice-retrieval/src/{brain_api/desk.rs,native_hnsw.rs,vector.rs,vector_alignment.rs}`, `rust/lattice-retrieval/tests/rag_eval.rs`, and `rust/lattice-chat/tests/cloud_lane.rs`: Verify `PART_OF` edge cascades across both `edges` (`from_node`/`to_node`) and `edges_v2` (`source`/`target`) on node deletion, harden aligned vector/HNSW search & RAG evaluation, and expand cloud-lane boundary E2E coverage.
- **Cooperative Download Cancellation & Automatic Semantic Embedding Transition (`latticeai/services`, `lattice-jobs`):**
  - `latticeai/services/model_runtime/download.py`, `model_loading.py`, `embedding_recommendation.py`, `rust/lattice-jobs/src/index_api/engine.rs`, and `tests/unit/test_download_cancel_and_embedding_transition.py`: Add cooperative `499` download cancellation, hardware-sized batch recommendations (`16`–`64`), and automatic semantic embedding activation + reindex drain when a downloaded embedding model finishes installing.
- **Platform Transport Decoupling, 100% Server i18n Lock, Parallel `cargo nextest` & Chained CD:**
  - `rust/lattice-platform/src/http_reply.rs` and `ARCHITECTURE.md`: Extract shared JSON response, FastAPI 422 validation envelope, localized message detail, and UTC ISO-8601 helpers out of `workspaceos::project_sessions` so `knowledge/` and `toolsurface/` never reach sideways across domains.
  - `scripts/check_server_i18n.mjs` and `tests/unit/test_server_messages.py`: Lock all 6 Python worker routers (`0 not yet claimed`).
  - `scripts/run_rust_tests.mjs` and `.github/workflows/{release.yml,publish.yml}`: Run parallel `cargo nextest` workspace suites with scrubbed connector tokens and chain `publish.yml` (`workflow_call`) directly after `release.yml` builds and publishes the GitHub Release.

## Artifacts

- `dist/ltcai-12.10.0-py3-none-any.whl`
- `dist/ltcai-12.10.0.tar.gz`
- `ltcai-12.10.0.tgz`
- `dist/ltcai-12.10.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_12.10.0_aarch64.dmg`
