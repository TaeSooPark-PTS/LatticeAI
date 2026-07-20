# Lattice AI v9.8.0 — Honest Knowledge Pipeline

Release date: 2026-07-20

9.8.0 makes the file → folder → web → graph → RAG → automation pipeline
honest and robust end to end. Every ingest now reports how well the source
extracted, background folder ingestion survives per-item failures and can
resume, chat answers disclose when their graph context is limited, vector
index freshness is visible in the product, and both the agent evaluation
gate and automation suggestions grew quality machinery.

## Highlights

### Extraction quality on every ingest

- Every ingest result (file, folder item, note, web page) carries
  `extraction_quality: {score: 0..1, level: high|medium|low, reasons: [...]}`
  computed from pure heuristics — text length, sentence structure,
  character/word diversity, whitespace ratio, fragment lines, and (for web
  sources) nav/boilerplate remnants. Upstream extractors can pass their own
  confidence (`metadata.extraction_confidence`), which takes precedence.
- Low-quality captures add `warnings` so the UI can flag them before they
  pollute the Brain; the ingestion panels render the warning under the
  source's stage track.
- `gate_ingest_candidate()` (the proactive quality gate) now runs in
  observe-mode on every non-chat ingest: the verdict is recorded as
  `quality_gate: {action: ingest|skip_duplicate|review, detail}` without
  changing ingest behavior — groundwork for enforcement in a later release.

### Robust background ingestion with progress and resume

- Background ingestion jobs track `total`, `processed`, `failed`, capped
  per-item `errors`, and `created_at`/`updated_at`; a single bad file no
  longer kills a folder job, and interrupted or failed jobs resume from the
  remaining items only (`done_indices`-based, no re-ingesting).
- New HTTP surface: `GET /api/ingestion/jobs`, `GET /api/ingestion/jobs/{id}`,
  `POST /api/ingestion/jobs/{id}/resume`, and `POST /api/ingestion/folder`
  (with the same local-disk approval dance as existing local indexing;
  `background: true` returns a `job_id`).
- The app shows a jobs panel with a live progress bar, failed count, and a
  resume button — polling only while a job is actually running.

### Honest RAG signaling in chat

- Every chat answer now computes `context_quality:
  {mode: hybrid|lexical_only|none, nodes, limited, reason}` from the same
  graph retrieval the answer used — surfaced top-level in the non-stream
  response and in the final SSE trailer event alongside the existing trace.
- When `limited` is true (0–1 matched nodes, vector fallback, or search
  failure), the assistant bubble shows a small localized note — "그래프 기반
  컨텍스트가 제한적입니다" — instead of silently pretending full recall.

### Visible vector freshness

- New `GET /api/brain/vector-freshness` returns
  `{status: ready|pending|unavailable, pending_items, total_items, detail}`
  and never raises — embedding/storage failures degrade to `unavailable`
  with a reason.
- The Brain views show a soft chip when knowledge is still waiting for
  vector indexing ("일부 지식이 최신 인덱싱 대기 중"), refreshed after each
  ingest rather than by constant polling.

### Agent evaluation grew to 16 scenarios

- Four new deterministic scenarios: an ingestion tool chain that must
  confirm the save, concept extraction reflected in the answer, a
  RAG-grounded answer that must cite retrieval results (with a negative test
  proving the grounding gate actually gates), and an automation suggestion
  path pinned to proposal-first governance.
- The eval fake tool port now serves canned `knowledge_graph_ingest` /
  `knowledge_graph_search` fixtures, and `Scenario.expect_final_contains`
  fails any scenario whose final message is not grounded in tool results.

### Automation suggestions with confidence

- Every automation suggestion now carries `confidence` (0–1, deterministic),
  `confidence_factors` (repeat counts, distinct examples, intent match, and
  KG-related node counts when the graph is available), and a
  `low_confidence` flag; responses include a `quality` block reporting
  suppressed low-confidence and duplicate suggestions.
- Duplicate clusters mapping to the same recipe keep only the strongest
  suggestion; suggestions whose recipe is already installed show
  `installed: true` instead of re-suggesting; sub-threshold suggestions are
  dropped.

### Simplified README

- The README was rebuilt media-first: a hero walkthrough GIF, a screenshot
  grid of what you can do, a short "Why Lattice AI", quick start, and a
  compact release-history table — roughly 60% less prose.

## Compatibility

- All API changes are additive; existing responses only gain fields
  (`extraction_quality`, `warnings`, `quality_gate`, `context_quality`,
  `confidence`, `quality`).
- `context_for_query()` default output is byte-identical; the metadata path
  is opt-in (`with_meta` / `context_for_query_with_meta()`).
- The quality gate is observe-only in 9.8.0 — no ingest is skipped by it.
- `PLUGIN_SDK_VERSION` is unchanged.

## Verification

- 1263 unit tests green; frontend vitest 27 green; tsc, ruff, frontend lint,
  i18n literal/parity, and OpenAPI drift gates pass.
- `scripts/agent_eval.py`: 16/16 scenarios, success rate 1.0.
- ko/en i18n key parity maintained for all new UI strings.

## Artifacts

- `dist/ltcai-9.8.0-py3-none-any.whl`
- `dist/ltcai-9.8.0.tar.gz`
- `ltcai-9.8.0.tgz`
- `dist/ltcai-9.8.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_9.8.0_aarch64.dmg`
