# Lattice AI

**개인 AI 워크스페이스 서버** — 로컬/클라우드 LLM을 웹 UI · VS Code 확장 · Telegram 봇 · MCP 도구 하나로 묶습니다.

Apple Silicon MLX 로컬 추론 · OpenAI/Groq/OpenRouter 클라우드 모델 · Graph RAG · 멀티스텝 에이전트 워크플로

[![PyPI](https://img.shields.io/pypi/v/ltcai?label=pypi)](https://pypi.org/project/ltcai/)
[![npm](https://img.shields.io/npm/v/ltcai?label=npm)](https://www.npmjs.com/package/ltcai)
[![VS Code Marketplace](https://vsmarketplacebadges.dev/version/parktaesoo.ltcai.svg)](https://marketplace.visualstudio.com/items?itemName=parktaesoo.ltcai)
[![Open VSX](https://img.shields.io/open-vsx/v/parktaesoo/ltcai?label=Open%20VSX)](https://open-vsx.org/extension/parktaesoo/ltcai)
[![License](https://img.shields.io/github/license/TaeSooPark-PTS/LatticeAI)](./LICENSE)

Lattice AI는 개인 개발자가 로컬 모델, 클라우드 모델, 에이전트 툴링, 코드 에디터 연동을 하나의 워크스페이스로 운영할 수 있게 만든 서버입니다.

### 현재 배포 버전

- `PyPI`: `ltcai==0.1.17`
- `npm`: `ltcai@0.1.17`
- `VS Code Marketplace`: `parktaesoo.ltcai@0.1.17`
- `Open VSX`: `parktaesoo.ltcai@0.1.17`

### 왜 Lattice AI인가

- **하나의 서버, 여러 인터페이스**: 웹 UI, VS Code/Cursor 확장, Telegram 봇, MCP 도구를 한 번에 연결합니다.
- **로컬 우선 + 클라우드 선택**: Apple Silicon MLX 로컬 모델과 OpenAI 호환 클라우드 모델을 같은 UX로 다룹니다.
- **실전형 에이전트 워크플로**: 파일 편집, grep, todo, 터미널 도구를 묶어 멀티스텝 작업을 수행합니다.

### 빠른 링크

- [설치 & 첫 실행](#설치--첫-실행-30초)
- [퍼블릭 배포 가이드](./docs/public-deploy.md)
- [보안 모델](./docs/security-model.md)
- [아키텍처](./docs/architecture.md)
- [변경 이력](./docs/CHANGELOG.md)

---

## 설치 & 첫 실행 (30초)

```bash
# PyPI (클라우드 모델)
pip install ltcai

# PyPI (Apple Silicon MLX 로컬 모델 포함)
pip install "ltcai[local]"

# npm (자동 Python 환경 구성)
npm install -g ltcai

# 서버 실행 (로컬)
LTCAI
# → http://localhost:4825

# 외부에서 접속 가능하게 실행 (Cloudflare 터널 자동 개설)
LTCAI --tunnel
# → http://localhost:4825
# → https://xxxx.trycloudflare.com  ← 어디서든 접속 가능한 공개 URL
```

**`--tunnel` 동작 방식:**
- cloudflared가 없으면 자동 다운로드 (계정 불필요)
- 서버를 `0.0.0.0`에 바인딩하고 Cloudflare 무료 터널로 HTTPS 공개 URL 발급
- `LATTICEAI_TELEGRAM_BOT_TOKEN` + `LATTICEAI_TELEGRAM_CHAT_ID` 환경변수가 있으면 시작 시 Telegram으로 URL 자동 전송
- 서버 종료 시 터널도 함께 종료

**설치 확인:**

```
$ LTCAI doctor
[OK] Python 3.11+: 3.11.9
[OK] FastAPI: required server dependency
[OK] Uvicorn: required server dependency
[OK] OpenAI SDK: required for cloud providers
[OK] MLX: required for Apple Silicon local models
[OK] MLX-LM: required for local text models
[OK] MLX-VLM: required for Gemma/VLM models
[OPTIONAL] Ollama binary: optional local-server engine
[OK] Data dir: /Users/you/.ltcai
[OK] Static UI: /path/to/static
[INFO] Cloud keys configured: OPENAI_API_KEY
```

---

## 첫 채팅 → VS Code 연결 → Telegram 연결

### 1단계: 첫 채팅

1. `http://localhost:4825` 열기
2. **회원가입** → 첫 번째 계정이 자동으로 admin
3. 상단 모델 드롭다운 → 모델 선택 → 채팅 시작

클라우드 모델 사용 시 API 키를 먼저 설정하거나, 어드민 패널에서 입력합니다:

```bash
OPENAI_API_KEY=sk-... LTCAI
```

### 2단계: VS Code 연결

1. VS Code → Extensions → `ltcai` 검색 → Install
2. `Cmd+Shift+A` → Lattice AI 채팅 패널 열기
3. 기본적으로 `http://localhost:4825` 에 자동 연결됩니다

| 단축키 | 기능 |
|--------|------|
| `Cmd+Shift+A` | 채팅 패널 열기 |
| `Cmd+Shift+E` | 선택 코드 편집 |
| `Cmd+Shift+M` | 모델 로드 / 전환 |
| 우클릭 메뉴 | Explain / Edit / Knowledge Garden 저장 |

### 3단계: Telegram 봇 연결

BotFather에서 토큰을 발급받은 후:

```bash
LATTICEAI_TELEGRAM_BOT_TOKEN=your-token LTCAI
```

이후 Telegram에서 봇과 대화하면 로컬 AI 서버와 실시간으로 연결됩니다.

---

## 기능 개요

| 기능 | 설명 |
|------|------|
| **웹 UI** | 반응형 채팅 + 어드민 패널 + 그래프 시각화 |
| **VS Code / Cursor 확장** | 채팅, Edit Selection, Diff 뷰, 파일 첨부 |
| **Telegram 봇** | 로컬 AI 미러 + Codex 클라우드 봇 |
| **MCP 서버** | Claude Desktop / Cursor에서 직접 도구 사용 |
| **MLX 로컬 추론** | Apple Silicon에서 Gemma, Qwen, DeepSeek 등 |
| **클라우드 모델** | OpenAI, Groq, Together, OpenRouter |
| **Graph RAG** | 채팅·문서를 SQLite 지식 그래프로 자동 구조화 |
| **에이전트** | 파일 편집·생성, grep, todo, 터미널 (25스텝) |
| **PWA** | iPad / Android 홈화면 설치 지원 |
| **SSO** | Entra ID / Okta OIDC |

---

## 플랫폼 지원

| 기능 | macOS (Apple Silicon) | Windows / Linux |
|------|:---:|:---:|
| 웹 UI / 클라우드 모델 | ✅ | ✅ |
| VS Code / Cursor 확장 | ✅ | ✅ |
| Telegram 봇 | ✅ | ✅ |
| MLX 로컬 모델 (Gemma, Qwen 등) | ✅ | ❌ Apple Silicon 전용 |
| Ollama / vLLM / LM Studio 연동 | ✅ | ✅ |

---

## 로컬 모델 (Apple Silicon)

```bash
LATTICEAI_MODE=local \
LATTICEAI_LOCAL_MODEL=mlx-community/gemma-4-26b-a4b-it-4bit \
LTCAI
```

### 추천 모델 (M-series Mac)

| 모델 | 용도 | 크기 | 추천 |
|------|------|------|------|
| `mlx-community/gemma-4-26b-a4b-it-4bit` | 범용/코딩 | ~14GB | ⭐⭐⭐⭐⭐ |
| `mlx-community/Qwen2.5-Coder-32B-Instruct-4bit` | 코딩 | ~18GB | ⭐⭐⭐⭐⭐ |
| `mlx-community/Qwen2.5-Coder-14B-Instruct-4bit` | 코딩 | ~8GB | ⭐⭐⭐⭐ |
| `mlx-community/DeepSeek-R1-0528-4bit` | 추론 | ~38GB | ⭐⭐⭐⭐ |
| `mlx-community/Phi-4-4bit` | 코딩 | ~8GB | ⭐⭐⭐⭐ |

> **32GB Mac 추천**: `gemma-4-26b-a4b-it-4bit` — 빠르고 뛰어난 범용 성능

### 멀티모델 핫스왑

```bash
curl -X POST localhost:4825/models/load \
  -H "Content-Type: application/json" \
  -d '{"model_id": "mlx-community/Qwen2.5-Coder-14B-Instruct-4bit"}'
```

---

## 퍼블릭 모드 (클라우드 서버)

Render, Fly.io, Railway, VPS 등에서 운영할 때:

```bash
LATTICEAI_MODE=public \
LATTICEAI_ALLOW_LOCAL_MODELS=false \
LATTICEAI_PUBLIC_MODEL=openai:gpt-4o-mini \
OPENAI_API_KEY=sk-... \
LATTICEAI_INVITE_CODE=my-secret-code \
LTCAI
```

자세한 내용은 [docs/public-deploy.md](docs/public-deploy.md)를 참고하세요.

### Docker

```bash
docker build -t lattice-ai .
docker run --rm -p 4825:4825 \
  -e OPENAI_API_KEY="$OPENAI_API_KEY" \
  -e LATTICEAI_INVITE_CODE="my-secret-code" \
  -v "$PWD/.data:/data" \
  lattice-ai
```

---

## 보안

- **바인딩**: 기본 `127.0.0.1:4825` (로컬 전용) — 같은 Wi-Fi 기기 접근 허용 시 `LATTICEAI_HOST=0.0.0.0` 설정
- **인증**: 모든 민감 엔드포인트 로그인 세션 필요 (`REQUIRE_AUTH=true` 기본)
- **세션**: 24시간 TTL + sliding refresh, 서버 디스크 저장 (재시작 후에도 유지)
- **CORS**: 기본 localhost만 허용 — 외부 허용 시 `LATTICEAI_CORS_ALLOW_NETWORK=true`
- **API 키**: OS keyring/Keychain 저장 (평문 미저장)
- **쿠키**: `HttpOnly + SameSite=Lax` (CSRF 방어)
- **Rate limit**: `/chat` 30/분, `/agent` 6/분, `/upload` 12/분 (per user)
- **파일 업로드**: magic-number 시그니처 검증 (확장자 위조 차단)
- **텔레메트리**: 없음. 모든 데이터는 로컬(`~/.ltcai/`)에만 저장됩니다.

보안 취약점 제보: [SECURITY.md](SECURITY.md) 참고

---

## 문제 해결

| 증상 | 원인 | 해결 |
|------|------|------|
| 포트 4825 이미 사용 중 | 이전 프로세스 잔존 | `lsof -i :4825` → `kill <PID>` 또는 `--port 4826` |
| `ModuleNotFoundError: mlx` | MLX 미설치 | `pip install "ltcai[local]"` (Apple Silicon 전용) |
| Python 3.10 이하 | 버전 불일치 | Python 3.11+ 필요. `python3 --version` 확인 |
| `pyautogui` 권한 오류 | macOS 접근성 권한 | 시스템 설정 → 개인정보 보호 → 접근성 → 터미널 허용 |
| `LTCAI doctor` OPTIONAL 표시 | 선택 의존성 미설치 | 해당 기능 불필요 시 무시 가능 |
| API 키 없음 경고 | 클라우드 모델 미설정 | `OPENAI_API_KEY=sk-...` 환경변수 또는 어드민 패널에서 입력 |
| Telegram 봇 응답 없음 | 토큰 미설정 또는 서버 미실행 | `LATTICEAI_TELEGRAM_BOT_TOKEN` 확인, 서버 로그 확인 |
| iPad / 다른 기기에서 접근 안 됨 | 기본 바인딩 127.0.0.1 | `LATTICEAI_HOST=0.0.0.0 LTCAI` 로 재시작 |

---

## 에디터 확장

| 마켓플레이스 | 링크 |
|---|---|
| VS Code / Cursor | [marketplace.visualstudio.com](https://marketplace.visualstudio.com/items?itemName=parktaesoo.ltcai) |
| Antigravity / VSCodium | [open-vsx.org](https://open-vsx.org/extension/parktaesoo/ltcai) |

### 릴리스 체크

`0.1.17 릴리스는 아래 네 채널을 동일 버전으로 맞춥니다.

- `npm`
- `PyPI`
- `VS Code Marketplace`
- `Open VSX`

### 수동 설치 (VSIX)

```bash
cd vscode-extension
npm install && npm run build && npm run package:vsix
# Extensions → "..." → "Install from VSIX"
```

---

## 어드민 패널

`http://localhost:4825/admin` — 관리자 계정으로 로그인 후 접근

- 사용자 목록 및 역할 관리 (admin / user)
- 대시보드 (메모리, 모델, 시스템 상태, 활동 차트)
- 초대 링크 생성 및 복사

> 첫 번째로 가입한 계정이 자동으로 admin입니다.
> 환경변수로 고정: `LATTICEAI_ADMIN_EMAILS=you@example.com`

---

## P-Reinforce 지식 정원

코드/텍스트를 `~/.ltcai-brain/`에 자동 분류 저장합니다.

```
~/.ltcai-brain/
├── 00_Raw/       # 원시 데이터, 아이디어
├── 10_Wiki/      # 검증된 개념, 레퍼런스
├── 20_Skills/    # 코드 스니펫, 프롬프트
├── 30_Projects/  # 프로젝트 컨텍스트
└── 40_Log/       # 날짜별 작업 로그
```

에디터에서 텍스트 선택 → 우클릭 → **"Save to Knowledge Garden"**

---

## Data Graph / Graph RAG

채팅·AI 답변·업로드 문서(PDF/DOCX/XLSX/PPTX/TXT/CSV)를 `~/.ltcai/knowledge_graph.sqlite`에 자동 저장합니다.

- 시각화: `http://localhost:4825/graph`
- 검색 및 RAG 컨텍스트 자동 주입

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
| GET | `/tools/list_dir` | 디렉토리 목록 |
| POST | `/tools/run_command` | 터미널 명령 실행 |
| GET | `/mcp/installed` | 설치된 MCP 목록 |

자세한 MCP 도구 목록: [docs/mcp-tools.md](docs/mcp-tools.md)

---

## 자동 시작 (Mac)

```bash
cat > ~/Library/LaunchAgents/com.ltcai.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.ltcai</string>
  <key>ProgramArguments</key><array>
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

또는: `./start_ai.sh` (자동 재시작 + caffeinate)

---

## 기여

[CONTRIBUTING.md](CONTRIBUTING.md)를 참고하세요.

## 보안 취약점 제보

[SECURITY.md](SECURITY.md)를 참고하세요.

## 릴리스 노트

현재 버전: **0.1.17** — 자세한 변경 이력은 [docs/CHANGELOG.md](docs/CHANGELOG.md) 참고.

## 라이선스

MIT
