[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../STYLE_SYSTEM.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./STYLE_SYSTEM.md)

# Lattice AI — 스타일 시스템 (v3.3.1)

이 문서는 Lattice AI `/app` 디자인 시스템과 v3.3.1의 시각적 재구축 결정을 정리한 단일 기준 문서입니다.

> **핵심 결정 사항.** v3.3.1은 런타임 동작을 그대로 보존하면서 눈에 보이는 `/app` 제품 셸을 의도적으로 재구축했습니다. 변경은 토큰 우선(token-first) 방식으로 이루어졌습니다. 산발적인 스타일 오버라이드 대신 공유 v3 디자인 시스템 계층에서 색상 팔레트, 테두리 반경(radii), 밀도, 셸 계층 구조, 카드, 테이블, 폼, 빈 상태(empty state), 그리고 주요 Home/Files/Chat/Settings 준비 상태 표면을 일괄 개편했습니다. 레거시 정적 페이지 CSS(`static/workspace.css`, `platform.css`, `css/responsive.css`, `css/reference/*`)는 `/app` SPA 외부에 남아 있으며 제품의 시각적 소스로 사용되지 않았습니다.

---

## 1. 시각적 방향성 (Visual direction)

목표 품질 기준: **Linear · Raycast · ChatGPT · 현대적인 AI 워크스페이스** — 프리미엄 감각, 높은 집중도, 정보 밀도, 뚜렷한 위계, 우수한 여백을 지향하며 레거시 관리자 포털(Jira / ServiceNow) 스타일은 명시적으로 배제합니다.

아이덴티티 모티프: **프라이빗 검색 격자(private retrieval lattice)** — Knowledge Graph, Vector Index, Hybrid Search를 하나의 로컬 준비 상태 시스템으로 표현합니다. v3.3.1은 둥근 대시보드 카드 위주의 UI에서 벗어나 컴팩트 레일, 조용한 상단 바, 8px 카드 반경, 차분한 중립 표면, 명시적인 활성/사용 불가(`live`/`unavailable`) 소스 배지를 갖춘 밀도 높은 Linear/Raycast 스타일 워크스페이스로 전환합니다.

---

## 2. CSS 아키텍처 (토큰 우선, 계층형 구조)

다음 순서대로 로드됩니다(`scripts/build_v3_assets.mjs`가 생성하는 `static/v3/asset-manifest.json` → `entrypoints.styles` 참조):

| 계층 | 파일 | 역할 |
| --- | --- | --- |
| 색상 단일 진실 공급원 | `static/css/tokens.css` | 테마가 적용되는 모든 색상/표면/테두리/그림자/포커스 토큰; 라이트 + `[data-lt-theme="dark"]` + OS 테마 추종. |
| 구조적 토큰 | `static/v3/css/lattice.tokens.css` | 비색상 토큰: 타이포그래피 스케일, 간격(4pt), 테두리 반경, 고도(elevation), 모션, z-index, 레이아웃 레일; 색상 토큰 위의 시맨틱 필러/상태 별칭. |
| 베이스 / 리셋 | `static/v3/css/lattice.base.css` | 요소 리셋, 기본 타이포그래피, 포커스 링. |
| 컴포넌트 | `static/v3/css/lattice.components.css` | 버튼, 입력창, 필(pill), 카드, 테이블, 패널, 토스트, 미터, 배지. |
| 셸 / 레이아웃 | `static/v3/css/lattice.shell.css` | 앱 프레임: 내비게이션 레일, 상단 바, 커맨드 팔레트, 드로어, 콘텐츠 최대 너비. |
| 뷰 / 페이지 | `static/v3/css/lattice.views.css` | 뷰별 레이아웃: 채팅, 파일 드롭존, 파이프라인, 관리자, 필러 뷰. |

