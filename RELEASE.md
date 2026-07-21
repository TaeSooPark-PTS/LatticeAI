# Lattice AI Release Guide

이 문서는 `npm`, `PyPI`, `VS Code`, `Cursor`, `Antigravity`, `Open VSX` 배포를
한 번에 처리하기 위한 체크리스트입니다.

> **현재 `.github/workflows/release.yml`은 태그 push에서 빌드와 검증만 수행합니다.**
> PyPI / npm / VS Code Marketplace / Open VSX 배포는 아래 수동 절차로만
> 진행합니다. 태그 생성은 패키지 스토어 publish를 자동으로 트리거하지 않습니다.

> **릴리스 증거물 보존 정책 (`output/release/`)**: 버전별 스크린샷/영상 증거는
> 최신 3개 버전만 보관합니다. `npm run release:evidence`가 캡처 후 자동으로
> `scripts/prune_release_evidence.mjs`를 실행해 오래된 버전을 정리하며
> (`LTCAI_RELEASE_EVIDENCE_KEEP`으로 조정), 과거 증거는 언제든 해당 태그를
> 체크아웃해 재생성할 수 있습니다.

## v9.9.2 — Artifact Trust (2026-07-21)

9.9.2 unifies every file write behind the model-agnostic validation pipeline
(`sanitize_write_content` — the agent JSON loop now shares the direct chat
path's extract→validate→repair guarantee), makes the chat surface
artifact-first (`artifacts[]` contract, auto-suffix instead of silent
overwrite, optional Brain indexing of generated files), enforces the minimal
plan schema with a deterministic file-step fallback, filters trivial agent
learnings before they enter the Brain, and renders honesty in the UI:
"Auto-repaired" badges on file cards and an unmistakable warning strip for
`NEEDS_REVIEW`/`FAILED` runs. The FG-01..FG-08 scenario matrix from the
2026-07-21 review is pinned as a permanent unit-test harness.

- 상세: [RELEASE_NOTES_v9.9.2.md](RELEASE_NOTES_v9.9.2.md)

## v9.9.1 — Clean Foundations (2026-07-21)

9.9.1 removes the legacy root-shim layer (12 of 13 tracked shims deleted;
`server.py` alone remains for `uvicorn server:app`), adds a legacy debt gate
to `npm run lint`, and polishes the product surface: a "First 5 minutes"
guided card and the daily briefing on the empty Brain home, proactive Cmd+K
quick actions, a Review Center with human-language labels/framed diffs, and a
localized error pipeline so failures speak plain ko/en. The test suite is
scenario-named (37 files renamed), `output/release/` keeps only the newest
three evidence sets via an automated retention policy, and the current-release
docs gate now also verifies the ARCHITECTURE artifact map.

- 상세: [RELEASE_NOTES_v9.9.1.md](RELEASE_NOTES_v9.9.1.md)

## v9.9.0 — Fail-Closed Trust (2026-07-21)

9.9.0 makes the "trustworthy autonomy / honest knowledge" promises enforceable
by fixing two P0 trust defects, governing every mutating tool, and making
onboarding honest about unverifiable hardware — plus supply-chain, benchmark,
and doc-integrity groundwork.

### Change proposals can't overwrite your edits (P0)

- Proposals record `base_sha256` + `base_exists`; approval re-hashes the disk
  and rejects a changed/deleted/created target with a **409 conflict** instead
  of overwriting newer content. Writes are atomic (`os.replace`); duplicate or
  concurrent approvals apply exactly once (replay → 409).

### A confused verifier never reports success (P0)

- Unparseable critic output no longer fabricates PASS/DONE: one strict repair
  retry, then terminate as the new `NEEDS_REVIEW` state. `DONE` requires a
  valid PASS **and** execution evidence; the loose `next_state == DONE` success
  path is removed.

### Every mutating tool is governed (P1)

- `MUTATING_TOOL_INVENTORY` classifies all side-effecting tools; a CI gate
  fails closed on any ungoverned new mutator. Existing-content overwrites that
  can't be staged as a proposal (`create_docx/xlsx/pptx/pdf`, `local_write`)
  are blocked (409) at dispatch; new-file creation is unaffected.

### Honest onboarding (P1)

- Device analysis is `loading | ready | unavailable`; a failed probe shows the
  cause + retry + "continue without a model" instead of a fabricated
  `supported: true` card.

### Leaner, audited, honest (P2)

- Initial JS bundle −22% (180.3 → 141.6 KiB gzip) with a CI budget gate.
- `dependency-audit.yml` (pip-audit + npm audit + CycloneDX SBOM) and
  `postgres-integration.yml` (scheduled pgvector) workflows; all actions
  SHA-pinned. `docs/SECURITY_AUDIT.md`, `docs/BENCHMARKS.md` +
  `scripts/bench_models.py`, `docs/USABILITY_AUDIT.md`, and doc status/link
  classification. Eval separates correct-completion from safe-termination.

### Verification

- 1287 unit / 39 frontend / integration 3 passed (11 skipped, live PG on
  schedule) green; agent eval 20/20 @ 1.0; ruff, tsc, frontend lint, i18n,
  bundle budget, OpenAPI drift, current-release + doc-status gates pass;
  pip-audit + npm audit 0 vulns.

### Honest limitations

- External pentest and real user interviews were out of autonomous scope
  (substituted by a static security scan and heuristic usability audit);
  live PostgreSQL and per-model long benchmarks run via scheduled CI /
  harness rather than locally.

### Artifacts (exact filenames)

- `dist/ltcai-9.9.0-py3-none-any.whl`
- `dist/ltcai-9.9.0.tar.gz`
- `ltcai-9.9.0.tgz`
- `dist/ltcai-9.9.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_9.9.0_aarch64.dmg`

## v9.8.0 — Honest Knowledge Pipeline (2026-07-20)

9.8.0 makes the file→folder→web→graph→RAG→automation pipeline honest and
robust end to end: extraction-quality scoring on every ingest, resumable
background folder jobs with a jobs API and UI progress, chat answers that
disclose limited graph context, visible vector-index freshness, a 16-scenario
agent evaluation gate, and confidence-scored automation suggestions.

### Ingestion quality & robustness

- Every ingest result carries `extraction_quality` (score/level/reasons from
  pure heuristics; upstream confidence takes precedence) plus `warnings` on
  low-quality captures; the proactive quality gate runs observe-only and
  records `quality_gate` verdicts without changing behavior.
- Background jobs track progress (`total`/`processed`/`failed`/errors),
  survive per-item failures, and resume from remaining items.
- New endpoints: `GET /api/ingestion/jobs`, `GET /api/ingestion/jobs/{id}`,
  `POST /api/ingestion/jobs/{id}/resume`, `POST /api/ingestion/folder`
  (approval-gated local-disk access, `background: true` → `job_id`).

### Honest RAG & freshness signals

- Chat answers compute `context_quality` (mode/nodes/limited/reason) —
  top-level in non-stream responses and in the final SSE trailer; the UI
  shows a small note when graph context is limited.
- `GET /api/brain/vector-freshness` reports ready/pending/unavailable with
  pending counts and never raises; the Brain views show a pending-indexing
  chip refreshed after ingests.

### Evaluation & automation quality

- `scripts/agent_eval.py` grew 12 → 16 deterministic scenarios (ingestion
  chain, concept extraction, RAG-grounded answer with a gate-proving negative
  test, automation proposal-first) with grounding assertions against canned
  tool fixtures.
- Automation suggestions gain deterministic `confidence`,
  `confidence_factors` (including KG grounding), duplicate suppression,
  installed-recipe detection, and a low-quality floor; responses report a
  `quality` block.

### Product & docs

- README rebuilt media-first (hero GIF + screenshot grid, ~60% less prose).
- Frontend: freshness chip, low-extraction warnings, context-quality note in
  the assistant bubble, jobs progress panel with resume — all ko/en i18n.

### Verification

- 1263 unit / 27 frontend tests green; ruff, tsc, frontend lint, i18n
  parity/literal, OpenAPI drift, and current-release docs gates pass;
  agent eval 16/16 at success rate 1.0.

### Artifacts (exact filenames)

- `dist/ltcai-9.8.0-py3-none-any.whl`
- `dist/ltcai-9.8.0.tar.gz`
- `ltcai-9.8.0.tgz`
- `dist/ltcai-9.8.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_9.8.0_aarch64.dmg`

## v9.7.0 — Proactive Hybrid Brain (2026-07-20)

9.7.0 deepens the Brain along three tracks: unified hybrid retrieval that
keeps itself indexed, proactive graph-layer quality intelligence, and a
change-governance loop that is now closed end-to-end in the Review Center.

### Unified hybrid retrieval + self-syncing vector index

- `KnowledgeGraphStore.hybrid_search()` — one graph-layer entrypoint fusing
  lexical `search()` and `vector_search()`: scores normalized to [0,1],
  alpha-weighted fusion, chunk hits rolled up to parent nodes, per-source
  `scores`/`fusion` provenance on every match, workspace-scoped throughout.
  Falls back to `mode: "lexical_only"` (with detail) when the vector side is
  unavailable. `context_for_query(use_hybrid=True)` opts the context builder
  into it; the default path is byte-identical to 9.6.0.
- `index_node_incremental(node_id)` + automatic post-ingest sync in
  `IngestionPipeline` (`auto_vector_index=True`, env
  `LATTICEAI_AUTO_VECTOR_INDEX`): each successful non-duplicate ingest
  indexes just the new node's chunks; vector failures never fail the ingest —
  they downgrade `indexing_status` to `pending` so `rebuild_vector_index`
  backlog discovery picks them up.

### Folder & web ingestion

- `IngestionPipeline.ingest_folder(root, recursive=True, background=False)` —
  directory walk with `.latticeignore` (gitignore-like globs, `dir/` prunes,
  `#` comments), hard skip-list (`.git`, `node_modules`, `__pycache__`, venvs,
  build dirs), hidden-by-default, size/extension filters, capped error
  reporting, and optional scheduling on the background ingestion queue.
- `ingest_web_page(url, extracted_text)` — formalizes the web seam: fetching,
  cleaning, and layout parsing belong upstream (browser extension / tools);
  the graph layer receives extracted text and owns structuring + concepts.
  The module docstring now states this parsing-depth contract explicitly.

### Proactive Brain in the graph layer

- New `lattice_brain/graph/proactive.py` (`ProactiveBrain`): duplicate
  detection (content-hash exact + token-signature near-duplicates,
  sub-quadratic sampling), contradiction detection (negation and temporal,
  reusing `lattice_brain/quality.py`), a combined JSON-safe
  `quality_report()` (duplicates + contradictions + stale nodes + edge
  quality), and `consolidate_duplicates()` merge planning — proposal-first,
  plan-only until the store grows a safe merge primitive (auto-detected).
- New read endpoints `GET /api/brain/duplicates` and
  `GET /api/brain/quality-report`; `/api/brain/contradictions` and
  `/api/brain/consolidate` gain graph-layer results additively.
- `gate_ingest_candidate()` — a pure quality-gating seam (ingest /
  skip_duplicate / review) ready for ingestion-time adoption.

### Closed change-governance loop

- Review Center approval of a `change_proposal` item now delegates to
  `ChangeProposalService.approve_and_apply` — the staged content actually
  lands on disk through the single application path (409 on replay); before
  9.7.0 the review-queue approve only flipped the status.
- Proposals carry full provenance: tool, risk, change class, originating
  conversation id; reject accepts a reason (recorded); new counts endpoints
  (`GET /api/proposals/counts`, `GET /automation/reviews/counts`) badge the
  review inbox; a proposal detail endpoint serves diff + staged content.
- Frontend: `change_proposal` source filter, unified-diff preview, tier and
  deletion badges, reject-with-reason input, pending-count badge — ko/en
  i18n parity maintained.

### Agent-loop evaluation & runtime consistency

- `scripts/agent_eval.py` gate: 8 → 12 scenarios, adding file-generation
  happy path, file-generation failure recovery, a 3-step multi-step workflow
  chain with exact ordered tool-call assertions, and a governed-write
  proposal path pinning the approve()-excludes-governed-tools invariant.
- `SingleAgentRuntime.execute` (206 lines) decomposed into six focused
  helpers with zero behavior change; the multi-agent orchestrator now
  surfaces the real failure reason in `execution_failed` timeline events; new
  `test_runtime_consistency.py` pins contract-envelope, status-vocabulary,
  and fail-closed parity between the single- and multi-agent runtimes.

### Structure, performance & housekeeping

- All 10 root legacy modules (`knowledge_graph.py`, `kg_schema.py`,
  `llm_router.py`, `mcp_registry.py`, …) now emit `DeprecationWarning`
  naming their package replacement; the legacy-compatibility registry tracks
  all 13 shims.
- `scripts/profile_kg.py` — offline synthetic KG profiler (p50/p95 latency +
  tracemalloc peaks for ingest/search/context/traverse/vector phases);
  measured baseline recorded in `docs/PERFORMANCE.md`, which also names
  brute-force `vector_search()` as the first optimization candidate at scale.

### Verification

- 1201 unit / 13 integration / 19 frontend tests green; agent-loop-eval
  12/12; brain-quality-eval, readiness, docs, i18n-literal, openapi-drift,
  ruff, and frontend lint gates all pass.

## v9.6.0 — Trusted Agent Loop (2026-07-20)

9.6.0 engineers trust into autonomous work along four tracks: loop
observability, weak-model robustness with a real evaluation harness,
proposal-first change governance, and structural housekeeping.

### Agent loop observability (`loop` payload)

- `latticeai/core/agent_trace.py` — `LoopTrace` records typed events for
  every run: llm calls, parse errors (recovered or not), named format
  repairs, corrections, tool outcomes (ok / error / blocked / proposed),
  retries, approval and verdict decisions, rollback results.
- The agent API returns `loop` (trace summary) with both the
  waiting-approval and final responses.

### Weak-model robustness + evaluation harness

- `extract_action_details` adds python-literal repair (single quotes,
  True/False/None via `ast.literal_eval`) and reports every tolerance used
  by name; a second formatting slip escalates the correction with the exact
  valid tool list.
- `scripts/agent_eval.py` (new CI gate) drives the real SingleAgentRuntime
  through 8 deterministic scripted scenarios — happy path, weak-model format
  gauntlet, prose-slip recovery, correction escalation, destructive block,
  loop detection, critic retry, unrecoverable garbage — and fails the build
  unless all pass.

### Proposal-first change governance (`/api/proposals`)

- `latticeai/core/tool_governor.py` centrally classifies every governed
  call: read / additive / mutation / destructive.
- Additive creates (new files) now run with minimal friction in the agent
  loop; mutations and deletions of existing files are staged as review
  proposals (review-queue source `change_proposal`) with a unified diff,
  exact staged content, and a small/large tier. Approve applies exactly what
  was reviewed; reject discards; nothing touches disk while pending.
- The Brain home gains the "변경 제안 / Change proposals" panel with diff
  previews and one-click approve/reject; proposals also appear in the Act
  review center.

### Structure & process

- Ruff per-file lint ignores trimmed from 9 entries to 3 (all dead ignores
  removed; the one remaining legacy monolith is scoped to `E702` only).
- AGENTS.md carries a machine-checked current-release marker and agent-loop
  invariants, enforced by `scripts/check_current_release_docs.mjs`.

### Verification

- New tests: `test_agent_trace.py` (11), `test_agent_eval.py` (4),
  `test_change_proposals.py` (15), `PendingProposalsPanel.test.tsx` (2).
- Full sweep: 1127 unit / 13 integration / 19 frontend / 18 visual tests,
  agent-loop-eval + brain-quality + readiness + docs gates green, live-boot
  smoke on `/api/proposals`.

## v9.5.0 — Command Center (2026-07-20)

9.5.0 puts the whole Brain one keystroke away. A new read-only, deterministic
Command Center surface condenses every product area into two endpoints, and
the app gains a Cmd+K command palette plus a Today's Briefing panel.

### Command Center (`/api/command/*`)

- `GET /api/command/briefing` — one payload answering "what does my Brain see
  today?": recent knowledge from the scoped graph, conversation activity,
  automation enabled/draft counts, pending review items, a Brain-health
  snapshot, top automation suggestions, and state-derived quick actions with
  stable ids. Each section degrades independently when a backend is
  unavailable.
- `GET /api/command/search?q=…` — universal search grouping results across
  knowledge nodes (scoped keyword search), the user's own conversations
  (deduped per conversation, newest first), and installed automations. All
  reads are scoped to the requesting user and workspace.

### Command Palette + Today's Briefing (frontend)

- Cmd+K (or Ctrl+K) opens a command palette with grouped results
  (지식/지난 대화/자동화/화면 이동), keyboard navigation, and one-press page
  jumps; typing queries the universal search with debounce.
- The Brain home gains a collapsible "오늘의 브리핑 / Today's briefing" panel:
  stat chips (questions, automations on, awaiting review, Brain health),
  recently added knowledge, waiting automation suggestions, and one-click
  quick actions derived from actual product state.
- Fully ko/en localized; no model calls, no writes, no external actions.

### Verification

- New `tests/unit/test_command_center.py` (11 tests) and
  `CommandPalette.test.tsx` (3 component tests) cover section independence,
  scoped reads, quick-action derivation, search grouping/dedupe/scoping, and
  palette keyboard interaction.
- Full sweep: 1097 unit / 13 integration / 17 frontend / 18 visual tests,
  lint + typecheck + docs + readiness gates green, live-boot smoke on both
  new endpoints.

## v9.4.0 — Question-Driven Everyday Automation (2026-07-20)

9.4.0 makes automating daily life effortless. The Brain now watches what the
user actually does — the questions they keep asking and the knowledge folders
they keep feeding — and proposes concrete automations with the user's own
words as evidence.

### Automation Intelligence (`/api/automation/*`)

- `GET /api/automation/patterns` — deterministic local mining of recurring
  question intents from the user's chat history (token-signature clustering,
  Korean+English aware, no model call). Each pattern carries its literal
  example questions, count, and last-asked time.
