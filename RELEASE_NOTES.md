# Release Notes

The current release target is v5.0.0. Older sections are historical
release notes and should not be read as newer product claims.

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
- Kept v4.6.0 and older notes as historical release records.

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

## v4.4.0 - Brain Engine Extraction Release

Lattice AI v4.4.0 physically extracts the Brain Core into the standalone
`lattice_brain` package. Graph, memory, context, conversations, ingestion,
agent/hook runtime, workflow, and portability now physically live in
`lattice_brain`; `latticeai` keeps only thin compatibility shims, and new
isolation tests guarantee `lattice_brain` never imports `latticeai`.

### Highlights

- Moved the knowledge graph into `lattice_brain.graph` and the hook/multi-agent/
  agent runtime into `lattice_brain.runtime`; ingestion, workflow, and
  backup/restore portability moved to `lattice_brain.ingestion`,
  `lattice_brain.workflow`, and `lattice_brain.portability`.
- `latticeai.brain.*` is deprecation-shimmed; moved `latticeai.core.*` and
  `latticeai.services.*` paths alias the physical modules with identity
  preserved.
- Added `tests/unit/test_lattice_brain_isolation.py`: an import-hook test that
  fails if `lattice_brain` ever imports `latticeai`, plus an end-to-end
  exercise of the Brain Core without FastAPI.
- No user data, storage, migration, archive, or API behavior changes.

### Expected Artifacts

- `dist/ltcai-4.4.0-py3-none-any.whl`
- `dist/ltcai-4.4.0.tar.gz`
- `dist/ltcai-4.4.0.vsix`
- `ltcai-4.4.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.4.0_aarch64.dmg`

Full notes: [RELEASE_NOTES_v4.4.0.md](RELEASE_NOTES_v4.4.0.md)

## v4.3.3 - Dead-Code Cleanup Release

Lattice AI v4.3.3 promotes the post-cleanup `main` tree after the independent
dead-code, architecture, and runtime audit. It preserves the v4.3.2 product
behavior while refreshing exact-current release artifacts and documentation.

### Highlights

- Removed post-audit dead code and obsolete release/doc drift without changing
  user-facing feature behavior.
- Corrected architecture and release documentation so the current tree is
  described as v4.3.3 while v4.3.2 reports remain historical audit evidence.
- Kept Vercel/static-docs readiness: Vercel builds a documentation-only static
  page and must not deploy the localhost FastAPI runtime.
- Preserved README badges for PyPI, npm, VS Code Marketplace, Open VSX, CI, and
  license while keeping owner-controlled registry caveats explicit.
- No feature behavior changes are included beyond cleanup, safety, and
  documentation alignment.

### Expected Artifacts

- `dist/ltcai-4.3.3-py3-none-any.whl`
- `dist/ltcai-4.3.3.tar.gz`
- `dist/ltcai-4.3.3.vsix`
- `ltcai-4.3.3.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.3.3_aarch64.dmg`

Full detail: [RELEASE_NOTES_v4.3.3.md](RELEASE_NOTES_v4.3.3.md).

## v4.3.2 RC - Product Polish & Graph UX Overhaul

Lattice AI v4.3.2 polishes the v4.3.1 desktop product and overhauls the Brain
graph UX while preserving the existing frontend architecture, Brain Core,
storage, agent/workflow, API, and user-data contracts. This RC builds validated
artifacts only; it does not tag, create a GitHub Release, publish packages, or
deploy.

### Highlights

- Brain now opens on a semantic Cytoscape graph explorer with search,
  importance filtering, type grouping, group collapse/expand, focus
  neighborhoods, label modes, importance sizing, and backend hybrid-search
  results.
- Brain, Ask, Capture, Act, Library, and System replace raw JSON dumps with
  structured summaries, operation results, readable status panels, and honest
  unavailable states.
