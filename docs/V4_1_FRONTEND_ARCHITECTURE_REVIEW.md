# v4.1.0 Frontend Architecture Review

## Scope

This review covers the v4.1.0 replacement of the `/app` frontend and desktop
shell. It does not redesign Brain Core, storage, backend API contracts, or the
agent/workflow runtime.

## Baseline

v4.0.1 served a static frontend assembled under `static/v3` with bespoke build
and lint scripts. Capability parity existed, but the implementation duplicated
view systems, carried legacy static route assumptions, and could not cleanly
share typed API contracts with the FastAPI backend.

## Target Architecture

- Desktop shell: Tauri 2.0 primary, Electron fallback only.
- Frontend: React, TypeScript, Vite, TanStack Query, Zustand, React Flow,
  Cytoscape.js, Tailwind CSS, local shadcn-style primitives.
- API: generated OpenAPI TypeScript client from the existing FastAPI app.
- Runtime: single client-side SPA, no SSR, no CDN.
- Backend: existing FastAPI app remains the source of truth.

## Implemented Architecture

- `frontend/` owns the React/Vite source.
- `frontend/src/api/openapi.ts` is generated from `frontend/openapi.json`.
- `frontend/src/api/client.ts` wraps generated-client JSON calls and keeps
  streaming chat plus multipart upload as explicit fetch special cases.
- `frontend/src/routes.ts` defines Brain, Ask, Capture, Act, Library, System and
  maps legacy hash routes into those groups.
- `static/app` is the shipped build output and is served by `/app`.
- `src-tauri/` contains the Tauri 2.0 shell and backend-origin bridge.
- `desktop/electron/` contains the fallback shell.
- `static/sw.js` precaches the React app manifest/assets for offline-capable
  local startup.

## Boundary Decisions

- The backend contract was preserved; only the frontend consumer changed.
- Brain Core and storage modules were not modified for UI convenience.
- No CDN fallback was introduced; all visual/runtime dependencies are bundled.
- No demo-only controls were added. Unavailable capabilities render explicit
  unavailable/error states based on API responses.
- npm runtime installs are kept lean: React/Vite/Tauri/Electron toolchains are
  development dependencies because the distributable app ships built assets.

## Capability Mapping

| v4.0.1 capability | v4.1.0 surface |
| --- | --- |
| Knowledge Graph, hybrid search, memory, provenance, portability | Brain |
| Chat, conversations, context trace, attachments | Ask |
| Uploads, connected folders, local runtime, URL capture, index pipeline | Capture |
| Agents, runs, approvals, workflows, triggers, hooks, tools | Act |
| Models, embeddings, skills, MCP, templates/plugins | Library |
| Account, auth, workspaces, snapshots, activity, network, settings, admin/security | System |

## Residual Risk

- The first Vite bundle is larger than 500 kB after minification because graph,
  workflow, and app shell libraries are included in the initial desktop SPA
  bundle. This is a performance optimization target, not a capability blocker,
  and the build remains fully local/offline.
