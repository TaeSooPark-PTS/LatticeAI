# Lattice AI Release Guide

이 문서는 `npm`, `PyPI`, `VS Code`, `Cursor`, `Antigravity`, `Open VSX` 배포를
한 번에 처리하기 위한 체크리스트입니다.

> **v3.0.0부터 `.github/workflows/release.yml`은 태그 push에서 빌드와 검증만 수행합니다.**
> PyPI / npm / VS Code Marketplace / Open VSX 배포는 아래 수동 절차로만
> 진행합니다. 태그 생성은 패키지 스토어 publish를 자동으로 트리거하지 않습니다.

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

Lattice AI v7.0.0 — Brain Productization Loop. 7.0.0은 6.7.0에서 정리한
route IA와 rich-page code-splitting 위에, 첫 사용자가 5분 안에 "내 자료가
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

## v6.7.0 릴리스 노트 (2026-06-18)

Lattice AI v6.7.0 — Brain IA Cleanup. 6.7.0은 6.6.0 Brain Proof Runtime 위에서
"기능은 있는데 못 가는 앱" 상태를 정리한다. Brain Home 첫 흐름에서 문서 넣기,
그래프/검색, 모델 변경, 설정으로 바로 이동할 수 있고, 기존 rich pages는 shared
Brain shell 안에서 실제 제품 IA로 열린다.

`routes.ts`는 visible product shell routes, direct product routes, legacy
compatibility aliases를 분리한다. `/chat`, `/ask`, `/graph` 같은 기존 entry URL은
계속 보존하지만, 사용자가 보는 IA는 `Lattice Brain`, `Files`, `Graph`, `Models`,
`Settings`, `Act`로 정리된다.

Frontend bundle도 정리했다. Brain explorer, Capture, Library, System, Act pages는
`React.lazy` route chunks로 분리되어 첫 Brain Home bundle에 한꺼번에 들어오지
않는다. v6.6.0의 backend-owned Brain proof, workspace-scoped ingestion, honest
empty proof 상태는 그대로 유지한다.

Expected artifacts (exact 6.7.0 names only):
- dist/ltcai-6.7.0-py3-none-any.whl
- dist/ltcai-6.7.0.tar.gz
- dist/ltcai-6.7.0.vsix
- ltcai-6.7.0.tgz
- src-tauri/target/release/bundle/dmg/Lattice AI_6.7.0_aarch64.dmg

## v6.6.0 릴리스 노트 (2026-06-18)

Lattice AI v6.6.0 — Brain Proof Runtime. 6.6.0은 6.5.0 Brain Experience
Readiness 위에서 첫 사용자가 5분 안에 "Brain이 실제로 똑똑해졌다"를 확인하도록
backend-owned proof loop를 추가한다. 핵심은 예쁜 Brain 설명이 아니라, 저장된
대화/문서/결정이 다시 불러와지고 모델이 바뀌어도 맥락 저장소가 유지된다는
증거를 Brain Home에 바로 표시하는 것이다.

`MemoryService.brain_proof()`와 `/api/memory/brain-proof`는 Brain readiness,
workspace/conversation/graph/vector counts, active model id, unified recall
sample을 하나의 증거 payload로 묶는다. App-factory interaction runtime context는
active-model getter를 Memory router까지 전달하므로 Brain proof assembly가
frontend 추정이나 monolithic factory inline wiring에 묶이지 않는다. Brain Home은
이 payload로 recallable context, model-continuity, knowledge-store proof, 최근
recall item을 첫 화면에 표시한다.

Follow-up hardening: Brain proof는 capability와 proven evidence를 분리한다.
빈 Brain은 model-independent capability만 보여주고 continuity proof는 durable
evidence가 생긴 뒤 true로 내려온다. `/api/memory/brain-proof?q=`의 자동
conversation seed도 current user/workspace로 제한한다.
추가로 chat-to-Knowledge-Graph ingestion은 workspace scope를 graph node까지
전달하고, Brain proof의 conversation count/proven 판정도 scoped conversation만
사용한다.
Direct Knowledge Graph ingest와 document upload ingestion도 request workspace를
읽어 graph/source/document node projection에 반영한다.
Brain Home에는 같은 scoped upload 계약 위에 `파일/문서 넣기` CTA를 추가해,
첫 사용자가 채팅만이 아니라 실제 파일로도 Brain을 바로 키울 수 있게 한다.
README-linked release evidence도 `output/release/v6.6.0/`로 갱신했다.

Expected artifacts (exact 6.6.0 names only):
- dist/ltcai-6.6.0-py3-none-any.whl
- dist/ltcai-6.6.0.tar.gz
- dist/ltcai-6.6.0.vsix
- ltcai-6.6.0.tgz
- src-tauri/target/release/bundle/dmg/Lattice AI_6.6.0_aarch64.dmg

