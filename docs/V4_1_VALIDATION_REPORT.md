# v4.1.0 Validation Report

## Summary

v4.1.0 RC validation passed for the React/Vite frontend, Tauri desktop shell,
FastAPI backend compatibility, release artifacts, and installed-wheel smoke.

## Commands Run

| Area | Command | Result |
| --- | --- | --- |
| OpenAPI generation | `npm run frontend:openapi` | Pass; generated 308 paths |
| Static app build | `npm run build:assets` | Pass; wrote `static/app/asset-manifest.json` |
| Frontend lint / no CDN / API compatibility | `npm run lint` | Pass; scanned 28 frontend/static files; OpenAPI exposes 308 paths |
| TypeScript build | `npm run typecheck` | Pass; frontend TS + VS Code extension build |
| Python compile | `npm run check:python` | Pass; compiled 206 modules |
| Ruff | `node scripts/run_python.mjs -m ruff check .` | Pass |
| Unit tests | `npm run test:unit -- --tb=short` | Pass; 585 passed, 2 warnings |
| Live integration tests | `LTCAI_TEST_BASE_URL=http://127.0.0.1:8899 npm run test:integration -- --tb=short` | Pass; 9 passed |
| Playwright visual/offline tests | `npx playwright test tests/visual/v3.spec.js` | Pass; 12 passed |
| Tauri cargo check | `npm run desktop:tauri:check` | Pass |
| Tauri desktop build | `LATTICEAI_DESKTOP_NO_BACKEND=1 npm run desktop:tauri:build` | Pass; built `.app` and DMG |
| Electron fallback syntax/version | `node --check desktop/electron/main.cjs && npx electron --version` | Pass; Electron v42.4.0 |
| Release artifacts | `npm run release:artifacts` plus resumed `npm pack && npm run package:vsix` | Pass |
| Artifact validation | `npm run release:validate` | Pass; exact v4.1.0 wheel/sdist/VSIX/tgz found |
| Wheel smoke | `node scripts/run_python.mjs scripts/wheel_smoke.py --wheel dist/ltcai-4.1.0-py3-none-any.whl` | Pass; imports 19 modules and `/health` reports 4.1.0 |
| npm pack dry-run | `npm pack --dry-run` | Pass; `ltcai-4.1.0.tgz`, 259 files, 2.9 MB |

## Generated Artifacts

- `dist/ltcai-4.1.0-py3-none-any.whl`
- `dist/ltcai-4.1.0.tar.gz`
- `dist/ltcai-4.1.0.vsix`
- `ltcai-4.1.0.tgz`
- `src-tauri/target/release/bundle/macos/Lattice AI.app`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.1.0_aarch64.dmg`

## Notes

- Vite reports one large initial bundle warning after minification. The app
  remains fully local/offline and passes visual/offline startup validation.
- `cargo check` reports a future-incompatibility warning in transitive
  `block v0.1.6`; the current Tauri 2.0 build passes on Rust 1.96.
- Release artifact validation warns that historical artifacts remain in
  `dist/`; this is expected and reinforces the rule to upload only exact
  v4.1.0 filenames, never `dist/*`.
- No external registry publish was performed.
