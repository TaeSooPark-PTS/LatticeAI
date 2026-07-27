# Changelog

The top entry is either the current unreleased main-branch work or the current
release line. Older entries are historical and may describe behavior as it
existed at that release.

## [9.9.9] - 2026-07-27

### Changed
- i18n is split per lazy route. `frontend/src/i18n/registry.ts` holds a shared
  mutable table; `shell` registers eagerly (app frame, language switcher,
  generic `ui.*`) and `brain` / `workspace` / `onboarding` register themselves
  when their module is imported, which happens inside the lazy chunk of the
  route that needs them. Initial static JS drops from 150.0 KiB to 99.3 KiB
  gzip (505.1 KiB → 317.3 KiB raw), and lazy chunks kept off first paint go
  from 25 to 32.
- The bundle budget returns to its original 150 KiB. 9.9.8 raised it to 152 KiB
  because the shared i18n table made the ceiling unreachable without one; that
  cause is gone.
- The admin console is behind a `React.lazy` boundary. It is a rare, separate
  surface and it was pulling the whole workspace copy namespace onto first
  paint.
- `brain.title` (the product wordmark) moved from the `brain` namespace to
  `shell` — it renders in the app frame before any route resolves.

### Added
- `scripts/check_i18n_namespace_coverage.mjs`, wired into `npm run lint`. It
  walks the real module graph and, for the eager root and every `React.lazy`
  boundary, verifies each `t(lang, "key")` in that chunk's static closure is
  covered by a namespace the chunk imports. Without it the split fails
  silently: `t()` returns the raw key, so the UI shows `system.permission.title`
  instead of Korean text with no error raised. Type-only imports are not
  counted as runtime edges.
- `frontend/src/test/setup.ts` registers every namespace, because a unit test
  renders a component without its route. Production coverage is proven by the
  script above, not by this file.

## [9.9.8] - 2026-07-27

### Added
- Permission modes (`latticeai/core/permission_mode.py`): a `strict` /
  `trusted` / `bypass` autonomy dial layered over ToolRegistry + Change
  Governor. `strict` is the default and matches 9.9.7 behavior exactly.
- `GET/POST /api/permission-mode` and `GET /api/permission-mode/catalog`,
  backed by `PermissionModeService` (per-workspace > per-user > process
  default, `LATTICEAI_PERMISSION_MODE` for the default). Switching to
  `bypass` requires `acknowledge_risk=true` and is audited.
- Mode-invariant circuit breakers: destructive risk, root/home paths,
  `rm -rf /` style commands, and binary overwrites are denied in every mode.
- `AgentRunContext.permission_mode`: the mode is resolved once per run and
  persisted with a paused approval, so a plan and its execution are judged by
  one dial even if the stored preference changes mid-run.
- `PermissionModePanel` in 환경설정 → 에이전트 자율성: the dial is set from the
  product, not only from the API. The selector renders the server's own
  catalog instead of a hardcoded mode list and will not send a `bypass`
  switch until the acknowledgement the server requires is ticked.

### Fixed
- Stored per-user and per-workspace modes never reached enforcement: the
  bound resolver was called without a scope, so every caller collapsed onto
  the process default and the dial had no effect. `enforce_policy` and the
  agent tool gate now resolve with `user_email` / `workspace_id`.
- Orphan change proposals under `trusted` / `bypass`: the gate called
  `ChangeGovernor.review()` — which persists a proposal as a side effect —
  and then discarded the verdict, applying the change *and* leaving a pending
  proposal in the Review Center. The mode is now decided before the governor
  is consulted.
- `AgentRunContext` used `__slots__` with no `permission_mode` slot, making
  the documented per-run override unreachable dead code.
- `PermissionModeService.set_mode()` deadlocked: it held a non-reentrant
  `threading.Lock` while calling `resolve()`, which takes the same lock, so
  every mode change hung its worker thread forever.

### Changed
- `enforce_policy`: the two `fail_closed` branches that both raised 409 are
  one branch again — a binary overwrite has no safe apply path in any mode.
- `PermissionModeService.rebind_data_dir()` / `rebind_audit()`: explicit
  wiring now rebinds an already-created singleton instead of being dropped,
  so an early lazy caller cannot pin the store to `~/.ltcai` with no audit
  sink.
- Initial-JS bundle budget raised 150 KiB → 152 KiB gzip. The 9.9.7 tree sat
  at 149.3 KiB and the new settings panel needed ~1.0 KiB, nearly all of it
  bilingual copy: page components are lazy, but `frontend/src/i18n/*` is one
  synchronous table, so every surface's copy is on the first-paint path by
  construction. Splitting i18n per lazy route is the durable fix and is not in
  this release.
- The permission gates live in `SingleAgentRuntime` itself. `permission_mode`
  is a real `AgentDeps` field, and `approval_requirements` /
  `_blocked_by_gates` / `_governor_review` are mode-aware in place instead of
  being replaced at construction time by a monkey-patch that would break
  silently if those signatures changed.

### Removed
- `latticeai/core/agent_mode_patch.py` — the monkey-patch layer, folded into
  `agent.py`.
- Dead no-op `if ... pass` block in `is_circuit_breaker`.
- `filter_governor_verdict`, unused after the governor fix.

## [9.9.7] - 2026-07-27

### Added
- `POST /agent` accepts `stream: true` and emits the same named `agent_step`
  SSE frames the web chat route already produced; the terminal payload is
  identical to the JSON response (`tests/unit/test_agent_stream_parity.py`).
- VS Code: `ltcai.runAgentLive` (live step timeline) and
  `ltcai.evidenceActions` (one-click follow-ups from the last recall's cited
  sources, file actions routed through the agent).
- Telegram: grounding badge on every answer plus `/review` — a Review Center
  over `/api/proposals` with inline approve/reject and honest 409 reporting.
- Browser extension: "Ask your Brain" with the server's grounding verdict, and
  pending-approval visibility. Still posts only to `127.0.0.1`.
- `GET /api/brain/garden` + `KnowledgeGardenPanel`: the knowledge garden in
  four beds (recent / contradictions / stale / frequent), where "frequent" is
  real graph degree and Chunk nodes are excluded.
- `latticeai/core/agent_profiles.py`: `standard` / `compact` agent profiles
  selected from the model id (or `LATTICEAI_AGENT_PROFILE`), with a
  direct-path fallback that writes the plan's files without any JSON tool call
  when a small model cannot hold the protocol.
- `GET /knowledge-graph/local/health` + `FolderMemoryHealthCard`: per-folder
  indexing coverage, failures with their stored reasons, and one explicitly
  global vector-freshness figure.
- Skills `meeting_notes` and `weekly_review`, with a contract test that rejects
  a skill whose `action` is not a registered tool.
- `POST /api/capture/voice` + `GET /api/capture/voice/status`
  (`latticeai/services/voice_capture.py`): voice memo ingestion with an
  optional local transcriber and honest degradation when none exists.

### Changed
- `docs/SURFACE_PARITY.md` contains no `✖` entries; every remaining `—` states
  why it is a design boundary.
- The browser extension is no longer described as capture-only (manifest name,
  popup, README).

### Fixed
- The VS Code SSE reader kept the `event:` name, so named `agent_step` frames
  are no longer misread as chat chunks.
- `garden_overview` clamps an explicit `limit=0` to 1 instead of silently
  re-expanding it to the default.

## [9.9.6] - 2026-07-27

