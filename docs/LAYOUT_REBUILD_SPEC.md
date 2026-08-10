# Lattice AI 레이아웃 전면 재구성 설계서

작성: pts_claudecode (기획·설계) · 2026-08-04 · 기준 커밋 `0d40ae4` · 기준 버전 `10.6.3`

이 문서의 모든 파일 경로와 줄 번호는 실제로 열어서 확인한 것이다.
추측한 경로는 한 줄도 없다.

> **증거 기준선 안내 (v11.2.0 기준).** 이 설계서가 인용하는
> `output/release/v10.6.3/screenshots/` 는 **더 이상 리포에 없다** — 릴리스 증거는
> v11.0.0 부터만 보관한다(`output/release/v11.0.0` · `v11.0.1` · `v11.1.0`).
> 따라서 아래의 "이전 캡처와 해시 대조" 절차는 그대로는 재현할 수 없다.
> 문서는 당시 측정값을 기록한 사료로 남기고, 지금 다시 채점한다면 기준선은
> **가장 최근 릴리스의 `output/release/<버전>/screenshots/`** 로 바꿔서 읽는다.

---

## 0. 이 재구성이 존재하는 이유

### 0.1 제품 정의

Lattice AI 는 **내 컴퓨터 안에서만 도는 개인 기억 시스템**이다.
관리자 콘솔이 아니고, 팀용 SaaS 대시보드가 아니고, 개발 도구가 아니다.

화면만 보고 이것을 알 수 있어야 한다. 지금은 알 수 없다.

### 0.2 지금 무엇이 잘못됐는지 (측정값)

`output/release/v10.6.3/screenshots/` 를 실제로 열어서 확인한 것:

| 증상 | 근거 |
| --- | --- |
| 설정 화면이 3열 카드 격자 = 관리자 콘솔 | `08-system.png`, `frontend/src/pages/System.tsx:418` `xl:grid-cols-3` |
| 자료 화면이 2열 카드 격자 = 대시보드 | `06-capture.png`, `frontend/src/pages/Capture.tsx:97` `capture-secondary` |
| 대화 홈이 뷰포트 920px 중 약 620px 에서 끝나고 300px 가 죽은 공간 | `04-brain-chat-home.png` |
| 대화 홈 전체가 카드 하나 안에 들어가 있어 위젯처럼 보임 | `BrainConversation.tsx:165` `.brain-chat-home-card` |
| 페이지·기능 컴포넌트에 `grid-cols` 계열 유틸이 **47회** | `grep -rn "grid-cols\|xl:col-span\|md:grid-cols" frontend/src/pages/*.tsx frontend/src/features/**/*.tsx \| wc -l` → `47` |

47개의 다단 격자가 이 제품이 대시보드처럼 보이는 단일 최대 원인이다.
**격자를 줄이는 것이 이번 작업의 물리적 핵심이다.**

### 0.3 지난 릴리스의 실패 (반복 금지)

담당자가 `frontend/src/pages/` 만 뒤지고 `frontend/src/components/onboarding/` 의
화면 4개(로그인·추천·설치·분석)를 손도 대지 않은 채 "전면 재구성 완료"라고 보고했다.
픽셀 대조에서 그 4개가 이전 버전과 완전히 동일했다.

**온보딩 4개 화면은 `frontend/src/pages/` 안에 없다.** 아래 §1 인벤토리를 읽어라.

---

## 1. 화면 인벤토리 (전부 직접 열어 확인)

### 1.1 릴리스 캡처 12개 화면 → 실제 구현 파일

캡처 스크립트: `scripts/capture_release_evidence.mjs` (219줄, 직접 읽음)
캡처 뷰포트: **1440 × 920**, `fullPage: true`, 언어 `ko`, 모드 `basic`

| # | 캡처 파일 | 도달 경로 | **실제 구현 파일 (검증됨)** | 캡처가 기다리는 셀렉터 |
| --- | --- | --- | --- | --- |
| 01 | `01-login.png` | 온보딩 step=`login` | `frontend/src/components/onboarding/LoginScreen.tsx` (187줄) | 텍스트 `이 Brain의 주인을 정합니다.` |
| 02 | `02-recommended-models.png` | 온보딩 step=`recommend` | `frontend/src/components/onboarding/RecommendationScreen.tsx` (274줄) | 텍스트 `추천대로 시작하세요.` |
| 03 | `03-install-load-progress.png` | 온보딩 step=`install` | `frontend/src/components/onboarding/InstallScreen.tsx` (264줄) + `DownloadConsentPanel.tsx` (36줄) | 버튼 `준비하고 시작하기` 클릭 후 180ms |
| 04 | `04-brain-chat-home.png` | `#/brain` (기본) | `frontend/src/features/brain/BrainHome.tsx:143` → `BrainConversation.tsx` (789줄) 빈 상태 경로 328–563 + `BrainHomeHero.tsx` + `BrainComposer.tsx` + `IngestionPanels.tsx` + `BrainQuickControls.tsx` | `main[aria-label='Lattice Brain']` |
| 05 | `05-memory-graph.png` | `#/knowledge-graph` | `frontend/src/pages/Brain.tsx` (648줄) `view==="graph"` → `DigitalBrainExplorer` (Brain.tsx:213) + `frontend/src/pages/brain/CytoscapeGraph.tsx:177` | `[data-testid='brain-cytoscape']` |
| 06 | `06-capture.png` | `#/capture` | `frontend/src/pages/Capture.tsx` (669줄) | `h1.page-title` = `어떤 자료를 기억할까요?` (Capture.tsx:60) |
| 07 | `07-model-library.png` | `#/models` | `frontend/src/pages/Library.tsx` (759줄) | `h1.page-title` = `Lattice가 사용할 AI를 선택하세요.` (Library.tsx:81) |
| 08 | `08-system.png` | `#/settings` | `frontend/src/pages/System.tsx` (759줄) `SettingsPanel` 366–542 | `h1.page-title` = `Lattice를 내 방식에 맞게 설정하세요.` (System.tsx:70) |
| 09 | `09-automation-runs.png` | `#/runs` | `frontend/src/pages/Act.tsx` (650줄) `RunsListPanel` 197–256 + `frontend/src/features/act/InstalledAutomations.tsx` (163줄) | `h1.page-title` + 텍스트 `내 승인 기다리는 중` |
| 10 | `10-admin-console.png` | `#/admin/users` | `frontend/src/features/admin/AdminConsole.tsx` (337줄) — **`App.tsx:60` 이 `/admin` 접두사를 가로채 별도 전체화면으로 렌더한다. System.tsx 의 admin 탭이 아니다.** | 없음 (250ms 대기) |
| 11 | `11-knowledge-journey.png` | `#/pipeline` | `frontend/src/pages/Capture.tsx` `PipelinePanel` 610–644, `<ol className="capture-journey">` at **Capture.tsx:616** | `role=list` name `자료가 기억이 되는 3단계` 를 품은 `.capture-secondary-column > *` **요소 스코프 캡처** |
| 12 | `12-review-center.png` | `#/review` | `frontend/src/features/review/ReviewInbox.tsx` (183줄) + `ReviewCard.tsx` (288줄) + `ProposalConflictNote.tsx` (70줄) | 텍스트 `/Review Center\|리뷰 센터\|검토함/` |

### 1.2 캡처에 안 잡히지만 존재하는 화면 (전부 실존 확인)

