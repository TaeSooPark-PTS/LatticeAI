[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../FIGMA_SPEC.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./FIGMA_SPEC.md)

# Lattice AI Figma 동급 제품 사양서 (v6.1.0)

현재 이 저장소에는 편집 가능한 Figma 컴포넌트 라이브러리가 포함되어 있지 않습니다. v6.1.0에서는 완성된 Figma 라이브러리를 대신하여 이 문서를 구현 수준의 제품 사양서로 사용합니다. 프로덕션 디자인의 단일 진실 공급원(source of truth)은 `static/css/tokens.css`와 `static/v3/css/*.css`에 토큰화된 `/app` 구현입니다.

## 제품 방향성

- 엔터프라이즈 관리자 대시보드보다는 Linear, Raycast, ChatGPT에 가까운 프리미엄 로컬 우선(local-first) AI 워크스페이스.
- 밀도 높고, 차분하며, 가독성이 뛰어나고 신뢰할 수 있는 UI.
- 가짜 준비 상태(fake readiness) 배제: 모든 런타임 표면은 활성(live), 사용 불가(unavailable), 대기 중(pending), 또는 명시적 비활성(disabled) 상태를 정직하게 표시합니다.
- 기본 모드(Basic mode): Home, Chat, Files, Search, Knowledge, Memory, Models, Settings.
- 고급 모드(Advanced mode): Agents, Workflows, Skills, Hooks, MCP.
- 관리자 모드(Admin mode): Users, Permissions, Audit Logs, Security, Policies, Private VPC.

## 셸 (Shell)

- 좌측 커맨드 레일: 데스크톱 292px, 접힌 태블릿 76px, 모바일 오프캔버스 드로어.
- 브랜드 블록: 컴팩트 마크, "Lattice AI", "Private runtime".
- 브랜드 바로 아래에 워크스페이스 선택기 배치.
- 컴팩트 행, 아이콘, 제목, 짧은 메타데이터로 구성된 그룹형 내비게이션.
- 푸터에는 검색 준비 상태(retrieval readiness), 계정, 테마 토글 표시.
- 상단 바(Topbar)는 노이즈를 최소화하여 브레드크럼, 커맨드 팔레트, 인덱스 칩, 모드 전환기만 배치.

## 컴포넌트

- 카드와 패널은 8px 이하의 테두리 반경(radius)을 사용합니다.
- 버튼은 컴팩트하고 아이콘 중심이며 토큰 색상을 사용합니다.
- 테이블은 고정 헤더(sticky header), 컴팩트 행, 토큰화된 호버 상태를 사용합니다.
- 입력창/셀렉트/텍스트 영역은 38px의 컨트롤 높이와 포커스 토큰 링을 공유합니다.
- 빈 상태(Empty state)는 아이콘, 제목, 정직한 사유, 그리고 가능한 경우 다음 동작 버튼을 포함합니다.
- 상태 필(Status pill)은 데이터를 꾸며내지 않고 항상 `ready`, `pending`, `idle`, `failed`, `live`, `unavailable`을 명확히 식별합니다.

## 주요 뷰 (Primary Views)

- Home: 백엔드, 모델, 검색, 메모리, 인덱스 소스, 빠른 작업, 최근 활동(가용 시)을 보여주는 로컬 준비 상태 대시보드.
- Chat: 대화 레일, 근거 기반 스레드, 컴포저, 검색 컨텍스트, 스트리밍 상태, 모델 미로드 오류 상태를 갖춘 네이티브 3단 채팅.
- Files: 수동 업로드는 항상 가능하며, 폴더 연결은 노출되지만 데스크톱 로컬 에이전트가 없을 때는 정직하게 비활성화됩니다.
- Search: 키워드, 벡터, 그래프 점수를 함께 보여주는 하이브리드 검색.
- Knowledge: SVG 그래프 탐색기와 엔티티 인스펙터.
- Memory: 회상(recall), 소스 티어, 검사, 압축(compact), 재구축(rebuild) 표면.
- Models: 활성 모델, 임베딩 제공자, 카탈로그, 로드/언로드 동작.
- Settings: 외관, 워크스페이스, 런타임 준비 상태, 임베딩, 통합 프로브, `/health` 기반 About 버전 정보.

## 반응형 요구사항

- 390px 모바일 화면에서 Home, Knowledge, Search, Settings, Admin 라우트에 가로 스크롤 넘침(horizontal overflow)이 없어야 합니다.
- 태블릿/모바일에서 채팅 대화 및 컨텍스트 창은 드로어(drawer)로 전환됩니다.
- 고정 포맷 컨트롤은 호버 및 로딩 상태에서도 일정한 크기를 유지합니다.

## 에셋 빌드 규칙

`static/css/tokens.css`, `static/v3/css/*.css`, 또는 `static/v3/js/**/*.js`를 변경한 후에는 반드시 `npm run build:assets`를 실행하십시오. 매니페스트 버전은 `package.json`과 일치해야 합니다.