### Added
- Surface parity for VS Code: `ltcai.askBrain` (grounding badge on recall),
  `ltcai.reviewCenter` (list/approve/reject staged change proposals via
  `/api/proposals`, 409 conflicts reported honestly), `ltcai.runAgent`
  (post-run step + outcome summary). Parity logic lives in
  `vscode-extension/surface.ts`, asserted by `tests/vscode-extension.test.cjs`
  (wired into `npm run lint`).
- `POST /api/evidence/actions` + `latticeai/services/evidence_actions.py`:
  deterministic, model-free composition of evidence-scoped follow-up prompts
  (summary / checklist / document / one-page) from an answer's citations.
- `latticeai/core/run_explain.py`: deterministic plain-language run outcome
  (`code`, `headline`, `details`, `model_strain`, `next_step`), returned as
  `explanation` on `/agent` and rendered in the web app, VS Code, and Telegram.
- `prose` chunking strategy (`lattice_brain/graph/_kg_common.py`): sentence and
  paragraph-aware boundaries for `.txt/.pdf/.docx/.html/…`; `plain` stays
  byte-identical to the legacy walk.
- `citation_locator()` + chunk metadata on vector hits: citations name the
  section/page they came from (`Guide > Setup · p.4`, `p.4–5` across a page
  break) and stay silent when the chunk cannot prove it.
- `infer_edge_relation()` + `plan_relation_noise_reduction()`: relations carry
  an evidence class (`verb` | `cooccurrence`) with matching weights; the
  curator can demote weak/hub co-occurrence edges without touching verb-backed
  or legacy ones.
- Project sessions: `latticeai/core/project_sessions.py` + `/api/projects`
  (files produced, open TODOs, last honest verification, run history). `/agent`
  accepts `project_id`, injects the project state into planning/execution, and
  folds the run's outcome back in.
- `latticeai/core/artifact_ledger.py` + a `Files created in this conversation`
  context section: files a run just wrote are recallable before asynchronous
  indexing catches up.
- `requirement_coverage()`: a critic `PASS` that left a declared manifest file
  unwritten now ends as `NEEDS_REVIEW`.
- `funnel_alerts()`: `GET /api/admin/funnel-metrics` returns named, actionable
  alerts with the triggering value; silent below 10 samples.
- Stale-embedder recovery UI: names the problem and offers a one-click
  re-index, with an honest failure message.
- `lattice_brain/ingestion_jobs.py` and
  `latticeai/core/workspace_review_items.py`: behaviour-preserving extractions.
- Six additional multi-agent/workflow scenarios (retry exhaustion, recovery,
  two-gate pause/resume, stale resume cursor, per-role observability, cross-run
  role isolation).

### Changed
- Document generation shares chat's context contract: same `approx_tokens`
  budget, same `context_quality` signal, same assembly `trace` shape.
- `chunk_strategy_for()` routes prose document formats to the new `prose`
  strategy (newly ingested `.txt/.pdf/.docx/.html` content gets different chunk
  ids; existing indexed content is untouched).
- The VS Code HTTP client rejects on 4xx/5xx instead of resolving an error body
  as success.

### Fixed
- A verb-less sentence listing more than four concepts no longer manufactures
  a chain of `관련됨` relations.
- `stale_embedder` was computed but never surfaced in the UI.
- PDF chunks that span a page break are labelled with the page range they
  actually cover instead of only their starting page.

## [9.9.5] - 2026-07-26

### Added
- Optional cross-encoder rerank (`lattice_brain/graph/rerank.py`): env
  `LATTICEAI_CROSS_ENCODER_RERANK=1` (model via
  `LATTICEAI_CROSS_ENCODER_MODEL`); hybrid_search returns additive `rerank`
  meta; default path is identity with no model download.
- Sidecar-backed Playwright first-value E2E: `tests/e2e/`,
  `scripts/run_sidecar_e2e.mjs`, `npm run test:e2e:sidecar`, nightly
  `.github/workflows/e2e-sidecar.yml`.
- VS Code approval commands: `ltcai.listApprovals`, `ltcai.approveAgent`,
  `ltcai.rejectAgent` with pause-token session cache.
- Telegram approval handles both `waiting_approval` and `awaiting_approval`
  with run_id+token resume preferred.
- Agent loop L4/L5/L7 helpers: `artifact_checklist`, `files_written`,
  snapshot rollback ports (`snapshot_file` / `restore_snapshot`) with
  mode-tagged results (`git` | `snapshot` | `none`).
- Knowledge-graph read surface decomposition:
  `lattice_brain/graph/retrieval_reads.py` (`KnowledgeGraphReadsMixin`).

### Changed
- Legacy `human_in_loop` pauses now use the durable `AgentRunStore` path
  (`legacy_context=True`); the separate in-memory `_pending` map is removed.
  Wire contract (`status=waiting_approval`, `context_id`) is preserved.
- Rollback recovers file-create actions via pre-write snapshots when git is
  unavailable or not governed.
- Critic verify prompts include a deterministic artifact checklist derived
  from transcript sanitize/repair flags.
- Executor prompts list files already written in the current run.
- `docs/SURFACE_PARITY.md` marks VS Code/Telegram approval as provided.

### Tests
- `test_agent_loop_l4_l5_l7.py`, `test_cross_encoder_rerank.py`,
  `test_snapshot_rollback_ports.py`, L1 approval flow extensions.

## [9.9.4] - 2026-07-26

### Added
- Durable approval/run store (`latticeai/core/run_store.py`): paused
  `awaiting_approval` runs are mirrored to `data/agent_runs/` (one JSON file
  per run, SHA-256 token hashes, wall-clock expiry) and resume across server
  restarts; expired resumes answer 410 with a one-click replan hint;
  `GET /agent/approvals` lists pending runs (memory ∪ disk).
- Single retrieval policy (`lattice_brain/graph/retrieval_policy.py`):
  rule-based query rewrite (env kill-switch `LATTICEAI_QUERY_REWRITE=0`),
  query-class fusion weights, and a 14-day recency half-life consumed by both
  the 3-channel service fusion and the 2-channel graph fusion; responses
  carry `policy {search_query, rewrite_rules}` and recency-class matches a
  `scores.age_decay` multiplier in the [0.5, 1.0] band.
- Live agent step streaming: `AgentDeps.on_step` / per-run `ctx.on_step`
  observers emit plan/approval/execute/verify/rollback/terminal events;
  streamed agent chats send named `event: agent_step` SSE frames
  (`agent_live_stream`) before the unchanged final payload frames; the UI
  renders a live step timeline with a collapsed post-run summary.
- Type-aware chunking: markdown chunks at heading boundaries with
  `heading_path` provenance, code chunks at function/blank-line boundaries,
  plain text byte-identical to the legacy chunker (same chunk ids); every
  chunk records `strategy` + `start_char`; PDF chunks carry a 1-based `page`
  derived from per-page extraction offsets (omitted when implausible).
- Embedder fingerprint: the vector index records the embedder model id +
  dimension; a swap surfaces `stale_embedder` in `index_status().embedder`,
  vector freshness, and hybrid `vector_degraded` instead of silently empty
  vector channels.
- Citation-instructed answers: one `_compose_system` helper (all four prompt
  paths) appends `CITATION_INSTRUCTION` whenever retrieved context exists; a
  deterministic synthetic grounding bench gates `assess_answer_grounding`
  verdict accuracy in CI.
- Graph hygiene cadence: Command Center briefing gains a `hygiene` section +
  one-click dry-run quick action suggesting `/knowledge-graph/curate/noise`
  when the graph exceeds 200 nodes and no curation ran in 7 days
  (`last_noise_curate_at` in graph_meta).