| 화면 | 파일 | 비고 |
| --- | --- | --- |
| 언어 선택 + Brain 깨우기 | `frontend/src/components/ProductFlow.tsx:124` `WakeBrainScreen` + `onboarding/LanguageChooser.tsx` | 캡처 01 직전 화면 |
| 환경 분석 | `frontend/src/components/onboarding/AnalysisScreen.tsx` (135줄) | `ProductFlowScreens.tsx:1` 로 export 되어 있으나 ProductFlow 는 현재 렌더하지 않음. **삭제 금지, export 유지.** |
| 앱 셸 (상단바·모바일 하단바·더보기 팝오버) | `frontend/src/App.tsx:128` `BrainShell` | 12개 화면 중 04–12 전부에 나옴 |
| 하이브리드 검색 | `Brain.tsx` `view==="knowledge"` → `HybridSearch` | `#/hybrid-search` |
| 통합 기억 패널 | `Brain.tsx` `view==="memory"` → `UnifiedMemoryPanel` | `#/memory` |
| 명령 팔레트 (Cmd+K) | `frontend/src/features/command/CommandPalette.tsx` (300줄) + `CommandPaletteHost.tsx` | `App.tsx:59` 에서 상시 마운트 |
| 오늘의 브리핑 | `frontend/src/features/command/DailyBriefingPanel.tsx` (165줄) | |
| 대기 중 제안 | `frontend/src/features/command/PendingProposalsPanel.tsx` (160줄) | |
| 네트워크 경계 | `frontend/src/components/NetworkBoundaryPanel.tsx` (376줄) | System `network` 탭 |
| 권한 모드 다이얼 | `frontend/src/components/PermissionModePanel.tsx` (186줄) | |
| 작업공간 전환 | `frontend/src/components/WorkspaceProfileSwitcher.tsx` (176줄) | 더보기 팝오버 안 |
| 코어 중단 배너 | `frontend/src/components/CoreServiceUnavailableBanner.tsx` (65줄) | 전역 |
| 파일 미리보기 모달 | `frontend/src/features/brain/FilePreviewModal.tsx` (163줄) | |
| 에이전트 승인 카드 / 단계 타임라인 | `features/brain/AgentApprovalCard.tsx`, `AgentStepTimeline.tsx` | 대화 중 |
| 기억 정원 / 기억 링 / 깊이 출현 | `features/brain/KnowledgeGarden.tsx`, `MemoryRings.tsx`, `DepthEmergence.tsx` | 대화 홈 선반 안 |
| 폴더 기억 건강도 | `frontend/src/features/capture/FolderMemoryHealth.tsx` (146줄) | Capture 우측 열 |

---

## 2. 기능 인벤토리 — **삭제 금지 목록**

사용자 지시: 기능을 없애지 말 것. 재배치·재구성·이름변경은 허용.
**도달 불가능해지는 기능이 하나라도 있으면 이 작업은 실패다.**

### 2.1 라우트 테이블 4개 (`frontend/src/routes.ts`, 118줄) — 항목 삭제 절대 금지

지휘자가 이 4개 테이블을 기계적으로 대조한다. `frontend/src/routes.test.ts` (203줄) 가 이미 이걸 지킨다.

**(1) `productShellRoutes` — 15–25줄, 6개.** `routes.test.ts:124` 이 길이 6을 강제한다.
`brain`(대화) · `capture`(자료) · `memory`(기억) · `library`(AI 모델) · `act`(작업) · `system`(설정)

**(2) `directProductRoutes` — 27–34줄, 6개.**
`brain` `capture` `knowledge-graph` `models` `settings` `review`

**(3) `compatibilityRouteAliases` — 36–70줄, 33개.**
`home` `onboarding` `hybrid-search` `memory` `ask` `chat` `files` `pipeline` `capture-browser`
`my-computer` `agents` `runs` `review-center` `workflows` `planning` `hooks` `tools` `skills`
`mcp` `marketplace` `account` `workspace-admin` `snapshots` `activity` `network` `settings`
`system-admin` `admin/users` `admin/permissions` `admin/audit` `admin/security`
`admin/policies` `admin/private-vpc`

**(4) `commandRoutes` — 80–90줄, 7개.**
`page-brain` `page-capture` `page-memory` `page-library` `page-act` `page-review` `page-system`

> 라벨 변경은 가능하다(`labelKey` 가 가리키는 문구를 바꾸는 것). **키·경로·항목 수를 바꾸면 실패.**

### 2.2 탭 인벤토리 — 전부 도달 가능해야 함

| 페이지 | 탭 id (실제 구현 확인) | 파일:줄 |
| --- | --- | --- |
| Act | `runs` `agents` `workflows` `hooks` `tools` + runs 하위탭 `runs`/`review` | `Act.tsx:44-48`, 하위탭 `Act.tsx:26-29` |
| Library | `models` `skills` `mcp` `marketplace` | `Library.tsx:58-61` |
| System | `account` `workspaces` `snapshots` `activity` `settings` `network` `admin` | `System.tsx:35-41` |
| Brain | `knowledge` `memory` + 뷰 `graph` | `Brain.tsx:38-39`, `Brain.tsx:35` |
| Capture | 방법 `files` `local` `browser` + 상시 `PipelinePanel` | `Capture.tsx:37-39`, `Capture.tsx:98` |

**basic 모드 숨김은 삭제가 아니다.** 현재 basic 에서 숨는 것:
- System: `activity` `network` `admin` (`System.tsx:51-53`)
- Act: `hooks` `tools` (`Act.tsx:89-92`)
- 대화 홈: 준비도 %, 모델 pill, 컨텍스트 품질 사유, 근거 지표, 수집 타임라인, 모델 연속성, Brain 개요 (`BrainConversation.tsx:184,189,228,530,534-551,575,578-586,607-624`)

이 게이트들은 **유지**한다. 새로 뭔가를 basic 에서 숨기려면, advanced 에서 반드시 보여야 한다.

### 2.3 사용자가 지금 할 수 있는 일 (동작 단위)

없애면 안 되는 동작. 위치는 옮겨도 된다.

**온보딩** — 언어 선택 / Brain 깨우기 / 기존 Brain 사용 / 이름·이메일·비밀번호로 등록·로그인 /
환경 스캔 재시도 / 추천 모델 3종 중 선택 / 모델 없이 건너뛰기 / 뒤로 / 다운로드 동의 확인 /
설치 시작 / 나중에 하기 / 설치 후 진입

**대화** — 질문 보내기 / 스트리밍 중지 / 재생성 / 새 대화 / 지난 대화 이어가기·삭제 /
이미지 첨부 / 문서 첨부 / **전역 드래그 앤 드롭 5개까지** (`BrainHome.tsx:102-109`) /
스타터 프롬프트 3개 / 제안 질문 3개 / 파일·폴더·노트·웹 수집 / 권한 모드 다이얼 /
근거 보기 / 상세 요청 / 실행 승인·거부 / 할 일 만들기 / Brain 그림 눌러 기억 지도 열기 /
깊이별 이동(`openDepth`, `BrainHome.tsx:91-95`)

**기억** — 그래프 탐색 / 그래프 검색 / 이웃 노드 / 하이브리드 검색 / 통합 기억 패널 /
임베딩 신선도 알림 / 오래된 임베더 알림 / 연결 지도 열기(`Brain.tsx:99`)

**자료** — 파일 올리기 / 폴더 연결 / 웹페이지 저장 / 파이프라인 3단계 확인 /
최근 수집 확인 / 폴더 기억 건강도 / 추출 품질 노트

**작업** — 승인 대기 승인·거부 / 설치된 자동화 dry-run → live 2단계 실행 /
에이전트 실행 중지 / 워크플로 중지·재개(승인/거부) / 목표 적기 / 레시피 /
보호 장치(훅) / 도구 권한 / 검토함 필터(상태·출처) / 승인·보류·미루기·해제·지금 실행 /
제안 충돌 재적용

**AI 모델** — 활성 모델 확인 / 원클릭 전환(`data-testid="library-switch-${id}"`, `Library.tsx:202`) /
스킬 / 연결(MCP) / 마켓

**설정** — 계정·비밀번호 / 작업공간 / 스냅샷 생성·비교·내보내기·복원 / 활동 기록 /
기기 페어링·푸시 / 환경설정 / 관리자

