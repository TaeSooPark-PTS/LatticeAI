# Lattice AI Release Guide

이 문서는 `npm`, `PyPI`, `VS Code`, `Cursor`, `Antigravity`, `Open VSX` 배포를
한 번에 처리하기 위한 체크리스트입니다.

> **현재 `.github/workflows/release.yml`은 태그 push에서 빌드와 검증만 수행합니다.**
> PyPI / npm / VS Code Marketplace / Open VSX 배포는 아래 수동 절차로만
> 진행합니다. 태그 생성은 패키지 스토어 publish를 자동으로 트리거하지 않습니다.

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

8.9.0 closes the actionable findings from `docs/CODE_REVIEW_2026-07-06.md`
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
- Refreshed current-release documentation while preserving historical 7.x
  release history.

Expected artifacts (exact 8.0.0 names only):
- dist/ltcai-8.0.0-py3-none-any.whl
- dist/ltcai-8.0.0.tar.gz
- dist/ltcai-8.0.0.vsix
- ltcai-8.0.0.tgz
- src-tauri/target/release/bundle/dmg/Lattice AI_8.0.0_aarch64.dmg

## v7.9.0 — Agent Runtime Boundary Hardening (2026-06-23)

7.9.0 advances the top architecture priority: AgentRuntime extraction. The
product facade remains `lattice_brain.runtime.agent_runtime.AgentRuntime`, while
the older single-agent loop now has an explicit `SingleAgentRuntime` name and a
compatibility alias for existing imports.

### Changed
- Added `SingleAgentRuntime` for the single-agent PLAN / EXECUTE / VERIFY loop.
- Preserved `latticeai.core.agent.AgentRuntime` as a compatibility alias.
- Updated tool-dispatch wiring to construct `SingleAgentRuntime` directly.
- Moved single-agent git rollback behind an injected `rollback_file` port owned
  by `ToolDispatchService`.
- Added a shared `runtime-boundary/v1` descriptor for the product and
  single-agent runtime surfaces.
- Added `RuntimeBoundaryProtocol` for the common runtime inspection surface.
- Updated architecture/product readiness targets and current-release docs to 7.9.0.

Expected artifacts (exact 7.9.0 names only):
- dist/ltcai-7.9.0-py3-none-any.whl
- dist/ltcai-7.9.0.tar.gz
- dist/ltcai-7.9.0.vsix
- ltcai-7.9.0.tgz
- src-tauri/target/release/bundle/dmg/Lattice AI_7.9.0_aarch64.dmg

## v7.8.0 — Brain Chat Home UX Simplification (2026-06-22)

7.8.0 upgrades Lattice AI from “product complete” to “understandable at a glance”.
The main Brain experience no longer asks the user to parse a command center,
ingestion grid, timeline, overview, model proof, and care controls before they
can talk to the Brain.

### Changed
- Brain Chat Home now puts the chat purpose, starter prompts, and composer in
  the first viewport.
- Source ingestion, readiness, proof, timeline, overview, model continuity, and
  care controls are collapsed behind one utility drawer.
- Workspace navigation is visible on the default Brain surface.
- Default depth controls stay hidden until the user intentionally travels
  deeper into the Brain.
- Obsolete Brain conversation and first-run guide components were removed.
- Product and architecture readiness targets now track 7.8.0.
- Release screenshots, walkthrough video/GIF, and capture notes were refreshed
  under `output/release/v7.8.0/`.

Expected artifacts (exact 7.8.0 names only):
- dist/ltcai-7.8.0-py3-none-any.whl
- dist/ltcai-7.8.0.tar.gz
- dist/ltcai-7.8.0.vsix
- ltcai-7.8.0.tgz
- src-tauri/target/release/bundle/dmg/Lattice AI_7.8.0_aarch64.dmg

## v7.7.0 — Complete Product (2026-06-22)

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

Expected artifacts (exact 7.7.0 names only):
- dist/ltcai-7.7.0-py3-none-any.whl
- dist/ltcai-7.7.0.tar.gz
- dist/ltcai-7.7.0.vsix
- ltcai-7.7.0.tgz
- src-tauri/target/release/bundle/dmg/Lattice AI_7.7.0_aarch64.dmg

