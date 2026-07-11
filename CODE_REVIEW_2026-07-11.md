# Lattice AI 전체 코드 리뷰 (2026-07-11)

- **대상 버전**: 9.0.0 (main, working tree clean 시점)
- **범위**: `latticeai/`, `lattice_brain/`, `tools/`, `frontend/src`, `vscode-extension`, `browser-extension`, `desktop/`, 테스트·릴리스 스크립트, 레포 위생
- **방식**: 정적 메트릭 + 핵심 위험 파일 라인 검증 + 보안/아키텍처/프런트엔드 3축 병렬 탐색
- **이전 리뷰 대비**: 2026-07-06 / 07-07 / 07-08 리뷰 항목의 **수정 여부 재검증** 포함

---

## 0. 한 줄 총평

기능 표면과 패키지 경계는 이미 “로컬 우선 Digital Brain” 수준까지 올라왔다.  
다음 병목은 **새 기능이 아니라 (1) 남은 접근제어 fail-open, (2) 조용한 성공 UX, (3) 1k+ god-module / ambient DI 부채**다.

---

## 1. 잘 되어 있는 점

| 영역 | 평가 | 근거 |
|------|------|------|
| Import-safe 앱 조립 | 강함 | `app_factory` / `server_app.__getattr__` — import 시 GPU/싱글톤/파일 쓰기 없음 |
| Brain Core 격리 | 강함 | `lattice_brain` → `latticeai` 금지 가드 테스트 존재 |
| Tool / Agent 경계 | 양호 | `ToolRegistry` + `ToolDispatchService` + `AgentRuntime` façade |
| KG 안정화 | 양호 | v2 projection, dual-read, equivalence 테스트, mixin 분해 진행 |
| 세션 보안 기본기 | 양호 | scrypt 비밀번호, 세션 hash-at-rest, disabled 계정 fail-closed |
| 7/8 Critical 일부 수정 | 확인됨 | `local_read` auto_approve 해제, `run_command` path traversal 차단, `/mcp/tools` 인증 |
| 테스트 밀도 (Python) | 강함 | unit ~119, integration 소수, release/openapi/i18n 검사 스크립트 |
| 릴리스 규율 | 강함 | exact-version artifact 검증, `dist/*` 금지 방향 |

---

## 2. 이전 Critical 재검증 (7/8 리뷰)

| # | 이슈 | 상태 | 근거 |
|---|------|------|------|
| 1 | 텔레그램 chat-id 허용목록 없음 | **미해결 (Critical)** | `telegram_bot.py` 메시지 루프가 모든 chat 자동 `register_chat_id` |
| 2 | 초대 게이트 `authorized=true` 쿠키 | **미해결 (Critical)** | `static_routes.py:68-74` 평문 리터럴 신뢰 |
| 3 | `local_read`/`local_list` auto_approve | **수정됨** | `_rc()` → `auto_approve=False` |
| 4 | `run_command` `../` 탈출 | **수정됨** | `_validate_command_paths` |
| 5 | `GET /mcp/tools` 인증 누락 | **수정됨** | `require_user(request)` |
| 6 | KG workspace 스코프 fail-open | **미해결 (Critical)** | `retrieval.py` 예외 시 `{}` → legacy-global 노출 |
| 7 | Computer Use 정책 | **부분 수정** | HTTP `/cu/*`에 `require_user`+policy 있음. 스크린샷은 여전히 auto_approve |
| 8 | 채팅 no-model 빈 파일 / run stuck / intent 과탐지 | **대부분 수정** | 파일 액션 전 model 체크, run_executor except, intent 패턴 좁힘 |

**결론**: 7/8 보안 Critical 6건 중 **3건 수정 / 3건 잔존**. 잔존 3건이 공개·다사용자 시나리오의 최상위 리스크다.

---

## 3. 개선 우선순위 요약

