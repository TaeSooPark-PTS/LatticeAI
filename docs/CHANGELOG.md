# Changelog

The top entry is either the current unreleased main-branch work or the current
release line. Older entries are historical and may describe behavior as it
existed at that release.

## [7.5.0] - 2026-06-20

> Runtime Debt Burn-down & Release Risk Cleanup. Turns the 7.4.0 contract
> envelope into a consumed API surface, expands retrieval quality to a 250+
> record local corpus fixture, and removes release/security warnings.

### Added
- `extract_contract`, `require_contract`, `contract_view`, and `contract_views`
  helpers for consumers that need a surface-agnostic `agent-run-contract/v1`
  projection.
- AgentRuntime status/list/detail/events and realtime feed responses now expose
  compact `contracts` views alongside legacy payloads.
- Deterministic 250+ record retrieval benchmark corpus while keeping 12 judged
  queries and real `KnowledgeGraphStore` + `SearchService` execution.

### Changed
- Tauri Rust/CLI dependencies are updated within the Tauri 2 line, removing the
  old transitive `block v0.1.6` future-incompatibility warning.
- npm dependency overrides move `js-yaml` to a non-vulnerable version; `npm
  audit` reports 0 vulnerabilities.
- Package/runtime/static metadata is synchronized to 7.5.0; package publish and
  deployment remain owner-run only.

## [7.4.0] - 2026-06-20

> Runtime Contract Convergence & Corpus Retrieval. Completes the
> agent-run-contract/v1 family across run storage, workflow execution, audit
> events, realtime events, and a real corpus-scale retrieval quality gate.

### Added
- Persisted agent and workflow run rows now carry refreshed contract metadata
  for queued, running, terminal, cancelled, and interrupted states.
- Workflow engine results, replay payloads, audit log events, and realtime SSE
  feed events now expose the same `agent-run-contract/v1` family envelope while
  preserving existing top-level compatibility fields.
- Corpus-scale retrieval fixture with 30+ documents, judged queries,
  must-include expectations, and thresholds for recall, precision, NDCG, and
  hit rate.
- `scripts/brain_quality_eval.py` now exercises the real local
  `KnowledgeGraphStore` + `SearchService` hybrid retrieval path before scoring.

### Changed
- `RetrievalBenchmarkRunner` reports dynamic metric aliases for the selected
  `top_k` and a `must_include_hit_rate`.
- Package/runtime/static metadata is synchronized to 7.4.0; package publish and
  deployment remain owner-run only.

## [7.3.0] - 2026-06-20

> Runtime Contract & Retrieval Quality. Turns the next AgentRuntime extraction
> step and the uploaded roadmap's hybrid-search quality goals into a small,
> tested release: shared run contracts and deterministic recall regression.

### Added
- `lattice_brain.runtime.contracts.AgentRunContract`, a serializable
  `agent-run-contract/v1` payload shared by single-agent and multi-agent
  execution paths.
- Multi-agent API result/run patches now include the shared contract with
  runtime, mode, status, roles, retries, timeline, and terminal-state data.
- Single-agent runtime exposes the same contract helper for UI/API/storage
  convergence in the next extraction pass.
- `scripts/brain_quality_eval.py` now runs deterministic hybrid recall/ranking
  regression checks with recall and precision thresholds.

### Changed
- Package/runtime/static metadata is synchronized to 7.3.0; package publish and
  deployment remain owner-run only.

## [7.2.0] - 2026-06-20

> Runtime Trust Baseline. Adds execution preview and registry diagnostics so
> agent runs and tool permissions become inspectable contracts before action.

### Added
- `POST /agents/api/run/preview` for AgentRuntime readiness, role selection,
  input keys, retry clamping, and blocking reasons without starting a run.
- `GET /tools/registry` for the live ToolRegistry manifest across dispatch
  handlers, governance policy, catalog descriptions, and permissions.
- `GET /tools/registry/diagnostics` for a compact drift check suitable for CI,
  admin views, and runtime health panels.
- Unit coverage for AgentRuntime preview and ToolRegistry manifest diagnostics.

### Changed
- Tool governance now covers `read_document`, and the catalog describes
  `create_web_project`, closing the current dispatch/governance/catalog drift.
- Package/runtime/static metadata is synchronized to 7.2.0; package publish and
  deployment remain owner-run only.

## [7.1.0] - 2026-06-20

> Brain Usability Completion. Completes the 7.1.0 first-run through editor-sync
> usability pass: clear onboarding, visible ingestion, graph controls, inline
> proof, workspace/admin discovery, feedback states, and VS Code sync status.

### Added
- Hardware visualization, expected timing, install timeline, and next-action
  copy in first-run onboarding.
- Brain Home ingestion stage disclosure and memory emergence timeline for file,
  folder, note, and URL sources.
- Knowledge Graph search suggestions, entity type filters, recent/all-time time
  exploration, focus clearing, and neighbor highlighting.
- Inline answer citation markers with keyboard-accessible proof cards.
- Workspace/profile switcher, Admin Console gate, consent revoke feedback, and
  shared empty/error feedback surfaces in the Brain shell.
- VS Code extension heartbeat/status endpoint plus extension and main-app sync
  indicators for connected/indexing/synced/offline states.

### Changed
- Package/runtime/static metadata is synchronized to 7.1.0; package publish and
  deployment remain owner-run only.

## [7.0.0] - 2026-06-18

> Brain Productization Loop. Turns the Brain proof work into a first-five-minute
> product flow: add sources, ask a question, see proof/citations, and verify the
> same Brain evidence after switching models.

### Added
- Brain Home ingestion panel for files, local folder paths, notes, and web URLs,
  all backed by existing workspace-scoped ingestion routes.
- Answer-level Memory proof and source citation cards rendered under assistant
  responses after Brain proof refreshes for the user's query.
- Model-continuity demo strip that lets the user recheck the same Brain
  evidence and jump to model switching from the Brain flow.
- Deterministic `scripts/brain_quality_eval.py` recall/KG quality gate, wired
  into CI after the unit suite.
- Visual mock coverage for Brain proof, document upload, note ingest, folder
  indexing, and web URL ingestion endpoints.

