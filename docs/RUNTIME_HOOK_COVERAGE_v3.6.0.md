# Runtime Hook Coverage — v3.6.0

v3.6.0 makes the Knowledge Graph the primary architecture and adds new
**ingestion** paths (web URL, browser tab, unified text/file pipeline) plus
**portability** ops (export/import/backup/restore). This doc extends
[`RUNTIME_HOOK_COVERAGE_v3.5.0.md`](./RUNTIME_HOOK_COVERAGE_v3.5.0.md) and records
that the new data-mutating paths run through the unified lifecycle.

The single tool path is `dispatch_tool(hooks, name, args, run_fn)` in
`latticeai/core/hooks.py` (`pre_tool → execute → post_tool`). v3.5.0's one honest
gap was that **KG ingestion did not fire hooks**. v3.6.0 closes it: every source
now flows through `IngestionPipeline.ingest` (`latticeai/services/ingestion.py`),
which wraps the store write in `dispatch_tool(..., source="ingestion")`.

**Result.** All v3.5.0 coverage is preserved (no regression), and every new
v3.6.0 ingestion path is covered. Portability admin/maintenance ops follow the
v3.5.0 convention for service maintenance (own audit events), documented below.

## New v3.6.0 execution paths

| Entrypoint | Execution | Lifecycle path | pre fired | post fired | Test |
|---|---|---|---|---|---|
| `IngestionPipeline.ingest` (any source) | `ingest_source` / `ingest_document` | `dispatch_tool(name="kg_ingest.<type>", source="ingestion")` | yes (`pre_tool`) | yes (`post_tool`) | `test_ingestion_pipeline` |
| `POST /api/browser/read-url` | fetch URL → `pipeline.ingest` (web_url) | pipeline → `dispatch_tool` | yes | yes | `test_browser_ingestion`, `test_runtime_coverage_v36` |
| `POST /api/browser/ingest-current-tab` | sanitize → `pipeline.ingest` (browser_tab) | pipeline → `dispatch_tool` | yes | yes | `test_browser_ingestion`, `test_runtime_coverage_v36` |
| Local file / upload via pipeline | `ingest_document` | pipeline → `dispatch_tool` | yes | yes | `test_ingestion_pipeline` |
| Provenance write per ingestion | `record_provenance` | inside the bracketed `dispatch_tool` run_fn | (bracketed) | (bracketed) | `test_ingestion_pipeline` |

A blocking `pre_tool` hook makes ingestion return `status="blocked"` (the
`PermissionError` from `dispatch_tool` is caught and surfaced honestly), exactly
mirroring how a blocked tool call is handled — verified in
`test_runtime_coverage_v36` and `test_ingestion_pipeline`.

## Intentionally outside the tool lifecycle (documented, not gaps)

| Entrypoint | Why not `pre_tool`/`post_tool` |
|---|---|
| `POST /api/knowledge-graph/{export,export-file,backup,restore,import}` | Admin **portability/maintenance** operations over the whole machine-global graph, not agent-vocabulary tools. They are admin-gated (`require_admin`) and recorded via the platform audit trail — same convention as the v3.5.0 memory maintenance ops (`prune/compact/rebuild/clear`). Wrapping a whole-store backup in `pre_tool` would misrepresent it as a per-action agent tool. |
| `read_document` inside upload (`upload_service.py`) | Already inside the `pre_upload`→`post_upload` lifecycle (unchanged from v3.5.0). |
| `POST /api/memory/{prune,compact,rebuild,clear}` | Unchanged from v3.5.0 — service maintenance with own audit events. |
| Read-only KG reads (`/knowledge-graph/{stats,graph,search,...}`, `/api/knowledge-graph/portability` status) | Execute no mutation — nothing to gate. |

## Summary

- v3.5.0 coverage of tool/agent execution paths: **100%, preserved** (no regression).
- v3.6.0 gap closed: **KG ingestion now fires `pre_tool`/`post_tool`** via the unified pipeline (the one honest carry-over note from v3.5.0).
- New ingestion paths (unified pipeline, `read-url`, `ingest-current-tab`): **covered**.
- Portability admin ops: **documented as audit-gated maintenance**, consistent with the v3.5.0 convention — not bypasses.
- Coverage of discovered mutating ingestion paths: **100%**.