## v7.6.0 릴리스 노트 (2026-06-22)

Lattice AI v7.6.0 — Brain-Centered UX & Architecture Closure. 7.6.0은 로컬에
생긴 두 리뷰 문서(`review.md`, `ux-brain-simplification-review.md`)의 내용을
다음 릴리스로 미루지 않고 제품/코드/검증 계약으로 닫는다.

첫 실행은 이제 일반 로그인/모델 마법사가 아니라 `Wake Brain`으로 시작한다. 사용자는
Brain을 먼저 만나고, 주인 확인 → 컴퓨터 확인 → Brain voice 선택의 3단계 흐름을 본다.
Brain Home에는 Living Brain 주변의 concentric memory rings와 직접 depth controls가
추가되어 Now, Memory, Topics, Relationships, Full Graph로 바로 이동할 수 있다.
이로써 Brain은 텍스트 비유가 아니라 화면의 중심 조작 객체가 된다.

아키텍처 리뷰도 테스트 가능한 계약으로 고정했다. 7.6.0 readiness contract는
AgentRuntime boundary, ToolRegistry separation, central Config, server decomposition,
Knowledge Graph hardening, Brain UX closure를 모두 gate로 노출하고 unit test로 검증한다.
기존 AgentRuntime/ToolRegistry/Config/KG portability 테스트와 함께 두 리뷰 문서의
완료 조건을 릴리스 회귀 방지 대상으로 만든다.

7.6.0 release evidence는 `output/release/v7.6.0/` 아래에서 새로 캡처한 screenshots,
walkthrough video, GIF를 기준으로 한다.

Expected artifacts (exact 7.6.0 names only):
- dist/ltcai-7.6.0-py3-none-any.whl
- dist/ltcai-7.6.0.tar.gz
- dist/ltcai-7.6.0.vsix
- ltcai-7.6.0.tgz
- src-tauri/target/release/bundle/dmg/Lattice AI_7.6.0_aarch64.dmg

## v7.5.0 릴리스 노트 (2026-06-20)

Lattice AI v7.5.0 — Runtime Debt Burn-down & Release Risk Cleanup. 7.5.0은
7.4.0에서 남긴 위험/기술부채를 다음 버전으로 미루지 않고 줄인다.

contract family는 이제 붙어 있는 metadata에 그치지 않는다. AgentRuntime status/list/detail/events와
realtime feed는 compact `contracts` view를 함께 반환해 UI, replay, admin, exporter가
agent/workflow/audit/realtime별 top-level shape를 다시 파싱하지 않고
`agent-run-contract/v1` family envelope를 소비할 수 있다.

Brain quality gate는 250개 이상 record가 들어간 deterministic local corpus fixture로 확장했다.
`scripts/brain_quality_eval.py`는 실제 `KnowledgeGraphStore`와 `SearchService`를 구동해 12개
judged query의 recall, precision, NDCG, must-include hit-rate threshold를 검증한다.

릴리스 위험도 줄였다. npm audit finding을 0개로 낮추고, Tauri 2 dependency stack을 최신 2.x로
올려 기존 transitive `block v0.1.6` future-incompatibility warning을 제거했다. 7.5.0 산출물은
clean release artifact set으로 검증한다. README release screenshots/GIF도
`output/release/v7.5.0/` 기준으로 새로 캡처했다.

Local MLX model preparation also reuses valid existing Hugging Face cache snapshots when
the same model is already present outside Lattice's managed `~/.ltcai/hf-models` directory.

Expected artifacts (exact 7.5.0 names only):
- dist/ltcai-7.5.0-py3-none-any.whl
- dist/ltcai-7.5.0.tar.gz
- dist/ltcai-7.5.0.vsix
- ltcai-7.5.0.tgz
- src-tauri/target/release/bundle/dmg/Lattice AI_7.5.0_aarch64.dmg

