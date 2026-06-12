# Lattice AI v4.3.2 Validation Report

Date: 2026-06-13

## Summary

v4.3.2 validation is green for the implemented product polish, graph UX, desktop
shutdown, exact-version release artifacts, and the release-prep documentation /
Vercel static-build fixes.

## Release-Prep Fix Validation

These checks were rerun after the README badge restore, diagram-first
`ARCHITECTURE.md` rewrite, Vercel static-only configuration fix, and linked-doc
cleanup.

| Check | Result |
| --- | --- |
| `node -e "JSON.parse(require('fs').readFileSync('vercel.json','utf8'))"` | PASS |
| `npm run vercel:build` | PASS, generated `vercel-static/index.html` |
| `npm run docs:check-links` | PASS, README plus 15 README-linked Markdown files |
| Mermaid structural sanity check for `ARCHITECTURE.md` | PASS, 13 Mermaid blocks; `mmdc` was not installed locally |
| README badge link validation | PASS for PyPI, VS Code Marketplace, Open VSX, CI, license, and badge images; npm package page returned a CLI-only Cloudflare 403, so package identity was validated through `npm view ltcai` and `https://registry.npmjs.org/ltcai` |
| Registry version check | PASS, PyPI/npm/VS Code Marketplace/Open VSX currently report `4.3.1`; README does not claim v4.3.2 external registry publication |
| `node scripts/run_python.mjs -m pytest tests/unit/test_server_app_v14_decomposition.py::test_markdown_current_release_references_match_release tests/unit/test_truth_floor_t1_static.py::test_readme_does_not_overclaim_llm_driven_agents -v` | PASS, 2 passed |
| `npm run lint` | PASS |
| `npm pack --dry-run` | PASS, `ltcai-4.3.2.tgz`, 305 files, 2.9 MB |
| `npx --yes vercel@54.12.2 build` | BLOCKED by local Vercel linkage only: `project_settings_required`; no `.vercel` project settings or credentials are present in this checkout. Static build/config validation above passed. |

No product runtime code changed in this release-prep fix, and release artifacts
were not rebuilt.

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
| `npm run vercel:build` | PASS |
| Markdown link check for README-linked docs | PASS |

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
- GitHub Actions for the v4.3.2 RC commit were green before release-prep doc
  cleanup: `CI` and `Visual Smoke` both completed successfully for
  `8f3d182ee81bb395722ebab792dfd70f35e19e96`.
- Vercel config is intentionally documentation-only: it builds
  `vercel-static/index.html`, pins the Framework Preset to "Other", and does
  not attempt to auto-detect or host `server.py` / the desktop runtime.

## Result

PASS. v4.3.2 RC artifacts and runtime behavior are validated.
