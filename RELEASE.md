# Lattice AI Release Guide

이 문서는 `npm`, `PyPI`, `VS Code`, `Cursor`, `Antigravity`, `Open VSX` 배포를
한 번에 처리하기 위한 체크리스트입니다.

> **v3.0.0부터 `.github/workflows/release.yml`은 태그 push에서 빌드와 검증만 수행합니다.**
> PyPI / npm / VS Code Marketplace / Open VSX 배포는 아래 수동 절차로만
> 진행합니다. 태그 생성은 패키지 스토어 publish를 자동으로 트리거하지 않습니다.

## v4.6.0 릴리스 노트 (2026-06-13)

Lattice AI v4.6.0 — Living Brain Experience. Brain Core extraction,
StorageEngine, FastAPI, Tauri, backup/restore, model runtime, graph APIs, and
portability capabilities는 유지하고 first-launch flow를 Login -> Environment
Analysis -> Recommended Models -> Install & Load -> Brain으로 교체한다. primary
desktop experience는 Brain + Conversation 중심이며, graph는 제거하지 않고 Brain
안쪽의 가장 깊은 exploration layer로 이동한다.

- **Changed (First launch)**: `/app` opens to Login only until the product flow
  completes; no dashboard, graph, setup cards, system status, or Brain metrics
  are shown on the opening screen.
- **Changed (Guided setup)**: environment analysis, model recommendations, and
  install/download/validate/load are presented as consumer product steps without
  runtime jargon.
- **Changed (Primary experience)**: `/app` and compatible legacy hash routes now
  open to a single immersive Brain Space after model loading instead of
  graph/status/dashboard surfaces.
- **Changed (Living Brain)**: animated Brain presence is recognizable as a Brain
  and reacts to listening, memory recall, streaming/thinking, planning, and
  agent/workflow activity.
- **Changed (Progressive disclosure)**: Brain exploration is now five levels:
  Living Brain -> Memory Layer -> Knowledge Layer -> Relationship Layer ->
  Knowledge Graph.
- **Changed (Graph positioning)**: graph exploration appears only at the
  deepest Brain level, where nodes, edges, search, and focus details emerge from
  the Brain rather than opening as a separate graph page.
- **Preserved**: Brain Core, FastAPI APIs, Tauri desktop shell, StorageEngine,
  backup/restore, model runtimes, graph/search/chat/capture/automation/system
  workflows, and compatibility route aliases.
- **Expected artifacts**:
  - `dist/ltcai-4.6.0-py3-none-any.whl`
  - `dist/ltcai-4.6.0.tar.gz`
  - `dist/ltcai-4.6.0.vsix`
  - `ltcai-4.6.0.tgz`
  - `src-tauri/target/release/bundle/dmg/Lattice AI_4.6.0_aarch64.dmg`

## v4.5.1 RC 릴리스 노트 (2026-06-13)

Lattice AI v4.5.1 — Product Reimagining RC. `main` after v4.5.0 위에서 Brain
Core extraction, StorageEngine, FastAPI, Tauri, backup/restore, model runtime,
and portability capabilities는 유지하고 desktop product surface, navigation,
onboarding, information hierarchy, and visual language를 first-principles
Digital Brain experience로 교체한다. 이 RC 작업은 tag, GitHub Release,
PyPI/npm/VS Code/Open VSX publish를 포함하지 않는다.

- **Changed (Product shell)**: left-rail dashboard presentation was replaced by
  a compact premium desktop chrome, ambient brain canvas, command palette, and
  six-room product model: Home, Ask, Add, Automate, Library, Care.
- **Changed (Onboarding)**: first-run setup now reads as a user journey:
  Make it yours -> Choose a space -> Meet your Mac -> Pick a brain -> Install
  locally -> Try a question -> Set the pace -> Explore memory.
- **Changed (Navigation)**: legacy hash routes remain compatible, but visible
  navigation no longer exposes the old screen taxonomy as the primary product
  model.
- **Changed (Visual language)**: new carbon/warm-white base with jade, amber,
  violet, blue, and coral accents; fixed responsive type sizing; card radii
  remain 8px or smaller.
- **Preserved**: Brain Core, FastAPI APIs, Tauri desktop shell, StorageEngine,
  backup/restore, model runtimes, graph/search/chat/capture/automation/system
  workflows, and compatibility route aliases.
- **Expected artifacts**:
  - `dist/ltcai-4.5.1-py3-none-any.whl`
  - `dist/ltcai-4.5.1.tar.gz`
  - `dist/ltcai-4.5.1.vsix`
  - `ltcai-4.5.1.tgz`
  - `src-tauri/target/release/bundle/dmg/Lattice AI_4.5.1_aarch64.dmg`

## v4.5.0 RC 릴리스 노트 (2026-06-13)

Lattice AI v4.5.0 — Product Experience Recovery RC. `main` after v4.4.0 위에서
Brain Core extraction, StorageEngine, FastAPI, Tauri, backup/restore, portability
architecture는 redesign하지 않고 end-user setup/model/graph experience를 복구한다.
이 RC 작업은 tag, GitHub Release, PyPI/npm/VS Code/Open VSX publish를 포함하지
않는다.

- **Restored (First-run journey)**: Login -> Workspace Selection ->
  Environment Analysis -> Model Recommendation -> Model Installation -> Model
  Validation -> Mode Selection -> Brain Usage 흐름을 app shell과 command
  palette에서 다시 노출한다.
- **Changed (Model setup UX)**: Library Models는 Environment Analysis ->
  Recommended Models -> Install -> Download Progress -> Validate -> Load ->
  Ready 상태를 명시하고, runtime install/model download는 checkbox consent 없이는
  시작하지 않는다.
- **Fixed (Gemma 4 runtime regression)**: Gemma 4 MLX routing now distinguishes
  the local model metadata that first diverges at load time. Gemma 4 12B
  `gemma4_unified` is no longer shown as ready when installed MLX-VLM lacks
  `mlx_vlm.models.gemma4_unified`; it shows **Runtime update needed**. Gemma 4
  26B A4B remains on the working standard `gemma4` MLX-VLM path.
- **Changed (Basic mode)**: shared status badges, Brain graph copy, System
  readiness, and model cards avoid endpoint/module leakage in Basic mode while
  Advanced/Admin retain inspection detail.
- **Changed (Graph UX)**: graph/search copy now focuses on ideas, relationships,
  sources, focus, filtering, and readability rather than backend endpoint
  implementation.
- **Expected artifacts**:
  - `dist/ltcai-4.5.0-py3-none-any.whl`
  - `dist/ltcai-4.5.0.tar.gz`
  - `dist/ltcai-4.5.0.vsix`
  - `ltcai-4.5.0.tgz`
  - `src-tauri/target/release/bundle/dmg/Lattice AI_4.5.0_aarch64.dmg`

## v4.4.0 릴리스 노트 (2026-06-13)

Lattice AI v4.4.0 — Brain Engine Extraction Release. Brain Core implementation을
standalone `lattice_brain` package로 물리적으로 이동하고, `latticeai` 경로는
compatibility shim으로 유지한다. Storage, migration, backup/restore,
`.latticebrain` archive, FastAPI API behavior는 변경하지 않는다.

- **Changed (Physical extraction)**: knowledge graph, memory, context,
  conversations, ingestion, hooks/multi-agent/agent runtime, workflow, and
  KG portability implementations now live physically under `lattice_brain`.
- **Changed (Compatibility)**: `latticeai.brain.*` warns as deprecated while
  old `latticeai.core.*` and `latticeai.services.*` moved paths remain module
  identity aliases.
- **Added (Isolation validation)**: `tests/unit/test_lattice_brain_isolation.py`
  blocks `latticeai` imports while importing/exercising `lattice_brain`.
- **Behavior**: no user-data, storage-layout, migration, backup/restore,
  archive, graph/search/ingestion, or API behavior changes.
- **Expected artifacts**:
  - `dist/ltcai-4.4.0-py3-none-any.whl`
  - `dist/ltcai-4.4.0.tar.gz`
  - `dist/ltcai-4.4.0.vsix`
  - `ltcai-4.4.0.tgz`
  - `src-tauri/target/release/bundle/dmg/Lattice AI_4.4.0_aarch64.dmg`

## v4.3.3 릴리스 노트 (2026-06-13)

Lattice AI v4.3.3 — Dead-Code Cleanup Release. v4.3.2 RC 이후 독립 dead-code /
architecture / runtime audit cleanup과 documentation fixes가 tracked tree를
변경했으므로 v4.3.2 artifacts를 재사용하지 않고 exact-current-main
artifacts를 다시 빌드한다.

- **Changed (Cleanup release)**: post-audit dead code and obsolete release/doc
  drift are removed while preserving compatibility shims, user data paths, and
  package boundaries.
- **Fixed (Architecture docs)**: README, ARCHITECTURE.md, release notes, feature
  status, and changelog now identify v4.3.3 as the current release while keeping
  v4.3.2 reports as historical evidence.
- **Fixed (Vercel/static docs)**: Vercel remains static documentation-only and
  must not auto-detect or deploy `server.py` as a hosted FastAPI app.
- **Fixed (README badges)**: PyPI, npm, VS Code Marketplace, Open VSX, CI, and
  license badges remain restored with owner-published registry caveats.
- **Behavior**: no feature behavior changes beyond cleanup, safety, and
  documentation alignment.
- **Expected artifacts**:
  - `dist/ltcai-4.3.3-py3-none-any.whl`
  - `dist/ltcai-4.3.3.tar.gz`
  - `dist/ltcai-4.3.3.vsix`
  - `ltcai-4.3.3.tgz`
  - `src-tauri/target/release/bundle/dmg/Lattice AI_4.3.3_aarch64.dmg`