- System exposes `.latticebrain` export, inspect, verify, import dry-run,
  confirmed import, restore dry-run, confirmed restore, storage, backup health,
  Brain Network, and device identity through real APIs.
- Tauri app-level exit handling shuts down the FastAPI sidecar after normal
  macOS quit and releases the localhost port.
- README badges, a diagram-first architecture map, and a static-only Vercel
  documentation build prepare the repository for owner-controlled publication
  without claiming that v4.3.2 has reached external registries.
- v4.3.2 self-audit evidence includes screenshots and a graph walkthrough GIF
  under `output/audits/v4.3.2-rc/`.

### Expected Artifacts

- `dist/ltcai-4.3.2-py3-none-any.whl`
- `dist/ltcai-4.3.2.tar.gz`
- `dist/ltcai-4.3.2.vsix`
- `ltcai-4.3.2.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.3.2_aarch64.dmg`

Full detail: [RELEASE_NOTES_v4.3.2.md](RELEASE_NOTES_v4.3.2.md).

## v4.3.1 RC - End-User Audit Repair

Lattice AI v4.3.1 repairs the v4.3.0 end-user audit blockers while preserving
the v4.3 frontend, Brain Core, storage, agent/workflow, API, and user-data
architecture. This RC builds validated artifacts only; it does not tag, create a
GitHub Release, publish packages, or deploy.

### Highlights

- Tauri desktop startup resolves the FastAPI sidecar, reports sidecar status and
  errors, writes logs, and shuts the sidecar down on close.
- npm clean install ships `requirements.txt` and fails honestly if dependency
  bootstrap cannot complete.
- Model Load refuses implicit runtime installs and model downloads by default.
- Agent simulation is reported unavailable instead of being recorded as real
  success when no LLM-backed model is loaded.
- Workflow create, import, export, and run paths are exposed in Act through the
  existing workflow API.
- Runtime labels, configured ports, Postgres dependency status, sqlite-vec
  fallback status, and `.latticebrain` bundle sections match runtime behavior.

### Expected Artifacts

- `dist/ltcai-4.3.1-py3-none-any.whl`
- `dist/ltcai-4.3.1.tar.gz`
- `dist/ltcai-4.3.1.vsix`
- `ltcai-4.3.1.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.3.1_aarch64.dmg`

Full detail: [RELEASE_NOTES_v4.3.1.md](RELEASE_NOTES_v4.3.1.md).

## v4.2.0 - Brain Core & Storage Rebuild

Lattice AI v4.2.0 extracts the backend Digital Brain boundary into the
independent `lattice_brain` package and adds a pluggable storage layer while
preserving v4.1.0 APIs, frontend behavior, and SQLite user data. GitHub Release
artifacts are created from validated local builds only; no external package
registries are published as part of this release.

### Highlights

- `lattice_brain.BrainCore` is the FastAPI construction boundary for the
  Knowledge Graph and durable conversation store.
- `StorageEngine`, `SQLiteEngine`, and `PostgresEngine` define the storage
  layer; SQLite remains the default.
- sqlite-vec is detected honestly when available; otherwise local hash-vector
  cosine search remains the real fallback.
- Postgres/pgvector setup and SQLite-to-Postgres migration are opt-in and
  fail closed when DSN/dependencies are missing.
- Docker Postgres setup requires explicit user consent; live v4.2.0 validation
  covered pgvector migration integrity, idempotence, and fail-closed behavior.
- Encrypted `.latticebrain` archives can back up and restore the local brain DB
  and blobs.

### Expected Artifacts

- `dist/ltcai-4.2.0-py3-none-any.whl`
- `dist/ltcai-4.2.0.tar.gz`
- `dist/ltcai-4.2.0.vsix`
- `ltcai-4.2.0.tgz`

Full detail: [RELEASE_NOTES_v4.2.0.md](RELEASE_NOTES_v4.2.0.md).

## v4.0.1 - Digital Brain Platform Maintenance