| 순위 | 우선순위 | 주제 | 영향 |
|------|----------|------|------|
| P0 | 보안 | 텔레그램 ACL, 초대 쿠키, KG fail-closed | 원격 제어 / 게이트 우회 / 교차 워크스페이스 유출 |
| P0 | 신뢰성 | 프런트 `ApiResult` 조용한 성공 | 서비스 장애를 “빈 Brain”으로 오인 |
| P1 | 정책 | 스크린샷 auto_approve, knowledge/home 도구 스코프, Discord 토큰 노출 | 데스크톱/볼트 데이터 유출 |
| P1 | 아키텍처 | `app_factory` ambient DI, `model_runtime` 전역, `chat.py` god-router | 변경 비용·회귀 위험 |
| P2 | 유지보수 | runtime 얕은 모듈 통합, god-file 분할, root 패키지 이전 | 온보딩·리팩터 속도 |
| P2 | 프런트 | BrainHome 분할, i18n/CSS 분할, FE 단위 테스트 | UI 확장 병목 |
| P3 | 위생 | VSIX 잔재, dual desktop, 문서/버전 하드코딩 | 클론·릴리스 잡음 |

---

## 4. 보안 / 접근 제어

### 4.1 Critical — 지금 당장

#### C1. 텔레그램 봇 chat-id 허용목록 없음
- **위치**: `latticeai/integrations/telegram_bot.py` (~944–945)
- **문제**: 들어오는 모든 메시지를 처리하고 `register_chat_id`로 영구 등록. 웹 UI 대화 미러링·스크린샷(`/ss`)·에이전트 명령이 허용목록 없이 열림.
- **영향**: 봇 username만 알면 소유자 데스크톱/대화에 접근 가능.
- **개선**:
  1. `LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS` (쉼표 구분) 필수 또는 기본 거부
  2. allowlist 통과 전에 `register_chat_id` 금지
  3. callback query에도 동일 ACL
  4. API 호출용 `LATTICEAI_SERVER_SESSION_TOKEN` 필수화 (sessions.json 평문 스캔 중단)

#### C2. 초대 게이트가 정적 쿠키를 신뢰
- **위치**: `latticeai/api/static_routes.py:62-74`
- **문제**: `Cookie: authorized=true` 만으로 게이트 통과. 서명/세션 바인딩 없음.
- **영향**: 공개 배포 + 초대 게이트 환경에서 게이트 무력화 → 계정 페이지 진입 → 빈 설치 시 첫 가입자 admin 레이스와 결합 위험.
- **개선**:
  1. HMAC 서명 쿠키(secret + exp + nonce) 또는 서버 세션 claim
  2. 평문 `"true"` 완전 제거
  3. 기본 초대 코드 `gemma-lattice-ai` (`config.py:198`) 제거 — 공개 모드 부팅 시 랜덤 강제

#### C3. Knowledge Graph 워크스페이스 스코프 fail-open
- **위치**: `lattice_brain/graph/retrieval.py` `workspaces_of` / `filter_scoped_nodes` (~95–128)
- **문제**:
  - `nodes_v2` 조회 예외 → `return {}`
  - 스코프 맵에 없는 id → `None`(legacy-global)으로 취급 → **모두에게 노출**
- **영향**: 프로젝션 손상/마이그레이션 중 조용한 교차 워크스페이스 데이터 유출.
- **개선**:
  1. 예외 시 fail-closed: 빈 결과 또는 에러 전파
  2. multi-tenant 모드에서 unknown id 기본 비공개
  3. legacy-global은 `include_legacy_global=True` 명시 시에만
  4. 회귀 테스트: “쿼리 실패 시 타 워크스페이스 노드가 절대 안 나옴”

### 4.2 High

| ID | 문제 | 위치 | 개선 |
|----|------|------|------|
| H1 | `computer_screenshot`/`computer_status` auto_approve=True | `tool_registry.py:168-169` | HITL 승인 또는 desktop-control capability |
| H2 | Discord 권한 알림에 **전체 승인 토큰** 포함 | `permissions.py:100-108, 142` | hint(8자)만 전송, 승인 UI 딥링크 |
| H3 | `knowledge_search`/`obsidian_*` auto_approve + home 샌드박스 | `tool_registry.py:164-167`, `tools/knowledge.py` | 워크스페이스/유저 스코프, consent |
| H4 | 채팅 intent가 `network_status()`를 policy 게이트 밖 호출 | `chat.py` network intent 경로 | `enforce_tool_policy` 또는 admin-only |
| H5 | 기본 초대 코드 하드코딩 | `config.py:198` | 공개 모드에서 강제 설정 |

