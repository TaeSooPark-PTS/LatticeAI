# Lattice AI v6.4.0 - Digital Brain Quality Hardening

Lattice AI v6.4.0 is a Digital Brain quality hardening release. It does not add
unrelated product features or redesign the UI. The release tightens workspace
scope boundaries and adds non-destructive quality primitives for embeddings,
retrieval, memory extraction, graph validation, structured context, and recall
benchmarks.

## Highlights

- Added `lattice_brain.quality` with embedding fallback labelling, drift and
  re-index planning, BM25 lexical scoring, hybrid fusion, reranker fallback
  contracts, memory candidate scoring/deduplication/conflict/retention helpers,
  graph edge confidence/evidence metrics, structured context guardrails, and
  retrieval benchmark metrics.
- Scoped graph/search reads across graph, node, neighborhood, relationship,
  keyword, vector, graph, and hybrid retrieval paths.
- Scoped Memory Manager prune, compact, and clear operations to the caller's
  owner/workspace boundary.
- Blocked Memory Manager graph clear because the existing graph clear path is
  not workspace-scoped.
- Added `docs/v6.4/BRAIN_QUALITY_BASELINE.md` with baseline findings, risk
  register, validation items, and deferred work.
- Synchronized package/runtime/static metadata to `6.4.0`.

## Validation Focus

- Brain quality primitive unit coverage.
- Multi-workspace graph/search read isolation.
- Memory Manager scoped mutation and graph-clear guard coverage.
- Existing hybrid search and memory/context regression coverage.

## Expected Artifacts

- `dist/ltcai-6.4.0-py3-none-any.whl`
- `dist/ltcai-6.4.0.tar.gz`
- `dist/ltcai-6.4.0.vsix`
- `ltcai-6.4.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_6.4.0_aarch64.dmg`

Package registry publishing and production deployment remain owner-run only.
