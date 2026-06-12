# Changelog

## [4.2.0] - 2026-06-12

> Brain Core & Storage Rebuild release. The backend Digital Brain boundary is
> available through the independent `lattice_brain` package, while the v4.1.0
> frontend, FastAPI contracts, and SQLite user data remain compatible.

### Added

- `lattice_brain` import package with `BrainCore`, Knowledge Graph,
  conversation, memory/context, identity/network, archive, and storage facades.
- Pluggable storage layer: `StorageEngine`, `SQLiteEngine`, `PostgresEngine`,
  `DockerPostgresWizard`, and `SQLiteToPostgresMigrator`.
- sqlite-vec capability detection with honest `bruteforce-cosine` local vector
  search fallback.
- Opt-in Postgres/pgvector scale setup and non-destructive SQLite-to-Postgres
  migration planning/copy tooling.
- Live Docker-backed pgvector migration validation, including rowid-less FTS5
  shadow tables, row-count integrity, idempotent reruns, and fail-closed
  Postgres behavior.
- Encrypted `.latticebrain` archive create/restore support for the SQLite brain
  database and blob directory.
- FastAPI routes for storage status, consent-gated Docker setup,
  SQLite-to-Postgres migration, and encrypted archive create/restore.

### Changed

- FastAPI constructs the graph/conversation runtime through
  `lattice_brain.BrainCore`; root and `latticeai.brain.*` imports remain
  compatibility shims.
- OpenAPI client regenerated with 313 paths.
- System settings exposes API-backed storage status, Docker setup consent, and
  migration planning controls.
- Synchronized package/runtime versions to `4.2.0`, including Tauri config and
  `lattice_brain.__version__`.

### Expected Artifacts

- `dist/ltcai-4.2.0-py3-none-any.whl`
- `dist/ltcai-4.2.0.tar.gz`
- `dist/ltcai-4.2.0.vsix`
- `ltcai-4.2.0.tgz`

## [4.1.0] - 2026-06-12

> Frontend & Desktop Rebuild release candidate. The existing FastAPI backend,
> Brain Core, storage architecture, and agent/workflow runtime remain the
> source of truth; the frontend and desktop shell are replaced by a React/Vite
> desktop architecture.

### Added

- React + TypeScript + Vite SPA under `frontend/`, using TanStack Query,
  Zustand, React Flow, Cytoscape.js, Tailwind CSS, local shadcn-style
  primitives, and a generated OpenAPI TypeScript client.
- Tauri 2.0 desktop shell under `src-tauri/` that launches the local backend
  and exposes the backend origin to the SPA; Electron fallback shell retained
  under `desktop/electron/`.
- Primary graph-first navigation: Brain, Ask, Capture, Act, Library, System.
- OpenAPI export/generation script and frontend lint guard for generated-client
  usage, no-CDN static assets, and stale frontend references.

### Changed

- `/app` now serves the built React/Vite bundle from `static/app`.
- Legacy static v3 frontend assets and v3 build/lint scripts are removed after
  capability parity was migrated into the new React surfaces.
- Release/build scripts now build Vite app assets and preserve Python, npm, and
  VSIX packaging flows at version `4.1.0`.
- npm Python-backed scripts use `scripts/run_python.mjs` to prefer
  `LTCAI_PYTHON` or the repo virtualenv before falling back to system Python.

### Validation Scope

- Python compile check, ruff, unit tests, live integration tests, frontend lint,
  TypeScript build, Playwright visual tests, desktop shell checks, no-CDN
  verification, release artifact validation, wheel smoke, and npm pack dry-run.

### Expected Artifacts

- `dist/ltcai-4.1.0-py3-none-any.whl`
- `dist/ltcai-4.1.0.tar.gz`
- `dist/ltcai-4.1.0.vsix`
- `ltcai-4.1.0.tgz`

## [4.0.1] - 2026-06-12

> Digital Brain Platform maintenance release for commits on `main` after tag
> `v4.0.0`. This release does not publish to PyPI, npm, the VS Code
> Marketplace, or Open VSX; artifacts are built and attached to GitHub Release
> `v4.0.1` only.

### Added

- Durable async run executor for agent/workflow runs, including persisted
  queued/running/final states, realtime SSE progress, cooperative cancellation,
  and startup reconciliation of orphaned active runs.
- Stable user UUID migration, centralized policy enforcement, local invitation
  tokens, and SQLite-backed Workspace OS state with JSON compatibility
  mirroring.
- Complete `/app` SPA parity surfaces for account/profile/password, workspace
  and organization administration, invitations, snapshots/time-machine with
  merge-restore, activity/presence, run approvals/cancellation, workflow
  triggers, Brain Network pairing/push, chat context trace, and Knowledge Graph
  provenance coverage.

### Changed

- Retired legacy static HTML/CSS/JS UI pages and legacy visual specs. Legacy GET
  routes now redirect into the matching `/app` surface.
- Added en/ko i18n runtime coverage for the shell, routes, and new parity views,
  guarded by `scripts/lint_v3.mjs`.
- Bumped synchronized package/runtime versions to `4.0.1`.

### Expected Artifacts

- `dist/ltcai-4.0.1-py3-none-any.whl`
- `dist/ltcai-4.0.1.tar.gz`
- `dist/ltcai-4.0.1.vsix`
- `ltcai-4.0.1.tgz`

## [4.0.0] - 2026-06-12

> Digital Brain Platform. The Knowledge Graph is now the durable brain store
> spine: focused `latticeai/brain/` modules own graph storage, schema, ingestion,
> provenance, retrieval, and document structure, while root modules remain
> compatibility shims.

### Changed

- Decomposed the monolithic Knowledge Graph implementation into focused
  `latticeai/brain/` modules, with every new module kept below 1,500 lines.
- Flipped graph writes to `nodes_v2` / `edges_v2` as the authoritative write
  path; legacy tables are maintained as a compatibility projection.
- Added one-time pre-flip SQLite backup, `PRAGMA user_version=4`, schema-version
  reporting, and fail-closed protection for newer DB formats.
- Added the durable async run executor for agent/workflow runs: persisted
  queued/running/final states, realtime SSE progress, cooperative cancellation,
  and startup reconciliation of orphaned active runs.
- Added stable user UUID migration, centralized RBAC policy enforcement, local
  invitation tokens, and SQLite-backed Workspace OS state with JSON compatibility
  mirroring and no durable-history truncation.
- Retired the legacy static UI pages in favor of the v4 `/app` SPA. Legacy GET
  routes redirect into `/app`; new parity views cover token-native account,
  workspace/org management, invitations, snapshots/time-machine with
  merge-restore, activity/presence, run approvals/cancellation, workflow
  triggers, Brain Network peer pairing/push, chat context trace, and Knowledge
  Graph provenance coverage, with en/ko i18n gated by frontend lint.

## [3.6.0] - 2026-06-10

> Knowledge Graph First. The Knowledge Graph becomes the primary architecture:
> every data source converges into it through one unified ingestion pipeline, with
> formalized entities/relationships, browser/web inputs, local portability, and
> per-node provenance. Lattice AI is a Digital Brain Platform — the graph is the
> durable asset; models read it and are replaceable.

### Added

- Unified ingestion pipeline (`latticeai/services/ingestion.py`): one entrypoint
  for files, folders, web URLs, browser tabs, and text — idempotent by content
  hash, bracketed by `pre_tool`/`post_tool`.
- Knowledge Graph entities `Source`/`Repository`/`Meeting`/`Organization`/
  `Workflow`/`Agent` and relationships `indexed_from`/`modified_by`/
  `belongs_to_project`/`part_of`/`discussed_in`/`decided_by`/`generated_by`/
  `used_by_agent` (additive, lossless `from_legacy`).
- Browser & web ingestion routes (`/api/browser/read-url`, `/ingest-current-tab`)
  and a Manifest V3 extension scaffold that posts only to `127.0.0.1`.
- Knowledge Graph export/import (versioned JSON) and binary backup/restore
  (`latticeai/services/kg_portability.py`,
  `/api/knowledge-graph/{export,import,backup,restore,portability,provenance}`).
- Provenance trail (`ingestion_provenance` table + query API) — every node is
  explainable.
- Knowledge Graph UI tabs: Status, Sources, Capture, Backup.

### Changed

- KG ingestion now fires the tool hook lifecycle (closes the v3.5.0 gap);
  coverage documented in `docs/RUNTIME_HOOK_COVERAGE_v3.6.0.md`.
- README repositioned as a Digital Brain Platform; Vercel remains landing-only.

## [3.3.1] - 2026-06-08

> v3.3.1 — Visual Product Rebuild. The `/app` frontend keeps the same runtime
> behavior but receives a new product shell, navigation hierarchy, visual token
> system, and readiness-focused primary views.

### Changed

- Rebuilt the global `/app` shell with a denser command rail, grouped
  Basic/Advanced/Admin navigation, local retrieval readiness footer, quiet
  topbar, and mode-aware command palette.
- Reorganized production navigation to Basic (Home, Chat, Files, Search,
  Knowledge, Memory, Models, Settings), Advanced (Agents, Workflows, Skills,
  Hooks, MCP), and Admin (Users, Permissions, Audit Logs, Security, Policies,
  Private VPC). Compatibility-only views remain deep-linkable.
- Replaced the v3.3.0 palette with cooler neutral light/dark tokens, tightened
  cards/panels to an 8px radius system, and compacted buttons, inputs, stats,
  tables, segmented controls, and empty states.
- Rebuilt Home as a local readiness dashboard for backend, model, retrieval,
  memory, connected sources, stats, and recent activity when available.
- Clarified Files manual upload versus desktop local-agent folder connection.
- Added Settings runtime readiness for backend, desktop local agent, model
  runtime, host telemetry, and embedding provider configuration.
- Fixed Chat send/stop button wiring so streaming uses a stable handler.

### Added

- `VISUAL_REBUILD_NOTES_v3.3.1.md` with implementation and QA notes.
- `FIGMA_SPEC.md` as the in-repo Figma-equivalent design spec for v3.3.1.

### Validation

- `npm run build:assets` regenerated content-hashed v3 assets at `3.3.1`.
- Package publication, deployment, tags, and GitHub Release creation were not
  performed.

## [3.2.0] - 2026-06-08

> v3.2 — Feature-Complete Platform. Multi-agent collaboration, an agent
> registry, marketplace + templates, workflow agents, autonomous planning, a
> long-term memory platform + manager, and skills/hooks/tool/MCP registries are
> all operable from `/app`. Enterprise (SSO/SCIM/RBAC/compliance/DLP/VPC/
> governance/multi-tenant controls) remains future work.

### Added

- **Agent Registry** — `latticeai/core/agent_registry.py` +
  `/agents/api/registry*`: built-in roles projected from `multi_agent`,
  persisted custom agents, capability discovery, and per-agent config.
- **Hooks platform** — `latticeai/core/hooks.py` + `/api/hooks/*`: persisted
  lifecycle registry (pre_run/post_run/pre_tool/post_tool/agent/pipeline/
  workflow) with enable/disable/reorder/register/inspect.
- **Long-term memory platform + Memory Manager** —
  `latticeai/services/memory_service.py` + `/api/memory/*`: unifies workspace
  memories, agent snapshots, conversation history, and KG graph/vector behind
  one façade; recall/inspect/prune/compact/rebuild/clear + usage/sources/health.
- **Agent templates** — five named templates in `latticeai/core/marketplace.py`
  plus a `clone` endpoint; `MARKETPLACE_VERSION`/`PLUGIN_SDK_VERSION`/
  `MULTI_AGENT_VERSION` → `3.2.0`.
- **MCP Manager surface** — `create_mcp_router` mounted through the tools router
  in `server_app`, reviving `/mcp/*`, `/skills/marketplace`,
  `/plugins/directory`, `/mcp/call`.
- **Eight `/app` views** — memory, planning, workflows, marketplace, skills,
  hooks, tools, mcp; a Platform nav group; fallback-safe `api.js` adapters.
- **Release claim audit** — `docs/V3_2_AUDIT.md` records a strict 20-claim
  PASS/PARTIAL/FAIL matrix with implementation evidence, fixes, validation,
  artifact readiness, and release metadata policy.

### Changed

- Version bumped to 3.2.0 across Python, npm, the VS Code extension, the v3
  asset manifest, and runtime version constants.
- `/app#/agents` now exposes the Agent Registry API directly, including
  registry metadata, capability discovery, enablement, and custom-agent
  registration.
- `/app#/skills` now normalizes the live `/workspace/skills` response shape
  (`installed`, `available`, and object/array `registry`) instead of only a
  legacy `skills` array.
- MCP/skills/plugin-directory routes are mounted once through the tools router;
  route compatibility tests now guard against duplicate public path/method
  registrations.

### Notes

- Validation covered lint, typecheck, Python compile, 365 unit tests, live
  integration tests, 90 Playwright tests, real `/app` browser route validation,
  Python/npm/VSIX builds, and exact-version release artifact validation.
- No packages were published and nothing was deployed.

## [3.1.0] - 2026-06-07

> v3.1 — Mainline Product Platform Completion. `/app` is now the full
> non-enterprise local-first workspace: Classic pages are compatibility routes,
> production embedding profiles are explicit, AgentRuntime and registries are
> the integration boundaries, and v3 runtime assets are hash-manifested.

### Added

- **Hashed asset pipeline** — `npm run build:assets` writes
  `static/v3/asset-manifest.json`, hashed CSS/JS siblings, and import-rewritten
  ES modules. `/app` reads the manifest and loads hashed assets automatically.
- **Production embedding profiles** — local `bge-m3`, `nomic-embed-text`,
  `e5-large`, `gte-large`; Ollama `nomic-embed-text`, `mxbai-embed-large`,
  BGE-M3-compatible providers; MLX Apple Silicon profiles; and
  OpenAI-compatible `text-embedding-3-small` / `text-embedding-3-large`.
- **Native model lifecycle controls** — `/app#/models` now calls the real
  `/models/load` and `/models/unload/{model_id}` endpoints.

### Changed

- **Classic retirement** — normal user workflows no longer link to Classic
  Chat, Classic Runtime, or Classic Admin. Compatibility routes remain available
  for rollback/debug.
- **Truthful unavailable states** — v3 fallback adapters return empty
  unavailable payloads instead of sample data, fake counters, or fabricated
  health.
- **Release metadata** — package, npm, VS Code extension, Workspace OS, docs,
  and expected artifacts are aligned at `3.1.0`.

### Validation

- Release target: `npm run lint`, `npm run typecheck`, `npm run check:python`,
  backend/integration tests, Playwright visual tests, `python -m build`,
  `npm run build`, `npm pack`, VSIX package, and exact-version artifact
  validation.

## [3.0.0] - 2026-06-07

> v3 — Local-first AI Workspace Platform. The hybrid-search
> backend and the token-native `/app` workspace shell now ship together: the
> shell's adapters call the real v3 retrieval APIs, and Chat is a first-class
> native view (no link-out to the legacy page). Legacy `/chat` remains available
> as a rollback/debug path.

### Added — Backend retrieval

- **Hybrid search API** — added `/api/search/hybrid`, `/api/search/keyword`,
  `/api/search/vector`, `/api/graph`, `/api/graph/node`,
  `/api/graph/relationship`, `/api/index/status`, and `/api/index/rebuild`.
