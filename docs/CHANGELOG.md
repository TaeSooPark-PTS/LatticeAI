# Changelog

## [0.1.29] - 2026-05-25

### 관리자 UX 및 거버넌스 개선

- **관리자 대시보드 섹션 분리**
  - 대시보드, 사용자 관리, 권한 관리, SSO 관리, 보안 모니터링, 감사 로그가 각각 독립된 역할을 갖도록 정리
  - 사용자 관리는 활성/비활성 상태를, 권한 관리는 기본/고급/관리자 모드 권한을 명확히 표시
  - SSO 관리는 Okta / Microsoft Entra ID OIDC 설정 저장 및 테스트 플로우를 제공

- **보안 모니터링 / 감사 로그 내보내기**
  - 보안 모니터링 로그와 감사 로그를 각각 TXT, Excel(`.xls`), CSV로 추출 가능
  - 모든 내보내기 파일에 UTF-8 BOM을 포함해 한글이 깨지지 않도록 처리
  - 감사 로그의 사용자 사용량/위험도와 감사 이벤트, 보안 모니터링의 위험/준수 필드를 파일로 보존 가능

- **전역 UX 및 언어 전환 개선**
  - account/admin/chat/graph 화면의 언어 버튼 전환 시 주요 UX 텍스트가 한국어/영어로 함께 갱신되도록 개선
  - 홈/채팅 화면 구조를 분리해 채팅 전환 시 상태 충돌을 줄임
  - 채팅 빈 화면에서 Lattice AI의 역할과 기능을 더 명확히 안내

- **대시보드 시각 안정화**
  - 전체 사용자, 활성 메시지, 현재 모델, VPC 상태 카드의 줄바꿈/가독성 개선
  - 감사 로그의 Graph nodes / Edges 수치가 `[object Object]`로 표시되던 문제 수정
  - 분리된 정적 JS 파일(`static/scripts/*.js`)이 npm/PyPI 패키지에 포함되도록 배포 설정 보강

### Release
- 배포 버전을 `0.1.29`로 상향
- 대상 채널: `npm` · `PyPI` · `VS Code Marketplace` · `Open VSX`

---

## [0.1.28] - 2026-05-24

### 버그 수정: 추천 모델 ID 오류

- **`google/gemma-4-E4B` → `mlx-community/gemma-4-e4b-it-4bit` 수정**
  - 기존 ID는 HuggingFace의 BF16 풀프리시전 원본 모델 (~16GB) 로, MLX 포맷이 아니어서 `mlx_vlm.load()` 로 로드 불가능
  - 올바른 MLX 4-bit 양자화 버전(`mlx-community/gemma-4-e4b-it-4bit`, 5.2GB, 43K downloads)으로 교체
  - 크기 표시도 `"Next-Gen"` → `"5.2GB"` 로 실제 값으로 수정

### Release
- 배포 버전을 `0.1.28`로 상향
- 대상 채널: `npm` · `PyPI` · `VS Code Marketplace` · `Open VSX`

---

## [0.1.27] - 2026-05-24

### 로그인 페이지 UI 개선

**Language 버튼**
- 언어 표시 버튼 라벨을 `한국어 / English` 가변 텍스트에서 `Language` 고정 텍스트로 변경
- 버튼 위치를 화면 고정(fixed) → 로그인 카드 우측 상단(absolute) 으로 이동, 화면 크기 무관하게 카드 안에 항상 위치
- 버튼 크기 약 2/3 축소 (font 13px→11px, padding 6/14px→4/9px)
- footer 하단 언어 전환 버튼 제거 (도움말·개인정보처리방침 링크만 유지)

**로그인 카드 레이아웃**
- 카드 전체 크기 약 4/5 축소 — 너비 `min(720px)→min(460px)`, 폰트·버튼 높이·여백 비례 감소
- 타이틀 폰트 `38–54px → 28–40px`, 부제목 `24–34px → 17–24px`
- 카드 수직 위치: 타이틀바(58px)를 제외한 나머지 화면의 정중앙 배치 (`flex-direction: column` + `justify-content: center`, `padding-top: 58px`)
- 카드가 타이틀바와 겹치는 현상 구조적 수정 (기존 `align-items: center` 로 카드가 위로 올라가는 문제 해결)
- 로그인 카드와 개인정보처리방침 사이 여백 확보 (bottom padding 증가)

### Release
- 배포 버전을 `0.1.27`로 상향
- 대상 채널: `npm` · `PyPI` · `VS Code Marketplace` · `Open VSX`

---

## [0.1.26] - 2026-05-24

### MCP 관리 대폭 확장 — 3-탭 UI

**새 기능**

- **레지스트리 탭** — 기존 MCP 목록 (빌트인 + 원격 레지스트리)
  - 인기 MCP 20개 추가: `mcp-postgres`, `mcp-sqlite`, `mcp-brave-search`, `mcp-tavily`, `mcp-puppeteer`, `mcp-vercel`, `mcp-cloudflare`, `mcp-docker`, `mcp-stripe`, `mcp-supabase`, `mcp-hubspot`, `mcp-memory`, `mcp-sequential-thinking`, `mcp-discord`, `mcp-telegram`, `mcp-everything` 등
  - 각 항목에 `env_vars` 필드 (설치 시 필요한 환경변수 안내)

- **Claude Code 탭** — `~/.claude/settings.json` mcpServers 자동 동기화
  - Claude Code에서 설치한 MCP 목록을 Lattice AI UI에서 바로 확인
  - 이름·패키지·환경변수 정보 표시, "Claude Code" 소스 배지

- **직접 추가 탭** — 커스텀 MCP 폼
  - 이름·패키지·설명·환경변수·아이콘 직접 입력
  - 추가된 항목은 `~/.ltcai/custom_mcps.json`에 저장 (서버 재시작 후에도 유지)
  - 삭제 버튼 (어드민 전용)

