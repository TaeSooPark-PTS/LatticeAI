# Changelog

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