- **SQLite vector index** — added local deterministic embeddings,
  `vector_embeddings`, and `vector_index_operations` for incremental indexing,
  rebuilds, and status monitoring.
- **Embedding status** — the default `lattice-local-hash-v1` embedder is a
  deterministic local fallback, not a production semantic embedding model.
  Future providers may include Ollama, MLX, OpenAI-compatible providers, and
  other local embedding runtimes.
- **Graph retrieval helpers** — added node lookup, relationship search, bounded
  traversal, neighbor expansion, and service-level result fusion.
- **Backend architecture doc** — added `docs/V3_BACKEND_ARCHITECTURE.md` with
  storage, search, API, and migration details.

### Added — Native app shell (`/app`)

- **Unified app shell** (`static/v3/`) — nav rail, command palette (⌘K),
  workspace switcher (Personal/Organization), and mode switcher
  (Basic/Advanced/Admin); hash-routed views for every primary and admin area
  (Home, Chat, Knowledge Graph, Hybrid Search, Files, Pipeline, Agents, Models,
  My Computer, Settings, and Admin · Users/Permissions/Audit/Security/Policies/
  Private VPC).
- **Native Chat view** — a first-class 3-pane chat (conversations · thread ·
  retrieval context) wired to the real backend (`POST /chat` SSE + `/history/*`)
  with streaming, empty/error/loading states; surfaces Knowledge Graph, Vector,
  Hybrid Search, and indexed-file context per answer. The legacy `/chat` page
  stays reachable but is no longer the primary chat experience.
- **Primary entry behavior** — `/app` is the product entry after login and SSO;
  the PWA manifest starts at `/app`.
- **Retrieval identity** — Knowledge Graph + Vector Index + Hybrid Search are
  surfaced as a first-class "retrieval lattice" on Home and a live index chip.
- **Token-native design system** — `static/v3/css/lattice.*.css` built on top of
  `tokens.css` with no dependency on the legacy override layers; full light/dark
  and desktop/tablet/mobile support.
- **Integration adapters** — `static/v3/js/core/api.js` calls the real v3
  endpoints and degrades to clearly-badged sample data; no backend logic in the UI.

### Validation

- Backend coverage includes v3 indexing, migration status, vector retrieval,
  graph relationship traversal, hybrid result fusion, and API contract tests.
- Frontend coverage: `tests/visual/v3.spec.js` and `scripts/lint_v3.mjs` (wired
  into `npm run lint`); see `docs/V3_FRONTEND.md` for IA + design decisions.
- Release preparation builds exact `3.0.0` Python, npm, and VSIX artifacts.
  Package-store publication remains manual and is not triggered by pushing the
  release tag.

> Frontend Product Shell Redesign — workspace navigation, auth entry, and shared
> product surfaces were realigned around the local-first AI workspace model
> without changing backend contracts.

### Changed

- **Workspace IA** — the workspace shell now separates primary user workflows,
  admin controls, and runtime tooling, with Basic, Advanced, and Admin modes.
- **Navigation** — Chat and Workspace navigation now use consistent labels for
  Home, Chat, Knowledge Graph, Files, Pipeline, My Computer, Search, and
  organization administration.
- **Design tokens** — shared product surfaces moved away from the prior
  lavender-heavy treatment toward neutral work surfaces with blue, teal, and
  amber accents.
- **Auth surface** — account screens use the same token-native product shell as
  the workspace experience and hide decorative background elements.

### Validation

- Frontend validation includes lint, Python checks, browser-rendered workspace
  smoke checks, and Playwright visual regression coverage.
- Production build output was intentionally not generated for this frontend-only
  redesign pass.

## [2.2.7] - 2026-06-05

> Visual Stabilization Release — browser-rendered screens were reviewed and
> polished until Chat, onboarding, graph, Workspace OS, and operational panels
> felt like one product.

### Fixed

- **Chat composer haze** — removed the dark-mode white/milky bottom composer
  effect and the legacy inner textarea border; the shell now owns the focus
  state and the attachment/send controls stay readable.
- **Knowledge Graph canvas** — replaced the washed-out light graph work surface
  with an intentional dark canvas treatment.
- **Workspace OS dark surfaces** — relationship/list cards, inputs, tags,
  health cards, and capability cards no longer fall back to hardcoded white.
- **Onboarding/modals** — workspace select, PC environment analysis,
  recommendation result, auto setup, mode select, pipeline, My Computer,
  profile, settings, Private VPC, and model-state panels now share the same
  dark panel language.
- **Account dark contrast** — account/register logo text, inputs, and window
  controls remain readable in dark mode.

### Changed

- **Cache-busting** — all versioned frontend assets now use `?v=2.2.7`,
  including `/static/scripts/chat.js?v=2.2.7`.
- **Version sync** — Python package, npm package, VS Code extension, Workspace
  OS, lockfiles, and runtime metadata aligned at `2.2.7`.

### Validation

- Release target includes Python compile/pytest, npm lint/typecheck/test/build,
  Python build + twine check, npm pack, VSIX package, and Playwright visual QA.
- Package-store publishing remains manual; release artifacts are version-scoped
  and must use exact `2.2.7` filenames.

## [2.2.5] - 2026-06-04

> Release Hygiene Hotfix — dark-mode overlay clarity, modal state protection,
> static asset version alignment, favicon routing, and Telegram log redaction.

### Added

- **Modal manager** — Chat overlays now share one blocking-modal controller with
  Escape close, backdrop close, pagehide/navigation cleanup, and body scroll-lock
  restoration.
- **Favicon asset** — `static/favicon.ico` is packaged and served by
  `/favicon.ico`.
- **Sensitive-log helper** — Telegram bot tokens are normalized to
  `bot123:REDACTED` before HTTP, exception, or response text reaches logs.
- **Validation coverage** — unit tests for token masking and static release
  hygiene, plus Playwright checks for modal stack behavior and favicon response.

### Changed

- **Overlay theme tokens** — full-screen overlays use `--overlay-scrim` and no
  blur-heavy backdrop, preventing washed-out dark-mode UI.
- **Surface token coverage** — modal, drawer, file manager, My Computer,
  onboarding, model switcher, pipeline, and admin surfaces are remapped to
  semantic tokens (`--modal`, `--surface`, `--surface-elevated`, `--input`).
- **Cache-busting** — all versioned frontend assets now use `?v=2.2.5`, including
  `/static/scripts/chat.js?v=2.2.5`.
- **Version sync** — Python package, npm package, VS Code extension, Workspace
  OS, lockfiles, and runtime metadata aligned at `2.2.5`.

### Validation

- Release target includes Python compile/pytest, npm lint/typecheck/test/build,
  Python build + twine check, npm pack, VSIX package, and Playwright visual QA.
- Package-store publishing remains manual; release artifacts are version-scoped
  and must use exact `2.2.5` filenames.

## [2.2.1] - 2026-06-04

> Frontend / UX Overhaul Release — Lattice AI keeps feature behavior stable
> while improving responsive layout, theme handling, accessibility, graph UX,
> admin tables, file attachment, and release packaging readiness.

### Added

- **Light/dark mode** — OS detection, manual theme toggle, and persisted theme
  state.
- **File attachment UX** — drag-and-drop and screenshot paste support for
  attachments.
- **Knowledge Graph controls** — zoom buttons, fullscreen, minimap,
  relationship filter, mobile graph/card view, and theme-aware palette.

### Changed

- **Responsive UI** — phone/tablet/laptop/desktop/ultrawide/4K layouts now use
  mobile-first reflow. Content is not hidden on smaller screens.
- **Design tokens** — `static/css/tokens.css` is the single source of truth for
  UI tokens, and theme styling no longer depends on `!important`.
- **Accessibility** — 44px touch targets, `:focus-visible` focus rings,
  keyboard-safe chat composer behavior, iOS no-zoom inputs, and reduced-motion
  support.
- **Admin UX** — wide admin tables reflow to mobile cards with larger touch
  targets and light/dark support.
- **Model cards** — country, company, run mode, and internet usage are shown in
  plain language.
- **Manual release scripts** — local publish scripts now build exact-version
  artifacts before upload and validate the same artifact set used by CI.
- **Marketplace positioning** — README, VS Code Marketplace/Open VSX README,
  npm metadata, VSIX metadata, and release copy now use the local-first AI
  workspace / AI pipeline / Knowledge Graph / multi-agent workflow positioning.
- **Release media refresh** — v2.2.1 screenshots and demo GIF regenerated from
  the live local app under `docs/images/`.

### Validation

- Unit/integration suites, Python build, npm pack, VSIX package, and
  exact-version release artifact validation are the release targets.
- Package-store publishing remains manual and must use the exact 2.2.1
  filenames.

## [2.2.0] - 2026-06-04

> Multimodal-First Knowledge OS Release — Lattice AI is aligned around the
> Knowledge Graph, multimodal inputs, source disclosure, and Gemma-4-first model
> recommendations.

### Added

- **Source disclosure metadata** — recommended model catalog entries now include
  maker country, maker company, execution method, internet requirement, and
  model name.
- **Principle documents** — added root-level `PROJECT_PRINCIPLES.md`,
  `AI_PHILOSOPHY.md`, `MODEL_POLICY.md`, `KNOWLEDGE_GRAPH.md`,
  `RELEASE_NOTES.md`, `ARCHITECTURE.md`, and `CHANGELOG.md`.
- **Gemma-4 default path** — default local model configuration and recommendation
  aliases now center on Gemma 4 12B/31B multimodal models.

### Changed

- **README / architecture rewrite** — current docs now describe Lattice AI as an
  AI Knowledge Graph workspace rather than a chat app or model launcher.
- **Multimodal recommendation logic** — local recommendation catalogs and setup
  flows use current multimodal model families only: Gemma 4, Qwen3-VL, and
  Llama 4.
- **Mode language** — basic and advanced modes are feature-equivalent and differ
  by explanation level; admin mode remains the authority boundary.
- **Runtime policy** — Apple Silicon local execution now checks MLX-VLM instead
  of MLX-LM.
- **Version sync** — Python package, npm package, VS Code extension, Workspace
  OS, runtime constants, FastAPI app, and `/health` metadata aligned at `2.2.0`.

### Removed

- MLX-LM as a current local text-only recommendation/install path.
- Text-only low-spec fallback recommendations.
- Current recommendation entries for Gemma 2, Gemma 3, Qwen2.5-VL, SmolLM,
  Phi, Mistral, DeepSeek, GPT-OSS, and Llama 3.x.

### Validation

- Unit tests added/updated for multimodal catalog policy, source metadata,
  Gemma-4 aliases, and version metadata.
- Package-store publishing remains manual; release artifacts are version-scoped
  and must use exact filenames.

## [2.1.0] - 2026-06-01

> Agent Platform Maturity Release — v2.1 operationalizes the v2.0 platform
> without redesigning it. Agent handoff, context packets, review/retry loops,
> timeline replay, memory snapshots, planning records, marketplace templates,
> and realtime execution observability are now first-class and additive.

### Added

- **Explicit agent handoff** — handoff records now include `handoff_id`,
  source/target agent ids, reason, task summary, context packet, status, and
  timestamps. Handoffs are workspace-scoped, persisted, inspectable, and replayable.
- **Agent context packets** — structured transfer packets include objective,
  task summary, workspace/graph/memory/workflow context, plugin outputs,
  constraints, reviewer notes, and retry metadata with obvious secret fields
  redacted before persistence.
- **Review / retry loops** — Planner -> Executor -> Reviewer records plan review,
  reviewer outcomes (`approve`, `reject`, `retry`), retry history, retry limits,
  reviewer notes, and failure propagation.
- **Timeline / replay** — agent and workflow runs expose replay support through
  persisted frames that show actor, time, reason, input, output, and decision.
  UI pages add replay viewers for agent and workflow runs.
- **Agent memory and planning** — `short_term`, `workspace`, and `long_term`
  memory scopes are supported, memory snapshots are workspace-scoped and
  replayable, and agent plans persist with plan-review metadata.
- **Workflow / agent / plugin hardening** — plugin output enters agent context,
  agent output enters workflow output, retry paths are bounded, and failures
  propagate into run status and realtime events.
- **Marketplace foundation** — local Plugin, Workflow, and Agent templates with
  metadata, export/import, install hooks, and a template registry. No cloud
  marketplace service is introduced.
- **Realtime execution observability** — existing SSE feed emits
  `agent_started`, `handoff_created`, `handoff_accepted`, `handoff_completed`,
  `review_requested`, `review_approved`, `retry_requested`,
  `workflow_started`, `plugin_started`, `plugin_completed`, `execution_failed`,
  and related workspace-scoped events.

### Changed

- Python package, npm package, VS Code extension, workspace, FastAPI app, and
  `/health` version metadata aligned at `2.1.0`.
- Multi-Agent Runtime, Plugin SDK, Workflow Engine, and Realtime surface
  versions now report `2.1.0`.
- Platform UI pages for agents, workflows, plugins, and activity now expose
  handoff chains, review panels, retry history, replay, templates, and plugin
  execution visibility.

### Validation

- Unit coverage added for handoff/context persistence, review/retry history,
  memory snapshots, replay, workflow-agent-plugin output propagation,
  marketplace template install, and realtime execution events.
- Package-store publishing remains manual; release artifacts are version-scoped.

## [2.0.0] - 2026-06-01

> Multi-Agent Workflow Platform — Lattice AI becomes a local-first **Agentic
> Workspace Platform** with four integrated subsystems: Plugin SDK, Workflow
> Designer, Multi-Agent Runtime 2.0, and Realtime Collaboration. Backward
> compatible and additive: API paths/schemas, `server:app`,
> `latticeai.server_app.app`, CLI, Workspace/Chat/Model/MCP/KG APIs, existing
> skills/snapshots/memories/agent & workflow history, and VS Code extension
> commands remain stable. New workspace state keys (`plugin_registry`,
> `workflow_runs`) are backfilled on load via deep-merge — no destructive
> migration.

### Added

- **Plugin SDK** (`latticeai/core/plugins.py`, `latticeai/api/plugins.py`) —
  `plugin.json` manifest, an allow-listed permission model, discovery,
  validation, lifecycle (install/enable/disable/uninstall), and a permissioned
  execution boundary. Plugins **extend** the existing skill registry (installing
  a plugin registers its bundled skills) rather than replacing skills. Ships two
  example plugins (`plugins/hello-world`, `plugins/git-insights`). Routes under
  `/plugins/registry`, `/plugins/validate`, `/plugins/install`, `/plugins/enable`,
  `/plugins/disable`, `/plugins/uninstall`, `/plugins/execute`, page `/plugins/sdk`.
- **Workflow Designer** (`latticeai/core/workflow_engine.py`,
  `latticeai/api/workflow_designer.py`) — node-based workflows
  (trigger/tool/skill/plugin/agent/condition/output), validation, a bounded
  deterministic execution engine, run history, and JSON export/import. Legacy
  `steps`-list workflows are auto-normalized so pre-2.0 history still runs.
  Routes under `/workflows/api/*`, page `/workflows`.
- **Multi-Agent Runtime 2.0** (`latticeai/core/multi_agent.py`,
  `latticeai/api/agents.py`) — Planner/Executor/Reviewer/Researcher/Release role
  orchestration with handoff, bounded retry, and an observable timeline; runs
  persist to agent history + knowledge graph + timeline. Deterministic by
  default (no LLM required) with an injectable role runner. Routes under
  `/agents/api/*`, page `/agents`.