## v6.5.0 릴리스 준비 노트 (2026-06-18)

Lattice AI v6.5.0 — Brain Experience Readiness. 6.5.0은 6.4.0의 Digital
Brain quality hardening 위에서 일반 사용자가 제품 상태를 더 빨리 이해하도록
Brain Home의 피드백 루프를 정리한다. Grok/OpenClaw 감사와 로컬 코드 검토 결과,
품질 경계는 단단해졌지만 "내 Brain이 지금 얼마나 자랐고 다음에 무엇을 해야
하는지"가 표면에서 충분히 보이지 않는 점이 가장 큰 사용 경험 gap이었다.

Brain Home은 이제 `MemoryService`가 계산한 Brain readiness summary를 사용해
memory, graph, relationship, source health 상태를 설명하고, Living Brain에는
1단계 conversation에서 5단계 graph까지의 진행 rail이 계속 보인다. 대화 후
memory-save feedback은 "출처와 함께 나중에 다시 불러올 수 있다"는 source-aware
recall 의미를 즉시 알려준다. 이 변경은 graph/storage schema를 바꾸지 않고
사용자가 Digital Brain 방향성을 체감하도록 만드는 backend-backed UX 개선이다.

Expected artifacts (exact 6.5.0 names only):
- dist/ltcai-6.5.0-py3-none-any.whl
- dist/ltcai-6.5.0.tar.gz
- dist/ltcai-6.5.0.vsix
- ltcai-6.5.0.tgz
- src-tauri/target/release/bundle/dmg/Lattice AI_6.5.0_aarch64.dmg

## v6.4.0 릴리스 준비 노트 (2026-06-17)

Lattice AI v6.4.0 — Digital Brain Quality Hardening. 6.4.0은 새 제품
surface를 추가하지 않고 Digital Brain의 품질 경계와 회귀 테스트를 강화한다.
핵심 변경은 workspace-scoped graph/search/memory boundary, non-destructive
Brain quality primitives, structured context guardrails, retrieval benchmark
metrics, 그리고 6.4 baseline/risk 문서화다.

`lattice_brain.quality`는 embedding fallback labelling, drift/re-index plan,
BM25 lexical scoring, hybrid fusion, reranker fallback contract, memory
candidate scoring/dedupe/conflict/retention, graph edge confidence/evidence
metrics, structured context assembly guardrails, retrieval benchmark metric
calculation을 DB schema 변경 없이 제공한다.

Graph/search API는 graph, node, neighborhood, relationship, keyword, vector,
graph, hybrid retrieval 경로에 caller workspace scope를 유지한다. Memory
Manager prune/compact/clear는 caller owner/workspace set과 교집합인 memory만
변경하며, workspace-safe graph delete가 생기기 전까지 Memory Manager graph clear는
차단한다.

Expected artifacts (exact 6.4.0 names only):
- dist/ltcai-6.4.0-py3-none-any.whl
- dist/ltcai-6.4.0.tar.gz
- dist/ltcai-6.4.0.vsix
- ltcai-6.4.0.tgz
- src-tauri/target/release/bundle/dmg/Lattice AI_6.4.0_aarch64.dmg

## v6.3.1 릴리스 준비 노트 (2026-06-17)

Lattice AI v6.3.1 — Access Runtime / i18n Follow-up. 6.3.1은 6.3.0의
Product Hardening Completion 위에서 남은 app-factory access-control debt와
Capture/Review Center localization gap을 닫는다.

`app_factory.py` 안에 있던 user role lookup, bearer/cookie session extraction,
current-user enforcement, admin enforcement, public user projection helper는
`latticeai.runtime.access_runtime`으로 이동했다. 기존 closure call signature와
HTTP status contract는 유지하며, 새 unit test가 admin/user/unauthenticated
경로와 identity projection을 직접 고정한다.

Capture와 Review Center는 tabs, headings, placeholders, action labels,
empty/error states, Run Now feedback, snooze/unsnooze 설명을 공유 i18n map으로
옮겨 Korean/English surface를 맞춘다. README release screenshots, GIF, evidence
index는 `output/release/v6.3.1/` 경로로 갱신한다. Package/runtime/static
metadata는 6.3.1로 동기화하고, package publish와 production deployment는 계속
owner-run이다.

Expected artifacts (exact 6.3.1 names only):
- dist/ltcai-6.3.1-py3-none-any.whl
- dist/ltcai-6.3.1.tar.gz
- dist/ltcai-6.3.1.vsix
- ltcai-6.3.1.tgz
- src-tauri/target/release/bundle/dmg/Lattice AI_6.3.1_aarch64.dmg

## v6.3.0 릴리스 준비 노트 (2026-06-16)

