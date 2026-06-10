# Lattice AI v3.6.0 — Knowledge Graph First

**Lattice AI is not a model-personalization system. It is a Digital Brain
Platform.** v3.5.0 stabilized the foundation; v3.6.0 makes the Knowledge Graph the
primary architecture. Models are replaceable. Knowledge is durable. Your
Knowledge Graph is the durable asset, and every data source now converges into it.

## v3.5.0 carry-over audit

A full carry-over review (`docs/CARRYOVER_AUDIT_v3.6.0.md`) classified every open
v3.5.0 item as blocking / non-blocking / obsolete. **Result: zero blocking
items.** Settled postures were preserved — Vercel stays a landing/download/demo
surface (never the runtime), OIDC stays RSA-only, and the legacy `/account` and
`/admin` pages stay out of scope. The one honest v3.5.0 gap — KG ingestion not
firing tool hooks — is closed in this release.

## Knowledge Graph First architecture

All user data sources flow through a single unified model:

> source → extraction → normalization → metadata → chunking → entity detection →
> relationship detection → embedding → **Knowledge Graph** → RAG / agents / memory / search

No new data source bypasses the Knowledge Graph; none creates an isolated silo.

## What's new

### Unified ingestion pipeline
- New `latticeai/services/ingestion.py` — one `IngestionPipeline.ingest()`
  entrypoint normalizes every source (file, folder, web URL, browser tab,
  text/markdown/note/code) into a single `IngestionItem`.
- Idempotent by content hash — re-ingesting the same content links/updates
  instead of duplicating.
- Routed through the shared `dispatch_tool` lifecycle so `pre_tool`/`post_tool`
  hooks fire on data ingestion (closing the v3.5.0 gap); a blocking `pre_tool`
  returns an honest `status="blocked"`.
- Each item records `source_type`, `source_uri`, content hash, `captured_at`,
  `modified_at`, owner/workspace, permissions, graph node + chunk IDs, and
  embedding/indexing status.

### Entity & relationship model
- Six new first-class entities: `Source`, `Repository`, `Meeting`,
  `Organization`, `Workflow`, `Agent`.
- Eight new relationships: `indexed_from`, `modified_by`, `belongs_to_project`,
  `part_of`, `discussed_in`, `decided_by`, `generated_by`, `used_by_agent`.
- Additive and lossless — `from_legacy()` normalizes the new aliases (incl. Korean
  verbs); unknown types still fall back to `CONCEPT`/`MENTIONS`. Schema kept
  extensible. Documented in `docs/kg-schema.md`.

### Browser & web ingestion (as graph inputs, not standalone features)
- `POST /api/browser/read-url` — the local runtime fetches a public URL, extracts
  readable text, stores it as `source_type=web_url`. Fails gracefully (HTTP 422)
  on blocked / login-required / non-HTML pages.
- `POST /api/browser/ingest-current-tab` — accepts a sanitized, size-limited
  payload from the local browser extension as `source_type=browser_tab`.
- Manifest V3 Chrome/Edge extension scaffold under `browser-extension/` — sends
  the current tab to the local runtime only (`127.0.0.1`). No external server, no
  cloud upload.

### Export / import / backup / restore
- Logical export/import (versioned JSON: nodes/edges/chunks/sources/provenance +
  schema/projection/embed-dim header; merge/replace + dry-run; refuses newer
  schemas).
- Binary backup/restore (`VACUUM INTO` snapshot incl. vector embeddings + blob
  directory, sha256 integrity-checked).
- `latticeai/services/kg_portability.py` + `/api/knowledge-graph/{export,
  export-file,import,backup,restore,portability,provenance}`. Local-first — no
  cloud service required.

### Provenance & auditability
- New `ingestion_provenance` table + `record/get/list/provenance_stats` methods —
  an append-only trail making every node explainable (origin, time, pipeline,
  embedded, linked, duplicate, agent-used).

### Runtime / hook safety
- New ingestion, browser, and web paths respect the v3.5.0 lifecycle standard via
  `dispatch_tool`. `docs/RUNTIME_HOOK_COVERAGE_v3.6.0.md` records coverage with no
  regression from v3.5.0.

### UI
- The Knowledge Graph view is recast as your digital brain with tabs: **Explore**
  (entity/relation mesh), **Status** (graph + ingestion health), **Sources**
  (provenance — where every node came from), **Capture** (read a URL into the
  graph), and **Backup** (export / import / backup).

## Validation

- Unit tests: full suite green (`tests/unit/`), including new suites for the
  schema, ingestion pipeline, provenance, browser routes, portability, and v3.6.0
  hook coverage.
- `npm run lint`, `npm run check:python`, `npm run build`, and release-artifact
  validation pass; v3 frontend lint 64/64.

## Not in scope (settled)
- Vercel remains landing/download/demo only — never the runtime.
- OIDC remains RSA-only unless intentionally expanded.
- Legacy `/account` and `/admin` pages remain outside the v3 SPA view set.

## External publishing
None. No publish to npm, PyPI, VS Code Marketplace, Open VSX, Docker Hub, or
Vercel was performed by this release.
