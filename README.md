# Lattice AI

Local/cloud LLM workspace server — Apple Silicon MLX, OpenAI-compatible providers, MCP, VS Code/Cursor extension, Telegram bot.

```bash
pip install ltcai          # PyPI
npm install -g ltcai       # npm
LTCAI                      # → http://localhost:4825
```

---

## v0.1.8 변경사항

### Added
- **PWA (Progressive Web App)** — iPad / Android / Galaxy Tab 홈화면 설치 지원
  - `manifest.json` + Service Worker (`sw.js`) + 아이콘 4종 (192px, 512px, apple-touch-icon 180px, favicon 32px)
  - iOS Safari: 공유 버튼 → "홈 화면에 추가" → 앱처럼 전체화면 실행
  - Android Chrome: 주소창 "설치" 버튼 → 홈화면 앱
  - `viewport-fit=cover` — iPhone Dynamic Island / 노치 안전영역 확장
- **서버 네트워크 공개 바인딩** — 기본 host `0.0.0.0`으로 변경
  - 같은 Wi-Fi에서 모든 기기(`http://<Mac IP>:4825`)로 바로 접근 가능
  - 시작 배너에 로컬 URL + 네트워크 IP 자동 출력
- **Windows 서버 호환성**
  - `computer_screenshot`: macOS `screencapture` → Windows/Linux `pyautogui` fallback
  - `computer_open_app` / `computer_open_url`: `open -a` / `cmd /c start` / `xdg-open` 플랫폼 자동 분기

---

## v0.1.7 변경사항

### Added
- **모바일 반응형 UI** — 폰/태블릿 화면 크기에 자동 대응
  - 사이드바 슬라이드 드로어 (☰ 햄버거 버튼, 오버레이 탭으로 닫기)
  - iOS 소프트 키보드 대응 (`100dvh`), 노치/홈바 안전영역 (`safe-area-inset`)
  - ops-strip 카드 가로 스크롤 압축, textarea 자동 줌 방지
- 브레이크포인트 3단계: 900px / 768px / 480px

---

## v0.1.6 변경사항

### Added
- **LATTICEAI_ENABLE_GRAPH** 환경변수 — Data Graph를 퍼블릭 배포에서 완전히 비활성화하는 토글 (기본값 `true`)
  - `false` 설정 시 모든 그래프 API 404, 인제스트 건너뜀, 사이드바 버튼 자동 숨김

---

## v0.1.5 변경사항

### Added
- **Data Graph** — 채팅·AI 답변·업로드 문서를 SQLite 지식 그래프로 자동 구조화, `/graph`에서 Canvas 기반 Force-directed 시각화
- **Graph RAG** — 그래프 검색 결과를 채팅 컨텍스트에 자동 주입하여 이전 대화·문서 참조 강화
- **Telegram 원격 제어** — 인라인 키보드 메뉴로 상태 조회, 모델 관리, 스크린샷, 그래프 통계 원격 제어
- 어드민 세션 핸드오프 보안 강화 — URL 파라미터 → `sessionStorage` 1회 읽기 방식으로 교체

---

## v0.1.4 변경사항

### Added
- 세션 영속성 — 서버 재시작 후에도 로그인 유지
- SSO 로그인 — Entra ID / Okta OIDC 지원 (`OIDC_DISCOVERY_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`)
- 채팅 히스토리 검색 — 사이드바 검색창으로 대화 내용 키워드 검색
- 대화 삭제 — 사이드바 각 대화에 삭제 버튼
- MCP 서버 관리 UI — 사이드바 "MCP 관리" 버튼으로 설치/목록 모달
- VS Code 인라인 Diff 뷰 — Edit Selection 결과를 diff로 먼저 확인 후 Apply/Discard
- VS Code 현재 파일 첨부 — `Lattice AI: Attach Current File to Chat` 명령 추가

---

## v0.1.3 변경사항

### Added
- 프로필 수정 (`PATCH /account/profile`) — 이름·닉네임 변경
- 회원가입 폼 — 비밀번호 확인 필드, 인라인 에러 메시지
- 어드민 패널 초대 링크 섹션 — 원클릭 복사
- 어드민 대시보드 메시지 활동 차트 (Chart.js, 최근 14일)
- 웹 UI 한국어 / 영어 전환 (`🌐 Languages` 버튼, localStorage 저장)

### Fixed
- 로그아웃 시 `/logout` API 호출로 서버 세션 쿠키 정상 만료
- 인증(`account.html`)과 채팅(`chat.html`) UI 분리 — 레거시 파일 제거
- 채팅 헤더에서 언어 선택 드롭다운이 ops-strip을 가리는 문제 수정