**전역** — Cmd+K 팔레트 / 언어 전환 / 테마 전환 / 작업공간 전환 / 관리자 게이트 /
건너뛰기 링크(`App.tsx:237`) / VS Code 동기화 상태(advanced 전용)

---

## 3. 새 정보 구조

### 3.1 하나의 원칙

> **한 화면 = 한 가지 일.**
> 그 일이 화면에서 가장 큰 것이어야 하고, 나머지는 전부 그것보다 조용해야 한다.

이걸 물리적으로 강제하는 규칙 3개:

**R1 — 격자 금지.** 페이지 최상위 레이아웃에 `grid-cols-2/3` 를 쓰지 않는다.
세로 흐름이 기본이다. 격자는 "같은 종류의 항목이 4개 이상 있을 때"만 허용한다.
지금의 47개를 **20개 이하**로 줄인다.

**R2 — 읽는 너비.** 글이 있는 블록은 최대 `--lt-measure-text` (68ch ≈ 720px).
페이지 전체는 최대 `--lt-measure-page` (1120px). 1440 프레임에서 양옆 여백이 남는 게 정상이다.
그래프(05)만 예외로 전면 출혈(full-bleed).

**R3 — 카드는 결정에만.** 사용자가 눌러야 할 것을 담은 것만 카드(테두리+그림자)로 올린다.
정보만 있는 블록은 카드가 아니라 제목 + 목록이다.
지금은 전부 카드라서 무엇이 중요한지 알 수 없다.

### 3.2 화면별 1순위 / 2순위 / 접어둘 것

| # | 화면 | **1순위 (화면에서 제일 큰 것)** | 2순위 | 접거나 아래로 |
| --- | --- | --- | --- | --- |
| 01 | 로그인 | 이름·이메일·비밀번호 3필드 + 시작 버튼 | "이 컴퓨터 안에서만" 약속 한 줄 | 3가지 약속 상세 |
| 02 | 추천 모델 | **모델 하나**와 그 시작 버튼 | 환경 확인 한 줄 | 대안 2개(접기), 건너뛰기 |
| 03 | 설치 진행 | 진행 상태 한 덩어리 (Brain + 진행바 + 현재 단계 문장) | 남은 시간 | 예상 시간표, 다운로드 동의, 안내문 |
| 04 | 대화 홈 | **입력창** | Brain + 인사 한 줄, 스타터 3개 | 수집 도크, 지난 대화·통찰 선반 |
| 05 | 기억 지도 | **그래프 캔버스 (전면 출혈)** | 떠 있는 검색 | 노드 상세(사이드), 하위뷰 전환 |
| 06 | 자료 | **드롭 존 하나** | 방법 3개 전환 | 3단계 여정, 최근 수집, 폴더 건강도 |
| 07 | AI 모델 | **지금 이 Brain의 목소리** (활성 모델) | 바꿀 수 있는 모델 목록(행) | 스킬·연결·마켓 |
| 08 | 설정 | **한 줄 = 한 설정** 세로 목록 | 구역 제목 | 위험한 것(초기화 등) 맨 아래 |
| 09 | 자동화 실행 | **내 승인 기다리는 것** | 최근에 한 일 (하나의 시간순 목록) | 설치된 자동화 |
| 10 | 관리자 | **지금 문제 있나 한 문장** + 돌아가기 | 접히는 구역 6개 | 원시 로그 |
| 11 | 3단계 여정 | **3단계가 가로로 흐르는 리본** | 단계별 현재 개수 | — |
| 12 | 검토 센터 | **결정 하나 = 한 행** | 남은 개수 한 줄 | 필터, 상세 |

### 3.3 셸(공통 프레임) 재구성

`frontend/src/App.tsx:128` `BrainShell`

- 상단바 좌측 브랜드 옆에 **상시 로컬 배지** 추가: `이 컴퓨터 안에서만` (i18n 신규 키).
  12개 화면 중 9개에 나오므로 "이게 무엇을 하는 물건인지" 를 가장 싸게 전달하는 자리다.
  `aria-hidden` 금지 — 실제 텍스트로 읽혀야 한다.
- 1차 내비 `대화 / 자료 / 기억` 은 **유지**. 2차 `작업 / AI 모델 / 설정` 도 유지.
  단 2차 링크는 지금보다 확실히 더 조용하게(아이콘 + 작은 글자, 활성만 강조).
- 모바일 하단바·더보기 팝오버·건너뛰기 링크·포커스 트랩(`App.tsx:158-214`)은 **동작 그대로 유지**.

---

## 4. pts_gemini 작업 지시 (프론트엔드 전담)

### 4.0 소유 범위

- **소유:** `frontend/src/**` 전체
- **소유 안 함:** `tests/visual/mock_server.cjs` (grok 소유), `latticeai/**`, `lattice_brain/**`
- 새 API 가 필요하면 §5 의 grok 목록에 이미 들어있다. 목록에 없는 걸 부르지 마라 — 404 가 캡처에 찍힌다.

### 4.1 절대 깨면 안 되는 계약 (깨면 릴리스 캡처가 실패한다)

`scripts/capture_release_evidence.mjs` 가 의존하는 것들. 바꾸려면 **같은 커밋에서 스크립트도 고쳐라.**
스크립트를 고치든 안 고치든, 최종적으로 12개 png 가 전부 생성되어야 한다.

| 계약 | 현재 위치 | 캡처 줄 |
| --- | --- | --- |
| 버튼 `Brain 지금 깨우기` | `flow.wake.primary` | 84 |
| 텍스트 `이 Brain의 주인을 정합니다.` | `flow.login.title` | 85 |
| placeholder `나`/`You`, `you@local`, `로컬 Brain 비밀번호` | LoginScreen 3필드 | 88–90 |
| 버튼 `내 Brain 시작하기` | `flow.login.submit` | 91 |
| 텍스트 `추천대로 시작하세요.` | `flow.recommend.body` | 92 |
| 버튼 `추천으로 바로 시작` | `flow.recommend.primary` | 95 |
| 텍스트 `모델을 준비하고 시작합니다.` / 버튼 `준비하고 시작하기` | InstallScreen | 96–97 |
| `main[aria-label='Lattice Brain']` | `BrainHome.tsx:143` (`brain.aria.home`) | 100 |
| `[data-testid='brain-cytoscape']` | `CytoscapeGraph.tsx:177` | 104 |
| `h1.page-title` × 4 (Capture:60, Library:81, System:70, Act:85) | | 114,118,122,130 |
| 텍스트 `내 승인 기다리는 중` (`act.runStatus.awaiting_approval`) | `Act.tsx` RunList 배지 | 131 |
| `.capture-secondary-column` 이 `role=list name="자료가 기억이 되는 3단계"` 를 품은 카드의 **직계 부모** | `Capture.tsx:616` `<ol className="capture-journey" aria-label=...>` | 144–151 |
| 텍스트 `검토함` (`review.inbox.title`) | `ReviewInbox.tsx:111` | 154 |
| Review Center 에 원시 JSON 파싱 에러 문구가 없을 것 | | 155–158 |

### 4.2 스타일 시스템 함정 (이거 모르면 작업이 조용히 죽는다)

`frontend/src/styles/cssLayering.test.ts` 가 강제하는 것 (직접 읽음):

1. **프로젝트 CSS 는 전부 unlayered.** Tailwind 유틸은 `@layer utilities`.
   → **unlayered 규칙이 Tailwind 유틸을 무조건 이긴다.** 소스 순서·명시도 무관.
2. 테스트는 스타일시트에서 **프로젝트 소유 클래스 100개 이상**을 추출한 뒤,
   9개 컴포넌트 파일에서 **그 클래스에 레이아웃 유틸(`p-*` `m-*` `gap-*` `flex` `grid` `justify-*` …)이
   붙어 있으면 실패**시킨다.
