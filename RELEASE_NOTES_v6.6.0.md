# Lattice AI v6.6.0 - Brain Proof Runtime

v6.6.0 makes the Brain's value visible in the first few minutes: saved context,
model-independent continuity, and recall evidence now appear directly on Brain
Home instead of staying implicit in backend stores.

## Added

- `/api/memory/brain-proof`, backed by `MemoryService.brain_proof()`, combining
  Brain readiness, durable memory counts, graph/vector state, active model id,
  and a unified recall sample.
- Brain Home proof strip for recallable context, model-continuity, and
  knowledge-store state.
- Recent recall proof card that shows the latest memory/item the Brain can bring
  back after useful context is saved.
- Brain Home document upload CTA in the empty state and composer, so first-time
  users can grow the Brain with files without leaving the main surface.
- Refreshed README-linked release evidence under `output/release/v6.6.0/`.
- Unit coverage for model-independent Brain proof recall behavior.
- Honest empty-state Brain proof: model-independent capability remains visible,
  but continuity is only marked proven after durable evidence exists.

## Changed

- Interaction router runtime context now passes active-model state into the
  Memory router, reducing app-factory inline wiring while preserving route
  order.
- Default Brain proof recall seeding is scoped to the current user/workspace
  before falling back to conversation memory.
- Chat-to-Knowledge-Graph ingestion carries workspace scope into graph nodes,
  and Brain proof conversation counts are scoped before continuity is marked
  proven.
- Direct Knowledge Graph ingest and document uploads now carry request
  workspace scope into graph/source/document nodes.
- Package, runtime, static, VS Code, and Tauri metadata are synchronized to
  6.6.0.

## Release Boundaries

- No storage schema migration.
- No package registry publish or production deployment.
- No automatic external ingestion or external model/reranker use.
