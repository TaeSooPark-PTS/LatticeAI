# Release Notes

The current release is v6.6.0. The tracked release-note surface starts at
v4.5.0 so the public Git tree stays focused on the current product era.

## v6.6.0 - Brain Proof Runtime

Lattice AI v6.6.0 makes the Brain's first-five-minute value visible: saved
context, model-independent continuity, and recall evidence now appear directly
on Brain Home.

### Highlights

- Added `/api/memory/brain-proof`, backed by `MemoryService.brain_proof()`, for
  backend-owned Brain proof across memory, conversation, graph, vector, active
  model, and unified recall signals.
- Brain Home now shows recallable context, model-continuity, knowledge-store
  proof, and the latest recallable item.
- Brain Home now includes a document upload CTA in the empty state and composer,
  backed by workspace-scoped document ingestion.
- Interaction router runtime context now carries active-model state into the
  Memory router, reducing app-factory inline wiring while preserving route
  order.
- Synchronized package/runtime/static versions to `6.6.0`.
- Refreshed release evidence under `output/release/v6.6.0/`.

### Expected Artifacts

- `dist/ltcai-6.6.0-py3-none-any.whl`
- `dist/ltcai-6.6.0.tar.gz`
- `dist/ltcai-6.6.0.vsix`
- `ltcai-6.6.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_6.6.0_aarch64.dmg`

Full notes: [RELEASE_NOTES_v6.6.0.md](RELEASE_NOTES_v6.6.0.md)

## v6.4.0 - Digital Brain Quality Hardening

Lattice AI v6.4.0 tightens the Digital Brain quality layer without adding
unrelated product surface. It focuses on scoped retrieval/memory boundaries,
embedding/retrieval quality primitives, graph confidence/evidence metrics,
structured context guardrails, and benchmarkable recall quality.

### Highlights

- Added `lattice_brain.quality` for non-destructive Brain quality primitives:
  embedding fallback labels, drift/re-index plans, BM25 lexical scoring, hybrid
  fusion, reranker fallback contracts, memory candidate quality, graph edge
  quality metrics, structured context guardrails, and retrieval benchmark
  metrics.
- Scoped graph/search API reads across graph, node, neighborhood, relationship,
  keyword, vector, graph, and hybrid retrieval paths.
- Scoped Memory Manager prune/compact/clear operations to the caller's
  owner/workspace boundary and blocked unscoped graph clear.
- Added 6.4.0 Brain quality baseline/risk documentation and regression tests.
- Refreshed release screenshots and Living Brain walkthrough GIF under
  `output/release/v6.4.0/`.
- Synchronized package/runtime/static versions to `6.4.0`.

### Expected Artifacts

- `dist/ltcai-6.4.0-py3-none-any.whl`
- `dist/ltcai-6.4.0.tar.gz`
- `dist/ltcai-6.4.0.vsix`
- `ltcai-6.4.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_6.4.0_aarch64.dmg`

Full notes: [RELEASE_NOTES_v6.4.0.md](RELEASE_NOTES_v6.4.0.md)

## v6.3.1 - Access Runtime / i18n Follow-up

Lattice AI v6.3.1 follows the v6.3.0 hardening release with a focused
architecture and localization pass. It extracts access-control helpers from
`app_factory.py`, adds direct runtime tests, and routes Capture/Review Center
copy through shared Korean/English i18n keys.

### Highlights

- Extracted role lookup, bearer/cookie token extraction, current-user checks,
  admin checks, and public user projection into `latticeai.runtime.access_runtime`.
- Added focused unit tests for user/admin auth behavior and public identity
  projection.
- Localized Capture and Review Center tabs, labels, placeholders, empty states,
  feedback, and action copy.
- Refreshed release screenshots, GIF, and evidence index under
  `output/release/v6.3.1/`.
- Synchronized package/runtime/static versions to `6.3.1`.

### Expected Artifacts

- `dist/ltcai-6.3.1-py3-none-any.whl`
- `dist/ltcai-6.3.1.tar.gz`
- `dist/ltcai-6.3.1.vsix`
- `ltcai-6.3.1.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_6.3.1_aarch64.dmg`

Full notes: [RELEASE_NOTES_v6.3.1.md](RELEASE_NOTES_v6.3.1.md)

## v6.3.0 - Product Hardening Completion