- `GET /api/automation/suggestions` — recurring patterns become one-click
  suggestions: digest/status/follow-up intents map to the matching starter
  recipe, any other repeated question becomes a "scheduled answer"
  automation, and connected knowledge folders with indexed files become
  folder-digest suggestions triggered when new knowledge arrives.
- `POST /api/automation/install` — idempotent, consent-first install: each
  accepted suggestion is created as a disabled draft workflow (trigger →
  draft agent → review output) with review-queue gating, local-only /
  no-external-actions flags, and provenance metadata (`suggestion_id`), via
  the same validated WorkspaceOS workflow path as the starter recipes.
- `GET /api/automation/overview` — one payload for the automation surface:
  suggestions, installed automations with enable state, and consent
  contract.

### Intuitive automation surface

- The Act page's recipes tab now opens with "Automation suggestions for
  you": evidence chips ("you asked this 7 times", "a folder with 42 files in
  your Brain"), cadence labels, and a one-click Create button that produces
  a reviewable draft. Fully ko/en localized; visible in basic mode.

### Scope and safety

- History mining is scoped: `user_email` + workspace boundaries flow into
  the conversation-store query; legacy-global rows are excluded for scoped
  reads.
- Suggestion ids are deterministic, so re-requesting suggestions or double-
  clicking install never duplicates workflows.