### Changed
- Brain Home is now ingestion-first instead of chat-first: first screen action
  labels are files/folders/notes/web, with deeper graph/model/settings still
  reachable from the shell.
- Package/runtime/static metadata is synchronized to 7.0.0; package publish and
  deployment remain owner-run only.

## [6.7.0] - 2026-06-18

> Brain IA Cleanup. Makes rich product pages reachable from Brain Home,
> separates visible product IA from legacy compatibility aliases, and
> code-splits heavy pages out of the first Brain bundle.

### Changed
- Brain Home now exposes direct action routes for adding documents, searching
  the knowledge graph, changing models, and opening settings, so the rich
  Capture, Brain explorer, Library, and System pages are reachable from the
  first Brain flow.
- The post-onboarding app shell now mounts Capture, Brain explorer/graph,
  Library, System, and Act pages inside a shared Brain shell navigation instead
  of leaving those rich pages as compatibility-only routes.
- `routes.ts` now keeps product shell routes and compatibility aliases in
  separate maps while preserving old hash/entry URLs.
- Rich pages now load through `React.lazy`, splitting Brain explorer, Capture,
  Library, System, and Act chunks away from the initial Brain Home bundle.
- Package/runtime/static metadata is synchronized to 6.7.0; package publish and
  deployment remain owner-run only.

## [6.6.0] - 2026-06-18

> Brain Proof Runtime. Makes the first-five-minute Brain value visible by
> showing backend-owned evidence that saved context can be recalled and survives
> model changes.

### Added
- `/api/memory/brain-proof`, backed by `MemoryService.brain_proof()`, combining
  Brain readiness, durable memory counts, graph/vector depth, active model id,
  and a unified recall sample.
- Brain Home proof strip for recallable context, model-continuity, and
  knowledge-store state.
- Recent recall proof card that surfaces the latest memory/item the Brain can
  bring back after a useful exchange.
- Brain Home document upload CTA in the empty state and composer, giving the
  first session a direct way to grow the Brain with files.
- Refreshed README-linked release evidence under `output/release/v6.6.0/`.
- Unit coverage for model-independent Brain proof recall behavior.
- Honest empty-state Brain proof: model-independent capability remains visible,
  but continuity is only marked proven after durable evidence exists.

### Changed
- Interaction router runtime context now carries an active-model getter into
  the Memory router, reducing app-factory inline wiring while preserving route
  order.
- Default Brain proof recall seeding is scoped to the current user/workspace
  before falling back to conversation memory.
- Chat-to-Knowledge-Graph ingestion now carries workspace scope into projected
  graph nodes, and Brain proof conversation counts use scoped conversations so
  another user/workspace cannot make an empty Brain look proven.
- Direct Knowledge Graph ingest and document upload ingestion now resolve the
  request workspace and project that scope into graph/source/document nodes.
- Package/runtime/static metadata is synchronized to 6.6.0; package publish and
  deployment remain owner-run only.

## [6.5.0] - 2026-06-18

> Brain Experience Readiness. Uses the 6.4.0 quality baseline to improve what
> normal users can understand: how alive the Brain is, where they are in the
> memory-to-graph journey, and what changed after a useful exchange is saved.

### Added
- Brain Home readiness strip that classifies the current Brain as waiting for
  its first memory, forming topics, or ready for map exploration.
- Backend-owned Brain readiness summary from `MemoryService`, exposed through
  `/api/memory/manager` and `/api/memory/brain-quality`, so the UI no longer
  has to infer Brain growth from local fragments.
- Persistent depth progress rail across the Living Brain surface so the
  memory -> topics -> relationships -> graph journey is visible and reversible.
- Source-aware memory-save detail under chat feedback, making the recall loop
  explicit after a conversation enriches the Brain.
- Visual regression assertions for Brain readiness, depth progress, and
  source-aware memory-save feedback.

### Changed
- Brain overview empty states now guide first-memory use without exposing graph
  internals or administrator language.
- Brain readiness now uses the same memory, graph, relationship, and source
  health signals that the Memory Manager reports, keeping product UX aligned
  with backend Brain state.
- Package/runtime/static metadata is synchronized to 6.5.0; package publish and
  deployment remain owner-run only.

## [6.4.0] - 2026-06-17

> Digital Brain Quality Hardening. Tightens workspace-scoped Brain retrieval
> and memory mutation boundaries while adding non-destructive quality primitives
> for embeddings, retrieval, memory, graph validation, context assembly, and
> benchmarks.

### Added
- `lattice_brain.quality` with embedding fallback labelling, drift/re-index
  planning, BM25 lexical scoring, hybrid fusion, reranker fallback contracts,
  memory candidate scoring/deduplication/conflict/retention helpers, graph edge
  confidence/evidence metrics, structured context guardrails, and retrieval
  benchmark metric calculation.
- `docs/v6.4/BRAIN_QUALITY_BASELINE.md` documenting the 6.4.0 Digital Brain
  quality baseline, risk register, validation items, and deferred work.
- Refreshed README release evidence screenshots and walkthrough GIF under
  `output/release/v6.4.0/`.
- Unit coverage for Brain quality primitives and workspace-scoped graph/search
  and memory-manager mutation boundaries.

### Changed
- `/api/graph*`, `/knowledge-graph/*`, and hybrid-search service paths now
  preserve workspace scope across graph, node, neighborhood, relationship,
  keyword, vector, graph, and hybrid retrieval.
- Memory Manager prune, compact, and clear operations now intersect requested
  ids/kinds with the caller's scoped memory set.
- Memory Manager graph clear is blocked because the existing graph clear path
  is not workspace-scoped.
- Package/runtime/static metadata is synchronized to 6.4.0; package publish and
  deployment remain owner-run only.

## [6.3.1] - 2026-06-17

> Access Runtime / i18n Follow-up. Closes the next app-factory decomposition
> slice and finishes Capture/Review Center localization coverage on top of the
> v6.3.0 product-hardening line.

### Changed
- Moved app-factory access-control helper closures into
  `latticeai.runtime.access_runtime` while preserving role lookup, token
  extraction, user enforcement, admin enforcement, and public user projection
  call signatures.