- Review-before-promote: `LATTICEAI_GRAPH_PROMOTION_REVIEW=1` (or the
  `GRAPH_PROMOTION_REVIEW` enterprise capability) stages curator topic
  promotions as `pending_promotions` instead of writing them;
  `GET /knowledge-graph/promotions` + `apply`/`reject` endpoints.
- Project manifests beyond web: React/Vite starter (package.json, module-entry
  index.html, src/main.jsx, src/App.jsx, src/App.css) and Python package
  (`pkg/__init__.py`, `core.py`, `cli.py`, README.md); `normalize_plan`
  rewrites empty/partial pure-file plans to the manifest
  (`manifest_steps`/`manifest_rewrite` plan fixes).
- Harness: five deterministic workflow/multi-agent scenarios;
  `scripts/funnel_soft_gate.py` (advisory code_only/needs_review thresholds,
  `--strict` opt-in); `approval_pauses`/`approval_resumes` counters +
  `approval_resume_rate`; `scripts/bench_agent_smoke.py` weekly fail-open
  real-model agent smoke + `agent-smoke.yml` workflow (report artifact, never
  a gate).
- Frontend: agent step timeline, source-card → stored-chunk modal, folder
  watch health card, approval TTL countdown with expiry replan, generated-file
  "remembered in Brain" chips, demo-corpus → "connect your own data" CTA,
  parse-repair count note — all ko/en parity and bundle-budget clean.
- `docs/SURFACE_PARITY.md`: per-surface capability matrix with honest gaps.

### Changed
- Executor prompts use a sliding transcript window (recent 8 steps full,
  older steps one-line summaries, per-string truncation caps, last 3
  corrections); the critic sees every step with capped string bodies
  (`TranscriptBudget`, `LATTICEAI_AGENT_TRANSCRIPT_*`).
- Agent memory learnings now record the actual terminal status (ok /
  needs_review / failed) and prompt for what-went-wrong on non-DONE runs.

## [9.9.3] - 2026-07-22

### Added
- Multi-file Artifact Loop: `infer_project_manifest` → per-file
  generate/validate → cross-file reference repair → bundle validation →
  `artifacts[]` + `project.zip_url`; safe `GET /tools/download_zip`.
- Interactive approval: governed plans pause as `awaiting_approval` with a
  single-use 10-minute token; `POST /agent/resume` approves, edits, or
  cancels. Inline approval card in chat.
- First Value Loop: `POST/GET/DELETE /api/setup/demo-corpus` (3 built-in demo
  documents, `demo://` provenance, idempotent) + the "30초 체험" home track
  with suggested-question chips.
- Answer grounding: `grounding {status, source_ids, overlap}` on chat
  responses; 근거 있음/근거 없음 badge.
- Retrieval fusion: query-class detection (fact/code/person/recency) with
  per-class channel weights and a benchmark-threshold CI gate
  (`tests/unit/test_retrieval_fusion_gate.py`).
- Opt-in folder watch (`/api/ingestion/watch`, polling + persisted consent),
  capture-quality CTA (`capture_quality` on browser captures), graph noise
  curation job (`POST /knowledge-graph/curate/noise`, dry-run default).
- Automation visibility: `POST /api/automation/run-now` (dry-run-first),
  `last_execution` on overview/briefing, failed runs → Review queue; Act-panel
  "지금 한 번 실행" cards.
- UX: inline file preview (sandboxed iframe / modal), folder job report card,
  global drag-and-drop capture, 409 proposal rebase flow, focus traps, graph
  keyboard navigation, reduced-motion coverage, success pulse / inflow motes.
- Harness: agent_eval 23 scenarios (dirty-write filegen paths), golden
  sanitize fixtures, `bench_models.py --filegen` (fail-open), deterministic
  knowledge-pipeline E2E test, funnel metrics
  (`GET /api/admin/funnel-metrics`), `PhaseBudgets` per-phase token caps,
  `.ts/.tsx/.jsx/.vue/.svelte` filegen validation and Python `ast.parse`.

### Changed
- Approval-requiring agent runs return `awaiting_approval` instead of FAILED;
  legacy `context_id` resume with `approved: true` now executes the gated
  steps.
- Hybrid search responses expose `query_class`; chat responses expose
  `grounding`; automation overview exposes `last_execution`.

### Fixed
- Fenced/chatty CSS is now sanitized before write (`.css` validation rejects
  Markdown fences) — caught by the new golden fixtures.
- A legacy approved resume no longer re-fails at the approval gate.

## [9.9.2] - 2026-07-21

### Added
- ArtifactWritePipeline: `sanitize_write_content` in
  `latticeai/core/file_generation.py` — a conservative validate-first →
  extract → repair gate. The agent executor applies it to every `write_file`
  `args.content` before dispatch, records a `content_sanitize` verdict on the
  transcript, and tags `artifact_sanitize`/`artifact_repair` in the loop trace.
- Artifact-first chat contract: direct file-creation responses and agent runs
  carry an `artifacts[]` array (`kind/path/filename/bytes/previewable/valid/
  repaired`) via `collect_artifacts(transcript)`.
- Direct-path overwrite protection: an existing target is auto-suffixed
  (`generated_page_2.html`) and announced, instead of silently overwritten.
- Generated files are optionally indexed into the Brain through the unified
  `IngestionPipeline` (`workspace://` provenance, `origin: generated_file`);
  disable with `LATTICEAI_INGEST_GENERATED=0`. The response reports an honest
  `brain_ingest` status; ingest failures never fail the file creation.
- Plan schema enforcement (`normalize_plan`): non-empty goal, junk-step
  filtering, `estimated_steps` clamping, and a deterministic single
  `write_file` step for file-intent requests whose plan came back empty.
- Memory quality filter (`filter_learnings`): trivial and duplicate agent
  learnings are dropped before entering the Brain.
- FG harness `tests/unit/test_artifact_write_scenarios.py` (FG-01..FG-08):
  intent gating, filename inference, dirty-output extraction, truncated-HTML
  repair, agent-path sanitization, how-to non-routing, scaffold validity.
- Frontend honesty surfaces: "Auto-repaired" badge on file cards
  (`generation.repaired`), and a distinct warning strip for `NEEDS_REVIEW` /
  `FAILED` terminal agent states (`role="alert"`, ko/en, dark/light).

### Changed
- HTML file validation now rejects documents wrapped in prose or Markdown
  fences, so extraction gets a chance to slice out the real document instead
  of the wrapper being saved as "valid".
- `product_readiness` `action-aware-chat` gate additionally proves the
  ArtifactWritePipeline evidence (module, agent seam, FG harness test).

## [9.9.1] - 2026-07-21

### Removed
- All repo-root compatibility shims except `server.py`: `ltcai_cli.py`,
  `auto_setup.py`, `setup_wizard.py`, `mcp_registry.py`, `kg_schema.py`,
  `knowledge_graph.py`, `knowledge_graph_api.py`, `local_knowledge_api.py`,
  `llm_router.py`, `p_reinforce.py`, `telegram_bot.py`, and the root `tools/`
  package (12 of 13 tracked shims, 92%). Canonical imports are the
  `latticeai.*` / `lattice_brain.*` module paths; the console script and
  `bin/ltcai.js` now target `latticeai.cli.entrypoint` directly.
- Stale release evidence: `output/release/` now keeps only the newest three
  versioned evidence directories (automated retention policy).

### Added
- Legacy debt gate: `scripts/check_legacy_debt.mjs` in `npm run lint` plus a
  rewritten `tests/unit/test_legacy_root_shims.py` fail if a root module
  reappears or any source tree imports a removed shim.