### 4.3 Medium

| ID | 문제 | 개선 |
|----|------|------|
| M1 | 세션 쿠키에 `Secure` 플래그 없음 (HTTPS/터널 시) | non-loopback / public mode에서 Secure |
| M2 | `require_auth=False` 로컬 기본 — 바인딩 실수 시 전면 개방 | non-loopback 강제 인증 유지 + 시작 경고 강화 |
| M3 | 비인증 `/status` `/mode` `/engines` 정찰 정보 | 민감 필드는 인증 필요 |
| M4 | `permission_queue.json` non-atomic / 권한 모드 불명확 | `atomic_write_json` + 0o600 |
| M5 | `/mcp/tools`가 절대 `AGENT_ROOT` 경로 반환 | 상대/마스킹 |

### 4.4 보안 점수카드

| 항목 | 점수 | 메모 |
|------|------|------|
| 로컬 싱글유저 loopback | B+ | 기본 설계에 맞음 |
| 공개/터널 배포 | D+ | 초대 쿠키·텔레그램·기본 invite |
| 다사용자 워크스페이스 격리 | C | KG fail-open이 치명적 |
| 도구 정책 일관성 | B- | local FS 개선, desktop/knowledge 잔존 |
| 시크릿 취급 | C+ | Discord full token, 일부 파일 모드 |

---

## 5. 아키텍처 / 유지보수

### 5.1 규모 (생성물 제외 핵심 소스)

| 파일 | 대략 LOC | 진단 |
|------|----------|------|
| `frontend/src/i18n.ts` | 2,149 | 단일 파일 번역 맵 |
| `latticeai/core/workspace_os.py` | 1,405 | OS façade + 상수 집중 |
| `latticeai/app_factory.py` | 1,238 | composition god-procedure |
| `latticeai/services/model_runtime.py` | 1,166 | 전역 호환 + 엔진/로드 혼재 |
| `lattice_brain/graph/discovery_index.py` | 1,126 | 로컬 인덱싱 mixin 비대 |
| `latticeai/api/chat.py` | 1,111 | 채팅/에이전트/히스토리/문서 혼재 |
| `latticeai/services/memory_service.py` | 1,038 | 다계층 메모리 파사드 |
| `latticeai/integrations/telegram_bot.py` | 1,014 | 통합 표면 전체 |
| `frontend/src/styles/experience.css` | ~3,800 | 제품 CSS 거대 단일 파일 |
| `frontend/src/styles.css` (합산 import 경로) | ~8,700 | 토큰/경험/테일윈드 혼합 |

`except Exception` 약 **367회** (`latticeai`/`lattice_brain`/`tools`/`frontend` 범위). 의도적 방어가 많으나 **silent empty return** 패턴이 데이터 유실·스코프 fail-open과 결합하면 위험.

### 5.2 AGENTS.md 리팩터 순서 대비 현황

| 우선순위 | 상태 | 남은 일 |
|----------|------|---------|
| 1. AgentRuntime 추출 | 구조 완료 | 이중 이름(`core.agent.AgentRuntime` alias vs Brain façade) 정리 |
| 2. ToolRegistry 분리 | 구조 완료 | `tools/` 패키지 → `latticeai.tools` 이전, governance alias 정리 |
| 3. Config 중앙화 | 상당 부분 | `model_runtime` bare globals / env 재조회 제거 |
| 4. Server 분해 | 1차 완료 | `dict(locals())` ambient DI → typed stages |
| 5. KG 안정화 | 상당 부분 | fail-closed 스코프, discovery_index 추가 분할 |
| 6. 문서 동기화 | 프로세스 존재 | 리뷰/로드맵 버전 하드코딩 정리 |
| 7. UI 향상 | 진행 중 | BrainHome 분할, ApiResult 정직성 |