이 구조는 전통적인 `tokens / layout / components / pages / utilities` 분할과 직접 대응됩니다. `tokens.css` + `lattice.tokens.css`는 토큰, `lattice.shell.css`는 레이아웃, `lattice.components.css`는 컴포넌트, `lattice.views.css`는 페이지에 해당하며 유틸리티는 `lt3-*` 헬퍼로 인라인 정의됩니다.

**단 하나의 핵심 규칙:** 컴포넌트와 뷰 CSS는 오직 `var(--lt3-*)` 또는 상속된 `var(--*)` 시맨틱 색상 토큰만 사용합니다. 테마가 적용되는 표면에 16진수 색상값(hex)을 직접 하드코딩하지 마십시오. 이 규칙은 두 토큰 파일 상단에 명시되어 있으며, 토큰 하나만 수정해도 전체 UI 테마가 일관되게 바뀌도록 보장합니다. `!important` 남용이나 컴포넌트별 다크 모드 오버라이드를 두지 않습니다.

### 빌드 파이프라인

`scripts/build_v3_assets.mjs`는 모든 CSS/JS 에셋에 대해 콘텐츠 해시가 붙은 파일들을 생성하고, ES 모듈 임포트를 해시된 파일명으로 다시 작성하며, `asset-manifest.json`을 출력합니다. v3.3.0부터 매니페스트의 `version`은 하드코딩된 리터럴 대신 `package.json`에서 가져옵니다. `static/v3` 소스나 `static/css/tokens.css`를 편집한 후에는 반드시 `npm run build:assets`를 실행하십시오.

---

## 3. 주요 토큰 레퍼런스

**타이포그래피 스케일** (`lattice.tokens.css`): `--lt3-text-2xs 11px` → `--lt3-text-4xl 48px`, 본문 기본값은 `--lt3-text-md 15px`. 굵기(weight)는 400~800이며, 예측 가능하고 컴팩트한 UI를 위해 자간(letter-spacing) 토큰은 모두 `0`입니다.

**간격 (4pt 기준):** `--lt3-space-1 4px` … `--lt3-space-9 64px`.

**테두리 반경 (Radii):** `--lt3-radius-xs 4px`, `--lt3-radius-sm 6px`, `--lt3-radius-md/lg/xl 8px`, `--lt3-radius-pill`.

**고도 (Elevation):** `--lt3-elev-0..3`, 상속된 `--shadow*`를 통해 테마에 맞게 반응합니다.

**모션:** `--lt3-ease`, `--lt3-dur-1..4` (90~420ms).

**레이아웃 레일:** `--lt3-rail-w 292px` / 접힘 상태 `76px`, `--lt3-topbar-h 56px`, `--lt3-content-max 1360px`.

**검색 아이덴티티 (Retrieval identity):** `--lt3-pillar-graph`(블루 / `--accent`), `--lt3-pillar-vector`(틸 / `--accent-2`), `--lt3-pillar-hybrid`(마젠타 / `--accent-pink`)와 각각의 `*-soft` `color-mix` 변형, 배경 메시 토큰 `--lt3-mesh-*`.

**상태 (Status):** `--success/warning/danger`에서 파생된 `--lt3-ok/warn/err/info-soft`.

---

## 4. 정보 구조 (모드 게이팅)

단일 테이블인 `static/v3/js/core/routes.js`가 레일, 커맨드 팔레트, 라우터, 브레드크럼을 모두 구동합니다. 모드는 Basic < Advanced < Admin(`MODE_RANK`) 순으로 확장됩니다.

- **Basic:** Home, Chat, Files, Search, Knowledge, Memory, Models, Settings.
- **Advanced:** Agents, Workflows, Skills, Hooks, MCP 추가.
- **Admin:** Users, Permissions, Audit, Security, Policies, Private VPC 추가.

Pipeline, Planning, My Computer, Marketplace, Tools와 같은 레거시/실험적 페이지는 호환성을 위해 딥링크로 접근할 수 있지만 프로덕션 레일이나 커맨드 팔레트에는 전면 노출되지 않습니다.

---

