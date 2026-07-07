# Changelog

The top entry is either the current unreleased main-branch work or the current
release line. Older entries are historical and may describe behavior as it
existed at that release.

## [Unreleased]

### Added
- Added Brain Brief suggested questions that turn current memory, recall proof,
  graph concepts, and conversation history into clickable first-screen prompts.
- Suggested Brain questions now send immediately from the first screen instead
  of only filling the composer.
- Added one-click follow-up prompts under the latest Brain answer for turning a
  reply into a checklist, evidence review, or prioritized next steps.

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

### Fixed
- Added regression coverage for provider API-key lookup/storage behavior,
  including keyring precedence, plaintext fallback gating, legacy plaintext
  cleanup after keyring writes, and identity creation on plaintext fallback.
- Added regression coverage for Computer Use policy enforcement, audit-safe
  typed-text metadata, and direct local-write system-prefix blocking.

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
  preserving historical 7.x entries.

### Fixed
- Made logical Knowledge Graph `replace` imports transactional so malformed
  imports roll back without clearing the existing graph.
- Locked v2 read-equivalence coverage for `list_documents`, `get_node`,
  `relationship_search`, and `traverse`.
- Preserved colliding legacy edge labels during logical import/backfill while
  keeping native write-door synonym dedupe canonical.

## [7.9.0] - 2026-06-23

### Changed
- Added `SingleAgentRuntime` as the explicit name for the legacy single-agent
  state machine while preserving `AgentRuntime` as a compatibility alias.
- Updated tool dispatch to build `SingleAgentRuntime` directly.
- Moved single-agent git rollback behind an injected `rollback_file` port owned
  by `ToolDispatchService`, keeping shell execution out of the core state
  machine.
- Added a shared `runtime-boundary/v1` descriptor so product and single-agent
  runtime boundaries are machine-readable in config/tests.
- Added `RuntimeBoundaryProtocol` as the minimal shared inspection surface for
  runtime-boundary-aware dependency injection.
- Updated architecture and product readiness targets to 7.9.0.

## [7.8.0] - 2026-06-22

### Changed
- Rebuilt Brain Chat Home around immediate conversation: chat purpose, starter
  prompts, and the composer now occupy the first viewport.
- Collapsed source ingestion, readiness, proof, timeline, overview, model
  continuity, and care controls behind one utility drawer.
- Kept workspace navigation visible on the default Brain surface.
- Hid default depth controls until the user intentionally travels deeper.
- Integrated the six post-7.7 UX drafts into the canonical Brain experience:
  first-run value cards, stronger recommendation affordances, product-toned
  Brain Home copy, and routed legacy Brain conversation entry points to the
  canonical Brain Home surface.
- Removed obsolete Brain conversation and first-run guide components.
- Moved draft onboarding polish out of inline styles and into the shared design
  stylesheet with responsive behavior and bilingual copy.
- Updated architecture and product readiness targets to 7.8.0.
- Refreshed 7.8.0 release screenshots, walkthrough video/GIF, and capture
  notes under `output/release/v7.8.0/`.

## [7.7.0] - 2026-06-22

> 7.7.0 marks the complete, finished product stage for Lattice AI.
> After 7.6.0 architecture closure, this release polishes every surface so that anyone looking at the code, UI, docs, or running app immediately recognizes: "this is now a product".

Lattice AI v7.7 delivers the Living Brain as the undeniable center, production-grade runtime contracts, stable ToolRegistry, full ingestion-to-graph flows, bilingual professional UX, and zero-beta signals. Classifiers moved to Production/Stable. All prior gates remain enforced under finished product contract.

Package metadata, Tauri, frontend, Python all aligned to 7.7.0. UI/UX microcopy and signals updated to convey finished professional tool.

### Productization Highlights
- Extreme self + claude-code (pts_claudecode) used for polish, evaluation, iteration.
- "This is a product" bar: clear durable knowledge ownership, no loose ends.
- Validation: typecheck, unit, cargo, build scripts exercised.

### Changed
- Package/runtime/static metadata synchronized to 7.7.0.
- Development status to Production/Stable.
- All current-release references point to 7.7.0.

## [7.6.0] - 2026-06-22

> Brain-Centered UX & Architecture Closure. Incorporates the two local review
> files into the release line with a Wake Brain first-run surface, memory rings
> plus direct depth controls, and machine-checkable architecture readiness gates.

### Added
- Wake Brain first-run entry before owner/profile setup, reducing onboarding to
  the product promise first and the setup mechanics second.
- Concentric memory rings around Brain Home plus direct controls for Now, Memory,
  Topics, Relationships, and Full Graph navigation.
- `latticeai.services.architecture_readiness.architecture_readiness()` and
  `tests/unit/test_v76_review_completion.py` to keep AgentRuntime, ToolRegistry,
  Config, server decomposition, KG hardening, and Brain UX review closure under
  test.

### Changed
- Package/runtime/static metadata is synchronized to 7.6.0; package publish and
  deployment remain owner-run only.
- README and release docs now describe 7.6.0 as the current release and point to
  refreshed 7.6.0 screenshots, walkthrough GIF, and release evidence index under
  `output/release/v7.6.0/`.

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
- Refreshed README release evidence screenshots and walkthrough GIF under
  `output/release/v7.5.0/`.

### Changed
- Tauri Rust/CLI dependencies are updated within the Tauri 2 line, removing the
  old transitive `block v0.1.6` future-incompatibility warning.
- npm dependency overrides move `js-yaml` to a non-vulnerable version; `npm
  audit` reports 0 vulnerabilities.
- CI lint compatibility is restored for the Brain quality gate script.
- Local MLX model preparation now recognizes valid existing Hugging Face cache
  snapshots, avoiding an unnecessary re-download when the model already exists
  outside Lattice's managed `~/.ltcai/hf-models` directory.
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

- Completed ALL Recommended next refactor items from report #15 in this session:
  - Server decomp wave (model_runtime globals/wiring): model_loading.py for prepare_and_load and stream, _MODEL_RUNTIME_STATE, model_engines.
  - Deeper WorkspaceOSStore (timeline + plugins + snapshots + memory).
  - KG embed: set_embed_dim.
- All refactoring needed finished this session per AGENTS. 767 tests, builds, greps clean.

### Added (this session features)
- `vision_analyze` tool: new multimodal vision analysis tool using screenshot b64 + prompt. Leverages existing VLM support (image_data in generate). Added to computer-use agent prompt and general tools. Fits seamlessly with computer_use, agent runtime, tool registry, and VLM models without affecting text-only paths.
- More recent multimodal models in user recommendations (Llama 3.2 11B Vision, Phi-3.5 Vision, Qwen2.5-VL 7B, Moondream2) + family order update in model_recommendation. Expanded curated list in model_capability_registry for better local VLM choices on Apple Silicon and other.
- All additions checked for compatibility with existing KG (descriptions can be ingested), agents/tools dispatch, chat/computer_use (image pass-through), model rec logic, and non-multimodal fallbacks.

- Perfect completion of report #15 recommended refactor:
  - Server decomp wave: ModelRuntimeState class (not dict) for globals/wiring in model_runtime; sync_to_module_globals for compat; further clean.
  - Deeper WorkspaceOSStore: timeline/plugins managers fully delegated and composed (record, has_permission etc all through).
  - KG embed: set_embed_dim available for optional central handling.
- All changes preserve legacy exactly, full composition, small modules.