## v7.4.0 릴리스 노트 (2026-06-20)

Lattice AI v7.4.0 — Runtime Contract Convergence & Corpus Retrieval. 7.4.0은
7.3.0에서 시작한 `agent-run-contract/v1` 작업을 agent run에 머물지 않고 workflow
run, audit event, realtime event까지 확장한다.

agent/workflow persisted rows는 queued/running/terminal/cancelled/interrupted 전환마다
contract를 갱신한다. Workflow engine 결과, replay payload, audit log, realtime SSE
feed는 기존 top-level 필드를 유지하면서 `contract.family == agent-run-contract/v1`인
공통 envelope를 추가한다. 감사 로그는 redaction 이후 contract를 생성하므로 secret을
contract artifact로 다시 노출하지 않는다.

Brain quality gate도 corpus-scale fixture로 확장했다. `scripts/brain_quality_eval.py`는
기존 small deterministic recall gate에 더해 `KnowledgeGraphStore`와 `SearchService`를
실제로 구동해 30개 이상 corpus item, 12개 judged query, recall/precision/NDCG,
must-include hit-rate threshold를 검증한다.

Expected artifacts (exact 7.4.0 names only):
- dist/ltcai-7.4.0-py3-none-any.whl
- dist/ltcai-7.4.0.tar.gz
- dist/ltcai-7.4.0.vsix
- ltcai-7.4.0.tgz
- src-tauri/target/release/bundle/dmg/Lattice AI_7.4.0_aarch64.dmg

## v7.3.0 릴리스 노트 (2026-06-20)

Lattice AI v7.3.0 — Runtime Contract & Retrieval Quality. 7.3.0은 7.2.0에서
추가한 runtime trust surface를 내부 실행 계약과 retrieval 품질 gate로 강화한다.

single-agent runtime과 multi-agent facade는 이제 공통 `agent-run-contract/v1` payload를
공유한다. 이 계약은 run id, agent id, runtime 종류, mode(simulation/llm), status, goal,
roles, current role, retry count, timeline, artifacts, blocking reason, terminal 여부를 담는다.
multi-agent API 결과와 persisted run patch는 이 contract를 포함하고, single-agent runtime도
같은 contract helper를 노출한다. 목적은 real vs simulated history가 섞이지 않게 하고,
AgentRuntime extraction의 다음 단계에서 UI/API/storage가 같은 shape를 소비하게 만드는 것이다.

Brain quality gate도 강화했다. `scripts/brain_quality_eval.py`는 기존 durable recall proof에
더해 deterministic hybrid recall/ranking fixture를 실행하고 recall/precision threshold를 확인한다.
Roadmap의 hybrid search optimization, continuous recall regression, durable Brain vision을 7.3.0의
작은 검증 가능한 단위로 반영했다.

Expected artifacts (exact 7.3.0 names only):
- dist/ltcai-7.3.0-py3-none-any.whl
- dist/ltcai-7.3.0.tar.gz
- dist/ltcai-7.3.0.vsix
- ltcai-7.3.0.tgz
- src-tauri/target/release/bundle/dmg/Lattice AI_7.3.0_aarch64.dmg

## v7.2.0 릴리스 노트 (2026-06-20)

Lattice AI v7.2.0 — Runtime Trust Baseline. 7.2.0은 7.1.0의 Brain usability
surface 위에서 AgentRuntime과 ToolRegistry의 실행 신뢰도를 제품 계약으로 끌어올린다.

AgentRuntime은 실행 전에 `POST /agents/api/run/preview`로 goal, roles, inputs,
retry budget, runtime health, blocking reason을 반환한다. 사용자는 실제 run row를
만들거나 LLM/tool 실행을 시작하기 전에 왜 실행 가능한지 또는 왜 막히는지 확인할 수 있다.
Product runtime은 LLM-backed orchestrator가 준비되지 않은 simulation mode를 실제 성공으로
기록하지 않으며, preview도 같은 준비 상태를 설명한다.