## v4.3.2 RC 릴리스 노트 (2026-06-13)

Lattice AI v4.3.2 — Product Polish & Graph UX Overhaul. `main` after v4.3.1
위에서 frontend/desktop product surface를 다듬고 Brain graph explorer를 실제
end-user workflow 중심으로 확장한다. Brain Core, storage, agent/workflow
architecture는 redesign하지 않는다. GitHub Release, tag, external package
registry publish는 이 RC 작업에 포함하지 않는다.

- **Changed (Brain graph)**: Brain landing graph is a semantic Cytoscape
  explorer with search, min-importance filter, type groups, group
  collapse/expand, focused neighborhoods, label modes, node importance sizing,
  and backend hybrid-search results.
- **Changed (Product polish)**: Brain, Ask, Capture, Act, Library, and System
  replace raw JSON dumps with structured result cards, entity lists, readable
  status panels, and honest unavailable states backed by existing APIs.
- **Changed (Portability UX)**: System exposes archive export, inspect, verify,
  import dry-run, confirmed import, restore dry-run, confirmed restore, storage,
  backup health, and Brain Network status through real FastAPI routes.
- **Fixed (Desktop lifecycle)**: Tauri app-level exit handling now kills the
  FastAPI sidecar on normal macOS quit as well as window close.
- **Fixed (Release prep)**: README badges were restored without claiming
  v4.3.2 registry publication, `ARCHITECTURE.md` was rewritten as a
  diagram-first system map, and Vercel is now static documentation-only so it
  does not auto-detect `server.py` as a FastAPI deployment.
- **Validated**: end-user self-audit screenshots/GIFs, Python compile, ruff,
  unit tests, live integration tests, frontend lint/typecheck, Playwright
  visual tests, Tauri check/build, release artifact validation, wheel smoke,
  npm pack dry-run, Markdown links, README badges, Mermaid diagrams, and Vercel
  static build/config.
- **Expected artifacts**:
  - `dist/ltcai-4.3.2-py3-none-any.whl`
  - `dist/ltcai-4.3.2.tar.gz`
  - `dist/ltcai-4.3.2.vsix`
  - `ltcai-4.3.2.tgz`
  - `src-tauri/target/release/bundle/dmg/Lattice AI_4.3.2_aarch64.dmg`

## v4.3.1 RC 릴리스 노트 (2026-06-12)

Lattice AI v4.3.1 — End-User Audit Repair RC. v4.3.0 published artifact
experience에서 확인된 P0/P1 blocker를 architecture redesign 없이 수정한다.
GitHub Release, tag, external package registry publish는 이 RC 작업에
포함하지 않는다.

- **Fixed (Desktop)**: Tauri shell resolves and starts the FastAPI sidecar from
  installed or bundled runtime paths, records sidecar logs/errors, surfaces
  status, and shuts down the sidecar on close.
- **Fixed (npm)**: npm clean install includes `requirements.txt` and fails
  honestly if dependency bootstrap cannot complete.
- **Fixed (Privacy/model load)**: Model Load refuses implicit runtime installs
  and model downloads by default; token/model presence alone cannot start
  outbound communication.
- **Fixed (Act)**: agent simulation is an unavailable state rather than a
  recorded success; workflows expose real create/import/export/run paths.
- **Fixed (Status honesty)**: stale RC label, configured port/SSO defaults,
  Postgres dependency status, sqlite-vec fallback reporting, and `.latticebrain`
  bundle-section claims now match runtime behavior.
- **Expected artifacts**:
  - `dist/ltcai-4.3.1-py3-none-any.whl`
  - `dist/ltcai-4.3.1.tar.gz`
  - `dist/ltcai-4.3.1.vsix`
  - `ltcai-4.3.1.tgz`
  - `src-tauri/target/release/bundle/dmg/Lattice AI_4.3.1_aarch64.dmg`

## v4.3.0 RC 릴리스 노트 (2026-06-12)

Lattice AI v4.3.0 — Portability & Product Hardening. `main` after v4.2.0 위에서
portable `.latticebrain` archive format, confirmed import/restore, pre-migration
backup verification, desktop sidecar lifecycle hardening, local-only startup
guards, admin product-hardening status, and exact-version release artifact
validation을 추가한다. GitHub Release, tag, external package registry publish는
이 RC 작업에 포함하지 않는다.

- **Added (Portable archive)**: encrypted `.latticebrain` archives include the
  brain DB, blobs, portable JSON state, workspace export bundles when present,
  storage metadata, provenance, and public device identity metadata.
- **Added (Backup/restore safety)**: archive inspect/verify/import/restore and
  restore dry-run APIs; destructive restore/import requires explicit admin
  confirmation.
- **Changed (Migration safety)**: live SQLite→Postgres migration creates and
  verifies a pre-migration backup before copying data.
- **Changed (Desktop)**: Tauri sidecar status/restart/shutdown commands and
  loopback-only/default-off environment guards.
- **Changed (Privacy/admin)**: external integration status reports
  `credential_present` separately from `enabled`; token presence alone does not
  enable outbound communication.
- **Expected artifacts**:
  - `dist/ltcai-4.3.0-py3-none-any.whl`
  - `dist/ltcai-4.3.0.tar.gz`
  - `dist/ltcai-4.3.0.vsix`
  - `ltcai-4.3.0.tgz`
  - `src-tauri/target/release/bundle/dmg/Lattice AI_4.3.0_aarch64.dmg`

## v4.2.0 릴리스 노트 (2026-06-12)

Lattice AI v4.2.0 — Brain Core & Storage Rebuild. `main` after v4.1.0 위에서
backend Digital Brain boundary를 독립 import package `lattice_brain`으로
분리하고 pluggable storage layer를 추가한다. SQLite는 기본값으로 유지하며,
Postgres/pgvector, Docker setup, SQLite→Postgres migration은 모두 명시적 opt-in
mode이다. GitHub Release에는 검증된 artifact만 첨부하며 PyPI, npm Registry,
VS Code Marketplace, Open VSX에는 publish하지 않는다.

- **Added (Brain Core package)**: FastAPI가 `lattice_brain.BrainCore`를 통해
  Knowledge Graph와 durable conversation store를 구성한다. 기존
  `knowledge_graph.py`, `kg_schema.py`, `latticeai.brain.*` import는
  compatibility shim으로 유지한다.
- **Added (Storage abstraction)**: `StorageEngine`, `SQLiteEngine`,
  `PostgresEngine`, `DockerPostgresWizard`, `SQLiteToPostgresMigrator`.
- **Validated (live Postgres)**: explicit Docker consent 하에서
  `pgvector/pgvector:pg16`으로 SQLite→Postgres migration integrity,
  rowid-less FTS5 shadow table handling, pgvector distance search, idempotent
  rerun, fail-closed behavior를 검증했다.
- **Added (Archives)**: AES-256-GCM encrypted `.latticebrain` archive
  create/restore support.
- **Changed (API/UI)**: OpenAPI 313 paths; System settings에서 storage status,
  consent-gated Docker setup, migration planning을 실제 API로 호출한다.
- **Preserved**: v4.1.0 frontend/desktop architecture, existing FastAPI
  contracts, SQLite user data, local-first/privacy-first/offline operation.
- **Expected artifacts**:
  - `dist/ltcai-4.2.0-py3-none-any.whl`
  - `dist/ltcai-4.2.0.tar.gz`
  - `dist/ltcai-4.2.0.vsix`
  - `ltcai-4.2.0.tgz`

## v4.1.0 RC 릴리스 노트 (2026-06-12)

Lattice AI v4.1.0 — Frontend & Desktop Rebuild. `main @ v4.0.1` 위에서
frontend와 desktop shell을 React/Vite/Tauri 2.0 기반 Digital Brain desktop
architecture로 교체한다. Backend, Brain Core, storage, agent/workflow runtime은
기존 FastAPI contract를 source of truth로 유지한다. PyPI, npm Registry, VS
Code Marketplace, Open VSX에는 publish하지 않는다.

- **Added (React desktop SPA)**: React, TypeScript, Vite, TanStack Query,
  Zustand, React Flow, Cytoscape.js, Tailwind CSS, local shadcn-style primitives,
  generated OpenAPI client를 사용하는 `/app` SPA.
- **Added (Desktop shell)**: Tauri 2.0 primary shell (`src-tauri/`)과 Electron
  fallback-only shell (`desktop/electron/`).
- **Changed (Navigation)**: Brain, Ask, Capture, Act, Library, System 중심의
  graph-first primary navigation.
- **Changed (Frontend assets)**: legacy static v3 frontend와 build/lint scripts를
  제거하고 `static/app` Vite build output을 package/release 대상에 포함한다.
- **Preserved**: local-first/privacy-first/offline operation, v4.0.1 backend API
  contracts, Brain Core, storage compatibility, durable agents/workflows, user data.
- **Expected artifacts**:
  - `dist/ltcai-4.1.0-py3-none-any.whl`
  - `dist/ltcai-4.1.0.tar.gz`
  - `dist/ltcai-4.1.0.vsix`
  - `ltcai-4.1.0.tgz`

## v4.0.1 릴리스 노트 (2026-06-12)

Lattice AI v4.0.1 — Digital Brain Platform Maintenance. `v4.0.0` 태그 이후
`main`에 포함된 검증된 변경을 패키징한다. GitHub Release에는 검증된 산출물만
첨부하며 PyPI, npm Registry, VS Code Marketplace, Open VSX에는 publish하지
않는다.