Lattice AI v6.3.0 — Product Hardening Completion. 6.3.0은 6.2.0의 App /
ProductFlow / root module decomposition 위에서 남은 hardening gap을 닫는다.
Brain archive UX는 passphrase guidance, inspect/restore preview, portability
status를 더 명확히 보여주고, memory provenance와 document ingestion 화면은 source
type, created time, retry/failure reason, Brain/Graph routing 결과를 더 직접적으로
표시한다.

Review Center Run Now는 계속 approve가 아닌 preview/regenerate 계약으로 유지되며,
backend runner는 scoped user/workspace context와 run id backlink를 테스트로
고정한다. `app_factory.py`의 Review runtime wiring은 별도 runtime helper로 빠졌고,
legacy root shim import path는 smoke test로 고정된다. Release validation은 6.3.0
exact-version wheel, sdist, npm tgz, VSIX, static asset, Tauri artifact 경로를
검증한다.

Expected artifacts (exact 6.3.0 names only):
- dist/ltcai-6.3.0-py3-none-any.whl
- dist/ltcai-6.3.0.tar.gz
- dist/ltcai-6.3.0.vsix
- ltcai-6.3.0.tgz
- src-tauri/target/release/bundle/dmg/Lattice AI_6.3.0_aarch64.dmg

## v6.2.0 릴리스 준비 노트 (2026-06-16)

Lattice AI v6.2.0 — Product Decomposition / Release Smoke Automation. 6.2.0은
6.1.0에서 정리한 Digital Brain 제품 흐름을 유지하면서 가장 큰 frontend/backend
composition surface를 줄인다. `App.tsx`는 Brain Home과 Admin Console feature
module로 분리되고, `ProductFlow.tsx`는 onboarding screen component와
design-system CSS를 조립하는 orchestrator로 축소된다.

Model download consent UX는 다운로드 크기, 저장 위치, 외부 접속 대상, 나중에
하기 경로를 명시한다. Admin Console과 onboarding copy는 공유 i18n map을 통해
Korean/English coverage를 갖는다. Root legacy module은 package module 위의
compatibility shim으로 축소되고, app-factory route registration은 typed
tool/interaction context를 사용해 route order를 보존하면서 parameter surface를
줄인다. Release smoke automation은 wheel 설치, npm tgz 내용, static asset
manifest, Tauri artifact 검증을 exact-version artifact validator 뒤에 추가한다.

Expected artifacts (exact 6.2.0 names only):
- dist/ltcai-6.2.0-py3-none-any.whl
- dist/ltcai-6.2.0.tar.gz
- dist/ltcai-6.2.0.vsix
- ltcai-6.2.0.tgz
- src-tauri/target/release/bundle/dmg/Lattice AI_6.2.0_aarch64.dmg

## v6.1.0 릴리스 준비 노트 (2026-06-16)

Lattice AI v6.1.0 — Product Hardening / Digital Brain Completion. 6.1.0은
새로운 대형 기능보다 v6.0.0의 Digital Brain 정체성을 실제 제품 흐름에 더
단단히 연결한다. 첫 실행은 모델 설치가 막혀도 로컬 Brain으로 진입할 수 있고,
Brain Home은 첫 memory 저장/확인/backup 루프를 명시한다. Review Center는
Pending/Snoozed/All, Unsnooze, Run now semantics를 유지하면서 액션 의미를 더
분명히 표시한다.

Backend hardening은 `lattice_brain`이 `latticeai`/`ltcai`를 실제 import하지
않는 AST guard, model download consent unit guard, `ltcai_cli.py` root entrypoint
유지와 `latticeai.cli.runtime` helper 분리를 포함한다. Tool dispatch authorization은
공유 ToolRegistry 위의 injectable service boundary로 분리되어 app-factory의
module global 의존을 줄인다. Chat agent runtime construction도 app-factory
assembly로 이동해 chat router는 production runtime construction을 소유하지
않고 `AppContext`로 주입받는다. v6.1 문서는 baseline scan, frontend UX
hardening, backend hardening evidence를 기록한다.

Expected artifacts (exact 6.1.0 names only):
- dist/ltcai-6.1.0-py3-none-any.whl
- dist/ltcai-6.1.0.tar.gz
- dist/ltcai-6.1.0.vsix
- ltcai-6.1.0.tgz
- src-tauri/target/release/bundle/dmg/Lattice AI_6.1.0_aarch64.dmg

## v6.0.0 릴리스 준비 노트 (2026-06-15)

