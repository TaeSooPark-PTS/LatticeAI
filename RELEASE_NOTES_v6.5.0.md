# Lattice AI v6.5.0 - Brain Experience Readiness

v6.5.0 turns the 6.4.0 Digital Brain hardening work into clearer everyday
feedback for normal users. The Brain now explains whether it is waiting for its
first memory, forming topics, or ready for map-level exploration.

## Added

- Brain readiness strip on Brain Home, derived from backend Memory Manager
  memory, graph, relationship, and source health signals.
- `/api/memory/brain-quality` for the same backend-owned readiness summary used
  by `/api/memory/manager`.
- Persistent depth progress rail for the Living Brain journey from conversation
  through memories, topics, relationships, and the full knowledge graph.
- Source-aware memory-save detail after chat, clarifying that useful context is
  available later through recall.
- Visual regression coverage for readiness, depth progress, and memory-save
  feedback.

## Changed

- Brain empty states now guide first-memory use in product language instead of
  exposing graph internals.
- Package, runtime, static, VS Code, and Tauri metadata are synchronized to
  6.5.0.

## Release Boundaries

- No storage schema migration.
- No package registry publish or production deployment.
- No automatic external ingestion or external model/reranker use.
