# Lattice AI

[![PyPI Version](https://img.shields.io/pypi/v/ltcai?label=PyPI)](https://pypi.org/project/ltcai/)
[![npm Version](https://img.shields.io/npm/v/ltcai?label=npm)](https://www.npmjs.com/package/ltcai)
[![VS Code Marketplace Version](https://vsmarketplacebadges.dev/version-short/parktaesoo.ltcai.svg)](https://marketplace.visualstudio.com/items?itemName=parktaesoo.ltcai)
[![Open VSX Version](https://img.shields.io/open-vsx/v/parktaesoo/ltcai?label=Open%20VSX)](https://open-vsx.org/extension/parktaesoo/ltcai)
[![CI Status](https://github.com/TaeSooPark-PTS/LatticeAI/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/TaeSooPark-PTS/LatticeAI/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**Lattice AI v4.6.0 is a local-first Living Brain desktop workspace.** It runs
as a Tauri desktop app with a localhost FastAPI sidecar, stores the user's brain
locally by default, and makes the Brain plus conversation the primary product
experience.

This README describes the v4.6.0 release-preparation tree, which preserves the
v4.4.0 physical `lattice_brain` extraction, v4.5.0 capability recovery, and
v4.5.1 product shell while repositioning the graph as a deep exploration layer.
The home screen is now the living Brain conversation; memories, knowledge,
relationships, and the advanced graph are progressively disclosed beneath it.
External package registries are owner-published; the badges above link to
package pages and may show the most recently published owner-controlled registry
version, which can lag behind the GitHub Release.

## What Was Verified

- Desktop app starts and serves the React/Vite product shell from the FastAPI
  sidecar.
- The home route opens directly to a living Brain presence plus durable
  conversation, with the Brain always visible during the primary chat flow.
- The Brain presence reacts to conversation, recall, model readiness, and
  agent/workflow activity with neural movement, memory pulses, and status
  changes.
- Memories, Knowledge, Relationships, and Graph are ordered as progressive
  disclosure layers; the graph is no longer the product's first impression.
- Capture uploads a document through `/upload/document` and shows indexed
  documents from the backend.
- Act exposes workflow create/run surfaces and agent runtime status without
  presenting simulation as real success. When no LLM-backed model is loaded,
  deterministic model-free agent simulation is reported honestly and does not call a model.
- Library shows model/runtime availability honestly.
- First-run setup remains available through setup/system routes without
  displacing the Brain-first home experience.
- Gemma 4 MLX models are checked against their local `config.json` before load:
  Gemma 4 12B `gemma4_unified` now shows **Runtime update needed** when the
  installed MLX-VLM lacks `mlx_vlm.models.gemma4_unified`, while Gemma 4 26B
  A4B stays on the working `gemma4` MLX-VLM path.
- System exposes storage, backup health, archive, Brain Network, device
  identity, and admin status through real APIs.
- Backup, restore dry-run, archive verify, archive import dry-run, and
  `.latticebrain` portability flows were exercised.

## Product Tour

### Desktop Startup

The v4.6.0 DMG build launches a visible Tauri app, starts the FastAPI sidecar on
localhost, and shuts that sidecar down on normal macOS quit.

![Desktop startup and local sidecar](output/audits/v4.3.2-rc/screenshots/13-desktop-sidecar-startup.png)

Evidence:

- Health log: `output/audits/v4.3.2-rc/logs/desktop-sidecar-health-after-shutdown-fix.json`
- Shutdown log: `output/audits/v4.3.2-rc/logs/desktop-shutdown-after-fix.txt`

### Living Brain

Brain opens on a living presence and conversation. The visual Brain is not a
logo or static decoration: it changes state while the user types, when memories
are recalled, while a response streams, and when agent/workflow activity is
reported.

The advanced graph still exists, but it is intentionally opened from the Graph
layer after the user moves through Brain, Memories, Knowledge, and
Relationships.

![Brain graph explorer](output/audits/v4.3.2-rc/screenshots/02-graph-explorer-before.png)

![Graph product walkthrough](output/audits/v4.3.2-rc/gifs/graph-product-walkthrough.gif)

### Conversation

Conversation uses backend conversation, model, and memory APIs. If no model is
loaded, the UI shows a ready/unavailable state instead of fabricating an answer
while still keeping nearby memory and source signals visible.

![Conversation with context and no-model honesty](output/audits/v4.3.2-rc/screenshots/14-ask-context.png)

### Capture

Capture sends files through the backend upload/ingestion path and shows indexed
documents returned by the backend. The screenshot below includes a note uploaded
during release-prep evidence capture.

![Capture document ingestion](output/audits/v4.3.2-rc/screenshots/15-capture-ingestion.png)

Upload evidence: `output/audits/v4.3.2-rc/logs/readme-upload-note.json`

### Act

Act exposes agents, workflows, approvals, hooks, and runtime status through
existing backend APIs. The audited workflow path created a real workflow record;
agent runtime simulation remains honestly unavailable as product success when no
LLM-backed model is loaded.

![Workflow create and run surfaces](output/audits/v4.3.2-rc/screenshots/09-workflow-create-run.png)

![Agent runtime status](output/audits/v4.3.2-rc/screenshots/10-agent-runtime-status.png)

### Library

Library shows models, skills, MCP, and marketplace/configuration state with
availability reported from runtime APIs. Optional model runtimes are not treated
as loaded unless the runtime is actually available. Model setup now follows the
explicit Environment Analysis -> Recommended Models -> Install -> Download
Progress -> Validate -> Load -> Ready path, with download/install consent kept
visible.

![Library model status](output/audits/v4.3.2-rc/screenshots/12-library-model-status.png)

### System

System exposes account/workspace status, storage mode, backup health, archive
operations, device identity, Brain Network, and admin hardening state. External
integrations remain opt-in.

![System storage status](output/audits/v4.3.2-rc/screenshots/08-system-storage-status.png)

![Brain Network and device identity](output/audits/v4.3.2-rc/screenshots/11-brain-network-device-identity.png)

### Backup / Restore

Backup and restore flows are backed by the knowledge-graph portability APIs.
The retained v4.3.2 product audit exercised backup health, backup creation, restore dry-run, and
archive restore surfaces.

![Brain backup and portability controls](output/audits/v4.3.2-rc/screenshots/06-brain-portability-backup.png)

### `.latticebrain` Portability

The portable brain format is an encrypted `.latticebrain` archive. The audited
System flow exercised archive export, inspect, verify, import dry-run, and
restore dry-run/restore controls through FastAPI.

![System archive flows](output/audits/v4.3.2-rc/screenshots/07-system-archive-flows.png)

Archive evidence:

- Create: `output/audits/v4.3.2-rc/logs/archive-create.json`
- Verify: `output/audits/v4.3.2-rc/logs/archive-verify.json`
- Import dry-run: `output/audits/v4.3.2-rc/logs/archive-import-dry-run.json`

## Architecture At A Glance

- **Desktop shell**: Tauri 2 primary shell; Electron remains fallback-only.
- **Frontend**: React, TypeScript, Vite, TanStack Query, Zustand, Cytoscape.js,
  React Flow, Tailwind/shadcn-style primitives, generated OpenAPI client.
- **Backend**: FastAPI on localhost is the source of truth for the UI.
- **Brain Core**: independent Python package `lattice_brain` (graph, memory,
  context, conversations, ingestion, agent/hook runtime, workflow,
  portability, storage) physically hosted in the package, imported by FastAPI,
  CLI, tests, and future tools, and guaranteed by tests to never import
  `latticeai`.
- **Storage**: `StorageEngine` abstraction with SQLite default and optional
  PostgreSQL/pgvector scale mode.
- **Portability**: encrypted `.latticebrain` archive plus backup/restore and
  migration tooling.
- **Privacy**: local-first by default; cloud models, Telegram, Brain Network,
  Docker, model downloads, and update checks require explicit opt-in paths.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the detailed v4 architecture. v4.6.0
does not redesign the v4.4.0 Brain Core extraction, StorageEngine, FastAPI,
Tauri, backup/restore, or portability architecture.

## Installation And Release Artifacts

Validated v4.6.0 artifacts are produced from the Living Brain tree:

- `dist/ltcai-4.6.0-py3-none-any.whl`
- `dist/ltcai-4.6.0.tar.gz`
- `ltcai-4.6.0.tgz`
- `dist/ltcai-4.6.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.6.0_aarch64.dmg`

For a public release, attach only those exact artifacts to the GitHub Release.
Package-registry publishing is reserved for the owner.

## Local Development

```bash
npm install
npm run dev
```

Open the local app:

```text
http://127.0.0.1:4825/app
```

Build the release artifacts:

```bash
npm run release:artifacts
npm run release:validate
```

Run the main validation set:

```bash
npm run check:python
node scripts/run_python.mjs -m ruff check .
npm run lint
npm run typecheck
npm run test:unit
LTCAI_TEST_BASE_URL=http://127.0.0.1:4932 npm run test:integration
npm run test:visual
npm run desktop:tauri:check
node scripts/run_python.mjs scripts/wheel_smoke.py --wheel dist/ltcai-X.Y.Z-py3-none-any.whl
npm pack --dry-run
```

## Known Limitations

- External package registries are owner-published and can lag behind the GitHub
  Release.
- PostgreSQL/pgvector is optional scale mode and is not required for default
  local use.
- Docker is never auto-started by default.
- Model downloads and cloud model calls require explicit user action/consent.
- Conversation does not fabricate answers when no model is loaded.
- Historical artifacts may remain in `dist/`; release uploads must use exact
  v4.6.0 filenames for this release.

## Release History

| Version | Theme |
| --- | --- |
| 4.6.0 | Living Brain Experience: made Brain plus conversation the home product, added an animated living Brain presence, and moved graph exploration to the deepest intentional layer |
| 4.5.1 | Product Reimagining RC: replaced the desktop shell, navigation model, onboarding journey, first-viewport hierarchy, and visual system while preserving capabilities and local-first architecture |
| 4.5.0 | Product Experience Recovery RC: restored first-run setup, workspace/model onboarding, explicit model install/download/validate/load flow, Gemma 4 runtime compatibility gating, Basic-mode polish, and graph discoverability |
| 4.4.0 | Brain Engine Extraction: Brain Core physically moved into `lattice_brain` (graph/memory/context/conversation/ingestion/runtime/workflow/portability), latticeai paths reduced to compatibility shims, isolation tests guaranteeing no `latticeai` imports |
| 4.3.3 | Dead-Code Cleanup Release: post-audit cleanup, architecture documentation correction, Vercel/static-docs readiness, README badge restoration, exact-current artifacts |
| 4.3.2 | Product Polish & Graph UX Overhaul RC: evidence-based README, graph UX, structured product state, archive UX, desktop sidecar cleanup, publishing readiness |
| 4.3.1 | End-User Audit Repair RC: desktop sidecar startup, npm clean install, default-off downloads, honest agent/workflow states |
| 4.3.0 | Portability & Product Hardening RC: encrypted `.latticebrain` archives, backup/restore hardening, local-only startup guards |
| 4.2.0 | Brain Core & Storage Rebuild: independent `lattice_brain`, StorageEngine abstraction, SQLite default, opt-in Postgres/pgvector |
| 4.1.0 | Frontend & Desktop Rebuild: React/Vite SPA, Tauri shell, generated OpenAPI client |
| 4.0.1 | Digital Brain maintenance: durable async runs, identity/workspace state, `/app` parity |
| 4.0.0 | Digital Brain Platform foundation |
| 3.0.0 | v3 local-first AI workspace platform |

## Current Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) - v4 architecture.
- [FEATURE_STATUS.md](FEATURE_STATUS.md) - current feature status and historical
  status ledger.
- [RELEASE_NOTES.md](RELEASE_NOTES.md) - current release notes index.
- [RELEASE_NOTES_v4.6.0.md](RELEASE_NOTES_v4.6.0.md) - v4.6.0 Living Brain release notes.
- [RELEASE_NOTES_v4.5.1.md](RELEASE_NOTES_v4.5.1.md) - v4.5.1 RC release notes.
- [RELEASE_NOTES_v4.5.0.md](RELEASE_NOTES_v4.5.0.md) - v4.5.0 RC release notes.
- [RELEASE_NOTES_v4.4.0.md](RELEASE_NOTES_v4.4.0.md) - v4.4.0 release notes.
- [RELEASE_NOTES_v4.3.3.md](RELEASE_NOTES_v4.3.3.md) - v4.3.3 release notes.
- [RELEASE.md](RELEASE.md) - release checklist and exact artifact guidance.
- [SECURITY.md](SECURITY.md) - security posture.
- [docs/CHANGELOG.md](docs/CHANGELOG.md) - changelog.
- [docs/V4_3_2_DEADCODE_AUDIT_REPORT.md](docs/V4_3_2_DEADCODE_AUDIT_REPORT.md) - v4.3.3 cleanup basis.
- [docs/V4_3_3_VALIDATION_REPORT.md](docs/V4_3_3_VALIDATION_REPORT.md) - v4.3.3 validation report.
- [docs/V4_5_0_PRODUCT_EXPERIENCE_RECOVERY_REPORT.md](docs/V4_5_0_PRODUCT_EXPERIENCE_RECOVERY_REPORT.md) - v4.5.0 product recovery report.
- [docs/V4_5_0_VALIDATION_REPORT.md](docs/V4_5_0_VALIDATION_REPORT.md) - v4.5.0 validation report.
- [docs/V4_5_1_PRODUCT_REIMAGINING_REPORT.md](docs/V4_5_1_PRODUCT_REIMAGINING_REPORT.md) - v4.5.1 product reimagining report.
- [docs/V4_5_1_UX_REPORT.md](docs/V4_5_1_UX_REPORT.md) - v4.5.1 UX report.
- [docs/V4_5_1_VISUAL_DESIGN_REPORT.md](docs/V4_5_1_VISUAL_DESIGN_REPORT.md) - v4.5.1 visual design report.
- [docs/V4_5_1_NAVIGATION_REPORT.md](docs/V4_5_1_NAVIGATION_REPORT.md) - v4.5.1 navigation report.
- [docs/V4_5_1_ONBOARDING_REPORT.md](docs/V4_5_1_ONBOARDING_REPORT.md) - v4.5.1 onboarding report.
- [docs/V4_5_1_MODEL_EXPERIENCE_REPORT.md](docs/V4_5_1_MODEL_EXPERIENCE_REPORT.md) - v4.5.1 model experience report.
- [docs/V4_5_1_GRAPH_EXPERIENCE_REPORT.md](docs/V4_5_1_GRAPH_EXPERIENCE_REPORT.md) - v4.5.1 graph experience report.
- [docs/V4_5_1_VALIDATION_REPORT.md](docs/V4_5_1_VALIDATION_REPORT.md) - v4.5.1 validation report.
- [docs/V4_5_1_RC_ARTIFACTS.md](docs/V4_5_1_RC_ARTIFACTS.md) - v4.5.1 screenshot, GIF, and RC artifact manifest.
- [docs/V4_6_0_LIVING_BRAIN_EXPERIENCE_REPORT.md](docs/V4_6_0_LIVING_BRAIN_EXPERIENCE_REPORT.md) - v4.6.0 Living Brain design and validation notes.
- [docs/V4_3_2_GRAPH_UX_REPORT.md](docs/V4_3_2_GRAPH_UX_REPORT.md) - graph UX report.
- [docs/V4_3_2_PRODUCT_POLISH_REPORT.md](docs/V4_3_2_PRODUCT_POLISH_REPORT.md) - product polish report.
- [docs/V4_3_2_SELF_AUDIT_REPORT.md](docs/V4_3_2_SELF_AUDIT_REPORT.md) - self-audit evidence.
- [docs/V4_3_2_VALIDATION_REPORT.md](docs/V4_3_2_VALIDATION_REPORT.md) - RC validation report.
- [docs/V4_3_2_DOCUMENTATION_CLEANUP_REPORT.md](docs/V4_3_2_DOCUMENTATION_CLEANUP_REPORT.md) - release-prep documentation cleanup report.
- [docs/V4_3_2_GITHUB_VERCEL_CHECK_REPORT.md](docs/V4_3_2_GITHUB_VERCEL_CHECK_REPORT.md) - GitHub/Vercel readiness report.
- [docs/V4_3_2_INDEPENDENT_AUDIT_PACKAGE.md](docs/V4_3_2_INDEPENDENT_AUDIT_PACKAGE.md) - package for an independent reviewer.
- [docs/V4_3_2_DEADCODE_AUDIT_REPORT.md](docs/V4_3_2_DEADCODE_AUDIT_REPORT.md) - independent dead-code, architecture, and runtime audit.
- [docs/V4_DIGITAL_BRAIN_RECOVERY.md](docs/V4_DIGITAL_BRAIN_RECOVERY.md) - transformation recovery file.

## License

MIT. See [LICENSE](LICENSE).
