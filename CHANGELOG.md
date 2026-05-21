# Changelog

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