## 5. 필수 MCP 점검 결과

### 5.1 Figma MCP 점검 결과

- **인증된 사용자:** `taesoo park` (rnlgnquvk@gmail.com), 플랜: starter, **시트 유형: View**(읽기 전용).
- **접근 가능한 디자인 파일:** **발견되지 않음.** 이 계정에서 Figma MCP는 파일 목록을 노출하지 않으며, 저장소 전체(`*.md`/`*.json` grep)에도 `figma.com` 참조가 없고 제공된 Figma 파일 키나 URL도 없습니다. 따라서 Lattice AI를 위한 **별도의 Figma 디자인 원본은 존재하지 않습니다.**
- **컴포넌트 / 변수 / 토큰 / 타이포그래피 / 레이아웃 관찰:** 확인할 파일이 없으며, 설령 파일이 생성되더라도 View 시트로는 라이브러리를 작성할 수 없습니다.
- **결론 ("사용 가능한 Figma가 없는 경우" 경로 적용):** 디자인 방향성은 **코드 내부**(이 문서와 토큰 파일들)에 정의됩니다. Lattice AI의 디자인 시스템은 이미 프로덕션 단일 진실 공급원인 CSS 토큰으로 살아 움직이고 있으므로, 읽기 전용 시트로 병렬 Figma 소스를 도입하면 정렬이 아니라 불일치(drift)만 초래합니다. v3.3.1의 모든 디자인 결정은 토큰 파일, 본 문서, 그리고 [`FIGMA_SPEC.md`](./FIGMA_SPEC.md)로 추적됩니다.

### 5.2 Vercel MCP 점검 결과

- **팀:** `taesoopark-pts' projects` (`team_uzCWFcxFe7Nf10vWZy91xqoQ`).
- **프로젝트:** `lattice-ai` (`prj_3rj8tG39eHurN6wR9NHFWQSE5fxD`), 프레임워크 **fastapi**, `nodeVersion 24.x`, **`live: false`**.
- **배포 이력:** `main@acffa89`("Validate v3.2 release claims")에서 생성된 단일 프로덕션 배포(`dpl_8op2rSTzPYEYrcmSa2EyVKFhii2u`). 빌드 상태: **READY**.
- **런타임 상태:** **동작 불가(broken).** 모든 요청이 **HTTP 500**을 반환하며 런타임 로그에 `could not import "server.py"` 및 서버리스 함수의 Python 임포트 오류가 기록됩니다(`GET /`, `/favicon.ico` → 500).
- **도메인:** `lattice-ai-sigma.vercel.app`, `lattice-ai-taesoopark-pts-projects.vercel.app`, `lattice-ai-git-main-…vercel.app`.
- **환경 / 설정:** 저장소에 `.vercel/project.json`이나 `vercel.json`이 없으며 GitHub 연동 자동 배포로 생성된 상태입니다.
- **준비 상태 결론:** Lattice AI는 **로컬 우선(local-first)** 플랫폼입니다. 로컬 MLX(Apple Silicon), 로컬 파일시스템, 로컬 SQLite 지식 그래프가 필수이므로 **Vercel 서버리스 환경에서는 작동할 수 없으며**, 실제 배포 상태도 이를 증명합니다(모든 라우트 500). **어떤 Vercel URL도 작동하는 제품으로 제시하거나 호스팅/프로덕션 준비 완료 상태로 주장하지 마십시오.** 정직한 입장은 다음과 같습니다. Lattice AI는 로컬(`npm start` / `LTCAI`)에서 실행되며, Vercel 프로젝트는 제품 표면이 아니라 비기능적 아티팩트(또는 제거 대상)로 취급해야 합니다.

### 5.3 프론트엔드 디자인 감사 결과

소스 코드를 근거로 위계 / 간격 / 타이포그래피 / 접근성 / 반응형 / 내비게이션 / 일관성을 감사했습니다.

