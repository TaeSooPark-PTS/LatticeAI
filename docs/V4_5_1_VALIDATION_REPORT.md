# v4.5.1 Validation Report

Release date: 2026-06-13

## Scope

Validation covers the v4.5.1 product reimagining, version sync, frontend visual
changes, release artifacts, and compatibility preservation.

## Command Matrix

| Check | Result |
| --- | --- |
| Python compile | PASS: `npm run check:python` compiled 775 modules |
| Ruff | PASS: `node scripts/run_python.mjs -m ruff check .` |
| Unit tests | PASS: `npm run test:unit` - 616 passed, 2 warnings |
| Integration tests | PASS: `LTCAI_TEST_BASE_URL=http://127.0.0.1:4932 npm run test:integration` - 9 passed, 1 skipped |
| Frontend lint | PASS: `npm run lint` |
| TypeScript typecheck | PASS: `npm run typecheck` and `npm run typecheck:frontend` |
| Playwright | PASS: `npm run test:visual` - 14 passed |
| Tauri check/build | PASS: `npm run desktop:tauri:check`; PASS via `npm run release:artifacts` for the Tauri app and DMG build |
| Release artifact validation | PASS: `npm run release:validate` |
| Wheel smoke | PASS: `node scripts/run_python.mjs scripts/wheel_smoke.py --wheel dist/ltcai-4.5.1-py3-none-any.whl` |
| npm pack dry-run | PASS: `npm pack --dry-run` |

## Browser Evidence

- Desktop screenshot: `output/audits/v4.5.1-reimagining/screenshots/home-desktop.png`
- Mobile screenshot: `output/audits/v4.5.1-reimagining/screenshots/home-mobile.png`
- Walkthrough GIF: `output/audits/v4.5.1-reimagining/gifs/v4.5.1-reimagining-walkthrough.gif`

## RC Artifacts

Expected exact-version artifacts:

- `dist/ltcai-4.5.1-py3-none-any.whl`
- `dist/ltcai-4.5.1.tar.gz`
- `ltcai-4.5.1.tgz`
- `dist/ltcai-4.5.1.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.5.1_aarch64.dmg`

## Artifact Hashes

| Artifact | SHA-256 |
| --- | --- |
| `dist/ltcai-4.5.1-py3-none-any.whl` | `3d5ce1a0a85f7aba1f78587cd7e4a66c63dd5c03ddde7cee57624ec3f487899b` |
| `dist/ltcai-4.5.1.tar.gz` | `62f0e05ff32554cf599b76678de3136bc02e5af4775144e7347182eed0fb4675` |
| `ltcai-4.5.1.tgz` | `e755f40f87484d8a6e3f6bc95f48f0f78e1d0fcde3af8b14c709cf7fa71b2e4b` |
| `dist/ltcai-4.5.1.vsix` | `3badc5915dc31425fa383d5946f78e0914497aa9e523cb7fbdc81a295b8f4a2f` |
| `src-tauri/target/release/bundle/dmg/Lattice AI_4.5.1_aarch64.dmg` | `689ffc9553facf3987a5d57016dfd24fc3872f9c0810e28621f96b340ca38ce0` |

`npm run release:validate` confirmed all exact-version RC artifacts are present.
It also warned that historical artifacts remain in `dist/`, so publish commands
must continue to use explicit v4.5.1 filenames rather than a `dist/*` glob.