Lattice AI v4.0.1 packages the commits on `main` after tag `v4.0.0`. It closes
the post-tag implementation gaps without reusing the `v4.0.0` version number.
No external package registries are published as part of this release.

### Highlights

- Durable async agent/workflow runs: queued/running/final rows, realtime SSE
  progress, cooperative cancellation, and startup reconciliation.
- Durable identity and workspace state: stable user UUIDs, centralized policy,
  local invitation tokens, and SQLite Workspace OS state with JSON
  compatibility.
- `/app` is the only shipped product UI: legacy static pages are removed and
  compatibility routes redirect into the SPA.
- SPA parity is complete for account/profile/password, workspace/org
  administration, invitations, snapshots/time-machine, activity/presence,
  run approvals/cancellation, workflow triggers, Brain Network, chat context
  trace, and Knowledge Graph provenance coverage.
- en/ko i18n runtime coverage is wired through the shell, routes, and parity
  views.

### Expected Artifacts

- `dist/ltcai-4.0.1-py3-none-any.whl`
- `dist/ltcai-4.0.1.tar.gz`
- `dist/ltcai-4.0.1.vsix`
- `ltcai-4.0.1.tgz`

Full detail: [RELEASE_NOTES_v4.0.1.md](RELEASE_NOTES_v4.0.1.md).

## v3.6.0 - Knowledge Graph First

Lattice AI is not a model-personalization system. It is a **Digital Brain
Platform**: the Knowledge Graph is your durable asset, and models read it.
v3.6.0 makes the graph the primary architecture — every source converges into it
through one unified ingestion pipeline. Nothing was published to an external
registry and Vercel remains landing/download/demo only (never the runtime).

### Highlights

- **Unified ingestion pipeline** — one entrypoint for files, folders, web URLs,
  browser tabs, and text; idempotent by content hash; routed through the
  `pre_tool`/`post_tool` hook lifecycle.
- **Formalized entities/relationships** — `Source`/`Repository`/`Meeting`/
  `Organization`/`Workflow`/`Agent` + `indexed_from`/`modified_by`/
  `belongs_to_project`/`part_of`/`discussed_in`/`decided_by`/`generated_by`/
  `used_by_agent`.
- **Browser & web ingestion** — local URL fetch + browser-tab capture (Manifest V3
  extension, `127.0.0.1` only).
- **Portability** — local export/import (JSON) and binary backup/restore (DB +
  blobs, integrity-checked); no cloud required.
- **Provenance** — every node records where it came from; queryable audit trail.
- **Knowledge Graph as the primary surface** — Status, Sources, Capture, Backup.

Full detail: [RELEASE_NOTES_v3.6.0.md](RELEASE_NOTES_v3.6.0.md).

## v3.3.1 - Visual Product Rebuild

Lattice AI v3.3.1 rebuilds the visible `/app` product experience while
preserving the local-first runtime behavior. No packages were published, no tag
was created, no GitHub Release was created, and nothing was deployed.

### Highlights

- Rebuilt the global shell with a denser command rail, grouped navigation, local
  retrieval readiness footer, quiet topbar, and mode-aware command palette.
- Reorganized production navigation into Basic, Advanced, and Admin surfaces,
  while keeping compatibility-only pages deep-linkable.
- Replaced the v3.3.0 palette with cooler neutral light/dark tokens and an 8px
  card/panel component system.
- Rebuilt Home as a truthful readiness dashboard for backend, model, retrieval,
  memory, sources, stats, and recent activity.
- Clarified Files manual upload versus desktop local-agent folder connection.
- Added Settings runtime readiness and stabilized the Chat streaming send/stop
  button handler.
- Added `VISUAL_REBUILD_NOTES_v3.3.1.md`, `FIGMA_SPEC.md`, and updated
  `STYLE_SYSTEM.md`.

### Expected Artifacts

