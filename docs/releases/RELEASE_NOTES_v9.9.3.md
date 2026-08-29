# Lattice AI v9.9.3 — Closed Loops (2026-07-22)

> **Status: historical** — point-in-time release note.

9.9.3 closes every loop the 2026-07-21 full-stack review left open: the
complete 22-item backlog from
[docs/reviews/REMAINING_WORK_AFTER_9.9.2.md](../reviews/REMAINING_WORK_AFTER_9.9.2.md)
ships in this release — the multi-file artifact loop, the interactive approval
loop, the 30-second first-value loop, the retrieval/curation loop, the
automation visibility loop, and the harness that proves each of them.

## Highlights

### Artifact Loop — multi-file projects (backlog #1)

- `infer_project_manifest` turns "todo 앱 html+css+js" into a real project
  manifest (ko/en, Hangul-particle-safe); each file is generated and validated
  through the shared `sanitize_write_content` pipeline.
- Bundle-level guarantees: `validate_project_bundle` resolves HTML `href`/`src`
  references against the bundle; `repair_bundle_references` rewrites dangling
  refs (e.g. `styles.css` → `style.css`) instead of shipping a broken page.
- Safe zip download: `GET /tools/download_zip?path=<dir>` with workspace
  confinement, symlink/traversal refusal, and a 50 MB cap. The chat payload
  advertises it as `project.zip_url`; every file rides the `artifacts[]`
  contract. Single-file behavior is unchanged.

### Interactive approval — `awaiting_approval` (backlog #2)

- Agent runs whose plan needs human approval now pause as
  `status: "awaiting_approval"` (previously they terminated FAILED), carrying a
  plan summary and a single-use approval token (10-minute TTL, bound to run
  and user).
- `POST /agent/resume` `{run_id, approval_token, approve, edited_plan?}` —
  approve executes the governed steps to completion, deny records a cancelled
  run; expired/mismatched/replayed tokens fail 410/403/404. The fail-closed
  gate itself is unchanged: no governed step ever executes without a valid
  approval.
- Inline approval card in chat: plan summary, expiry hint, 승인하고 실행 /
  수정해서 실행 (inline plan edit) / 취소 — resumed results merge into the
  normal chat file-card path.

### First Value Loop — 30 seconds to real recall (backlog #3)

- `POST /api/setup/demo-corpus` installs 3 built-in Korean demo documents
  through the normal ingestion pipeline (`demo://` provenance, idempotent,
  removable via DELETE) and returns suggested questions whose answers live in
  the corpus.
- The "30초 체험" track on the empty Brain home: one click → docs land with
  inflow animation → question chips → a real sourced answer → an invitation to
  generate an HTML page from it. Progress persists; the track never nags.

### Honest grounding (backlog #11)

- Every Brain-context answer now carries
  `grounding: {status: supported|unsupported|no_context, source_ids, overlap}`
  — rendered as a small 근거 있음/근거 없음 badge. Annotation only; answers are
  never blocked.

### Retrieval fusion + benchmark gate (backlog #5)

- Hybrid search classifies queries (fact / code / person / recency) and fuses
  keyword/vector/graph channels with per-class weights
  (`LATTICEAI_FUSION_WEIGHTS` overridable); responses expose `query_class`.
- A judged benchmark fixture corpus with CI thresholds
  (`tests/unit/test_retrieval_fusion_gate.py`) fails the build on regression:
  recall@5 ≥ 0.75, must-include ≥ 0.90, query-class accuracy 1.0.

### Knowledge pipeline depth (backlog #8, #9, #10)

- **Folder watch (opt-in, default off):** `POST/GET/DELETE
  /api/ingestion/watch` with the explicit approval dance; polling with mtime
  snapshots, persisted consent, incremental re-ingest. Never watches without
  stored explicit opt-in; never deletes.
- **Capture quality CTA:** browser/web captures return
  `capture_quality {status: thin|ok, reason, suggestions}` (recapture, paste
  manually, highlight source) using the pipeline's shared quality schema.
- **Graph noise curation:** `POST /knowledge-graph/curate/noise` (dry-run
  default) removes high-document-frequency heuristic concept nodes and
  normalizes relation verbs through a ko/en dictionary; user-created nodes are
  always protected.