3. 어느 시트에도 `@layer` 를 새로 넣으면 실패한다.

**따라서:**
- `.brain-*` `.ritual-*` `.capture-*` `.page-*` `.product-*` `.data-panel` `.library-*` `.admin-*` 에는
  Tailwind 레이아웃 유틸을 붙이지 마라. 레이아웃은 `frontend/src/styles/experience/*.css` 에 쓴다.
- 반대로 **클래스 없는 순수 `<div className="grid gap-4 ...">` 에는 유틸이 정상 동작한다.**
  지금 47개 격자가 바로 그 형태다 — 그래서 실제로 렌더링되고 있고, 그래서 대시보드로 보인다.
- 새 레이아웃 클래스를 만들 때는 **기존 시트가 안 쓰는 접두사**를 써라. 겹치면 조용히 진다.

시트별 소유 접두사 (직접 확인):
`shell.css`→`.brain-nav/.brain-topbar/.brain-more/.brain-skip` · `conversation.css`+`conversation-active.css`+`conversation-fixes.css`→`.brain-message/.brain-composer/.brain-home-insights` ·
`home-simple.css`→`.brain-home-station/.brain-hero/.brain-ingestion-dock/.brain-prompt-*/.brain-secondary-deck` ·
`graph-home.css`→`.brain-centered-home/.brain-home-control-deck/.brain-home-shelves/.brain-flow-*` ·
`graph.css`→`.brain-flow-node/.brain-live-source-panel/.brain-automation-*` ·
`capture.css`→`.data-panel/.capture-*/.work-goal-card/.library-active-*` ·
`affordance.css`→`.product-page/.product-tabs/.page-hero/.capture-journey-step/.brain-prompt-pill` ·
`responsive.css`→ 위 전부의 브레이크포인트

### 4.3 화면별 지시

각 항목은 **무엇이 1순위인지 다시 정하고, 요소를 실제로 옮기고 묶고 덜어내는 것**이다.
문구만 바꾸면 실패로 간주한다.

---

#### [01] 로그인 — `frontend/src/components/onboarding/LoginScreen.tsx`

현재 (직접 읽음): 세로 1단 좁은 칼럼. 거대한 Brain 그림(`ProductFlow.tsx:84` `.ritual-brain`) →
제목 → 폼 카드 → 약속 바 3개(`ProductPromise`, 164–186).
1440 프레임에서 720px 폭 칼럼 하나만 쓰고 나머지가 비어 있다.

**바꿀 것:**
1. **좌우 2단으로 재구성.** 좌: Brain 그림(현재보다 작게) + `이 컴퓨터 안에서만 사는 기억을 만듭니다`
   한 문장 + 약속 3개를 세로로. 우: 폼 카드. 900px 미만에서 세로로 접힌다.
   → 이것만으로 01 은 이전 버전과 명백히 달라진다.
2. `ProductPromise` (164–186) 를 **폼 아래에서 좌측 열로 이동**. 삭제 금지 — 3개 사실 전부 유지.
3. 폼 카드는 이 화면의 유일한 raised surface 유지 (`.ritual-login-card`, 95줄). 이건 잘 돼 있다.
4. 각주 2줄(152–155)은 유지하되 크기를 더 낮춰 버튼과 경쟁하지 않게.
5. `ProductFlow.tsx:82` 의 `<main className="ritual-container">` 랜드마크 1개 유지 —
   step 별로 main 을 새로 만들지 마라(주석 78–81 이 이유를 설명한다).

**건드릴 파일:** `LoginScreen.tsx` · `ProductFlow.tsx` (좌우 골격) ·
`frontend/src/styles.css` 의 `.ritual-*` (레이아웃) · `frontend/src/i18n/onboarding.ts` (신규 문구)

---

#### [02] 추천 모델 — `frontend/src/components/onboarding/RecommendationScreen.tsx`

현재: 제목 → 환경 배너 블록(`renderEnvironmentCheck`, 89–125) → 히어로 카드(144–180) →
대안 그리드 2개(182–220) → 푸터(222–228). 선택지가 3개 동시에 보여서 "추천"이 추천으로 안 읽힌다.

**바꿀 것:**
1. **기본 상태에서 모델을 하나만 보여준다.** 대안 2개는 `<details>`(`다른 선택 보기`)로 접는다.
   접힌 상태가 기본. **삭제가 아니라 접기다** — 열면 그대로 선택 가능해야 한다.
2. 환경 배너(89–125)를 블록에서 **제목 밑 한 줄**로 강등. 성공/경고 색 점은 유지.
3. 히어로 카드를 화면 폭의 주인공으로. 버튼(169)이 화면에서 가장 강한 요소가 되어야 한다.
4. `loading` (34–49) / `unavailable` (51–85) 분기도 **같은 골격**으로 다시 짜라.
   지금 셋이 서로 다른 레이아웃이라 상태가 바뀌면 화면이 튄다.
5. 푸터(222–228)의 `뒤로` / `건너뛰기`는 유지.

---

#### [03] 설치 진행 — `frontend/src/components/onboarding/InstallScreen.tsx`

현재: **세로로 쌓인 블록이 9개다** — 제목·부제(78–83) / 예상 카드+타임라인(84–96) /
Brain(98–104) / 다운로드 동의(106) / 진행(108–121) / 상태문(123) / 상태카드(124–126) /
에러(128–133) / 버튼(135–149) / 로컬 노트(151–153).

**바꿀 것:**
1. **3구역으로 압축.**
   - (가) 무엇을 준비 중인가 — 모델 이름 한 줄
   - (나) **진행 덩어리** — Brain + 진행바 + 4단계 + 현재 단계 문장을 **하나의 시각 단위로 묶는다**.
     지금은 넷이 따로 떠 있다.
   - (다) 접힌 상세 — 예상 시간표(`ritual-timeline`), 다운로드 동의(`DownloadConsentPanel.tsx`),
     안내문 2개. **전부 유지, `<details>` 안으로.**
2. 버튼 행(135–149)은 맨 아래 고정. `뒤로`/`나중에`/`시작` 3개 유지.
3. 에러 카드(128–133)는 접힌 상세 밖에 — 나오면 반드시 보여야 한다.
4. `cleanConsumerText` (256–264) 의 기술용어 세탁은 **절대 건드리지 마라.**

---

#### [04] 대화 홈 — `frontend/src/features/brain/BrainConversation.tsx` (빈 상태 328–563)

이 제품의 정문이다. 가장 중요한 화면.

현재 (직접 읽음 + 스크린샷 확인): `.brain-centered-home` → `.brain-home-station`
(Hero → Composer → toolbar(수집도크+빠른제어)) → `.brain-secondary-deck`(제안) →
`footer.brain-home-quiet`(지난 대화·통찰 선반).
**전체가 `.brain-chat-home-card`(165줄) 카드 하나 안에 있고, 920px 뷰포트 중 620px 에서 끝난다.**

**바꿀 것:**
1. **빈 홈에서 바깥 카드(`.brain-chat-home-card.is-empty-home`)의 카드 외형을 벗긴다.**
   대화 중(`has-messages`)일 때의 외형은 유지해도 된다. 앱이 위젯이 아니라 방처럼 보여야 한다.
2. **세로 리듬을 뷰포트에 채운다.** `.brain-centered-home` 이 남는 높이를 차지하도록.
   Brain+인사는 광학적 상단 1/3, **입력창이 화면의 무게중심**, 스타터 칩은 입력창 바로 밑,
   선반은 바닥. 300px 죽은 공간을 없앤다.
3. **입력창을 지금보다 크게, 넓게.** 이 화면에서 1순위다. `.brain-composer` 를 키워라.
4. `.brain-secondary-deck` 의 카드 외형을 벗기고 스타터 3개를 **칩 줄**로 입력창에 붙인다.
   제안 질문(3개, `suggestedQuestions.slice(0,3)`) 분기(387–413)와
   스타터 폴백(414–432) **둘 다 유지**.
