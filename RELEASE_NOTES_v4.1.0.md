# Lattice AI v4.1.0 RC — Frontend & Desktop Rebuild

> Status: release candidate. This release builds and validates artifacts from
> `main @ v4.0.1` plus the frontend/desktop rebuild work. It does not publish to
> PyPI, npm Registry, VS Code Marketplace, Open VSX, or production deployment
> targets.

v4.1.0 replaces the frontend implementation with the Digital Brain desktop
architecture while preserving v4.0.1 capabilities and backend API contracts.

## Included

- React + TypeScript + Vite SPA for `/app`.
- TanStack Query, Zustand, React Flow, Cytoscape.js, Tailwind CSS, and local
  shadcn-style UI primitives.
- Generated OpenAPI TypeScript client from the existing FastAPI application.
- Tauri 2.0 primary desktop shell that launches the local backend.
- Electron fallback shell retained only as a fallback desktop runner.
- Primary navigation: Brain, Ask, Capture, Act, Library, System.
- Legacy static v3 frontend assets removed after capability migration.
- `static/app` Vite assets packaged for offline/local-first operation.

## Preserved

- Existing FastAPI backend as the source of truth.
- Brain Core, storage architecture, Knowledge Graph data, and user data
  compatibility.
- Durable agent/workflow runtime behavior, approvals, triggers, tools, MCP,
  models, workspaces, snapshots, activity, network, and admin/security surfaces.
- Local-first, privacy-first, and offline-capable operation.

## Expected Artifacts

- `dist/ltcai-4.1.0-py3-none-any.whl`
- `dist/ltcai-4.1.0.tar.gz`
- `dist/ltcai-4.1.0.vsix`
- `ltcai-4.1.0.tgz`

## Validation Scope

- Python compile check.
- Ruff.
- Unit tests.
- Live integration tests.
- Frontend lint.
- TypeScript build.
- Playwright visual tests.
- Generated OpenAPI client verification.
- Desktop shell verification.
- No CDN dependency verification.
- Offline/static app startup verification.
- Release artifact validation.
- Wheel smoke test.
- npm pack dry-run.

## External Registries

No PyPI, npm Registry, VS Code Marketplace, or Open VSX publish step is part of
this release candidate.