- **Added (Async runtime)**: agent/workflow run은 durable queued/running/final
  row, realtime SSE progress, cooperative cancellation, startup reconciliation을
  사용한다.
- **Added (Identity/workspace state)**: stable user UUID, centralized policy,
  local invitation token, SQLite-backed Workspace OS state + JSON compatibility
  mirror를 포함한다.
- **Changed (Frontend of record)**: legacy static HTML/CSS/JS UI를 제거하고
  compatibility route는 `/app` SPA로 redirect한다.
- **Added (SPA parity)**: account/profile/password, workspace/org admin,
  invitations, snapshots/time-machine merge-restore, activity/presence,
  run approvals/cancel, workflow triggers, Brain Network, chat context trace,
  KG provenance coverage, en/ko i18n을 포함한다.
- **Expected artifacts**:
  - `dist/ltcai-4.0.1-py3-none-any.whl`
  - `dist/ltcai-4.0.1.tar.gz`
  - `dist/ltcai-4.0.1.vsix`
  - `ltcai-4.0.1.tgz`

## v3.3.1 릴리스 노트 (2026-06-08)

Lattice AI v3.3.1 — Visual Product Rebuild. `/app`의 런타임 동작은 보존하고,
제품의 시각 언어와 정보 구조를 재구성한다. 태그, GitHub Release, 패키지
publish, 배포는 수행하지 않는다.

- **Changed (`/app` shell)**: 더 조밀한 command rail, Basic/Advanced/Admin
  navigation, local retrieval readiness footer, quiet topbar, mode-aware command
  palette로 글로벌 앱 프레임을 재구축했다.
- **Changed (Design system)**: v3.3.0 palette를 cooler neutral light/dark
  token으로 교체하고, card/panel radius를 8px 중심으로 정리했다. Buttons,
  inputs, tables, stats, empty states도 compact product UI로 재작성했다.
- **Changed (Home)**: Home을 backend/model/retrieval/memory/source/activity
  readiness dashboard로 재구성했다. 가짜 readiness는 표시하지 않는다.
- **Changed (Files / Settings / Chat)**: Files는 manual upload와 desktop
  local-agent folder connection을 명확히 분리한다. Settings는 backend,
  local-agent, model runtime, host telemetry, embedding config를 보여준다.
  Chat send/stop streaming button은 단일 handler로 안정화했다.
- **Added (Design docs)**: `VISUAL_REBUILD_NOTES_v3.3.1.md`,
  `FIGMA_SPEC.md`, updated `STYLE_SYSTEM.md`.
- **Validation target**: `npm run lint`, `npm run typecheck`,
  `npm run check:python`, unit tests, Playwright visual/runtime checks,
  `npm run build`.

## v3.2.0 릴리스 노트 (2026-06-08)

Lattice AI v3.2.0 — Feature-Complete Platform. non-enterprise use case 전체를
`/app`에서 수행할 수 있으며, Enterprise(SSO/SCIM/RBAC/compliance/DLP/VPC/
governance/multi-tenant controls)는 future work로 남긴다. 패키지 스토어
publish 및 배포는 수행하지 않는다.

- **Added (Agent platform completion)**: Multi-agent collaboration, Agent
  Registry, offline Marketplace templates, Workflow Agents, Autonomous
  Planning, Long-Term Memory + Memory Manager, Skills/Hooks/Tool/MCP
  registries를 `/app`에서 노출한다.
- **Fixed (Agent Registry UI wiring)**: `/agents/api/registry*`가 존재하지만
  `/app#/agents`에서 보이지 않던 registry metadata/version/capability/
  enablement/custom-agent registration surface를 연결했다.
- **Fixed (Skills Registry UI wiring)**: `/workspace/skills`의 실제
  `{ installed, available, registry }` payload를 `/app#/skills`가 정규화해
  installed/available skill을 모두 표시한다.
- **Fixed (Route hygiene)**: MCP/skills/plugin-directory router 중복 include를
  제거하고 public path/method 중복 등록 방지 테스트를 추가했다.
- **Added (Release audit)**: `docs/V3_2_AUDIT.md`에 20개 v3.2.0 claim의
  PASS/PARTIAL/FAIL matrix, 수정 내역, 검증 결과, artifact readiness를 기록했다.
- **Validation**: `npm run lint`, `npm run typecheck`, `npm run check:python`,
  unit/integration tests, `npx playwright test`, real `/app` browser route sweep,
  `python -m build`, `npm run build`, `npm pack`, VSIX package,
  `npm run release:validate`.

## v3.1.0 릴리스 노트 (2026-06-07)

Lattice AI v3.1.0 — Mainline Product Platform Completion. `/app`가
non-enterprise Local-First AI Workspace Platform의 기본이자 완성된 제품
경험이다. Classic 화면은 compatibility/debug route로만 유지한다. 패키지
스토어 publish 및 배포는 수행하지 않는다.

- **Changed (Classic retirement)**: Chat, Models, Agents, Files, Pipeline,
  My Computer, Settings, Knowledge Graph, Hybrid Search, Admin workflow가
  `/app`에서 완료된다. 정상 workflow는 Classic 진입이 필요 없다.
- **Added (Native model lifecycle)**: `/app#/models`에서 `/models/load`,
  `/models/unload/{model_id}`를 직접 호출한다.
- **Added (Production embedding profiles)**: local `bge-m3`,
  `nomic-embed-text`, `e5-large`, `gte-large`; Ollama `nomic-embed-text`,
  `mxbai-embed-large`, BGE-M3-compatible providers; MLX; OpenAI-compatible
  `text-embedding-3-small`/`text-embedding-3-large` profile을 제공. Hash
  embedding은 fallback only로 유지.
- **Changed (Frontend truth rule)**: v3 fallback adapter는 sample data/fake
  counter/fake health 대신 unavailable empty state를 반환한다.
- **Added (Hashed assets)**: `npm run build:assets`가
  `static/v3/asset-manifest.json` 및 hashed CSS/JS를 생성하고 `/app`가
  manifest를 통해 로드한다. Runtime HTML의 `?v=` cache-busting 제거.
- **Validation target**: `npm run lint`, `npm run typecheck`,
  `npm run check:python`, backend/integration tests, `npx playwright test`,
  browser validation, `python -m build`, `npm run build`, `npm pack`, VSIX
  package, release artifact validator.

## v3.0.0 릴리스 노트 (2026-06-07)

Lattice AI v3 — Local-First AI Workspace Platform. `/app`가 기본 제품
경험이며 로그인/SSO 후 `/app`로 진입한다. Legacy `/chat`은 rollback/debug
경로로 계속 유지한다. 패키지 스토어 publish 및 배포는 수행하지 않는다.

- **Added (/app primary shell)**: Native Chat, Knowledge Graph, Hybrid Search,
  Files, Pipeline, Agents, Models, My Computer, Settings, Admin 화면을 하나의
  v3 shell에서 제공. Personal / Organization Workspace와 Basic / Advanced /
  Admin mode를 지원.
- **Added (v3 retrieval backend)**: Knowledge Graph + SQLite Vector Index +
  Hybrid Search를 `/api/search/*`, `/api/graph*`, `/api/index/*` API로 통합.
- **Changed (Post-login routing)**: 계정 로그인, 회원가입 후 자동 로그인, SSO
  callback, PWA start URL이 `/app`를 기본 제품 진입점으로 사용.
- **Kept (Rollback path)**: `/chat` legacy page는 rollback/debug 용도로 유지.
- **Fixed (No-model chat state)**: `POST /chat`은 모델이 없을 때
  `{"error":"no_model_loaded", ...}` 형태의 명확한 JSON 400 응답을 반환하고,
  v3 Chat은 사용자에게 모델 로드 안내를 표시.
- **Changed (Hybrid Search display)**: alpha처럼 보이는 단일 값 대신 keyword /
  vector / graph fusion weights와 per-signal 점수를 표시.
- **Changed (Embedding disclosure)**: 기본 vector signal은
  `lattice-local-hash-v1` deterministic local fallback embeddings임을 명확히
  표기. Production semantic embedding model로 주장하지 않음. 향후 provider는
  Ollama, MLX, OpenAI-compatible providers, 기타 local embedding runtime 가능.
- **Fixed (CDN resilience)**: Tabler icon webfont CDN 실패 시 icon-only controls가
  compact fallback glyph를 표시해 navigation/useability를 유지.
- **Changed (Release workflow safety)**: v* tag push는 build/validation만 수행.
  PyPI/npm/Marketplace/Open VSX publish job은 제거되어 tag push가 package
  publication을 자동 실행하지 않음.
- **Validation target**: `npm run lint`, `npm run typecheck`,
  `npm run check:python`, backend unit tests, `npx playwright test`, real app
  browser validation, `python -m build`, `npm run build`, `npm pack`, VSIX
  package, release artifact validator.

## v2.2.7 릴리스 노트 (2026-06-05)

Visual System Stabilization — 실제 브라우저 화면 기준으로 Home, Chat,
Workspace Select, Onboarding, PC Environment Analysis, Recommendation Result,
Auto Setup, Mode Select, Knowledge Graph, Pipeline, My Computer, Admin/Profile/
Settings/VPC/Model State 계열 화면을 재검토하고 다크/라이트 시각 언어를 정리한다.
기능 추가 없음. 패키지 스토어 publish 는 수동 절차로만.

- **Fixed (Chat composer)**: 하단 입력 컨테이너/textarea/첨부/전송 버튼의
  흰 haze와 legacy inner border를 제거하고, outer composer shell 중심의 선명한
  다크 포커스 상태로 통일.
