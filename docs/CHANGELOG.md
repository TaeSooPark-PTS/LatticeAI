# Changelog

## [0.1.19] - 2026-05-23

### Publisher 변경

- VS Code extension publisher `parktaesoo` → `TaeSooPark-PTS` 로 변경
- Extension ID: `parktaesoo.ltcai` → `TaeSooPark-PTS.ltcai`
- Open VSX namespace 통일 (`TaeSooPark-PTS`)
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
