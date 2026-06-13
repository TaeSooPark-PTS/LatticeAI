# Lattice AI v4.5.0 Product Surface Reset Validation Report

Date: 2026-06-13

## Summary

Validation passed on macOS after the complete product-surface reset.

## Validation Matrix

| Check | Result |
| --- | --- |
| Python compile | PASS: `npm run check:python` compiled 775 modules |
| Ruff | PASS: `node scripts/run_python.mjs -m ruff check .` |
| Unit tests | PASS: `npm run test:unit` (`616 passed`, 2 warnings) |
| Integration tests | PASS: `LTCAI_TEST_BASE_URL=http://127.0.0.1:8899 npm run test:integration` (`9 passed`, `1 skipped`) |
| Focused lattice_brain / graph / search / ingestion / archive tests | PASS: 49 focused tests |
| Frontend lint | PASS: `npm run lint` |
| TypeScript / VS Code extension build | PASS: `npm run typecheck` |
| Playwright product surface validation | PASS: `npx playwright test tests/visual/v3.spec.js` (`14 passed`) |
| Tauri cargo check | PASS: `npm run desktop:tauri:check` |
| Tauri DMG build | PASS: `npm run release:artifacts` |
| Release artifact validation | PASS: `npm run release:validate` |
| Wheel smoke | PASS: `node scripts/run_python.mjs scripts/wheel_smoke.py --wheel dist/ltcai-4.5.0-py3-none-any.whl` |
| npm pack dry-run | PASS: `npm pack --dry-run` |
| Markdown links | PASS: `npm run docs:check-links` |
| Documentation safety grep | PASS: no stale current-release references or unsafe publish commands for this scope |

## UX Regression Coverage Added

Playwright now validates:

- first-run onboarding journey is visible and actionable
- model setup uses guided user language
- Basic mode does not expose `MLX`
- Basic mode does not expose `GGUF`
- Basic graph view hides endpoint and Cytoscape implementation leakage
- Admin controls are gated until Admin mode is selected
- mobile layout has no horizontal overflow

## Notes

- A first integration attempt without a live server failed with connection refused. The live-server integration suite was rerun against `http://127.0.0.1:8899` and passed.
- The Postgres live migration integration remains skipped unless the dedicated Postgres fixture is available.
- Vite still reports the existing large frontend chunk warning.
- Tauri/Rust still reports the known upstream `block v0.1.6` future-incompatibility warning.
- `release:validate` warns that historical artifacts remain in `dist/`; explicit v4.5.0 artifact paths were validated and no `dist/*` upload was used.

## Validated v4.5.0 RC Artifacts

| Artifact | SHA-256 |
| --- | --- |
| `dist/ltcai-4.5.0-py3-none-any.whl` | `6e87096e2a2383cbd8847d1cd91d6027207ce5cc6b2815f589873465181ba26d` |
| `dist/ltcai-4.5.0.tar.gz` | `6901a3f4f1394d8ec8b90566c875cf639929a595ab4829ec3e58698d0d8974b9` |
| `ltcai-4.5.0.tgz` | `c4d44717aa3f498e4005034a8b491183941c6e658df4e610b0a9a2dde8ce17c6` |
| `dist/ltcai-4.5.0.vsix` | `67a8dfd428ce99f13b99019f5d0beb1bc34409f43562ebf89874e16d682bf2f7` |
| `src-tauri/target/release/bundle/dmg/Lattice AI_4.5.0_aarch64.dmg` | `2a6577013df649f42e12f7f48d978a3952f79c6d12f7dec615ff36ecfb196c84` |

## External Publishing

No PyPI, npm Registry, VS Code Marketplace, or Open VSX publish commands were run.