Lattice AI v6.3.0 completes the hardening pass that followed the 6.2
decomposition work. It closes remaining UX, Review Center, app-factory,
compatibility-shim, i18n, and release-validation gaps while keeping package
publish and production deployment owner-run.

### Highlights

- Hardened Brain archive, memory provenance, document ingestion, Review Center,
  and local model runtime UX surfaces.
- Preserved Review Center Run Now as preview/regenerate, with scoped backend
  contract coverage and run id back-link assertions.
- Moved Review Center run-now wiring out of `app_factory.py` into a focused
  runtime helper.
- Added legacy root shim smoke coverage for server, knowledge graph, CLI,
  Telegram, and P-Reinforce compatibility paths.
- Synchronized package/runtime/static versions to `6.3.0`.

### Expected Artifacts

- `dist/ltcai-6.3.0-py3-none-any.whl`
- `dist/ltcai-6.3.0.tar.gz`
- `dist/ltcai-6.3.0.vsix`
- `ltcai-6.3.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_6.3.0_aarch64.dmg`

Full notes: [RELEASE_NOTES_v6.3.0.md](RELEASE_NOTES_v6.3.0.md)

## v6.2.0 - Product Decomposition / Release Smoke Automation

Lattice AI v6.2.0 reduces the largest product and runtime composition surfaces
while keeping the v6 Digital Brain behavior stable. The app shell, onboarding
flow, root legacy modules, router assembly, model consent copy, and release
smoke checks are split into smaller testable boundaries.

### Highlights

- Split Brain Home and Admin Console out of `App.tsx`.
- Split ProductFlow onboarding screens into dedicated components and removed
  inline layout styles from the onboarding surface.
- Added download size, storage location, external target, and do-later consent
  copy before model download starts.
- Moved root CLI, Telegram, and P-Reinforce bodies behind package modules while
  preserving root import/script compatibility shims.
- Added typed tool/interaction router contexts for app-factory decomposition
  without changing router registration order.
- Added `npm run release:smoke` for wheel install, npm tgz, static asset, and
  Tauri artifact smoke validation.
- Synchronized package/runtime/static versions to `6.2.0`.
- Refreshed README screenshots and walkthrough GIF under
  `output/release/v6.2.0/`.

### Expected Artifacts

- `dist/ltcai-6.2.0-py3-none-any.whl`
- `dist/ltcai-6.2.0.tar.gz`
- `dist/ltcai-6.2.0.vsix`
- `ltcai-6.2.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_6.2.0_aarch64.dmg`

Full notes: [RELEASE_NOTES_v6.2.0.md](RELEASE_NOTES_v6.2.0.md)

## v6.1.0 - Product Hardening / Digital Brain Completion

Lattice AI v6.1.0 tightens the v6 Digital Brain experience without opening a
new broad product surface. First-run can enter the local Brain before model
setup, Brain Home explains the first memory/backup loop, Review Center actions
are clearer, and backend hardening reinforces Brain Core isolation, model
download consent, CLI packaging, and runtime construction seams.

### Highlights

- Added a first-run path to create or open the Brain when model setup is
  deferred.
- Clarified the Brain Home first memory loop and backup/export ownership path.
- Clarified Review Center Run now semantics while preserving pending/snoozed
  review behavior.
- Added an AST import guard proving `lattice_brain` does not import
  `latticeai` or `ltcai`.
- Added model download consent unit coverage so token presence alone does not
  start an external download path.
- Moved pure CLI runtime helpers into `latticeai.cli.runtime` while preserving
  the root `ltcai_cli.py` entrypoint and wheel packaging.
- Added injectable ToolRegistry/ToolDispatch and chat AgentRuntime seams for
  app-factory decomposition without changing route order.
- Refreshed v6.1 release evidence under `output/release/v6.1.0/`.

### Expected Artifacts

- `dist/ltcai-6.1.0-py3-none-any.whl`
- `dist/ltcai-6.1.0.tar.gz`
- `dist/ltcai-6.1.0.vsix`
- `ltcai-6.1.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_6.1.0_aarch64.dmg`

Full notes: [RELEASE_NOTES_v6.1.0.md](RELEASE_NOTES_v6.1.0.md)

## v6.0.0 - Product Reset / Review Center Completion

Lattice AI v6.0.0 starts the product-quality reset branch by completing the
Review Center recovery loop: users can now see Snoozed automation suggestions,
bring them back with Unsnooze, and rely on generated OpenAPI schemas for the
review item contract.