- Nothing runs on its own: accepted suggestions stay disabled until the user
  explicitly enables them, and enabled runs still land in the review queue.

### Verification

- New `tests/unit/test_automation_intelligence.py` (10 tests): clustering,
  intent mapping, scoping, stable ids, install marking, consent-first
  definitions, overview, and no-backend degradation.
- Full sweep: 1086 unit, 13 integration, 14 frontend vitest, 18 playwright
  visual tests passing; lint/typecheck/docs/readiness gates green; live-boot
  smoke on all four new endpoints.

The exact 9.4.0 release artifacts are:

- `dist/ltcai-9.4.0-py3-none-any.whl`
- `dist/ltcai-9.4.0.tar.gz`
- `ltcai-9.4.0.tgz`
- `dist/ltcai-9.4.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_9.4.0_aarch64.dmg`

## v9.3.0 — Proactive Brain Intelligence (2026-07-20)

9.3.0 turns the Brain from a passive store into an active steward of its own
knowledge. The previously dormant `lattice_brain.quality` layer (dedupe,
merge, conflict/temporal-contradiction detection, edge quality) is wired into
router-facing capabilities, and the core recall path gains semantic evidence.

### Brain Intelligence (`/api/brain/*`)

- `GET /api/brain/health` — scored diagnosis across freshness (stale-node
  ratio), connectivity (orphan-node ratio), embedding coverage (vector-index
  scale), and consistency (duplicate/contradiction pressure), with an overall
  grade and recommended care actions. Every number is read from live stores;
  missing stores degrade the dimension to `unavailable`.
