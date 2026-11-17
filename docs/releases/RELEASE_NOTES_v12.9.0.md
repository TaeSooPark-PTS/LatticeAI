# Lattice AI 12.9.0 — Semantic Guard

> **Status: historical** — point-in-time release note.

2026-11-17

**12.9.0 Semantic Guard** completes Phase 0–4 retrieval query-contract alignment, pure-Rust native HNSW & scoped Python HNSW sidecars, fail-closed network boundary & cloud context isolation, desktop/CLI/supervisor/Docker runtime guardrails, live Workspace OS SSE streaming, and v2 `.latticebrain` archive KDF hardening.

## What landed

- **Contract-Aligned Semantic Query Embedding & Native HNSW (`Phase 0–1`):**
  - `rust/lattice-retrieval/src/{query_embed.rs,native_hnsw.rs,vector_hnsw.rs,hybrid.rs,service_hybrid.rs,search_api/handlers.rs,memory_api/recall.rs}` and `rust/lattice-retrieval/tests/semantic_query_embed.rs`: Align query-time vector embedding against the active store's `EmbeddingIndexContract` across `/api/search/hybrid`, `/memory/recall`, and `/chat`, and introduce a pure-Rust `NativeHnswIndex` alongside `scripts/bench_retrieval_baseline.py`.
  - `latticeai/core/vector_index/{hnsw.py,sidecar.py}` and `tests/unit/test_vector_index_sidecar.py`: Scope Python HNSW sidecars by `(workspace_id, model_id, dim)` with atomic metadata validation and contract drift protection.
- **Fail-Closed Network Boundary & Cloud Context Isolation (`Phase 2`):**
  - `rust/lattice-platform/src/knowledge/{network_boundary.rs,network_boundary_guard.rs}`, `rust/lattice-chat/src/{boundary.rs,cloud/context.rs}`, and `rust/lattice-platform/src/toolsurface/agent_registry.rs`: Enforce server-side `local_only` / `cloud_allowed` network boundary guards, explicit consent checks, sensitivity filtering, and workspace-scoped cloud slice isolation across chat, agent registries, and egress paths.
- **Desktop, CLI, Host Supervisor, & Docker Runtime Guardrails (`Phase 3`):**
  - `src-tauri/src/backend.rs`, `bin/ltcai.js`, `rust/lattice-host/src/{main.rs,gateway/mod.rs,supervisor/command.rs}`, and `Dockerfile`: Harden Tauri desktop process lifecycle & health probes, `LTCAI` CLI host/worker startup resolution, `lattice-host` supervisor & gateway request guardrails, and multi-stage container builds.
- **Live Workspace OS Realtime SSE & App Bootstrap Modularization (`Phase 4`):**
  - `rust/lattice-platform/src/workspaceos/realtime.rs`, `frontend/src/hooks/{useRealtimeStream.ts,useRealtimeStream.test.tsx,useAppBootstrap.ts}`, `frontend/src/App.tsx`, and `frontend/src/components/ProductFlow.tsx`: Deliver live authenticated Server-Sent Events (`/api/realtime/stream`) with reconnect backoff and 100% Vitest coverage, extract `useAppBootstrap` out of `App.tsx`, and refine first-run onboarding flows.
- **v2 `.latticebrain` Archive KDF Hardening & CI Gates:**
  - `rust/lattice-platform/src/knowledge/portability/{crypto.rs,kdf.rs}` and `.github/workflows/{ci.yml,dependency-audit.yml,e2e-sidecar.yml}`: Upgrade `.latticebrain` v2 archive key derivation parameters with backward-compatible v1 decrypt support, and tighten CI, Sidecar E2E, and Dependency Audit gates.

## Artifacts

- `dist/ltcai-12.9.0-py3-none-any.whl`
- `dist/ltcai-12.9.0.tar.gz`
- `ltcai-12.9.0.tgz`
- `dist/ltcai-12.9.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_12.9.0_aarch64.dmg`