`architecture_readiness`가 **심볼 존재만으로 complete**를 찍는 구조는 잔여 부채를 가린다. 게이트를 “심볼 + 금지 패턴(예: `build_runtime_namespace(locals())`, module-level `STATE` dual sync)” 수준으로 강화할 것.

### 5.3 구체적 아키텍처 개선 항목

#### A1. `app_factory._build` ambient DI 제거 (P1)
- **현재**: `return build_runtime_namespace(locals(), runtime_bundle=…)` — 조립 지역변수 대량 export
- **문제**: 테스트·온보딩·디버그가 전 앱 조립에 묶임
- **개선**: 단계별 typed builder
  1. `ConfigRuntime`
  2. `SecurityRuntime`
  3. `BrainRuntime`
  4. `ModelRuntime`
  5. `RouterBundle`
  - `server_app.__getattr__` export 표면을 명시 allowlist로 축소

#### A2. `model_runtime` 전역 상태 제거 (P1)
- **현재**: `ModelRuntimeState` + 모듈 레벨 이름 dual sync
- **개선**: 주입된 `state`/`router`만 사용. HTTPException 생성은 API 계층으로 이동.

#### A3. `api/chat.py` 분할 + `ChatService` 심화 (P1)
- **현재**: `ChatService` ~68줄(얇음), 실제 로직은 API 1.1k
- **분할 제안**:
  - `chat_stream` / `chat_agent_http` / `chat_history` / `chat_documents`
  - intent 경로는 이미 `chat_helpers` — service 소유로 이동

#### A4. runtime 얕은 모듈 통합 (P2)
- 1-liner/pass-through (`tail_wiring`, `app_context_runtime` 등) 인라인 또는 도메인 패키지로 통합:
  - `runtime/auth/`
  - `runtime/models/`
  - `runtime/platform/`
  - `runtime/web/`
- **삭제 테스트**: 모듈 삭제 시 복잡도가 호출자에 재출현하지 않으면 얕은 모듈.

#### A5. 레이어링 위반 해소 (P2)
API/runtime이 루트 구현을 직접 import:

| 소비자 | 루트 모듈 |
|--------|-----------|
| `api/local_files.py` | `local_knowledge_api` |
| `api/setup.py`, `api/models.py` | `auto_setup`, `setup_wizard` |
| `runtime/hooks_runtime.py` | `local_knowledge_api` |

→ 본체를 `latticeai.services.*` / `latticeai.setup.*`로 이동, 루트는 10줄 shim.

#### A6. Agent 이름 충돌 (P2)
- `latticeai.core.agent.AgentRuntime = SingleAgentRuntime` alias
- `lattice_brain.runtime.agent_runtime.AgentRuntime` 제품 façade  
→ alias 제거 또는 `SingleAgentRuntime`만 공개.

#### A7. 중복 유틸 (P3)
- `_now()` 13+ 모듈 복제 → `latticeai.core.timeutil`
- run status 상수 (`ACTIVE`/`TERMINAL`) 3곳 복제 → 단일 모듈

#### A8. `except Exception` 문화 감사 (P1–P2)
우선 실패 비용이 큰 곳부터 좁히기:

1. `retrieval.workspaces_of` — fail-open → fail-closed
2. `memory_service` empty list swallow
3. `multi_agent` role step continue
4. hooks optional path (로그 필수)

---

## 6. 프런트엔드 / UX 정직성

### 6.1 핵심 설계 이슈: `ApiResult` + React Query

`frontend/src/api/base.ts`의 Result 타입은 throw하지 않고 `{ ok:false, data: empty }`를 반환한다.  
React Query `queryFn`이 항상 resolve 하므로:

- `isError`가 거의 안 뜸
- 실패가 “빈 목록 / quiet brain”으로 캐시됨
- `DataPanel`은 올바르게 `result.ok`를 보지만, 많은 빌더는 보지 않음

### 6.2 High — 조용한 성공

#### F1. `attachAnswerProof` / `verifyModelContinuity`가 실패를 무시
- **위치**: `BrainHome.tsx:429-443`, `554-564`
- **문제**: `proofResult.ok` 미검사 → `buildBrainProof` 기본값(`status:"quiet"`, `capability:true`)로 합성 증거 표시 → 연속성 데모 “성공” 메시지
- **개선**: `!ok`면 에러 피드백, 합성 proof 부착 금지