- `GET /api/brain/insights` — proactive digest: recent knowledge growth,
  trending node types, stale knowledge, disconnected (orphan) nodes, and
  suggested questions grounded in real node titles.
- `GET /api/brain/contradictions` — negation/preference conflicts and
  temporal contradictions across workspace memories, plus explicit
  CONTRADICTS edges from the graph, each with evidence snippets.
- `POST /api/brain/consolidate` — duplicate-memory and duplicate-edge
  detection. Dry-run by default; `apply=true` prunes only exact duplicate
  workspace memories through the audited MemoryService path and never mutates
  graph content.

### Hybrid recall

- `POST /api/memory/recall` blends vector similarity into the lexical
  ranking (`hybrid-evidence/v2` quality gate). Semantic hits surface
  knowledge phrased differently from the query; vector matches are
  workspace-scoped through `filter_scoped_nodes` before they can influence
  results; each row reports its `evidence_kinds` (lexical/semantic); and any
  vector-tier failure degrades recall honestly back to `lexical-evidence/v1`.

### Brain surface

- New "Brain intelligence check" panel beside Brain care: plain-language
  health grades, per-dimension scores, activity/attention chips,
  recommended care actions, and duplicate-cleanup preview/apply. Fully ko/en
  localized.

### Verification

- `tests/unit/test_brain_intelligence.py` (14 tests) covers health scoring,
  scoped graph reads, insights, contradiction pairs, consent-first
  consolidation, and hybrid recall (blend, merge, scoping, degradation).
- Full sweep on this release: 1076 unit, 13 integration, 14 frontend vitest,
  18 playwright visual tests passing; lint/typecheck/brain-quality-eval/
  product-readiness gates green; all four new endpoints exercised against a
  live-boot app.

The exact 9.3.0 release artifacts are:

- `dist/ltcai-9.3.0-py3-none-any.whl`
- `dist/ltcai-9.3.0.tar.gz`
- `ltcai-9.3.0.tgz`
- `dist/ltcai-9.3.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_9.3.0_aarch64.dmg`

## v9.2.0 — Model-Agnostic File Generation (2026-07-20)

9.2.0 makes "create a file" work reliably with any loaded LLM, including small
local models (gemma/qwen class) that previously produced broken HTML or chat
wrappers instead of file content.

### File generation pipeline

- New `latticeai/core/file_generation.py` module treats every model reply as
  untrusted content: extension-aware strict prompting (the prompt pins the
  exact first line, e.g. `<!DOCTYPE html>`), extraction of the real payload
  from Markdown fences, `<think>`/reasoning blocks, and conversational
  framing, per-type structural validation (complete HTML documents, parseable
  JSON, CSS rule blocks, fence-free code), one corrective retry that feeds the
  rejection reason back to the model, and a deterministic repair fallback
  (truncated HTML is closed, fragments are wrapped in a valid scaffold,
  invalid JSON is recovered or re-encoded) so the user always receives a
  structurally valid file.
- Chat file requests that name a type but no filename ("html 파일 만들어줘",
  "웹페이지 만들어줘") now resolve to an inferred target and run on the
  deterministic direct-write path instead of the model-driven agent JSON
  loop. File-generation temperature is clamped and the token budget raised so
  documents complete.
- The `/chat` direct-write response reports `generation` metadata (attempts,
  validation reasons, whether deterministic repair ran), and the confirmation
  message discloses when auto-repair produced the saved file.

### Agent loop hardening

- `extract_action` strips `<think>` blocks before locating the action JSON and
  tolerates trailing commas.
- The executor no longer aborts the run on the first malformed action reply:
  up to two corrective format reminders are fed back through the corrections
  channel before halting.
- The executor prompt pins exact `write_file` content rules (complete raw
  content, no fences, extension-valid documents).

### Tests

- `tests/unit/test_file_generation.py` covers extraction, validation, repair,
  filename inference, prompt anchoring, and the retry/repair orchestration
  (22 tests). Full unit suite: 1062 passing.

The exact 9.2.0 release artifacts are:

- `dist/ltcai-9.2.0-py3-none-any.whl`
- `dist/ltcai-9.2.0.tar.gz`
- `ltcai-9.2.0.tgz`
- `dist/ltcai-9.2.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_9.2.0_aarch64.dmg`

## v9.1.0 — Code Review Completion & Fail-Closed Runtime (2026-07-11)

9.1.0 completes every actionable item in
`docs/reviews/CODE_REVIEW_2026-07-11.md`. The release makes network, workspace,
invitation, and tool boundaries fail closed; replaces ambient runtime and model
state with typed ownership; decomposes the chat and frontend hotspots; makes
service failures visible and testable; and removes tracked release/review
clutter without rewriting historical release records.

### Security and access control

- Telegram messages and callback queries are denied unless their chat ID is in
  the required `LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS` allowlist. Allowed chats
  are registered only after authorization, and the bridge authenticates to the
  local server with the required `LATTICEAI_SERVER_SESSION_TOKEN`.