- **위계 및 간격:** 우수함. 일관된 `viewHeader`(아이브로우 → 제목 → 부제), 4pt 스케일 기반의 `lt3-stack-*` 리듬, `panel`/`card` 고도 시스템을 갖추고 있으며 감사된 뷰에서 임의의 매직 넘버가 발견되지 않았습니다.
- **타이포그래피:** 단일 고정 타이포그래피 스케일(`Inter` 산세리프 + 모노스페이스 스택, 본문 15px, 고밀도 테이블 13px)을 사용하여 정보 밀도가 높은 워크스페이스에 적합합니다.
- **접근성 (양호하나 보완할 점 존재):** 입력창에 `aria-label`이 있고 토글에 `aria-pressed`가 적용되어 있으며 커맨드 팔레트에도 라벨이 지정되어 있습니다. **추적할 보완 항목:** 업로더/드래그오버에 시각적 포커스/호버 상태가 있으나 일부 아이콘 전용 버튼이 `title` 속성에만 의존합니다. 라이트 모드에서 `--surface-2` 위의 `--faint` 텍스트 명도 대비를 재점검할 필요가 있습니다(`npm test`에 자동화된 a11y 스위트가 없으므로 향후 Playwright 패스에 axe 추가를 고려할 수 있습니다).
- **반응형:** 좁은 화면에서 셸이 레일을 접고(`--lt3-rail-w-collapsed`) 드로어와 스크림(`lattice.shell.css`)을 사용하며, 통계 행(statrow)은 `auto-fit minmax`를 사용합니다. 태블릿/모바일 QA 노트는 §6을 참조하십시오.
- **일관성:** 가장 두드러진 강점입니다. 모든 곳에서 단일 컴포넌트 어휘(`lt3-*`), 단일 IA 테이블, 단일 소스/`unavailable` 배지 규칙을 공유합니다.

---

## 6. 반응형 및 시각적 QA 노트

v3.3.1 재구축은 로컬 `/app` 런타임에서 Playwright 시각적 점검과 소스 코드 리뷰를 통해 검증되었습니다.

- **데스크톱 / 노트북 (≥1024px):** 전체 커맨드 레일, 조용한 상단 바, 콘텐츠 최대 너비 1360px, Home 준비 상태 대시보드, 네이티브 Chat/Files/Settings 표면.
- **태블릿 (~768px):** 레일이 아이콘 형태로 접히며, Chat은 대화 및 컨텍스트 창에 드로어 동작을 사용합니다.
- **모바일 (<640px):** 레일이 스크림을 동반한 오프캔버스 드로어로 전환되고, 통계 행은 `auto-fit`으로 줄바꿈되며, Files 업로드는 전체 너비와 터치 친화적 레이아웃을 유지합니다.

---

## 7. v3.3.1 스타일 시스템 변경 요약

- 글로벌 셸 재구축: 밀도 높은 레일, 그룹화된 Basic/Advanced/Admin IA, 준비 상태 푸터, 더 차분해진 상단 바, 활성 모드별 커맨드 팔레트 필터링.
- v3.3.0 팔레트를 차분한 중립 로컬 워크스페이스 팔레트로 교체하고 라이트/다크 패리티 토큰 업데이트.
- 테두리 반경을 8px 카드/패널 시스템으로 조이고 구조적 토큰에서 음수 자간(negative letter-spacing) 제거.
- `lattice.components.css`에서 카드, 통계 타일, 테이블, 입력창, 세그먼트 컨트롤, 빈 상태, 로딩 표면 전면 개편.
- Home을 백엔드/모델/검색/메모리 실시간 상태와 최근 활동(가용 시)을 보여주는 준비 상태 대시보드로 재구축.
- Files 수동 업로드와 데스크톱 로컬 에이전트 폴더 연결 간의 차이 명확화.
- Settings에 백엔드, 데스크톱 로컬 에이전트, 모델 런타임, 호스트 텔레메트리, 임베딩 제공자 구성을 위한 런타임 준비 상태 표시 추가.