- **Realtime Collaboration** (`latticeai/core/realtime.py`,
  `latticeai/api/realtime.py`) — in-process pub/sub bus, presence, and an
  activity feed over SSE. Wired as the workspace `event_sink`, so every
  timeline event flows to the feed automatically. Workspace isolation enforced;
  single-user local mode preserved. Routes `/realtime/stream` (SSE),
  `/realtime/feed`, `/realtime/presence*`, page `/activity`.
- **Cross-system integration** (`latticeai/services/platform_runtime.py`) —
  workflows can run tools/skills/plugins/agents; agent runs can run
  plugins/workflows; graph entities link to workflow runs and agent runs; all
  activity surfaces in the unified timeline + realtime feed. Recursion is bounded
  by construction.
- **Platform UI** — `static/plugins.html`, `workflows.html`, `agents.html`,
  `activity.html` (+ shared `static/platform.css`, `static/scripts/platform.js`),
  linked from the Workspace dashboard.
- **Docs** — `docs/V2_ARCHITECTURE.md`, `docs/PLUGIN_SDK.md`,
  `docs/WORKFLOW_DESIGNER.md`, `docs/MULTI_AGENT_RUNTIME.md`,
  `docs/REALTIME_COLLABORATION.md`.

### Changed

- Python package, npm package, VS Code extension, workspace, FastAPI app, and
  `/health` version metadata aligned at `2.0.0`.
- `server_app` cross-system wiring extracted into
  `latticeai/services/platform_runtime.py` to keep the assembly file lean.

### Validation

- Unit (incl. new plugin/workflow/multi-agent/realtime suites), integration
  smoke, startup/import, route-compatibility (full v1.x baseline preserved),
  and release-artifact checks. Package-store publishing remains manual.

## [1.7.0] - 2026-06-01

> Graph & Collaboration Release — Graph Canvas interactions, Enterprise Admin
> UI, Skill Marketplace completion, Workspace Health, screenshot automation, and
> Playwright visual smoke coverage. Backward compatible: API paths/schemas,
> `server:app`, `latticeai.server_app.app`, CLI, Workspace/Chat/Model/MCP/KG
> APIs, and VS Code extension commands remain stable.

### Added

- **Graph Canvas** — node expand/collapse, focused subgraphs, relationship
  highlighting, shortest-path visualization, URL/node click-through navigation,
  and source/conversation actions. Uses existing graph/relationship APIs; no
  schema change or destructive migration.
- **Enterprise Admin UI** — `/admin#enterprise` now surfaces Admin Policies,
  Audit Export, SIEM Export preview, Organization Settings, and Enterprise
  Capability Status. Community remains fully functional and ungated.
- **Skill Marketplace completion** — install progress (Download → Validate →
  Ready), validation status, recommended/popular/update surfaces, version
  metadata, and source metadata.
- **Workspace Health Dashboard** — indexed files, graph nodes, graph
  relationships, installed skills, memory entries, agent runs, current model,
  last sync time, and workspace status.
- **Screenshot automation** — `scripts/capture/` contains reproducible
  Playwright capture scripts for workspace, graph, skills, enterprise, and
  onboarding screenshots.
- **Visual smoke tests** — `tests/visual/*` plus `.github/workflows/visual.yml`
  run Workspace, Graph, Skills, Organization, and Enterprise screen checks on
  PR/push and nightly schedule with failure artifacts.

### Changed

- Python package, npm package, VS Code extension, workspace, FastAPI app, and
  `/health` version metadata aligned at `1.7.0`.
- CI package validation is version-scoped instead of a broad `dist/*` check.

### Validation

- Unit, integration, startup/import, route compatibility, MCP, model endpoint,
  visual smoke, VSIX build, and release artifact validation are the release
  target checks. Package-store publish remains manual only.

## [1.6.0] - 2026-06-01

> Product Experience Deepening — user-facing UX (Knowledge Graph explorer,
> workspace summary, model recommendation 2.0, skill marketplace tabs, Enterprise
> capability panel) and a refresh of `docs/images/*` to **real captured UI**
> screenshots. Not a refactor: API paths, request/response schemas, `server:app`,
> CLI, MCP, and the Knowledge Graph contract are unchanged. The only code changes
> are additive frontend (`static/`) and version metadata.

### Added

- **Knowledge Graph Explorer (workspace)** — an Entity Explorer (importance-
  ranked entity cards + search) with a detail panel showing inbound/outbound
  relationships, related entities, and the shortest path back to you; plus a
  Recent Activity feed and a Workspace Memory feed. Built entirely on the existing
  `/knowledge-graph/graph` and `/workspace/relationships/*` endpoints (additive
  UI, no new API, no schema change).
- **Workspace summary & quick-switch** — a "Current Workspace" card (active
  workspace, role, members, scoped counts) and one-click switch chips, preserving
  `workspace_id` scoping and the owner/admin/member/viewer model.
- **Model Recommendation 2.0** — the onboarding recommendation panel now shows a
  machine summary (OS/RAM/GPU/engine), a "best for this PC" callout with the
  reason, estimated RAM, and next step, per-family status, and a cloud caution.
  Estimates are labelled and conservative.
- **Skill Marketplace tabs** — Recommended / Popular / Installed / Updates tabs
  with version, category, and source, plus install / enable / disable actions on
  the existing skill lifecycle API.
- **Enterprise capability panel** — a 12-capability status matrix in the workspace
  (Community reports all disabled; nothing gates a Community feature).

### Changed

- **Real UI visuals** — `docs/images/{hero.gif,onboarding,model-recommendation,
  workspace,graph,organization,skills,enterprise}` are now **real screenshots**
  captured from the running app with Playwright + headless Chrome (the v1.5.0
  set was structural diagrams). `architecture.png` remains a structural diagram.
  README references the new real screenshots with no broken links.
- Python package, npm package, VS Code extension, FastAPI app, and `/health`
  version metadata aligned at `1.6.0`.

### Validation

- Unit tests pass; route-compatibility, startup/import, streaming, model-endpoint,
  MCP/KG, and workspace/org permission tests preserved; `npm run check:python`
  green; new UI verified rendering in a real browser via Playwright; VSIX build
  verified. Test/build/packaging artifacts only — no package-store publish.

## [1.5.0] - 2026-06-01

> Unified Product Release — CI/VSIX recovery, hardware-aware local model
> recommendation, model-catalog extraction, an Enterprise PoC seam, and a
> product-page README with an up-to-date architecture diagram. The public route
> contract, schemas, `server:app`, CLI, UI, and VS Code integration are
> unchanged.

### Fixed

