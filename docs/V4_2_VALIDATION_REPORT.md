# Lattice AI v4.2.0 RC — Validation Report

Date: 2026-06-12
Commit under validation: v4.2.0 RC commit on `main` after `v4.1.0`

## Result

v4.2.0 RC validation passed for the implemented Brain Core package, storage
abstraction, SQLite default runtime, encrypted archives, generated OpenAPI
client, frontend/system controls, desktop check, and release artifacts.

## Commands

| Check | Command | Result |
| --- | --- | --- |
| OpenAPI generation | `npm run frontend:openapi` | PASS — 313 paths |
| Python compile | `npm run check:python` | PASS — 234 modules |
| Ruff | `node scripts/run_python.mjs -m ruff check .` | PASS |
| Unit tests | `npm run test:unit -- --tb=short` | PASS — 592 passed, 2 warnings |
| Live integration | `LTCAI_TEST_BASE_URL=http://127.0.0.1:8899 npm run test:integration -- --tb=short` | PASS — 9 passed |
| Frontend lint | `npm run lint` | PASS |
| TypeScript + VS Code extension build | `npm run typecheck` | PASS |
| Vite app build | `npm run build:assets` | PASS |
| Playwright visual/offline suite | `npx playwright test tests/visual/v3.spec.js` | PASS — 12 passed |
| Tauri desktop check | `npm run desktop:tauri:check` | PASS |
| Release artifacts | `npm run release:artifacts` | PASS |
| Artifact validation | `npm run release:validate` | PASS |
| Wheel smoke | `node scripts/run_python.mjs scripts/wheel_smoke.py --wheel dist/ltcai-4.2.0-py3-none-any.whl` | PASS |
| npm dry-run | `npm pack --dry-run` | PASS |

## Storage-Specific Coverage

- `test_v42_brain_storage.py` validates:
  - `lattice_brain` package exports a working Knowledge Graph store.
  - `BrainCore` constructs SQLite graph and durable conversation stores.
  - default storage is SQLite.
  - explicit Postgres without DSN fails honestly.
  - SQLite-to-Postgres migration planning preserves all user tables and
    idempotence keys.
  - Docker setup does not start without explicit consent.
  - encrypted `.latticebrain` archives round-trip DB + blobs.
  - portability service exposes storage status, dry-run migration, and archives.
- Existing vector tests continue to validate real local vector search.
- Existing portability tests continue to validate JSON export/import and ZIP
  backup/restore.

## Generated Artifacts

- `dist/ltcai-4.2.0-py3-none-any.whl`
- `dist/ltcai-4.2.0.tar.gz`
- `dist/ltcai-4.2.0.vsix`
- `ltcai-4.2.0.tgz`

## Warnings

- Vite reports the main app chunk is larger than 500 kB; build succeeds.
- Tauri/Rust reports transitive `block v0.1.6` future-incompatibility warning;
  cargo check succeeds.
- Release validation warns that older artifacts remain in `dist/`; exact
  v4.2.0 artifact validation passes and publish docs require exact filenames.
- Wheel smoke in a clean venv reports MLX unavailable; expected when optional
  local MLX runtime is not installed. `/health` still reports version `4.2.0`.

## Owner-Only Validation Boundary

Live Postgres copy validation was not run because it requires a user-provided
Postgres DSN or explicit consent to start Docker. The migration planner,
Postgres unavailable/fail-closed behavior, pgvector initialization code path,
and Docker no-consent behavior are implemented and unit-tested. SQLite remains
the default and fully validated runtime.

## External Registries

No PyPI, npm Registry, VS Code Marketplace, Open VSX, or other external
registry publish command was run.