- `dist/ltcai-3.3.1-py3-none-any.whl`
- `dist/ltcai-3.3.1.tar.gz`
- `dist/ltcai-3.3.1.vsix`
- `ltcai-3.3.1.tgz`

## v3.2.0 - Feature-Complete Platform

Lattice AI v3.2.0 is the final major feature release before maintenance mode and
is feature-complete for all non-enterprise use cases. A user can install Lattice
AI, connect models, build knowledge graphs, use hybrid search, build memory, run
agents, create agent workflows and multi-agent systems, create/install agents
and skills, use MCP tools, build automations, and store long-term memory —
without leaving `/app`. Enterprise (SSO, SCIM, RBAC, compliance, DLP, private
VPC, governance, multi-tenant controls) remains future work.

### Highlights

- **Multi-agent collaboration** — Planner → Researcher → Executor → Reviewer
  with handoffs, shared context, review/retry, and replayable timelines.
- **Agent Registry** — registration, discovery, metadata, versioning,
  capabilities, and configuration (`/agents/api/registry*`).
- **Marketplace & Templates** — five named agent templates with clone / export /
  import / install over an offline-capable local catalog.
- **Workflow Agents & Autonomous Planning** — trigger → chain → tools → memory →
  result, and goal → plan → execute → review → replan with inspect/replay.
- **Long-Term Memory + Memory Manager** — six tiers unified with recall,
  inspect, prune, compact, rebuild, and clear (`/api/memory/*`).
- **Skills, Hooks, Tool Registry, MCP Manager** — manage skills; lifecycle hooks
  (`/api/hooks/*`); governed tool registry; connected MCP servers/tools/health.
- v3 fallback states are unavailable/empty instead of sample data or fake
  counters. Package publication and deployment were not performed.

### Release Audit & Hardening

- Added [docs/V3_2_AUDIT.md](docs/V3_2_AUDIT.md), a strict 20-claim audit
  covering implementation, routes, adapters, views, tests, runtime validation,
  artifacts, tag policy, and GitHub Release metadata.
- Fixed `/app#/agents` so the Agent Registry is visible and actionable from the
  product view, including built-in/custom registry metadata, capabilities,
  enablement, and custom-agent registration.
- Fixed `/app#/skills` so it renders the live `/workspace/skills` payload
  (`installed`, `available`, and object/array `registry` forms) instead of
  falling back to an empty state.
- Removed duplicate MCP route registration and added a public route
  path/method duplicate guard.
- Expanded Playwright coverage for v3.2 platform views, Agent Registry, and
  Skills Registry. The real `/app` browser sweep passed on desktop and mobile
  routes with no app 404/500 responses, console/page errors, Classic dependency
  text, or horizontal overflow.

### Expected Artifacts

- `dist/ltcai-3.2.0-py3-none-any.whl`
- `dist/ltcai-3.2.0.tar.gz`
- `dist/ltcai-3.2.0.vsix`
- `ltcai-3.2.0.tgz`

## v3.1.0 - Mainline Product Platform Completion

Lattice AI v3.1.0 completes the non-enterprise Local-First AI Workspace
Platform around `/app`. Classic remains a compatibility/debug path only.

### Highlights

- `/app` is the full native workspace for Chat, Models, Agents, Files, Pipeline,
  My Computer, Settings, Knowledge Graph, Hybrid Search, and Admin views.
- Models load/unload from `/app#/models` through real backend endpoints.
- v3 fallback states are unavailable/empty instead of sample data or fake
  counters.
- Production embedding profiles cover local, Ollama, MLX, and
  OpenAI-compatible semantic providers; hash embeddings are fallback-only.
- `/app` loads build-generated hashed assets through
  `static/v3/asset-manifest.json`; runtime HTML no longer uses `?v=` asset
  cache-busting.
- Package publication and deployment were not performed.

### Expected Artifacts

