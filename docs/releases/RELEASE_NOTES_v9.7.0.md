# Lattice AI v9.7.0 — Proactive Hybrid Brain

> **Status: historical** — point-in-time release note.

Release date: 2026-07-20

9.7.0 deepens the Digital Brain along three tracks: retrieval that fuses
lexical and vector evidence inside the graph layer and keeps its own index in
sync, proactive quality intelligence (duplicates, contradictions, staleness)
running directly over the Knowledge Graph, and a change-governance loop that
is now closed end-to-end — what the Review Center approves is exactly what
lands on disk.

## Highlights

### Unified hybrid retrieval

- `KnowledgeGraphStore.hybrid_search(query, top_k=…, alpha=…)` is the single
  graph-layer entrypoint for retrieval: lexical `search()` and
  `vector_search()` run together, scores are normalized to [0,1] and fused
  (alpha-weighted), chunk hits roll up to their parent nodes, and every match
  reports `scores: {lexical, vector}` plus a `fusion` provenance tag
  (`lexical` / `vector` / `both`).
- Honest degradation: when the vector index is unavailable, the result says
  `mode: "lexical_only"` with a detail — no silent pretending.
- `context_for_query(use_hybrid=True)` lets the agent context builder opt in;
  the default path is byte-identical to 9.6.0.

### Self-syncing vector index

- New `index_node_incremental(node_id)` embeds just one node's chunks.
- `IngestionPipeline` calls it automatically after every successful
  non-duplicate ingest (`auto_vector_index=True`, opt-out env
  `LATTICEAI_AUTO_VECTOR_INDEX`). A vector failure never fails the ingest:
  `indexing_status` downgrades to `pending` and the existing
  `rebuild_vector_index` backlog discovery picks it up later.

### Folder & web ingestion

- `IngestionPipeline.ingest_folder(root, recursive=True, background=False)`
  walks a directory tree with `.latticeignore` support (gitignore-like
  globs, `dir/` prunes, `#` comments), a hard skip-list (`.git`,
  `node_modules`, `__pycache__`, venvs, build dirs), hidden-files-by-default
  exclusion, size/extension filters, capped per-file error reporting, and
  optional scheduling on the background ingestion queue.
- `ingest_web_page(url, extracted_text)` formalizes the web seam and the
  parsing-depth contract: fetching, cleaning, and layout/OCR parsing belong
  upstream (browser extension, tools); the graph layer receives extracted
  text and owns structuring + concept extraction.

### Proactive Brain in the graph layer

- New `lattice_brain/graph/proactive.py` (`ProactiveBrain`) operates on the
  store's public read API: duplicate detection (content-hash exact +
  token-signature near-duplicates, sub-quadratic sampling), contradiction
  detection (negation + temporal), a combined JSON-safe `quality_report()`
  (duplicates, contradictions, stale nodes, edge quality), and
  `consolidate_duplicates()` merge planning — consent-first and plan-only
  until the store exposes a safe merge primitive (auto-detected when it
  does).
- New endpoints: `GET /api/brain/duplicates`, `GET /api/brain/quality-report`;
  `/api/brain/contradictions` and `/api/brain/consolidate` gain graph-layer
  results additively.
- `gate_ingest_candidate()` provides a pure ingest-time quality gate seam
  (`ingest` / `skip_duplicate` / `review`) for future wiring.

### Closed change-governance loop

- Review Center approval of a `change_proposal` now delegates to
  `ChangeProposalService.approve_and_apply`: the staged content is applied
  through the one audited path (409 on replay). Before 9.7.0, review-queue
  approval only flipped the item's status.
- Proposals carry full provenance — tool, risk, change class, and the
  originating conversation id (the agent loop now forwards it to the
  governor). Reject accepts and records a reason.
- New `GET /api/proposals/counts`, `GET /api/proposals/{id}` (diff + staged
  content), `GET /automation/reviews/counts`; the Review Center UI gains a
  `change_proposal` source filter, unified-diff preview, tier/deletion
  badges, a pending-count badge, and a reject-reason input (ko/en i18n
  parity).

### Agent evaluation & runtime consistency

- The CI agent-eval gate grew from 8 to 12 deterministic scenarios:
  file-generation happy path, file-generation failure recovery, a 3-step
  multi-step workflow chain with exact ordered tool-call assertions, and a
  governed-write proposal path that pins the
  approve()-excludes-governed-tools invariant.
- `SingleAgentRuntime.execute` (206 lines) was decomposed into six focused
  helpers with zero behavior change; the multi-agent orchestrator now
  surfaces the real failure reason in `execution_failed` timeline events;
  `tests/unit/test_runtime_consistency.py` pins contract-envelope,
  status-vocabulary, and fail-closed parity across both runtimes.

### Structure, performance & housekeeping

- All 10 root legacy modules (`knowledge_graph.py`, `kg_schema.py`,
  `llm_router.py`, `mcp_registry.py`, `p_reinforce.py`, …) emit
  `DeprecationWarning` naming their package replacement; the
  legacy-compatibility registry tracks all 13 shims.
- `scripts/profile_kg.py` — offline synthetic KG profiler (p50/p95 latency
  and tracemalloc peaks for ingest / search / context / traverse / vector
  phases). Measured baseline lives in `docs/PERFORMANCE.md`; at ~18k
  embeddings the brute-force `vector_search()` scan (~1.7 s/query) is the
  named first optimization candidate.

## Compatibility

- All changes are additive; existing API responses only gain fields.
- `context_for_query()` default behavior is unchanged (hybrid is opt-in).
- Root legacy imports keep working; the DeprecationWarnings are
  informational and name the exact replacement path.
- `PLUGIN_SDK_VERSION` is unchanged.

## Verification

- 1201 unit / 13 integration / 19 frontend tests green.
- `scripts/agent_eval.py`: 12/12 scenarios, success rate 1.0.
- `scripts/brain_quality_eval.py`: recall@5 1.0 (small fixture) / 0.95
  (corpus fixture), must-include hit rate 1.0.
- Ruff, frontend lint, tsc, i18n-literal, openapi-drift, and the
  current-release docs gate all pass.

## Artifacts

- `dist/ltcai-9.7.0-py3-none-any.whl`
- `dist/ltcai-9.7.0.tar.gz`
- `ltcai-9.7.0.tgz`
- `dist/ltcai-9.7.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_9.7.0_aarch64.dmg`