### Highlights

- Added Pending, Snoozed, and All filters to the Review Center.
- Added explicit Unsnooze backend policy and API.
- Added frontend Unsnooze action and clearer `snoozed_until` copy.
- Moved Review Center UI into `frontend/src/features/review/`.
- Regenerated OpenAPI artifacts and switched ReviewItem client types to
  generated component schemas.
- Added v6 plan, architecture review, UX review, and quality scorecard docs.
- Synchronized package/runtime/static versions to `6.0.0`.

### Expected Artifacts

- `dist/ltcai-6.0.0-py3-none-any.whl`
- `dist/ltcai-6.0.0.tar.gz`
- `dist/ltcai-6.0.0.vsix`
- `ltcai-6.0.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_6.0.0_aarch64.dmg`

Full notes: [RELEASE_NOTES_v6.0.0.md](RELEASE_NOTES_v6.0.0.md)

## v5.6.0 - Brain Automation Review Center

Lattice AI v5.6.0 adds a Review Center for Brain automation. Scheduled and
Brain-event workflow output can now become workspace-scoped review items instead
of immediate user-visible decisions. Users inspect source, status, payload, and
provenance, then approve, dismiss, snooze, or run the automation again.

### Highlights

- Added `/automation/reviews` API with explicit ReviewItem response models.
- Added source-aware review items for `workflow_run`, `trigger`, and
  `kg_change_digest` automation output.
- Added guarded actions: approve, dismiss, snooze, and run_now.
- Preserved `run_now != approve`; reruns only update run provenance.
- Added TriggerService and RunExecutor opt-in enqueue via `review_queue: true`.
- Added Act > Runs > Review inbox with source filters, provenance detail, and
  empty/loading/error states.
- Regenerated OpenAPI artifacts and synchronized package/runtime/static versions
  to `5.6.0`.

### Expected Artifacts

- `dist/ltcai-5.6.0-py3-none-any.whl`
- `dist/ltcai-5.6.0.tar.gz`
- `dist/ltcai-5.6.0.vsix`
- `ltcai-5.6.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_5.6.0_aarch64.dmg`

Full notes: [RELEASE_NOTES_v5.6.0.md](RELEASE_NOTES_v5.6.0.md)

## v5.5.0 - Release Coordination

Lattice AI v5.5.0 completes the release coordination pass for the current
product line. It preserves the v5.4.0 Brain Automation Scheduler behavior while
synchronizing package/runtime/static metadata, lockfiles, release documentation,
and exact artifact names for the 5.5.0 line.

### Highlights

- Bumped synchronized Python, npm, VSIX, Tauri, runtime constants, lockfiles, and
  static metadata to `5.5.0`.
- Updated current-release README, RELEASE.md, docs/CHANGELOG.md,
  FEATURE_STATUS.md, vscode-extension/README.md, and release notes.
- Preserved consent-first Brain automation recipes, TriggerService dedup,
  LATTICE_TZ, degraded status, enabled:false disarming, runtime graph cleanup,
  and E2E scenario coverage from v5.4.0.
- Kept package publish and deployment owner-run only.

### Expected Artifacts

- `dist/ltcai-5.5.0-py3-none-any.whl`
- `dist/ltcai-5.5.0.tar.gz`
- `dist/ltcai-5.5.0.vsix`
- `ltcai-5.5.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_5.5.0_aarch64.dmg`

Full notes: [RELEASE_NOTES_v5.5.0.md](RELEASE_NOTES_v5.5.0.md)

## v5.4.0 - Brain Automation Scheduler

Lattice AI v5.4.0 adds consent-first Brain automation scheduler. Daily Memory
Digest, Weekly Project Review, and Follow-up Radar recipes can be created as
reviewable disabled drafts from the Automate page. Triggers stay disarmed until
explicitly enabled. Includes TriggerService hardening (dedup, LATTICE_TZ,
degraded status, provenance), runtime dependency graph cleanup, and E2E coverage.
All remote CI gates (Python 3.11/3.12, integration, visual, build, Vercel) passed
for PR #4 before this release prep.

### Highlights

- Added consent-first Brain automation recipe drafts (Daily Memory Digest,
  Weekly Project Review, Follow-up Radar) installed as disabled workflows.