```text
dist/ltcai-3.1.0-py3-none-any.whl
dist/ltcai-3.1.0.tar.gz
dist/ltcai-3.1.0.vsix
ltcai-3.1.0.tgz
```

## v3.0.0 - Local-First AI Workspace Platform

Lattice AI v3.0.0 makes `/app` the primary product experience and ships the v3
retrieval backend with the native workspace shell.

### Highlights

- `/app` is the default post-login workspace; legacy `/chat` remains available
  as a rollback/debug path.
- Native Chat streams through `POST /chat`, keeps retrieval context in the v3
  shell, and shows a friendly no-model-loaded setup message.
- Knowledge Graph, Vector Index, and Hybrid Search are first-class v3 surfaces.
- Hybrid Search shows keyword, local vector, and graph signal weights instead of
  a misleading alpha value.
- Default embeddings are `lattice-local-hash-v1` deterministic local fallback
  embeddings, not a production semantic embedding model.
- Release workflow now builds and validates tag artifacts without publishing
  packages automatically.

### Expected Artifacts

```text
dist/ltcai-3.0.0-py3-none-any.whl
dist/ltcai-3.0.0.tar.gz
dist/ltcai-3.0.0.vsix
ltcai-3.0.0.tgz
```

## v2.2.7 - Visual System Stabilization

Lattice AI v2.2.7 stabilizes the rendered browser UI across the major product
screens. The focus is visual completion: dark mode no longer looks foggy, Chat
uses the same product language as the operational panels, and Workspace OS /
Knowledge Graph no longer carry washed-out legacy surfaces.

### Fixes

- Chat composer, textarea, attachment controls, send button, and bottom dock now
  render as a cohesive dark surface with no white haze.
- Knowledge Graph dark canvas is a dark work surface instead of a light/milky
  rectangle.
- Workspace OS list/input/card surfaces use theme tokens in dark mode.
- Workspace select, onboarding, environment analysis, recommendation result,
  auto setup, mode select, pipeline, My Computer, profile, settings, Private
  VPC, model state, and model switcher panels share a consistent modal/panel
  treatment.
- Account/register dark titles and inputs retain strong contrast.

### Expected Artifacts

```text
dist/ltcai-2.2.7-py3-none-any.whl
dist/ltcai-2.2.7.tar.gz
dist/ltcai-2.2.7.vsix
ltcai-2.2.7.tgz
```

## v2.2.5 - Release Hygiene Hotfix

Lattice AI v2.2.5 finishes the release-prep cleanup for dark-mode overlays,
modal state, cache-busting, favicon routing, and Telegram log safety.

### Fixes

- Full-screen overlays now use the shared `--overlay-scrim` token and avoid
  blur-heavy backdrops, preventing foggy or washed-out dark mode.
- Chat modal state is centralized so one blocking modal is active at a time,
  Escape/backdrop close works, route changes clear stale layers, and body scroll
  lock restores.
- Static asset query strings are aligned to `?v=2.2.5`; Chat loads
  `/static/scripts/chat.js?v=2.2.5`.
- `/favicon.ico` is served from a packaged `static/favicon.ico` asset.
- Telegram bot tokens are redacted as `bot123:REDACTED` before logging.

### Expected Artifacts

```text
dist/ltcai-2.2.5-py3-none-any.whl
dist/ltcai-2.2.5.tar.gz
dist/ltcai-2.2.5.vsix
ltcai-2.2.5.tgz
```

## v2.2.4 - Chat Dark Mode Fix

Lattice AI v2.2.4 completes the dark theme. It fixes the v2.2.3 known issue where
the entire Chat page rendered light even in dark mode. No new features. Every fix
keeps the existing design-token system, adds no `!important`, uses no inline-style
band-aids, and does not regress light mode.

### Fix