- Invitation authorization uses a signed, expiring server-bound value instead
  of trusting `authorized=true`; the built-in invitation code is removed and
  an enabled public invitation gate uses either an explicitly configured random
  code or a generated per-install secret persisted with private permissions.
  New SSO accounts must carry the same verified invite authorization, bound to
  the server-side one-time OIDC state, nonce, and PKCE transaction.
- Knowledge Graph scope lookup and unknown v2 nodes fail closed. Legacy-global
  reads require an explicit compatibility opt-in and have regression coverage
  for projection failures and cross-workspace isolation.
- Computer screenshot/status, knowledge and Obsidian tools, and chat network
  status now pass explicit capability, consent, user, workspace, or policy
  gates instead of relying on permissive auto-approval.
- Permission notifications disclose only token hints and can link to the
  optional `LATTICEAI_PERMISSION_UI_URL`; queue persistence is atomic and
  private. Non-loopback cookies are secure, reconnaissance endpoints redact or
  require authentication, and MCP paths are masked.

### Runtime and maintainability

- App assembly is expressed as typed config, security, Brain, model, and router
  stages. The legacy `server_app` surface remains an explicit compatibility
  allowlist and no longer depends on exporting `locals()`.
- Model selection and loading use injected typed state rather than dual-synced
  module globals, with API error translation kept at the HTTP boundary.
- Chat contracts, history, documents, and streaming live in focused modules;
  the route layer delegates to services instead of owning every chat concern,
  and agent/Computer Use records keep authenticated user/workspace ownership.
- Shallow runtime pass-through modules and repeated timestamp/status utilities
  are consolidated. Root setup and local-knowledge modules are compatibility
  shims over package-owned implementations.
- AgentRuntime naming is explicit, high-cost broad exception paths log or fail
  closed, and readiness gates check forbidden architectural patterns as well as
  symbol presence.

### Frontend reliability and repository hygiene

- Failed API results render unavailable/error states rather than healthy empty
  Brain data. Proof attachment, continuity checks, and action callbacks report
  success only after an `ok` response, with a core-service unavailable banner
  for critical queries.
- Brain logic is split into focused hooks, translations into namespaces, and
  experience styling into surface files. User-facing strings use i18n and the
  version is injected from package metadata.
- Vitest coverage now protects API empty shapes, proof parsing, conversation
  sessions, primitives, and i18n; visual coverage asserts that failed services
  do not become quiet-success UI.
- Obsolete local VSIX files are removed, ignored build/audit/workspace trees stay
  outside release archives, Electron is documented as an experimental
  compatibility shell, and review documents are archived under `docs/reviews/`.

The exact 9.1.0 release artifacts are:

- `dist/ltcai-9.1.0-py3-none-any.whl`
- `dist/ltcai-9.1.0.tar.gz`
- `dist/ltcai-9.1.0.vsix`
- `ltcai-9.1.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_9.1.0_aarch64.dmg`

The following product and isolation work is also included in 9.1.0:

- Reframed Brain Home around the product's actual knowledge lifecycle: chat,
  files, folders, notes, and web pages visibly enter the Living Brain, then
  resolve into a lightweight graph built only from real Knowledge Graph nodes
  and edges.
- Added truthful ingestion emergence evidence, native desktop folder selection,
  persistent conversation-to-knowledge traces, grounded next actions, and
  desktop/mobile layouts that keep the Brain—not a dashboard—the protagonist.
- Rebuilt the empty Brain home as a one-viewport organism: source capture,
  composer, real graph, life signal, and the primary memory-grounded action stay
  visible without page scrolling, while history and deeper proof open as
  overlays. Continuous breathing, heartbeat, sparks, and Brain-to-graph pulses
  now accelerate from real listening, recall, synthesis, and action state.
- Preserved access to every memory-grounded action in the compact command deck;
  the first action stays one click away and the complete reviewed set opens in a
  focused popover instead of extending the page.
- Brain automation recipes are now created as reviewable disabled drafts and
  require an explicit enable action. Triggered and review-queue runs execute a
  real agent pipeline grounded by scoped MemoryService recall, with researcher,
  planner, executor, and reviewer roles.
- Normalized ingestion hook names and provenance so chat, upload, local-folder,
  note, web, and legacy sources can drive workspace-scoped recipes without
  treating failed ingestion as knowledge.
- Local-folder hierarchy, file, chunk, concept, and semantic nodes now carry
  workspace-scoped identities. Automation events validate the same write scope,
  persist it through watcher restarts, and stay isolated by workspace and owner;
  legacy personal-folder nodes reproject in place without destructive ID rewrites.
- Enabling a reviewed recipe preserves the user's edited prompt, roles, name,
  and nodes; empty web captures remain visibly unsuccessful.
- Replaced the floating hamburger/drawer shell with visible desktop task
  navigation and a mobile bottom bar for Chat, Sources, Memory, and Work; model,
  settings, workspace, and admin utilities remain available in an accessible
  secondary menu.
- Rebuilt Brain Home around a large composer, contextual starters, recent
  conversations, visible source capture, the living knowledge flow, and grounded
  automation. Deeper memory rings and runtime proof still use progressive
  disclosure.
- Memory opens on search instead of the graph, basic Work opens on a single goal
  composer instead of runtime metrics, and basic Sources uses a one-column add
  flow with technical pipeline controls hidden.
- Added keyboard focus trapping/restoration for the secondary menu, semantic
  tab roles and arrow-key navigation, skip navigation, 44-pixel mobile targets,
  reduced-motion handling, and desktop/mobile visual regression coverage.
- Consolidated the new shell, conversation, and content grammar in a dedicated
  `experience.css` layer while keeping feature-specific legacy visualization
  styles compatible.

- Model generation now snapshots the requested model per request, so concurrent
  chat, streaming, and document jobs cannot switch each other's process-wide
  model state.
- Chat, upload, browser capture, graph ingestion, Brain Network, portability,
  MCP, realtime presence, shared registries, hooks, model lifecycle, and
  permission decisions now enforce authenticated identity, active workspace
  scope, or administrator ownership as appropriate.
- Knowledge Graph IDs for new workspace-scoped messages, documents, people,
  concepts, structured document children, and events include workspace identity;
  legacy unscoped IDs remain readable and are not destructively migrated.
- Web URL capture now rejects private/reserved DNS targets and rebinding,
  revalidates redirects, disables environment proxies, and enforces a streamed
  4 MiB response limit.
- Integration/OpenAPI generation runs in disposable state, committed OpenAPI
  artifacts are drift-gated, release archives reject personal bridge files, and
  the browser extension is aligned to version 9.1.0 and port 4825.