- **Fixed (Knowledge Graph)**: 다크모드에서 그래프 캔버스가 밝은 사각형으로
  떠 보이던 문제를 `--graph-bg` 기반 작업면으로 정리.
- **Fixed (Workspace OS)**: 관계 카드, 리스트, 입력, 태그, health/capability 카드가
  다크모드에서 흰색 표면으로 회귀하지 않도록 토큰화.
- **Fixed (Onboarding / modals)**: Workspace Select, 환경 분석, 추천 결과, 자동 설정,
  모드 선택, Pipeline, My Computer, Profile, Settings, Private VPC, Model State,
  Model Switcher를 같은 panel/modal 언어로 정리.
- **Fixed (Account contrast)**: 로그인/회원가입 카드의 제목, 입력, 창 컨트롤이
  다크모드에서 읽히도록 보정.
- **Changed (Cache-busting)**: 정적 자산 쿼리 스트링을 `?v=2.2.7` 로 갱신.
- **Added (Tests)**: `tests/visual/v227.spec.js` — Chat composer, 모바일 composer,
  Knowledge Graph canvas, Workspace OS dark inputs/cards 회귀 방지.
- **Validation target**: Python compile/pytest, npm lint/typecheck/test/build,
  Python build + twine check, npm pack, VSIX package, Playwright visual suite.

## v2.2.5 릴리스 노트 (2026-06-04)

Release Hygiene Hotfix — v2.2.4 이후 남아 있던 다크모드 foggy/washed-out
표면, 모달 스택, 정적 자산 버전 드리프트, `/favicon.ico` 404, Telegram 토큰
로그 노출 위험을 정리한다. 기능 추가 없음. 패키지 스토어 publish 는 수동 절차로만.

- **Fixed (Overlay scrim)**: 전체 화면 오버레이가 라이트 보라/흰색 반투명
  backdrops와 blur를 섞어 다크모드에서 흐릿하고 탁해 보이던 문제를
  `--overlay-scrim` 토큰 기반 스크림으로 정리.
- **Fixed (Modal stack)**: Chat의 계정/MCP/모드/모델/파이프라인/VPC/상태/파일/
  권한/온보딩/설정/내 컴퓨터 오버레이를 중앙 modal manager로 통과시켜 한 번에
  하나의 blocking modal만 활성화. Escape 닫기, backdrop 닫기, route/pagehide 정리,
  body scroll-lock 복원을 공통화.
- **Fixed (Dark surfaces)**: 모달, drawer, admin panel, local file manager, My
  Computer, onboarding, model switcher, pipeline settings 등 하드코딩 라이트 표면을
  `--modal`/`--surface`/`--surface-2`/`--surface-elevated`/`--input` 토큰으로 보정.
- **Fixed (Cache-busting)**: 모든 versioned frontend asset query를 `?v=2.2.5`로
  정규화. 특히 Chat는 `/static/scripts/chat.js?v=2.2.5` 로 로드.
- **Fixed (Favicon)**: `static/favicon.ico`를 추가하고 `/favicon.ico` route가
  packaged asset을 서빙하도록 구성.
- **Fixed (Telegram logs)**: Telegram API URL/HTTP/exception/response 로그에
  `bot123:secret` 형태의 토큰이 남지 않도록 `bot123:REDACTED` 마스킹 도입.
- **Added (Tests)**: Telegram token masking, static cache/favicon hygiene, modal
  stack/scroll-lock/permission restore, favicon availability 검증 추가.
- **Validation target**: Python compile/pytest, npm lint/typecheck/test/build,
  Python build + twine check, npm pack, VSIX package, Playwright visual suite.

## v2.2.4 릴리스 노트 (2026-06-04)

Chat Dark Mode Fix — v2.2.3 Known Issue(채팅 페이지가 다크모드에서 통째로 라이트로
렌더링되던 문제)를 완전히 해결한다. 기능 추가 없음. 모든 수정은 디자인 토큰 체계를
유지하고 `!important`·인라인 스타일 땜질을 쓰지 않으며 라이트 모드를 회귀시키지 않는다.

- **원인**: `lattice-reference.css` 의 `body.lattice-ref-chat` 가 색 토큰 16개
  (`--bg`/`--surface`/`--text`/`--border` …)를 *라이트 리터럴*로 재정의한다. body 는
  :root 의 자손이라 이 body-level 값이 `:root[data-lt-theme="dark"]` 다크 토큰을
  가려서, 채팅 페이지의 모든 `var(--token)` 이 다크에서도 라이트로 해석됐다.
- **Fixed (토큰 플립)**: `:root[data-lt-theme="dark"] body.lattice-ref-chat` 에서
  같은 16개 토큰을 `tokens.css` 다크 팔레트로 다시 가리킨다(명시도 트릭이 아닌 정식
  테마 분기). 토큰 기반 채팅 표면(본문·버블·사이드바·헤더·입력창 등)이 한 번에 다크로.
- **Fixed (하드코딩 라이트 표면)**: 토큰 대신 라이트 색을 직접 박은 표면(사이드바,
  user-strip, 헤더, mode-segmented, logout/lang 버튼, 계정/MCP/모드/워크스페이스
  모달, 모달 입력, 입력 박스, 워크스페이스 카드)을 `[data-lt-theme="dark"]` 스코프
  오버라이드로 다크 토큰(`--sidebar`/`--modal`/`--input` …)에 매핑. 모든 가시 요소의
  opaque-light 배경을 훑는 데이터 기반 스캔으로 식별.
- **Fixed (토스트)**: chat.js 인라인 cssText(라이트 하드코딩)를 토큰 기반
  `#ltcai-toast` CSS 규칙으로 이동 → 라이트/다크 자동 대응.
- **Added (테스트)**: `tests/visual/v224.spec.js`(14개) — 다크 zero-light-surface
  스캔, 다크 표면/텍스트, 라이트 비회귀 가드, 토스트, 10폭(375~3440) 가로 스크롤/
  입력창 잘림 검증. 시각 스위트 52개.
- **Changed (Cache-busting)**: 정적 자산 쿼리 스트링 `?v=2.2.4`.
- **Validation target**: responsive/theme/accessibility/visual/VSIX/Python/npm
  artifact 검증 대상. 패키지 스토어 publish 는 수동 절차로만.

## v2.2.3 릴리스 노트 (2026-06-04)

Frontend Stability & UX Fixes — 기능 추가 없이 v2.2.1 이후 발견된 실제 사용성
문제를 수정하고 전체 UI/UX 품질 감사를 수행한 안정화 릴리스. 모든 수정은 기존
디자인 토큰 시스템을 유지하고 `!important`·명시도 트릭·임시 땜질을 쓰지 않는다.

- **Fixed (로그인 입력 가독성)**: 로그인 화면 입력 필드 배경이 흰색으로
  하드코딩됐는데 입력 텍스트는 다크에서 거의 흰색으로 뒤집히는 토큰이라
  이메일/비밀번호가 "흰 배경 위 흰 글자"로 안 보였다. `[data-lt-theme="dark"]`로
  스코프된 정식 다크 로그인 테마(다크 글래스 카드/타이틀바/입력/ SSO 버튼, 밝은
  타이틀·서브타이틀)와 Chrome/Safari autofill 보정을 추가. 라이트 테마는 그대로.
- **Fixed (추천 결과 클릭 불가)**: 추천 결과의 모델 그룹(Gemma 4·Qwen3-VL·Llama 4)
  아코디언과 액션 버튼이 `#onboarding-body` 에 들어가는데, 스크롤 영역을 만들려던
  규칙이 *복합* 선택자(`.onboarding-body.lattice-ref-chat`)라 영영 매치되지 않아
  카드가 콘텐츠를 잘라 접근 불가였다. 스크롤 영역을 복원해 아코디언/버튼 도달 가능.
- **Fixed (추천 결과 스크롤 불가)**: 위와 동일 원인. `#onboarding-body` 가
  `flex:1·min-height:0·overflow-y:auto` 스크롤 영역이 되어 긴 목록을 끝까지 스크롤.
- **Fixed (추천 결과 다크 가독성)**: 온보딩 카드/내부 카드가 흰색 하드코딩이라
  다크에서 안 보였다. 오버레이 토큰을 다크 팔레트로 다시 가리키고 "Best for this
  PC" 콜아웃을 토큰화.
- **Changed (버튼 상호작용 안정화)**: 로그인·온보딩·그래프·관리자 컨트롤의
  클릭 가능 여부를 hit-testing 으로 검증(overlay/pointer-events/z-index 차단 없음).
- **Added (Playwright QA)**: 로그인 가독성(라이트/다크), 추천 결과 스크롤·아코디언·
  버튼 도달, 다크 가독성, 미처리 페이지 에러 테스트 추가(시각 스위트 38개).
- **Changed (Cache-busting)**: 정적 자산 쿼리 스트링을 `?v=2.2.3` 로 갱신.
- **Validation target**: responsive/theme/accessibility/visual/VSIX/Python/npm
  artifact 검증 대상. 패키지 스토어 publish 는 수동 절차로만 진행.

## v2.2.2 릴리스 노트 (2026-06-04)

Frontend QA Stabilization Release — 기능 추가 없이 v2.2.x 반응형 UI를 안정화하고,
전체 프론트엔드 QA에서 발견한 인터랙션 결함을 수정하며, Playwright 시각 테스트를
강화하고 README/릴리스 문서를 마무리합니다. 모든 수정은 기존 디자인 토큰 구조를
유지하고 `!important`를 추가하지 않습니다.