- `scripts/prune_release_evidence.mjs` retention policy (newest 3, override
  with `LTCAI_RELEASE_EVIDENCE_KEEP`), wired into `npm run release:evidence`.
- First-run "First 5 minutes" guided card on the empty Brain home: ask a first
  question, add a first file/note, see what the Brain learned — real feature
  wiring, persistent progress, dismissible.
- Daily briefing on the Brain home (immediate fetch, friendly empty state) and
  proactive quick actions on Cmd+K cold open (open briefing, review pending,
  ask the Brain) with live counts.
- Localized API error pipeline: timeout/unreachable/status failures map to
  friendly ko/en copy instead of raw `statusText` or English literals.
- `scripts/check_current_release_docs.mjs` now verifies the ARCHITECTURE.md
  Release Artifact Map names current-version artifacts exactly.

### Changed
- Review Center at product quality: translated status/source/risk/change-class
  labels, framed diffs (file target, +/- coloring, honest truncation count),
  raw IDs relegated to a collapsed "Technical details" disclosure, structured
  success/failure feedback, and a distinct error state with retry for the
  pending-proposals panel.
- Intelligence/care/admin/markdown panels show friendly i18n error copy first;
  raw backend detail is demoted to secondary text.
- 37 version-named test files (`test_v3_*`, `test_v42_*` … `test_t9_*`,
  `test_kg_v2/v4_*`) renamed to scenario-named suites; versioned test
  function names cleaned up. Coverage is unchanged (1284 unit tests).
- Packaging: `pyproject.toml` ships a single root module (`server`);
  `package.json` files list and `MANIFEST.in` no longer ship shim files.
- Docs: `docs/LEGACY_COMPATIBILITY.md` rewritten for the post-shim layout;
  `docs/kg-schema.md` points at `lattice_brain/graph/schema.py`; ARCHITECTURE
  artifact map and 9.8.0-era references brought current.

## [9.9.0] - 2026-07-21

### Fixed
- Change proposals record the target's original content hash and existence;
  approval re-hashes the disk and rejects a modified/deleted/created target
  with a 409 conflict instead of overwriting newer content. Applies are atomic
  (`os.replace`) and serialized so duplicate/concurrent approvals apply once.
- The agent verifier no longer fabricates a PASS on unparseable critic output:
  one strict repair retry, then the new terminal `NEEDS_REVIEW` state. `DONE`
  now requires a valid PASS and deterministic execution evidence; the loose
  `next_state == DONE` success path is removed.
- Device analysis no longer fabricates a `supported: true` model card on probe
  failure; the recommendation screen models `loading | ready | unavailable`
  and offers retry / continue-without-a-model.

### Added
- `MUTATING_TOOL_INVENTORY` single-source governance classification for every
  side-effecting tool, a fail-closed CI coverage gate, and dispatch-level
  blocking (409) of existing-content overwrites that cannot be staged as a
  reviewable proposal (`create_docx/xlsx/pptx/pdf`, `local_write`).
- Agent-eval result classification (`correct_completion` / `safe_termination`
  / `needs_review` / `failed`) and fail-closed verifier scenarios (20 total).
- CI: `dependency-audit.yml` (pip-audit + npm audit + CycloneDX SBOM) and
  scheduled `postgres-integration.yml`; all GitHub Actions SHA-pinned; a
  frontend bundle-budget gate (150 KiB gzip).
- `docs/SECURITY_AUDIT.md`, `docs/BENCHMARKS.md` + `scripts/bench_models.py`,
  `docs/USABILITY_AUDIT.md`, `docs/CI_AND_RELEASE_GATES.md`, and a
  documentation status/link gate (`scripts/check_doc_status.mjs`).

### Changed
- Initial JS bundle reduced ~22% (180.3 → 141.6 KiB gzip) via lazy-loaded
  onboarding, Brain home, and command palette.
- `ARCHITECTURE.md` verified against the real module layout and corrected;
  stale 9.6-era operational/feature/development docs updated and classified.

## [9.8.0] - 2026-07-20

### Added
- Added `extraction_quality` (score/level/reasons) and low-quality `warnings`
  to every ingest result, with upstream extractor confidence taking
  precedence; the proactive `gate_ingest_candidate()` now records an
  observe-only `quality_gate` verdict on non-chat ingests.
- Added background ingestion job progress (`total`/`processed`/`failed`,
  capped per-item errors) with resume-from-remaining support, plus
  `GET /api/ingestion/jobs`, `GET /api/ingestion/jobs/{id}`,
  `POST /api/ingestion/jobs/{id}/resume`, and approval-gated
  `POST /api/ingestion/folder`.
- Added `context_quality` (mode/nodes/limited/reason) to chat responses
  (non-stream top-level and final SSE trailer) with a localized
  limited-context note in the assistant bubble.
- Added `GET /api/brain/vector-freshness` and a pending-indexing chip in the
  Brain views; `vector_freshness()` never raises and degrades to
  `unavailable` with a reason.
- Added four agent-eval scenarios (ingestion chain, concept extraction,
  RAG-grounded answer with a negative grounding test, automation
  proposal-first), growing the CI gate to 16 scenarios with
  `expect_final_contains` grounding assertions.
- Added deterministic `confidence`, `confidence_factors`, duplicate
  suppression, installed-recipe detection, and a low-quality floor to
  automation suggestions, with a `quality` reporting block.
- Added a frontend ingestion jobs panel (progress bar, failed count, resume)
  and extraction-quality warnings in the ingestion panels, all with ko/en
  i18n parity.

### Changed
- Rebuilt README media-first: hero walkthrough GIF, screenshot grid, compact
  release-history table, and roughly 60% less prose.
- `context_for_query()` gains an opt-in metadata path
  (`context_for_query_with_meta()`); the default output is byte-identical.
- Background ingestion job initial status is now `queued` (was internal
  `pending`).

## [9.7.0] - 2026-07-20

### Added
- Added `KnowledgeGraphStore.hybrid_search()`: graph-native fusion of
  lexical and vector retrieval with normalized scores, per-source
  provenance, chunk→parent roll-up, workspace scoping, and an honest
  `lexical_only` fallback; `context_for_query()` gains an opt-in
  `use_hybrid` flag.
- Added `index_node_incremental()` and automatic post-ingest vector-index
  sync in `IngestionPipeline` (opt-out `LATTICEAI_AUTO_VECTOR_INDEX`);
  vector failures downgrade to `indexing_status: pending` instead of
  failing the ingest.
- Added `IngestionPipeline.ingest_folder()` (recursive walk,
  `.latticeignore` gitignore-like filtering, size/extension limits,
  optional background queue) and `ingest_web_page()` (formalized
  extracted-text web seam; fetching/parsing stays upstream).
- Added `lattice_brain/graph/proactive.py` (`ProactiveBrain`): duplicate
  detection, contradiction detection, combined `quality_report()`,
  consent-first `consolidate_duplicates()` planning, and the pure
  `gate_ingest_candidate()` quality-gating seam.
- Added `GET /api/brain/duplicates` and `GET /api/brain/quality-report`;
  `/api/brain/contradictions` and `/api/brain/consolidate` return
  graph-layer results additively.
- Added `GET /api/proposals/counts`, `GET /api/proposals/{id}` (diff +
  staged content), reject-with-reason, and
  `GET /automation/reviews/counts`; the Review Center UI gains a
  `change_proposal` filter, diff preview, tier/deletion badges, a
  pending-count badge, and a reject-reason input (ko/en parity).