---

## v0.1.1 변경사항

### Added
- 비밀번호 변경 API (`POST /account/change-password`)
- 웹 UI 비밀번호 변경 모달 (헤더 계정 아이콘)

---

## v0.1.0 변경사항

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

---

## 아키텍처

```
Lattice AI/
├── server.py              # FastAPI 브릿지 서버 (port 4825)
├── knowledge_graph.py     # SQLite Data Graph + Graph RAG 저장소
├── llm_router.py          # 로컬/클라우드 모델 라우터
├── tools.py               # 워크스페이스 도구 (파일, 터미널, 스크린샷 등)
├── p_reinforce.py         # P-Reinforce 지식 정원 엔진
├── telegram_bot.py        # 로컬 AI Telegram 미러 봇
├── codex_telegram_bot.py  # 클라우드 Codex Telegram 봇
├── static/                # 웹 UI (indexd.html), 어드민 패널 (admin.html)
├── bin/ltcai.js           # npm CLI entrypoint
├── pyproject.toml         # PyPI 메타데이터
└── vscode-extension/      # VS Code / Cursor / Antigravity 확장
```

---

## 언어 지원

웹 UI는 **한국어 / 영어** 전환을 지원합니다.

- 로그인 페이지 우측 상단 **🌐 Languages** 버튼
- 메인 화면 헤더 **🌐** 버튼
- 선택한 언어는 브라우저에 저장됩니다

---

## 플랫폼 지원

| 기능 | macOS (Apple Silicon) | Windows / Linux |
|------|:---:|:---:|
| 웹 UI / 클라우드 모델 (OpenAI, Groq 등) | ✅ | ✅ |
| VS Code / Cursor 확장 | ✅ | ✅ |
| Telegram 봇 | ✅ | ✅ |
| MLX 로컬 모델 (Gemma, Qwen 등) | ✅ | ❌ Apple Silicon 전용 |
| Ollama / vLLM / LM Studio 연동 | ✅ | ✅ |

> Windows / Linux에서 로컬 모델을 사용하려면 서버 실행 후 웹 UI(`http://localhost:4825`)에서 Ollama 등을 설치할 수 있습니다.

---

## 빠른 시작

### 설치 & 실행

```bash
# PyPI (기본 — 클라우드 모델만)
pip install ltcai

# PyPI (Apple Silicon MLX 포함)
pip install "ltcai[local]"

# npm
npm install -g ltcai

# 서버 실행
LTCAI
# → http://localhost:4825
```

#### 개발 모드

```bash
python ltcai_cli.py
python ltcai_cli.py --reload   # 코드 변경 시 자동 재시작

LTCAI doctor                   # 의존성 및 환경 체크
```

npm으로 설치한 경우 첫 실행 시 `~/.ltcai/npm-python`에 Python 가상환경을 자동으로 생성합니다.
자동 설치를 끄려면 `LTCAI_SKIP_NPM_BOOTSTRAP=1`을 설정하세요.

런타임 데이터는 기본적으로 `~/.ltcai/`에 저장됩니다. 경로 변경: `LATTICEAI_DATA_DIR=/path/to/data`

---

## 로컬 모드 (Apple Silicon)

```bash
LATTICEAI_MODE=local \
LATTICEAI_LOCAL_MODEL=mlx-community/gemma-4-26b-a4b-it-4bit \
LTCAI
```

- MLX 로컬 모델 자동 로드
- Telegram 미러 봇 활성화 가능
- 파일/터미널/스크린샷 도구 사용 가능

---

## 퍼블릭 모드 (클라우드 서버)

Render, Fly.io, Railway, VPS 등에서 운영할 때 사용합니다. MLX를 사용하지 않고 클라우드 모델로 동작합니다.

```bash
LATTICEAI_MODE=public \
LATTICEAI_ALLOW_LOCAL_MODELS=false \
LATTICEAI_ENABLE_TELEGRAM=false \
LATTICEAI_PUBLIC_MODEL=openai:gpt-4o-mini \
OPENAI_API_KEY=sk-... \
LATTICEAI_INVITE_CODE=my-secret-code \
LTCAI
```

지원 클라우드 모델 프리픽스:

```
openai:gpt-4o-mini
openrouter:openai/gpt-4o-mini
groq:llama-3.1-8b-instant
together:meta-llama/Llama-3.3-70B-Instruct-Turbo
```

### Docker

```bash
docker build -t lattice-ai .
docker run --rm -p 4825:4825 \
  -e OPENAI_API_KEY="$OPENAI_API_KEY" \
  -e LATTICEAI_INVITE_CODE="my-secret-code" \
  -v "$PWD/.data:/data" \
  lattice-ai
```