- Automate page recipe cards with duplicate-guard, no-dup-click, success feedback.
- TriggerService: explicit enabled:false treated as disarmed, cooldown dedup
  guards, LATTICE_TZ support, consecutive_failures + degraded per-trigger status.
- lattice_brain/runtime 책임+의존성 그래프 정리 + entrypoint mapping docs.
- A-direction E2E scenarios for draft install, trigger fire, review flow.
- Bumped synchronized Python, npm, VSIX, Tauri, runtime, and static metadata to
  `5.4.0`.

### Expected Artifacts

- `dist/ltcai-5.4.0-py3-none-any.whl`
- `dist/ltcai-5.4.0.tar.gz`
- `dist/ltcai-5.4.0.vsix`
- `ltcai-5.4.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_5.4.0_aarch64.dmg`

Full notes: [RELEASE_NOTES_v5.4.0.md](RELEASE_NOTES_v5.4.0.md)

## v5.2.0 - User-Focused Model Transformation

Lattice AI v5.2.0 turns model choice into a transparent, user-facing capability
system. Users see which local models are load-ready, what hardware they fit,
what Hugging Face verification exists, and what download/load path will run
before they consent.

### Highlights

- Added a structured model capability registry with modality, hardware,
  download/load strategy, license, safety, and Hugging Face verification fields.
- Added automated HF registry verification and preserved modern multimodal
  candidates as registry entries while keeping the picker focused on load-ready
  families.
- Hardened the verification contract so `verified` requires HF presence plus
  config and tokenizer hints, with weights hints reported separately.
- Added workspace-scoped marketplace template install registry state to prevent
  cross-workspace install-status bleed.
- Updated the Library UI to show verified, multimodal, hardware, load strategy,
  license, and safety details before download/load consent.
- Captured fresh v5.2.0 screenshots, GIF, and WebM release evidence.
- Bumped synchronized Python, npm, VSIX, Tauri, runtime, and static metadata to
  `5.2.0`.

### Expected Artifacts

- `dist/ltcai-5.2.0-py3-none-any.whl`
- `dist/ltcai-5.2.0.tar.gz`
- `dist/ltcai-5.2.0.vsix`
- `ltcai-5.2.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_5.2.0_aarch64.dmg`

Full notes: [RELEASE_NOTES_v5.2.0.md](RELEASE_NOTES_v5.2.0.md)

## v5.1.0 - Product Trust & Clarity Release

Lattice AI v5.1.0 makes the product easier to understand and safer to trust:
it is a local-first private AI memory layer / Digital Brain where knowledge is
durable and models are replaceable.

### Highlights

- Rewrote the README first screen around the product reason to exist, practical
  use cases, local ownership, and model-agnostic Brain portability.
- Added `PRIVACY.md`, `docs/WHY_LATTICE.md`, and `docs/TRUST_MODEL.md`, and
  refreshed `FEATURE_STATUS.md`, `SECURITY.md`, and `ARCHITECTURE.md`.
- Removed `csp:null` from Tauri production config and added an app-shell CSP
  header validation gate.
- Centralized secret redaction for logs, audit payloads, security exports, and
  builtin hook packets.
- Disabled silent chat auto-file reads and required explicit consent for model
  download requests.
- Added `app_factory.py` builder seams for config, security, and Brain runtime
  construction.
- Bumped synchronized Python, npm, VSIX, Tauri, runtime, and static metadata to
  `5.1.0`.

### Expected Artifacts

- `dist/ltcai-5.1.0-py3-none-any.whl`
- `dist/ltcai-5.1.0.tar.gz`
- `dist/ltcai-5.1.0.vsix`
- `ltcai-5.1.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_5.1.0_aarch64.dmg`

Full notes: [RELEASE_NOTES_v5.1.0.md](RELEASE_NOTES_v5.1.0.md)

## v5.0.0 - Multilingual Brain Foundation Release

Lattice AI v5.0.0 starts the major-version cleanup line by making the product
usable in Korean or English from first launch through Brain exploration, while
preserving the existing AgentRuntime, ToolRegistry, Brain Core, Admin Console,
and graph foundations.

### Highlights

- Added a persisted Korean/English language choice to first-run onboarding,
  Brain home, graph exploration, and the Admin Console header.
- Localized login, environment analysis, model recommendation, model
  install/load, Brain quick views, starter prompts, save feedback, and graph
  fallback copy.
