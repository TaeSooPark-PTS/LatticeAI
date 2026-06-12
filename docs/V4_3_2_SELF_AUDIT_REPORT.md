# Lattice AI v4.3.2 Self-Audit Report

Date: 2026-06-13

## Method

The audit used the built v4.3.2 product, not source-code claims. It started a
local FastAPI backend with an isolated data directory, seeded a real document
through the upload API, opened the React SPA, exercised product flows, captured
screenshots/GIF evidence, launched the rebuilt Tauri app, and verified desktop
sidecar startup/shutdown.

## Runtime Inputs

- API base: `http://127.0.0.1:4932`
- Audit data dir: `/tmp/lattice-v432-audit`
- Desktop data dir: `/tmp/lattice-v432-desktop3`
- Uploaded note: `/tmp/lattice-v432-files/v432-audit-note.md`
- Archive path: `/tmp/lattice-v432-audit/self-audit.latticebrain`

## Observed Results

| Scenario | Result | Evidence |
| --- | --- | --- |
| First startup | PASS | `output/audits/v4.3.2-rc/screenshots/01-first-startup.png` |
| Graph exploration baseline | PASS | `output/audits/v4.3.2-rc/screenshots/02-graph-explorer-before.png` |
| Graph search/collapse/focus | PASS | `output/audits/v4.3.2-rc/screenshots/03-graph-search.png`, `04-graph-collapse-group.png`, `05-graph-focus-neighborhood.png` |
| Brain backup and graph portability | PASS | `output/audits/v4.3.2-rc/screenshots/06-brain-portability-backup.png` |
| System archive export/inspect/verify/import/restore dry-run | PASS | `output/audits/v4.3.2-rc/screenshots/07-system-archive-flows.png` |
| Storage and Postgres optional status | PASS | `output/audits/v4.3.2-rc/screenshots/08-system-storage-status.png` |
| Workflow create/run surfaces | PASS | `output/audits/v4.3.2-rc/screenshots/09-workflow-create-run.png` |
| Agent runtime availability | PASS | `output/audits/v4.3.2-rc/screenshots/10-agent-runtime-status.png` |
| Brain Network and device identity | PASS | `output/audits/v4.3.2-rc/screenshots/11-brain-network-device-identity.png` |
| Library model unavailable honesty | PASS | `output/audits/v4.3.2-rc/screenshots/12-library-model-status.png` |
| Desktop sidecar startup | PASS | `output/audits/v4.3.2-rc/screenshots/13-desktop-sidecar-startup.png` |
| Desktop sidecar shutdown | PASS | `output/audits/v4.3.2-rc/logs/desktop-shutdown-after-fix.txt` |

## Runtime Logs

- Upload result: `output/audits/v4.3.2-rc/logs/upload-note.json`
- Graph after upload: `output/audits/v4.3.2-rc/logs/graph-after-upload.json`
- Archive create: `output/audits/v4.3.2-rc/logs/archive-create.json`
- Archive verify: `output/audits/v4.3.2-rc/logs/archive-verify.json`
- Archive import dry-run: `output/audits/v4.3.2-rc/logs/archive-import-dry-run.json`
- Storage: `output/audits/v4.3.2-rc/logs/storage.json`
- Backup health: `output/audits/v4.3.2-rc/logs/backup-health-after-ui.json`
- Workflow create: `output/audits/v4.3.2-rc/logs/workflow-create.json`
- Browser automation results: `output/audits/v4.3.2-rc/logs/self-audit-browser-results.json`
- Desktop health: `output/audits/v4.3.2-rc/logs/desktop-sidecar-health-after-shutdown-fix.json`

## Media

- Graph walkthrough GIF: `output/audits/v4.3.2-rc/gifs/graph-product-walkthrough.gif`
- Graph walkthrough video: `output/audits/v4.3.2-rc/videos/graph-product-walkthrough.webm`

## Result

PASS. The observed product behavior supports the v4.3.2 RC claims. No placeholder
or fake functionality was accepted as complete. Optional capabilities remain
honestly unavailable unless their runtime dependencies and user consent exist.
