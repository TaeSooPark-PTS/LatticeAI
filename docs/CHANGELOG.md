# Changelog

The top entry is either the current unreleased main-branch work or the current
release line. Older entries are historical and may describe behavior as it
existed at that release.

## [Unreleased]

No unreleased changes yet.

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