- Capture tabs, headings, placeholders, action labels, upload details, folder
  connection copy, browser capture copy, and pipeline controls now use the
  shared Korean/English i18n map.
- Review Center inbox/card copy, status badges, empty states, Run Now feedback,
  snooze/unsnooze labels, and action aria labels now use the shared i18n map.
- README release evidence screenshots and walkthrough GIF are refreshed under
  `output/release/v6.3.1/`.
- Package/runtime/static metadata is synchronized to 6.3.1; package publish and
  deployment remain owner-run only.

### Added
- Focused unit coverage for the extracted access runtime auth/admin behavior.

## [6.3.0] - 2026-06-16

> Product Hardening Completion. Completes the 6.2 decomposition follow-up by
> tightening Brain archive, provenance, ingestion, Review Center, local model
> runtime, app-factory wiring, compatibility shim, i18n, and release-validation
> surfaces.

### Changed
- Brain Home and onboarding screens are split into finer feature-owned modules
  while preserving the existing routes, CSS surface, and visual behavior.
- Platform and automation runtime wiring moved behind a dedicated app-factory
  seam, keeping construction side-effect free and route order unchanged.
- Admin, Brain, and onboarding localized aria/placeholder surfaces now use the
  shared i18n map.
- Brain archive care now validates archive paths, confirms passphrases, gives
  strength guidance, and summarizes inspect/restore-preview results without
  dumping raw JSON.
- Document capture now exposes a drop/choose ingest queue with progress,
  failure reason, retry, source metadata, and post-ingest Brain/Graph
  confirmation.
- Brain memory and graph surfaces now show provenance/source type, created time,
  source path/title/conversation fallback, and copy-source affordances.
- Review Center cards now keep visible action result/error feedback for
  run-now/approve/dismiss/snooze flows while preserving run-now as
  preview/regenerate rather than approval.
- Local model setup now reports loaded model, engine state, cache/storage path,
  download/load progress, reload/unload controls, and an honest no-model state.
- App factory model/chat/review-tail wiring moved into focused runtime wiring
  modules while preserving route order.
- Review Center run-now backend wiring moved into `latticeai.runtime.review_wiring`
  and is covered by scoped runner/status-preservation tests.
- Legacy root shim import smoke now covers server, knowledge graph, CLI,
  Telegram, and P-Reinforce compatibility paths.
- Package/runtime/static metadata is synchronized to 6.3.0; package publish and
  deployment remain owner-run only.

### Added
- Frontend i18n literal check for localized aria/placeholder props in Brain,
  Admin, and onboarding TSX surfaces.
- Focused runtime wiring modules for model runtime, chat/interaction contexts,
  and review/Brain tail registration.

## [6.2.0] - 2026-06-16

> Product Decomposition / Release Smoke Automation. Splits the largest product
> surfaces into feature modules, strengthens consent/i18n coverage, shrinks
> legacy root modules, and automates release smoke validation.

### Added
- Feature-owned Brain Home and Admin Console modules behind the existing app
  shell route surface.
- Onboarding screen components for ProductFlow, backed by design-system CSS
  instead of inline style layout.
- Model download consent details for size, storage location, external target,
  and a "do later" path.
- Typed tool and interaction router context objects for app-factory route
  registration without changing route order.
- `npm run release:smoke` covering wheel install, npm tgz contents, static
  assets, and Tauri artifact checks.

### Changed
- Historical root modules for CLI, Telegram, and P-Reinforce now delegate to
  package modules while preserving import and script compatibility.
- Admin Console and onboarding user-facing copy now route through shared i18n
  keys for Korean/English coverage.
- Package/runtime/static metadata is synchronized to 6.2.0; package publish
  and deployment remain owner-run only.
- README release evidence screenshots and walkthrough GIF are refreshed under
  `output/release/v6.2.0/`.

### Preserved
- v6.1 screenshots and validation reports remain historical evidence.
- Package publish and deploy commands continue to require exact artifact names.

## [6.1.0] - 2026-06-16

> Product Hardening / Digital Brain Completion. Tightens the local-first Brain
> flow, Brain Core boundary, backend trust gates, and release documentation
> without adding broad new product surfaces.

### Added
- First-run path to open the Brain without installing or loading a model first.
- Brain Home empty-state loop that shows first memory save, Brain state return,
  and backup ownership.
- AST import guard proving `lattice_brain` does not import `latticeai` or
  `ltcai`.
- Unit coverage for model download consent blocking in `model_runtime`.
- `latticeai.cli.runtime` for pure CLI runtime helpers while preserving the root
  `ltcai_cli.py` entrypoint.
- v6.1 baseline, frontend UX, backend hardening, and instruction documents under
  `docs/v6.1/`.

### Changed
- Review Center cards clarify that Run now previews/executes without approval.
- Static app assets are refreshed for the v6.1 frontend hardening changes.
- Tool dispatch authorization now uses an injectable service boundary around
  the shared ToolRegistry while preserving the legacy module-level API.
- Chat agent runtime construction moved out of `create_chat_router` for
  production app assembly and is now injected through `AppContext`.
- README release evidence screenshots and walkthrough GIF are refreshed under
  `output/release/v6.1.0/`, including the Review Center surface.
- README and release notes described v6.1.0 as the then-current hardening target
  with exact expected artifact filenames.

### Preserved
- Package publish, deployment, tag creation, and main merge remain out of scope
  unless explicitly requested.
- Package/runtime version metadata is synchronized to 6.1.0; package publish,
  deployment, tag creation, and main merge remain owner-run only.

## [6.0.0] - 2026-06-15

> Product Reset / Review Center Completion. Raises the Review Center from a
> pending-only inbox into a reversible automation review surface while
> documenting the v6 quality uplift honestly.

### Added
- Review Center status filters for Pending, Snoozed, and All.
- Explicit `POST /automation/reviews/{item_id}/unsnooze` API and backend policy.
- Frontend Unsnooze action and clear `snoozed_until` presentation.
- `docs/v6/PLAN.md`, `ARCHITECTURE_REVIEW.md`, `UX_REVIEW.md`, and
  `QUALITY_SCORECARD.md`.

### Changed
- Review Center frontend moved from `Act.tsx` into
  `frontend/src/features/review/` components and helpers.