### 퍼블릭 서버 체크리스트

- `LATTICEAI_MODE=public` 설정
- 클라우드 API 키 설정 (`OPENAI_API_KEY` 등)
- `LATTICEAI_INVITE_CODE`를 비공개 값으로 설정
- `LATTICEAI_ENABLE_GRAPH=false` — Data Graph를 공개 서버에서 숨기려면 설정
- `/data`에 영구 볼륨 마운트
- HTTPS 리버스 프록시 앞에 두기 (nginx, Caddy 등)

---

## 모델

### 지원 모델 예시 (M-series Mac 기준)

| 모델 | 용도 | 크기 | 추천도 |
|------|------|------|--------|
| `mlx-community/gemma-4-26b-a4b-it-4bit` | 범용/코딩 | ~14GB | ⭐⭐⭐⭐⭐ |
| `mlx-community/Qwen2.5-Coder-32B-Instruct-4bit` | 코딩 | ~18GB | ⭐⭐⭐⭐⭐ |
| `mlx-community/Qwen2.5-Coder-14B-Instruct-4bit` | 코딩 | ~8GB | ⭐⭐⭐⭐ |
| `mlx-community/Qwen2.5-Coder-7B-Instruct-4bit` | 코딩 | ~4GB | ⭐⭐⭐ |
| `mlx-community/DeepSeek-R1-0528-4bit` | 추론 | ~38GB | ⭐⭐⭐⭐ |
| `mlx-community/Phi-4-4bit` | 코딩 | ~8GB | ⭐⭐⭐⭐ |
| `mlx-community/Llama-3.1-8B-Instruct-4bit` | 범용 | ~4.5GB | ⭐⭐⭐ |

> **32GB Mac 추천**: gemma-4-26b-a4b-it-4bit — 빠르고 뛰어난 범용 성능

### 멀티모델 핫스왑

```bash
# 모델 로드
curl -X POST localhost:4825/models/load \
  -H "Content-Type: application/json" \
  -d '{"model_id": "mlx-community/Qwen2.5-Coder-14B-Instruct-4bit"}'

# 즉시 전환 (재로드 없음)
curl -X POST localhost:4825/models/switch/mlx-community%2FQwen2.5-Coder-14B-Instruct-4bit

# 언로드
curl -X DELETE localhost:4825/models/unload/mlx-community%2FQwen2.5-Coder-14B-Instruct-4bit
```

---

## 에디터 확장