- **Fixed (Mobile nav)**: Knowledge Graph / Admin 햄버거 토글이 소스 순서 버그로
  모바일·태블릿에서 숨겨져 드로어 접근이 막혀 있던 문제 수정. 기본 숨김 규칙을
  노출 규칙보다 먼저 선언하도록 재배치.
- **Fixed (Admin actions)**: graph 전용 `.toolbar { position:absolute; z-index:20 }`
  규칙의 선택자가 과넓어 Admin/Chat 폼 툴바까지 새어 헤더 위로 떠오르며 새로고침/
  로그아웃 버튼 클릭을 가로막던 문제 수정. graph 외 페이지에서는 정상 흐름으로 환원.
- **Fixed (Horizontal overflow)**: Workspace 의 시각적 숨김 체크박스
  (`#computer-memory-toggle`)가 뷰포트 폭만큼 늘어나 가로 오버플로우를 만들던 문제
  수정. 1px 히트박스로 가둠.
- **Added (QA coverage)**: 라이트/다크 테마 패리티, 버튼 hit-testing, 가로 스크롤
  없음(375px~3440px 10개 뷰포트), 모바일 드로어 열기/닫기, Escape 닫기, 카드뷰
  스크롤을 자동 검증하는 Playwright 스위트 확장.
- **Changed (Cache-busting)**: 변경된 CSS/JS 가 기존 설치에도 반영되도록 정적 자산
  쿼리 스트링을 `?v=2.2.2` 로 갱신.
- **Validation target**: responsive/theme/accessibility/visual/VSIX/Python/npm
  artifact 검증 대상. 패키지 스토어 publish 는 수동 절차로만 진행.

## v2.2.1 릴리스 노트 (2026-06-04)

Frontend / UX Overhaul Release — Lattice AI를 local-first AI workspace,
AI pipeline platform, Knowledge Graph platform, multi-agent workflow platform,
local model management workspace, 그리고 Personal / Organization workspace
중심으로 정리합니다. 기능 동작은 그대로 유지하면서 모든 화면 크기에서 콘텐츠를
숨기지 않고 재배치하며, 라이트/다크 테마, 접근성, Knowledge Graph 뷰, 관리자
테이블, 파일 첨부 경험을 개선합니다.

- **Changed (Responsive UI)**: phone/tablet/laptop/desktop/ultrawide/4K 전반에
  mobile-first 반응형 레이아웃 적용. 작은 화면에서도 콘텐츠를 숨기지 않고
  재배치만 합니다.
- **Added (Light/Dark mode)**: OS 감지 + 수동 토글 + 상태 persistence 기반의
  라이트/다크 모드 추가.
- **Changed (Design tokens)**: `static/css/tokens.css`를 단일 진실 공급원으로
  하는 design-token 시스템 재구축. `!important` 기반 테마 처리를 제거했습니다.
- **Added (Accessibility)**: 44px 터치 타깃, `:focus-visible` 포커스 링,
  keyboard-safe chat composer(visualViewport inset), iOS no-zoom 입력,
  reduced-motion 지원.
- **Changed (Knowledge Graph UX)**: 리사이즈 시 re-fit하는 반응형 canvas,
  zoom 버튼, fullscreen, minimap, relationship filter, 모바일 graph/card 뷰,
  theme-aware 팔레트.
- **Changed (Admin UX)**: 넓은 admin 테이블이 모바일에서 카드로 reflow되며
  반응형 레이아웃, 다크/라이트, 더 큰 터치 타깃을 갖습니다.
- **Added (File UX)**: drag & drop 및 스크린샷 paste로 파일 첨부.
- **Changed (Model cards)**: 제작 국가, 제작 회사, 실행 방식, 인터넷 사용
  여부를 plain language로 표시.
- **Changed (Positioning)**: README, Marketplace/Open VSX 노출 문구, release
  copy를 "Local-first AI workspace for knowledge graphs, AI pipelines, and
  multi-agent coding workflows" 계열로 정렬.
- **Validation target**: responsive/theme/accessibility/visual/VSIX/Python/npm
  artifact 검증 대상. 패키지 스토어 publish는 수동 절차로만 진행.

## v2.2.0 릴리스 노트 (2026-06-04)

Multimodal-First Knowledge OS Release — Lattice AI를 단순 채팅 앱이나 모델
런처가 아니라 파일, 문서, 이미지, 스크린샷, 대화, 판단, 작업 기록을 지식화하는
AI Knowledge Graph workspace로 정렬합니다.

- **Changed (Product direction)**: README, architecture, release notes, model
  policy, AI philosophy, Knowledge Graph principles를 v2.2.0 철학에 맞춰 갱신.
- **Changed (Multimodal-first)**: local model recommendation catalog가
  Gemma 4, Qwen3-VL, Llama 4 중심의 멀티모달 모델만 추천합니다.
- **Removed (Text-only path)**: MLX-LM 추천/설치 경로와 text-only local
  fallback 모델을 제거하고 MLX-VLM 중심으로 전환.
- **Removed (Old generations)**: Gemma 2/3, Qwen2.5-VL, SmolLM, Phi, Mistral,
  DeepSeek, GPT-OSS, Llama 3.x 현재 추천 항목 제거.
- **Added (Source disclosure)**: 모델 카탈로그와 추천 응답에 제작 국가,
  제작 회사, 실행 방식, 인터넷 사용 여부, 모델명을 포함.
- **Changed (Modes)**: 기본/고급 모드는 기능 차이가 아니라 설명 밀도 차이로
  정리. 관리자 모드만 사용자/권한/감사/정책 권한을 가짐.
- **Changed (Version sync)**: Python/npm/VS Code extension/workspace/FastAPI
  `/health` version metadata aligned at `2.2.0`.
- **Validation target**: unit/integration/build/VSIX/Python/npm artifact checks.
  패키지 스토어 publish는 수동 절차로만 진행.

## v2.1.0 릴리스 노트 (2026-06-01)

Agent Platform Maturity Release — v2.0에서 도입한 Plugin SDK, Workflow
Designer, Multi-Agent Runtime, Realtime 기반을 유지하면서 실행 품질,
관찰성, 메모리, 계획, 재생 가능성, 에이전트 협업을 성숙화합니다. 모든 변경은
additive이며 v1.x/v2.0 workspace, workflow, agent history, Plugin Registry,
CLI, VS Code extension, `server:app`, `latticeai.server_app.app` 호환성을
유지합니다.

- **Added (Agent Handoff)**: `handoff_id`, source/target agent, reason, task
  summary, context packet, status, timestamps를 갖는 first-class handoff.
  handoff는 workspace-scoped, inspectable, replayable, testable입니다.
- **Added (Agent Context Packets)**: objective, task summary, workspace/graph/
  memory/workflow context, plugin outputs, constraints, reviewer notes, retry
  metadata를 구조화하고 obvious secret fields를 redaction합니다.
- **Added (Review / Retry)**: Planner -> Executor -> Reviewer review cycle,
  `approve`/`reject`/`retry` outcomes, retry reason/history/limits, reviewer
  notes, timeline integration, infinite retry 방지.
- **Added (Timeline / Replay)**: agent/workflow replay frames가 actor, time,
  reason, input, output, decision을 노출. `/agents/api/runs/{id}/replay`,
  `/workflows/api/runs/{id}/replay` 및 UI replay viewer 추가.
- **Added (Agent Memory / Planning)**: `short_term`, `workspace`, `long_term`
  memory scopes, workspace-scoped memory snapshots, plan persistence, plan review.
- **Changed (Workflow-Agent-Plugin hardening)**: plugin output이 agent context에,
  agent output이 workflow output에 들어가며 failures/retries가 run status와
  realtime feed에 전파됩니다.
- **Added (Marketplace Foundation)**: Plugin/Workflow/Agent template metadata,
  export/import, install hooks, template registry. Cloud marketplace service는
  구현하지 않았습니다.
- **Changed (Realtime Observability)**: 기존 SSE 인프라로 `agent_started`,
  `handoff_created`, `handoff_accepted`, `handoff_completed`,
  `review_requested`, `review_approved`, `retry_requested`,
  `workflow_started`, `plugin_started`, `plugin_completed`,
  `execution_failed` 등 workspace-scoped execution events를 emit합니다.
- **Changed (Version sync)**: Python/npm/VS Code/workspace/FastAPI `/health`
  version metadata aligned at `2.1.0`.
- **Validation**: handoff/context/retry/replay/memory/planning/marketplace/
  realtime unit coverage, full unit/integration/startup/import/route/plugin/
  workflow/agent/visual/VSIX/release artifact checks 대상. 패키지 스토어
  publish는 수동 절차로만 진행.

## v2.0.0 릴리스 노트 (2026-06-01)

Multi-Agent Workflow Platform — Lattice AI는 local-first AI *workspace*에서
local-first **Multi-Agent Workflow Platform**으로 확장됩니다. 네 개의 신규
서브시스템이 하나의 통합 플랫폼으로 추가되며, 모든 변경은 additive이고 v1.x
호환성(API path/schema, `server:app`, `latticeai.server_app.app`, CLI,
Workspace/Chat/Model/MCP/KG API, 기존 skills/snapshots/memories/agent·workflow
history, VS Code 확장)을 유지합니다. workspace의 신규 state key
(`plugin_registry`, `workflow_runs`)는 load 시 deep-merge로 backfill되어
파괴적 마이그레이션이 없습니다.

- **Added (Plugin SDK)**: `plugin.json` manifest, 허용목록 기반 permission 모델,
  discovery/validation/lifecycle, 그리고 permission 경계를 강제하는 실행
  boundary. 플러그인은 기존 skill을 **대체하지 않고 확장**합니다(설치 시 번들된
  skill을 기존 skill registry에 등록). 예제 플러그인 2종 포함
  (`plugins/hello-world`, `plugins/git-insights`). `/plugins/registry`,
  `/plugins/validate`, `/plugins/install|enable|disable|uninstall|execute`,
  page `/plugins/sdk`.
