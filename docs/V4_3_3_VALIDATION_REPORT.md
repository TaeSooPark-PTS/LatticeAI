# Lattice AI v4.3.3 Validation Report

Date: 2026-06-13

v4.3.3 validation is green for the post-cleanup release tree. The release
rebuilds exact-current-main artifacts after the independent dead-code,
architecture, and runtime audit cleanup; v4.3.2 artifacts are intentionally not
reused.

## Validation Results

| Check | Result |
| --- | --- |
| `npm run check:python` | PASS, compiled 689 modules |
| `node scripts/run_python.mjs -m ruff check .` | PASS |
| `npm run lint` | PASS |
| `npm run typecheck` | PASS, frontend TypeScript and VS Code extension build |
| `npm run test:unit` | PASS, 602 tests |
| `LTCAI_TEST_BASE_URL=http://127.0.0.1:4932 npm run test:integration` | PASS, 9 tests and 1 consent-gated Postgres migration test skipped |
| `npm run test:visual` | PASS, 12 Playwright tests |
| `npm run desktop:tauri:check` | PASS |
| `npm run release:artifacts` | PASS |
| `npm run release:validate` | PASS for exact v4.3.3 wheel, sdist, npm tgz, VSIX, and DMG |
| `node scripts/run_python.mjs scripts/wheel_smoke.py --wheel dist/ltcai-4.3.3-py3-none-any.whl` | PASS, `/health` reported 4.3.3 |
| `npm pack --dry-run` | PASS, `ltcai-4.3.3.tgz`, 292 files, 2.9 MB |
| `node scripts/run_python.mjs -m twine check dist/ltcai-4.3.3-py3-none-any.whl dist/ltcai-4.3.3.tar.gz` | PASS |
| `npm run docs:check-links` | PASS, README and 16 README-linked Markdown files |
| `npm run vercel:build` | PASS, static placeholder generated for 4.3.3 |

## Artifacts

| Artifact | SHA-256 |
| --- | --- |
| `dist/ltcai-4.3.3-py3-none-any.whl` | `716d0029a6053edacde13cad978f38e41a9c67b458eba37421ae5594e7b99948` |
| `dist/ltcai-4.3.3.tar.gz` | `77c19e9ad3e0cea757265b707b6414aef53a9d132f0456b7f0c36bcf09ece6b4` |
| `ltcai-4.3.3.tgz` | `fb29c81152fe13854e6c3ba07f5ba8b8b14f25eedbb1920600d05ef768bec044` |
| `dist/ltcai-4.3.3.vsix` | `28f243dbea1d0c49f78eb09176ae5c1f95d0d8530e49a103664dd338e53eac88` |
| `src-tauri/target/release/bundle/dmg/Lattice AI_4.3.3_aarch64.dmg` | `e717a53f83762799a32e2fa1e23c9a6fd5b2b2221bb503d12e7940e57a3455a2` |

## Notes

- `npm run release:validate` warned that historical artifacts remain in
  `dist/`; uploads must use only the exact v4.3.3 filenames above.
- The live Postgres migration test remains skipped unless the owner explicitly
  sets Docker/Postgres consent for that destructive external dependency path.
- External registry publishing was not attempted.