**API 엔드포인트**
- `GET /mcp/claude-code-servers` — Claude Code settings.json mcpServers 반환
- `GET /mcp/custom` — 사용자 추가 커스텀 MCP 목록
- `POST /mcp/custom` — 커스텀 MCP 추가
- `DELETE /mcp/custom/{id}` — 커스텀 MCP 삭제 (어드민)

---

## [0.1.25] - 2026-05-24

### Knowledge Graph 전면 재설계 — 점=명사, 선=동사

**설계 원칙**
- **점(Node) = 명사** — 의미 있는 대상 (문서, 사람, 개념, 에러, 코드, 채팅 등)
- **선(Edge) = 동사** — 대상 간의 관계 (언급함, 포함함, 해결함, 의존함 등)
- 원본 데이터(PDF·PPT·채팅·코드 등)는 그대로 보관, AI가 핵심 개념을 추출해 점으로 만들고 관계를 선으로 연결

**노드 타입 (점 = 명사)**
- `Chat` — 대화 세션
- `Document` — 파일 (PDF·PPT·Word·Excel·이미지)
- `Concept` — 개념·아이디어·기술 용어
- `Person` — 사람 (사용자, 언급된 인물)
- `Error` — 오류·버그·예외
- `Code` — 코드·함수·클래스
- `Feature` — 소프트웨어 기능
- `Task` — 할 일·액션 아이템
- `Decision` — 결정 사항

**엣지 어휘 (선 = 동사형)**
`언급함` · `포함함` · `해결함` · `의존함` · `설명함` · `비교함` · `사용함` · `연결함` · `확장함` · `생성함` · `대체함` · `지원함` · `발생함` · `관련됨` · `작성함` · `업로드함`

**핵심 개선**
- `_extract_concepts()` — 고유명사·복합어·기술 용어 추출 (Lattice AI, Graph RAG, VS Code 등)
- `_classify_node_type()` — 개념별 노드 타입 자동 분류 (윈도우 컨텍스트 기반)
- `_infer_edge()` — 문장 내 동사·조사 패턴으로 엣지 레이블 자동 결정
- `_extract_triples()` — 문장 단위 개념 쌍 → (주어, 동사, 목적어) 트리플 추출
- `ingest_message()` 재설계 — 메시지 단위 → 대화 세션(Chat) 단위 노드
- `ingest_document()` 재설계 — Document 노드 + 동사형 엣지 (포함함, 업로드함)
- 중복 제거 — 하위 개념이 상위 복합어에 완전히 흡수될 때만 제거
- Message·AIResponse·Chunk 노드는 RAG 검색용으로만 저장, 그래프 비표시

### Release
- 배포 버전을 `0.1.25`로 상향
- 대상 채널: `npm` · `PyPI` · `VS Code Marketplace` · `Open VSX`

## [0.1.24] - 2026-05-24

### 안정화 및 UX 개선

- **로컬 파일 인증 강화** — `/local/list` · `/local/read` · `/local/write` · `/local/serve`에서 로그인 세션 필수화 (`_require_local_user` 헬퍼 도입)
- **`GET /local/list` 라우트 추가** — smoke-test 및 브라우저 직접 호출 호환
- **VS Code 배지 수정** — shields.io `visual-studio-marketplace` 폐기 → `vsmarketplacebadges.dev`로 전환
- **README 이미지 URL 안정화** — 로고·스크린샷을 `raw.githubusercontent.com` 절대 URL로 전환해 PyPI / npm / Marketplace 페이지에서도 표시
- **Quick Start 분리** — PyPI / npm / VS Code 사용자의 첫 설치 경로를 각각 명확히 안내
- **GitHub Actions Node 24** — CI 런타임을 Node 24로 업그레이드

### Release
- 배포 버전을 `0.1.24`로 상향
- 대상 채널: `npm` · `PyPI` · `VS Code Marketplace` · `Open VSX`

## [0.1.23] - 2026-05-24

### Discord 권한 알림 시스템

- **`GET /permissions/pending`** — 대기 중인 파일 접근 권한 요청 목록 (관리자)
- **`POST /permissions/approve/{token}`** — 권한 승인 (관리자 세션 또는 `LATTICEAI_PERMISSION_SECRET`)
- **`POST /permissions/deny/{token}`** — 권한 거부/취소
- **`GET /permissions/status/{token}`** — 승인 상태 폴링 (AI 에이전트용)
- 권한 토큰 기본값 `approved: False` — 명시적 승인 전까지 파일 접근 불가
- `~/.ltcai/permission_queue.json` — 서버가 기록, Claude Code Discord 플러그인이 읽어 알림 전송
- `LATTICEAI_PERMISSION_SECRET` 환경변수 — 모니터 스크립트가 세션 없이 approve/deny 호출 가능
- `perm_monitor.py` — 권한 목록 조회·승인·거부 CLI 도우미 (`list` / `approve TOKEN` / `deny TOKEN` / `discord-msg`)
- Discord에서 `승인 <토큰앞8자>` / `거부 <토큰앞8자>` 로 파일 접근 제어 가능

### 리포지터리 UX 개선