Lattice AI v6.0.0 — Product Reset / Review Center Completion. 6.0.0은 5.6.0
Review Center 위에 Snoozed 필터, 명시적 Unsnooze 전이, OpenAPI 기반 Review
타입/operation 호출, Review UI 모듈 분리, v6 품질 scorecard 문서를 얹는다.
`app_factory.py`는 session/hooks/web/persistence/lifespan/automation/context/
platform/app-context/router-registration runtime seam으로 분해됐고, 364-entry
route/mount snapshot은 그대로 유지한다. `run_now`는 계속 승인이 아닌
preview/regenerate 액션이며, snooze 만료는 read-time 해석으로 유지한다.
README 스크린샷/GIF release evidence는 `output/release/v6.0.0/` 경로로
다시 캡처했고 Review Center 화면을 포함한다.

Expected artifacts (exact 6.0.0 names only):
- dist/ltcai-6.0.0-py3-none-any.whl
- dist/ltcai-6.0.0.tar.gz
- dist/ltcai-6.0.0.vsix
- ltcai-6.0.0.tgz
- src-tauri/target/release/bundle/dmg/Lattice AI_6.0.0_aarch64.dmg

## v5.6.0 릴리스 준비 노트 (2026-06-15)

Lattice AI v5.6.0 — Brain Automation Review Center. 5.6.0은 v5.4.0에서 시작한
Brain Automation Scheduler 위에 review-first 사용자 검토 계층을 추가한다.
자동화 결과는 즉시 승인된 변경처럼 보이지 않고 `/automation/reviews` 기반
Review inbox에 쌓이며, 사용자는 Act 화면의 Review 탭에서 provenance를 확인하고
Approve, Dismiss, Snooze, Run now를 선택한다. Run now는 실행/재생성만 수행하고
승인 상태로 바꾸지 않는다. TriggerService와 RunExecutor는 `review_queue: true`
옵트인 workflow에 대해서만 review item을 만든다.

Expected artifacts (exact 5.6.0 names only):
- dist/ltcai-5.6.0-py3-none-any.whl
- dist/ltcai-5.6.0.tar.gz
- dist/ltcai-5.6.0.vsix
- ltcai-5.6.0.tgz
- src-tauri/target/release/bundle/dmg/Lattice AI_5.6.0_aarch64.dmg

## v5.5.0 릴리스 준비 노트 (2026-06-15)

Lattice AI v5.5.0 — Release Coordination. 5.5.0은 v5.4.0 Brain Automation
Scheduler 기능선을 유지하면서 패키지/런타임/정적 메타데이터와 릴리스 문서를
5.5.0으로 동기화한다. consent-first Brain automation, TriggerService dedup,
LATTICE_TZ, degraded 상태, enabled:false disarming, runtime graph cleanup은 그대로
보존한다. 패키지 publish와 배포는 owner-run 절차로 남겨둔다.

Expected artifacts (exact 5.5.0 names only):
- dist/ltcai-5.5.0-py3-none-any.whl
- dist/ltcai-5.5.0.tar.gz
- dist/ltcai-5.5.0.vsix
- ltcai-5.5.0.tgz
- src-tauri/target/release/bundle/dmg/Lattice AI_5.5.0_aarch64.dmg

## v5.4.0 릴리스 준비 노트 (2026-06-15)

Lattice AI v5.4.0 — Brain Automation Scheduler. 5.4.0은 consent-first Brain
automation recipes (Daily Memory Digest, Weekly Project Review, Follow-up Radar)를
도입한다. Automate 페이지에서 reviewable draft로 설치되며, TriggerService
스케줄러가 dedup 가드, LATTICE_TZ 지원, degraded 상태, enabled:false 무장해제
처리를 통해 안전하게 트리거를 관리한다. runtime 그래프 정리와 E2E 시나리오도
함께 포함. PR #4 전체 원격 체크 통과 후 기능 게이트 완료.

Expected artifacts (exact 5.4.0 names only):
- dist/ltcai-5.4.0-py3-none-any.whl
- dist/ltcai-5.4.0.tar.gz
- dist/ltcai-5.4.0.vsix
- ltcai-5.4.0.tgz
- src-tauri/target/release/bundle/dmg/Lattice AI_5.4.0_aarch64.dmg

## v5.3.0 릴리스 준비 노트 (2026-06-14)

Lattice AI v5.3.0 — Product Clarity and Runtime Cleanup. 5.3.0은 기능 추가보다
제품 정체성, 첫인상, 문서 구조, 앱 조립부 유지보수성을 정리한다. Lattice AI의
중심 정의는 “local-first Digital Brain that keeps your knowledge durable
across any AI model”이며, private AI memory layer는 core capability, Living
Brain은 UX metaphor로 정리한다.

- **Changed (README)**: README 첫 화면을 배지/릴리스/아티팩트 중심에서 제품
  소개, 필요 이유, 사용자가 할 수 있는 일, 1분 흐름, 스크린샷 순서로 재구성했다.