- Kept the technical-debt plan explicit: config centralization, KG
  stabilization, ToolRegistry characterization, AgentRuntime extraction, then
  app factory decomposition.
- Bumped synchronized Python, npm, VSIX, Tauri, runtime, and static metadata to
  `5.0.0`.

### Expected Artifacts

- `dist/ltcai-5.0.0-py3-none-any.whl`
- `dist/ltcai-5.0.0.tar.gz`
- `dist/ltcai-5.0.0.vsix`
- `ltcai-5.0.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_5.0.0_aarch64.dmg`

Full notes: [RELEASE_NOTES_v5.0.0.md](RELEASE_NOTES_v5.0.0.md)

## v4.7.2 - Intuitive Brain UX Release

Lattice AI v4.7.2 keeps the user experience centered on the Living Brain while
making it more direct for everyday users. First-run login no longer turns a
saved-user typo into a new empty Brain, model setup has a one-click recommended
path, and the Brain home exposes memory, topics, relationships, and the full
graph without requiring repeated exploratory clicks.

### Highlights

- Added saved-profile guards for email mismatch and wrong saved-user password.
- Added one-click recommended model setup and clearer large-download language.
- Added direct Brain view buttons: Memory, Topic, Relationship, and Graph.
- Added Brain overview cards for recent memories, older memories, and major
  topics, plus saved-to-memory feedback after chat.
- Refreshed visual validation and release evidence for the more intuitive Brain
  flow.
- Bumped synchronized Python, npm, VSIX, Tauri, runtime, and static metadata to
  `4.7.2`.

### Expected Artifacts

- `dist/ltcai-4.7.2-py3-none-any.whl`
- `dist/ltcai-4.7.2.tar.gz`
- `dist/ltcai-4.7.2.vsix`
- `ltcai-4.7.2.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.7.2_aarch64.dmg`

Full notes: [RELEASE_NOTES_v4.7.2.md](RELEASE_NOTES_v4.7.2.md)

## v4.7.1 - Admin Operations Release

Lattice AI v4.7.1 keeps the user experience centered on the Living Brain while
moving operational work into a separate Admin Console. Users get a simpler Brain
home; administrators get users, logs, security events, policies, and Brain
index operations without crowding the conversation surface.

### Highlights

- Added role permission visibility, audit log search, and severity filtering to
  the dedicated `#/admin` console.
- Added `/admin/log-retention` for local retention posture, retained events,
  prune candidates, and export-before-prune status.
- Split Admin Console data loading into a dedicated frontend hook so user Brain
  state and admin observability state do not share UI runtime state.
- Refreshed visual validation and release evidence for the separated admin
  experience.
- Bumped synchronized Python, npm, VSIX, Tauri, runtime, and static metadata to
  `4.7.1`.

### Expected Artifacts

- `dist/ltcai-4.7.1-py3-none-any.whl`
- `dist/ltcai-4.7.1.tar.gz`
- `dist/ltcai-4.7.1.vsix`
- `ltcai-4.7.1.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.7.1_aarch64.dmg`

Full notes: [RELEASE_NOTES_v4.7.1.md](RELEASE_NOTES_v4.7.1.md)

## v4.6.1 - Living Brain Release Refresh

Lattice AI v4.6.1 is the publishable Living Brain release refresh. It preserves
the v4.6.0 Living Brain implementation while moving the release artifacts and
owner publishing commands to `4.6.1` because PyPI versions/files are immutable
once published or reserved.

### Highlights

- Bumped synchronized Python, npm, VSIX, Tauri, runtime, and static metadata to
  `4.6.1`.
- Refreshed README around the current Login -> Environment Analysis ->
  Recommended Models -> Install & Load -> Brain Chat flow.
- Captured fresh Living Brain screenshots/GIF evidence for Brain Chat and the
  five Brain depths.
- Updated architecture and release docs without redesigning the backend
  architecture.
- Kept tracked release notes visible from v4.5.0 through v5.1.0.

### Expected Artifacts

- `dist/ltcai-4.6.1-py3-none-any.whl`
- `dist/ltcai-4.6.1.tar.gz`
- `dist/ltcai-4.6.1.vsix`
- `ltcai-4.6.1.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.6.1_aarch64.dmg`

Full notes: [RELEASE_NOTES_v4.6.1.md](RELEASE_NOTES_v4.6.1.md)