#### F2. `buildBrainProof` healthy 기본값
- **위치**: `brainData.ts:176-219`
- **문제**: API down과 empty brain 구분 불가
- **개선**: `ok`가 아니면 `status: "unavailable"` 전용 모델

#### F3. `ActionButton`이 실패에도 `onSuccess` 호출
- **위치**: `components/primitives.tsx:359-364`
- **문제**: 라벨만 실패로 바꾸고 콜백/invalidate 실행
- **개선**: `res.ok`일 때만 `onSuccess` + invalidate

#### F4. React Query 전역 장애 배너 부재
- 핵심 쿼리(memory/graph/history/health) 다수가 `ok:false`면 “로컬 서비스 불가” 배너

### 6.3 구조 / 유지보수

| ID | 개선 |
|----|------|
| F5 | `BrainHome.tsx`(~800) → hooks: `useBrainChat`, `useBrainIngestion`, `useBrainHistory`, `useBrainProof` |
| F6 | `i18n.ts` 네임스페이스 분할, 버전 문자열을 `package.json`에서 주입 |
| F7 | `experience.css` 표면별 분할 (shell/conversation/graph/capture) |
| F8 | Act/System/Library 하드코딩 문자열 i18n allowlist 축소 |
| F9 | `Ask.tsx` re-export 정리 |
| F10 | **프론트 단위 테스트 전무** — vitest로 `base.ts` empty-shape, `brainData` 파서, conversation session 추가 |
| F11 | Playwright: `ok:false` mock → empty quiet가 아닌 error UI 단언 |

### 6.4 클라이언트 주변

| 클라이언트 | 평가 | 개선 |
|------------|------|------|
| Browser extension | A- | 유지. 범위 작음, 테스트 있음 |
| VS Code extension | B | 구버전 VSIX 4개 제거 (`0.3.2`~`1.0.0`), 소스 구조 정리 |
| Tauri | B+ | 주 데스크톱 경로. `target/` 로컬 비대 — 문서화 |
| Electron | C | 보조 셸. 포트(8765 vs 4825) 문서 정렬 또는 experimental 표기 |

---

## 7. 테스트 / 품질 게이트

### 강점
- Python unit 폭넓음 (runtime seams, KG v2–v4, agent, security, release)
- OpenAPI drift / current-release docs / i18n literal / markdown links 검사
- exact-version release artifact 검증

### 공백
| 공백 | 권장 |
|------|------|
| 프론트 unit 0 | vitest + RTL 최소 세트 |
| silent-except 경로 | fail-closed 회귀 테스트 필수 |
| factory phase ordering | typed assembly 후 단계 단위 테스트 |
| architecture_readiness 낙관 | 금지 패턴 게이트 추가 |
| integration 소수 | chat stream + auth + workspace scope E2E 1–2개 |

---

## 8. 레포 위생 / 문서

### 위생 (gitignore는 대체로 맞으나 working tree 무거움)
- `agent_workspace/` 중첩 node_modules
- `output/audits` 거대 트리
- `src-tauri/target/`
- `vscode-extension/*.vsix` 구버전 4개 **tracked 가능 여부 점검 후 제거**
- root `__pycache__/`, `*.log`, `chat_history.json` 런타임 잔여

### 문서
- 리뷰 문서 복수 (`CODE_REVIEW_*`, `review*.md`, `docs/CODE_REVIEW_*`) — `docs/reviews/` 아카이브 권장
- `docs/ROADMAP_RECOMMENDATIONS.md`와 FEATURE_STATUS는 방향이 맞음
- i18n/product_readiness 등에 박힌 버전 문자열 릴리스 자동화와 연동

---

## 9. 권장 실행 로드맵

### Sprint 0 — 보안 차단 (1–3일, 기능 추가 없음)
1. 텔레그램 allowlist + 자동 등록 금지
2. 초대 쿠키 HMAC/세션화 + 기본 invite 코드 제거
3. KG `workspaces_of` / `filter_scoped_nodes` fail-closed + 회귀 테스트
4. Discord 알림 토큰 전문 제거
5. `computer_screenshot` auto_approve=False

