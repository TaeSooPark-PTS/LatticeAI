# v4.1.0 Frontend Migration Report

## Objective

Replace the v4.0.1 static frontend implementation with the Digital Brain
desktop architecture while preserving capabilities, user data, backend API
contracts, and local-first/offline operation.

## Migration Summary

- Added React + TypeScript + Vite source under `frontend/`.
- Added generated OpenAPI schema/client files under `frontend/openapi.json` and
  `frontend/src/api/openapi.ts`.
- Added React capability pages for Brain, Ask, Capture, Act, Library, System.
- Added Tauri 2.0 desktop shell under `src-tauri/`.
- Added Electron fallback shell under `desktop/electron/`.
- Replaced `/app` static serving with `static/app/index.html`.
- Replaced v3 asset build/lint scripts with Vite build and frontend lint guards.
- Removed `static/v3` and retired `scripts/build_v3_assets.mjs` /
  `scripts/lint_v3.mjs`.
- Updated package metadata so Python wheel/sdist, npm tgz, and VSIX builds ship
  the new `static/app` assets.

## Compatibility

- Existing FastAPI routes are preserved.
- The generated OpenAPI client is produced from the live app schema.
- Legacy app hash routes are mapped into the new primary navigation groups.
- Service worker caching now targets the Vite app manifest instead of v3 assets.
- User data formats, Brain database files, Workspace OS state, snapshots,
  conversations, memories, and graph data are not migrated or destructively
  changed by the frontend rebuild.

## Capability Parity

| Capability | Migration result |
| --- | --- |
| Brain graph exploration | Cytoscape.js graph view in Brain |
| Hybrid search | Brain search tab backed by `/api/search/hybrid` |
| Memory recall | Brain memory tab backed by memory APIs |
| Provenance and portability | Brain provenance/portability actions |
| Chat and context trace | Ask surface with streaming chat and context panel |
| Conversation management | Ask history backed by conversation APIs |
| Document upload | Capture upload using multipart backend endpoint |
| Connected folders/local runtime | Capture desktop/local runtime APIs |
| Index pipeline and URL capture | Capture pipeline/browser ingestion APIs |
| Agents and run records | Act agents/runs/approvals backed by runtime APIs |
| Workflow graph | Act React Flow visualization and workflow APIs |
| Triggers, hooks, tools | Act surfaces backed by trigger/hook/tool APIs |
| Models and embeddings | Library model/embedding APIs |
| Skills, MCP, templates/plugins | Library registry APIs |
| Account/auth/profile/password | System account APIs |
| Workspaces/invitations | System workspace APIs |
| Snapshots/time-machine | System snapshot APIs |
| Activity/presence/network | System activity and network APIs |
| Admin/security/settings | System admin and settings APIs |

## Removed Frontend Debt

- Duplicate static v3 view modules.
- Legacy frontend build pipeline.
- Legacy v3 frontend lint script.
- Static v3 asset tree in release packages.
- Direct then-current release references to v4.0.1 in then-latest docs.

## Data Preservation

No user data migration is required for v4.1.0. All persisted state remains owned
by the existing backend stores. The frontend rebuild only changes the client and
desktop shell used to access those stores.