- **Chat dark mode works everywhere.** The chat skin redefined its color tokens as
  light literals on `<body>`, which shadowed the dark theme tokens, so the chat
  body, message bubbles, composer, sidebar, header, modals, drawers, toast, and
  the recommendation/onboarding entry all stayed light in dark mode. The tokens
  now flip to the dark palette under `[data-lt-theme="dark"]`, and the few
  surfaces that baked in light colors instead of tokens were corrected with
  proper dark theme branches. Light mode is unchanged.
- **Toast** is now theme-aware (was a hardcoded light toast).

### Quality

- A data-driven dark scan walks every visible chat element and asserts there are
  **zero** opaque-light surfaces in dark mode (shell + all overlays + bubbles).
- Light-mode non-regression is guarded by test.
- Responsive checks at 375 / 390 / 430 / 768 / 1024 / 1280 / 1440 / 1920 / 2560 /
  3440 px: no horizontal scroll and the composer is never clipped below the fold.
- Playwright visual suite expanded to 52 tests.

### Expected Artifacts

```text
dist/ltcai-2.2.4-py3-none-any.whl
dist/ltcai-2.2.4.tar.gz
dist/ltcai-2.2.4.vsix
ltcai-2.2.4.tgz
```

## v2.2.3 - Frontend Stability & UX Fixes

Lattice AI v2.2.3 is a frontend stabilization release. It contains no new
features. It fixes real usability problems reported after v2.2.1, runs a full
UI/UX quality pass, and strengthens the automated visual tests. Every fix keeps
the existing design-token system and adds no `!important` or specificity-only
overrides.

### Fixes

- **Login inputs are readable in dark mode.** Email and password text was
  invisible in dark mode (light field background + theme text that flips to
  near-white = "white on white"). The login screen now has a proper dark theme
  (dark glass card, titlebar, fields, SSO buttons; light title/subtitle) and a
  Chrome/Safari autofill correction. Light mode is unchanged.
- **The recommendation result is clickable and scrollable again.** The model
  groups (Gemma 4, Qwen3-VL, Llama 4) and the action buttons were clipped and
  unreachable because the recommendation body had no working scroll region
  (a CSS selector that never matched its element). Long content now scrolls to
  the bottom, the accordions expand/collapse, and the action buttons are
  reachable.
- **The recommendation screen is readable in dark mode** (dark cards, light
  text), including the "Best for this PC" callout.
- **Button interactions are stable** across login, onboarding, the Knowledge
  Graph, and Admin — verified clickable with no overlay, `pointer-events`, or
  `z-index` blockers.

### Quality

- Light/Dark theme readability pass across login, onboarding, workspace, graph,
  and admin.
- Responsive checks from 375 px phones to 3440 px ultrawide.
- Accessibility: focus rings, keyboard operation, and Escape-to-close.
- Playwright visual suite expanded to 38 tests (login readability, recommendation
  scroll + accordions + reachable actions, dark-mode readability, and
  uncaught-page-error coverage).

### Expected Artifacts

```text
dist/ltcai-2.2.3-py3-none-any.whl
dist/ltcai-2.2.3.tar.gz
dist/ltcai-2.2.3.vsix
ltcai-2.2.3.tgz
```

## v2.2.2 - Frontend QA Stabilization Release

Lattice AI v2.2.2 is a stabilization release for the local-first AI workspace.
It contains no new features. It hardens the v2.2.x responsive UI, fixes
interaction defects found in a full frontend QA pass, strengthens the automated
visual test suite, and finalizes the README and release documentation. All
fixes preserve the existing design-token structure and add no `!important`.

### QA fixes

- **Mobile navigation reachable again** — the Knowledge Graph and Admin
  hamburger toggles were hidden on phones/tablets due to a CSS source-order
  bug; their drawers are now reachable across the mobile/tablet breakpoints.
- **Admin actions clickable** — a graph-only absolute `.toolbar` rule leaked
  onto Admin/Chat and floated a panel over the header, blocking the Refresh and
  Logout buttons. Scoped off the graph page.