- **Added (Workflow Designer)**: 노드 기반 워크플로
  (trigger/tool/skill/plugin/agent/condition/output), validation, 경계가 있는
  결정적 실행 엔진, run history, JSON export/import. 레거시 `steps` 워크플로는
  자동 정규화되어 pre-2.0 history도 그대로 실행됩니다. `/workflows/api/*`,
  page `/workflows`.
- **Added (Multi-Agent Runtime 2.0)**: Planner/Executor/Reviewer/Researcher/
  Release 역할 오케스트레이션 — handoff, bounded retry, 관찰 가능한 timeline.
  run은 agent history + knowledge graph + timeline에 기록됩니다. 기본은 LLM 없이
  동작하는 결정적 runner이며 LLM runner를 주입할 수 있습니다. `/agents/api/*`,
  page `/agents`.
- **Added (Realtime Collaboration)**: in-process pub/sub bus, presence,
  SSE 기반 activity feed. workspace `event_sink`로 연결되어 모든 timeline
  이벤트가 자동으로 feed에 흐릅니다. workspace isolation 유지, single-user
  local mode 보존. `/realtime/stream`(SSE), `/realtime/feed`,
  `/realtime/presence*`, page `/activity`.
- **Added (Cross-system integration)**: `latticeai/services/platform_runtime.py`
  — 워크플로가 tool/skill/plugin/agent를 실행, agent run이 plugin/workflow를
  실행, graph 엔티티가 workflow run·agent run과 연결, 모든 활동이 통합
  timeline·realtime feed에 표시. 재귀는 구조적으로 제한됩니다.
- **Changed (Version sync)**: Python/npm/VS Code/workspace/FastAPI `/health`
  version metadata aligned at `2.0.0`.
- **Validation**: unit(신규 plugin/workflow/multi-agent/realtime 포함)/
  integration smoke/startup/import/route compatibility/release artifact 검증.
  패키지 스토어 publish는 수동 절차로만 진행.

## v1.7.0 릴리스 노트 (2026-06-01)

Graph & Collaboration Release — Graph Canvas, Collaboration UX, Enterprise Admin
UI, Skill Marketplace completion, Workspace Health, screenshot automation, and
visual smoke coverage. 모든 변경은 additive이며 API path/schema, `server:app`,
`latticeai.server_app.app`, CLI, Workspace/Chat/Model/MCP/KG API, VS Code 확장
호환성을 유지합니다.

- **Added (Graph Canvas)**: expand/collapse, focus subgraph, relationship
  highlighting, shortest-path visualization, URL/node click-through navigation,
  and source/conversation actions. 기존 `/knowledge-graph/*` 및
  `/workspace/relationships/*` endpoint 재사용, schema 변경 없음.
- **Added (Enterprise Admin UI)**: `/admin#enterprise`에 Admin Policies, Audit
  Export, SIEM Export preview, Organization Settings, Capability Status. Community
  edition은 기능 lockout 없이 disabled capability를 명확히 표시.
- **Added (Skill Marketplace completion)**: install progress(Download →
  Validate → Ready), validation status, recommended/popular/update tabs,
  version/source/install metadata.
- **Added (Workspace Health Dashboard)**: Indexed Files, Graph Nodes,
  Relationships, Installed Skills, Memory Entries, Agent Runs, Current Model,
  Last Sync Time, Workspace Status.
- **Added (Screenshot automation)**: `scripts/capture/`에 workspace, graph,
  skills, enterprise, onboarding capture scripts와 README. `npm run
  capture:*`로 exact screenshot 재생성 가능.
- **Added (Visual Regression Smoke)**: Playwright mock-server 기반 시각 smoke
  tests(`tests/visual/*`)와 nightly/PR/push GitHub Actions workflow. Workspace,
  Graph, Skills, Organization, Enterprise 화면을 검증하고 실패 report artifact를
  업로드.
- **Changed (Version sync)**: Python/npm/VS Code/workspace/FastAPI `/health`
  version metadata aligned at `1.7.0`.
- **Validation**: unit/integration/startup/import/route compatibility/MCP/model/
  visual smoke/VSIX/release artifact checks 대상. 패키지 스토어 publish는 수동
  절차로만 진행.

## v1.6.0 릴리스 노트 (2026-06-01)

Product Experience Deepening — 구조 리팩토링이 아니라 사용자가 체감하는 UX 강화와
**실제 UI 스크린샷** 갱신에 집중. API path/schema/호환성은 그대로 유지.
자세한 내용은 [`docs/CHANGELOG.md`](docs/CHANGELOG.md)의 `[1.6.0]` 항목 참고.

- **Added (Knowledge Graph UX)**: workspace에 Entity Explorer — 중요도순 엔터티
  카드 + 검색, 선택 시 inbound/outbound 관계·related entities·shortest path를 보여주는
  detail 패널, Recent Activity 피드, Workspace Memory 피드. 기존
  `/knowledge-graph/graph`·`/workspace/relationships/*` API만 사용(additive UI).
- **Added (Workspace UX)**: "Current Workspace" 요약 카드(활성 워크스페이스·역할·멤버·
  스코프 카운트)와 quick-switch 칩. workspace_id 스코핑/권한 모델 불변.
- **Added (Model Recommendation 2.0)**: 온보딩 추천 패널을 강화 — 머신 요약(OS/RAM/
  GPU/engine), top pick 콜아웃(이유·예상 RAM·다음 단계), family별 상태, 클라우드 caution.
  추정치는 "estimated"로 보수적으로 표시.
- **Added (Skill Marketplace UX)**: Recommended / Popular / Installed / Updates 탭과
  버전/카테고리/소스 표시, install·enable·disable 액션. 기존 skill lifecycle API 사용.
- **Added (Enterprise surface)**: workspace에 Enterprise Capability 패널 — 12개
  capability 상태 매트릭스(Community=모두 disabled, 게이트 없음).
- **Changed (Visuals)**: `docs/images/*`를 Playwright + 실제 서버 캡처 기반의 **실제 UI
  스크린샷**으로 교체(onboarding, model-recommendation, workspace, graph, organization,
  skills, enterprise) + 실제 UI 기반 hero.gif. architecture.png는 구조 다이어그램 유지.
- **Validation**: 단위 테스트 green, route compatibility/startup/streaming/model/MCP/KG
  contract 유지, `npm run check:python` green, Playwright로 신규 UI 렌더 검증, VSIX 빌드 검증.
- 테스트/빌드/패키징 산출물만 생성 — 패키지 스토어 publish는 수동 절차로만 진행.

## v1.5.0 릴리스 노트 (2026-06-01)

Unified Product Release — CI 복구, model recommendation, catalog 추출, Enterprise
PoC, 문서/비주얼 현대화를 한 릴리스로 통합. 자세한 내용은
[`docs/CHANGELOG.md`](docs/CHANGELOG.md)의 `[1.5.0]` 항목 참고.

- **Fixed (CI/VSIX)**: `vscode-extension/package-lock.json`에 고정돼 있던
  `@azure/core-tracing@^1.4.0`(레지스트리 최신은 1.3.1)로 인한 `npm ci` ETARGET
  실패를 lockfile 재생성으로 해결. `npm ci` → `tsc` compile → `vsce package`
  체인이 다시 green.
- **Added**: `latticeai/services/model_recommendation.py` — OS/RAM/CPU/GPU/Disk
  기반 tri-state(Recommended/Compatible/Not Recommended) 분류 + family 그룹화.
  `GET /models/recommendations` 신규 엔드포인트, `/workspace/onboarding/model-
  recommendations` payload에 `catalog` 보강.
- **Changed**: 정적 모델 카탈로그(`ENGINE_MODEL_CATALOG` 등)를
  `latticeai/services/model_catalog.py`로 추출하고 `model_runtime`에서 재export —
  `model_runtime.py` 1,973 → 1,721줄. 동작/공개 import 불변.
- **Added**: Enterprise PoC seam — `latticeai/core/enterprise_admin.py`(admin
  policies / audit export / SIEM export stub / org settings)와
  `GET /admin/enterprise`, `GET /admin/enterprise/siem-export`. Community는 모든
  Enterprise capability를 비활성으로 보고하며 어떤 Community 기능도 게이트하지 않음.
- **Added**: DeepSeek 모델 패밀리를 ollama/llamacpp 카탈로그에 안전하게 추가
  (버전 필터 정규식과 충돌하지 않는 식별자 사용).
- **Changed**: README를 릴리스 로그가 아닌 제품 소개 페이지로 재작성
  (Why / Core Capabilities / Architecture / Current Release / Documentation),
  구조 기반 다이어그램(`docs/images/*`)과 최신 아키텍처 다이어그램 삽입.
- **Validation**: 단위 테스트 266 pass, route compatibility/startup/import/
  streaming/model endpoint/MCP/KG contract 유지, `npm run check:python` green,
  VSIX 빌드 검증.
- 테스트/빌드/패키징 산출물만 생성 — 패키지 스토어 publish는 수동 절차로만 진행.

## v1.4.0 릴리스 노트 (2026-05-31)

Server App Final Decomposition — 목표 줄 수 미달 없이 핵심 클러스터를 실제
router/service 계층으로 이동.
자세한 내용은 [`docs/CHANGELOG.md`](docs/CHANGELOG.md)의 `[1.4.0]` 항목 참고.

