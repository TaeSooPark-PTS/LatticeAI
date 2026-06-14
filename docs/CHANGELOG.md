# Changelog

The top entry is the current release-preparation target. Older entries are
historical and may describe behavior as it existed at that release.

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