5. 수집 도크(`BrainIngestionDock` inline, `IngestionPanels.tsx:170`)는 입력창 아래 조용한 한 줄로.
   파일·폴더·노트·웹 4개 액션과 팝오버 전부 유지.
6. 선반 2개(`brain-history-shelf` 446, `brain-insights-shelf` 484)는 `<details>` 유지.
   basic 게이트(530, 534–551)도 그대로.
7. `BrainHomeHero.tsx` — 인사 + 기억 요약 한 줄 구조는 좋다. 크기·간격만 새 리듬에 맞춰라.
   Brain 그림 클릭 → 기억 지도(`onExploreBrain`) 유지.
8. `brain-drop-overlay`(`BrainHome.tsx:145`) 전역 드롭 오버레이 유지.

**건드릴 파일:** `BrainConversation.tsx` · `BrainHomeHero.tsx` · `BrainComposer.tsx` ·
`IngestionPanels.tsx` · `styles/experience/home-simple.css` · `graph-home.css` ·
`conversation.css` · `responsive.css`

---

#### [05] 기억 지도 — `frontend/src/pages/Brain.tsx` (`view==="graph"`, 89–94) + `pages/brain/CytoscapeGraph.tsx`

현재: `header.brain-layer-header` → 하위뷰 내비 → **`DataPanel` 로 감싼** `DigitalBrainExplorer`(213) →
그 안에 검색 Input(261) + `grid xl:grid-cols-[1fr_220px_180px_170px]`(258) +
`grid lg:grid-cols-[1fr_18rem]`(274). 그래프가 패널 안의 패널 안에 있다.

**바꿀 것:**
1. **그래프를 전면 출혈로.** `DataPanel` 껍데기(89–94)를 그래프 뷰에서 제거하고
   캔버스가 콘텐츠 영역 전체를 차지하게. 이 화면만 R2(읽는 너비) 예외다.
2. **검색을 캔버스 위 떠 있는 알약으로.** `Brain.tsx:261` Input 을 격자 칸에서 꺼낸다.
   → 캡처 스크립트(105–110)는 `input[aria-label='Search knowledge graph']` 또는
   placeholder 에 `Search`/`검색` 이 있는 첫 input 을 찾는다. **`검색` 이 placeholder 에 남아야 한다.**
   현재 `graph.search.basic` = `생각, 파일, 사람, 노트 검색…` — 조건 만족. 문구 바꿀 때 `검색` 유지.
   이참에 `aria-label` 도 붙여라(지금은 placeholder 만 있다).
3. 258줄 4열 격자 → 통계는 캔버스 하단 조용한 한 줄로.
4. 274줄 2열은 **노드 선택 시에만 열리는 사이드 패널**로. 평소엔 캔버스가 100%.
5. `data-testid="open-connections-map"`(`Brain.tsx:99`), `brain-cytoscape`(`CytoscapeGraph.tsx:177`) 유지.
6. `StaleEmbedderNotice` / `VectorFreshnessNotice`(84–85) 유지 — 캔버스 위 배너로.

---

#### [06] 자료 — `frontend/src/pages/Capture.tsx`

현재: `header.page-hero`(58–62) → `section.capture-station`(65–92, 방법 3개 + 인테이크) →
`div.capture-secondary`(97–100) = **2열 격자**(PipelinePanel | RecentCapturePanel).

**바꿀 것:**
1. **드롭 존을 화면의 주인공으로.** 방법 전환 3개(`capture-method-${id}`, 79줄)는 드롭 존 위의
   조용한 탭 줄로 강등. 3개 다 유지(`files`/`local`/`browser`).
2. **`capture-secondary` 2열 격자를 해체.** 3단계 여정 + 최근 수집 + 폴더 건강도를
   드롭 존 아래 **세로 흐름**으로. 드롭 존과 시각적으로 경쟁하면 안 된다.
3. ⚠️ **`.capture-secondary-column` 클래스 이름과 그 직계 자식 관계는 유지하라.**
   캡처 11이 `.capture-secondary-column > *` 로 카드를 잡는다(스크립트 148줄).
   구조를 바꾸려면 `scripts/capture_release_evidence.mjs` 143–151 을 같이 고쳐라.
4. `capture.title` = `어떤 자료를 기억할까요?` 는 `h1.page-title`(60줄)로 유지.

---

#### [07] AI 모델 — `frontend/src/pages/Library.tsx`

현재: `page-hero` → `ActiveModelCard`(108–217, 상시) → `Tabs` 4개 → 패널.
`ModelsPanel`(219–545) 안에 `grid xl:grid-cols-[1.2fr_0.8fr]`(304),
`md:grid-cols-[1fr_auto]`(426) 등.

**바꿀 것:**
1. **활성 모델을 "지금 이 Brain의 목소리" 진술문으로.** 카드가 아니라 화면 상단의 문장 +
   그 아래 한 줄 근거. 지금은 다른 카드들과 같은 무게라 무엇이 현재인지 안 보인다.
2. **모델 목록을 카드 격자에서 행 목록으로.** 한 행 = 이름 / 한 줄 설명 / 크기 / 전환 버튼.
   `data-testid="library-switch-${id}"`(202) 유지. 원클릭 전환은 이 화면의 핵심 동작이다.
3. 304·426·663·705·732줄 격자를 **최대 1개**만 남긴다.
4. 탭 4개(`models`/`skills`/`mcp`/`marketplace`) 전부 유지, 단 `models` 가 기본이고
   나머지 3개는 시각적으로 더 조용하게.

---

#### [08] 설정 — `frontend/src/pages/System.tsx`

**이번 릴리스에서 가장 크게 달라져야 하는 화면.** `08-system.png` 는 현재 완전한 관리자 콘솔이다.

현재: `page-hero`(68–72) → 탭 그룹 3개(73–92, `data-testid="system-tab-groups"`) →
패널. `SettingsPanel`(366–542)이 `grid xl:grid-cols-3`(418) + 내부 `sm:grid-cols-2`(476, 510).

**바꿀 것:**
1. **3열 카드 격자를 완전히 없애고 단일 열 설정 목록으로.**
   한 행 = `이름 / 한 줄 설명 / 오른쪽 끝 컨트롤`. 구역 제목으로 묶는다.
   설정 항목을 하나도 빼지 마라 — 배치만 바꾼다.
2. 위험한 동작(초기화·삭제류)은 목록 맨 아래 별도 구역으로 분리.
3. 탭 7개(`account` `workspaces` `snapshots` `activity` `settings` `network` `admin`) 전부 유지.
   basic 가시성 규칙(51–53) 유지. 탭 그룹 3개(identity/data/system) 구조 유지 —
   `data-testid="system-tab-groups"` 유지.
4. 125·137·196·253·280·300·324·418·476·510·640·699 의 격자를 **3개 이하**로.
5. `system.title` 을 `h1.page-title`(70)로 유지.

---

#### [09] 자동화 실행 — `frontend/src/pages/Act.tsx` (`RunsListPanel` 197–256) + `features/act/InstalledAutomations.tsx`

현재: 승인 DataPanel(`is-attention`) → `InstalledAutomations`(`grid lg:grid-cols-2` 76줄) →
`grid xl:grid-cols-2`(247) 로 에이전트 실행 / 워크플로 실행 **좌우 분리**.

**바꿀 것:**
1. **승인 대기를 화면 최상단 전폭 행으로.** 승인/거부 버튼이 이 화면의 유일한 강한 요소.
   `act.approval.request` basic 문구(215줄 부근) 유지.
