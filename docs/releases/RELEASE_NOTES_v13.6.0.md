[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./RELEASE_NOTES_v13.6.0.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](../ko/releases/RELEASE_NOTES_v13.6.0.md)

# Lattice AI v13.6.0 — Living Synthesis (2026-11-24)

> **Status: canonical** — current release note (Zero-TS5 Native TypeScript 7 OpenAPI Codegen & Domain-Modularized Unit Test Suite).

Eliminates the last remaining TypeScript 5 dependency from the workspace by replacing `openapi-typescript@7.13.0` and the `openapi-ts5` alias with a zero-dependency native TypeScript 7 (`tsgo`) OpenAPI declaration generator (`scripts/generate_openapi_types.mjs`), and splits the five monolithic `tests/unit/test_cov_p1b_remaining*.py` coverage bundles into nine cohesive domain unit test modules backed by shared runtime test fakes (`tests/unit/_runtime_test_fakes.py`).

## What Changed

- **Zero-TS5 Native TypeScript 7 OpenAPI Declaration Generator (`scripts/generate_openapi_types.mjs`, `scripts/check_openapi_drift.mjs`, `package.json`, `package-lock.json`):**
  - Replaced `openapi-typescript@7.13.0`, the `openapi-ts5` (`npm:typescript@^5.9.3`) alias, `scripts/run_openapi_typescript.mjs`, and the `package.json` `overrides` workaround with a deterministic zero-dependency ESM OpenAPI 3.x-to-TypeScript generator (`scripts/generate_openapi_types.mjs`).
  - Restored `typecheck:frontend` directly to `npx tsc -p tsconfig.json --noEmit` running natively on `typescript@7.0.2` (`tsgo`) with zero TypeScript 5 packages anywhere in `package-lock.json`.
  - Preserved full `openapi-fetch` `paths`, `components["schemas"]`, and `operations` type compatibility across all 415 API paths and 457 operations with 100% Vitest coverage.
- **Domain-Modularized Python Unit Test Suite (`tests/unit/_runtime_test_fakes.py`, `tests/unit/test_*.py`):**
  - Split the five monolithic 650–845-line `tests/unit/test_cov_p1b_remaining*.py` files (3,379 lines total) into nine focused domain modules (`170–449` lines each): `test_model_engines_servers.py`, `test_model_engines_install_and_download.py`, `test_model_runtime_loading_flow.py`, `test_api_lifespan_csrf_and_users.py`, `test_tools_filesystem_knowledge_and_search.py`, `test_core_config_sessions_and_quiet.py`, `test_model_runtime_services_and_audit.py`, `test_worker_foundation_and_runtime_edges.py`, and `test_worker_router_and_edge_cases.py`.
  - Consolidated five duplicated copies of `_Done`, `_Popen`, `_UrlResp`, and `_fast_deadline` into `tests/unit/_runtime_test_fakes.py`, preserving all 45 tests and `>= 96%` Python line coverage.

## Exact Build Artifacts

- `dist/ltcai-13.6.0-py3-none-any.whl`
- `dist/ltcai-13.6.0.tar.gz`
- `ltcai-13.6.0.tgz`
- `dist/ltcai-13.6.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_13.6.0_aarch64.dmg`
