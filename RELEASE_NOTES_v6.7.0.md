# Lattice AI v6.7.0 - Brain IA Cleanup

v6.7.0 finishes the first pass on Brain Home route IA: the product no longer
has rich pages that exist in code but are hard to reach from the first Brain
flow.

## Changed

- Brain Home now exposes direct `Add`, `Find`, `Model`, and `Settings` actions
  for document capture, graph/search, model selection, and settings.
- Capture, Brain explorer, Library, System, and Act pages now mount inside a
  shared Brain shell navigation.
- `routes.ts` separates product shell routes and direct product routes from
  legacy compatibility aliases, preserving old URLs without mixing them into
  visible IA.
- Rich pages now load with route-level `React.lazy` chunks, so the initial Brain
  Home bundle no longer includes the full graph explorer, capture, model,
  settings, and automation surfaces at once.
- Static app assets, package metadata, Python runtime metadata, VS Code
  extension metadata, and Tauri metadata are synchronized to 6.7.0.

## Preserved

- v6.6.0 Brain proof API behavior, honest empty proof states, workspace-scoped
  ingestion, and model-independent Brain continuity semantics remain intact.
- Legacy `/chat`, `/ask`, `/graph`, `/models`, `/settings`, `/review`, and
  related compatibility entry URLs still land in the app.
- Package registry publish and production deployment remain owner-run only.

## Release Boundaries

- No storage schema migration.
- No automatic external ingestion.
- No package registry publish or production deployment.
- No external model, embedding, or reranker use without explicit opt-in.