## v4.6.0 - Living Brain Experience

Lattice AI v4.6.0 makes the Brain the product. First launch now moves through
Login, environment analysis, recommended models, guided install/load, and then
the living Brain conversation; memories, knowledge, relationships, and the
advanced graph sit behind progressive disclosure.

### Highlights

- First launch opens to Login only, then a friendly setup flow that recommends
  models instead of exposing a catalog.
- Home after model load opens directly into Brain plus conversation instead of
  a graph, dashboard, or status surface.
- The living Brain remains visible while chatting and reacts to listening,
  recall, thinking, planning, and agent/workflow activity.
- `/ask` and `/chat` remain compatible routes but now land in the Brain
  conversation.
- Primary navigation is reduced to Brain, Memory, Files, Automations, Models,
  and Settings.
- The graph is preserved as advanced exploration at the deepest Brain layer.

### Expected Artifacts

- `dist/ltcai-4.6.0-py3-none-any.whl`
- `dist/ltcai-4.6.0.tar.gz`
- `dist/ltcai-4.6.0.vsix`
- `ltcai-4.6.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.6.0_aarch64.dmg`

Full notes: [RELEASE_NOTES_v4.6.0.md](RELEASE_NOTES_v4.6.0.md)

## v4.5.1 RC - Product Reimagining

Lattice AI v4.5.1 replaces the desktop product surface on top of the v4.5.0
capability recovery. It preserves Brain Core, StorageEngine, FastAPI, Tauri,
backup/restore, model runtime, and portability behavior while changing the
visible shell, navigation, onboarding, hierarchy, and visual language.

### Highlights

- Home, Ask, Add, Automate, Library, and Care replace the prior dashboard
  navigation as the primary product model.
- First-run setup becomes a non-technical journey: Make it yours, Choose a
  space, Meet your Mac, Pick a brain, Install locally, Try a question, Set the
  pace, Explore memory.
- The app shell now uses a compact premium desktop chrome, command palette,
  responsive mobile drawer, and ambient brain canvas.
- Global styling moves to a calmer Digital Brain palette with fixed responsive
  type sizing and 8px-or-smaller card radii.
- Legacy hash routes continue to resolve into the replacement SPA.

### Expected Artifacts

- `dist/ltcai-4.5.1-py3-none-any.whl`
- `dist/ltcai-4.5.1.tar.gz`
- `dist/ltcai-4.5.1.vsix`
- `ltcai-4.5.1.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.5.1_aarch64.dmg`

Full notes: [RELEASE_NOTES_v4.5.1.md](RELEASE_NOTES_v4.5.1.md)

## v4.5.0 RC - Product Experience Recovery

Lattice AI v4.5.0 restores the original end-user setup and model experience on
top of the v4.4.0 physical Brain extraction. It does not redesign
`lattice_brain`, StorageEngine, FastAPI, Tauri, backup/restore, or portability
architecture. This RC builds validated artifacts only; it does not tag, create a
GitHub Release, publish packages, or deploy.

### Highlights

- First-run setup now surfaces Login -> Workspace Selection -> Environment
  Analysis -> Model Recommendation -> Model Installation -> Model Validation ->
  Mode Selection -> Brain Usage from the app shell and command palette.
- Library Models exposes the existing prepare/load stream as a readable setup
  flow: Environment Analysis, Recommended Models, Install, Download Progress,
  Validate, Load, and Ready.
- Runtime install/model download remains explicit-consent only. No model files
  are downloaded and no local runtime installation starts from token/model
  presence alone.
- Gemma 4 MLX models are blocked from "ready" when installed MLX-VLM lacks the
  Gemma 4 `gemma4_unified` component. Users see friendly recovery guidance and
  alternatives such as Qwen3-VL local models or Gemma 4 GGUF through local
  server runtimes.
- Basic mode hides developer endpoint/module leakage in status badges, graph
  copy, model cards, and computer readiness while Advanced/Admin retain
  inspection detail.

### Expected Artifacts

- `dist/ltcai-4.5.0-py3-none-any.whl`
- `dist/ltcai-4.5.0.tar.gz`
- `dist/ltcai-4.5.0.vsix`
- `ltcai-4.5.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.5.0_aarch64.dmg`

Full notes: [RELEASE_NOTES_v4.5.0.md](RELEASE_NOTES_v4.5.0.md)