### Sprint 1 — 신뢰성 / UX 정직성 (2–4일)
1. `ApiResult` → query throw 어댑터 또는 전역 unavailable 배너
2. `attachAnswerProof` / `verifyModelContinuity` / `ActionButton` ok 검사
3. `buildBrainProof` unavailable 상태 분리
4. chat network intent policy 게이트

### Sprint 2 — 런타임 심화 (1–2주)
1. `app_factory` typed assembly stages
2. `model_runtime` globals 제거
3. `api/chat.py` 분할 + `ChatService` 심화
4. root `local_knowledge_api` / `auto_setup` 패키지 이전

### Sprint 3 — 유지보수 / DX
1. runtime 모듈 통합
2. BrainHome/i18n/CSS 분할
3. FE unit 테스트 도입
4. VSIX/문서/레포 위생

---

## 10. 영역별 점수

| 영역 | 점수 | 한 줄 |
|------|------|------|
| 제품 방향 / 아키텍처 의도 | A- | Brain-first, registry/runtime 경계 명확 |
| 보안 (loopback 싱글유저) | B | 기본 위협 모델에 대체로 맞음 |
| 보안 (공개/다사용자) | D+ | Critical 3건 잔존 |
| 백엔드 구조 | B- | 분해 진행, god-procedure/전역 잔존 |
| KG / Brain Core | B+ | 테스트·projection 강함, 스코프 fail-open |
| 에이전트 / 도구 | B | 정책 게이트 개선, desktop/knowledge 갭 |
| 프런트 구조 | B | features 분할 양호, Brain god-file |
| 프런트 정직성(오류 UX) | C | Result+empty defaults 조용한 성공 |
| Python 테스트 | A- | 넓고 깊음 |
| 프론트 테스트 | D+ | visual smoke 수준 |
| 릴리스 엔지니어링 | A- | exact artifact 규율 |
| 레포 위생 | C- | ignore는 있으나 로컬 산출물 거대 |

**종합**: **B- (로컬 제품) / C (네트워크 노출 시)**  
9.0.0 “Code Review Closure”는 7/8 이슈의 절반을 닫았다. **남은 Critical 3건과 조용한 성공 UX를 닫기 전까지 “closure”를 완전하다고 보기 어렵다.**

---

## 11. 다음 리팩터 추천 (AGENTS.md 정렬)

1. **보안 fail-closed 3종** (텔레그램 / 초대 / KG 스코프) — 제품 신뢰의 전제
2. **Server decomposition phase 2** — typed `RuntimeBundle`, `locals()` 제거
3. **model_runtime 전역 제거** — Config/DI 중앙화 완성
4. **ChatService 심화** — API god-router 해소
5. **Frontend Result-type 정직성** — 그 다음 UI 기능 확장

---

## 12. 검증 메모 (이 리뷰에서 직접 확인한 코드)

- `tool_registry.py`: `local_list`/`local_read` → `_rc(auto_approve=False)` ✅
- `tools/commands.py`: `_validate_command_paths` ✅
- `api/mcp.py`: `/mcp/tools` `require_user` ✅
- `api/static_routes.py`: `authorized == "true"` ❌
- `telegram_bot.py`: allowlist 없음, 자동 등록 ❌
- `retrieval.py`: `except Exception: return {}` + legacy-global 노출 ❌
- `run_executor.py`: agent path `except` → `status=failed` ✅ (이전 High 수정)
- `chat.py`: file action 전 no-model 체크 ✅
- `chat_helpers.py`: network/url intent 패턴 좁힘 ✅
- `BrainHome.tsx` / `brainData.ts`: proof `ok` 미검사 ❌
- `primitives.tsx` ActionButton: 실패 시에도 onSuccess ❌
- `permissions.py`: Discord 메시지에 full token ❌
- `config.py`: 기본 invite `gemma-lattice-ai` ❌

---

*이 문서는 구현 지시서가 아니라 개선 백로그다. P0부터 닫고 커밋 단위로 회귀 테스트를 고정하는 것을 권장한다.*