- **Changed (Product identity)**: package metadata, pyproject, architecture,
  feature status, release docs, VS Code extension docs가 local-first Digital
  Brain 정의를 공유하도록 정리했다.
- **Changed (Onboarding copy)**: 첫 실행 Login, Environment Analysis,
  Recommended Models, Install/Load, Brain Chat 문구를 사용자 가치, 로컬 보관,
  모델 교체 가능성, 명시적 동의 중심으로 맞췄다.
- **Changed (Model UX)**: Basic 모델 목록은 짧은 추천 세트로 시작하고,
  Advanced에서 registry/verification/hardware/license/safety detail을 유지한다.
- **Added (Docs)**: `docs/DEVELOPMENT.md`와
  `docs/LEGACY_COMPATIBILITY.md`를 추가해 개발자 검증 흐름과 root legacy shim
  유지 이유를 분리했다.
- **Changed (Architecture)**: `app_factory.py`의 config/security/Brain runtime
  assembly seam을 `latticeai.runtime` 모듈로 이동해 composition root를 줄였다.
- **Preserved**: local-first 기본값, explicit external communication consent,
  server_app lazy compatibility, Brain Core isolation, model download consent,
  and package registry owner-run publishing.

Expected artifacts (exact 5.3.0 names only):
- dist/ltcai-5.3.0-py3-none-any.whl
- dist/ltcai-5.3.0.tar.gz
- dist/ltcai-5.3.0.vsix
- ltcai-5.3.0.tgz
- src-tauri/target/release/bundle/dmg/Lattice AI_5.3.0_aarch64.dmg

## v5.2.0 릴리스 노트 (2026-06-14)

Lattice AI v5.2.0 — User-Focused Model Transformation (major). 5.2.0은 모델
목록을 구조화된 capability registry로 전면 개편하고, Hugging Face 실제
존재/설정/토크나이저 검증 자동화 스크립트를 추가하며, 최신 멀티모달 후보
(Gemma 3, Qwen2.5-VL, Llama-3.2 Vision, Pixtral 등)는 검증 투명성용 registry에
보존한다. 사용자-facing 추천/로드 목록은 현재 load-ready family 중심으로
좁혀 catalog noise를 줄이고, verified 상태, hardware fit(RAM), modality 배지,
download/load 전략, license/safety notes, large-model limitations을 명시적
동의 전에 보여준다. 레지스트리·API·프론트엔드 전반이 사용자 중심으로
업데이트되었다.

- **Added (Registry & Verification)**: model_capability_registry.py (HardwareProfile, VerificationStatus, ModelCapability dataclass with hf_repo, modality, quant, strategies, hardware notes, license, safety). scripts/verify_hf_model_registry.py (HF API light + restricted deep config/tokenizer, --test-load for small, LARGE explicit notes, verification_report.json). 16 models (core+5 modern) all HF-present, 15 config+tok hints.
- **Changed (Backend)**: model_catalog now delegates to registry (legacy shapes 100% preserved + rich fields) and finalizes the user-facing catalog to current load-ready families while keeping non-primary candidates in the registry. model_recommendation forwards verification/hardware/strategies. /models and /models/recommendations include registry.verified + per-model 5.2 metadata.
- **Changed (Verification Contract)**: `verification.verified` and verified-model API lists now require HF presence plus config and tokenizer hints, while weights presence is exposed separately as `has_weights_hint`; registry-only candidates stay off the load-ready catalog.
- **Changed (Workspace/Marketplace)**: template install registry entries are now workspace-scoped, and `/marketplace/templates/registry` returns only the authorized workspace scope to prevent cross-workspace install-state bleed.
- **Fixed (Portability)**: SQLite restore now checkpoints WAL state before taking the pre-restore backup, so failed blob restores roll back to the current graph state consistently on Linux/Python 3.12 CI and local builds.
- **Changed (Frontend)**: Library ModelsPanel now shows multimodal badges, ✓ HF verified, hardware notes, load_strategy, license/safety notes in advanced mode, recommended_default. Guided setup copy updated for consent-first transparency.
- **Changed (UX/Product)**: Users see clear "why this model fits my machine", "HF verified?", "how it will download/load", large-model warnings before any action. Bold expansion of real multimodal options while keeping Gemma4/Qwen3-VL/Llama4 family.
- **Version**: 5.2.0 across pyproject, latticeai/__init__, npm, vscode-extension.
- **Tests/Build**: New dedicated unit tests (5/5 passed), full catalog filter + recommendation payload validation, verification script executed successfully.
- **Docs**: README/RELEASE/CHANGELOG/AGENTS-aligned sync performed.

Expected artifacts (exact 5.2.0 names only):
- dist/ltcai-5.2.0-py3-none-any.whl
- dist/ltcai-5.2.0.tar.gz
- dist/ltcai-5.2.0.vsix
- ltcai-5.2.0.tgz