| 마켓플레이스 | 링크 |
|---|---|
| VS Code / Cursor | [marketplace.visualstudio.com](https://marketplace.visualstudio.com/items?itemName=parktaesoo.ltcai) |
| Antigravity / VSCodium | [open-vsx.org](https://open-vsx.org/extension/parktaesoo/ltcai) |

### 수동 설치 (VSIX)

```bash
cd vscode-extension
npm install
npm run build
npm run package:vsix

# 설치 (모든 에디터 한 번에)
npm run install:all

# 또는 에디터에서: Extensions → "..." → "Install from VSIX"
```

### 키보드 단축키

| 단축키 | 기능 |
|--------|------|
| `Cmd+Shift+A` | 채팅 패널 열기 |
| `Cmd+Shift+E` | 선택 코드 편집 |
| `Cmd+Shift+M` | 모델 로드 / 전환 |
| 우클릭 메뉴 | Explain / Edit / Knowledge Garden에 저장 |

---

## Telegram 봇

### 1. 로컬 AI 봇 (local 모드)

로컬 Lattice AI 서버와 대화하고 웹 채팅을 Telegram으로 미러링합니다.

```bash
LATTICEAI_TELEGRAM_BOT_TOKEN=your-token LTCAI
```

### 2. Codex 클라우드 봇

Telegram에서 GPT 기반 개발 어시스턴트와 대화하고, 선택적으로 GitHub 이슈를 생성합니다.

```bash
CODEX_TELEGRAM_BOT_TOKEN=your-token \
OPENAI_API_KEY=sk-... \
CODEX_OPENAI_MODEL=gpt-4o \
python codex_telegram_bot.py
```

Telegram 명령어: `/start` `/reset` `/issue 제목`

선택적으로 GitHub 이슈 연동:

```bash
GITHUB_TOKEN=ghp-... GITHUB_REPO=owner/repo
```

---

## 보안

- **인증**: 모든 `/tools/*`, `/agent`, `/mcp/*`, `/local/*` 등 민감 엔드포인트는 로그인 세션 필요 (`REQUIRE_AUTH=true` 시)
- **세션**: 7일 TTL, 서버 메모리 저장 (재시작 시 로그아웃)
- **바인딩**: 기본 `127.0.0.1:4825` — 외부 접근 허용 시 명시적으로 `LATTICEAI_HOST=0.0.0.0` 설정
- **CORS**: 기본 localhost만 허용 — 외부 허용 시 `LATTICEAI_CORS_ALLOW_NETWORK=true`
- **API 키**: OS keyring/Keychain 저장 — 평문 저장 허용 시 `LATTICEAI_ALLOW_PLAINTEXT_API_KEYS=true`
- **히스토리**: 저장 전 API key/token/password 패턴 자동 마스킹
- **쿠키**: `HttpOnly + SameSite=Lax` (CSRF 방어)

---

## 어드민 패널

`http://localhost:4825/admin` — 관리자 계정으로 로그인 후 접근 가능

- 사용자 목록 및 역할 관리 (admin / user)
- 사용자 비활성화 / 삭제
- 대시보드 (메모리, 모델, 시스템 상태)

> **첫 번째로 가입한 계정이 자동으로 admin이 됩니다.** 서버를 처음 실행한 후 `/register` 또는 웹 UI에서 회원가입하면 됩니다. 이후 추가 admin은 어드민 패널에서 지정할 수 있습니다.
>
> 환경변수로 admin을 고정할 수도 있습니다:
> ```bash
> LATTICEAI_ADMIN_EMAILS=you@example.com LTCAI
> ```

---

## P-Reinforce 지식 정원

코드/텍스트를 `~/.ltcai-brain/`에 자동 분류 저장합니다.

```
~/.ltcai-brain/
├── INDEX.md
├── 00_Raw/       # 원시 데이터, 아이디어
├── 10_Wiki/      # 검증된 개념, 레퍼런스
├── 20_Skills/    # 코드 스니펫, 프롬프트
├── 30_Projects/  # 프로젝트 컨텍스트
└── 40_Log/       # 날짜별 작업 로그
```

에디터에서 텍스트 선택 → 우클릭 → **"Save to Knowledge Garden"**

또는 API:

```bash
curl -X POST localhost:4825/garden \
  -H "Content-Type: application/json" \
  -d '{"content": "학습한 내용", "category": "10_Wiki"}'
```

---

## Data Graph / Graph RAG

웹 채팅과 AI 답변, 업로드 문서(PDF/DOCX/XLSX/PPTX/TXT/CSV)를 `~/.ltcai/knowledge_graph.sqlite`에 자동 저장합니다.

- 채팅/AI 답변: `Message`, `AIResponse`, `Person`, `Conversation`, `Topic`, `Decision`, `Task`
- 문서 업로드: `File`, `Chunk`, `Page`, `Slide`, `Sheet`, `Image`, `Topic`
- 원본 파일 blob: `~/.ltcai/knowledge_graph_blobs/`
- 시각화: `http://localhost:4825/graph`
- API: `/knowledge-graph/graph`, `/knowledge-graph/search`, `/knowledge-graph/context`

`/clear`, `/clear_all`, 대화 삭제는 사용자 채팅창만 정리합니다. Data Graph/RAG 데이터와 관리자 감사 로그는 보존됩니다.

---

## API 엔드포인트

| Method | Path | 설명 |
|--------|------|------|
| GET | `/health` | 서버 상태, 현재 모델 |
| GET | `/models` | 추천 모델 목록 + 로드 상태 |
| POST | `/models/load` | 모델 로드 |
| POST | `/models/switch/{id}` | 활성 모델 전환 |
| DELETE | `/models/unload/{id}` | 모델 언로드 |
| POST | `/chat` | 채팅 생성 (`stream=true/false`) |
| POST | `/agent` | 파일 생성/수정 에이전트 |
| POST | `/garden` | 지식 정원 저장 |
| GET | `/garden/tree` | 지식 트리 조회 |
| GET | `/tools/list_dir` | 디렉토리 목록 |
| POST | `/tools/run_command` | 터미널 명령 실행 |
| GET | `/mcp/installed` | 설치된 MCP 목록 |

---

## 자동 시작 (Mac)

```bash
cat > ~/Library/LaunchAgents/com.ltcai.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.ltcai</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/LTCAI</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/ltcai.log</string>
  <key>StandardErrorPath</key><string>/tmp/ltcai.err</string>
</dict>
</plist>
EOF

launchctl load ~/Library/LaunchAgents/com.ltcai.plist
```

또는 동봉된 스크립트 사용:

```bash
./start_ai.sh   # 자동 재시작 + caffeinate (슬립 방지)
```

---

## 라이선스

MIT
