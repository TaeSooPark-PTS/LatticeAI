# Changelog

The top entry is either the current unreleased main-branch work or the current
release line. Older entries are historical and may describe behavior as it
existed at that release.

## [Unreleased]

### Changed
- Continued server decomposition: extracted SSO into `sso_runtime.py`, audit helpers into `audit_runtime.py`; further shrunk `app_factory._build`.
- Model runtime monolith reduction: extracted HF download/progress/repo logic (~220 LOC) to `latticeai/services/model_download.py` with full re-export compat for globals and callers.
- Config centralization: `timezone`, `max_local_models`, `allow_model_downloads`, `model_download_timeout` added to `Config` and wired.
- WorkspaceOSStore decomposition: extracted `WorkspacePermissionManager` + role/permission logic to `workspace_permissions.py`; composed in main store; reduced god-class surface.
- Workspace helpers previously extracted + dedup.
- Shim improvement: clarified deprecation paths.
- Added explicit `__all__` surfaces and kept all contracts/tests green.
- Completed recommended next refactor (report item 15): 
  - Server decomp wave: significant extraction of engine server logic (lmstudio/ollama/vllm/llamacpp ensure, ollama pull, support, install entry) to latticeai/services/model_engines.py with re-exports and late imports to avoid cycles. model_runtime now delegates.
  - Deeper WorkspaceOSStore: full Timeline and Plugin/Marketplace composition.
  - KG: optional centralization for EMBED_DIM with getenv fallback (compat preserved).
- All tests 767 passed, ruff/build/docs clean.

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