- **영어 README** 전면 재작성 — 한국어는 접을 수 있는 `<details>` 섹션으로 이동
- **SVG 로고** 추가 (`docs/images/logo.svg`)
- **경쟁 제품 비교표** — Lattice AI vs Open WebUI · Continue.dev · GitHub Copilot
- **Quick Start 분리** — PyPI / npm / VS Code 사용자의 첫 설치 경로를 각각 명확히 안내
- **비교표 기준 명시** — 공개 제품 동작 기준 시점을 README에 표기
- **패키지 페이지 이미지 안정화** — README 이미지 URL을 GitHub raw URL로 전환해 PyPI / npm / Marketplace에서도 표시되도록 개선
- **npm 패키지 정리** — 배포 tarball에서 테스트/캐시 파일 제외
- **실제 UI 스크린샷 3장** — Chat UI · Admin Dashboard · Data Graph (Playwright 2x 캡처)
- **VS Code 익스텐션 카테고리** `Other` → `AI, Machine Learning, Chat, Other`
- **VS Code 익스텐션 키워드** 8개 → 16개 (copilot, apple-silicon, groq, graph-rag 등)
- **VS Code 익스텐션 README** 전면 재작성 (기능표, 비교표, 모델 목록)
- 구버전 `.tgz` / `.vsix` 빌드 파일 삭제

### CI / 보안 안정화

- `/local/list` `GET` smoke-test 호환 라우트 추가
- `/local/list`, `/local/read`, `/local/write`, `/local/serve`는 로컬 개발 모드에서도 로그인 세션을 요구하도록 강화
- GitHub Actions integration smoke test 실패 원인 수정

### Release
- 배포 버전을 `0.1.23`으로 상향
- 대상 채널: `npm` · `PyPI` · `VS Code Marketplace` · `Open VSX`

## [0.1.22] - 2026-05-24

### 리포지터리 UX 개선 — 다운로드 유입 최적화

#### README 전면 재작성
- **영어 메인 문서** — 한국어는 접을 수 있는 `<details>` 섹션으로 이동 (국제 유입 대응)
- **SVG 로고 추가** (`docs/images/logo.svg`) — 인디고→시안 그라디언트 래티스 그리드 아이콘
- **경쟁 제품 비교표** — Lattice AI vs Open WebUI · Continue.dev · GitHub Copilot 10개 기준 비교
- **PyPI 월간 다운로드 수 배지** 추가 (신뢰도 지표)
- 기능 · 보안 · API · 트러블슈팅 섹션을 표(table) 형식으로 정리 (가독성 향상)

#### 실제 UI 스크린샷 자동 캡처
- `docs/images/screenshot-chat.png` — 웹 채팅 UI (사이드바, 모델/파이프라인/VPC 카드)
- `docs/images/screenshot-admin.png` — 어드민 대시보드 + Audit & Data Governance 섹션
- `docs/images/screenshot-graph.png` — Data Graph 시각화 (299 노드, 443 엣지)
- README 상단에 3단 그리드 스크린샷 테이블 추가
- `scripts/take_screenshots.js` — Playwright Chromium 헤드리스 캡처 스크립트 (2x 레티나)

#### VS Code 익스텐션 메타데이터 개선
- **카테고리** `Other` → `AI, Machine Learning, Chat, Other` (Marketplace 검색 노출 증가)
- **키워드** 8개 → 16개 추가 (`copilot`, `apple-silicon`, `groq`, `graph-rag` 등)
- **설명 문구** 구체화 — 핵심 차별점(MLX, MCP, Graph RAG, zero telemetry) 명시
- **익스텐션 README 전면 재작성** — 기능표 · 빠른 시작 · 단축키 · 지원 모델 · 설정 · 비교표 포함

#### 리포지터리 정리
- 루트 및 `vscode-extension/`의 구버전 `.tgz` / `.vsix` 빌드 파일 삭제

### Release preparation

- 배포 버전을 `0.1.22`로 상향
  - `package.json`
  - `pyproject.toml`
  - `vscode-extension/package.json`
- npm / PyPI / VS Code Marketplace / Open VSX 배포 전 빌드 산출물 생성

### Verification

- Python compile check 통과
- unit tests 통과
- root npm package 생성
- Python wheel / sdist 생성
- VS Code / Open VSX용 VSIX 생성

## [0.1.21] - 2026-05-24

### Setup Wizard — 자동 설치 · 연결 · 검증 · 복구

- **구성요소 자동 감지** — Homebrew, Python, Git, Node/npm, Ollama, LM Studio, Tesseract, MLX 계열 탐지
  - `COMMON_PATH_DIRS` 확장: `/opt/homebrew/bin`, `~/.local/bin`, `~/.latticeai/bin` 등 자동 포함
  - `PACKAGE_MODULES` 맵으로 pip 패키지 → import 이름 변환 (mlx-lm, mlx-vlm, openai-whisper 등)
- **공식 다운로드 연결** — 자동 설치 실패 시 OS별 공식 페이지(`OFFICIAL_DOWNLOADS`) 자동 오픈
- **설치 완료 자동 감지** — binary / Python 모듈 재탐색 폴링으로 설치 완료 감지
- **환경 변수 / PATH 자동 세팅** — PATH 누락 디렉토리를 `.env`의 `LATTICEAI_EXTRA_PATH`에 자동 저장
  - `_update_env_file()` 헬퍼로 `.env` 파일 안전 갱신 (중복 없이 key 업데이트)
- **동작 테스트** — binary는 `--version`, Python 패키지는 `import` smoke test
- **실패 시 자동 복구** — PATH 재보정, pip 재시도, brew 실패 시 공식 다운로드 fallback

### 보안 강화 — 로컬 파일 접근 승인 시스템

- **토큰 기반 로컬 파일 승인** — `_local_permission_response()` / `_require_local_approval()`
  - 5분(300초) TTL 만료 토큰으로 read / write / list 각 액션을 별도 승인
  - write 승인 시 `content_hash`(SHA-256)로 내용 위변조 방지
  - 만료 토큰 자동 정리(lazy GC)
  - Discord permission monitor 또는 웹 UI 승인 후에만 토큰 활성화