- Review item frontend types now alias generated OpenAPI component schemas.
- Review Center API calls now use generated OpenAPI operation paths for list,
  approve, dismiss, snooze, unsnooze, and run_now actions.
- `app_factory.py` runtime assembly moved behind session, hooks, web,
  persistence, lifespan, automation, context/search, platform service, app
  context, and router-registration seams while preserving the frozen 364-entry
  route/mount snapshot.
- First-run/onboarding copy now states local-first trust boundaries more
  directly: local knowledge by default, explicit downloads, and explicit
  external transfer.
- README release evidence screenshots and walkthrough GIF are refreshed under
  `output/release/v6.0.0/`, including the Review Center surface.
- OpenAPI artifacts and synchronized package/runtime/static metadata now target
  `6.0.0`.

### Preserved
- `run_now` remains preview/regenerate and does not approve.
- Snooze expiry remains read-time only; explicit unsnooze is the only immediate
  return-to-pending mutation.
- Package publishing, GitHub Release creation, artifact upload, and merge to
  `main` remain out of scope for this branch.

## [5.6.0] - 2026-06-15

> Brain Automation Review Center. Adds a workspace-scoped review inbox for
> automation output so scheduled runs and Brain-event suggestions land as
> inspectable items before the user approves, dismisses, snoozes, or reruns
> them.

### Added
- Backend Review Queue service, store persistence, and `/automation/reviews`
  API with explicit `source`, `status`, `effective_status`, payload, and
  provenance fields.
- Review item actions: approve, dismiss, snooze, and run_now. `run_now` is a
  preview/regenerate action and does not mark the item approved.
- Optional `review_queue: true` opt-in path for TriggerService and RunExecutor
  to enqueue review items without changing legacy scheduler behavior.
- Act page Review inbox under Runs with source filtering, pending review cards,
  provenance details, and guarded actions.

### Changed
- Bumped synchronized Python, npm, VSIX, Tauri, runtime constants, lockfiles,
  OpenAPI artifacts, and static metadata to `5.6.0`.
- Updated release documentation and artifact names for exact 5.6.0 release
  preparation.

### Preserved
- Snoozed items stay hidden until their `snoozed_until` time expires; expiry is
  interpreted at read time through `effective_status` without scheduler mutation.
- Existing v5.4/v5.5 automation and workflow behavior remains compatible unless
  a workflow explicitly opts into review queue creation.
- Package registry publishing and deployment remain owner-run only.

## [5.5.0] - 2026-06-15

> Release Coordination. Synchronized package/runtime/static metadata and release
> documentation for the 5.5.0 line while preserving v5.4.0 Brain Automation
> Scheduler behavior.

### Changed
- Bumped synchronized Python, npm, VSIX, Tauri, runtime constants, lockfiles, and
  static asset manifest metadata to `5.5.0`.
- Updated README, RELEASE.md, RELEASE_NOTES.md, FEATURE_STATUS.md,
  vscode-extension/README.md, and this changelog so current-release references
  and expected artifact names point at exact 5.5.0 filenames.

### Preserved
- v5.4.0 consent-first Brain automation recipes, TriggerService dedup,
  LATTICE_TZ, degraded status, enabled:false disarming, runtime graph cleanup,
  and E2E scenario coverage remain the functional baseline.
- Package registry publishing and deployment remain owner-run only.

## [5.4.0] - 2026-06-15

> Brain Automation Scheduler. Consent-first recipe drafts (Daily Memory Digest,
> Weekly Project Review, Follow-up Radar) install as disabled workflows.
> Scheduler triggers (TriggerService) with dedup, LATTICE_TZ, degraded status,
> and runtime graph cleanup.

### Added
- Consent-first Brain automation recipe drafts for Daily Memory Digest, Weekly
  Project Review, and Follow-up Radar. Recipes install as disabled workflows so
  users can inspect them before any scheduler or Brain-event trigger fires.

### Changed
- Workflow trigger scanning now treats explicit `enabled: false` trigger config
  as disarmed while preserving legacy behavior for existing workflows that do
  not include an `enabled` field.
- The Automate page now surfaces Brain automation recipe cards with local-only,
  review-before-run consent copy.
- Recipe install UX: success feedback on "Create reviewable draft", install-time
  button disabled to block duplicates clicks, and if same recipe draft already
  exists (by metadata), button state changes + guide text instead of re-creating.