- **Changed**: `latticeai/server_app.py` 5,381 → 1,303줄. 2,000줄 이하와
  1,500줄 이하 목표를 모두 달성.
- **Added**: `latticeai/api/{chat,tools,computer_use,local_files,permissions,garden,setup,static_routes}.py`,
  `latticeai/services/{model_runtime,tool_dispatch,upload_service,app_context}.py`,
  `latticeai/api/deps.py`.
- **Changed**: chat/history/agent, model runtime/provider helpers, tools/local/
  CU/permissions/upload, garden/setup/static UI pages, MCP/KG glue를
  `server_app.py` 밖으로 이동.
- **Added**: v1.4 decomposition guard
  (`tests/unit/test_server_app_v14_decomposition.py`)로 line-count,
  independent import, version metadata를 검증.
- **Changed**: README / RELEASE / CHANGELOG / SECURITY / package scripts의
  current-release 문맥을 v1.4.0으로 정렬하고 README 내부 0.6.0 current 충돌 제거.
- **Validation**: route compatibility, streaming contract, model endpoint
  presence, MCP/KG presence, import/startup, tools/local/CU route snapshot,
  Python/VSIX/npm packaging을 검증.
- 테스트/빌드/패키징 산출물만 생성 — 패키지 스토어 publish는 수동 절차로만 진행.

## v1.3.0 릴리스 노트 (2026-05-31)

server_app.py 추가 분해(phase 3) — 안전망 우선 구축 후 model/MCP 라우터 추출.
자세한 내용은 [`docs/CHANGELOG.md`](docs/CHANGELOG.md)의 `[1.3.0]` 항목 참고.

- **Added**: route-compatibility 안전망(`tests/unit/test_route_compatibility.py`) —
  209개 public path + import/startup + streaming/model/MCP/KG contract를 동결.
  코드 이동 **전에** 구축해 누락/리네임/깨진 import를 즉시 검출.
- **Added**: `latticeai/api/models.py`(`create_models_router`) — `/models*`,
  `/engines*`, `/setup/set-api-key`. `latticeai/api/mcp.py`(`create_mcp_router`) —
  `/mcp/*`, `/skills/*`, `/plugins/directory*`, `/mcp/call`.
- **Changed**: server_app.py ~5,948 → ~5,382줄. API path/schema, `server:app`
  import path, CLI/UI/KG/Admin/Security/VS Code 호환 전부 유지(route snapshot로 검증).
- **Note**: chat/streaming, `/tools/*`·`/cu/*`·`/local/*`·`/upload`·`/permissions`,
  ~1,700줄 model/engine provider helper 블록은 다음 패스로 이월(안전망이 이미
  해당 이동을 de-risk). 2,000줄 목표는 아직 미달성.
- CI 하드닝(VSIX compile guard, Node.js 24, 버전 한정 validator) 유지.
- 테스트/빌드/패키징 산출물만 생성 — 어떤 배포도 수행하지 않음.

## v1.2.0 릴리스 노트 (2026-05-31)

server_app.py 모듈화(routers + service layer) + workspace/org guardrail 강화.
자세한 내용은 [`docs/CHANGELOG.md`](docs/CHANGELOG.md)의 `[1.2.0]` 항목 참고.

- **Changed**: `latticeai/server_app.py`를 ~6,585 → ~5,948줄로 축소. workspace/
  Organization API와 health/engine summary endpoint를 전용 router(+service layer)로
  추출. `server_app`은 app assembly/lifespan/middleware/router include 중심.
  `server:app` import path·API path·schema 모두 유지.
- **Added**: `latticeai/api/workspace.py`(`create_workspace_router`),
  `latticeai/api/health.py`(`create_health_router`),
  `latticeai/services/{workspace_service,model_service,chat_service}.py`.
- **Changed**: workspace read/write가 `WorkspaceService` 게이트를 통과 — 비멤버는
  org read/write 불가, viewer는 write 불가, owner/admin만 멤버 관리. no-auth
  로컬 owner fallback 유지, named stranger bypass 차단.
- **Added**: graph/skills가 machine-global 공유 상태임을 `shared_global_areas`로 명시.
- **Added**: `test_server_app_modularization.py`, `test_workspace_service.py`.
- CI 하드닝(VSIX compile guard, Node.js 24, 버전 한정 validator, no `dist/*` glob) 유지.
- 테스트/빌드/패키징 산출물만 생성 — 어떤 배포도 수행하지 않음.

## v1.1.0 릴리스 노트 (2026-05-31)

Organization Workspace foundation + open-core Enterprise seam + CI/release
하드닝. 자세한 내용은 [`docs/CHANGELOG.md`](docs/CHANGELOG.md)의 `[1.1.0]` 항목 참고.

- **Added**: Personal / Organization workspace 분리 모델(`workspace_id`, `type`,
  `owner_user_id`, `members`, `roles`, `settings`)과 `/workspace/orgs/*`,
  `/workspace/registry`, `/workspace/activate`, `/workspace/editions` API.
- **Added**: owner/admin/member/viewer 역할과 read/write/manage_members/
  manage_workspace 권한. Snapshot/Memory/Agent/Workflow/Trace/Timeline에
  `workspace_id` 스코핑(`X-Workspace-Id` 헤더).
- **Added**: open-core Enterprise seam(`latticeai/core/enterprise.py`) —
  `Edition`/`EnterpriseCapability` enum + `CapabilityRegistry`. Community는 어떤
  Enterprise 기능도 활성화하지 않으며 Community 기능을 제한하지 않음.
  `docs/ENTERPRISE.md`, `docs/EDITION_STRATEGY.md` 참고.
- **Added**: `scripts/validate_release_artifacts.py` — 단일 버전 산출물 존재/버전
  일치/VSIX entrypoint 검증, `dist/*` 글롭 혼입 경고.
- **Changed**: `release.yml` Node.js 24 대응(`FORCE_JAVASCRIPT_ACTIONS_TO_NODE24`),
  `actions/checkout@v5`/`setup-node@v5`/`setup-python@v6`로 갱신. 산출물 업로드와
  `twine check`를 태그 버전으로만 한정 — **`dist/*` 글롭 업로드 금지**.
- **Changed**: 1.0.x workspace state는 로드 시 비파괴 마이그레이션으로 v1.1
  모델로 승격(레거시 레코드는 Personal workspace로 매핑).
- 테스트/빌드/패키징 산출물만 생성 — 어떤 배포도 수행하지 않음.

> **릴리스 산출물 업로드 규칙**: PyPI/npm/VSIX 업로드 시 `dist/*` 글롭을 쓰지 말고
> 항상 해당 버전 파일명만 명시한다. CI는 `validate_release_artifacts.py`로 이를
> 강제한다.

## v1.0.1 릴리스 노트 (2026-05-31)

CI packaging 회귀 수정 patch. 자세한 내용은
[`docs/CHANGELOG.md`](docs/CHANGELOG.md)의 `[1.0.1]` 항목 참고.

- **Fixed**: Release (build-only) 워크플로의 `Build VSIX` 단계가
  `Extension entrypoint(s) missing: extension/out/extension.js`로 실패하던 문제
  수정. `vsce package` 전에 TypeScript 컴파일이 없었음(`vscode-extension/out/`은
  gitignore되어 clean CI checkout에 부재).
- **Added**: `vscode-extension/package.json`에 `vscode:prepublish` → `compile`
  (`tsc -p .`) 스크립트를 추가해 `vsce package`가 항상 entrypoint를 컴파일하도록
  하고 로컬/CI 빌드 경로를 일치시킴.
- **Changed**: `release.yml`이 packaging 전에 `npm run compile` 실행 +
  `out/extension.js` 존재를 검증.
- **Changed**: Python/npm/VS Code extension/FastAPI `/health` 버전을 `1.0.1`
  으로 정렬.
- 테스트/빌드/패키징 산출물만 생성 — 어떤 배포도 수행하지 않음.

## v1.0.0 릴리스 노트 (2026-05-31)

AI workspace integration release. 자세한 내용은
[`docs/CHANGELOG.md`](docs/CHANGELOG.md)의 `[1.0.0]` 항목 참고.

- **Added**: `/workspace` command center and `/workspace/*` APIs for Graph,
  Snapshot, Memory, Agent, Workflow, Skills, and Timeline.
- **Added**: reentrant first-run onboarding, Knowledge Graph context answer traces, indexing
  dashboard, snapshots, Time Machine, Knowledge Diff, Personal Memory,
  Multi-Agent Graph, Relationship Explorer, approval-gated Local Computer
  Memory, Skill Marketplace state, and Workflow Graph.
- **Added**: VS Code workflow commands for Refactor Selection, Generate Tests,
  Send To Lattice, and Ask About Current File while preserving Explain
  Selection.
- **Changed**: Python/npm/VS Code extension/FastAPI `/health` 버전을 `1.0.0`
  으로 정렬.
- 테스트/빌드/패키징 산출물만 생성 — 어떤 배포도 수행하지 않음.

## v0.6.0 릴리스 노트 (2026-05-31)

Runtime / registry / config extraction release. 자세한 내용은
[`docs/CHANGELOG.md`](docs/CHANGELOG.md)의 `[0.6.0]` 항목 참고.

- **Changed**: `server.py`를 historical `server:app` 호환 엔트리포인트로 축소하고
  FastAPI 앱 조립/라우트 wiring을 `latticeai.server_app`으로 이동.
- **Changed**: tool dispatch, governance, permission view, MCP description,
  prompt catalog를 `latticeai.core.tool_registry.ToolRegistry`로 통합.