- **로컬 파일 미리보기 보호** — `/local/serve`, `/tools/read_document`, `/tools/pdf_pages`도 서버 발급 approval token 없이는 로컬 절대 경로를 열지 않도록 변경
- **workspace 정적 노출 제거** — `/agent-files` `StaticFiles` mount 제거, 인증이 있는 다운로드 라우트만 사용
- **세션 토큰 저장 강화** — 로그인 응답 body에서 bearer token 제거, 웹 UI는 HttpOnly cookie 기반 인증만 사용
  - `static/account.html`, `static/chat.html`, `static/admin.html`, `static/graph.html`의 `localStorage` 세션 토큰 의존 제거
- **loopback 감지** — `_host_is_loopback()` + `ipaddress` 표준 라이브러리로 네트워크 노출 여부 판단
  - `REQUIRE_AUTH` 기본값: 퍼블릭 모드 또는 네트워크 노출 시 `true` 자동 적용
  - `OPEN_REGISTRATION`: 네트워크 노출/퍼블릭 모드에서 기본 `false` (초대 코드 필요)
- **CORS 세밀 제어** — wildcard credential CORS 대신 `LATTICEAI_CORS_ALLOWED_ORIGINS` 환경변수로 허용 출처 추가 설정 가능
- **파일 자동 주입(opt-in)** — `LATTICEAI_AUTO_READ_CHAT_PATHS=true` 설정 시에만 채팅 메시지의 로컬 경로를 컨텍스트에 주입 (기본 OFF — 클라우드 모델 파일 누출 방지)

### 어드민 대시보드 — Audit & Data Governance

- **감사 로그 섹션** — 사용자별 AI 사용량, 업로드 문서 수, 민감정보 감지, clear/delete 이벤트, 최근 감사 이벤트 표시
- **데이터 보존 정책** — `/clear`, `/clear_all`, 대화 삭제는 화면 정리만 수행; Data Graph / RAG / 감사 로그는 보존
  - clear 동작을 `ClearEvent` 노드로 그래프에 기록 (언제 누가 clear 했는지 감사 추적)
- **민감정보 검사** — 문서 업로드 텍스트를 감사 로그에 기록

### Graph RAG / Data Graph

- **한국어 단어 검색 개선** — 2글자 키워드(`문서`, `모델` 등) RAG 검색 누락 문제 수정
- **`graph.html` 독립 페이지 유지** — 채팅 사이드바 `Data Graph` 버튼으로 연결, New Chat 버튼은 대화 검색 아래로 이동

### CLI / Node.js 래퍼

- `ltcai_cli.py` — `doctor` 명령어에 확장된 구성요소 탐지 통합
- `bin/ltcai.js` — Node.js 래퍼 PATH 보정 로직 개선

### 테스트

- `tests/unit/test_security.py` — loopback 감지, 로컬 파일 접근 approval token, write content hash 검증 추가
- `tests/unit/test_setup_wizard.py` — 자동 설정 구성요소 감지와 PATH 보정 검증 추가

### 환경변수 추가 (`.env.example`)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `LATTICEAI_AUTO_READ_CHAT_PATHS` | `false` | 채팅 메시지 내 로컬 경로 자동 주입 |
| `LATTICEAI_CORS_ALLOWED_ORIGINS` | `` | 추가 허용 CORS 출처 (콤마 구분) |
| `LATTICEAI_EXTRA_PATH` | `` | 추가 PATH 디렉토리 (Setup Wizard 자동 기록) |

## [0.1.20] - 2026-05-23

### Release
- 배포 버전을 `0.1.19`로 상향
- 대상 채널: `npm` · `PyPI` · `VS Code Marketplace` · `Open VSX`

## [0.1.18] - 2026-05-23

### MCP Registry 통합