ToolRegistry는 `GET /tools/registry`와 `GET /tools/registry/diagnostics`로 dispatch handler,
governance policy, catalog description, permission projection의 live contract를 노출한다.
`read_document` governance와 `create_web_project` catalog description을 정렬했고, 단위 테스트가
handler/governance/catalog drift를 잡는다.

Expected artifacts (exact 7.2.0 names only):
- dist/ltcai-7.2.0-py3-none-any.whl
- dist/ltcai-7.2.0.tar.gz
- dist/ltcai-7.2.0.vsix
- ltcai-7.2.0.tgz
- src-tauri/target/release/bundle/dmg/Lattice AI_7.2.0_aarch64.dmg

## v7.1.0 릴리스 노트 (2026-06-20)

Lattice AI v7.1.0 — Brain Usability Completion. 7.1.0은 7.0.0의 Brain
Productization Loop 위에서 첫 실행, ingestion, graph 탐색, 답변 proof,
workspace/admin discovery, feedback state, VS Code 연동 상태를 제품 화면에서
명확히 보이게 한다.

첫 실행 온보딩은 하드웨어/메모리/GPU/런타임/모델 상태를 비개발자도 이해할 수
있는 라벨과 시각 정보로 설명하고, 추천 모델과 설치 화면은 예상 다운로드/첫 응답
시간과 다음 행동을 표시한다. Brain Home은 파일/폴더/노트/URL ingestion 단계와
memory emergence timeline을 보여줘 사용자가 "지식이 들어갔다"는 피드백을 바로
확인할 수 있다.

Knowledge Graph layer는 검색 추천, type filter, recent/all-time 시간 탐색,
선택 노드 focus 이동, neighbor highlight를 제공한다. Chat 답변은 inline source
citation marker와 접근 가능한 proof payload를 함께 렌더링한다. Shell에는
workspace/profile switcher, Admin Console gate, empty/error/consent revoke
feedback, VS Code extension sync indicator가 추가된다. VS Code extension은
heartbeat/status endpoint를 통해 main app에 연결/인덱싱/동기화 상태를 보고한다.

Expected artifacts (exact 7.1.0 names only):
- dist/ltcai-7.1.0-py3-none-any.whl
- dist/ltcai-7.1.0.tar.gz
- dist/ltcai-7.1.0.vsix
- ltcai-7.1.0.tgz
- src-tauri/target/release/bundle/dmg/Lattice AI_7.1.0_aarch64.dmg

## v7.0.0 릴리스 노트 (2026-06-18)

Lattice AI v7.0.0 — Brain Productization Loop. 7.0.0은 product route IA와
rich-page code-splitting 위에, 첫 사용자가 5분 안에 "내 자료가
Brain에 들어갔고, 답변이 출처와 함께 다시 불러와진다"를 확인하는 제품 루프를
올린다.

Brain Home 첫 화면은 파일, 폴더, 노트, 웹 URL ingestion을 중심으로 재정렬된다.
문서 업로드, 로컬 폴더 연결, note ingest, browser URL ingest는 기존
workspace-scoped ingestion 계약을 그대로 사용한다. 질문 답변에는 Memory proof와
source citation 카드가 붙고, Brain proof payload를 다시 조회해 답변이 어떤
기억/그래프 source에 기대는지 고정 노출한다.

모델 독립성은 설명 문구가 아니라 demo flow로 보인다. 사용자는 모델 페이지로
이동해 모델을 바꾼 뒤 Brain Home에서 같은 질문의 Brain evidence를 다시 확인할
수 있다. CI에는 deterministic recall/KG quality eval을 추가해 durable evidence,
source citation, graph/vector counts, model-continuity proof가 깨지면 릴리스가
실패하도록 했다.

Expected artifacts (exact 7.0.0 names only):
- dist/ltcai-7.0.0-py3-none-any.whl
- dist/ltcai-7.0.0.tar.gz
- dist/ltcai-7.0.0.vsix
- ltcai-7.0.0.tgz
- src-tauri/target/release/bundle/dmg/Lattice AI_7.0.0_aarch64.dmg