- lattice_brain/runtime dependency/responsibility graph 정리 + 실제 진입점 매핑 문서화 (runtime/* 모듈 헤더 + app_factory 주석). AgentRuntime (lattice_brain facade) vs latticeai/core/agent (single-agent state/plan/transcript) 분리 명확화.
- TriggerService 스케줄러 엣지케이스 보강: LATTICE_TZ env 지원 (describe() 노출), last_attempt_at + cooldown dedup 가드로 중복 실행 방지 (interval/brain_event), consecutive_failures + "degraded" status per-trigger 서페이싱.
- A방향 E2E 시나리오 초안 brain_automation.py 에 작성 (draft install, dedup, consent-first, trigger fire with provenance, LATTICE_TZ, degraded, review flow, RunExecutor 경로).

## Unreleased

## [5.3.0] - 2026-06-14

> Product Clarity and Runtime Cleanup. Lattice AI is now presented consistently
> as a local-first Digital Brain that keeps user knowledge durable across any AI
> model, while the first runtime extraction seams move out of `app_factory.py`.

### Added
- `docs/DEVELOPMENT.md` for contributor setup, validation gates, runtime
  assembly expectations, and documentation sync rules.
- `docs/LEGACY_COMPATIBILITY.md` to explain root compatibility modules, their
  current homes, why they remain packaged, and the safe removal checklist.
- v5.3.0 release evidence index and copied screenshot/GIF paths under
  `output/release/v5.3.0/`.

### Changed
- Reorganized README around product identity, user need, user actions,
  one-minute flow, screenshots, installation, architecture, and release
  preparation. Release artifacts no longer interrupt the first screen.
- Unified public descriptions in README, package metadata, pyproject, feature
  status, architecture, release notes, and VS Code extension docs.
- Improved onboarding and model setup copy around local ownership, explicit
  consent, model-as-voice, Brain-as-asset, and what happens before download/load.
- Simplified Basic model setup to a shorter recommendation set while Advanced
  keeps hardware, verification, load strategy, license, and safety detail.
- Moved config, security, and Brain runtime builders from `latticeai.app_factory`
  into `latticeai.runtime` modules while preserving lazy import compatibility.
- Bumped synchronized package/runtime/static versions to `5.3.0`.

### Preserved
- No package registry publishing automation.
- Local-first defaults and explicit consent gates for downloads, cloud calls,
  Telegram, Brain Network, Docker/Postgres setup, and update checks.
- `server_app.__getattr__` lazy compatibility and app factory import side-effect
  boundaries.

## [5.2.0] - 2026-06-14

> Lattice AI 5.2.0 — Aggressive User-Focused Model Transformation. Transparent
> structured capability registry, automated HF verification, modern multimodal
> additions, download/load strategy exposure, hardware notes, verified status,
> and updated UI/backend flows so users see exactly what they get before consent.

### Added
- Structured `ModelCapabilityRegistry` (latticeai/services/model_capability_registry.py) with dataclass fields for provider/hf_repo_id, modality, quantization, download_strategy, load_strategy, hardware (min/recommended RAM, Apple/CUDA prefs, notes), license, safety_notes, and rich VerificationStatus (hf_exists, has_config, has_tokenizer, pipeline, last_checked, notes).
- Modern multimodal candidates (Gemma 3 4B/12B, Qwen2.5-VL-7B, Llama-3.2-11B-Vision, Pixtral-12B) in the structured registry for HF verification transparency, alongside the user-facing Gemma-4 / Qwen3-VL / Llama-4 load-ready family.
- Automated verification script: `scripts/verify_hf_model_registry.py` (lightweight HF API + restricted snapshot for config/tokenizer; optional --test-load for small models; explicit LARGE_MODEL notes; writes verification_report.json).
- Registry info exposed via `/models` and `/models/recommendations` (registry.verified_count, verification dicts, hardware, strategies).
- New unit tests: `tests/unit/test_model_capability_registry.py` (5 tests covering registry, legacy shape, rec payload, report, roundtrips).

### Changed
- `model_catalog.py` now sources ENGINE_MODEL_CATALOG + aliases from the capability registry (single source of truth), preserves legacy shapes + reexports, and finalizes the user-facing catalog to current load-ready families so lower-generation or non-load-verified candidates do not become noisy primary choices.
- `model_recommendation.py` `_classify_one` now forwards 5.2 fields (hf_repo, verification, hardware, load_strategy, license, safety, recommended_default).
- `verification.verified` and verified-model API lists now require HF presence plus config and tokenizer hints, with weights-hint detail exposed separately, so the UI badge matches the actual load-readiness contract.
- Marketplace template installs now keep registry entries scoped per workspace, and `/marketplace/templates/registry` returns only the authorized workspace scope.
- SQLite restore now checkpoints WAL state before taking the pre-restore backup, keeping failed blob restores rollback-safe on Linux/Python 3.12 CI and local builds.
- Backend model APIs return rich fields; frontend can render verified badges, modality, hardware notes, strategies.
- Library.tsx (ModelsPanel): added "multimodal" + "✓ HF" verified badges, recommended_default support, hardware notes line, load_strategy plus license/safety notes in advanced detail, updated guided setup copy for transparency and consent.
- All registry HF ids confirmed present via HF API on 2026-06-14; 15/16 expose config/tokenizer hints, Pixtral remains available-but-not-local-load-verified, and large models are flagged with explicit limitations.
- Version bumped to 5.2.0 everywhere (pyproject, __init__, package.json, vscode-extension).

### Preserved
- Exact public API shapes, recommendation tri-state logic, engine aliases, family de-dup, download consent gates, no silent downloads.
- Historical changelog entries for 5.1.0 and prior.

## [5.1.0] - 2026-06-14

> Product Trust & Clarity Release. v5.1.0 clarifies Lattice AI as a
> local-first private AI memory layer / Digital Brain, then adds security,
> privacy, honesty, and architecture gates so the product does not overclaim.

### Changed

- Rewrote the README first screen around the positioning lines
  `Your private AI memory layer. Keep your knowledge. Switch any model.` and
  `모델은 바꿔도, 내 지식은 남는 로컬 AI 브레인.`
- Added practical use cases for preserving project decisions, switching models,
  connecting documents/conversations/files/notes/decisions, encrypted Brain
  archive portability, cloud lock-in avoidance, and honest no-model states.
- Added and refreshed trust documentation: `PRIVACY.md`, `docs/WHY_LATTICE.md`,
  `docs/TRUST_MODEL.md`, `SECURITY.md`, `ARCHITECTURE.md`, and
  `FEATURE_STATUS.md`.
- Removed `csp:null` from Tauri production config and added an app-shell CSP
  response header.
- Centralized secret redaction for logs, audit payloads, security exports, and
  builtin hook packets.
- Changed chat auto-file handling so `LATTICEAI_AUTO_READ_CHAT_PATHS` remains
  off by default and does not silently read arbitrary local paths even if
  enabled.
- Added explicit `allow_download=true` consent for model download requests.
- Added config, security, and Brain runtime builder seams in `app_factory.py`
  while preserving the existing API shape.
- Hardened release artifact cleanup so `release:artifacts` removes stale
  `dist/ltcai-*` and root `ltcai-*.tgz` files before rebuilding exact v5.1.0
  artifacts only.
- Made `npm run test:integration` self-contained by starting a local uvicorn
  server, waiting for `/health`, running the integration suite, and shutting the
  server down.
- Fixed a SQLite Brain restore TOCTOU race where transient `-wal` / `-shm`
  siblings could disappear between probe and copy during archive restore.
- Bumped synchronized package/runtime/static versions to `5.1.0`, including
  Python metadata, npm package metadata, VSIX metadata, Tauri metadata,
  `latticeai`, `lattice_brain`, runtime constants, and static asset metadata.

### Added

- v5.1 trust validation tests for CSP, secret redaction, audit redaction,
  auto-file-read blocking, public/network auth posture, `shell=True`
  production-path scanning, and Brain Core import isolation.
- A deterministic regression test for restore-time WAL sibling disappearance.
- v5.1 release evidence paths under `output/release/v5.1.0`.

### Preserved

- Tracked release-note history now starts at v4.5.0; older release-note files
  are hidden from the Git tree.
- External package publishing remains owner-run; this release prepares exact
  artifacts and GitHub Release assets without registry publish automation.

### Artifacts

- `dist/ltcai-5.1.0-py3-none-any.whl`
- `dist/ltcai-5.1.0.tar.gz`
- `dist/ltcai-5.1.0.vsix`
- `ltcai-5.1.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_5.1.0_aarch64.dmg`

## [5.0.0] - 2026-06-14

> Multilingual Brain Foundation Release. v5.0.0 starts the major-version cleanup
> line by preserving the existing AgentRuntime, ToolRegistry, Brain Core, Admin
> Console, and graph foundations while making the product usable in Korean or
> English from first launch through Brain exploration.

### Changed

- Added a persisted `lattice.language` preference with Korean and English
  choices available on first-run onboarding, the Brain home, and the separated
  Admin Console header.
- Localized first-run login, environment analysis, model recommendation,
  install/download/load status, Brain quick views, starter prompts, memory save
  feedback, overview panels, and graph focus fallback copy.
- Updated visual tests so the Korean path is explicitly selected before running
  existing first-run and Brain depth assertions.
- Bumped synchronized package/runtime/static versions to `5.0.0`, including
  Python metadata, npm package metadata, VSIX metadata, Tauri metadata,
  `latticeai`, `lattice_brain`, runtime constants, and static asset metadata.
- Captured collaboration guidance from pts_claudecode and pts_grok: the next
  technical refactor order is config centralization, KG stabilization,
  ToolRegistry characterization, AgentRuntime extraction, then app factory
  decomposition.

### Preserved

- Tracked release-note history remains visible from v4.5.0 through v5.1.0.
- External package publishing remains owner-run; this release prepares exact
  artifacts and GitHub Release assets without registry publish automation.

### Artifacts

- `dist/ltcai-5.0.0-py3-none-any.whl`
- `dist/ltcai-5.0.0.tar.gz`
- `dist/ltcai-5.0.0.vsix`
- `ltcai-5.0.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_5.0.0_aarch64.dmg`

## [4.7.2] - 2026-06-14

> Intuitive Brain UX Release. v4.7.2 makes the Living Brain easier for
> non-technical users: first-run login is safer, recommended model setup is
> one-click, Brain memory/topic/relationship/graph views are directly visible,
> and conversation shows saved-to-memory feedback while Admin remains separate.

### Changed

- Prevented saved-user email mismatch or wrong saved-user password from silently
  creating a new empty Brain during first-run login.
- Added a primary `추천대로 시작하기` model recommendation path and clearer large
  model download messaging without fake ETA.
- Added visible `기억 보기`, `주제 보기`, `관계 보기`, and `그래프로 보기` actions
  on the Brain surface so users can open the desired Brain depth directly.
- Added a Brain overview panel with recent memories, older memories, major
  topics, and saved-to-memory feedback after conversation.
- Updated first-run and empty-Brain copy toward plain user language while
  keeping the graph as the deepest advanced layer.
- Bumped synchronized package/runtime/static versions to `4.7.2`, including
  Python metadata, npm package metadata, VSIX metadata, Tauri metadata,
  `latticeai`, `lattice_brain`, runtime constants, and static asset metadata.
- Refreshed README, release notes, architecture, feature status, security,
  recovery notes, VS Code extension docs, release report, and release evidence
  paths for v4.7.2.

### Preserved

- Tracked release-note history remains visible from v4.5.0 through v5.1.0.
- External package publishing remains owner-run; this release prepares exact
  artifacts and GitHub Release assets without registry publish automation.

### Artifacts

- `dist/ltcai-4.7.2-py3-none-any.whl`
- `dist/ltcai-4.7.2.tar.gz`
- `dist/ltcai-4.7.2.vsix`
- `ltcai-4.7.2.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.7.2_aarch64.dmg`

## [4.7.1] - 2026-06-14

> Admin Operations Release. v4.7.1 extends the separated Admin Console with
> role permission visibility, audit search/severity filters, local log retention
> posture, and a dedicated Admin Console data-loading boundary while keeping the
> user Brain surface simple.

### Changed

- Added role permission visibility to the Admin Console so operators can inspect
  role member counts and capability summaries without entering the user Brain
  surface.
- Added server-backed audit filtering for search text, actor, action, severity,
  and limit on `/admin/audit`.
- Added `/admin/log-retention` to report local retention days, retained events,
  prune candidates, and export-before-prune status without destructive pruning.
- Split Admin Console data loading into a dedicated frontend hook so admin
  observability state stays separate from Brain chat state.
- Updated Admin Console visual mock data for filtered audit and retention
  coverage.
- Bumped synchronized package/runtime/static versions to `4.7.1`, including
  Python metadata, npm package metadata, VSIX metadata, Tauri metadata,
  `latticeai`, `lattice_brain`, runtime constants, and static asset metadata.
- Refreshed README, release notes, architecture, feature status, security,
  VS Code extension docs, release report, and release evidence paths for v4.7.1.

### Preserved

- Tracked release-note history remains visible from v4.5.0 through v5.1.0.
- External package publishing remains owner-run; this release prepares exact
  artifacts and GitHub Release assets without registry publish automation.

### Artifacts

- `dist/ltcai-4.7.1-py3-none-any.whl`
- `dist/ltcai-4.7.1.tar.gz`
- `dist/ltcai-4.7.1.vsix`
- `ltcai-4.7.1.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.7.1_aarch64.dmg`

## [4.7.0] - 2026-06-14

> Admin Separation Release. v4.7.0 keeps the Living Brain as the simple user
> surface and moves users, logs, security events, policies, and Brain operations
> into a dedicated Admin Console with synchronized release metadata and
> publishable artifacts.

### Changed

- Added a separate `#/admin` Admin Console so the normal `/app` user experience
  remains Brain + conversation instead of becoming an admin dashboard.
- Added admin overview metrics, user directory, audit log rows, security event
  rows, policy chips, and Brain index rebuild controls to the admin-only
  surface.
- Scoped admin history, audit, stats, and sensitivity endpoints by
  `X-Workspace-Id` / `workspace_id` when a workspace is selected, while keeping
  legacy global records visible in Personal workspace compatibility mode.
- Added frontend API helpers for `/admin/stats` and `/admin/security/events`,
  reusing the existing FastAPI admin/security backend rather than inventing a
  parallel logging store.
- Updated visual validation so the Admin Console route is checked separately
  from the user Brain surface.
- Bumped synchronized package/runtime/static versions to `4.7.0`, including
  Python metadata, npm package metadata, VSIX metadata, Tauri metadata,
  `latticeai`, `lattice_brain`, runtime constants, and static asset metadata.
- Refreshed release screenshots/GIF paths and docs for v4.7.0, including Admin
  Console evidence.
- Updated README, RELEASE.md, ARCHITECTURE.md, SECURITY.md, FEATURE_STATUS.md,
  VS Code extension docs, release notes, and release report for the current
  v4.7.0 release line.

### Preserved

- Tracked release-note history remains visible from v4.5.0 through v5.1.0.
- Local-first ownership, `.latticebrain` portability, rollback-safe restore,
  and the deepest-layer Knowledge Graph behavior are preserved.

### Artifacts

- `dist/ltcai-4.7.0-py3-none-any.whl`
- `dist/ltcai-4.7.0.tar.gz`
- `dist/ltcai-4.7.0.vsix`
- `ltcai-4.7.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.7.0_aarch64.dmg`

## [4.6.1] - 2026-06-14

> Living Brain release refresh and publishable version bump after the v4.6.0
> PyPI immutability block. v4.6.1 keeps the Living Brain implementation and
> synchronizes release metadata, README evidence, architecture docs, artifacts,
> and owner publishing commands to exact `4.6.1` filenames.

### Changed

- Added a Brain-first product direction review and synchronized philosophy /
  Knowledge Graph docs so the graph is documented as infrastructure inside the
  Brain rather than the product center.
- Added a compact "Care for my Brain" ownership panel to the Brain home
  experience, surfacing local-first/private/portable guarantees plus existing
  export, backup, encrypted archive, inspect, and restore dry-run actions
  without making the Knowledge Graph the product center.
- Refined the Brain home so conversation remains the primary surface and the
  "Care for my Brain" ownership controls open only when requested, with action
  results now reflecting the last completed ownership action.
- Strengthened first-run and empty-Brain product messaging around the core
  promise that models are replaceable while the user's knowledge, decisions,
  projects, and context are durable.
- Hardened Knowledge Graph backup restore and encrypted `.latticebrain` restore
  so DB/blob replacement is staged with pre-restore backups and rollback on
  partial failure.
- Bumped synchronized package/runtime/static versions to `4.6.1`, including
  Python metadata, npm package metadata, VSIX metadata, Tauri metadata,
  `latticeai`, `lattice_brain`, runtime constants, and static asset metadata.
- Refreshed README around the current Living Brain flow: Login -> Environment
  Analysis -> Recommended Models -> Install & Load -> Brain Chat.
- Replaced stale README screenshot/GIF references with fresh v4.6.1 release
  evidence for Login, setup, Brain Chat, Living Brain, Memory Layer, Knowledge
  Layer, Relationship Layer, and Knowledge Graph.
- Updated `ARCHITECTURE.md` for the current Tauri shell, React/Vite frontend,
  FastAPI localhost API, independent `lattice_brain` Brain Core, StorageEngine,
  SQLite default, PostgreSQL/pgvector opt-in, backup/restore, and
  `.latticebrain` portability architecture.
- Added `RELEASE_NOTES_v4.6.1.md` and the v4.6.1 release refresh report.

### Preserved

- Tracked release-note history remains visible from v4.5.0 through v5.1.0.
  Older hidden notes were not rewritten as v4.6.1 claims.
- No backend architecture redesign, package publishing, service deployment, or
  registry upload is part of this refresh.

### Artifacts

- `dist/ltcai-4.6.1-py3-none-any.whl`
- `dist/ltcai-4.6.1.tar.gz`
- `dist/ltcai-4.6.1.vsix`
- `ltcai-4.6.1.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.6.1_aarch64.dmg`

## [4.6.x Brain Exploration Update] - 2026-06-14

> The Brain is now the true interactive entry point to the user's knowledge.
> Clicking the living Brain progressively reveals deeper layers of the mind:
> Level 1 living presence -> memories -> concepts -> relationships -> the
> emergent full Knowledge Graph. The graph never appears abruptly; it grows out
> of the Brain as the user travels inward.

### Changed
- Rebuilt `LivingBrain` as a recognizable anatomical Brain with animated
  hemispheres, folds, memory ripples, thought particles, and state-specific
  responses for listening, recalling, thinking, planning, and acting.
- BrainHome now manages five progressive depths. Level 1 shows only the living
  Brain; Level 2 reveals memory fragments; Level 3 reveals concepts; Level 4
  reveals relationship threads; Level 5 reveals the searchable Knowledge Graph.
- Layered emergence uses the existing memory and graph APIs but presents them
  as unfolding layers inside one central Brain experience rather than separate
  pages, tabs, or a bottom depth bar.
- Level 5 now includes graph nodes, edges, search filtering, and focus details
  in an emergent graph panel.
- Conversation remains available at every depth; the Brain presence changes
  intensity as the user travels inward or streams a response.
- Updated visual validation to assert the new depth contract, graph emergence,
  graph search, chat streaming, mobile overflow, and legacy entry compatibility.

The user should feel they are moving inward through their own mind, with the graph as the deepest, most structured layer that grew from the living presence.

### Changed (Frontend — full replacement of the previous prototype)
- The entire post-onboarding surface is now a single immersive "Brain Space". The Brain presence is large, always visible, and the primary emotional and visual object.
- Conversation is the central, intimate way you live with the Brain. No traditional app chrome, dock, or page shell in the primary experience.
- LivingBrain component completely rewritten as a reactive, layered, breathing organism with memory ripples, thought particles, state-driven animation, and click-to-respond behavior.
- Progressive discovery enforced in the UI: from the Brain you gently descend into Memory, Knowledge, Connections, and The Map (the graph). The graph is never the landing experience.
- Onboarding (ProductFlow) reframed as a quiet, ceremonial awakening ritual with the Brain presence participating at every step.
- New warm, private, organic visual language (deep ember-gold presence, soft teal memory pulses, generous space, contemplative typography). Old dashboard aesthetics and navigation chrome suppressed on the primary path.
- Depth chambers are full-bleed, slow, human rooms — not feature pages. A small living trace of the Brain stays visible for continuity.
- All backend functionality (chat streaming, memory, hybrid search, graph, model prepare/load, portability, agents, etc.) is preserved and reachable. The old navigation remains for deep-link compatibility but is not the product surface.

The technology was already a Digital Brain. This release makes the *experience* one.

## [4.6.0] - 2026-06-13

> Living Brain Experience release after the v4.5.1 product shell reset. (Previous iteration — this RC supersedes the visual and structural approach while keeping the capability foundation.)

### Changed

- Added the required first-launch product flow: Login -> Environment Analysis
  -> Recommended Models -> Install & Load -> Brain.
- Made Brain plus conversation the post-model-load `/app` and `/app#/brain`
  experience.
- Added an animated living Brain presence that reacts to listening, memory
  recall, streaming/thinking, and agent/workflow activity.
- Centralized chat streaming, model status, image attachment, conversation
  history, and memory previews in a reusable Brain conversation component.
- Reduced visible primary navigation to Brain, Memory, Files, Automations,
  Models, and Settings;
  `/ask` and `/chat` remain route-compatible aliases into Brain.
- Reordered Brain layers as Brain -> Memories -> Knowledge -> Relationships ->
  Graph, moving graph exploration to the deepest intentional layer.
- Updated visual palette and copy so the product reads as a living digital
  Brain rather than a graph tool or dashboard.
- Removed the first-run dashboard/setup-card panel from the app shell; setup is
  now a full-screen product sequence before the Brain opens.

### Preserved

- Brain Core, FastAPI APIs, Tauri shell, StorageEngine, backup/restore,
  portability, model runtimes, graph/search/chat/capture/automation/system
  workflows, and route aliases remain capability-compatible.

### Validation

- v4.6.0 validation scope is tracked in
  `docs/V4_6_0_LIVING_BRAIN_EXPERIENCE_REPORT.md`.

### Artifacts

- `dist/ltcai-4.6.0-py3-none-any.whl`
- `dist/ltcai-4.6.0.tar.gz`
- `dist/ltcai-4.6.0.vsix`
- `ltcai-4.6.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.6.0_aarch64.dmg`

## [4.5.1] - 2026-06-13

> Product Reimagining release candidate after the v4.5.0 capability recovery.

### Changed

- Replaced the desktop shell with a compact premium chrome, ambient brain
  canvas, command palette, responsive mobile drawer, and six-room product model:
  Home, Ask, Add, Automate, Library, Care.
- Rewrote first-run onboarding as a non-technical journey: Make it yours ->
  Choose a space -> Meet your Mac -> Pick a brain -> Install locally -> Try a
  question -> Set the pace -> Explore memory.
- Retained all compatibility hash routes while changing visible navigation,
  information hierarchy, route labels, and page hero language.
- Replaced the global visual system with a calmer Digital Brain palette,
  fixed-size responsive typography, 8px-or-smaller card radii, and refined
  shared controls.

### Preserved

- Brain Core, FastAPI APIs, Tauri shell, StorageEngine, backup/restore,
  portability, model runtimes, graph/search/chat/capture/automation/system
  workflows, and route aliases remain capability-compatible.

### Validation

- Full v4.5.1 RC validation is tracked in
  `docs/V4_5_1_VALIDATION_REPORT.md`.

### Artifacts

- `dist/ltcai-4.5.1-py3-none-any.whl`
- `dist/ltcai-4.5.1.tar.gz`
- `dist/ltcai-4.5.1.vsix`
- `ltcai-4.5.1.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.5.1_aarch64.dmg`

## [4.5.0] - 2026-06-13

> Product Experience Recovery release candidate after the v4.4.0 physical Brain
> extraction.

### Changed

- Restored the first-run product journey in the desktop shell: Login ->
  Workspace Selection -> Environment Analysis -> Model Recommendation -> Model
  Installation -> Model Validation -> Mode Selection -> Brain Usage.
- Reworked Library Models around the existing prepare/load stream so model setup
  visibly moves through Environment Analysis, Recommended Models, Install,
  Download Progress, Validate, Load, and Ready.
- Basic mode now uses friendlier connected/needs-setup status language and
  hides endpoint/module details in the Brain graph, model cards, and computer
  readiness panels while preserving Advanced/Admin inspection detail.
- Brain graph/search copy now emphasizes ideas, relationships, sources, focus,
  filtering, and readability instead of backend endpoint implementation.

### Fixed

- Gemma 4 MLX routing now reads local model metadata before load. Gemma 4 12B
  `gemma4_unified` shows **Runtime update needed** when installed MLX-VLM lacks
  `mlx_vlm.models.gemma4_unified`; Gemma 4 26B A4B remains ready on the
  standard `gemma4` MLX-VLM path.
- Raw loader errors such as `No module named ...gemma4_unified` are converted
  into actionable runtime-update guidance instead of marking the model ready or
  routing it through an incompatible MLX-LM fallback.
- Workspace selection now persists across reloads so the selected workspace is
  used by API requests after restart.

### Validation

- Added unit coverage for Gemma 4 12B versus 26B runtime compatibility,
  recommendation state, catalog routing, and the MLX-LM fallback guard.
- Added Playwright coverage for first-run setup, model setup flow, Gemma
  recovery guidance, and Basic graph developer-leakage prevention.
- Full v4.5.0 RC validation is tracked in
  `docs/V4_5_0_VALIDATION_REPORT.md`.

### Artifacts

- `dist/ltcai-4.5.0-py3-none-any.whl`
- `dist/ltcai-4.5.0.tar.gz`
- `dist/ltcai-4.5.0.vsix`
- `ltcai-4.5.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.5.0_aarch64.dmg`