2. **에이전트 실행 / 워크플로 실행 2열을 하나의 시간순 목록으로 합친다.**
   행마다 출처 라벨(에이전트/워크플로)을 단다. 두 목록의 항목이 하나도 빠지면 안 된다.
   `ActionButton` 들(중지 / 승인재개 / 거부재개) 전부 유지.
   → grok 이 `GET /api/activity/runs` 를 만든다(§5.1). 그걸 쓰되, 실패하면 기존 두 쿼리로 폴백하라.
3. ⚠️ `내 승인 기다리는 중` 배지가 반드시 렌더되어야 한다(캡처 131줄).
   `runStatusLabel`(263–267)과 `awaiting_approval` 분기를 건드리지 마라.
4. `InstalledAutomations` 는 단일 열로. dry-run → live 2단계(127–151)는 그대로.
   `data-testid`: `installed-automations`(65), `installed-automation-card`(87),
   `automation-last-execution`(103) 유지.
5. `humanRunTitle`(285–297) — 실행 이름을 사람 말로 보여주는 로직. 건드리지 마라.
6. Act 탭 5개 + runs 하위탭 2개 전부 유지.

---

#### [10] 관리자 콘솔 — `frontend/src/features/admin/AdminConsole.tsx`

현재: 헤더(36–47) → **지표 타일 4개**(`admin-metrics` 49–69) → **`admin-grid` 패널 6개**(71–149).

**바꿀 것:**
1. **지표 타일 4개를 한 문장으로 대체.** `문제 없음` 또는 `확인할 것 N개`.
   4개 숫자의 원본은 각 구역 안에 남긴다(삭제 금지).
   → grok 이 `GET /admin/health-summary` 를 만든다(§5.1).
2. **`admin-grid` 6패널 격자를 단일 열 접힘(`<details>`) 목록으로.**
   6개 패널(사용자/역할/감사로그/보안이벤트/Brain운영/런타임신뢰) **전부 유지**.
   기본은 전부 접힘, 문제 있는 구역만 펼침.
3. 돌아가기 버튼(37–40)을 더 크고 분명하게. 여기는 평소 화면이 아니라는 신호.
4. 로그 필터(213–252) 전부 유지. 인덱스 재구축 버튼(127–130) 유지.

---

#### [11] 3단계 여정 — `frontend/src/pages/Capture.tsx` `PipelinePanel` 610–644

요소 스코프 캡처라서 **카드 내부 레이아웃을 바꾸면 즉시 이미지가 달라진다.**

**바꿀 것:**
1. **세로 `<ol>` 을 가로 3단 리본으로.** 단계 사이에 진행 방향이 보여야 한다.
2. 단계마다 **현재 개수**를 붙인다 (`받음 12 · 뽑아냄 12 · 이어붙임 9`).
   → grok 이 `GET /knowledge-graph/pipeline/status` 를 만든다(§5.2). 없으면 개수 자리를 비우고
   이유를 한 줄로 적어라. `—` 만 찍는 것은 금지(v9.9.7 규칙).
3. ⚠️ `<ol className="capture-journey" aria-label="자료가 기억이 되는 3단계">`(616) 의
   **role=list + aria-label 은 그대로 유지.** 캡처 144줄이 이걸로 카드를 찾는다.
4. `Card`/`CardHeader`/`CardContent` 구조(610–615)는 유지해도 되지만 내부는 반드시 바뀌어야 한다.

---

#### [12] 검토 센터 — `frontend/src/features/review/ReviewInbox.tsx` + `ReviewCard.tsx`

현재: `<Card>` 하나가 전부를 감싸고(107) → `CardHeader`(108–149) 안에
제목·설명·배지 2개 + **필터 Tabs 2줄**(상태 5개 / 출처 N개) → `CardContent`(150–180) 에
`grid gap-3` 리뷰 카드들.
필터 UI 가 실제 결정거리보다 크다.

**바꿀 것:**
1. **바깥 `<Card>` 를 벗긴다.** 제목 `검토함` + `결정 기다리는 N건` 을 페이지 수준 진술로.
2. **필터 2줄을 한 줄로.** 상태·출처를 같은 줄에 조용히. `ariaLabelledBy` 연결(134, 146) 유지.
   필터 항목은 하나도 빼지 마라(`reviewStatusFilters`, `reviewSourceFilters`).
3. **각 리뷰를 전폭 행으로.** 왼쪽 = 무엇을 결정하는지, 오른쪽 = 승인/보류/미루기/지금실행 버튼.
   `ReviewCard.tsx` 의 동작 5종 + `ProposalConflictNote` 409 재적용 흐름 전부 유지.
4. **빈 상태를 제대로 만든다.** `review.inbox.empty`(165) 가 지금은 작은 EmptyState 다.
   `지금은 결정할 것이 없어요` 를 화면의 주인공으로. 이게 정상 상태다.
5. ⚠️ `review.inbox.title`(`검토함`) 문자열 유지(캡처 154줄).
   ⚠️ 에러 문구에 원시 JSON 파싱 에러가 새면 캡처가 **throw** 한다(스크립트 155–158).
   `reviews.data?.error` 를 그대로 출력하는 158줄을 손볼 때 조심하라.

### 4.4 i18n 규칙

- 새 문구는 반드시 `ko` **와** `en` 둘 다 넣는다. `scripts/check_i18n_namespace_coverage.mjs` 가 막는다.
- 네임스페이스: `shell.ts`(셸) / `onboarding.ts`(01–03) / `brain.ts`(04–05) / `workspace.ts`(06–12).
  모듈이 쓰는 키의 네임스페이스를 그 청크가 import 하지 않으면 빌드가 깨진다
  (`i18n/registry.ts:18-20` 주석 참조).
- JSX 안에 한국어 리터럴을 직접 쓰지 마라. `scripts/check_i18n_literals.mjs` 가 막는다.

### 4.5 접근성 (감점 항목)

- 페이지당 `<main>` 하나. `App.tsx:399` `Content` 가 `contentOwnsMain` 으로 이미 조정한다 — 깨지 마라.
- `<details>` 로 접는 것은 접근성 OK. 접힌 안의 것도 키보드로 도달 가능해야 한다.
- 건너뛰기 링크(`App.tsx:237`) · 포커스 트랩(`App.tsx:158-214`) · `focusablesIn`(122–126)
  — 이 3개는 이미 정교하다. 건드리지 마라.
- 같은 이름의 landmark 두 개 금지 (`App.tsx:357-364` 주석이 이미 겪은 사고).
- 아이콘만 있는 버튼에는 `aria-label`.

### 4.6 검증 (제출 전 직접 돌려라)

```
npm run build:assets      # static/app 재생성. 이거 안 하면 캡처가 옛 UI를 찍는다.
npm run test:frontend
npm run lint
LTCAI_RELEASE_EVIDENCE_DIR=/tmp/lattice-preview npm run release:evidence
```

`static/app/` 은 vite 출력 디렉터리다. 손으로 파일을 두면 빌드가 지운다.

---

## 5. pts_grok 작업 지시 (백엔드·DB 전담)

### 5.0 소유 범위

- **소유:** `latticeai/**`, `lattice_brain/**`, `tests/visual/mock_server.cjs`
- **소유 안 함:** `frontend/src/**` (gemini 소유)
- `tests/visual/mock_server.cjs` 를 gemini 와 동시에 편집하지 마라. 이 파일은 grok 단독 소유다.

### 5.1 새 UI 가 요구하는 것 — 신규 API 3개

**(A) 통합 실행 목록 — 화면 09**
```
GET /api/activity/runs?limit=20
→ { runs: [ { id, source: "agent"|"workflow", title, status,
              started_at, finished_at, can_stop, can_resume } ] }
```
왜: 지금 `Act.tsx:247` 이 에이전트 실행과 워크플로 실행을 좌우 2열로 나눠 보여준다.
사용자에게 그 구분은 의미가 없다. 시간순 한 목록으로 합치려면 두 소스를 합쳐 정렬한
응답이 필요하다.
- 기존 `/agents/api/runtime/status` 의 `runs` 와 `/workflows/api/runs` 의 `runs` 를 합쳐 정렬.
- `title` 은 `Act.tsx:285-297` `humanRunTitle` 과 같은 우선순위로:
  `workflow_name → name → goal → title → query → input(문자열) → input.goal/...`