- Added 4 agent-eval scenarios (12 total): file-generation happy path,
  file-generation recovery, multi-step workflow chain, and a
  governed-write proposal path pinning the
  approve()-excludes-governed-tools invariant.
- Added `tests/unit/test_runtime_consistency.py`,
  `scripts/profile_kg.py`, and `docs/PERFORMANCE.md` (measured synthetic
  KG baseline; flags brute-force `vector_search()` as the next
  optimization target).

### Changed
- Review Center approval of `change_proposal` items now applies the staged
  content through `ChangeProposalService.approve_and_apply` (single
  application path, 409 on replay) instead of only flipping status.
- Change proposals carry tool/risk/change-class/conversation-id
  provenance; the agent loop forwards `conversation_id` to the governor.
- `SingleAgentRuntime.execute` decomposed into six focused helpers
  (behavior-preserving); the multi-agent orchestrator surfaces real
  failure reasons in `execution_failed` timeline events.
- All root legacy modules emit `DeprecationWarning` pointing at their
  package homes; the legacy-compatibility registry tracks all 13 shims.

### Security
- Proposal apply/reject stays fail-closed end-to-end: approval applies
  exactly the reviewed staged content, replays are rejected with 409, and
  reject reasons land in provenance for audit.

## [9.6.0] - 2026-07-20

### Added
- Added `latticeai/core/agent_trace.py` (`LoopTrace`): typed observability
  for the single-agent reasoning loop, returned as `loop` with every agent
  API response.
- Added `latticeai/core/agent_eval.py` + `scripts/agent_eval.py`: a
  deterministic scripted-model evaluation harness over the real agent state
  machine, wired into CI as a release gate.
- Added `latticeai/core/tool_governor.py` and
  `latticeai/services/change_proposals.py` + `/api/proposals` router:
  central read/additive/mutation/destructive classification with
  proposal-first governance — edits/deletions of existing files are staged
  as review-queue proposals (source `change_proposal`) with unified diffs
  and applied exactly as reviewed on approval.
- Added the "변경 제안 / Change proposals" Brain home panel with diff
  previews and one-click approve/reject.

### Changed
- The action parser tolerates Python-literal dicts and reports every repair
  by name; repeated formatting slips escalate the correction with the valid
  tool list.
- Additive file creates in the agent loop run without plan-approval
  friction; plan approval no longer hard-blocks governor-managed tools.
- Ruff per-file lint ignores trimmed from 9 entries to 3.

### Security
- Mutations and deletions of existing workspace files by the agent can no
  longer apply silently: they stage as reviewable proposals, and approve
  applies the exact reviewed content with full audit events.
- AGENTS.md is now inside the machine-checked release documentation gate.

## [9.5.0] - 2026-07-20

### Added
- Added `latticeai/services/command_center.py` and the `/api/command/*`
  router: a daily briefing aggregating recent knowledge, conversation
  activity, automation state, pending reviews, a Brain-health snapshot, top
  automation suggestions, and state-derived quick actions with stable ids;
  and a universal search grouping knowledge nodes, the user's own
  conversations (deduped per conversation), and installed automations.
- Added the Cmd+K Command Palette (grouped results, keyboard navigation,
  debounced universal search, static page jumps) and the collapsible
  "오늘의 브리핑 / Today's briefing" panel on the Brain home, fully ko/en
  localized.
- Added `tests/unit/test_command_center.py` (11 tests) and
  `frontend/src/features/command/CommandPalette.test.tsx` (3 tests).

### Changed
- Cmd+K now opens the Command Palette instead of only focusing the chat
  composer; the palette includes a direct jump to the Brain conversation.

### Security
- Both Command Center endpoints are read-only and scoped to the requesting
  user and workspace; scoped reads exclude legacy-global rows, and every
  briefing section degrades independently without leaking errors.

## [9.4.0] - 2026-07-20

### Added
- Added `latticeai/services/automation_intelligence.py` and the
  `/api/automation/*` router: recurring-question pattern mining
  (deterministic local token-signature clustering with literal-question
  evidence), automation suggestions from question patterns and connected
  knowledge folders, idempotent consent-first install (disabled draft,
  review-queue gated, local-only), and a combined overview payload.
- Added the "Automation suggestions for you" panel to the Act page with
  evidence chips and one-click creation, fully ko/en localized.
- Added `tests/unit/test_automation_intelligence.py` (10 tests).

### Changed
- Recurring question intents that match a starter recipe (daily digest,
  weekly project review, follow-up radar) suggest that recipe; other
  repeated questions become parameterized "scheduled answer" workflows.

### Security
- History mining respects user/workspace scoping and excludes legacy-global
  rows for scoped reads; suggestion installs carry provenance metadata and
  never enable themselves.

## [9.3.0] - 2026-07-20

### Added
- Added `latticeai/services/brain_intelligence.py` and the `/api/brain/*`
  router: health diagnosis (freshness/connectivity/search-readiness/
  consistency scores with recommended care actions), proactive insights
  digest, contradiction surfacing (memory pairs, temporal, CONTRADICTS
  edges), and consent-first duplicate consolidation (dry-run default,
  audited memory prune on apply, graph never mutated).
- Wired the previously dormant `lattice_brain.quality` layer
  (MemoryQualityManager, GraphEdgeQualityManager) into the product.
- Added the "Brain intelligence check" panel to the Brain surface with full
  ko/en localization, plus `latticeApi` client methods for the new endpoints.
- Added `tests/unit/test_brain_intelligence.py` (14 tests).

### Changed
- `/api/memory/recall` is now hybrid: vector similarity blends with lexical
  term evidence behind a `hybrid-evidence/v2` quality gate; results carry
  `vector_score` and `evidence_kinds`; vector matches are workspace-scoped
  via `filter_scoped_nodes` before they can influence rankings.

### Fixed
- Recall no longer misses knowledge phrased differently from the query when
  the vector index is available; vector-tier failures degrade recall to
  lexical with the error surfaced instead of breaking the endpoint.

## [9.2.0] - 2026-07-20

### Added
- Added `latticeai/core/file_generation.py`, a model-agnostic file content
  pipeline: extension-aware strict prompting with pinned first lines, payload
  extraction from fences/`<think>` blocks/chat framing, per-type structural
  validation, one corrective retry, and a deterministic repair fallback that
  guarantees a structurally valid file from any loaded LLM.
- Added filename inference for chat file requests that name a type but no path
  ("html 파일 만들어줘" → `generated_page.html`), keeping such requests on the
  deterministic direct-write path instead of the model-driven agent loop.
- Added `generation` metadata (attempts, validation reasons, auto-repair flag)
  to `/chat` direct file-action responses, and a user-facing notice when the
  saved file was produced by auto-repair.
- Added `tests/unit/test_file_generation.py` covering extraction, validation,
  repair, inference, prompting, and retry/repair orchestration (22 tests).

### Changed
- Chat file-action routing counts explicit artifact types (html, 웹페이지,
  webpage) as file words, so "html 페이지 만들어줘" creates a real file.
- Direct file generation clamps temperature to ≤0.3 and raises the token
  budget to ≥4096 so generated documents complete.
- The agent executor prompt pins `write_file` content rules: complete raw
  content, no Markdown fences, extension-valid documents.

### Fixed
- Small local models (gemma/qwen class) no longer save chat wrappers, fenced
  code blocks, reasoning traces, or truncated documents as file bytes.
- The agent JSON loop no longer aborts on the first malformed action reply:
  `extract_action` strips `<think>` blocks and tolerates trailing commas, and
  up to two corrective format reminders are fed back before halting.

