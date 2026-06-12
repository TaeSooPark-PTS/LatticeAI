# Lattice AI v4.3.2 Architecture

Lattice AI v4.3.2 is a local-first Digital Brain desktop product. The user-facing
product is a Tauri desktop shell that loads a React/Vite SPA and talks only to
the local FastAPI backend. The backend owns the Brain Core, storage engines,
archive/backup operations, model routing, agents, workflows, and all user data.

This document describes current v4.3.2 behavior. Older v3/v4 architecture notes
remain in `docs/` as historical records and should not be read as current
runtime claims unless they are explicitly updated for v4.3.2.

## Runtime Shape

```text
Tauri desktop shell
  -> starts/stops local FastAPI sidecar
  -> serves React/Vite SPA at /app
  -> exposes backend origin/status to the UI

React + TypeScript + Vite frontend
  -> generated OpenAPI client
  -> FastAPI localhost APIs only
  -> no direct Python calls
  -> no SSR and no CDN dependency

FastAPI backend
  -> imports lattice_brain
  -> owns Brain Core and application services
  -> exposes API contracts consumed by the frontend

lattice_brain package
  -> Knowledge Graph
  -> memory/conversation/context systems
  -> ingestion/provenance
  -> portability/archive support
  -> storage abstraction

StorageEngine
  -> SQLiteEngine default local brain
  -> PostgresEngine optional scale mode
```

## Desktop Shell

- Primary shell: Tauri 2 in `src-tauri/`.
- Fallback shell: Electron in `desktop/electron/`.
- The Tauri app launches the FastAPI backend on localhost, serves the SPA, and
  kills the sidecar on normal window close and app-level quit events.
- v4.3.2 validation confirmed the rebuilt desktop app responds on
  `127.0.0.1:8765` and releases the port after macOS quit.

## Frontend

Source lives in `frontend/`; built assets are packaged under `static/app/`.

The current frontend stack is:

- React
- TypeScript
- Vite
- TanStack Query
- Zustand
- Cytoscape.js for Brain graph exploration
- React Flow for workflow graph surfaces
- Tailwind CSS and local shadcn-style primitives
- Generated OpenAPI types in `frontend/src/api/openapi.ts`

Primary navigation:

- Brain
- Ask
- Capture
- Act
- Library
- System

Every normal visible control must call an existing backend API or show an honest
unavailable state. v4.3.2 specifically removes raw JSON dumps from normal product
flows and replaces them with structured operation/result/status views.

## FastAPI Backend

FastAPI remains the source of truth. The frontend never calls Python directly.
All product capability surfaces go through localhost API contracts.

Main backend responsibilities:

- health/mode/runtime status
- Knowledge Graph and hybrid search
- ingestion and provenance
- conversations and memory
- model catalog/load/status
- agent runtime and workflow runtime
- approvals, hooks, tools, skills, plugins, and MCP registry surfaces
- workspace/account/admin/security status
- storage status, backup/restore, archive import/export/verify
- Brain Network identity and peer status

## Brain Core

`lattice_brain` is the independent Python package boundary for Brain Core. It is
usable by FastAPI, CLI, tests, and future tools.

Current package responsibilities include:

- Knowledge Graph store and compatibility shims
- memory and conversation primitives
- context assembly and retrieval helpers
- ingestion/provenance support
- archive/portability support
- storage engine interfaces and implementations

Compatibility shims such as `knowledge_graph.py`, `kg_schema.py`, and
`knowledge_graph_api.py` remain for old imports, but FastAPI construction routes
through the package/application services.

## Storage Layer

Storage is selected through `StorageEngine`.

### SQLite Default

SQLite is the default and required local-first mode. It stores the local brain,
Knowledge Graph, durable conversations, workspace state, provenance, and related
runtime data.

Vector search behavior is honest:

- sqlite-vec is reported when available.
- When sqlite-vec is unavailable, the product reports the real fallback rather
  than pretending sqlite-vec is active.

### PostgreSQL / pgvector Optional Scale Mode

PostgreSQL is opt-in scale mode. It is never required for the default desktop
brain.

- `PostgresEngine` requires explicit configuration.
- pgvector capability is verified and reported honestly.
- SQLite-to-Postgres migration is explicit and fail-closed.
- Docker setup is consent-gated and never auto-started.

## Backup, Restore, And `.latticebrain`

v4.3.2 includes user-facing archive/backup surfaces backed by existing FastAPI
APIs.

The encrypted `.latticebrain` archive is the portable brain format. It supports
export, inspect, verify, import dry-run, confirmed import, restore dry-run, and
confirmed restore.

Safety rules:

- destructive import/restore requires explicit confirmation
- corrupt or unsupported archives fail closed
- wrong passphrase/signature/version mismatch is reported honestly
- existing local-first SQLite brains remain the default path

## Brain Network

Brain Network uses local device identity metadata and signed bundle semantics.
External peer communication is opt-in. Token or identity presence alone does not
start external communication.

The v4.3.2 UI exposes:

- device identity
- peer list/status
- Brain Network availability
- signed bundle status where available

## Privacy And Local-First Guarantees

Default startup is local-only:

- localhost backend
- SQLite default storage
- no cloud model call by default
- no Telegram bridge by default
- no Brain Network egress by default
- no Docker startup by default
- no model download or runtime install without explicit user action
- no CDN dependency in shipped app assets

External integrations are represented as opt-in capabilities. When dependencies
or consent are missing, the product should show an unavailable state rather than
silently falling back or faking success.

## Release Artifacts

v4.3.2 validated artifacts:

- `dist/ltcai-4.3.2-py3-none-any.whl`
- `dist/ltcai-4.3.2.tar.gz`
- `ltcai-4.3.2.tgz`
- `dist/ltcai-4.3.2.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.3.2_aarch64.dmg`

Do not upload by broad `dist/` wildcard. Use exact artifact filenames.

## Known Limitations

- External package registries are not published by the v4.3.2 RC preparation.
- PostgreSQL/pgvector validation is opt-in and requires explicit Docker/Postgres
  consent.
- Ask requires a loaded model for generated answers and does not fabricate a
  response when no model is loaded.
- Optional cloud providers require explicit keys and user action.
- Historical docs in `docs/` may describe older release states; use README,
  this file, `FEATURE_STATUS.md`, and v4.3.2 reports for current behavior.

## Current Evidence

- Self-audit: `docs/V4_3_2_SELF_AUDIT_REPORT.md`
- Validation: `docs/V4_3_2_VALIDATION_REPORT.md`
- Graph UX: `docs/V4_3_2_GRAPH_UX_REPORT.md`
- Product polish: `docs/V4_3_2_PRODUCT_POLISH_REPORT.md`