## v5.1.0 릴리스 노트 (2026-06-14)

Lattice AI v5.1.0 — Product Trust & Clarity Release. 5.1.0은 Lattice AI를
일반 AI 채팅앱, 모델 런처, 노트앱, 그래프 DB가 아니라 로컬 우선 private AI
memory layer / Digital Brain으로 분명히 정리한다. 모델은 바뀌어도 사용자의
문서, 대화, 결정, 관계, 프로젝트 맥락은 Brain에 남고, 사용자가 소유하고
이동할 수 있어야 한다.

- **Changed (Product positioning)**: README 첫 화면을 release-note 중심에서
  `Your private AI memory layer. Keep your knowledge. Switch any model.` /
  `모델은 바꿔도, 내 지식은 남는 로컬 AI 브레인.` 중심의 제품 설명으로 재작성했다.
- **Changed (Trust docs)**: `PRIVACY.md`, `docs/WHY_LATTICE.md`,
  `docs/TRUST_MODEL.md`, `FEATURE_STATUS.md`, `SECURITY.md`,
  `ARCHITECTURE.md`를 v5.1.0 trust model에 맞춰 동기화했다.
- **Changed (Security defaults)**: Tauri production CSP에서 `null`을 제거하고,
  app shell CSP header, shared secret redaction, audit/log redaction,
  auto local file read fail-closed, explicit model download consent gate를
  추가했다.
- **Changed (Architecture cleanup)**: `app_factory.py`에 config/security/Brain
  runtime builder seams를 추가해 composition root를 단계적으로 줄일 수 있게 했다.
- **Changed (Release hygiene)**: `release:artifacts`가 과거 `dist/ltcai-*`와
  root `ltcai-*.tgz` 산출물을 먼저 정리한 뒤 5.1.0 정확한 파일만 다시 만들도록
  강화했다. `npm run test:integration`은 이제 로컬 uvicorn 서버를 직접 띄우고
  종료하므로 CI와 로컬 검증이 같은 방식으로 통과한다.
- **Fixed (Brain portability)**: SQLite restore 중 `knowledge_graph.sqlite-wal`
  또는 `-shm` sibling이 체크포인트로 사라지는 TOCTOU race를 안전하게 처리해
  백업/복원 테스트가 플래키하게 실패하지 않게 했다.
- **Changed (Collaboration review)**: pts_claudecode는 자동 파일 읽기, 비밀값
  중앙 redaction, CSP, 모델 다운로드 동의, app_factory 분리 지점을 검토했고,
  pts_grok은 제품 포지셔닝, why-use-this, trust/privacy 문서, 사용자/관리자
  계층 정리를 검토했다.
- **Preserved**: tracked release-note history now starts at v4.5.0; older
  release-note files are hidden from the Git tree. Package registry publish
  remains owner-run.
- **Expected artifacts**:
  - `dist/ltcai-5.1.0-py3-none-any.whl`
  - `dist/ltcai-5.1.0.tar.gz`
  - `dist/ltcai-5.1.0.vsix`
  - `ltcai-5.1.0.tgz`
  - `src-tauri/target/release/bundle/dmg/Lattice AI_5.1.0_aarch64.dmg`

## v5.0.0 릴리스 노트 (2026-06-14)

Lattice AI v5.0.0 — Multilingual Brain Foundation Release. 5.0.0은 대격변
릴리스의 첫 단계로, 이미 존재하는 AgentRuntime / ToolRegistry / Brain Core
경계를 무리하게 흔들지 않고 사용자가 바로 체감하는 제품 기반을 정리한다.
첫 실행부터 Brain 홈, Knowledge Graph, Admin Console까지 한국어/영어 선택을
저장하고 즉시 반영해 50대/60대 일반 사용자도 자기 언어로 온보딩, 모델 준비,
Brain 탐색을 진행할 수 있게 한다.

- **Changed (Language choice)**: 첫 실행 화면에 한국어/English 선택기를 추가하고
  선택 언어를 `lattice.language`에 저장한다.
- **Changed (Bilingual onboarding)**: 로그인, 환경 분석, 추천 모델, 다운로드/로드
  안내를 한국어/영어 문구로 전환한다.
- **Changed (Bilingual Brain home)**: Brain 홈, 기억/주제/관계/그래프 버튼,
  저장 피드백, 그래프 focus 문구, Admin 진입 라벨을 선택 언어에 맞춘다.
- **Changed (Release metadata)**: Python, npm, VSIX, Tauri, runtime constants,
  and static metadata are synchronized to `5.0.0`.