- **Changed**: planner / executor / critic / memory updater prompts를
  `latticeai.core.agent_prompts`로 분리. `AgentRuntime`은 injected state-machine
  core로 유지.
- **Changed**: Python/npm/VS Code extension/FastAPI `/health` 버전을 `0.6.0`으로
  정렬.
- 테스트/빌드/패키징 산출물만 생성 — 어떤 배포도 수행하지 않음.

## v0.5.1 릴리스 노트 (2026-05-31)

KGStoreV2 정규화 스키마 + 마이그레이션 하드닝 + native API 정리. 자세한 내용은
[`docs/CHANGELOG.md`](docs/CHANGELOG.md)의 `[0.5.1]` 항목 참고.

- **Changed**: `attrs._kg` 패스스루 제거 — legacy 타입을 무손실 `NodeType`/
  `EdgeType` superset으로 정규화(`type`), 원본은 `legacy_type` 칼럼에 보존.
  summary/metadata 1급 칼럼화. 엣지 정체성 `(source,target,legacy_type)`.
- **Changed**: `_init_v2_schema` 마이그레이션을 단일 트랜잭션으로 원자화(중간 실패
  롤백, legacy 무손상). 프로젝션이 legacy 값을 verbatim 저장 → 뷰가 byte-faithful.
- **Removed**: production 미사용 native KGStoreV2 데이터 API(`upsert_*`/`get_node`/
  `search_*`)·`Node`/`Edge` 모델·관련 dead helper 제거. `KGStoreV2`는 schema/init/
  projection 지원 역할만 유지. 테스트의 직접 의존 제거.
- 단위 테스트 192 통과. 빌드 산출물만 생성 — 어떤 배포도 수행하지 않음.

## v0.5.0 릴리스 노트 (2026-05-31)

MLX 샘플링 API 호환성 버그 수정 + 릴리스 워크플로 build-only 전환. 자세한
내용은 [`docs/CHANGELOG.md`](docs/CHANGELOG.md)의 `[0.5.0]` 항목 참고.

- **Fixed**: 로컬 MLX 추론에서 `generate_step() got an unexpected keyword
  argument 'temp'` 오류 수정 — `temp=` 대신 `sampler=make_sampler(temp=…)` 전달
  (mlx_lm ≥ 0.20 / mlx_vlm API 변경 대응, 8개 호출부).
- **Changed**: 릴리스 워크플로를 build-only로 전환 — publish job 4종과
  `if: secrets.*` 제거, 테스트·빌드까지만 수행.
- 빌드 산출물만 생성 — 어떤 배포도 수행하지 않음.

## v0.4.0 릴리스 노트 (2026-05-31)

Knowledge Graph v2 read/write cutover. 자세한 내용은
[`docs/CHANGELOG.md`](docs/CHANGELOG.md)의 `[0.4.0]` 항목 참고.

- KGStoreV2 read/write cutover 완료 (legacy ↔ v2 동등성 보장)
- Dual-write projection 도입 (legacy 타입/summary/metadata를 `attrs._kg`에 보존)
- 모든 그래프 read에 deterministic ordering(`… , id ASC`) 적용
- 삭제 미러링 완성 (clear_all / delete_conversation / 로컬 폴더 재인덱싱)
- Legacy/V2 equivalence test suite 추가, 단위 테스트 **181 pass**
- 빌드 산출물만 생성 — 어떤 배포도 수행하지 않음

> 참고: legacy `nodes`/`edges`는 여전히 durable write source이며, v2는 동일
> 트랜잭션에서 갱신되는 프로젝션입니다. `LATTICEAI_KG_READ_V2=0`으로 legacy
> read 경로로 즉시 롤백할 수 있습니다.

## TODO — 후속 작업 (이번 릴리스 범위 밖)

완료된 항목 (KGStoreV2 정규화 리팩터링):

- ✅ **`migrate_legacy_to_v2()` 제거** — dead code 제거. 리프로젝션은
  `knowledge_graph._backfill_v2_if_needed` 단일 경로로 통합. CLI `migrate`
  서브커맨드도 제거.
- ✅ **KG schema redesign / `NodeType` 재설계** — `attrs._kg` 패스스루 제거,
  legacy 자유문자열 타입을 무손실 `NodeType`/`EdgeType` superset으로 정규화
  (`type`), 원본은 `legacy_type` 칼럼에 보존. summary/metadata는 1급 칼럼으로
  승격. 엣지 정체성은 `(source,target,legacy_type)`로 보존.
- ✅ **마이그레이션 원자성** — `_init_v2_schema`의 DROP→CREATE→VIEWS→BACKFILL→
  version-stamp 전체를 단일 트랜잭션(`BEGIN` + `_exec_script`로 implicit-commit
  회피)으로 처리. 중간 실패 시 전부 롤백 → 이전 프로젝션·version 보존, 다음 기동에
  재시도. legacy `nodes`/`edges`는 마이그레이션이 절대 건드리지 않음(손상 불가).
- ✅ **뷰 byte-faithfulness** — 프로젝션이 legacy `title`/`summary`/`metadata_json`을
  **verbatim** 저장(truncation·`sort_keys` 재인코딩 제거). 절단/정렬은 `_upsert_*`이
  legacy 기록 시 1회 수행하고 동일 값을 프로젝션에 전달. NULL summary·비정렬 멀티키·
  초과 길이까지 뷰가 legacy와 byte-identical(`test_view_is_byte_faithful_to_legacy`).
  `summary` 칼럼은 nullable로 변경. (projection_version 3→4 → 자동 리빌드.)
- ✅ **dual-write 불변식 가드** — 모든 legacy write는 `_upsert_*`(유일한 2개
  writer)를 경유하고 모든 delete는 v2에 미러됨을 구조적으로 확인. 런타임 진단
  `_v2_sync_report()`(legacy↔v2 id-set 일치) 추가 + 불변식 테스트.

남은 항목:

- **`KGStoreV2.upsert_*` / read API 정리** — 프로젝션은 raw SQL, read는 뷰를
  쓰므로 production 경로 미사용. (단, `test_document_generation`이 native
  `upsert_node`/`get_node`를 사용하므로 정리 시 동반 조정 필요.)
- **dual-write 모니터링 자동화** — `_v2_sync_report()`는 현재 테스트/진단용.
  주기적/기동시 헬스체크로 노출하면 우회 write 회귀를 조기 감지 가능.

## 0) 릴리스 전 체크

1. `python3 -m pytest tests/unit/ -v` — 단위 테스트 모두 통과 확인
2. `docs/CHANGELOG.md`의 최신 항목 작성 완료
3. CI(GitHub Actions) `ci.yml`이 main에서 green

## 1) 공통 준비

1. 버전 업데이트(세 곳 모두 동일하게 유지)
   - `package.json` (root)
   - `pyproject.toml`
   - `vscode-extension/package.json`
2. 루트에서 빌드/기본 검증
   - `npm run check:python`
   - `npm run release:artifacts`
   - `npm run release:validate`

현재 `v4.6.0` 기준 필수 산출물:

```text
dist/ltcai-4.6.0-py3-none-any.whl
dist/ltcai-4.6.0.tar.gz
dist/ltcai-4.6.0.vsix
ltcai-4.6.0.tgz
src-tauri/target/release/bundle/dmg/Lattice AI_4.6.0_aarch64.dmg
```

## 2) npm 배포

1. 로그인
   - `npm login`
2. 배포
   - `npm run publish:npm`
   - 직접 실행 시:
     ```
     npm publish "ltcai-4.6.0.tgz" --access public
     ```

## 3) PyPI 배포

1. 업로드 도구 설치
   - `python3 -m pip install --upgrade build twine`
2. 빌드
   - `npm run build:python`
3. 업로드
   - `npm run publish:pypi`  ← 권장 (`$npm_package_version` 자동 사용)
   - 직접 실행 시:
     ```
     python3 -m twine upload "dist/ltcai-4.6.0-py3-none-any.whl" "dist/ltcai-4.6.0.tar.gz"
     ```

참고:
- TestPyPI 먼저 쓰려면:
  ```
  python3 -m twine upload --skip-existing --repository testpypi \
    "dist/ltcai-4.6.0.tar.gz" "dist/ltcai-4.6.0-py3-none-any.whl"
  ```

## 4) VS Code / Cursor / Antigravity 확장 배포

`vscode-extension` 디렉터리 기준:

1. 의존성 설치 및 빌드
   - `npm install`
   - `npm run build`
2. VSIX 생성
   - `npm run package:vsix`
3. VS Code Marketplace 배포
   - `npm run publish:vscode`  ← 권장 (`$npm_package_version` 자동 사용)
   - 직접 실행 시:
     ```
     npx vsce publish --packagePath "../dist/ltcai-4.6.0.vsix"
     ```
4. Open VSX 배포 (Cursor/일부 포크 호환)
   - `npm run publish:openvsx`  ← 권장 (`$npm_package_version` 자동 사용)
   - 직접 실행 시:
     ```
     npx ovsx publish "../dist/ltcai-4.6.0.vsix"
     ```
5. 로컬 설치 (VS Code/Cursor/Antigravity)
   - `npm run install:all`

토큰:
- VS Code Marketplace: `vsce login <publisher>`
- Open VSX: `ovsx create-namespace <publisher>` / `ovsx publish ... -p <token>`

## 5) Antigravity/Cursor 관련 메모

- `Cursor`, `Antigravity`는 VSIX 설치가 가능하므로 `install:all`로 로컬 검증 가능.
- 원격 “스토어 등록”은 해당 스토어 정책/토큰이 필요합니다.
- 스토어 API/토큰 준비 후에는 같은 VSIX를 재사용해 등록하면 됩니다.