- The misleading client-only global egress toggle was removed. External actions
  continue to use their real feature-specific consent/configuration paths.
- MCP/plugin dispatch no longer bypasses local-file approval, and document RAG,
  answer traces, garden fallback, and realtime unscoped events fail closed at
  authenticated workspace boundaries.

## v9.0.0 — Code Review Closure & Runtime Cleanup (2026-07-08)

9.0.0 packages the July 8 code-review follow-up work and the remaining cleanup
risk reduction. The release keeps 8.9.0's scoped memory and ToolRegistry
hardening, then fixes functional reliability issues, consolidates duplicated
runtime/setup/frontend helpers, makes runtime audit append paths scale better,
and decomposes the main chat router epilogues so future chat behavior changes
have a smaller blast radius.

### Added
- Added regression coverage for no-model file generation, chat intent routing,
  permission-token cleanup, setup detection helpers, runtime audit JSONL appends,
  and shared chat fast-path epilogues.
- Added `latticeai.core.io_utils`, `latticeai.services.setup_detection`, and
  `lattice_brain.utils` as shared homes for duplicated JSON, timestamp, hash,
  and setup-probe helpers.

### Changed
- Runtime audit events now append to JSONL while preserving legacy JSON audit
  reads, avoiding full-file rewrites on every append.
- The legacy `server_app` runtime namespace now exports from an explicit
  allowlist instead of exposing every non-underscore local from app assembly.
- Chat fast paths now share history, notification, no-model, single-answer, and
  agent-payload epilogues instead of duplicating them in the main `/chat`
  handler.
- Setup wizard and zero-config setup share Windows GPU parsing, CUDA detection,
  WSL detection, and tool detection helpers.
- Static CSS and React SPA token ownership are documented as separate token
  sources with different consumption formats.
- README, release docs, readiness gates, package metadata, Tauri metadata, and
  VS Code extension metadata are synchronized to 9.0.0.

### Fixed
- File-generation requests now fail cleanly when no model is loaded instead of
  creating empty files and reporting success.
- Streaming chat/document generation now preserves terminal SSE events and
  history/trace persistence on mid-stream failures.
- Agent run executor exceptions now persist `failed` run status instead of
  leaving runs permanently `running`.
- Brain delegation now treats failed HTTP responses as failed UI activity.
- Local permission approval cleanup no longer corrupts the active token lookup
  when expired approvals are removed.
- Chat network-status and current-URL intent detection no longer overmatches
  generic IP/address questions.
- Telegram bot server URL configuration now honors environment overrides and
  avoids replaying hashed session keys as bearer cookies.
- Brain UI version copy, local embedding dimensions, and LATTICE_TZ-aware audit
  timestamps are aligned with the current runtime configuration.

Expected artifacts (exact 9.0.0 names only):
- `dist/ltcai-9.0.0-py3-none-any.whl`
- `dist/ltcai-9.0.0.tar.gz`
- `dist/ltcai-9.0.0.vsix`
- `ltcai-9.0.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_9.0.0_aarch64.dmg`

## v8.9.0 — Scoped Memory & Tool Policy Hardening (2026-07-06)

8.9.0 closes the actionable findings from `docs/reviews/CODE_REVIEW_2026-07-06.md`
except the explicitly excluded Computer Use direct API risk. The release
hardens authenticated history/KG scoping, direct Tool API policy gates,
AgentRuntime human-approval behavior, permission token storage, and frontend
maintainability seams. Installer/process execution now uses redacted command
plans, confirmation tokens, and local process audit events.

### Added
- Added user/workspace-scoped conversation history reads and deletes for chat
  and direct history tool routes.
- Added workspace scope enforcement inside Knowledge Graph retrieval/search,
  relationship search, traversal, and node reads.
- Added direct HTTP/MCP Tool API policy enforcement before hooks or handlers run.
- Added confirmation-token guarded installer/process command plans with redacted
  process audit events for setup and engine installation paths.
- Added regression coverage for TTL injection, scoped history, tool policy
  blocking, AgentRuntime explicit approval, permission token hashing, blocked
  local write prefixes, and model-download config injection.
- Added frontend API base split, CSS token/base split, and i18n literal
  allowlist budgets across `frontend/src`.

### Changed
- AgentRuntime now blocks non-auto-approved plans unless a real human approval
  path calls `approve(..., approved_by_human=True)`.
- Model download consent now flows through configured runtime state instead of
  reading environment variables directly in the gate.
- AppRuntime uses an explicit legacy namespace adapter for the historical
  module-level compatibility surface.
- README, release docs, readiness gates, package metadata, Tauri metadata, and
  VS Code extension metadata are synchronized to 8.9.0.
- Documentation clarifies that SQLite is the live local Brain store; Postgres
  remains optional scale/migration tooling rather than the default live KG
  implementation.

### Fixed
- Conversation store migrations now add scope columns before creating the
  workspace index, preserving upgrades from older DBs.
- Direct `write_file` and `edit_file` policy lookup now treats blocked system
  prefixes as destructive paths.
- Permission approval queues no longer persist raw approval tokens.
- Clearing the selected workspace now removes the persisted localStorage value.
- Tauri/backend API calls use credential inclusion for cross-origin localhost
  cookie/session behavior.

Expected artifacts (exact 8.9.0 names only):
- `dist/ltcai-8.9.0-py3-none-any.whl`
- `dist/ltcai-8.9.0.tar.gz`
- `dist/ltcai-8.9.0.vsix`
- `ltcai-8.9.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_8.9.0_aarch64.dmg`

## v8.8.0 — Brain Core Extraction & Recall Proof Hardening (2026-07-06)

8.8.0 packages the Brain Core extraction prep and recall-proof hardening work.
Internal-only Brain compatibility layers are physically removed, root-level
compatibility shims remain explicitly managed for external entrypoints, and the
Brain UI/backend path now gives users clearer conversation controls and better
evidence for why memories were recalled.

### Added
- Added Brain Core isolation coverage proving `lattice_brain` does not import
  the product `latticeai` package.
- Added retrieval quality gates for matched recall terms, confidence labels,
  and lexical evidence filtering.
- Added Brain conversation controls for starting, resuming, deleting, stopping,
  regenerating, and copying conversation output.

### Changed
- Removed internal-only flat Brain modules, the deprecated `latticeai.brain`
  namespace, and the `latticeai.services.agent_runtime` alias.
- Updated `legacy_shim_report()` to distinguish remaining shims from
  intentionally removed 8.8.0 layers.
