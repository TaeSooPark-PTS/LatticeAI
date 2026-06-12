# Lattice AI v4.3.2 RC - Product Polish & Graph UX Overhaul

Release date: 2026-06-13

v4.3.2 is a release candidate on `main` after v4.3.1. It focuses on product
polish and Brain graph UX. It preserves the v4.3.1 frontend architecture,
Brain Core, storage layer, agent/workflow runtime, FastAPI contracts, and user
data compatibility.

No tag, GitHub Release, external package registry publish, or deployment is
part of this RC.

## Highlights

- Brain now opens on a semantic Cytoscape graph explorer backed by
  `/knowledge-graph/graph` and hybrid search.
- Graph controls include search, minimum importance filtering, type groups,
  collapse/expand, focused neighborhoods, label modes, importance sizing, and
  backend query results.
- Raw JSON dumps were removed from normal Brain, Ask, Capture, Act, Library,
  and System product flows in favor of structured status panels and operation
  results.
- System exposes `.latticebrain` archive export, inspect, verify, import
  dry-run, confirmed import, restore dry-run, confirmed restore, storage,
  backup health, Brain Network, and device identity through existing APIs.
- Tauri app-level exit handling now shuts down the FastAPI sidecar on normal
  macOS quit and releases port 8765.

## Validation

- `npm run check:python` - passed, compiled 691 modules.
- `node scripts/run_python.mjs -m ruff check .` - passed.
- `npm run lint` - passed.
- `npm run typecheck` - passed.
- `npm run test:unit` - passed, 602 tests.
- `LTCAI_TEST_BASE_URL=http://127.0.0.1:4932 npm run test:integration` -
  passed, 9 tests and 1 consent-gated Postgres migration test skipped.
- `npm run test:visual` - passed, 12 Playwright tests.
- `npm run desktop:tauri:check` - passed.
- `npm run release:artifacts` - passed.
- `npm run release:validate` - passed for exact v4.3.2 artifacts.
- `node scripts/run_python.mjs scripts/wheel_smoke.py --wheel dist/ltcai-4.3.2-py3-none-any.whl`
  - passed.
- `npm pack --dry-run` - passed.
- Rebuilt desktop app startup/shutdown verification - passed.

## Artifacts

- `dist/ltcai-4.3.2-py3-none-any.whl`
- `dist/ltcai-4.3.2.tar.gz`
- `dist/ltcai-4.3.2.vsix`
- `ltcai-4.3.2.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.3.2_aarch64.dmg`

## Evidence

- Screenshots: `output/audits/v4.3.2-rc/screenshots/`
- GIF: `output/audits/v4.3.2-rc/gifs/graph-product-walkthrough.gif`
- Runtime logs: `output/audits/v4.3.2-rc/logs/`
- Validation report: `docs/V4_3_2_VALIDATION_REPORT.md`
