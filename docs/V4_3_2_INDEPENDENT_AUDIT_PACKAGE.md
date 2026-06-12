# Lattice AI v4.3.2 Independent Audit Package

Date: 2026-06-13

This package is for a separate AI or human reviewer. It summarizes what to
inspect and how to reproduce validation. It is not itself an independent audit.

## Current Architecture Summary

- Desktop: Tauri 2 primary shell in `src-tauri/`; Electron fallback in
  `desktop/electron/`.
- Frontend: React + TypeScript + Vite SPA in `frontend/`, shipped from
  `static/app/`.
- API: FastAPI localhost backend; frontend uses generated OpenAPI types and
  does not call Python directly.
- Brain Core: independent Python package `lattice_brain`.
- Storage: `StorageEngine` abstraction with SQLite default and optional
  PostgreSQL/pgvector scale mode.
- Portability: encrypted `.latticebrain` archives, backup/restore, inspect,
  verify, import dry-run, and restore dry-run/restore APIs.
- Privacy: default startup is local-only; external integrations and downloads
  require explicit opt-in paths.

## README Claims To Verify

- Desktop startup and sidecar shutdown.
- Brain graph search, grouping, focus, filtering, and graph persistence.
- Ask no-model honesty and graph context.
- Capture document ingestion.
- Act workflow create/run surfaces and honest agent runtime status.
- Library model/runtime availability.
- System storage, backup, archive, Brain Network, and device identity status.
- Backup/restore and `.latticebrain` portability flows.
- Exact v4.3.2 artifact readiness.

## Self-Audit Evidence Paths

- Root evidence folder: `output/audits/v4.3.2-rc/`
- Screenshots: `output/audits/v4.3.2-rc/screenshots/`
- GIF: `output/audits/v4.3.2-rc/gifs/graph-product-walkthrough.gif`
- Runtime logs: `output/audits/v4.3.2-rc/logs/`
- Video: `output/audits/v4.3.2-rc/videos/graph-product-walkthrough.webm`

## Key Screenshots / GIFs

- Desktop startup:
  `output/audits/v4.3.2-rc/screenshots/13-desktop-sidecar-startup.png`
- Brain graph:
  `output/audits/v4.3.2-rc/screenshots/02-graph-explorer-before.png`
- Graph walkthrough:
  `output/audits/v4.3.2-rc/gifs/graph-product-walkthrough.gif`
- Ask:
  `output/audits/v4.3.2-rc/screenshots/14-ask-context.png`
- Capture:
  `output/audits/v4.3.2-rc/screenshots/15-capture-ingestion.png`
- Act:
  `output/audits/v4.3.2-rc/screenshots/09-workflow-create-run.png`
- Library:
  `output/audits/v4.3.2-rc/screenshots/12-library-model-status.png`
- System:
  `output/audits/v4.3.2-rc/screenshots/08-system-storage-status.png`
- Backup/restore:
  `output/audits/v4.3.2-rc/screenshots/06-brain-portability-backup.png`
- `.latticebrain`:
  `output/audits/v4.3.2-rc/screenshots/07-system-archive-flows.png`

## Validation Results

See `docs/V4_3_2_VALIDATION_REPORT.md`.

Expected green checks:

- Python compile
- Ruff
- unit tests
- live integration tests
- frontend lint
- TypeScript typecheck
- Playwright visual tests
- Tauri check/build
- release artifact validation
- wheel smoke
- npm pack dry-run
- README-linked Markdown link check
- Vercel static build

## Commands To Reproduce

```bash
npm install
npm run check:python
node scripts/run_python.mjs -m ruff check .
npm run lint
npm run typecheck
npm run test:unit
LTCAI_TEST_BASE_URL=http://127.0.0.1:4932 npm run test:integration
npm run test:visual
npm run desktop:tauri:check
npm run release:artifacts
npm run release:validate
node scripts/run_python.mjs scripts/wheel_smoke.py --wheel dist/ltcai-4.3.2-py3-none-any.whl
npm pack --dry-run
npm run docs:check-links
npm run vercel:build
```

For live integration tests, start a local backend first or set
`LTCAI_TEST_BASE_URL` to an already running test backend.

## Known Limitations

- v4.3.2 is not tagged or released by this preparation work.
- External registries are not published by this preparation work.
- Ask needs a loaded model for generated answers; no-model state is expected to
  be honest rather than successful.
- PostgreSQL/pgvector and Docker flows are optional and consent-gated.
- Live Postgres migration validation is not part of this release-prep pass
  without fresh explicit Docker consent.