- Hardened AgentRuntime role validation, legacy run contract reads, and
  persisted retry budgets.
- Updated package/runtime/static/Tauri metadata and current-release
  documentation to 8.8.0.

### Fixed
- File ingestion now rejects directory paths at the file-ingest boundary.
- Memory recall filters zero-evidence noise when higher-confidence lexical
  matches exist, and answer proof citations expose matched terms and confidence.

Expected artifacts (exact 8.8.0 names only):
- `dist/ltcai-8.8.0-py3-none-any.whl`
- `dist/ltcai-8.8.0.tar.gz`
- `dist/ltcai-8.8.0.vsix`
- `ltcai-8.8.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_8.8.0_aarch64.dmg`

## v8.7.0 — Runtime State Hygiene & Release Evidence Refresh (2026-07-05)

8.7.0 packages the current main-branch hardening work into an exact release
line. Model runtime implementation paths now use the typed runtime-state object
as their source of truth, legacy module-global synchronization is explicitly
deprecated, and the checked-in release screenshots/GIF/WebM are refreshed from
the current app shell.

### Added
- Added unit coverage proving model-runtime internals read from
  `ModelRuntimeState` while the legacy globals remain a compatibility surface.
- Added 8.7.0 release evidence under `output/release/v8.7.0/`, including
  screenshots, walkthrough GIF/WebM, and the capture index.
- Added `RELEASE_NOTES_v8.7.0.md` and synchronized current-release docs.

### Changed
- Updated package/runtime/static/Tauri metadata to 8.7.0.
- Updated README release evidence links from the old 8.2.0 screenshots to the
  refreshed 8.7.0 captures.
- Updated current-release documentation and exact artifact examples to 8.7.0.

### Fixed
- Reduced internal reliance on bare module globals in
  `latticeai/services/model_runtime.py`; compatibility globals are still
  available for older callers.
- `sync_to_module_globals()` now emits `DeprecationWarning` so future code does
  not build new coupling to the legacy global state path.

Expected artifacts (exact 8.7.0 names only):
- `dist/ltcai-8.7.0-py3-none-any.whl`
- `dist/ltcai-8.7.0.tar.gz`
- `dist/ltcai-8.7.0.vsix`
- `ltcai-8.7.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_8.7.0_aarch64.dmg`

## v8.6.0 — Desktop Capture & Navigation Reliability (2026-07-05)

8.6.0 focuses on the user-facing capture path: folder selection now works from
the desktop app's localhost-hosted Tauri webview, Capture shows actionable
feedback when the picker is unavailable, and the new Brain shell navigation is
covered by updated visual smoke tests.

### Added
- Added Tauri capability coverage for `http://127.0.0.1:*` and
  `http://localhost:*`, preserving desktop IPC for the localhost app shell.
- Added a regression trust gate that verifies the Tauri capability keeps
  localhost desktop IPC enabled.
- Added `RELEASE_NOTES_v8.6.0.md` and synchronized current-release docs.

### Changed
- Updated Capture folder selection to detect both module and global Tauri
  bridges and to show a visible fallback message when the native picker cannot
  open.
- Updated Visual Smoke coverage for the Brain shell sidebar, advanced utility
  drawer, and admin-console entry flow.
- Synchronized package/runtime/static/Tauri metadata and release docs to 8.6.0.

### Fixed
- Fixed the folder-picker path for the Tauri production app after it navigates
  from bundled static content to the local FastAPI `/app` URL.
- Removed negative letter spacing from the updated frontend shell styling.

Expected artifacts (exact 8.6.0 names only):
- `dist/ltcai-8.6.0-py3-none-any.whl`
- `dist/ltcai-8.6.0.tar.gz`
- `dist/ltcai-8.6.0.vsix`
- `ltcai-8.6.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_8.6.0_aarch64.dmg`

## v8.5.0 — Tool Registry Readiness & Config DI (2026-07-01)

Full codebase scan for architectural debt, code smells, and improvement opportunities (per AGENTS.md priorities and rules). Targeted improvements implemented without breaking public behavior or legacy compatibility.

### Added
- Made ToolRegistry fully aligned: added governance + description for `vision_analyze`; diagnostics now report `ready: true` with no handler/governance mismatches.
- Propagated `tz_name` (from central `Config`) into `TriggerService` via `build_automation_runtime` / platform wiring — advances Config centralization and explicit DI for automation layer.

### Changed
- Updated TriggerService, automation_runtime, platform_runtime_wiring and call sites in app_factory to accept and forward `tz_name` for Config-driven construction (env fallback preserved for compatibility).
- Synchronized version metadata and all current-release documentation to 8.5.0.
- Performed full scan: identified large modules, legacy globals, registry drift, and DI gaps; addressed highest-leverage safe changes.

### Fixed
- ToolRegistry drift between handlers, governance, and descriptions (vision_analyze was implemented in tools/ but missing from central registry policy surface).
- Minor: timezone was only read via os.environ inside TriggerService instead of flowing from the Config seam.

Expected artifacts (exact 8.5.0 names only):
- `dist/ltcai-8.5.0-py3-none-any.whl`
- `dist/ltcai-8.5.0.tar.gz`
- `dist/ltcai-8.5.0.vsix`
- `ltcai-8.5.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_8.5.0_aarch64.dmg`

## v8.4.0 — Action-Aware Brain Chat (2026-07-01)

8.4.0 closes the gap between asking the Brain to create a file and seeing a
real artifact appear. Explicit create/write/save/edit file requests from the
Brain Chat route are now routed into the governed workspace file tool instead
of plain model generation, so the same composer can answer questions or perform
workspace file actions.

### Added
- Added a `/chat` file-action intent gate for explicit file creation, editing,
  saving, and artifact requests.
- Added regression coverage proving file creation requests from chat route into
  the workspace file tool and return `created_files`.

### Changed
- Kept normal Q&A on the direct chat generation path while routing only explicit
  side-effect file requests to the existing planner/executor/reviewer agent.
- Synchronized package/runtime/static/Tauri metadata and current-release docs to
  8.4.0.

### Fixed
- Literal file writes with user-provided content no longer require a model to be
  loaded before the workspace file tool runs.
- File target/content parsing no longer swallows surrounding prose into the path
  or treats descriptive words as literal file content.

Expected artifacts (exact 8.4.0 names only):
- dist/ltcai-8.4.0-py3-none-any.whl
- dist/ltcai-8.4.0.tar.gz
- dist/ltcai-8.4.0.vsix
- ltcai-8.4.0.tgz
- src-tauri/target/release/bundle/dmg/Lattice AI_8.4.0_aarch64.dmg