- **No horizontal overflow on Workspace** — a visually-hidden toggle checkbox
  was stretching to viewport width; constrained to a 1px hit-box.

### QA coverage (automated)

- Light/dark theme parity (computed colors actually invert).
- Button clickability / hit-testing (no overlay or `pointer-events` blockers).
- No horizontal scroll across 375px phone → 3440px ultrawide (10 viewports).
- Mobile hamburger drawers open and close (graph + admin).
- Escape closes open drawers (keyboard a11y).
- Long surfaces scroll instead of clipping.

### Expected Artifacts

```text
dist/ltcai-2.2.2-py3-none-any.whl
dist/ltcai-2.2.2.tar.gz
dist/ltcai-2.2.2.vsix
ltcai-2.2.2.tgz
```

## v2.2.1 - Frontend / UX Overhaul Release

Lattice AI v2.2.1 is a frontend and UX release for the local-first AI
workspace. It makes knowledge graph, AI pipeline, model workflow, and
multi-agent coding surfaces easier to use across screen sizes while preserving
feature behavior.

### Highlights

- Mobile-first responsive UI across phone, tablet, laptop, desktop, ultrawide,
  and 4K. Content is re-laid out for smaller screens, never hidden.
- Light/dark mode with OS detection, a manual toggle, and persistence.
- Rebuilt design-token system as a single source of truth
  (`static/css/tokens.css`); no `!important`-based theming.
- Accessibility: 44px touch targets, `:focus-visible` rings, a keyboard-safe
  chat composer (visualViewport inset), iOS no-zoom inputs, and reduced-motion
  support.
- Knowledge Graph UX: responsive canvas that re-fits on resize, zoom buttons,
  fullscreen, minimap, relationship filter, mobile graph/card view, and a
  theme-aware palette.
- Admin UX: wide tables reflow to cards on mobile, with responsive layout,
  dark/light support, and larger touch targets.
- File UX: drag & drop and screenshot paste to attach files.
- Model cards show country, company, run mode, and internet usage in plain
  language.

### GitHub Release Copy

Local-first AI workspace for knowledge graphs, AI pipelines, and multi-agent
coding workflows.

Plan, execute, review, and remember work across local models, cloud models,
files, and team workflows.

This release refreshes the v2.2.1 workspace UI and marketplace-facing
positioning around:

- Local-first AI Workspace
- AI Pipeline Platform
- Knowledge Graph Platform
- Multi-Agent Workflow
- Personal / Organization Workspace
- Local Model Management
- SSO for teams

### Expected Artifacts

```text
dist/ltcai-2.2.1-py3-none-any.whl
dist/ltcai-2.2.1.tar.gz
dist/ltcai-2.2.1.vsix
ltcai-2.2.1.tgz
```

## v2.2.0 - Multimodal-First Knowledge OS Release

Lattice AI v2.2.0 reframes the product as an AI Knowledge Graph workspace. The release moves
model policy, documentation, UI copy, and recommendation logic toward a
multimodal-first Knowledge Graph architecture.

### Highlights

- README and architecture docs rewritten around AI Knowledge Graph workspace direction.
- New principle docs added for AI philosophy, model policy, and Knowledge Graph
  behavior.
- Local model catalogs now recommend current multimodal families only.
- Gemma 4 is the default recommendation family.
- Gemma 2, Gemma 3, Qwen2.5-VL, text-only fallback models, and MLX-LM
  recommendation paths are removed.
- Model entries now carry source disclosure metadata.
- Basic and advanced modes remain feature-equivalent; admin mode carries the
  actual authority boundary.
- Version metadata is aligned to `2.2.0`.

### Expected Artifacts

```text
dist/ltcai-2.2.0-py3-none-any.whl
dist/ltcai-2.2.0.tar.gz
dist/ltcai-2.2.0.vsix
ltcai-2.2.0.tgz
```
