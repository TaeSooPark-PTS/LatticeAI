# Lattice AI v4.3.0 Validation Report

> Status: passed for v4.3.0 RC artifacts on 2026-06-12. No package registry,
> marketplace, GitHub Release, or tag publish was performed.

## Target Artifacts

- `dist/ltcai-4.3.0-py3-none-any.whl`
- `dist/ltcai-4.3.0.tar.gz`
- `ltcai-4.3.0.tgz`
- `dist/ltcai-4.3.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.3.0_aarch64.dmg`

## Validation Matrix

| Gate | Command | Result |
| --- | --- | --- |
| Python compile check | `npm run check:python` | PASS — compiled 238 modules |
| Ruff | `node scripts/run_python.mjs -m ruff check .` | PASS |
| Unit tests | `npm run test:unit` | PASS — 598 passed, 2 warnings |
| Integration tests | `LTCAI_TEST_BASE_URL=http://127.0.0.1:8899 npm run test:integration` | PASS — 9 passed, 1 skipped; backend was started on loopback with tunnel, Telegram, autoload, and network CORS disabled |
| Frontend lint | `npm run lint:frontend` | PASS — frontend TS, no-CDN scan, OpenAPI path guard; 318 paths |
| TypeScript typecheck | `npm run typecheck` | PASS — frontend and VS Code extension build |
| Playwright visual/offline | `npm run test:visual` | PASS — 12 passed |
| Tauri check | `npm run desktop:tauri:check` | PASS |
| Tauri build | `npm run release:artifacts` | PASS — built app and DMG through `desktop:tauri:build` |
| Archive export/import/restore | `tests/unit/test_v42_brain_storage.py`, `tests/unit/test_kg_portability.py` | PASS |
| Backup/restore corruption | `tests/unit/test_kg_portability.py` | PASS |
| Signature/version mismatch | `tests/unit/test_v42_brain_storage.py`, `tests/unit/test_t8_brain_network.py` | PASS |
| Default startup no-network | `tests/unit/test_config.py`, `tests/unit/test_v43_product_hardening.py`, `tests/unit/test_v43_cli_privacy.py`, integration startup banner | PASS |
| Release artifact validation | `npm run release:validate` | PASS — exact 4.3.0 files found; warning retained for historical artifacts in `dist/` |
| Wheel smoke | `node scripts/run_python.mjs scripts/wheel_smoke.py --wheel dist/ltcai-4.3.0-py3-none-any.whl` | PASS |
| npm pack dry-run | `npm pack --dry-run` | PASS |

## Artifact Hashes

| Artifact | SHA-256 |
| --- | --- |
| `dist/ltcai-4.3.0-py3-none-any.whl` | `c6fb5457bff312ebf694ccf83e53f82124de2b6d83f7f988a4f672b39475cf27` |
| `dist/ltcai-4.3.0.tar.gz` | `c7830b7db62ea0e6e7de2107f3c0903d17546cf9bfa199596fdb930d372a0aaf` |
| `dist/ltcai-4.3.0.vsix` | `939a2839f2b5551136df14321fbfe3da0460e35f76be3a1fea5306628c640df0` |
| `ltcai-4.3.0.tgz` | `e4c94d6331482dd913525c24198d95fef693667a85df88b73548ec281ac1cb16` |
| `src-tauri/target/release/bundle/dmg/Lattice AI_4.3.0_aarch64.dmg` | `bda5020dd556cd918cc3ef448468f175934e95ab4a7665a37db195faac7fc5fb` |

## Notes

- The v4.2 live Docker/pgvector integration test was skipped because no v4.3
  Docker consent was requested for this release-candidate validation pass.
- A direct `npm run test:integration` without a live server fails by design; the
  passing run above used the documented `LTCAI_TEST_BASE_URL` against an
  isolated loopback server.
- Tauri/Cargo emitted a dependency future-incompatibility warning for `block
  v0.1.6`; it did not fail `cargo check` or the release build.

## Registry Policy

No PyPI, npm Registry, VS Code Marketplace, Open VSX, or other external
registry publish is part of this RC.