- ⚠️ `status` 값 `awaiting_approval` 인 항목이 **최소 하나** 응답에 있어야 한다.
  릴리스 캡처가 `내 승인 기다리는 중` 배지를 기다린다.
- 기존 두 엔드포인트는 **삭제하지 마라.** 프론트가 폴백으로 쓴다.

**(B) 관리자 한 줄 요약 — 화면 10**
```
GET /admin/health-summary
→ { status: "ok"|"attention", issue_count: N,
    issues: [ { area, severity, message } ] }
```
왜: 지금 관리자 콘솔이 지표 타일 4개로 시작한다. "지금 문제 있나"를 한 문장으로 답해야
관리자 화면이 평소 화면이 아니라는 게 드러난다.
- `area` 는 `AdminConsole.tsx:71-149` 의 6패널과 같은 축: `users` `roles` `audit`
  `security` `brain_ops` `runtime_trust`
- 기존 `/admin/summary` `/admin/stats` `/admin/security/overview` 는 유지.

**(C) 파이프라인 단계별 개수 — 화면 06/11**
```
GET /knowledge-graph/pipeline/status
→ { received: N, extracted: N, connected: N, updated_at: ISO }
```
왜: 3단계 여정 카드(`Capture.tsx:610-644`)가 지금 "3단계가 있다"는 설명만 한다.
각 단계에 지금 몇 개가 있는지 보여야 살아있는 화면이 된다.
- 값을 못 구하면 그 키를 **생략**하라. `0` 으로 위장하지 마라 — 프론트가 이유를 표시한다.

### 5.2 필요 없는 것 (명시적으로 "없다"고 씀)

- **화면 01·02·03 (온보딩)** — 백엔드 변경 **필요 없음**. 기존 `/login` `/register`
  `/setup/scan` `/models/recommendations` `/models` `/local/sysinfo`
  `/engines/prepare-model/stream` 으로 충분하다.
- **화면 04 (대화 홈)** — 필요 없음. `/api/memory/manager` `/api/graph` `/models`
  `/api/memory/brain-brief` 로 충분. 빈 상태는 이미 0 을 반환한다.
- **화면 05 (기억 지도)** — 필요 없음. `/api/graph` `/knowledge-graph/neighbors/{id}` 로 충분.
- **화면 07 (AI 모델)** — 필요 없음. `/models` 가 `current`/`loaded`/`engines` 를 이미 준다.
- **화면 08 (설정)** — 필요 없음. 순수 레이아웃 작업이다.
- **화면 12 (검토 센터)** — 필터별 개수 엔드포인트를 **만들지 마라.**
  `/automation/reviews` 응답의 `items` 길이로 충분하고, `/api/proposals/counts` 가 이미 있다.
  엔드포인트를 늘리는 것보다 있는 걸 쓰는 게 낫다.
- **스키마 변경** — **없다.** 위 3개는 전부 기존 데이터의 조회·집계다.
  마이그레이션을 만들지 마라.

### 5.3 `tests/visual/mock_server.cjs` — 반드시 할 일

이 파일이 릴리스 캡처의 유일한 데이터 소스다 (`capture_release_evidence.mjs:29`).
**여기에 라우트가 없으면 화면이 "사용 불가"로 찍힌다.** 과거에 실제로 겪은 사고다
(파일 414–417줄 주석: `/models/load` 누락으로 모델 화면 1순위 동작이 모든 캡처에서 404).

1. §5.1 의 신규 3개 라우트를 **전부 목킹**하라. 응답 모양은 실제 구현과 동일해야 한다.
2. `/api/activity/runs` 목 데이터에 `status: "awaiting_approval"` 항목을 **반드시** 넣어라.
   없으면 캡처 09 가 타임아웃으로 실패한다.
3. `/knowledge-graph/pipeline/status` 는 세 값이 서로 다른 숫자여야 한다
   (예: 12 / 12 / 9). 셋이 같으면 단계가 흐른다는 게 안 보인다.
4. `/admin/health-summary` 는 `status: "attention"`, `issue_count: 1` 로 목킹하라.
   `ok` 로만 목킹하면 그 상태의 레이아웃이 캡처에 한 번도 안 나온다.
5. 기존 라우트를 **하나도 지우지 마라.** 화면이 조용히 빈 상태로 찍힌다.
6. `node --check tests/visual/mock_server.cjs` 가 `npm run lint` 에 포함되어 있다.

### 5.4 검증

```
node --check tests/visual/mock_server.cjs
npm run lint:python
.venv/bin/python -m pytest tests/ -q      # 파이썬은 .venv 필요
npm run frontend:openapi:check            # OpenAPI drift 게이트
```

신규 엔드포인트를 추가했으면 OpenAPI 를 재생성해야 CI drift 게이트를 통과한다.

---

## 6. 디자인 토큰 · 스타일 규약

### 6.1 재사용할 것 (`frontend/src/styles/tokens.css`, 108줄 — 직접 확인)

색 토큰은 이미 잘 정의돼 있다. **새 색을 만들지 마라. 하드코딩 hex 금지.**

- 표면: `--background` `--foreground` `--card` `--card-foreground` `--muted` `--muted-foreground`
- 액션: `--primary` `--primary-foreground` `--secondary` `--border` `--ring` `--input`
- 제품 의미색: `--brain-core` `--brain-halo` `--memory` `--knowledge` `--connection`
  `--map` `--ask` `--act` `--library`
- 상태: `--success` `--warning` `--danger` `--destructive`
- 테마 전환: `[data-theme="light"]` (기본 light, `appStore.ts:25`)

### 6.2 새로 만들 것 — 레이아웃 토큰

지금 토큰에 **간격·너비·타이포 스케일이 없다.** 그래서 화면마다 여백이 제각각이고
"읽는 너비"를 강제할 방법이 없다. `tokens.css` 에 추가한다:

```
/* 읽는 너비 — R2 를 물리적으로 강제한다 */
--lt-measure-text:  68ch;    /* 글 블록 최대 */
--lt-measure-form:  30rem;   /* 폼 칼럼 */
--lt-measure-page:  70rem;   /* 페이지 콘텐츠 최대 (1120px) */
--lt-measure-full:  none;    /* 05 그래프 전용 예외 */

/* 세로 리듬 — 4pt 기반, STYLE_SYSTEM.md §3 과 정합 */
--lt-space-1: 4px;  --lt-space-2: 8px;  --lt-space-3: 12px;
--lt-space-4: 16px; --lt-space-5: 24px; --lt-space-6: 32px;
--lt-space-7: 48px; --lt-space-8: 64px;

/* 구역 사이 간격 — 이 3개만 쓴다. 임의 값 금지 */
--lt-gap-tight:   var(--lt-space-3);   /* 한 덩어리 안 */
--lt-gap-block:   var(--lt-space-5);   /* 블록 사이 */
--lt-gap-section: var(--lt-space-7);   /* 구역 사이 */

/* 타이포 스케일 */
--lt-type-hero: 1.75rem;  --lt-type-title: 1.25rem;
--lt-type-body: 0.9375rem; --lt-type-small: 0.8125rem;

/* 반경 */
--lt-radius-sm: 6px; --lt-radius-md: 8px; --lt-radius-lg: 12px;
```

**세 간격 토큰만 쓴다는 규칙이 이 재구성의 리듬을 만든다.**
지금은 `gap-2` `gap-3` `gap-4` `gap-5` `gap-6` 이 뒤섞여 있어 위계가 안 읽힌다.