### Automation you can see (backlog #6)

- `POST /api/automation/run-now` with dry-run-first: a deterministic no-side-
  effect report, then an unlocked real run. Last execution (mode, status,
  summary, finished_at) is stamped on the workflow, shown on Act-panel cards
  and the daily briefing; failed runs enqueue a Review-queue item.

### UX completion (backlog #4, #7, #17, #18, #19, #20)

- **Inline file preview:** HTML in a fully sandboxed iframe (no scripts, no
  same-origin, restrictive CSP), md/txt/json in a focus-trapped modal; download
  stays.
- **Folder job report card:** "+N documents / failed / vector x% fresh" with
  up to 3 skip/fail samples on job completion; missing fields hide instead of
  rendering NaN.
- **Accessibility:** shared focus-trap hook on modals and the command palette,
  keyboard navigation for the knowledge graph (arrow keys, Enter, aria-live
  announcements), and reduced-motion coverage for all previously uncovered
  infinite animations.
- **Global drag-and-drop:** drop files anywhere on the Brain home to ingest.
- **409 rebase UX:** a conflicted proposal explains itself and offers
  "다시 읽어서 재적용" — re-reads the file, re-hashes, re-stages against the
  current base.
- **Emotional polish:** success pulse and knowledge-inflow motes, strictly
  behind `prefers-reduced-motion: no-preference`.

### Harness maturity (backlog #12, #13, #14, #15, #16, #21, #22)

- `agent_eval` grows to 23 scenarios including dirty-write → sanitize →
  critic-PASS and unverifiable → NEEDS_REVIEW filegen paths, with new
  `expect_repairs` / `expect_write_contains` / `expect_write_excludes`
  assertions on what the tool port actually received.
- Golden sanitize fixtures (`tests/fixtures/filegen/`): five dirty→clean pairs
  compared byte-for-byte; golden updates require deliberate review. The
  goldens immediately caught and fixed a real gap — fenced CSS used to be
  written verbatim; `.css` validation now rejects fences.
- `scripts/bench_models.py --filegen`: real local models × file types success
  report (fail-open, not a CI gate). Verified live: installed gemma produced
  6/6 valid file types.
- Deterministic knowledge-pipeline E2E test: temp folder → `ingest_folder` →
  hybrid search (`query_class`) → context quality → suggestions, one flowing
  test, no model, no network.
- Funnel metrics: `GET /api/admin/funnel-metrics` — file requests, real-file
  delivery rate, code-only rate, NEEDS_REVIEW rate, TTFV.
- Per-phase token budgets (`PhaseBudgets`: plan/execute/verify/memory) stop a
  weak model from burning the whole budget planning.
- File generation now recognizes `.ts/.tsx/.jsx/.vue/.svelte` and validates
  Python with `ast.parse` (syntax errors route through the repair path).

## Behavior changes

- Agent runs requiring approval return `awaiting_approval` instead of FAILED;
  a legacy `context_id` resume with `approved: true` now actually executes
  approval-gated steps (previously it re-failed at the gate).
- Chat responses (JSON and SSE trailer) gain `grounding`; hybrid search
  responses gain `query_class`; automation overview entries gain
  `last_execution`.
- `.css` writes that still contain Markdown fences are now sanitized instead
  of written verbatim.

## Known limitations

- Pending approval state is in-process: a server restart drops paused runs
  (resume → 404, surfaced as "expired — ask again").
- Project manifest inference targets web bundles (html+css+js); other combos
  fall through to existing single-file/agent routes.
- Folder watch counts deleted files but never removes graph content
  (destructive operations stay behind explicit flows).
- Grounding is a lexical heuristic; a fully paraphrased grounded answer can
  read `unsupported`.

## Verification

- `tests/unit`: 1437 passed · frontend: 81 passed (16 files) ·
  integration: 3 passed / 11 skipped
- `npm run lint` / `npm run typecheck` / i18n parity / bundle budget
  (146.7 KiB ≤ 150 KiB) all green
- `scripts/agent_eval.py`: 23/23 · `scripts/brain_quality_eval.py`: recall@5
  1.0 (small) / 0.95 (corpus), must-include 1.0