- **VSIX / `npm ci` (ETARGET)** — `vscode-extension/package-lock.json` pinned a
  non-existent `@azure/core-tracing@^1.4.0` (the registry's latest is `1.3.1`),
  breaking `npm ci` and the GitHub Actions VSIX build. The lockfile is
  regenerated so the published `^1.3.0` ranges resolve; `npm ci` → `npm run
  compile` → `vsce package` is green again.

### Added

- **Local model recommendation** — `latticeai/services/model_recommendation.py`
  classifies the model catalog into **recommended / compatible / not_recommended**
  from a detected system profile (OS/RAM/CPU/GPU/disk), grouped by family
  (Gemma, Qwen, Llama, Phi, DeepSeek, …). Exposed at `GET /models/recommendations`
  and folded into `/workspace/onboarding/model-recommendations` as a `catalog`
  field. Covered by `tests/unit/test_model_recommendation.py`.
- **Enterprise PoC surfaces** — `latticeai/core/enterprise_admin.py` plus
  `GET /admin/enterprise` and `GET /admin/enterprise/siem-export` provide admin
  policy, audit-export, SIEM-export-stub, and organization-settings views built
  on the existing capability seam. Community reports every Enterprise capability
  as disabled and never gates a Community feature
  (`tests/unit/test_enterprise_admin.py`).
- **DeepSeek family** — added to the Ollama and llama.cpp catalogs with
  identifiers chosen so the version-dedup filter is unaffected.

### Changed

- **Model catalog extraction** — the static catalog (`ENGINE_MODEL_CATALOG`,
  `ENGINE_INSTALLERS`, `MODEL_ENGINE_ALIASES`) and the pure version-dedup helpers
  moved to `latticeai/services/model_catalog.py`, re-exported by `model_runtime`
  for backward compatibility. `model_runtime.py` shrank from 1,973 to 1,721 lines
  (`tests/unit/test_model_catalog.py` pins the re-export identity).
- **README rewritten as a product page** — Why / Core Capabilities / Quick Start
  / Architecture / Current Release / Documentation, with structural diagrams
  (`docs/images/*`) and a current architecture diagram. Historical "New in 1.x"
  marketing blocks were removed from the README top (this changelog remains the
  version history).
- Python package, npm package, VS Code extension, FastAPI app, and `/health`
  version metadata aligned at `1.5.0`.

### Validation

- 266 unit tests pass; route-compatibility, import/startup, streaming, model
  endpoint, MCP/KG contract tests preserved; `npm run check:python` green; VSIX
  build verified. Test/build/packaging artifacts only — no package-store publish.

## [1.4.0] - 2026-05-31

> Server App Final Decomposition — chat, model runtime, tools/local/CU,
> permissions/upload, garden/setup/static, MCP, and KG glue extracted while
> preserving the public route contract.

### Added

- **Final decomposition guard** —
  `tests/unit/test_server_app_v14_decomposition.py` asserts
  `latticeai/server_app.py` stays under the 1,500-line target, new routers and
  services import independently, and version metadata is aligned.
- **New routers** — `latticeai/api/chat.py`, `latticeai/api/tools.py`,
  `latticeai/api/computer_use.py`, `latticeai/api/local_files.py`,
  `latticeai/api/permissions.py`, `latticeai/api/garden.py`,
  `latticeai/api/setup.py`, `latticeai/api/static_routes.py`, plus
  `latticeai/api/deps.py`.
- **New service seams** — `latticeai/services/model_runtime.py`,
  `latticeai/services/tool_dispatch.py`, `latticeai/services/upload_service.py`,
  and
  `latticeai/services/app_context.py`.

### Changed

- **server_app.py final decomposition** — reduced from 5,381 lines to 1,303
  lines. The file now owns FastAPI construction, lifespan, middleware, static
  mount, router wiring, and compatibility globals only.
- **Chat/history/agent extracted** — `/chat`, `/history*`, `/agent*`, streaming
  generator, document-generation session handling, Knowledge Graph context trace recording,
  and AgentRuntime wiring moved to `latticeai/api/chat.py` with behavior and
  SSE chunk format preserved.
- **Model runtime/provider extracted** — provider catalogs, engine aliases,
  install/download/pull/load/unload helpers, prepare-model streaming,
  compatibility smoke tests, runtime feature payloads, and cloud verification
  moved to `latticeai/services/model_runtime.py`.
- **Tools/local/CU/permissions/upload extracted** — `/tools/*` moved to
  `latticeai/api/tools.py`, `/local/*` and KG/local-knowledge router glue moved
  to `latticeai/api/local_files.py`, `/cu/*` moved to
  `latticeai/api/computer_use.py`, `/permissions/*` moved to
  `latticeai/api/permissions.py`, and `/upload/document` now delegates to
  `latticeai/services/upload_service.py`.
- **Garden/setup/static routes extracted** — `/garden*`, `/setup*`,
  `/permissions/open/*`, `/`, `/account`, `/chat`, `/admin`, `/status`,
  `/manifest.json`, `/sw.js`, and `/local/sysinfo` moved to dedicated routers.
- **Docs and release metadata aligned** — README current release conflict fixed,
  SECURITY supported versions updated, package metadata bumped to `1.4.0`, and
  publish docs avoid unsafe `dist/*` upload commands.

### Validation

- Route compatibility snapshot, import/startup checks, chat streaming contract,
  model endpoint presence, MCP/KG presence, v1.4 line-count/import/version
  guard, unit/integration suites, Python build, VSIX package, npm pack, twine
  check, and release artifact validation all pass for `1.4.0`.

## [1.3.0] - 2026-05-31

> Server app decomposition (phase 3) — safety-net suite first, then model & MCP router extraction.

### Added

- **Route-compatibility safety net** — `tests/unit/test_route_compatibility.py`
  freezes the full public route surface (209 paths) plus import/startup,
  streaming-contract, model/engine, and MCP/KG presence checks. Any dropped or
  renamed endpoint, broken import, or removed `StreamingResponse` now fails the
  suite immediately. This was built **before** moving code, per the decomposition
  plan.
- **Model / engine router** — `latticeai/api/models.py` (`create_models_router`)
  now owns `/models*`, `/engines*` (install / verify-cloud / pull-model /
  prepare-model[/stream]) and `/setup/set-api-key`. Heavy provider/runtime
  helpers remain injected from server_app (no import cycle, no new import-time
  side effects).
- **MCP / skills / plugins router** — `latticeai/api/mcp.py` (`create_mcp_router`)
  now owns `/mcp/*`, `/skills/*`, `/plugins/directory*`, and `/mcp/call`.
  Registry/tool symbols are imported directly from `mcp_registry` / `tools` /
  `tool_registry`; server_app-defined helpers are injected.

### Changed

- **server_app.py decomposition** — reduced from ~5,948 to ~5,382 lines by
  extracting the model/engine and MCP/skills/plugins clusters (and their
  request models) into the routers above. All API paths, request/response
  schemas, the `server:app` import path, CLI, UI, KG/Admin/Security routers, and
  VS Code integration are unchanged (asserted by the route snapshot test).
- Release metadata aligned to `1.3.0`; `/health` reports `1.3.0`.

### Notes

- The chat/streaming cluster, the `/tools/*` · `/cu/*` · `/local/*` ·
  `/upload` · `/permissions` clusters, and the ~1,700-line model/engine
  *provider helper* block remain in server_app and are scheduled for the next
  decomposition pass (the safety net now de-risks those moves). server_app.py is
  not yet under the 2,000-line target.
- CI hardening from 1.0.1/1.1.0 retained (VSIX compile guard, Node.js 24,
  version-scoped artifact validation — no `dist/*` glob).

## [1.2.0] - 2026-05-31

> Server app modularization (routers + service layer) and workspace/org guardrail hardening.

### Changed

- **server_app.py modularization (phase 2)** — reduced
  `latticeai/server_app.py` from ~6,585 to ~5,948 lines by extracting the
  workspace / Organization API and the health/engine-summary endpoints into
  dedicated routers backed by a new service layer. `server_app` now focuses on
  app assembly, lifespan, middleware, and router include. The historical
  `server:app` import path, all API paths, and request/response shapes are
  unchanged.
- **Workspace/Organization guardrails strengthened** — workspace-scoped reads
  and writes now go through `WorkspaceService`, which gates explicitly-named
  workspaces: non-members cannot read or write organization data, viewers
  cannot write, members can write, and only owners/admins manage members. The
  no-auth local-owner fallback for ownerless org workspaces is preserved, but a
  *named* stranger never bypasses membership. `set_active_workspace` continues
  to enforce membership.

### Added

- **New API routers** — `latticeai/api/workspace.py`
  (`create_workspace_router`) and `latticeai/api/health.py`
  (`create_health_router`), mirroring the existing auth/admin router-factory
  convention (no import cycle: routers receive dependencies, never import the
  app).
- **New service layer** — `latticeai/services/workspace_service.py`
  (`WorkspaceService`: scope resolution + permission guardrails),
  `latticeai/services/model_service.py` (`ModelService`: health/engine summary
  payloads), and `latticeai/services/chat_service.py` (`ChatService`: history +
  answer-trace seam; the streaming chat path is unchanged and now records traces
  through this façade).
- **Shared-global areas made explicit** — the local knowledge graph and
  installed skills remain machine-global shared state (not partitioned per
  workspace); this is now surfaced in `WorkspaceService.SHARED_GLOBAL_AREAS`,
  the `/workspace/os` summary (`shared_global_areas`), and code comments.
- **Startup/modularization tests** — `tests/unit/test_server_app_modularization.py`
  (import path, router registration, key route presence, no import cycle) and
  `tests/unit/test_workspace_service.py` (read/write/member guardrails).

### Notes

- Release metadata aligned to `1.2.0`; `APP_VERSION` continues to derive from
  `WORKSPACE_OS_VERSION` and `/health` reports `1.2.0`.
- CI release hardening from 1.0.1/1.1.0 is retained (VSIX compile guard, Node.js
  24, version-scoped artifact validation — no `dist/*` glob).

## [1.1.0] - 2026-05-31

> Organization Workspace foundation, open-core Enterprise seam, and CI/release hardening.

### Added

- **Organization Workspace foundation** — workspace now distinguishes
  `personal` and `organization` workspace types. A full workspace model
  (`workspace_id`, `name`, `type`, `owner_user_id`, `members`, `roles`,
  `settings`, `created_at`, `updated_at`, `status`) is stored in the existing
  local-first JSON store.
- **Organization Workspace API** — create org workspace, list workspaces, get
  workspace, update, archive (soft, non-destructive), add/remove member, update
  member role, get workspace summary, activate workspace, and an edition info
  endpoint, exposed under `/workspace/orgs/*`, `/workspace/registry`,
  `/workspace/activate`, and `/workspace/editions`.
- **Workspace roles and permissions** — `owner`, `admin`, `member`, `viewer`
  mapped to `read` / `write` / `manage_members` / `manage_workspace`. Owners and
  admins manage settings and members; members use the workspace; viewers are
  read-only. Personal workspaces always grant their local user owner rights.
- **Workspace-scoped data** — Snapshots, Memories, Agent runs, Workflows, answer
  Traces, and Timeline events now carry a `workspace_id`. Reads accept an
  optional `X-Workspace-Id` header / `workspace_id` query to scope results.
- **Enterprise extension seam (open-core)** — new `latticeai/core/enterprise.py`
  defines an `Edition` enum (`community`/`enterprise`), an
  `EnterpriseCapability` enum, and a runtime `CapabilityRegistry` that a future,
  separately-distributed Enterprise plugin can attach a provider to. The
  Community build ships **zero** enabled Enterprise capabilities and restricts no
  Community feature. Documented in `docs/ENTERPRISE.md` and
  `docs/EDITION_STRATEGY.md`.
- **Release artifact validator** — `scripts/validate_release_artifacts.py`
  verifies that exactly the expected `whl`/`tar.gz`/`vsix`/`tgz` exist for a
  single version, that internal versions match, that the VSIX contains
  `extension/out/extension.js`, and warns when `dist/` mixes other versions.
- **workspace UI** — Personal/Organization workspace switcher, current
  workspace indicator, and a minimal organization create / member / role panel
  wired into the existing workspace command center.

### Changed

- **CI / release hardening** — `release.yml` opts into Node.js 24
  (`FORCE_JAVASCRIPT_ACTIONS_TO_NODE24`) and bumps `actions/checkout@v5`,
  `actions/setup-node@v5`, `actions/setup-python@v6`. Artifact upload and
  `twine check` are now scoped to the tagged version only — never a `dist/*`
  glob — and the build runs the release artifact validator before upload.
- Existing 1.0.x workspace state is migrated non-destructively to the v1.1
  workspace model on load; legacy records map to the Personal workspace.
- Release metadata aligned to `1.1.0` across Python, npm, VS Code extension,
  FastAPI app metadata, and `/health`.

## [1.0.1] - 2026-05-31

> CI packaging fix for the VS Code extension build.

### Fixed

- **Release (build-only) VSIX packaging** — the `Build VSIX` job failed with
  `Extension entrypoint(s) missing: extension/out/extension.js` because the
  workflow ran `vsce package` without first compiling the TypeScript sources
  (`vscode-extension/out/` is gitignored and absent in a clean CI checkout).
- Added a `vscode:prepublish` → `compile` (`tsc -p .`) script to
  `vscode-extension/package.json` so `vsce package` always compiles the
  extension entrypoint, aligning the local and CI build paths.
- Updated `.github/workflows/release.yml` to run `npm run compile` and assert
  `out/extension.js` exists before packaging.

### Changed

- Release metadata aligned to `1.0.1` across Python, npm, VS Code extension,
  FastAPI app metadata, and `/health`.

## [1.0.0] - 2026-05-31

> AI workspace integration release.

### Added

- **workspace foundation** — new `/workspace` UI and `/workspace/*` API surface
  organize LatticeAI around Graph, Snapshot, Memory, Agent, Workflow, Skills,
  and Timeline areas while preserving existing chat, graph, admin, CLI, and MCP
  compatibility.
- **First-run onboarding wizard** — reentrant step state, completion API,
  hardware scan, model recommendations, folder connection state, and recovery
  from failed/skipped steps.
- **Knowledge Graph context answer trace** — each generated answer records source files,
  graph nodes, graph edges, confidence, retrieval metadata, graph jumps, and
  source jumps.
- **Local indexing dashboard** — indexed folder status, watcher state, success
  and failure counts, last scan time, graph node/edge totals, and pause/resume/
  remove operations.
- **Workspace snapshots and Time Machine** — immutable snapshots capture graph,
  chat, settings, indexed folders, and loaded model state. Snapshots can be
  listed, viewed by area, compared, and exported as ZIP artifacts.
- **Knowledge Diff** — Snapshot A/B comparison reports nodes added/removed/
  changed, edges added/removed, and decisions changed.
- **Personal Memory layer** — per-user preferences, decisions, working style,
  frequently used tools, and long-term memory with CRUD/search and graph links.
- **Multi-Agent Graph** — Planner, Executor, Reviewer, Researcher, and Release
  Agent entities plus agent run history and timeline recording.
- **Relationship Explorer** — inbound/outbound edge views, related entities, and
  shortest-path exploration for graph nodes.
- **Local Computer Memory** — defaults OFF, requires explicit approval, tracks
  activity summaries only after consent, and links approved records to graph.
- **Skill Marketplace registry** — install, uninstall, update, enable, disable,
  version tracking, and metadata state surfaced in the workspace.
- **Workflow Graph** — stores workflow timelines and searchable workflow graphs
  for repeatable actions such as Upload -> Summarize -> Generate -> Export.
- **VS Code workflow** — added Refactor Selection, Generate Tests, Send To
  Lattice, and Ask About Current File while preserving Explain Selection.

### Changed

- Release metadata aligned to `1.0.0` across Python, npm, VS Code extension,
  FastAPI app metadata, `/health`, README, changelog, and release docs.
- `KnowledgeGraphStore` gained non-destructive `remove_local_source()` for
  deleting only derived index/graph data while leaving user files untouched.

### Validation

- Unit, integration, Python build, npm build, VSIX packaging, and package
  verification were run for this release.

## [0.6.0] - 2026-05-31

> Runtime / registry / config extraction release.

### Changed

- **server.py thin entrypoint** — moved FastAPI app assembly and route wiring to
  `latticeai.server_app`; `server.py` now preserves the historical `server:app`
  import path for uvicorn, Docker, CLI scripts, and tests.
- **ToolRegistry ownership** — centralized tool dispatch, governance policies,
  permission views, MCP descriptions, prompt catalog text, and file-create
  metadata in `latticeai.core.tool_registry`. `tools.execute_tool()` delegates
  through the registry.
- **Agent prompts separated** — moved planner / executor / critic / memory
  updater prompts to `latticeai.core.agent_prompts`; `AgentRuntime` remains the
  injected state-machine core in `latticeai.core.agent`.
- **Release metadata** — bumped Python package, npm package, VS Code extension,
  FastAPI app, and `/health` version to `0.6.0`.

### Validation

- Full test suite: 202 passed.
- Python package build, `twine check`, npm pack, and VSIX package build verified.

## [0.5.1] - 2026-05-31

> KGStoreV2 정규화 스키마 + 마이그레이션 하드닝 + native API 정리(릴리스).

### Changed

- **KGStoreV2 정규화 스키마** — `attrs._kg` 패스스루 제거. legacy 자유문자열
  노드/엣지 타입을 무손실 `NodeType`/`EdgeType` superset으로 정규화해 `type`에
  저장하고 원본은 신규 `legacy_type` 칼럼에 보존. summary/metadata는 1급 칼럼으로
  승격. 엣지 정체성은 `(source,target,legacy_type)`로 키잉해 정규화 충돌로 인한
  엣지 소실 방지. `kgv2_*` 재구성 뷰가 legacy read를 byte-identical하게 유지.
- **마이그레이션 하드닝** — `_init_v2_schema`의 DROP→CREATE→VIEWS→BACKFILL→
  version-stamp 전 과정을 단일 트랜잭션(`BEGIN` + statement 단위 `_exec_script`)으로
  원자화. 중간 실패 시 전부 롤백되어 이전 프로젝션·`projection_version` 보존, 다음
  기동에서 재시도. 마이그레이션은 권위적 legacy `nodes`/`edges`를 절대 건드리지
  않음. 프로젝션은 legacy `title`/`summary`/`metadata_json`을 verbatim 저장해
  byte-faithful(절단·키 재정렬 제거, NULL summary round-trip).
- **KGStoreV2 native API 정리** — production 미사용인 native 데이터 API
  (`upsert_node`/`upsert_edge`/`get_node`/`list_nodes`/`neighbors`/`search_similar`)와
  `Node`/`Edge`/`Visibility` 모델, 관련 헬퍼(`validate_endpoints`,
  `EDGE_ENDPOINT_RULES`, `encode/decode_embedding`, `cosine`, row→model 변환)를
  제거. `KGStoreV2`는 스키마/초기화/heal/stats 지원 역할만 유지. `kg_schema.py`
  ~870→475줄. `test_document_generation`의 직접 `KGStoreV2` 의존을 제거하고
  스키마/production 경로 검증으로 전환.

### Removed

- dead code: `migrate_legacy_to_v2()` 및 관련 헬퍼/CLI `migrate` 서브커맨드,
  native KGStoreV2 데이터 API 및 모델, 미사용 import(`struct`/`uuid`/`dataclasses`
  /`datetime` 등).

### Internal

- dual-write 불변식 런타임 진단 `_v2_sync_report()` 추가. 단위 테스트 192 통과.

## [0.5.0] - 2026-05-31

> MLX 샘플링 API 호환성 버그 수정 + 릴리스 워크플로 build-only 전환.

### Fixed

- **MLX `temp` kwarg 제거 대응** — `llm_router.py`의 로컬 MLX 추론 경로(텍스트/
  비전, 동기/스트리밍, 문서 생성 4계열·총 8개 호출부)가 `mlx_lm.generate` /
  `mlx_vlm.generate`에 `temp=temperature`를 직접 넘기다가
  `generate_step() got an unexpected keyword argument 'temp'`로 실패하던 문제
  수정. mlx_lm ≥ 0.20 / mlx_vlm는 `temp` 키워드를 제거하고 `sampler` 콜러블을
  받도록 API가 바뀌었으므로, `make_sampler(temp=...)`로 만든 sampler를
  `sampler=`로 전달하도록 `_mlx_sampler()` 헬퍼를 도입.

### Changed

- **릴리스 워크플로 build-only 전환** — `.github/workflows/release.yml`이 v* 태그
  push 시 단위 테스트와 빌드 산출물 생성(`python -m build`, `twine check`,
  `npm pack`, `vsce package`)까지만 수행. `publish-pypi`/`publish-npm`/
  `publish-vscode`/`publish-ovsx` job과 GitHub Secrets 의존(`if: secrets.*`)을
  제거. 배포는 로컬에서 수동 인증 후 진행.

## [0.4.0] - 2026-05-31

> Knowledge Graph v2 read/write cutover — legacy/v2 동등성 보장, dual-write
> projection, deterministic ordering, 삭제 미러링 완성. 그래프 안정화 릴리스.

### Changed

- **KGStoreV2 read/write cutover 완료** — 그래프 read 메서드(`search`,
  `context_for_query`, `graph`, `neighbors`, `multi_hop_context`,
  `search_for_document_generation`, `stats`)와 write가 v2 store를 단일 경로로
  사용. `KnowledgeGraphStore` 공개 인터페이스는 시그니처·반환형 그대로 유지.
- **단일 read 코드 경로** — `_read_tables()`가 legacy 테이블 또는 v2 재구성
  뷰(`kgv2_nodes`/`kgv2_edges`)를 같은 코드로 조회. `LATTICEAI_KG_READ_V2`로
  소스 토글(기본 v2).

### Added

- **Dual-write projection** — `_upsert_node`/`_upsert_edge`가 동일 트랜잭션에서
  `nodes_v2`/`edges_v2`에 프로젝션 기록. legacy 타입 문자열을 v2 type 칼럼에
  보존하고 summary·원본 metadata_json을 `attrs._kg`에 보존해 결과 동등성 확보.
- **삭제 미러링** — `clear_all`, `delete_conversation`, 로컬 폴더 재인덱싱의
  모든 노드/엣지 삭제를 v2에 미러(`_v2_delete_nodes`/`_v2_delete_edges_from`,
  edges_v2 FK cascade 활용).
- **Deterministic ordering** — 모든 그래프 read의 `ORDER BY`에 `id ASC`
  tie-break 추가(엣지/이웃 쿼리 포함). legacy/v2 결과 순서가 항상 동일.
- **Legacy/V2 equivalence test suite** — `test_kg_v2_read_equivalence.py`(7개
  read + dual-write + 동률 timestamp + 재upsert + 삭제 반영),
  `test_kg_v2_backfill.py`(프로젝션·self-heal·idempotent).
- v2 스키마 self-heal — 구버전 init이 만든 *빈* v2 테이블의 컬럼 누락 시
  drop+recreate(행이 있으면 절대 drop 안 함).

### Internal

- agent 루프를 `latticeai/core/agent.py`(`AgentRuntime`+ports)로 추출, 앱 설정을
  `latticeai/core/config.py`(`Config.from_env`)로 단일화, `tools.py`에 tool
  registry 도입(`execute_tool` if/elif 제거). server.py 대폭 축소.
- 단위 테스트 181 passed.

## [0.3.2] - 2026-05-29

> 안정화 릴리스 — 모델 current 일관성, smoke test 3분류, 보안 대시보드 timezone
> 버그 수정, 자동 그래프 한국어 노이즈 개선, README 과장 표현 정리.

### Model loading & UI

- 웹 UI 모델 선택을 단일 흐름으로 통일(`selectModelByCard` → `prepareAndLoadModel`
  → smoke test → `current` 반영 → 채팅 가능 여부 표시). cloud(`loadSelectedModel`)
  경로도 백엔드 `current`를 단일 진실원으로 사용. "보이는 모델 ≠ 채팅에 쓰이는
  모델" 문제 제거.
- Smoke test 결과를 **ok / degraded / failed** 3분류로 확장
  (`model_compat.classify_smoke_response()`). 특수/role 토큰 누출, 폭주 반복,
  과도한 길이를 감지. `degraded`는 채팅은 가능하되 UI에 호환성 경고 표시.
  `/models/load`·`/engines/prepare-model/stream` 응답의 `compatibility_status`가
  3분류 값을 그대로 노출.

### Security dashboard

- **Timezone 버그 수정** — audit timestamp는 로컬 시간으로 기록되는데
  "events_today"는 UTC로 계산해 한국 사용자에게 날짜가 어긋나던 문제 수정.
  새 모듈 `latticeai/core/timezones.py`로 기준 시간대를 통일(`LATTICE_TZ` /
  `LTCAI_TZ` 환경변수, 기본 시스템 로컬). overview 응답에 `timezone` 필드 추가.

### Auto graph curator

- 한국어 노이즈 감소 — 조사 제거, 일반어/파일확장자 blacklist, 단일 출처
  후보 score 감점(여러 출처에서 반복된 개념만 승격).

### Docs & tests

- README/확장 설명의 과장 표현 완화(telemetry, skills/plugins 수치 등).
- 단위 테스트 추가: timezone, smoke 3분류, graph 노이즈, export secret redaction.
  (tests/unit 149 passed)

## [0.3.1] - 2026-05-29

> Model loading reliability + auto-graph curation + AI Security & Audit Command Center.
>
> 외부 리뷰 5건(모델 추천/다운로드, 사용자 직접 모델 선택, 모델 호환성 계층,
> 자동 그래프 방향, 관리자 보안/감사 대시보드) 피드백을 모두 반영했다.

### Model loading & inference

- 새 모듈 `latticeai/core/model_resolution.py` — `ModelResolution`이
  `input_id / engine / resolved_model / download_id / load_id / expected_current`을
  하나로 묶어 추천 카드, 다운로드, 로드, router cache, 프론트 current 표시가
  단계마다 어긋나는 문제를 제거.
- `prepare_and_load_model()` 와 `/engines/prepare-model/stream`이 동일한
  `ModelResolution`을 공유하도록 통합. LM Studio처럼 `instance_id`가 부여되는
  엔진은 `resolution.update_after_load()`로 후처리.
- 로드 직후 `_smoke_test_loaded_model()`가 한국어 짧은 채팅 테스트를 실행 →
  응답에 `ready_to_chat`, `compatibility_status`, `smoke_test` 필드 추가.
  Cloud 모델은 사용자 비용 발생을 피하기 위해 자동 skip.
- `/models` 응답에 `engine_options`(local_mlx / ollama / lmstudio / llamacpp /
  vllm 별 실제 model_id)와 `compat_profiles` 추가.
- 새 엔드포인트 `GET /models/compat-profiles`.

### Model compatibility layer

- 새 모듈 `latticeai/core/model_compat.py` — Family detection
  (gpt-oss / gemma / qwen / llama / mistral / phi / deepseek …),
  family 프로파일(stop tokens, disable_draft, postprocess, generation params),
  `fast_postprocess`, `validate_smoke_response`, `record_smoke_result`,
  `compat_cache`. 무거운 검사는 모델 로드 시 1회(Slow Path), 채팅 중에는
  캐시된 profile만 사용하는 Fast Path. 답변이 깨졌을 때만 1회 retry하는
  Recovery Path 구조.

### Auto knowledge graph curation

- 새 모듈 `latticeai/core/graph_curator.py` — 대화/파일/작업 로그에서
  Topic candidate 추출 → alias clustering(자동 병합) → promotion 결정
  (secret 차단, 중복 차단, 출처 최소치) → 파생 이야기 엣지 → 행동 시그널
  기반 큐레이션. Secret/API key/private key는 그래프 후보에서 자동 제거.

### Frontend — user-trusted current model

- `static/scripts/chat.js`의 `prepareAndLoadModel` 결과에서 백엔드
  `response.current`를 신뢰하고, `ready_to_chat=false` 또는
  `compatibility_status=degraded`일 때 사용자에게 호환성 경고 표시.
- 모델 카드를 직접 클릭할 때도 같은 표준 흐름을 타는
  `window.selectModelByCard()` 헬퍼 추가.

### Admin — AI Security & Audit Command Center

- 새 라우터 `latticeai/api/security_dashboard.py`가 11개 엔드포인트 추가:
  `/admin/security/{overview,users,events,events/{id},conversations/{id},`
  `conversations/{id}/raw,files,files/{id},files/{id}/content,raw,export}`.
- 모든 응답에서 hard secret(`sk-…`, `ghp_…`, `xoxb-…`, `AKIA…`,
  private key block 등)을 자동 redact. 원문/raw 조회는 별도
  `admin_view_sensitive_raw` 감사 이벤트로 기록.
- 관리자 UI: Security Overview 카드(오늘 이벤트, High Risk, 위험 채팅/파일,
  Secret/외부 전송 차단, 관리자 원문 조회 수, 검토 필요), User Risk Matrix
  (stacked bar), 민감정보 유형 donut chart, 민감 채팅/위험 파일 모니터,
  감사 타임라인, Raw Data Explorer.
- 사용자별 막대 클릭 → drill-down. JSON / CSV / XLSX / PDF / TXT
  추출 지원.

### Tests / CI

- 새 단위 테스트 28개 — `tests/unit/test_model_compat.py`,
  `tests/unit/test_model_resolution.py`, `tests/unit/test_graph_curator.py`,
  `tests/unit/test_security_dashboard.py`.
- `.github/workflows/ci.yml` syntax-check 단계에 4개 새 모듈 추가.
- 새 `.github/workflows/release.yml` — tag `v*` 푸시 시 PyPI / npm /
  VS Code Marketplace / Open VSX 자동 배포(필요 secrets: `PYPI_TOKEN`,
  `NPM_TOKEN`, `VSCE_PAT`, `OVSX_TOKEN`). 해당 secret이 비어 있는 job은
  자동 skip.

### Fixed

- FastAPI에서 `Request` 인자에 `= None` 디폴트 사용 시 발생하던 잠재 문제 수정
  (`security_dashboard.py` `/admin/security/raw`).
- `gpt-oss` family postprocess 순서를
  `trim_after_user_marker → strip_role_tokens`로 보정 — `<|user|>` 마커가
  먼저 제거돼 trim이 동작하지 않던 버그.

## [0.3.0] - 2026-05-27

### Knowledge Graph — LLM Structured Output Extraction

- `_extract_concepts()` / `_extract_triples()`를 LLM 기반으로 전환 (rule-based 폴백 유지)
- LLM Router 참조를 knowledge_graph에 주입하는 `set_llm_router()` 함수 추가
- `LATTICEAI_LLM_EXTRACTION` 환경변수로 LLM extraction on/off 제어

### Knowledge Graph — Hybrid Retrieval & Document Generation

- `search_for_document_generation()` 추가 — Hybrid Score (0.5×text + 0.3×graph + 0.2×recency) 기반 검색
- `multi_hop_context()` 추가 — Seed nodes에서 N-hop 그래프 탐색
- `DOCUMENT` NodeType, `USED_IN` / `INSPIRED_BY` / `CONTRADICTS` / `EVOLVES_FROM` EdgeType 추가
- Node에 `style`, `tone`, `importance_score`, `last_used` 필드 추가 (SQLite v2 스키마 반영)

### 문서 자동 생성 파이프라인

- `latticeai/core/context_builder.py` 신규 — Knowledge Graph → 구조화 Markdown Context 변환
- `latticeai/core/document_generator.py` 신규 — Intent detection + 전용 System Prompt + Session 관리
- `llm_router.py`에 `generate_document()` / `stream_generate_document()` 추가
- `/chat` 엔드포인트에서 "보고서 작성해줘" 같은 문서 생성 의도 자동 감지 → 전용 파이프라인 활성화
- 생성 문서에 참조 Knowledge Graph 노드 각주 자동 첨부
- 대화별 `DocumentGenerationSession`으로 반복 수정("이 부분 더 수정해") 지원

### UI/UX — 디자인 통일

- Account/Chat/Graph/Admin 전체 페이지를 통일된 lavender purple 테마로 전환
- 다크 모드 base 스타일 완전 제거 (`.app-layout` Obsidian dark, account dark base 등)
- 초록 테마(`#22d3a0`) 60+ 인스턴스를 보라(`#6f42e8`) 계열로 교체
- 메시지 버블: 다크 green → 보라 gradient(user), 밝은 lavender glass(AI)
- 사이드바, 입력창, 버튼, 모달 오버레이 모두 라이트 lavender로 통일
- 카드/패널에 hover lift 효과, 커스텀 스크롤바, focus ring, selection 색상 추가
- tokens.css에 글로벌 polish (scrollbar, selection, focus-visible) 추가

### 테스트

- `test_document_generation.py` 33개 테스트 추가 (intent detection, session, extraction, hybrid retrieval, context builder, schema v2)

### Release

- 배포 버전을 `0.3.0`으로 상향
- 대상 채널: `npm` · `PyPI` · `VS Code Marketplace` · `Open VSX`

## [0.2.2] - 2026-05-26

### 모델 카탈로그

- `GPT-OSS 20B`, `GPT-OSS 120B`, `Gemma 4 31B 4-bit`를 MLX/Ollama/vLLM/LM Studio/llama.cpp 모델 선택 및 다운로드/로드 흐름에 추가
- 엔진별 모델 목록에서 같은 패밀리의 최신 major/minor 버전이 있으면 낮은 버전 항목을 숨기도록 정리
- 설정 마법사 추천표와 RAM 티어에 새 모델을 반영

### 지식 그래프

- 로컬 폴더 스캔 시 PDF, Word, PowerPoint, Excel, CSV, 텍스트/코드, OCR 이미지 등 지원 파일은 실제 본문 텍스트가 추출된 경우에만 그래프 노드로 생성
- 빈 PDF/Word/PowerPoint/Excel 파일이나 OCR이 비어 있는 파일은 `skipped_empty_text`로 기록하고 그래프에는 표시하지 않도록 변경
- 기존 버전에서 파일명/상대경로만으로 만들어진 로컬 파일 노드는 다음 스캔에서 재추출 검증 후 자동 정리
- Word 표 셀, PowerPoint 슬라이드 텍스트, Excel 실제 셀 값 추출을 보강하고 파일명 기반 개념 추출을 제거

### UX

- 지식 그래프 오른쪽 사이드바의 하단 잘림 문제를 수정하고 데스크톱/모바일에서 패널, 메타데이터, 긴 경로가 자연스럽게 스크롤/줄바꿈되도록 조정

### Release

- 배포 버전을 `0.2.2`로 상향
- 대상 채널: `npm` · `PyPI` · `VS Code Marketplace` · `Open VSX`

## [0.2.1] - 2026-05-25

### 버그 수정

- **CI 테스트 import 수정** — `test_security.py`에서 `_rate_buckets` import 경로를 `server` → `latticeai.core.security`로 변경 (v0.2.0 모듈 분리에 따른 경로 변경 반영)

### Release
- 배포 버전을 `0.2.1`로 상향
- 대상 채널: `npm` · `PyPI` · `VS Code Marketplace` · `Open VSX`

---

## [0.2.0] - 2026-05-25

### server.py 모듈 분리 — latticeai/ 패키지 도입

server.py(6,815줄)에서 핵심 로직을 `latticeai/` 패키지로 분리하여 유지보수성을 개선했습니다.

**새 패키지 구조:**
```
latticeai/
  core/
    security.py    — 비밀번호 해싱, 레이트 리밋, IP 감지, 파일 매직 검증
    sessions.py    — 파일 기반 세션 저장소 (SessionStore 클래스)
    audit.py       — 감사 로깅, 민감정보 분석, 관리자 감사 리포트
  api/
    auth.py        — 인증/SSO/프로필 API 라우터 (register, login, logout, SSO, profile)
    admin.py       — 관리자 API 라우터 (dashboard, users, VPC, SSO, audit)
```

- server.py: 6,815줄 → 6,187줄 (628줄 감소, 868줄이 5개 모듈로 분산)
- 기존 API 호환성 100% 유지 — 모든 엔드포인트 경로와 응답 동일
- `knowledge_graph_api.py` / `local_knowledge_api.py`와 동일한 팩토리 라우터 패턴 사용

### README 전면 개편 — 사용자 경험 중심

- 핵심 메시지: "내 파일과 대화를 기억하고 연결하는 로컬 AI 워크스페이스"
- 기능 나열형 → 3분 워크플로 + Why 섹션 + 지식 그래프 설명
- 고급 기능(전체 기능표, 보안, 설정, API, 트러블슈팅)은 접기(details) 섹션으로 이동
- 비교표에 Knowledge Graph, Local Folder Indexing 항목 추가
- 모델 추천표에 최소 RAM 컬럼 추가
- 한국어 섹션도 경험 중심으로 재작성

### 보안 강화 — 패키지 설치 관리자 전용

- `/mcp/install`: `require_user` → `require_admin` + 감사 로그
- `/skills/install`: `require_user` → `require_admin` + 감사 로그
- `/mcp/custom` POST: `require_user` → `require_admin` + 감사 로그
- pip/npm 패키지 설치는 관리자만 실행 가능, 모든 시도가 `audit_log.json`에 기록

### Release
- 배포 버전을 `0.2.0`으로 상향 (메이저 구조 변경)
- 대상 채널: `npm` · `PyPI` · `VS Code Marketplace` · `Open VSX`

---

## [0.1.31] - 2026-05-25

### 모델 추천 보정 — 하드웨어 대비 과도한 모델 방지

- **Apple Silicon 32GB 추천 모델 하향 조정**
  - 32GB Mac: `Qwen3-VL-30B-A3B` (18GB) → `Qwen3-VL-8B` (q5_K_M, 5GB) 로 변경
  - 30B-A3B 모델은 48GB 이상에서만 추천 (OS 오버헤드 + KV 캐시 여유 확보)
  - 32GB 시스템에서 메모리 압박으로 인한 성능 저하 방지

- **`auto_setup.py` `_MODEL_CATALOG` 보수적 임계값 적용**
  - 30B-A3B: 최소 RAM 32GB → 48GB
  - 24GB VRAM 임계값 조정 (12GB로 완화하여 중급 GPU 커버)
  - 각 티어 간 여유분을 확보하여 실사용 시 안정적 추론 보장

- **`setup.py` 추천 로직 보정**
  - Apple Silicon 기본 추천 30B 임계값: `ram >= 32` → `ram >= 48`
  - MLX 모델 카탈로그 min_ram 상향: Qwen3-VL 30B (32→48), Gemma 3 27B (32→48), Gemma 4 26B (24→32), Mistral Small 24B (24→32), Qwen2.5 Coder 32B (32→36)
  - 크로스 플랫폼(vLLM/LM Studio) 30B 모델: 전용 GPU 시스템은 min_ram=32 유지 (VRAM에 로드되므로 RAM 부담 적음)

### Release
- 배포 버전을 `0.1.31`로 상향
- 대상 채널: `npm` · `PyPI` · `VS Code Marketplace` · `Open VSX`

---

## [0.1.30] - 2026-05-25

### 코드 품질 및 리팩토링

- **`server.py` 모듈 분리** — 7,568줄 → 6,798줄
  - MCP 레지스트리 상수 + 원격 레지스트리 페치 + 스킬 마켓플레이스 + 플러그인 디렉터리 로직을 `mcp_registry.py`(791줄)로 분리
  - `server.py`의 가독성과 유지보수성 대폭 향상

- **버그 수정 6건**
  - `requirements.txt`에 누락된 `pymupdf` 추가 (Docker 빌드 실패 방지)
  - 비밀번호 해싱 로그 메시지 "bcrypt" → 실제 알고리즘 "scrypt"로 수정
  - HuggingFace 모델 캐시 경로 `~/.latticeai/` → `~/.ltcai/`로 통일 (DATA_DIR과 일치)
  - OpenRouter 모델 카탈로그: Claude 3.5 → Claude 4.x, Gemini 2.0 → 2.5 업데이트
  - `.gitignore`에 임시 파일, 로그, 세션 파일 패턴 8개 추가
  - 고아 파일 정리 (구버전 GIF, 캡처 스크립트 삭제)

- **README 개선**
  - v0.1.29 실제 UI에서 새로 찍은 스크린샷 3장 + 애니메이션 데모 GIF 추가
  - GitHub Actions CI 배지 추가
  - 스크린샷에 이모지 레이블 + 설명 캡션 추가

### Release
- 배포 버전을 `0.1.30`으로 상향
- 대상 채널: `npm` · `PyPI` · `VS Code Marketplace` · `Open VSX`

---

## [0.1.29] - 2026-05-25

### 관리자 UX 및 거버넌스 개선

- **관리자 대시보드 섹션 분리**
  - 대시보드, 사용자 관리, 권한 관리, SSO 관리, 보안 모니터링, 감사 로그가 각각 독립된 역할을 갖도록 정리
  - 사용자 관리는 활성/비활성 상태를, 권한 관리는 기본/고급/관리자 모드 권한을 명확히 표시
  - SSO 관리는 Okta / Microsoft Entra ID OIDC 설정 저장 및 테스트 플로우를 제공

- **보안 모니터링 / 감사 로그 내보내기**
  - 보안 모니터링 로그와 감사 로그를 각각 TXT, Excel(`.xls`), CSV로 추출 가능
  - 모든 내보내기 파일에 UTF-8 BOM을 포함해 한글이 깨지지 않도록 처리
  - 감사 로그의 사용자 사용량/위험도와 감사 이벤트, 보안 모니터링의 위험/준수 필드를 파일로 보존 가능

- **전역 UX 및 언어 전환 개선**
  - account/admin/chat/graph 화면의 언어 버튼 전환 시 주요 UX 텍스트가 한국어/영어로 함께 갱신되도록 개선
  - 홈/채팅 화면 구조를 분리해 채팅 전환 시 상태 충돌을 줄임
  - 채팅 빈 화면에서 Lattice AI의 역할과 기능을 더 명확히 안내

- **대시보드 시각 안정화**
  - 전체 사용자, 활성 메시지, 현재 모델, VPC 상태 카드의 줄바꿈/가독성 개선
  - 감사 로그의 Graph nodes / Edges 수치가 `[object Object]`로 표시되던 문제 수정
  - 분리된 정적 JS 파일(`static/scripts/*.js`)이 npm/PyPI 패키지에 포함되도록 배포 설정 보강

### Release
- 배포 버전을 `0.1.29`로 상향
- 대상 채널: `npm` · `PyPI` · `VS Code Marketplace` · `Open VSX`

---

## [0.1.28] - 2026-05-24

### 버그 수정: 추천 모델 ID 오류

- **`google/gemma-4-E4B` → `mlx-community/gemma-4-e4b-it-4bit` 수정**
  - 기존 ID는 HuggingFace의 BF16 풀프리시전 원본 모델 (~16GB) 로, MLX 포맷이 아니어서 `mlx_vlm.load()` 로 로드 불가능
  - 올바른 MLX 4-bit 양자화 버전(`mlx-community/gemma-4-e4b-it-4bit`, 5.2GB, 43K downloads)으로 교체
  - 크기 표시도 `"Next-Gen"` → `"5.2GB"` 로 실제 값으로 수정

### Release
- 배포 버전을 `0.1.28`로 상향
- 대상 채널: `npm` · `PyPI` · `VS Code Marketplace` · `Open VSX`

---

## [0.1.27] - 2026-05-24

### 로그인 페이지 UI 개선

**Language 버튼**
- 언어 표시 버튼 라벨을 `한국어 / English` 가변 텍스트에서 `Language` 고정 텍스트로 변경
- 버튼 위치를 화면 고정(fixed) → 로그인 카드 우측 상단(absolute) 으로 이동, 화면 크기 무관하게 카드 안에 항상 위치
- 버튼 크기 약 2/3 축소 (font 13px→11px, padding 6/14px→4/9px)
- footer 하단 언어 전환 버튼 제거 (도움말·개인정보처리방침 링크만 유지)

**로그인 카드 레이아웃**
- 카드 전체 크기 약 4/5 축소 — 너비 `min(720px)→min(460px)`, 폰트·버튼 높이·여백 비례 감소
- 타이틀 폰트 `38–54px → 28–40px`, 부제목 `24–34px → 17–24px`
- 카드 수직 위치: 타이틀바(58px)를 제외한 나머지 화면의 정중앙 배치 (`flex-direction: column` + `justify-content: center`, `padding-top: 58px`)
- 카드가 타이틀바와 겹치는 현상 구조적 수정 (기존 `align-items: center` 로 카드가 위로 올라가는 문제 해결)
- 로그인 카드와 개인정보처리방침 사이 여백 확보 (bottom padding 증가)

### Release
- 배포 버전을 `0.1.27`로 상향
- 대상 채널: `npm` · `PyPI` · `VS Code Marketplace` · `Open VSX`

---

## [0.1.26] - 2026-05-24

### MCP 관리 대폭 확장 — 3-탭 UI

**새 기능**

- **레지스트리 탭** — 기존 MCP 목록 (빌트인 + 원격 레지스트리)
  - 인기 MCP 20개 추가: `mcp-postgres`, `mcp-sqlite`, `mcp-brave-search`, `mcp-tavily`, `mcp-puppeteer`, `mcp-vercel`, `mcp-cloudflare`, `mcp-docker`, `mcp-stripe`, `mcp-supabase`, `mcp-hubspot`, `mcp-memory`, `mcp-sequential-thinking`, `mcp-discord`, `mcp-telegram`, `mcp-everything` 등
  - 각 항목에 `env_vars` 필드 (설치 시 필요한 환경변수 안내)

- **Claude Code 탭** — `~/.claude/settings.json` mcpServers 자동 동기화
  - Claude Code에서 설치한 MCP 목록을 Lattice AI UI에서 바로 확인
  - 이름·패키지·환경변수 정보 표시, "Claude Code" 소스 배지

- **직접 추가 탭** — 커스텀 MCP 폼
  - 이름·패키지·설명·환경변수·아이콘 직접 입력
  - 추가된 항목은 `~/.ltcai/custom_mcps.json`에 저장 (서버 재시작 후에도 유지)
  - 삭제 버튼 (어드민 전용)

**API 엔드포인트**
- `GET /mcp/claude-code-servers` — Claude Code settings.json mcpServers 반환
- `GET /mcp/custom` — 사용자 추가 커스텀 MCP 목록
- `POST /mcp/custom` — 커스텀 MCP 추가
- `DELETE /mcp/custom/{id}` — 커스텀 MCP 삭제 (어드민)

---

## [0.1.25] - 2026-05-24

### Knowledge Graph 전면 재설계 — 점=명사, 선=동사

**설계 원칙**
- **점(Node) = 명사** — 의미 있는 대상 (문서, 사람, 개념, 에러, 코드, 채팅 등)
- **선(Edge) = 동사** — 대상 간의 관계 (언급함, 포함함, 해결함, 의존함 등)
- 원본 데이터(PDF·PPT·채팅·코드 등)는 그대로 보관, AI가 핵심 개념을 추출해 점으로 만들고 관계를 선으로 연결

**노드 타입 (점 = 명사)**
- `Chat` — 대화 세션
- `Document` — 파일 (PDF·PPT·Word·Excel·이미지)
- `Concept` — 개념·아이디어·기술 용어
- `Person` — 사람 (사용자, 언급된 인물)
- `Error` — 오류·버그·예외
- `Code` — 코드·함수·클래스
- `Feature` — 소프트웨어 기능
- `Task` — 할 일·액션 아이템
- `Decision` — 결정 사항

**엣지 어휘 (선 = 동사형)**
`언급함` · `포함함` · `해결함` · `의존함` · `설명함` · `비교함` · `사용함` · `연결함` · `확장함` · `생성함` · `대체함` · `지원함` · `발생함` · `관련됨` · `작성함` · `업로드함`

**핵심 개선**
- `_extract_concepts()` — 고유명사·복합어·기술 용어 추출 (Lattice AI, Knowledge Graph context, VS Code 등)
- `_classify_node_type()` — 개념별 노드 타입 자동 분류 (윈도우 컨텍스트 기반)
- `_infer_edge()` — 문장 내 동사·조사 패턴으로 엣지 레이블 자동 결정
- `_extract_triples()` — 문장 단위 개념 쌍 → (주어, 동사, 목적어) 트리플 추출
- `ingest_message()` 재설계 — 메시지 단위 → 대화 세션(Chat) 단위 노드
- `ingest_document()` 재설계 — Document 노드 + 동사형 엣지 (포함함, 업로드함)
- 중복 제거 — 하위 개념이 상위 복합어에 완전히 흡수될 때만 제거
- Message·AIResponse·Chunk 노드는 RAG 검색용으로만 저장, 그래프 비표시

### Release
- 배포 버전을 `0.1.25`로 상향
- 대상 채널: `npm` · `PyPI` · `VS Code Marketplace` · `Open VSX`

## [0.1.24] - 2026-05-24

### 안정화 및 UX 개선

- **로컬 파일 인증 강화** — `/local/list` · `/local/read` · `/local/write` · `/local/serve`에서 로그인 세션 필수화 (`_require_local_user` 헬퍼 도입)
- **`GET /local/list` 라우트 추가** — smoke-test 및 브라우저 직접 호출 호환
- **VS Code 배지 수정** — shields.io `visual-studio-marketplace` 폐기 → `vsmarketplacebadges.dev`로 전환
- **README 이미지 URL 안정화** — 로고·스크린샷을 `raw.githubusercontent.com` 절대 URL로 전환해 PyPI / npm / Marketplace 페이지에서도 표시
- **Quick Start 분리** — PyPI / npm / VS Code 사용자의 첫 설치 경로를 각각 명확히 안내
- **GitHub Actions Node 24** — CI 런타임을 Node 24로 업그레이드

### Release
- 배포 버전을 `0.1.24`로 상향
- 대상 채널: `npm` · `PyPI` · `VS Code Marketplace` · `Open VSX`

## [0.1.23] - 2026-05-24

### Discord 권한 알림 시스템

- **`GET /permissions/pending`** — 대기 중인 파일 접근 권한 요청 목록 (관리자)
- **`POST /permissions/approve/{token}`** — 권한 승인 (관리자 세션 또는 `LATTICEAI_PERMISSION_SECRET`)
- **`POST /permissions/deny/{token}`** — 권한 거부/취소
- **`GET /permissions/status/{token}`** — 승인 상태 폴링 (AI 에이전트용)
- 권한 토큰 기본값 `approved: False` — 명시적 승인 전까지 파일 접근 불가
- `~/.ltcai/permission_queue.json` — 서버가 기록, Claude Code Discord 플러그인이 읽어 알림 전송
- `LATTICEAI_PERMISSION_SECRET` 환경변수 — 모니터 스크립트가 세션 없이 approve/deny 호출 가능
- `perm_monitor.py` — 권한 목록 조회·승인·거부 CLI 도우미 (`list` / `approve TOKEN` / `deny TOKEN` / `discord-msg`)
- Discord에서 `승인 <토큰앞8자>` / `거부 <토큰앞8자>` 로 파일 접근 제어 가능

### 리포지터리 UX 개선

- **영어 README** 전면 재작성 — 한국어는 접을 수 있는 `<details>` 섹션으로 이동
- **SVG 로고** 추가 (`docs/images/logo.svg`)
- **경쟁 제품 비교표** — Lattice AI vs Open WebUI · Continue.dev · GitHub Copilot
- **Quick Start 분리** — PyPI / npm / VS Code 사용자의 첫 설치 경로를 각각 명확히 안내
- **비교표 기준 명시** — 공개 제품 동작 기준 시점을 README에 표기
- **패키지 페이지 이미지 안정화** — README 이미지 URL을 GitHub raw URL로 전환해 PyPI / npm / Marketplace에서도 표시되도록 개선
- **npm 패키지 정리** — 배포 tarball에서 테스트/캐시 파일 제외
- **실제 UI 스크린샷 3장** — Chat UI · Admin Dashboard · Data Graph (Playwright 2x 캡처)
- **VS Code 익스텐션 카테고리** `Other` → `AI, Machine Learning, Chat, Other`
- **VS Code 익스텐션 키워드** 8개 → 16개 (copilot, apple-silicon, groq, graph-rag 등)
- **VS Code 익스텐션 README** 전면 재작성 (기능표, 비교표, 모델 목록)
- 구버전 `.tgz` / `.vsix` 빌드 파일 삭제

### CI / 보안 안정화

- `/local/list` `GET` smoke-test 호환 라우트 추가
- `/local/list`, `/local/read`, `/local/write`, `/local/serve`는 로컬 개발 모드에서도 로그인 세션을 요구하도록 강화
- GitHub Actions integration smoke test 실패 원인 수정

### Release
- 배포 버전을 `0.1.23`으로 상향
- 대상 채널: `npm` · `PyPI` · `VS Code Marketplace` · `Open VSX`

## [0.1.22] - 2026-05-24

### 리포지터리 UX 개선 — 다운로드 유입 최적화

#### README 전면 재작성
- **영어 메인 문서** — 한국어는 접을 수 있는 `<details>` 섹션으로 이동 (국제 유입 대응)
- **SVG 로고 추가** (`docs/images/logo.svg`) — 인디고→시안 그라디언트 래티스 그리드 아이콘
- **경쟁 제품 비교표** — Lattice AI vs Open WebUI · Continue.dev · GitHub Copilot 10개 기준 비교
- **PyPI 월간 다운로드 수 배지** 추가 (신뢰도 지표)
- 기능 · 보안 · API · 트러블슈팅 섹션을 표(table) 형식으로 정리 (가독성 향상)

#### 실제 UI 스크린샷 자동 캡처
- `docs/images/screenshot-chat.png` — 웹 채팅 UI (사이드바, 모델/파이프라인/VPC 카드)
- `docs/images/screenshot-admin.png` — 어드민 대시보드 + Audit & Data Governance 섹션
- `docs/images/screenshot-graph.png` — Data Graph 시각화 (299 노드, 443 엣지)
- README 상단에 3단 그리드 스크린샷 테이블 추가
- `scripts/take_screenshots.js` — Playwright Chromium 헤드리스 캡처 스크립트 (2x 레티나)

#### VS Code 익스텐션 메타데이터 개선
- **카테고리** `Other` → `AI, Machine Learning, Chat, Other` (Marketplace 검색 노출 증가)
- **키워드** 8개 → 16개 추가 (`copilot`, `apple-silicon`, `groq`, `graph-rag` 등)
- **설명 문구** 구체화 — 핵심 차별점(MLX, MCP, Knowledge Graph context, local-first data handling) 명시
- **익스텐션 README 전면 재작성** — 기능표 · 빠른 시작 · 단축키 · 지원 모델 · 설정 · 비교표 포함

#### 리포지터리 정리
- 루트 및 `vscode-extension/`의 구버전 `.tgz` / `.vsix` 빌드 파일 삭제

### Release preparation

- 배포 버전을 `0.1.22`로 상향
  - `package.json`
  - `pyproject.toml`
  - `vscode-extension/package.json`
- npm / PyPI / VS Code Marketplace / Open VSX 배포 전 빌드 산출물 생성

### Verification

- Python compile check 통과
- unit tests 통과
- root npm package 생성
- Python wheel / sdist 생성
- VS Code / Open VSX용 VSIX 생성

## [0.1.21] - 2026-05-24

### Setup Wizard — 자동 설치 · 연결 · 검증 · 복구

- **구성요소 자동 감지** — Homebrew, Python, Git, Node/npm, Ollama, LM Studio, Tesseract, MLX 계열 탐지
  - `COMMON_PATH_DIRS` 확장: `/opt/homebrew/bin`, `~/.local/bin`, `~/.latticeai/bin` 등 자동 포함
  - `PACKAGE_MODULES` 맵으로 pip 패키지 → import 이름 변환 (mlx-lm, mlx-vlm, openai-whisper 등)
- **공식 다운로드 연결** — 자동 설치 실패 시 OS별 공식 페이지(`OFFICIAL_DOWNLOADS`) 자동 오픈
- **설치 완료 자동 감지** — binary / Python 모듈 재탐색 폴링으로 설치 완료 감지
- **환경 변수 / PATH 자동 세팅** — PATH 누락 디렉토리를 `.env`의 `LATTICEAI_EXTRA_PATH`에 자동 저장
  - `_update_env_file()` 헬퍼로 `.env` 파일 안전 갱신 (중복 없이 key 업데이트)
- **동작 테스트** — binary는 `--version`, Python 패키지는 `import` smoke test
- **실패 시 자동 복구** — PATH 재보정, pip 재시도, brew 실패 시 공식 다운로드 fallback

### 보안 강화 — 로컬 파일 접근 승인 시스템

- **토큰 기반 로컬 파일 승인** — `_local_permission_response()` / `_require_local_approval()`
  - 5분(300초) TTL 만료 토큰으로 read / write / list 각 액션을 별도 승인
  - write 승인 시 `content_hash`(SHA-256)로 내용 위변조 방지
  - 만료 토큰 자동 정리(lazy GC)
  - Discord permission monitor 또는 웹 UI 승인 후에만 토큰 활성화
- **로컬 파일 미리보기 보호** — `/local/serve`, `/tools/read_document`, `/tools/pdf_pages`도 서버 발급 approval token 없이는 로컬 절대 경로를 열지 않도록 변경
- **workspace 정적 노출 제거** — `/agent-files` `StaticFiles` mount 제거, 인증이 있는 다운로드 라우트만 사용
- **세션 토큰 저장 강화** — 로그인 응답 body에서 bearer token 제거, 웹 UI는 HttpOnly cookie 기반 인증만 사용
  - `static/account.html`, `static/chat.html`, `static/admin.html`, `static/graph.html`의 `localStorage` 세션 토큰 의존 제거
- **loopback 감지** — `_host_is_loopback()` + `ipaddress` 표준 라이브러리로 네트워크 노출 여부 판단
  - `REQUIRE_AUTH` 기본값: 퍼블릭 모드 또는 네트워크 노출 시 `true` 자동 적용
  - `OPEN_REGISTRATION`: 네트워크 노출/퍼블릭 모드에서 기본 `false` (초대 코드 필요)
- **CORS 세밀 제어** — wildcard credential CORS 대신 `LATTICEAI_CORS_ALLOWED_ORIGINS` 환경변수로 허용 출처 추가 설정 가능
- **파일 자동 주입(opt-in)** — `LATTICEAI_AUTO_READ_CHAT_PATHS=true` 설정 시에만 채팅 메시지의 로컬 경로를 컨텍스트에 주입 (기본 OFF — 클라우드 모델 파일 누출 방지)

### 어드민 대시보드 — Audit & Data Governance

- **감사 로그 섹션** — 사용자별 AI 사용량, 업로드 문서 수, 민감정보 감지, clear/delete 이벤트, 최근 감사 이벤트 표시
- **데이터 보존 정책** — `/clear`, `/clear_all`, 대화 삭제는 화면 정리만 수행; Data Graph / RAG / 감사 로그는 보존
  - clear 동작을 `ClearEvent` 노드로 그래프에 기록 (언제 누가 clear 했는지 감사 추적)
- **민감정보 검사** — 문서 업로드 텍스트를 감사 로그에 기록

### Knowledge Graph context / Data Graph

- **한국어 단어 검색 개선** — 2글자 키워드(`문서`, `모델` 등) RAG 검색 누락 문제 수정
- **`graph.html` 독립 페이지 유지** — 채팅 사이드바 `Data Graph` 버튼으로 연결, New Chat 버튼은 대화 검색 아래로 이동

### CLI / Node.js 래퍼

- `ltcai_cli.py` — `doctor` 명령어에 확장된 구성요소 탐지 통합
- `bin/ltcai.js` — Node.js 래퍼 PATH 보정 로직 개선

### 테스트

- `tests/unit/test_security.py` — loopback 감지, 로컬 파일 접근 approval token, write content hash 검증 추가
- `tests/unit/test_setup_wizard.py` — 자동 설정 구성요소 감지와 PATH 보정 검증 추가

### 환경변수 추가 (`.env.example`)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `LATTICEAI_AUTO_READ_CHAT_PATHS` | `false` | 채팅 메시지 내 로컬 경로 자동 주입 |
| `LATTICEAI_CORS_ALLOWED_ORIGINS` | `` | 추가 허용 CORS 출처 (콤마 구분) |
| `LATTICEAI_EXTRA_PATH` | `` | 추가 PATH 디렉토리 (Setup Wizard 자동 기록) |

## [0.1.20] - 2026-05-23

### Release
- 배포 버전을 `0.1.19`로 상향
- 대상 채널: `npm` · `PyPI` · `VS Code Marketplace` · `Open VSX`

## [0.1.18] - 2026-05-23

### MCP Registry 통합

- **`GET /mcp/tools` · `GET /mcp/installed`** — 기존 로컬 목록에 [registry.modelcontextprotocol.io](https://registry.modelcontextprotocol.io) 원격 목록을 실시간 병합
- **`POST /mcp/install`** — `npm` / `pypi` 설치 모드 추가 — 원격 레지스트리 MCP 서버를 클릭 한 번으로 설치 (`npm install -g` / `pip install`)
- **`POST /mcp/registry/refresh`** — 원격 레지스트리 캐시 강제 갱신
- `mcp_public_item` 응답에 `package` · `homepage` · `source` 필드 추가
- 원격 레지스트리는 1시간 TTL 인메모리 캐시, 서버 재시작 없이 최신 목록 유지
- `connector_info` 함수 인라인화 — `mcp_connector` 엔드포인트에서 combined registry 직접 조회

### Skills 마켓플레이스 (신규)

- **`GET /skills/marketplace`** — Apache-2.0 / MIT 검증 skills 목록 (Anthropic 18개 + 서드파티 59개 = 약 77개)
  - `?category=` · `?author=` 필터 파라미터 지원
  - 응답에 `authors` · `categories` 열거 포함
- **`POST /skills/install`** — `{ "plugin": "...", "skill": "..." }` 로 SKILL.md 런타임 fetch 후 로컬 `skills/` 에 저장
  - 파일 상단에 출처·라이선스 주석 자동 삽입 (`<!-- Source: ..., Apache-2.0 -->`)
  - `risk.json` 없으면 기본값 자동 생성
- **`GET /skills/list`** — 로컬 설치 skills 목록 (`source`: local / anthropic / third-party 구분)
- **`POST /skills/marketplace/refresh`** — 캐시 강제 갱신, author별 집계 반환
- 서드파티 소스 (모두 라이선스 검증 완료): Adobe (Apache-2.0) · Airtable (MIT) · Auth0 (Apache-2.0) · Expo (MIT) · Pydantic/Logfire (MIT)

### 플러그인 디렉터리 (신규)

- **`GET /plugins/directory`** — marketplace.json 기반 오픈소스 플러그인 149개 메타데이터 브라우저
  - `?q=` 전문 검색 · `?category=` · `?license=` 필터 지원
  - 응답에 `categories` · `licenses` 열거 포함
- **`POST /plugins/directory/refresh`** — 캐시 강제 갱신, license별 집계 반환
- `_KNOWN_REPO_LICENSES` 맵 — GitHub API 호출 없이 검증된 라이선스 즉시 조회
- 미확인 레포는 GitHub API fallback + 인메모리 per-repo 캐시
- Apache-2.0 / MIT / MIT-0 / CC-BY-4.0 플러그인만 노출, 라이선스 없는 34개 자동 제외

### Release
- 배포 버전을 `0.1.18`로 상향
- 대상 채널: `npm` · `PyPI` · `VS Code Marketplace` · `Open VSX`

## [0.1.17] - 2026-05-22

### Multi-LLM Pipeline

- **파이프라인 UI 카드** — ops 대시보드의 ACTIVE MODEL 카드와 PRIVATE VPC 카드 사이에 PIPELINE 카드 추가
  - 파이프라인 비활성 시: "멀티 LLM 파이프라인 / Plan → Execute → Review 모델 설정" 표시
  - 파이프라인 활성 시: "Pipeline ON / P:모델명 E:모델명 R:모델명" 으로 현재 설정 표시
- **멀티 LLM 에이전트 파이프라인** — Planning / Executing / Reviewing 3단계에 각각 다른 LLM 지정 가능
  - 모달에서 각 단계별 모델 선택 (로드된 로컬 모델 + 클라우드 프로바이더 자동 목록 구성)
  - 하나의 모델을 모든 단계에 사용해도 정상 동작
- **Human-in-the-loop** — 파이프라인 활성화 시 Planning 완료 후 사용자 승인을 기다렸다가 Execute 단계로 진행
  - 웹 UI: 플랜 승인 카드(`✅ 승인 / ❌ 취소`) 렌더링
  - Telegram 봇: 인라인 버튼으로 플랜 승인/취소
- **`/agent/resume` 엔드포인트** — `context_id`와 `approved` 필드로 대기 중인 에이전트 재개 또는 취소
- **`AgentRequest` 확장** — `planning_model`, `executing_model`, `reviewing_model`, `human_in_loop` 파라미터 추가
- **`LLMRouter.generate_as(model_id, ...)`** — 현재 모델을 임시 교체해 지정 모델로 생성 후 원복하는 헬퍼
- **Telegram 봇 인증 수정** — 서버 호출 시 `~/.ltcai/sessions.json`에서 어드민 세션 토큰을 읽어 쿠키로 전달
- **Telegram SSE 파싱** — `/chat` 스트리밍 응답(`text/event-stream`)을 올바르게 파싱하도록 수정
- **`_sessions_file()` 버그 수정** — 정의 이전에 전역 `DATA_DIR` 참조하던 문제 해결 (함수 내 경로 직접 계산)

### Release
- 배포 버전을 `0.1.17`로 상향
- 대상 채널: `npm`, `PyPI`, `VS Code Marketplace`, `Open VSX`

## [0.1.16] - 2026-05-22

### First-user admin bootstrap

- 서버를 처음 설치하고 가입하는 첫 번째 사용자가 자동으로 **admin** 권한 획득
- 이후 가입자는 기존과 동일하게 `user` 역할
- `/register` 응답에 `role` 필드 추가 — 클라이언트가 첫 가입 여부 확인 가능

### Release
- 배포 버전을 `0.1.16`으로 상향

## [0.1.15] - 2026-05-22

### Security hardening

- `LTCAI --tunnel` 실행 시 `LATTICEAI_REQUIRE_AUTH=true` 자동 강제 — 터널로 공개된 서버에 로그인 없이 접근 불가
- `/register` IP당 시간당 5회 rate limit
- `/login` IP당 5분당 10회 rate limit (brute force 방지)
- Cloudflare 터널 통과 시 `CF-Connecting-IP` 헤더로 실제 클라이언트 IP 추출
- `LATTICEAI_OPEN_REGISTRATION=false` 설정 시 회원가입 완전 차단 (관리자 직접 추가만 허용)

### Release
- 배포 버전을 `0.1.15`로 상향

## [0.1.14] - 2026-05-22

### `--tunnel` flag — 누구나 자기 PC를 서버로

- `LTCAI --tunnel` 한 줄로 Cloudflare 무료 터널 자동 개설
- cloudflared 바이너리가 없으면 GitHub에서 자동 다운로드 (`~/.latticeai/bin/`)
- macOS arm64/amd64, Linux arm64/amd64, Windows amd64 지원
- 터널 URL을 배너에 출력 + `LATTICEAI_TELEGRAM_BOT_TOKEN` / `LATTICEAI_TELEGRAM_CHAT_ID` 설정 시 Telegram 자동 알림
- `--tunnel` 지정 시 host 자동으로 `0.0.0.0`, CORS 네트워크 허용으로 전환

### Release
- 배포 버전을 `0.1.14`로 상향
- 대상 채널: `npm`, `PyPI`, `VS Code Marketplace`, `Open VSX`

## [0.1.13] - 2026-05-22

### Code quality & efficiency

- `HF_MODELS_ROOT` / `hf_model_dir` 중복 정의 제거 — `llm_router.py` 단일 소스로 통합, `server.py`에서 import
- `_looks_like_hf_model_dir` 가중치 파일 체크를 `.safetensors` / `.bin`으로 일치 — `.gguf`를 MLX 경로에서 잘못 허용하던 버그 수정
- `vllm_executable()` `shutil.which` 이중 호출 → 변수 캐시
- `ensure_lmstudio_model()` `_find_lmstudio_model_key` 이중 호출 → `found_key` 변수로 캐시
- `engine_support_status` 3단계 중첩 조건 → `is_apple_silicon` 플래그로 평탄화
- `ensure_llamacpp_server` 동일 프로세스 이중 `terminate()` 블록 → 단일 블록 (vllm 패턴과 통일)
- `ensure_vllm_server` 37줄 중첩 삼항 커맨드 빌더 → `if/elif/else` + `_host_args` 공통화
- `except: pass` → `except Exception: pass` (KeyboardInterrupt 노출)
- `knowledge_graph.py` 엣지 순회 루프 두 번 (`degree_map` + `topic_metrics`) → 단일 루프로 병합

### Performance & correctness

- `get_lmstudio_models()` TTL 캐시(10초) 추가 — `/health`, `/engines`, `/models` 매 요청마다 LM Studio HTTP 프로브하던 문제 해결, 서버 미응답 시 마지막 캐시 반환
- `/health`, `/engines`, `/models` 엔드포인트에서 `engine_status()` 호출을 `asyncio.to_thread()`로 오프로드 — LM Studio 최대 45초, ollama subprocess 블로킹이 이벤트 루프를 점유하던 문제 해결
- 앱 종료 시 `LOCAL_SERVER_PROCESSES` (vLLM, llama.cpp) 자식 프로세스 정리 — GPU 메모리 고아 프로세스 누수 수정

### Release
- 배포 버전을 `0.1.13`으로 상향
- 대상 채널: `npm`, `PyPI`, `VS Code Marketplace`, `Open VSX`

## [0.1.12] - 2026-05-22

### Local engine install / load flow
- `vLLM` 설치 경로를 macOS용 `Python 3.12 + vllm-metal` 흐름으로 교체
- `LM Studio` 번들 `lms` CLI와 native API를 사용해 서버 시작, 모델 다운로드, 모델 로드를 자동화
- `llama.cpp`는 선택한 GGUF를 alias와 함께 OpenAI 호환 서버로 직접 로드하도록 정리
- 모델 패널의 `설치` / `다운로드 후 자동 로드` 흐름이 실제 `prepare_and_load_model()` 경로로 수렴되도록 정리

### Verified
- 최소 테스트 모델 기준 실사용 검증 완료
- `vLLM`: `Qwen/Qwen2.5-0.5B-Instruct-AWQ`
- `LM Studio`: `https://huggingface.co/lmstudio-community/Qwen2.5-0.5B-Instruct-GGUF`
- `llama.cpp`: `lmstudio-community/Qwen2.5-0.5B-Instruct-GGUF`

### Release
- 배포 버전을 `0.1.12`로 상향
- 대상 채널: `npm`, `PyPI`, `VS Code Marketplace`, `Open VSX`

## [0.1.11] - 2026-05-21

### Agent state machine (renamed + cleaned up)
- 8개의 명시적 상태: `IDLE → PLANNING → WAITING_APPROVAL → EXECUTING → VERIFYING → (DONE | ROLLBACK → FAILED)`
- `RETRY` 상태 제거 — 재시도 카운터는 `AgentRunContext.retry_count`에 보관, `VERIFYING`이 `EXECUTING`으로 직접 전환
- 종료 상태를 `DONE` / `FAILED`로 분리 — 응답에 `final_state` 필드 추가, `status`는 `"ok"` 또는 `"failed"`

### Tool Permission Layer
- `ToolPermission` 추가 — `{ tool, risk, requires_approval, network }` 4-필드 컴팩트 뷰
- 기존 7-차원 `TOOL_GOVERNANCE`에서 자동 파생 (단일 진실 공급원)
- `GET /tools/permissions` 엔드포인트 추가
- `/mcp/tools` 응답의 각 툴에 `permission` 필드 노출

### Cleanup
- 중국어 응답 지원 제거 — `detect_language`는 이제 `ko` 또는 `en`만 반환
- `_LANG_HINT`에서 `"zh"` 키 삭제, EXECUTOR_PROMPT의 "Chinese" 언급 제거

### Repo
- `CHANGELOG.md` → `docs/CHANGELOG.md` 이동 (루트 가독성 개선)
- 자동 릴리스 워크플로(`release.yml`) 제거 — 수동 배포 유지

---

## [0.1.10] - 2026-05-21

### Agent intelligence (pro-developer workflow)
- **`AGENT_SYSTEM_PROMPT` 완전 재작성** — Claude Code 스타일 시니어 개발자 워크플로
  - Discover → Plan → Implement → Verify 4단계 강제
  - JSON 응답에 `thoughts` 필드 추가, transcript에 함께 기록되어 다음 스텝의 컨텍스트로 전달
  - 코드 읽기 전 수정 금지, 검증 없이 "완료" 주장 금지, 작은 diff 원칙
  - 새 도구 카탈로그 + 안티패턴(반복 액션·환각 import·placeholder URL) 명시
- **`max_steps` 상향** — 기본값 6 → 25, 캡 10 → 50 (`AgentRequest.max_steps`)

### New tools
- **`edit_file`** — 정밀 diff 편집. `old_string`이 파일에 유일하게 존재해야만 성공(또는 `replace_all=true`). 환각 import / 잘못된 위치 수정 방지. 결과에 `first_edit_line` 포함
- **`grep`** — 정규식 검색, 전체 텍스트 파일 대상, `glob` 필터, `context_lines`, binary dir(`node_modules`, `.git`, `venv`, `dist` 등) 자동 제외. 기존 `search_files`는 호환 유지
- **`todo_write` / `todo_read`** — 워크스페이스별 영구 TODO 리스트(`agent_workspace/.lattice/todos.json`). 멀티스텝 작업의 상태 유지. status ∈ `pending | in_progress | completed`. 다중 in_progress 경고
- **`read_file` 업그레이드** — `numbered`(라인 번호 뷰), `total_lines`, `start_line`/`end_line`, optional `offset`/`limit` 추가. 기존 `content` 반환 호환 유지
- 위 모든 도구에 `/tools/*` REST 엔드포인트 추가, `_TOOL_RISK` 등록, `/mcp/tools` 카탈로그 노출

### Loop safety
- `_FILE_CREATE_ACTIONS`에 `edit_file` 포함 — 같은 args로 연속 호출 시 자동 중단
- 반복 중단 메시지를 "다음 단계로 진행하세요"로 명확화

### Tests
- `tests/unit/test_tools.py`에 23개 신규 테스트 — edit_file (유일/모호/`replace_all`/identical), grep (regex·glob·case·context·binary dir), todo round-trip + 검증, read_file numbered/offset/limit, 샌드박스 이탈 차단 (`52 passed`)

### Security (보안 기본값 통일)
- **기본 바인딩 `0.0.0.0` → `127.0.0.1` 롤백** — v0.1.8에서 PWA 편의를 위해 0.0.0.0으로 변경했으나 개인 AI 서버의 기본값은 로컬 전용이어야 안전함. 네트워크 노출이 필요한 경우 `LATTICEAI_HOST=0.0.0.0` 명시적 설정.
- SECURITY.md, CONTRIBUTING.md, GitHub Actions CI/Release 워크플로 추가
- docs/ 문서 추가: architecture, security-model, public-deploy, mcp-tools, privacy

---

## [0.1.9] - 2026-05-21

### Security
- **세션 TTL 7일 → 24시간 + sliding refresh** — 활동 시 만료시간 자동 연장, 15분 단위 디스크 쓰기 throttle
- **평문 비밀번호 마이그레이션 audit 로깅** — `password_migrated_from_plaintext` 이벤트로 남은 평문 사용자 추적
- **파일 업로드 magic-number 검증** — `_bytes_match_extension()`: PDF/DOCX/XLSX/PPTX/PNG/JPEG/ZIP 시그니처 확인, 확장자 위조 방지
- **Rate limiting** — `/chat` 30 burst/분당 30, `/agent` 10 burst/분당 6, `/upload` 20 burst/분당 12. 토큰 버킷 per-user. `LATTICEAI_RATE_LIMIT=0`으로 비활성화 가능

### Reliability
- **PyMuPDF 파일 핸들 누수 수정** — `/tools/pdf_pages` try/finally로 doc.close() 보장, `len(doc)` 호출 위치 버그 수정
- **ollama serve 좀비 방지** — 실행 전 already_up 체크, `start_new_session=True`로 detach
- **knowledge_graph.py 손상된 metadata_json 안전 처리** — `_safe_loads()` 헬퍼로 corrupt row 통과 (5곳 적용)
- **백그라운드 asyncio 태스크 예외 로깅** — `_spawn()` 헬퍼 (`add_done_callback`) — startup 태스크 silent fail 방지
- **silent except → logging.warning** — `_load_sessions`, `_persist_sessions`, `load_vpc_config`, `load_mcp_installs`

### Tests
- **`tests/unit/test_security.py`** — 16개 신규 테스트: bcrypt 해시 라운드트립/유니크, MIME 검증, rate limit (29 → 31개 전체 통과)

---

## [0.1.8] - 2026-05-21

### Added
- **PWA (Progressive Web App)** — iPad / Android / Galaxy Tab 홈화면 설치 지원
  - `manifest.json`: 앱 이름, 아이콘, 배경색, 테마색, 단축키 정의
  - `sw.js` Service Worker: 정적 파일 캐시-퍼스트, API 네트워크-퍼스트, 오프라인 대응
  - 192×192, 512×512, apple-touch-icon 180×180, favicon 32×32 PNG 아이콘 생성
  - 모든 HTML에 `<link rel="manifest">`, `apple-mobile-web-app-*`, `theme-color` 메타태그 추가
  - `viewport-fit=cover` — iPhone Dynamic Island / 노치 안전영역 확장
- **서버 네트워크 공개 바인딩** — 기본 host `127.0.0.1` → `0.0.0.0`으로 변경
  - 같은 Wi-Fi 내 iPad / Android / Galaxy Tab 에서 `http://<Mac IP>:4825` 로 바로 접근 가능
  - 시작 배너에 로컬 / 네트워크 URL 및 "Add to Home Screen" 안내 출력
- **Windows 서버 호환성**
  - `computer_screenshot`: macOS `screencapture` 외 Windows/Linux에서 pyautogui fallback
  - `computer_open_app` / `computer_open_url`: `open -a` (macOS) / `cmd /c start` (Windows) / `xdg-open` (Linux) 자동 분기
  - `_PLATFORM` 상수 도입으로 향후 플랫폼 분기 일관성 확보
- **배포 파일 포함**: `manifest.json`, `sw.js`, `icons/` 폴더를 npm · PyPI 패키지에 포함

### Deployed
- npm ✅
- PyPI ✅
- VS Code Marketplace ✅
- Open VSX ✅

---

## [0.1.7] - 2026-05-21

### Added
- **모바일 반응형 UI** — 폰/태블릿 화면 크기에 자동 대응
  - 768px 이하: 사이드바가 좌측 슬라이드 드로어로 전환, 헤더 햄버거(☰) 버튼으로 열기
  - 오버레이 탭하면 사이드바 닫힘, 대화 선택 시 자동 닫힘
  - ops-strip 카드 3개 → 가로 스크롤 한 줄 압축 (모바일)
  - `100dvh` 적용 — iOS 소프트 키보드 올라와도 레이아웃 유지
  - `env(safe-area-inset-bottom)` — iPhone 노치/홈바 안전영역 자동 여백
  - textarea `font-size: 16px` (480px 이하) — iOS 자동 줌 방지
- 브레이크포인트 3단계: 900px(태블릿) / 768px(모바일 드로어) / 480px(폰)

---

## [0.1.6] - 2026-05-21

### Added
- **LATTICEAI_ENABLE_GRAPH** 환경변수 — Data Graph 기능을 퍼블릭 서버에서 완전히 숨길 수 있는 토글 (기본값 `true`)
  - `false`로 설정 시 모든 그래프 API 엔드포인트 404 반환, 인제스트 건너뜀, 사이드바 버튼 자동 숨김
- `.env.example`에 `LATTICEAI_ENABLE_GRAPH` 항목 추가 (로컬/퍼블릭 모드 각각)

---

## [0.1.5] - 2026-05-21

### Added
- **Data Graph** — 채팅·AI 답변·업로드 문서를 SQLite 지식 그래프로 자동 구조화, `/graph`에서 Canvas 기반 Force-directed 시각화
- **Knowledge Graph context** — 그래프 검색 결과를 채팅 컨텍스트에 자동 주입하여 이전 대화·문서 참조 능력 강화
- **Telegram 원격 제어** — 인라인 키보드 메뉴로 상태 조회, 모델 관리, 스크린샷, 그래프 통계, 문서 업로드 등 원격 제어
- `knowledge_graph.py` — KnowledgeGraphStore (node/edge/chunk/event), `ingest_message()`, `ingest_document()`, `context_for_query()`, `search()`, `neighbors()`
- `static/graph.html` — 타입별 색상, 줌/패닝, 핀치 줌, 이웃 하이라이트, 노드 상세 정보, 채팅 연결 링크

### Security
- 어드민 세션 핸드오프를 URL 파라미터 → `sessionStorage` 1회 읽기 방식으로 교체 (히스토리 노출 방지)
- `X-Admin-Email` 헤더 폴백 제거 — Bearer 토큰 인증만 허용

---

## [0.1.4] - 2026-05-18

### Added
- **세션 영속성** — 서버 재시작 후에도 로그인 유지 (sessions.json 파일 기반)
- **SSO 로그인** — Entra ID / Okta OIDC 지원 (`OIDC_DISCOVERY_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` 환경변수)
- **채팅 히스토리 검색** — 사이드바 검색창으로 대화 내용 키워드 검색 (`GET /history/search`)
- **대화 삭제** — 사이드바 각 대화에 삭제 버튼 추가
- **MCP 서버 관리 UI** — 사이드바 "MCP 관리" 버튼으로 설치/목록 확인 모달
- **인라인 Diff 뷰** — Edit Selection 결과를 diff로 보여주고 Apply/Discard 선택
- **현재 파일 첨부** — `Lattice AI: Attach Current File to Chat` 명령 추가 (VS Code)
- `authlib` 의존성 추가 (SSO OIDC 지원)

### Deployed
- PyPI ✅
- npm ✅
- VS Code Marketplace ✅
- Open VSX ✅

---

## [0.1.3] - 2026-05-18

### Added
- 프로필 수정 API (`PATCH /account/profile`) 및 UI — 이름·닉네임 변경
- 회원가입 폼 개선 — 비밀번호 확인 필드, 인라인 에러 메시지
- 어드민 패널 초대 링크 섹션 — 원클릭 복사
- 어드민 대시보드 메시지 활동 차트 (Chart.js, 최근 14일)
- 웹 UI 한국어 / 영어 전환 (`🌐 Languages` 버튼, localStorage 저장)

### Fixed
- 로그아웃 시 `/logout` API 호출하여 서버 세션 쿠키 정상 만료
- 인증(`account.html`)과 채팅(`chat.html`) UI 분리 — 레거시 `index.html` 제거
- `chat.html` 내 죽은 인증 코드 제거
- 채팅 헤더에서 언어 선택 드롭다운이 ops-strip을 가리는 문제 수정

### Deployed
- PyPI ✅
- npm ✅
- VS Code Marketplace ✅
- Open VSX ✅

---

## [0.1.1] - 2026-05-18

### Added
- 비밀번호 변경 API (`POST /account/change-password`)
- 웹 UI 비밀번호 변경 모달 (헤더 계정 아이콘)

### Docs
- 어드민 패널: 첫 가입자 자동 admin 안내 추가
- 플랫폼 지원 범위 (Windows/Linux) 안내 추가
- 언어 지원 (KO/EN) 안내 추가

### Deployed
- PyPI ✅
- npm ✅
- VS Code Marketplace ✅
- Open VSX ✅

---

## [0.1.0] - 2026-05-17

### Added
- FastAPI 브릿지 서버 (port 4825)
- Apple Silicon MLX 로컬 모델 지원 (Gemma 4, Qwen 2.5 등)
- 클라우드 모델 지원 (OpenAI, Groq, Together, OpenRouter 등)
- VS Code / Cursor / Antigravity 확장
- Telegram 봇 (로컬 AI 미러 + Codex 클라우드 봇)
- 어드민 패널 (`/admin`)
- P-Reinforce 지식 정원 엔진
- MCP 서버 연동
- Ollama / vLLM / LM Studio / llama.cpp 연동

### Security
- 모든 민감 엔드포인트 인증 적용
- SameSite=Lax 쿠키 (CSRF 방어)
- scrypt 비밀번호 해싱
- tempfile 레이스 컨디션 수정
- `run_command()` 위험 플래그 차단

### Deployed
- PyPI ✅
- npm ✅
- VS Code Marketplace ✅
- Open VSX ✅