### 6.3 절대 규칙

1. `@layer` 를 어느 시트에도 새로 넣지 마라 — `cssLayering.test.ts:110-116` 이 막는다.
2. 프로젝트 소유 클래스(`.brain-*` `.ritual-*` `.capture-*` `.page-*` `.product-*`
   `.data-panel` `.library-*` `.admin-*`)에 Tailwind **레이아웃 유틸**을 붙이지 마라 —
   `cssLayering.test.ts:119-135` 가 막는다. 레이아웃은 unlayered 시트에.
3. 테마 표면에 hex 하드코딩 금지 (`STYLE_SYSTEM.md` §2 "The one rule").
4. `!important` 금지. 컴포넌트별 다크모드 오버라이드 금지.
5. 새 레이아웃 클래스는 기존 시트가 안 쓰는 접두사로.
6. 애니메이션 주의: 릴리스 캡처가 프레임을 찍는다. `animate-pulse` 같은 것을 캡처 대상
   영역에 넣으면 매번 다른 이미지가 나와 "변경 없는 diff" 가 생긴다
   (`Act.tsx:210-213` 주석이 이미 겪은 사고).

---

## 7. 채점 기준 (100점 만점 · **95점 미만 재작업**)

채점은 내가 한다. 각 항목의 확인 방법을 미리 못박아 둔다.

### 7.1 릴리스 캡처 12개 화면 전부 변경 — **24점** (화면당 2점)

**확인 명령:** (기준선 경로는 문서 상단 안내대로 최신 릴리스 디렉터리로 바꿔 읽는다 —
`output/release/v10.6.3/` 는 리포에서 사라졌다.)
```
npm run build:assets
LTCAI_RELEASE_EVIDENCE_DIR=/tmp/lattice-after npm run release:evidence
for f in 01-login 02-recommended-models 03-install-load-progress 04-brain-chat-home \
         05-memory-graph 06-capture 07-model-library 08-system 09-automation-runs \
         10-admin-console 11-knowledge-journey 12-review-center; do
  a=$(shasum -a 256 output/release/v10.6.3/screenshots/$f.png | cut -d' ' -f1)
  b=$(shasum -a 256 /tmp/lattice-after/screenshots/$f.png | cut -d' ' -f1)
  [ "$a" = "$b" ] && echo "FAIL $f (동일)" || echo "ok $f"
done
```

- 해시가 같으면 그 화면 **0점**.
- 해시만 다르고 눈으로 봐서 배치가 그대로면 **0점** (문구·색만 바뀐 것). 12장 전부 육안 확인한다.
- ⚠️ 반대 함정: `LivingBrain` 이 있는 화면(01·03·04)은 무변경에도 해시가 달라진다.
  이 3개는 **해시가 아니라 육안 배치 변화**로만 채점한다.
- 캡처가 하나라도 **실패(타임아웃/throw)** 하면 이 항목 전체 0점.

### 7.2 기능 무손실 — **20점**

**확인:**
```
npm run test:frontend -- routes                       # routes.test.ts 통과
git diff main -- frontend/src/routes.ts               # 4개 테이블 항목 수 대조
```
- `productShellRoutes` 6개 / `directProductRoutes` 6개 / `compatibilityRouteAliases` 33개 /
  `commandRoutes` 7개 — **하나라도 줄면 이 항목 0점.** 확인:
  ```
  node -e "const s=require('fs').readFileSync('frontend/src/routes.ts','utf8');
  const seg=(a,b)=>s.slice(s.indexOf(a),s.indexOf(b));
  console.log((seg('compatibilityRouteAliases','export const primaryRoutes')
    .match(/^\s{2}\"?[a-z][a-z0-9\/-]*\"?:\s*\{/gmi)||[]).length)"
  ```
- §2.2 탭 인벤토리(Act 5+2, Library 4, System 7, Brain 2+1, Capture 3+1) 전부 도달 가능:
  각 `#/` 경로로 실제 진입해서 확인한다.
- §2.3 동작 목록에서 도달 불가가 된 것 1개당 **-4점.**

### 7.3 정보 위계 재설계 — **20점**

**확인:**
```
grep -rn "grid-cols\|xl:col-span\|md:grid-cols" frontend/src/pages/*.tsx frontend/src/features/**/*.tsx | wc -l
```
- 기준선 **47**. 20 이하 → 20점 / 21–30 → 12점 / 31–40 → 6점 / 41 이상 → **0점**.
- 추가로 화면별 §3.2 표의 1순위가 실제로 화면에서 가장 큰 요소인지 12장 전부 육안 확인.
  1순위가 안 잡히는 화면 1개당 **-2점.**
- `System.tsx` 의 `xl:grid-cols-3`(418) 가 남아 있으면 이 항목 **최대 10점**.

### 7.4 제품다움 — **12점**

12장을 보고 "이게 무엇을 하는 물건인지" 판단한다. 채점 관점:
- 개인 소유물처럼 보이는가, 팀 관리 도구처럼 보이는가 (**4점**)
- 셸의 로컬 배지(§3.3)가 실제로 렌더되는가 (**2점**)
- 죽은 공간 / 프레임 대비 콘텐츠 비율 — 특히 04 (**3점**)
- 첫 진입자가 다음 행동을 아는가 — 01·02·04·06 (**3점**)

### 7.5 접근성 · 시맨틱 — **8점**

- 페이지당 `<main>` 1개, 같은 이름 landmark 중복 없음 (**3점**)
- 접힌 `<details>` 안 요소가 키보드 도달 가능 (**2점**)
- 아이콘 전용 버튼에 `aria-label` (**2점**)
- 기존 `data-testid` 유지: `brain-cytoscape` `open-connections-map` `capture-method-*`
  `library-switch-*` `system-tab-groups` `installed-automations` `brain-home-station`
  `brain-ingestion-dock` `brain-quick-controls` `proposal-count-badge` (**1점**, 하나라도 없어지면 0)

### 7.6 게이트 통과 — **10점**

```
npm run lint            # 6점 — 실패 시 0
npm run test:frontend   # 4점 — 실패 시 0
```
`check_i18n_literals` / `check_i18n_namespace_coverage` / `check_bundle` / `node --check mock_server.cjs`
가 전부 `lint` 안에 있다. 부분 점수 없음.

### 7.7 토큰 · 스타일 규약 — **6점**

- `cssLayering.test.ts` 통과 (**3점**)
- §6.2 레이아웃 토큰이 `tokens.css` 에 실제로 추가되고 **사용**됨 (**2점**)
  — 정의만 하고 안 쓰면 0점.
- 새 hex 하드코딩 0개 (**1점**):
  `git diff main -- 'frontend/src/styles/**' | grep '^+' | grep -cE '#[0-9a-fA-F]{3,8}'`

### 7.8 즉시 재작업 조건 (점수와 무관)

- 릴리스 캡처 12장 중 하나라도 생성 실패
- `routes.ts` 4개 테이블에서 항목 삭제
- `frontend/src/components/onboarding/` 3개 화면(01·02·03) 중 하나라도 배치 무변경
  ← **지난 릴리스가 여기서 실패했다**
- `mock_server.cjs` 기존 라우트 삭제
- gemini 가 `mock_server.cjs` 를, grok 이 `frontend/src/` 를 편집

---

## 8. 두 담당자에게 보내는 마지막 한 줄

**pts_gemini:** 라벨을 바꾸지 말고 요소를 옮겨라. 47개 격자를 20개 아래로 내리는 것이
이 작업이 실제로 일어났다는 유일한 물리적 증거다. 그리고 `components/onboarding/` 을 열어라.

**pts_grok:** 엔드포인트 3개와 목 서버가 전부다. 스키마를 만들지 마라.
`mock_server.cjs` 에 라우트가 없으면 gemini 의 화면이 "사용 불가"로 찍힌다.
</content>