- **Changed (Collaboration review)**: pts_claudecode는 5.0.0 기술부채 순서를
  `config -> KG -> ToolRegistry -> AgentRuntime -> server decomposition`으로
  권고했고, pts_grok은 언어 선택/모델 준비/Brain 탐색을 사용자 체감 대격변의
  우선순위로 검토했다.
- **Preserved**: tracked release-note history remains visible from v4.5.0
  through v5.1.0. Package registry publish remains owner-run.
- **Expected artifacts**:
  - `dist/ltcai-5.0.0-py3-none-any.whl`
  - `dist/ltcai-5.0.0.tar.gz`
  - `dist/ltcai-5.0.0.vsix`
  - `ltcai-5.0.0.tgz`
  - `src-tauri/target/release/bundle/dmg/Lattice AI_5.0.0_aarch64.dmg`

## v4.7.2 릴리스 노트 (2026-06-14)

Lattice AI v4.7.2 — Intuitive Brain UX Release. Living Brain을 일반 사용자 홈으로
유지하면서 50대/60대 사용자가 봐도 더 직접적으로 이해할 수 있게 로그인,
추천 모델 시작, 기억/주제/관계/그래프 탐색, 대화 저장 피드백을 다듬는다.
Brain Core, StorageEngine, FastAPI, Tauri, backup/restore, model runtime, graph
APIs, portability, and separated Admin Console capabilities는 유지한다.

- **Changed (Safer first-run login)**: 저장된 로컬 Brain 이메일과 다른 이메일
  또는 기존 이메일의 잘못된 비밀번호는 자동 회원가입으로 새 빈 Brain을 만들지
  않고 확인 메시지를 보여준다.
- **Changed (One-click recommended setup)**: Recommended Models 단계에
  `추천대로 시작하기` primary action을 추가해 모델 선택을 모르는 사용자도 바로
  시작할 수 있게 했다.
- **Changed (Download trust)**: Install 단계는 큰 모델 다운로드가 오래 걸릴 수
  있음을 설명하고, 런타임이 제공하는 진행률만 표시해 가짜 ETA를 만들지 않는다.
- **Changed (Direct Brain views)**: Brain 홈에서 `기억 보기`, `주제 보기`,
  `관계 보기`, `그래프로 보기` 버튼으로 반복 클릭 없이 원하는 깊이를 열 수
  있다.
- **Changed (Memory/topic overview)**: Brain Chat 상단에 최근 기억, 이전 기억,
  주요 주제 요약과 대화 후 `기억에 저장됨` 피드백을 추가했다.
- **Changed (Release metadata)**: Python, npm, VSIX, Tauri, runtime constants,
  and static metadata are synchronized to `4.7.2`.
- **Changed (Architecture/docs sync)**: README, ARCHITECTURE.md, release notes,
  changelog, security posture, feature status, VS Code extension docs, recovery
  doc, and release report are synchronized to v4.7.2.
- **Preserved**: tracked release-note history remains visible from v4.5.0
  through v5.1.0. Package registry publish remains owner-run.
- **Expected artifacts**:
  - `dist/ltcai-4.7.2-py3-none-any.whl`
  - `dist/ltcai-4.7.2.tar.gz`
  - `dist/ltcai-4.7.2.vsix`
  - `ltcai-4.7.2.tgz`
  - `src-tauri/target/release/bundle/dmg/Lattice AI_4.7.2_aarch64.dmg`

## v4.7.1 릴리스 노트 (2026-06-14)

Lattice AI v4.7.1 — Admin Operations Release. Living Brain을 일반 사용자 홈으로
유지하면서 운영자 화면에 역할 권한, audit 검색/필터, 로그 보존 상태, Brain
운영 상태를 추가한다. Brain Core, StorageEngine, FastAPI, Tauri,
backup/restore, model runtime, graph APIs, and portability capabilities는
유지한다.

- **Changed (Role permissions)**: Admin Console now shows role member counts and
  capability summaries separately from the user Brain page.
- **Changed (Audit filtering)**: `/admin/audit` accepts search, actor, action,
  severity, and limit filters and reports matched/scoped event counts.
- **Changed (Retention posture)**: `/admin/log-retention` reports local
  retention days, retained events, prune candidates, and export-before-prune
  status without destructive pruning.
- **Changed (Runtime state separation)**: Admin Console data loading is isolated
  in a dedicated frontend hook so Brain chat state and admin observability state
  do not share UI runtime state.
- **Changed (Release metadata)**: Python, npm, VSIX, Tauri, runtime constants,
  and static metadata are synchronized to `4.7.1`.
- **Changed (Architecture/docs sync)**: README, ARCHITECTURE.md, release notes,
  changelog, security posture, feature status, VS Code extension docs, and
  release report are synchronized to v4.7.1.
- **Preserved**: tracked release-note history remains visible from v4.5.0
  through v5.1.0. Package registry publish remains owner-run.
