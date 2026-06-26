# Lattice AI Release Guide

이 문서는 `npm`, `PyPI`, `VS Code`, `Cursor`, `Antigravity`, `Open VSX` 배포를
한 번에 처리하기 위한 체크리스트입니다.

> **현재 `.github/workflows/release.yml`은 태그 push에서 빌드와 검증만 수행합니다.**
> PyPI / npm / VS Code Marketplace / Open VSX 배포는 아래 수동 절차로만
> 진행합니다. 태그 생성은 패키지 스토어 publish를 자동으로 트리거하지 않습니다.

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