## [9.1.0] - 2026-07-11

### Added
- Added required Telegram chat/callback allowlisting through
  `LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS` and authenticated local API bridging
  through `LATTICEAI_SERVER_SESSION_TOKEN`.
- Added signed, expiring invitation authorization, explicit legacy-global graph
  read opt-in, and fail-closed regression coverage for broken or unknown
  Knowledge Graph scope.
- Added optional permission-review deep links through
  `LATTICEAI_PERMISSION_UI_URL` while keeping approval tokens out of outbound
  notifications.
- Added Vitest coverage for API empty/result shapes, Brain proof state,
  conversation sessions, shared primitives, and i18n, plus visual assertions
  for unavailable core services.
- Added continuous vital rings, heartbeat echoes, neural sparks, body motion,
  and Brain-to-graph particles whose speed and intensity reflect real listening,
  recall, synthesis, and automation activity. Reduced-motion preferences still
  disable nonessential animation.
- Added a living knowledge flow that shows chat, files, folders, notes, and web
  pages entering the Brain, then renders a lightweight graph from actual
  Knowledge Graph nodes and edges on the first screen.
- Added persistent conversation-to-knowledge traces, truthful ingestion
  emergence counts and provenance, native desktop folder selection, and
  evidence-linked Brain automation actions.
- Added a consent-preserving recipe lifecycle: create a reviewable disabled
  draft, inspect its memory focus and evidence, then explicitly enable it.
- Added scoped MemoryService recall to workflow agents and researcher-first
  execution for triggered and review-queue automation runs.
- Added a visible task-oriented desktop navigation, mobile bottom navigation,
  accessible secondary menu, skip link, focus trap/restoration, keyboard-driven
  tabs, and reduced-motion behavior for the React workspace.
- Added a dedicated human-first experience stylesheet that owns the product
  shell, conversation canvas, shared content rhythm, responsive behavior, and
  warm paper/ink/jade visual tokens.
- Added regression gates for model-request concurrency, workspace/identity
  authorization, graph ID isolation, command sandbox escapes, SSRF and redirect
  rebinding, approval ownership, release archive hygiene, browser-extension byte
  limits/timeouts, OpenAPI drift, and disposable test harness state.
- Added a checked OpenAPI drift command and browser-extension test command to the
  normal lint gate.

### Changed
- Replaced ambient app-factory namespace assembly with typed config, security,
  Brain, model, and router stages while retaining an explicit compatibility
  surface.
- Replaced model-runtime dual globals with injected typed state and split chat
  contracts, documents, history, and streaming into focused modules.
- Split Brain feature hooks, translation namespaces, and experience CSS by
  product surface; frontend version copy now comes from package metadata.
- Consolidated shallow runtime pass-through modules, timestamps and run-status
  constants, and moved setup/local-knowledge implementations behind package
  owned modules with root compatibility shims.
- Archived code-review documents under `docs/reviews/`, removed obsolete local
  VSIX artifacts, and documented Electron as an experimental compatibility shell.
- Compressed the empty Brain home into a single desktop/mobile viewport: the
  living Brain, real graph, source dock, composer, primary grounded action, and
  flow status remain visible without page scrolling. Conversation history,
  deeper proof, and the complete automation action set now open in bounded
  overlays instead of stacking below the fold.
- Rebuilt Brain Home around the visible source-to-memory-to-graph-to-automation
  journey, while keeping the greeting, composer, suggested starts, recent
  conversations, and optional deeper Brain proof.
- Renamed and regrouped the primary experience around Chat, Sources, Memory, and
  Work. Memory now opens with search, basic Work leads with one goal composer,
  and basic mode hides pipeline, agent-registry, runtime-metric, plugin, and
  operator tabs until requested.
- Synced major tab choices to canonical hash routes so refresh, browser history,
  and bookmarked Sources, Memory, Work, Model, and Settings subviews preserve
  the user's place.
- Restyled shared panels, entity lists, empty states, statistics, onboarding,
  source capture, and active messages to use solid surfaces, whitespace, and
  separators instead of nested glass cards and dashboard grids.
- Brain Home now fetches a bounded graph preview so it can show real knowledge
  relationships immediately; heavier proof and the full graph explorer remain
  deferred until requested.
- Model generation, streaming, and document generation now use request-scoped
  model snapshots without mutating the global selected model.
- New workspace-scoped graph nodes derive IDs from workspace identity, preventing
  identical messages/files/concepts in separate workspaces from overwriting or
  reassigning one another while preserving legacy unscoped reads.
- Workspace and identity transitions synchronously clear frontend query and
  conversation state; the client-only egress toggle that claimed server-wide
  enforcement was removed.
- Hook/model/network/registry/whole-graph portability and graph-curation operations now use administrator
  boundaries, and chat/browser/upload/KG writes resolve workspace write access.
- MCP graph calls now enforce authenticated identity and workspace-scoped reads
  and writes; local MCP environment values are never returned by the API, and
  MCP/plugin tool dispatch cannot bypass the dedicated local-file approval flow.
- Memory, hybrid-search, and garden-note context assembly now use the active
  workspace instead of blending content from every workspace the user can access.
- Document-generation RAG, answer traces, trace timeline events, and realtime
  unscoped events now fail closed at the active workspace boundary.
- Long-lived realtime streams revalidate the session and workspace membership
  before replay, every queued event, and each heartbeat, so revocation takes
  effect without waiting for the SSE connection to reconnect.
- Realtime presence now validates workspace membership, prevents cross-user
  client-id takeover/eviction, and assigns authenticated joins to an allowed
  workspace instead of an unscoped global presence record.
- Local data directories and atomic JSON/session files now use private POSIX
  permissions where supported.
- Docker packaging now uses a small build context, a non-root runtime user, and
  a healthcheck; personal OpenClaw/bot bridge files are excluded from Git,
  Docker, and every release archive while remaining available as ignored
  machine-local files.

### Fixed
- Closed every actionable item in the July 11 code review across fail-closed
  security, typed runtime/model/chat ownership, honest frontend failures/tests,
  and repository hygiene.
- Telegram now rejects unknown chats before registration; invitation gates no
  longer trust a static cookie or built-in code, including SSO just-in-time
  provisioning; and graph projection failures cannot expose cross-workspace
  nodes.
- Agent and Computer Use history/run persistence now retains authenticated
  user and workspace ownership, and recent model context is selected through
  fail-closed storage scopes before prompt assembly.
- Desktop, knowledge, Obsidian, and network-status tools now use explicit
  policy/capability/consent checks. Permission queues use atomic private writes,
  notifications expose token hints only, and MCP paths are masked.
- Failed API results no longer become quiet healthy Brain state, proof is not
  synthesized after failure, and action success callbacks run only on success.
- Fixed ingestion deltas that compared against truncated display lists instead
  of complete graph/readiness baselines, and replaced simulated stage timers
  with post-response memory and graph verification.
- Fixed global Brain recall pulses being ignored by Brain instances without a
  local callback, relationship visuals inventing fallback endpoints, and SVG
  gradient IDs colliding when multiple living Brains appear on one page.
- Fixed Brain automation hook/agent configuration mismatches, missing scoped
  recall, trigger runs that skipped agent execution, and review-queue runs that
  did not execute the workflow's agent node.
- Fixed local-folder graph nodes and Brain events trusting unvalidated or absent
  workspace scope, losing scope on watcher restart, appearing in another
  workspace's graph/search, or waking another workspace/owner's recipe. Legacy
  personal nodes are reprojected without changing their IDs.