- Vercel is documentation-only and intentionally does not host the product
  runtime.

## Changed Files Since v4.3.1

Exact manifest prepared for review:

```text
.gitignore
ARCHITECTURE.md
FEATURE_STATUS.md
README.md
RELEASE.md
RELEASE_NOTES.md
RELEASE_NOTES_v4.3.2.md
SECURITY.md
docs/CHANGELOG.md
docs/V4_3_2_DOCUMENTATION_CLEANUP_REPORT.md
docs/V4_3_2_GITHUB_VERCEL_CHECK_REPORT.md
docs/V4_3_2_GRAPH_UX_REPORT.md
docs/V4_3_2_INDEPENDENT_AUDIT_PACKAGE.md
docs/V4_3_2_PRODUCT_POLISH_REPORT.md
docs/V4_3_2_SELF_AUDIT_REPORT.md
docs/V4_3_2_VALIDATION_REPORT.md
docs/V4_DIGITAL_BRAIN_RECOVERY.md
frontend/openapi.json
frontend/src/components/primitives.tsx
frontend/src/pages/Act.tsx
frontend/src/pages/Ask.tsx
frontend/src/pages/Brain.tsx
frontend/src/pages/Capture.tsx
frontend/src/pages/Library.tsx
frontend/src/pages/System.tsx
lattice_brain/__init__.py
latticeai/__init__.py
latticeai/core/marketplace.py
latticeai/core/multi_agent.py
latticeai/core/workspace_os.py
output/audits/v4.3.2-rc/gifs/graph-product-walkthrough.gif
output/audits/v4.3.2-rc/logs/archive-create.json
output/audits/v4.3.2-rc/logs/archive-import-dry-run.json
output/audits/v4.3.2-rc/logs/archive-verify.json
output/audits/v4.3.2-rc/logs/backup-health-after-ui.json
output/audits/v4.3.2-rc/logs/desktop-shutdown-after-fix.txt
output/audits/v4.3.2-rc/logs/desktop-sidecar-health-after-shutdown-fix.json
output/audits/v4.3.2-rc/logs/desktop-sidecar-health.json
output/audits/v4.3.2-rc/logs/graph-after-upload.json
output/audits/v4.3.2-rc/logs/readme-upload-note.json
output/audits/v4.3.2-rc/logs/self-audit-browser-results.json
output/audits/v4.3.2-rc/logs/storage.json
output/audits/v4.3.2-rc/logs/upload-note.json
output/audits/v4.3.2-rc/logs/workflow-create.json
output/audits/v4.3.2-rc/screenshots/01-first-startup.png
output/audits/v4.3.2-rc/screenshots/02-graph-explorer-before.png
output/audits/v4.3.2-rc/screenshots/03-graph-search.png
output/audits/v4.3.2-rc/screenshots/04-graph-collapse-group.png
output/audits/v4.3.2-rc/screenshots/05-graph-focus-neighborhood.png
output/audits/v4.3.2-rc/screenshots/06-brain-portability-backup.png
output/audits/v4.3.2-rc/screenshots/07-system-archive-flows.png
output/audits/v4.3.2-rc/screenshots/08-system-storage-status.png
output/audits/v4.3.2-rc/screenshots/09-workflow-create-run.png
output/audits/v4.3.2-rc/screenshots/10-agent-runtime-status.png
output/audits/v4.3.2-rc/screenshots/11-brain-network-device-identity.png
output/audits/v4.3.2-rc/screenshots/12-library-model-status.png
output/audits/v4.3.2-rc/screenshots/13-desktop-sidecar-startup.png
output/audits/v4.3.2-rc/screenshots/14-ask-context.png
output/audits/v4.3.2-rc/screenshots/15-capture-ingestion.png
output/audits/v4.3.2-rc/videos/graph-product-walkthrough.webm
package-lock.json
package.json
pyproject.toml
scripts/build_vercel_static.mjs
scripts/check_markdown_links.mjs
src-tauri/Cargo.lock
src-tauri/Cargo.toml
src-tauri/src/main.rs
src-tauri/tauri.conf.json
static/app/asset-manifest.json
static/app/assets/index-BhPuj8rT.js
static/app/assets/index-BhPuj8rT.js.map
static/app/assets/index-CHHal8Zl.css
static/app/assets/index-pdzil9ac.js
static/app/assets/index-pdzil9ac.js.map
static/app/assets/index-yZswHE3d.css
static/app/index.html
tests/visual/mock_server.cjs
tests/visual/v3.spec.js
vercel.json
vscode-extension/package-lock.json
vscode-extension/package.json
```
