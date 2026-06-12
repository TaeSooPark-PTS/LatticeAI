# Lattice AI v4.3.2 Validation Report

Date: 2026-06-13

## Summary

v4.3.2 validation is green for the implemented product polish, graph UX, desktop
shutdown, and exact-version release artifacts.

## Commands

| Check | Result |
| --- | --- |
| `npm run check:python` | PASS, compiled 691 modules |
| `node scripts/run_python.mjs -m ruff check .` | PASS |
| `npm run lint` | PASS |
| `npm run typecheck` | PASS |
| `npm run test:unit` | PASS, 602 passed, 2 warnings |
| `LTCAI_TEST_BASE_URL=http://127.0.0.1:4932 npm run test:integration` | PASS, 9 passed, 1 skipped |
| `npm run test:visual` | PASS, 12 Playwright tests |
| `npm run desktop:tauri:check` | PASS |
| `npm run release:artifacts` | PASS |
| `npm run release:validate` | PASS |
| `node scripts/run_python.mjs scripts/wheel_smoke.py --wheel dist/ltcai-4.3.2-py3-none-any.whl` | PASS |
| `npm pack --dry-run` | PASS |
| Rebuilt desktop app startup/shutdown verification | PASS |

## Artifact Validation

Exact v4.3.2 artifacts were found:

- `dist/ltcai-4.3.2-py3-none-any.whl`
- `dist/ltcai-4.3.2.tar.gz`
- `dist/ltcai-4.3.2.vsix`
- `ltcai-4.3.2.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.3.2_aarch64.dmg`

The release validator warned that `dist/` also contains older historical
artifacts. This is not a v4.3.2 artifact failure, but release uploads must use
only the exact filenames above and never a wildcard over the `dist/` directory.

## Desktop Verification

- The rebuilt Tauri app launched from
  `src-tauri/target/release/bundle/macos/Lattice AI.app/Contents/MacOS/lattice-ai-desktop`.
- `/health` responded on `127.0.0.1:8765` with version `4.3.2`, local mode, and
  the isolated data dir `/tmp/lattice-v432-desktop3`.
- Normal macOS quit released port 8765.
- Evidence:
  - `output/audits/v4.3.2-rc/logs/desktop-sidecar-health-after-shutdown-fix.json`
  - `output/audits/v4.3.2-rc/logs/desktop-shutdown-after-fix.txt`
  - `output/audits/v4.3.2-rc/screenshots/13-desktop-sidecar-startup.png`

## Notes

- The live Postgres migration test remained skipped because v4.3.2 did not
  request fresh Docker consent. Postgres remains optional and was not changed by
  this RC.
- Wheel smoke emitted optional MLX warnings inside the isolated venv, then
  passed imports and `/health`.
- Rust reported a future-incompatibility warning from transitive crate
  `block v0.1.6`; `cargo check` passed.

## Result

PASS. v4.3.2 RC artifacts and runtime behavior are validated.