- Fixed recipe activation replacing reviewed user edits with the starter
  definition, and stopped empty web captures from being presented as newly
  remembered knowledge.
- Blocked permission request self-approval, stale disabled/deleted-account
  sessions, agent approval forgery and cross-user resume, body identity spoofing,
  command traversal/symlink/interpreter escapes, and authenticated scope lookup
  fail-open behavior.
- Hardened URL ingestion against localhost/private/link-local/multicast/reserved
  targets, mixed DNS answers, redirect-to-metadata attacks, environment proxies,
  binary payloads, and unbounded response buffering.
- Isolated integration and OpenAPI generation from real `~/.ltcai` state and
  made missing required npm tarballs a release-validation failure.

## [9.0.0] - 2026-07-08

### Added
- Added Brain Brief suggested questions that turn current memory, recall proof,
  graph concepts, and conversation history into clickable first-screen prompts.
- Suggested Brain questions now send immediately from the first screen instead
  of only filling the composer.
- Added one-click follow-up prompts under the latest Brain answer for turning a
  reply into a checklist, evidence review, or prioritized next steps.
- Added a Brain chat to Review Center handoff so users can save an answer as a
  reviewable task draft and manage it alongside automation suggestions.
- Added direct Brain-to-Agent delegation and successful agent-run synthesis into
  durable Brain memory/graph context.
- Surfaced recent agent-synthesis memories in Brain overview and memory rings
  so delegated work is visibly reflected on the home screen.
- Improved agent-run Brain synthesis quality by splitting successful results
  into key facts, decisions, and follow-up memories with structured metadata.
- Agent follow-ups now enter Review Center as task drafts so delegated work
  produces actionable approval candidates instead of passive memory only.
- Approving an Agent follow-up review item now promotes it into a manual
  workflow draft with trigger, agent, and output nodes.
- Added large-feature foundations for KG/Retrieval scale diagnostics,
  background ingestion scheduling, offline multimodal image captions,
  proactive contradiction detection, and ingestion bridge marketplace templates.
- Added proactive Brain action cards that turn Brain Brief evidence into
  one-click ask, Agent delegation, Review Center draft, or graph navigation
  actions on the Brain home screen.
- Added a visible proactive Brain action trail so one-click suggestions show
  their running/completed/failed state after the user acts on them.

### Changed
- Continued app-factory decomposition by extracting user profile/API-key helper
  wiring into `latticeai.runtime.user_key_runtime`, keeping the legacy
  `server_app` callable surface while making keyring/plaintext fallback policy
  independently testable.
- Split additional runtime and static-data seams out of app, chat, MCP, model,
  and Knowledge Graph modules while preserving re-export compatibility for
  existing imports.
- Routed Computer Use direct `/cu/*` actions through the shared ToolRegistry
  policy gate and audit lifecycle, preserving route paths while blocking
  non-admin direct desktop control by default.
- Moved blocked-system-prefix protection into `tools.local_write` itself so
  local filesystem writes fail closed even when called outside the HTTP approval
  route.
- Narrowed the lazy `server_app` compatibility namespace by filtering
  app-factory scratch imports and runtime wiring dictionaries while preserving
  explicit legacy helpers, and added a typed `RuntimeBundle` migration target
  behind the legacy `_RUNTIME_BUNDLE` dict.

### Fixed
- Added regression coverage for provider API-key lookup/storage behavior,
  including keyring precedence, plaintext fallback gating, legacy plaintext
  cleanup after keyring writes, and identity creation on plaintext fallback.
- Added regression coverage for Computer Use policy enforcement, audit-safe
  typed-text metadata, and direct local-write system-prefix blocking.
- Fixed functional findings from the July 8 code review: file generation now
  fails cleanly when no model is loaded, chat/document streams preserve a
  terminal SSE event on generation errors, agent runs persist failed status on
  executor exceptions, Brain delegation treats HTTP failures as failures, and
  local permission expiry cleanup no longer corrupts the active token lookup.
- Tightened non-security chat intent detection, Telegram bot server URL
  configuration, LATTICE_TZ-aware runtime audit timestamps, local embedding
  dimension consistency, and stale Brain UI version copy.
- Paid down the remaining July 8 cleanup debt by moving duplicated JSON/ISO/hash
  and setup detection helpers into shared modules, switching runtime audit
  appends to JSONL while preserving legacy JSON reads, making the legacy runtime
  namespace allowlist-based, clarifying static-vs-SPA design token ownership,
  and consolidating duplicated frontend helper functions.
- Reduced the remaining chat-router risk by extracting repeated chat history,
  bridge notification, no-model, single-answer, direct-file, and agent-file
  response paths out of the main `/chat` handler, with regression coverage for
  the shared fast-path epilogue.

## [8.9.0] - 2026-07-06

### Added
- Added authenticated user/workspace scoping to durable conversation history
  reads and deletes.
- Added workspace-aware Knowledge Graph search, traversal, relationship, node,
  and chat-context reads.
- Added direct HTTP/MCP Tool API policy enforcement for registry-governed tools.
- Added permission approval queue hashing and atomic writes so raw tokens are
  not persisted at rest.
- Added confirmation-token guarded installer/process command plans with redacted
  local process audit events.
- Added regression coverage for scoped history, graph scoping, tool policy
  gates, AgentRuntime approval semantics, permission tokens, session TTL
  injection, and model-download runtime config.

### Changed
- AgentRuntime now requires explicit human approval for non-auto-approved plans
  and rolls back git-governed tool results even when `success` is omitted.
- Model download consent now uses configured runtime state instead of direct
  environment-variable reads.
- Frontend API base logic, CSS token/base rules, and i18n literal checks were
  split into smaller maintainability seams.
- Version bumped to 8.9.0 across Python, npm, VS Code extension, Tauri, static
  metadata, readiness gates, release notes, and current-release documentation.
- Documentation now states that SQLite is the live local Brain store; Postgres
  remains optional scale/migration tooling.

### Fixed
- Conversation store migration now creates the workspace index only after the
  scope columns exist.
- Direct `write_file`/`edit_file` policy lookup now blocks system write
  prefixes consistently with local-file approvals.
- Workspace selection clearing now removes the persisted workspace id.
- Tauri/local API fetches now include credentials for localhost backend
  sessions.

## [8.8.0] - 2026-07-06

### Added
- Added Brain Core isolation coverage that keeps `lattice_brain` independent of
  product-package imports.
- Added recall proof quality gates for matched terms, confidence labels, and
  lexical evidence filtering.
- Added Brain Chat conversation controls for new/resume/delete, stop,
  regenerate, copy, and richer ingestion progress.

### Changed
- Removed internal-only Brain shim layers: flat pre-graph modules,
  `latticeai.brain`, and `latticeai.services.agent_runtime`.
- Updated legacy compatibility reporting so removed shim layers are tracked
  separately from remaining external root shims.
- Hardened AgentRuntime boundary handling for unknown roles, legacy run
  contracts, and persisted retry budgets.
- Version bumped to 8.8.0 across Python, npm, VS Code extension, Tauri, static
  metadata, readiness gates, release notes, and current-release documentation.

### Fixed
- File ingestion now rejects directory paths before dispatching to document
  ingestion.
- Memory recall filters low-evidence noise when stronger lexical matches are
  present and surfaces explainable citation confidence.

## [8.7.0] - 2026-07-05

### Added
- Added unit test coverage for model-runtime `STATE` source-of-truth behavior
  and deprecation warnings on legacy global synchronization.