- **Expected artifacts**:
  - `dist/ltcai-4.7.1-py3-none-any.whl`
  - `dist/ltcai-4.7.1.tar.gz`
  - `dist/ltcai-4.7.1.vsix`
  - `ltcai-4.7.1.tgz`
  - `src-tauri/target/release/bundle/dmg/Lattice AI_4.7.1_aarch64.dmg`

## v4.7.0 릴리스 노트 (2026-06-14)

Lattice AI v4.7.0 — Admin Separation Release. Living Brain을 일반 사용자 홈으로
유지하면서 사용자/로그/보안/정책/인덱스 운영 기능을 별도 Admin Console로
분리한다. Brain Core, StorageEngine, FastAPI, Tauri, backup/restore, model
runtime, graph APIs, and portability capabilities는 유지한다.

- **Changed (User/admin separation)**: `/app` remains the simple Brain +
  conversation surface, while `#/admin` opens a separate console for operators.
- **Changed (Admin logs UX)**: audit events, security events, user directory,
  policy chips, and Brain index rebuild controls are grouped away from the user
  chat experience.
- **Changed (Workspace-safe admin reads)**: admin summary, stats, audit, and
  sensitivity reads honor `X-Workspace-Id` / `workspace_id` when present.
- **Changed (API client coverage)**: frontend API helpers now include
  `/admin/stats` and `/admin/security/events` so the Admin Console can use the
  existing FastAPI admin/security backend.
- **Changed (Visual evidence)**: release screenshots/GIFs are refreshed for
  v4.7.0 and include the dedicated Admin Console.
- **Changed (Release metadata)**: Python, npm, VSIX, Tauri, runtime constants,
  and static metadata are synchronized to `4.7.0`.
- **Changed (Architecture/docs sync)**: README, ARCHITECTURE.md, release notes,
  changelog, security posture, feature status, VS Code extension docs, and
  release report are synchronized to v4.7.0.
- **Preserved**: tracked release-note history remains visible from v4.5.0
  through v5.1.0. This release does not remove local-first ownership,
  portability, rollback-safe restore, or the deepest-layer Knowledge Graph.
- **Expected artifacts**:
  - `dist/ltcai-4.7.0-py3-none-any.whl`
  - `dist/ltcai-4.7.0.tar.gz`
  - `dist/ltcai-4.7.0.vsix`
  - `ltcai-4.7.0.tgz`
  - `src-tauri/target/release/bundle/dmg/Lattice AI_4.7.0_aarch64.dmg`

## v4.6.1 릴리스 노트 (2026-06-14)

Lattice AI v4.6.1 — Living Brain Release Refresh. v4.6.0 PyPI upload가
immutable version/file rule에 막힌 뒤 publish 가능한 release line을 `4.6.1`로
이동한다. Brain Core, StorageEngine, FastAPI, Tauri, backup/restore, model
runtime, graph APIs, and portability capabilities는 재설계하지 않는다.

- **Changed (Version bump)**: Python, npm, VSIX, Tauri, runtime constants, and
  static metadata are synchronized to `4.6.1`.
- **Changed (README refresh)**: README now describes Login -> Environment
  Analysis -> Recommended Models -> Install & Load -> Brain Chat and links to
  fresh v4.6.1 evidence.
- **Changed (Brain depths evidence)**: screenshots/GIF cover Living Brain,
  Memory Layer, Knowledge Layer, Relationship Layer, and Knowledge Graph with
  nodes, edges, search, and focus details.
- **Changed (Brain ownership UX)**: Brain ownership controls remain available
  from the Brain home, but conversation stays first; export, backup, archive,
  inspect, and restore preview now live behind a collapsed "Care for my Brain"
  control.
- **Changed (Product positioning UX)**: first-run and empty-Brain states now
  explain why Lattice exists: models are replaceable, user knowledge is durable,
  and the Brain is a private owned context layer.
- **Changed (Restore safety)**: backup restore and encrypted `.latticebrain`
  restore now keep pre-restore backups and roll back failed DB/blob swaps so the
  Brain is not left partially restored.
- **Changed (Architecture/docs sync)**: ARCHITECTURE.md, release notes,
  changelog, security posture, feature status, and VS Code extension docs are
  synchronized to v4.6.1.
- **Preserved**: tracked release-note history remains visible from v4.5.0
  through v5.1.0; no backend architecture redesign or external registry
  publishing is included.
- **Expected artifacts**:
  - `dist/ltcai-4.6.1-py3-none-any.whl`
  - `dist/ltcai-4.6.1.tar.gz`
  - `dist/ltcai-4.6.1.vsix`
  - `ltcai-4.6.1.tgz`
  - `src-tauri/target/release/bundle/dmg/Lattice AI_4.6.1_aarch64.dmg`

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