- **`GET /mcp/tools` · `GET /mcp/installed`** — 기존 로컬 목록에 [registry.modelcontextprotocol.io](https://registry.modelcontextprotocol.io) 원격 목록을 실시간 병합
- **`POST /mcp/install`** — `npm` / `pypi` 설치 모드 추가 — 원격 레지스트리 MCP 서버를 클릭 한 번으로 설치 (`npm install -g` / `pip install`)
- **`POST /mcp/registry/refresh`** — 원격 레지스트리 캐시 강제 갱신
- `mcp_public_item` 응답에 `package` · `homepage` · `source` 필드 추가
- 원격 레지스트리는 1시간 TTL 인메모리 캐시, 서버 재시작 없이 최신 목록 유지
- `connector_info` 함수 인라인화 — `mcp_connector` 엔드포인트에서 combined registry 직접 조회

### Skills 마켓플레이스 (신규)

- **`GET /skills/marketplace`** — Apache-2.0 / MIT 검증 skills 목록 (Anthropic 18개 + 서드파티 59개 = 약 77개)
  - `?category=` · `?author=` 필터 파라미터 지원
  - 응답에 `authors` · `categories` 열거 포함
- **`POST /skills/install`** — `{ "plugin": "...", "skill": "..." }` 로 SKILL.md 런타임 fetch 후 로컬 `skills/` 에 저장
  - 파일 상단에 출처·라이선스 주석 자동 삽입 (`<!-- Source: ..., Apache-2.0 -->`)
  - `risk.json` 없으면 기본값 자동 생성
- **`GET /skills/list`** — 로컬 설치 skills 목록 (`source`: local / anthropic / third-party 구분)
- **`POST /skills/marketplace/refresh`** — 캐시 강제 갱신, author별 집계 반환
- 서드파티 소스 (모두 라이선스 검증 완료): Adobe (Apache-2.0) · Airtable (MIT) · Auth0 (Apache-2.0) · Expo (MIT) · Pydantic/Logfire (MIT)

### 플러그인 디렉터리 (신규)

- **`GET /plugins/directory`** — marketplace.json 기반 오픈소스 플러그인 149개 메타데이터 브라우저
  - `?q=` 전문 검색 · `?category=` · `?license=` 필터 지원
  - 응답에 `categories` · `licenses` 열거 포함
- **`POST /plugins/directory/refresh`** — 캐시 강제 갱신, license별 집계 반환
- `_KNOWN_REPO_LICENSES` 맵 — GitHub API 호출 없이 검증된 라이선스 즉시 조회
- 미확인 레포는 GitHub API fallback + 인메모리 per-repo 캐시
- Apache-2.0 / MIT / MIT-0 / CC-BY-4.0 플러그인만 노출, 라이선스 없는 34개 자동 제외

### Release
- 배포 버전을 `0.1.18`로 상향
- 대상 채널: `npm` · `PyPI` · `VS Code Marketplace` · `Open VSX`

## [0.1.17] - 2026-05-22

### Multi-LLM Pipeline

- **파이프라인 UI 카드** — ops 대시보드의 ACTIVE MODEL 카드와 PRIVATE VPC 카드 사이에 PIPELINE 카드 추가
  - 파이프라인 비활성 시: "멀티 LLM 파이프라인 / Plan → Execute → Review 모델 설정" 표시
  - 파이프라인 활성 시: "Pipeline ON / P:모델명 E:모델명 R:모델명" 으로 현재 설정 표시
- **멀티 LLM 에이전트 파이프라인** — Planning / Executing / Reviewing 3단계에 각각 다른 LLM 지정 가능
  - 모달에서 각 단계별 모델 선택 (로드된 로컬 모델 + 클라우드 프로바이더 자동 목록 구성)
  - 하나의 모델을 모든 단계에 사용해도 정상 동작
- **Human-in-the-loop** — 파이프라인 활성화 시 Planning 완료 후 사용자 승인을 기다렸다가 Execute 단계로 진행
  - 웹 UI: 플랜 승인 카드(`✅ 승인 / ❌ 취소`) 렌더링
  - Telegram 봇: 인라인 버튼으로 플랜 승인/취소
- **`/agent/resume` 엔드포인트** — `context_id`와 `approved` 필드로 대기 중인 에이전트 재개 또는 취소
- **`AgentRequest` 확장** — `planning_model`, `executing_model`, `reviewing_model`, `human_in_loop` 파라미터 추가
- **`LLMRouter.generate_as(model_id, ...)`** — 현재 모델을 임시 교체해 지정 모델로 생성 후 원복하는 헬퍼
- **Telegram 봇 인증 수정** — 서버 호출 시 `~/.ltcai/sessions.json`에서 어드민 세션 토큰을 읽어 쿠키로 전달
- **Telegram SSE 파싱** — `/chat` 스트리밍 응답(`text/event-stream`)을 올바르게 파싱하도록 수정
- **`_sessions_file()` 버그 수정** — 정의 이전에 전역 `DATA_DIR` 참조하던 문제 해결 (함수 내 경로 직접 계산)

### Release
- 배포 버전을 `0.1.17`로 상향
- 대상 채널: `npm`, `PyPI`, `VS Code Marketplace`, `Open VSX`

## [0.1.16] - 2026-05-22

### First-user admin bootstrap

- 서버를 처음 설치하고 가입하는 첫 번째 사용자가 자동으로 **admin** 권한 획득
- 이후 가입자는 기존과 동일하게 `user` 역할
- `/register` 응답에 `role` 필드 추가 — 클라이언트가 첫 가입 여부 확인 가능

### Release
- 배포 버전을 `0.1.16`으로 상향

## [0.1.15] - 2026-05-22

### Security hardening

- `LTCAI --tunnel` 실행 시 `LATTICEAI_REQUIRE_AUTH=true` 자동 강제 — 터널로 공개된 서버에 로그인 없이 접근 불가
- `/register` IP당 시간당 5회 rate limit
- `/login` IP당 5분당 10회 rate limit (brute force 방지)
- Cloudflare 터널 통과 시 `CF-Connecting-IP` 헤더로 실제 클라이언트 IP 추출
- `LATTICEAI_OPEN_REGISTRATION=false` 설정 시 회원가입 완전 차단 (관리자 직접 추가만 허용)

### Release
- 배포 버전을 `0.1.15`로 상향

## [0.1.14] - 2026-05-22

### `--tunnel` flag — 누구나 자기 PC를 서버로

- `LTCAI --tunnel` 한 줄로 Cloudflare 무료 터널 자동 개설
- cloudflared 바이너리가 없으면 GitHub에서 자동 다운로드 (`~/.latticeai/bin/`)
- macOS arm64/amd64, Linux arm64/amd64, Windows amd64 지원
- 터널 URL을 배너에 출력 + `LATTICEAI_TELEGRAM_BOT_TOKEN` / `LATTICEAI_TELEGRAM_CHAT_ID` 설정 시 Telegram 자동 알림
- `--tunnel` 지정 시 host 자동으로 `0.0.0.0`, CORS 네트워크 허용으로 전환

### Release
- 배포 버전을 `0.1.14`로 상향
- 대상 채널: `npm`, `PyPI`, `VS Code Marketplace`, `Open VSX`

## [0.1.13] - 2026-05-22

### Code quality & efficiency

- `HF_MODELS_ROOT` / `hf_model_dir` 중복 정의 제거 — `llm_router.py` 단일 소스로 통합, `server.py`에서 import
- `_looks_like_hf_model_dir` 가중치 파일 체크를 `.safetensors` / `.bin`으로 일치 — `.gguf`를 MLX 경로에서 잘못 허용하던 버그 수정
- `vllm_executable()` `shutil.which` 이중 호출 → 변수 캐시
- `ensure_lmstudio_model()` `_find_lmstudio_model_key` 이중 호출 → `found_key` 변수로 캐시
- `engine_support_status` 3단계 중첩 조건 → `is_apple_silicon` 플래그로 평탄화
- `ensure_llamacpp_server` 동일 프로세스 이중 `terminate()` 블록 → 단일 블록 (vllm 패턴과 통일)
- `ensure_vllm_server` 37줄 중첩 삼항 커맨드 빌더 → `if/elif/else` + `_host_args` 공통화
- `except: pass` → `except Exception: pass` (KeyboardInterrupt 노출)
- `knowledge_graph.py` 엣지 순회 루프 두 번 (`degree_map` + `topic_metrics`) → 단일 루프로 병합

### Performance & correctness

- `get_lmstudio_models()` TTL 캐시(10초) 추가 — `/health`, `/engines`, `/models` 매 요청마다 LM Studio HTTP 프로브하던 문제 해결, 서버 미응답 시 마지막 캐시 반환
- `/health`, `/engines`, `/models` 엔드포인트에서 `engine_status()` 호출을 `asyncio.to_thread()`로 오프로드 — LM Studio 최대 45초, ollama subprocess 블로킹이 이벤트 루프를 점유하던 문제 해결
- 앱 종료 시 `LOCAL_SERVER_PROCESSES` (vLLM, llama.cpp) 자식 프로세스 정리 — GPU 메모리 고아 프로세스 누수 수정

### Release
- 배포 버전을 `0.1.13`으로 상향
- 대상 채널: `npm`, `PyPI`, `VS Code Marketplace`, `Open VSX`

## [0.1.12] - 2026-05-22

### Local engine install / load flow
- `vLLM` 설치 경로를 macOS용 `Python 3.12 + vllm-metal` 흐름으로 교체
- `LM Studio` 번들 `lms` CLI와 native API를 사용해 서버 시작, 모델 다운로드, 모델 로드를 자동화
- `llama.cpp`는 선택한 GGUF를 alias와 함께 OpenAI 호환 서버로 직접 로드하도록 정리
- 모델 패널의 `설치` / `다운로드 후 자동 로드` 흐름이 실제 `prepare_and_load_model()` 경로로 수렴되도록 정리

### Verified
- 최소 테스트 모델 기준 실사용 검증 완료
- `vLLM`: `Qwen/Qwen2.5-0.5B-Instruct-AWQ`
- `LM Studio`: `https://huggingface.co/lmstudio-community/Qwen2.5-0.5B-Instruct-GGUF`
- `llama.cpp`: `lmstudio-community/Qwen2.5-0.5B-Instruct-GGUF`

### Release
- 배포 버전을 `0.1.12`로 상향
- 대상 채널: `npm`, `PyPI`, `VS Code Marketplace`, `Open VSX`

## [0.1.11] - 2026-05-21

### Agent state machine (renamed + cleaned up)
- 8개의 명시적 상태: `IDLE → PLANNING → WAITING_APPROVAL → EXECUTING → VERIFYING → (DONE | ROLLBACK → FAILED)`
- `RETRY` 상태 제거 — 재시도 카운터는 `AgentRunContext.retry_count`에 보관, `VERIFYING`이 `EXECUTING`으로 직접 전환
- 종료 상태를 `DONE` / `FAILED`로 분리 — 응답에 `final_state` 필드 추가, `status`는 `"ok"` 또는 `"failed"`

### Tool Permission Layer
- `ToolPermission` 추가 — `{ tool, risk, requires_approval, network }` 4-필드 컴팩트 뷰
- 기존 7-차원 `TOOL_GOVERNANCE`에서 자동 파생 (단일 진실 공급원)
- `GET /tools/permissions` 엔드포인트 추가
- `/mcp/tools` 응답의 각 툴에 `permission` 필드 노출

### Cleanup
- 중국어 응답 지원 제거 — `detect_language`는 이제 `ko` 또는 `en`만 반환
- `_LANG_HINT`에서 `"zh"` 키 삭제, EXECUTOR_PROMPT의 "Chinese" 언급 제거

### Repo
- `CHANGELOG.md` → `docs/CHANGELOG.md` 이동 (루트 가독성 개선)
- 자동 릴리스 워크플로(`release.yml`) 제거 — 수동 배포 유지

---

## [0.1.10] - 2026-05-21

### Agent intelligence (pro-developer workflow)
- **`AGENT_SYSTEM_PROMPT` 완전 재작성** — Claude Code 스타일 시니어 개발자 워크플로
  - Discover → Plan → Implement → Verify 4단계 강제
  - JSON 응답에 `thoughts` 필드 추가, transcript에 함께 기록되어 다음 스텝의 컨텍스트로 전달
  - 코드 읽기 전 수정 금지, 검증 없이 "완료" 주장 금지, 작은 diff 원칙
  - 새 도구 카탈로그 + 안티패턴(반복 액션·환각 import·placeholder URL) 명시
- **`max_steps` 상향** — 기본값 6 → 25, 캡 10 → 50 (`AgentRequest.max_steps`)

### New tools
- **`edit_file`** — 정밀 diff 편집. `old_string`이 파일에 유일하게 존재해야만 성공(또는 `replace_all=true`). 환각 import / 잘못된 위치 수정 방지. 결과에 `first_edit_line` 포함
- **`grep`** — 정규식 검색, 전체 텍스트 파일 대상, `glob` 필터, `context_lines`, binary dir(`node_modules`, `.git`, `venv`, `dist` 등) 자동 제외. 기존 `search_files`는 호환 유지
- **`todo_write` / `todo_read`** — 워크스페이스별 영구 TODO 리스트(`agent_workspace/.lattice/todos.json`). 멀티스텝 작업의 상태 유지. status ∈ `pending | in_progress | completed`. 다중 in_progress 경고
- **`read_file` 업그레이드** — `numbered`(라인 번호 뷰), `total_lines`, `start_line`/`end_line`, optional `offset`/`limit` 추가. 기존 `content` 반환 호환 유지
- 위 모든 도구에 `/tools/*` REST 엔드포인트 추가, `_TOOL_RISK` 등록, `/mcp/tools` 카탈로그 노출

### Loop safety
- `_FILE_CREATE_ACTIONS`에 `edit_file` 포함 — 같은 args로 연속 호출 시 자동 중단
- 반복 중단 메시지를 "다음 단계로 진행하세요"로 명확화

### Tests
- `tests/unit/test_tools.py`에 23개 신규 테스트 — edit_file (유일/모호/`replace_all`/identical), grep (regex·glob·case·context·binary dir), todo round-trip + 검증, read_file numbered/offset/limit, 샌드박스 이탈 차단 (`52 passed`)

### Security (보안 기본값 통일)
- **기본 바인딩 `0.0.0.0` → `127.0.0.1` 롤백** — v0.1.8에서 PWA 편의를 위해 0.0.0.0으로 변경했으나 개인 AI 서버의 기본값은 로컬 전용이어야 안전함. 네트워크 노출이 필요한 경우 `LATTICEAI_HOST=0.0.0.0` 명시적 설정.
- SECURITY.md, CONTRIBUTING.md, GitHub Actions CI/Release 워크플로 추가
- docs/ 문서 추가: architecture, security-model, public-deploy, mcp-tools, privacy

---

## [0.1.9] - 2026-05-21

### Security
- **세션 TTL 7일 → 24시간 + sliding refresh** — 활동 시 만료시간 자동 연장, 15분 단위 디스크 쓰기 throttle
- **평문 비밀번호 마이그레이션 audit 로깅** — `password_migrated_from_plaintext` 이벤트로 남은 평문 사용자 추적
- **파일 업로드 magic-number 검증** — `_bytes_match_extension()`: PDF/DOCX/XLSX/PPTX/PNG/JPEG/ZIP 시그니처 확인, 확장자 위조 방지
- **Rate limiting** — `/chat` 30 burst/분당 30, `/agent` 10 burst/분당 6, `/upload` 20 burst/분당 12. 토큰 버킷 per-user. `LATTICEAI_RATE_LIMIT=0`으로 비활성화 가능

### Reliability
- **PyMuPDF 파일 핸들 누수 수정** — `/tools/pdf_pages` try/finally로 doc.close() 보장, `len(doc)` 호출 위치 버그 수정
- **ollama serve 좀비 방지** — 실행 전 already_up 체크, `start_new_session=True`로 detach
- **knowledge_graph.py 손상된 metadata_json 안전 처리** — `_safe_loads()` 헬퍼로 corrupt row 통과 (5곳 적용)
- **백그라운드 asyncio 태스크 예외 로깅** — `_spawn()` 헬퍼 (`add_done_callback`) — startup 태스크 silent fail 방지
- **silent except → logging.warning** — `_load_sessions`, `_persist_sessions`, `load_vpc_config`, `load_mcp_installs`

### Tests
- **`tests/unit/test_security.py`** — 16개 신규 테스트: bcrypt 해시 라운드트립/유니크, MIME 검증, rate limit (29 → 31개 전체 통과)

---

## [0.1.8] - 2026-05-21

### Added
- **PWA (Progressive Web App)** — iPad / Android / Galaxy Tab 홈화면 설치 지원
  - `manifest.json`: 앱 이름, 아이콘, 배경색, 테마색, 단축키 정의
  - `sw.js` Service Worker: 정적 파일 캐시-퍼스트, API 네트워크-퍼스트, 오프라인 대응
  - 192×192, 512×512, apple-touch-icon 180×180, favicon 32×32 PNG 아이콘 생성
  - 모든 HTML에 `<link rel="manifest">`, `apple-mobile-web-app-*`, `theme-color` 메타태그 추가
  - `viewport-fit=cover` — iPhone Dynamic Island / 노치 안전영역 확장
- **서버 네트워크 공개 바인딩** — 기본 host `127.0.0.1` → `0.0.0.0`으로 변경
  - 같은 Wi-Fi 내 iPad / Android / Galaxy Tab 에서 `http://<Mac IP>:4825` 로 바로 접근 가능
  - 시작 배너에 로컬 / 네트워크 URL 및 "Add to Home Screen" 안내 출력
- **Windows 서버 호환성**
  - `computer_screenshot`: macOS `screencapture` 외 Windows/Linux에서 pyautogui fallback
  - `computer_open_app` / `computer_open_url`: `open -a` (macOS) / `cmd /c start` (Windows) / `xdg-open` (Linux) 자동 분기
  - `_PLATFORM` 상수 도입으로 향후 플랫폼 분기 일관성 확보
- **배포 파일 포함**: `manifest.json`, `sw.js`, `icons/` 폴더를 npm · PyPI 패키지에 포함

### Deployed
- npm ✅
- PyPI ✅
- VS Code Marketplace ✅
- Open VSX ✅

---

## [0.1.7] - 2026-05-21

### Added
- **모바일 반응형 UI** — 폰/태블릿 화면 크기에 자동 대응
  - 768px 이하: 사이드바가 좌측 슬라이드 드로어로 전환, 헤더 햄버거(☰) 버튼으로 열기
  - 오버레이 탭하면 사이드바 닫힘, 대화 선택 시 자동 닫힘
  - ops-strip 카드 3개 → 가로 스크롤 한 줄 압축 (모바일)
  - `100dvh` 적용 — iOS 소프트 키보드 올라와도 레이아웃 유지
  - `env(safe-area-inset-bottom)` — iPhone 노치/홈바 안전영역 자동 여백
  - textarea `font-size: 16px` (480px 이하) — iOS 자동 줌 방지
- 브레이크포인트 3단계: 900px(태블릿) / 768px(모바일 드로어) / 480px(폰)

---

## [0.1.6] - 2026-05-21

### Added
- **LATTICEAI_ENABLE_GRAPH** 환경변수 — Data Graph 기능을 퍼블릭 서버에서 완전히 숨길 수 있는 토글 (기본값 `true`)
  - `false`로 설정 시 모든 그래프 API 엔드포인트 404 반환, 인제스트 건너뜀, 사이드바 버튼 자동 숨김
- `.env.example`에 `LATTICEAI_ENABLE_GRAPH` 항목 추가 (로컬/퍼블릭 모드 각각)

---

## [0.1.5] - 2026-05-21

### Added
- **Data Graph** — 채팅·AI 답변·업로드 문서를 SQLite 지식 그래프로 자동 구조화, `/graph`에서 Canvas 기반 Force-directed 시각화
- **Graph RAG** — 그래프 검색 결과를 채팅 컨텍스트에 자동 주입하여 이전 대화·문서 참조 능력 강화
- **Telegram 원격 제어** — 인라인 키보드 메뉴로 상태 조회, 모델 관리, 스크린샷, 그래프 통계, 문서 업로드 등 원격 제어
- `knowledge_graph.py` — KnowledgeGraphStore (node/edge/chunk/event), `ingest_message()`, `ingest_document()`, `context_for_query()`, `search()`, `neighbors()`
- `static/graph.html` — 타입별 색상, 줌/패닝, 핀치 줌, 이웃 하이라이트, 노드 상세 정보, 채팅 연결 링크

### Security
- 어드민 세션 핸드오프를 URL 파라미터 → `sessionStorage` 1회 읽기 방식으로 교체 (히스토리 노출 방지)
- `X-Admin-Email` 헤더 폴백 제거 — Bearer 토큰 인증만 허용

---

## [0.1.4] - 2026-05-18

### Added
- **세션 영속성** — 서버 재시작 후에도 로그인 유지 (sessions.json 파일 기반)
- **SSO 로그인** — Entra ID / Okta OIDC 지원 (`OIDC_DISCOVERY_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` 환경변수)
- **채팅 히스토리 검색** — 사이드바 검색창으로 대화 내용 키워드 검색 (`GET /history/search`)
- **대화 삭제** — 사이드바 각 대화에 삭제 버튼 추가
- **MCP 서버 관리 UI** — 사이드바 "MCP 관리" 버튼으로 설치/목록 확인 모달
- **인라인 Diff 뷰** — Edit Selection 결과를 diff로 보여주고 Apply/Discard 선택
- **현재 파일 첨부** — `Lattice AI: Attach Current File to Chat` 명령 추가 (VS Code)
- `authlib` 의존성 추가 (SSO OIDC 지원)

### Deployed
- PyPI ✅
- npm ✅
- VS Code Marketplace ✅
- Open VSX ✅

---

## [0.1.3] - 2026-05-18

### Added
- 프로필 수정 API (`PATCH /account/profile`) 및 UI — 이름·닉네임 변경
- 회원가입 폼 개선 — 비밀번호 확인 필드, 인라인 에러 메시지
- 어드민 패널 초대 링크 섹션 — 원클릭 복사
- 어드민 대시보드 메시지 활동 차트 (Chart.js, 최근 14일)
- 웹 UI 한국어 / 영어 전환 (`🌐 Languages` 버튼, localStorage 저장)

### Fixed
- 로그아웃 시 `/logout` API 호출하여 서버 세션 쿠키 정상 만료
- 인증(`account.html`)과 채팅(`chat.html`) UI 분리 — 레거시 `index.html` 제거
- `chat.html` 내 죽은 인증 코드 제거
- 채팅 헤더에서 언어 선택 드롭다운이 ops-strip을 가리는 문제 수정

### Deployed
- PyPI ✅
- npm ✅
- VS Code Marketplace ✅
- Open VSX ✅

---

## [0.1.1] - 2026-05-18

### Added
- 비밀번호 변경 API (`POST /account/change-password`)
- 웹 UI 비밀번호 변경 모달 (헤더 계정 아이콘)

### Docs
- 어드민 패널: 첫 가입자 자동 admin 안내 추가
- 플랫폼 지원 범위 (Windows/Linux) 안내 추가
- 언어 지원 (KO/EN) 안내 추가

### Deployed
- PyPI ✅
- npm ✅
- VS Code Marketplace ✅
- Open VSX ✅

---

## [0.1.0] - 2026-05-17

### Added
- FastAPI 브릿지 서버 (port 4825)
- Apple Silicon MLX 로컬 모델 지원 (Gemma 4, Qwen 2.5 등)
- 클라우드 모델 지원 (OpenAI, Groq, Together, OpenRouter 등)
- VS Code / Cursor / Antigravity 확장
- Telegram 봇 (로컬 AI 미러 + Codex 클라우드 봇)
- 어드민 패널 (`/admin`)
- P-Reinforce 지식 정원 엔진
- MCP 서버 연동
- Ollama / vLLM / LM Studio / llama.cpp 연동

### Security
- 모든 민감 엔드포인트 인증 적용
- SameSite=Lax 쿠키 (CSRF 방어)
- scrypt 비밀번호 해싱
- tempfile 레이스 컨디션 수정
- `run_command()` 위험 플래그 차단

### Deployed
- PyPI ✅
- npm ✅
- VS Code Marketplace ✅
- Open VSX ✅