## v8.3.0 — Orchestrated Brain Readiness (2026-07-01)

8.3.0 turns the architecture and product polish backlog into measured release
work. Legacy shims are now inventoried, AgentRuntime/workflow boundaries are
more inspectable, graph ingestion is routed through the unified pipeline, and
the release docs name the onboarding and community/plugin growth paths.

### Added
- Added a managed legacy compatibility inventory with owners, replacements,
  reasons, removal phases, and readiness metrics.
- Added AgentRuntime lifecycle coverage for legacy run records plus explicit
  WorkflowEngine boundary/config inspection.
- Added graph ingestion tests for upload-to-pipeline behavior and
  workspace-safe duplicate content.
- Added 8.3.0 onboarding and community/plugin docs.

### Changed
- Routed `/knowledge-graph/ingest` through `IngestionPipeline` when the
  pipeline is wired, preserving provenance and hook lifecycle behavior.
- Kept duplicate text/web/note content isolated per workspace while retaining
  content-hash duplicate semantics.
- Converted stateful root shims for `mcp_registry.py` and `llm_router.py` into
  physical module aliases.
- Updated upload client handling so HTTP failures surface as honest UI errors.
- Synchronized package/runtime/static/Tauri metadata, readiness targets, and
  current-release docs to 8.3.0.

### Upgrade Notes
- Existing legacy-global text/web/note graph nodes are not rewritten in place.
  Re-ingesting the same content with a workspace id can create a separate
  workspace-scoped node; re-index existing sources after upgrading when you want
  provenance to converge on workspace scopes.

Expected artifacts (exact 8.3.0 names only):
- dist/ltcai-8.3.0-py3-none-any.whl
- dist/ltcai-8.3.0.tar.gz
- dist/ltcai-8.3.0.vsix
- ltcai-8.3.0.tgz
- src-tauri/target/release/bundle/dmg/Lattice AI_8.3.0_aarch64.dmg

## v8.2.0 — Brain Brief (2026-06-27)

8.2.0 adds an evidence-backed Brain Brief to the default Brain Home. Instead of
making the user infer readiness from scattered panels, the home screen now shows
what to notice, which real memory/graph signals support it, and the easiest next
action.

### Added
- Added `MemoryService.brain_brief()` and `/api/memory/brain-brief` so the Brain
  home briefing is generated from real workspace, conversation, graph, vector,
  and source-health data.
- Added a Brain Brief panel to the centered Brain Home with a focus item,
  evidence counters, and direct actions for adding sources, asking, inspecting
  graph links, verifying model-independent proof, and managing backups.
- Added unit coverage for empty Brain guidance, recall-backed Brain Briefs, and
  the API endpoint.

### Changed
- Completed another runtime extraction pass by keeping model loading/server
  engine bodies in `model_loading.py` / `model_engines.py` behind compatibility
  delegations.
- Moved WorkspaceOS graph trace, run, skill, and snapshot comparison ownership
  into focused manager modules while preserving the store facade.
- Synchronized package/runtime/static/Tauri metadata, readiness targets, and
  current-release docs to 8.2.0.

Expected artifacts (exact 8.2.0 names only):
- dist/ltcai-8.2.0-py3-none-any.whl
- dist/ltcai-8.2.0.tar.gz
- dist/ltcai-8.2.0.vsix
- ltcai-8.2.0.tgz
- src-tauri/target/release/bundle/dmg/Lattice AI_8.2.0_aarch64.dmg

## v8.1.0 — Intuitive Brain Home (2026-06-27)

8.1.0 turns the default Brain surface from a dashboard-like status panel into a
product-first conversation entry. The living Brain stays directly above the
composer, while the first screen explains what the Brain remembers, what topic
is connected, and what the user should do next.

### Changed
- Added a focused `BrainFirstScreen` surface that combines LivingBrain, readiness
  status, recent memory, connected topic, and next-best action.
- Removed the dashboard-style four-metric growth strip from the default Brain
  entry and replaced it with narrative, action-oriented copy.
- Kept the primary action visible by moving talk/add-source/view-graph actions
  into the first screen and verifying their routes with Playwright.
- Tightened mobile and 320px layouts so the Brain and composer fit in the first
  viewport without horizontal overflow.
- Refreshed 8.1.0 screenshots, walkthrough GIF/WebM, static app assets, package
  metadata, Tauri metadata, readiness targets, and current-release docs.

Expected artifacts (exact 8.1.0 names only):
- dist/ltcai-8.1.0-py3-none-any.whl
- dist/ltcai-8.1.0.tar.gz
- dist/ltcai-8.1.0.vsix
- ltcai-8.1.0.tgz
- src-tauri/target/release/bundle/dmg/Lattice AI_8.1.0_aarch64.dmg

## v8.0.0 — Runtime Architecture Contract (2026-06-24)

8.0.0 makes the platform architecture release line explicit. AgentRuntime,
ToolRegistry, central Config, server decomposition, and Knowledge Graph
stabilization are now represented as machine-checkable contracts rather than
release-note claims.

### Changed
- Added `lattice-architecture-contract/v1` to `architecture_readiness()`,
  including the preferred refactoring order and concrete owners for runtime,
  registry, config, server, and KG boundaries.
- Added `tool-registry-contract/v1` to the live ToolRegistry manifest so
  dispatch, policy, and permission ownership are visible from one registry
  source of truth.
- Updated product readiness to target 8.0.0 and require the architecture
  contract, exact 8.0.0 artifacts, current docs, and release evidence.
- Made logical Knowledge Graph `replace` imports transactional, so malformed
  imports roll back without clearing the existing graph.
- Locked Knowledge Graph read-equivalence coverage for `list_documents`,
  `get_node`, `relationship_search`, and `traverse` across legacy and v2
  read paths.
- Preserved colliding legacy edge labels during logical import/backfill without
  regressing native write-door canonical edge dedupe.
- Synchronized Python, npm, VS Code extension, Tauri, static asset, marketplace,
  workspace, and multi-agent runtime versions to 8.0.0.
- Refreshed current-release documentation while setting 8.0.0 as the oldest retained
  release-history entry.

Expected artifacts (exact 8.0.0 names only):
- dist/ltcai-8.0.0-py3-none-any.whl
- dist/ltcai-8.0.0.tar.gz
- dist/ltcai-8.0.0.vsix
- ltcai-8.0.0.tgz
- src-tauri/target/release/bundle/dmg/Lattice AI_8.0.0_aarch64.dmg
