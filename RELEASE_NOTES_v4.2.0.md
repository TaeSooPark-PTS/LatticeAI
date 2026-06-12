# Lattice AI v4.2.0 RC — Brain Core & Storage Rebuild

> Status: release candidate. This release builds from `main` after v4.1.0.
> It does not tag, create a GitHub Release, publish packages, or deploy to any
> external registry.

v4.2.0 extracts the backend Digital Brain boundary into the independent
`lattice_brain` Python package and introduces a pluggable storage layer while
preserving v4.1.0 APIs, data, frontend behavior, and local-first defaults.

## Included

- `lattice_brain` Brain Core import package.
- `BrainCore` runtime facade used by FastAPI.
- `StorageEngine` ABC.
- `SQLiteEngine` default storage engine.
- Honest sqlite-vec capability detection with brute-force cosine fallback.
- `PostgresEngine` opt-in pgvector scale target.
- Explicit-consent Docker Postgres setup wizard.
- SQLite to Postgres migration planner/runner.
- Encrypted `.latticebrain` archive create/restore support.
- API-backed System storage status, Docker setup, and migration-plan controls.
- Generated OpenAPI client updated to 313 paths.

## Preserved

- Existing FastAPI API contracts.
- Existing v4.1.0 SQLite user data.
- Knowledge Graph, memory, context, ingestion, agent runtime, workflow runtime,
  skills/hooks/plugins, portability, signed bundles, and Brain Network behavior.
- React/Vite/Tauri frontend and desktop architecture.
- SQLite as the default local-first storage engine.

## Safety Rules

- Postgres is never required.
- Docker never auto-starts without explicit user consent.
- Explicit Postgres selection fails honestly if DSN or dependencies are missing.
- No startup migration rewrites or deletes user data.
- No external package registry publish step is part of this release candidate.

## Expected Artifacts

- `dist/ltcai-4.2.0-py3-none-any.whl`
- `dist/ltcai-4.2.0.tar.gz`
- `dist/ltcai-4.2.0.vsix`
- `ltcai-4.2.0.tgz`

## Validation Scope

- Python compile check.
- Ruff.
- Unit tests.
- Live integration tests.
- Migration idempotence and dry-run tests.
- SQLite backup/restore and encrypted archive tests.
- Vector search tests.
- Storage fallback tests.
- Frontend lint.
- TypeScript build.
- Tauri cargo check.
- Release artifact validation.
- Wheel smoke test.
- npm pack dry-run.

## External Registries

No PyPI, npm Registry, VS Code Marketplace, Open VSX, or other external
registry publish step is part of this release candidate.