- Added refreshed 8.7.0 screenshots, walkthrough GIF/WebM, and capture notes
  under `output/release/v8.7.0/`.

### Changed
- Reduced internal reliance on bare module globals in
  `latticeai/services/model_runtime.py`; implementation logic now consistently
  reads from the `ModelRuntimeState` instance while globals remain as a legacy
  compatibility surface.
- Added `DeprecationWarning` to `sync_to_module_globals()` while preserving the
  external shim behavior.
- Removed a loose `as any` cast from `frontend/src/pages/Act.tsx`.
- Version bumped to 8.7.0 across Python, npm, VS Code extension, Tauri, static
  metadata, README evidence links, release notes, and current-release
  documentation.

### Fixed
- Internal model-runtime functions now prefer the typed state object per the
  project preference for composition over global mutable state.

## [8.6.0] - 2026-07-05

### Added
- Added Tauri localhost remote capability coverage so the desktop app can keep
  using local IPC commands after navigating to the FastAPI-served `/app`.
- Added a regression trust gate for the Tauri localhost capability.

### Changed
- Improved the Capture source flow: desktop users can choose a folder with the
  native folder picker and immediately scan/connect it, while web page capture
  now supports paste, Enter-to-save, and `https://` normalization for bare
  domains.
- Updated Visual Smoke coverage for the new Brain shell sidebar and admin
  console entry flow.
- Version bumped to 8.6.0 across Python, npm, VS Code extension, Tauri, static
  metadata, readiness gates, and current-release documentation.

### Fixed
- Fixed native folder selection from the Tauri production app's localhost
  webview and added visible fallback feedback when the picker is unavailable.
- Removed negative letter spacing from updated frontend shell styling.

## [8.5.0] - 2026-07-01

### Added
- ToolRegistry now reports `ready: true` with full handler/governance/description alignment (added `vision_analyze` policy and description).
- `tz_name` now flows from central `Config` into `TriggerService` (via updated automation and platform wiring runtimes) for better DI and Config centralization.

### Changed
- Full codebase scan completed; improvements prioritized per AGENTS.md (registry, config injection, wiring seams).
- Version bumped to 8.5.0 across pyproject.toml, package.json, vscode-extension; all current-release doc references synchronized.
- Documentation sync performed for README, RELEASE.md, docs/CHANGELOG.md and Current release markers.

### Fixed
- Eliminated ToolRegistry drift for `vision_analyze` (handler existed in tools/ but missing from core registry governance surface used by diagnostics, MCP, and permission views).

## [8.4.0] - 2026-07-01

### Added
- Added a chat-to-agent file action gate so explicit file create/write/save/edit
  requests from Brain Chat execute through the governed workspace file tool.
- Added regression coverage that verifies `/chat` routes file creation intent to
  the workspace file tool and returns created artifact metadata.

### Changed
- Kept ordinary Q&A on `/chat` while routing only explicit side-effect file
  requests into the planner/executor/reviewer tool loop.
- Synchronized package/runtime/static/Tauri metadata and current-release docs to
  8.4.0.

### Fixed
- Allowed literal `/chat` file writes with user-provided content to execute
  before model loading, while still using a loaded model when content must be
  synthesized.
- Narrowed file target and content parsing to avoid treating surrounding prose
  as the workspace path or literal file body.
- Restored model loading dependency exports after the runtime/loading split so
  `/models/load` can prepare local MLX models again.
- Added the common Gemma 4 26B shorthand alias to the canonical
  `mlx-community/gemma-4-26b-a4b-it-4bit` model id.
- Updated the local server launcher to run `python -m uvicorn` from the active
  virtual environment, avoiding stale console-script interpreter bindings.

## [8.3.0] - 2026-07-01

### Added
- Added a managed legacy compatibility report for remaining root shims,
  including owners, replacements, reasons, removal phases, and readiness
  metrics.
- Added AgentRuntime/workflow maturity evidence through lifecycle helper reuse,
  legacy event compatibility, WorkflowEngine boundary/config inspection, and
  centralized legacy workflow step projection.
- Added graph ingestion coverage for upload-to-pipeline behavior and
  workspace-safe duplicate content.
- Added 8.3.0 onboarding and community/plugin growth documentation.

### Changed
- Routed `/knowledge-graph/ingest` through the unified `IngestionPipeline` when
  available, preserving provenance and hook lifecycle behavior for MCP notes and
  messages.
- Isolated text/web/note graph node identity by workspace while preserving the
  content hash used for duplicate detection.
- Converted `mcp_registry.py` and `llm_router.py` into physical module aliases
  for their current implementations.
- Improved upload client error handling so failed uploads cannot look
  successful.
- Synchronized package/runtime/static/Tauri metadata and current-release docs to
  8.3.0.

### Upgrade Notes
- Existing legacy-global text/web/note graph nodes are not rewritten in place.
  Re-ingesting the same content with a workspace id can create a separate
  workspace-scoped node; re-index existing sources after upgrading when you want
  provenance to converge on workspace scopes.

## [8.2.0] - 2026-06-27

### Added
- Added an evidence-backed Brain Brief to Brain Home so users can see what to
  notice, what evidence supports it, and what action to take next.
- Added `GET /api/memory/brain-brief`, backed by `MemoryService.brain_brief()`,
  with honest empty-state guidance and recall/graph/model-proof actions.
- Added unit coverage for Brain Brief service behavior and the memory API route.

### Changed
- Completed the remaining model loading/runtime extraction into
  `model_loading.py` and `model_engines.py` while preserving compatibility
  delegations from `model_runtime.py`.
- Extracted WorkspaceOS graph trace, agent/workflow run, skill, and snapshot
  comparison ownership into focused manager modules.
- Wired Knowledge Graph embedding dimensions from the central resolved `Config`
  embedder at app startup.
- Synchronized Python, npm, VS Code extension, Tauri, workspace, readiness,
  static asset, and current-release documentation versions to 8.2.0.

## [8.1.0] - 2026-06-27

### Changed
- Rebuilt Brain Home around an intuitive first screen with LivingBrain, recent
  memory, connected topic, next-best action, and the composer visible together.
- Replaced dashboard-style Brain growth metrics with narrative, product-facing
  copy and focused primary actions.
- Tightened mobile and narrow viewport behavior so the Brain and composer remain
  visible without horizontal overflow.
- Refreshed release screenshots, walkthrough GIF/WebM, static app assets, and
  exact 8.1.0 artifact metadata.
- Synchronized Python, npm, VS Code extension, Tauri, workspace, readiness, and
  current-release documentation versions to 8.1.0.

## [8.0.0] - 2026-06-24

### Changed
- Added `lattice-architecture-contract/v1` to make the AgentRuntime,
  ToolRegistry, Config, server decomposition, and Knowledge Graph stabilization
  boundaries explicit and testable for the major architecture line.
- Added `tool-registry-contract/v1` to the ToolRegistry manifest, including
  dispatch, policy, and permission ownership.
- Updated architecture and product readiness targets to 8.0.0.
- Synchronized package/runtime/static/Tauri metadata to 8.0.0.
- Updated current-release docs and exact artifact names to 8.0.0 while
  setting 8.0.0 as the oldest retained release-history entry.

### Fixed
- Made logical Knowledge Graph `replace` imports transactional so malformed
  imports roll back without clearing the existing graph.
- Locked v2 read-equivalence coverage for `list_documents`, `get_node`,
  `relationship_search`, and `traverse`.
- Preserved colliding legacy edge labels during logical import/backfill while
  keeping native write-door synonym dedupe canonical.
