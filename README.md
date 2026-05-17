# Lattice AI

Local/cloud LLM workspace server with MLX, Ollama, vLLM, OpenAI-compatible providers,
BYOK API keys, MCP recommendations, and editor extensions for VS Code, Cursor, and Antigravity.

---

## 아키텍처

```
Lattice AI/
├── server.py              # FastAPI bridge server (port 4825)
├── llm_router.py          # local/cloud model router
├── tools.py               # local workspace tools
├── static/                # web UI
├── bin/ltcai.js           # npm CLI entrypoint
├── pyproject.toml         # PyPI metadata
└── vscode-extension/      # VS Code/Cursor/Antigravity extension
```

---

## 빠른 시작

### 1. 서버 설치 & 실행

```bash
# PyPI
pip install ltcai

# 로컬 MLX까지 함께 쓰려면
pip install "ltcai[local]"

# npm
npm install -g ltcai

# 서버 실행
LTCAI
# → http://localhost:4825 에서 실행됨
```

개발 중에는 설치 없이도 실행할 수 있습니다.

```bash
python ltcai_cli.py
python ltcai_cli.py --reload
LTCAI doctor
```

`npm install -g ltcai`로 설치한 경우 첫 실행 시 `~/.ltcai/npm-python`에 Python 가상환경을 만들고
`requirements.txt`를 설치합니다. 자동 설치를 끄려면 `LTCAI_SKIP_NPM_BOOTSTRAP=1`을 설정하세요.

Lattice AI stores runtime data in `~/.ltcai/` by default. Override it with
`LATTICEAI_DATA_DIR=/path/to/data` when running `LTCAI`.

### 2. 첫 모델 로드 (터미널 or 확장 프로그램에서)

```bash
# 터미널에서 직접
curl -X POST http://localhost:4825/models/load \
  -H "Content-Type: application/json" \
  -d '{"model_id": "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"}'
```

또는 확장 프로그램에서 `Cmd+Shift+M` → 모델 선택

### 3. 확장 프로그램 설치

```bash
cd vscode-extension
npm install
npm run build
npm run package:vsix

# VS Code / Cursor / Antigravity에서:
# 1. Extensions 패널 → "..." → "Install from VSIX" 또는
# 2. 로컬 CLI가 있으면:
npm run install:all
```

---

## 모델/비용 구조

- Local LLM: MLX, Ollama, vLLM, LM Studio, llama.cpp
- Cloud LLM: OpenAI, OpenRouter, Groq, Together, xAI 등 OpenAI-compatible provider
- API 비용: 사용자가 본인 API key를 입력하는 BYOK 구조입니다. 사용자별 키로 호출되므로 키 소유자가 사용량을 부담합니다.
- 초대 링크 게이트는 기본 비활성화되어 있습니다. 다시 켜려면 `LATTICEAI_INVITE_GATE_ENABLED=true`를 설정하세요.

## 보안 기본값

- 기본 서버 바인딩은 `127.0.0.1:4825`입니다. 같은 네트워크에서 접속하게 하려면 명시적으로 `LATTICEAI_HOST=0.0.0.0`을 설정하세요.
- CORS는 기본적으로 localhost만 허용합니다. 네트워크 공개가 필요하면 `LATTICEAI_CORS_ALLOW_NETWORK=true`를 명시적으로 설정하세요.
- 사용자 API key는 OS keyring/Keychain에 저장합니다. keyring을 사용할 수 없는 환경에서 평문 저장을 허용하려면 `LATTICEAI_ALLOW_PLAINTEXT_API_KEYS=true`를 직접 설정해야 합니다.
- 히스토리 저장 전 API key/token/password 패턴은 마스킹됩니다.

## 지원 모델 예시 (M5 32GB 기준)

| 모델 | 용도 | 크기 | 추천도 |
|------|------|------|--------|
| `mlx-community/Qwen2.5-Coder-7B-Instruct-4bit`  | 코딩 | ~4GB  | ⭐⭐⭐ |
| `mlx-community/Qwen2.5-Coder-14B-Instruct-4bit` | 코딩 | ~8GB  | ⭐⭐⭐⭐ |
| `mlx-community/Qwen2.5-Coder-32B-Instruct-4bit` | 코딩 | ~18GB | ⭐⭐⭐⭐⭐ |
| `mlx-community/Llama-3.1-8B-Instruct-4bit`      | 범용 | ~4.5GB| ⭐⭐⭐ |
| `mlx-community/DeepSeek-R1-0528-4bit`           | 추론 | ~38GB | ⭐⭐⭐⭐ |
| `mlx-community/Phi-4-4bit`                      | 코딩 | ~8GB  | ⭐⭐⭐⭐ |
| `mlx-community/gemma-3-27b-it-4bit`             | 범용 | ~15GB | ⭐⭐⭐ |

> **M5 32GB 추천**: Qwen2.5-Coder-32B-Instruct-4bit (18GB) — 32GB에서 여유롭게 동작

---

## 멀티모델 핫스왑

여러 모델을 동시에 메모리에 올려두고 즉시 전환 가능:

```bash
# 모델 A 로드
curl -X POST localhost:4825/models/load -d '{"model_id":"mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"}'

# 모델 B도 함께 로드
curl -X POST localhost:4825/models/load -d '{"model_id":"mlx-community/Llama-3.1-8B-Instruct-4bit"}'

# B → A 즉시 전환 (재로드 없음)
curl -X POST localhost:4825/models/switch/mlx-community%2FQwen2.5-Coder-7B-Instruct-4bit

# 메모리 해제
curl -X DELETE localhost:4825/models/unload/mlx-community%2FLlama-3.1-8B-Instruct-4bit
```

---

## 키보드 단축키

| 단축키 | 기능 |
|--------|------|
| `Cmd+Shift+A` | 채팅 패널 열기 |
| `Cmd+Shift+E` | 선택 코드 편집 (선택 필요) |
| `Cmd+Shift+M` | 모델 로드 / 전환 |
| 우클릭 메뉴 | Explain / Edit / Garden에 저장 |

---

## P-Reinforce 지식 정원사

지식은 `~/.ltcai-ai-brain/`에 자동 분류 저장:

```
~/.ltcai-ai-brain/
├── INDEX.md
├── 00_Raw/       # 원시 데이터, 아이디어
├── 10_Wiki/      # 검증된 개념, 레퍼런스
├── 20_Skills/    # 코드 스니펫, 프롬프트
├── 30_Projects/  # 프로젝트 컨텍스트
└── 40_Log/       # 날짜별 작업 로그
```

사용법: 에디터에서 텍스트 선택 → 우클릭 → **"Save to Knowledge Garden"**

---

## API 엔드포인트

| Method | Path | 설명 |
|--------|------|------|
| GET | `/health` | 서버 상태, 현재 모델 |
| GET | `/models` | 추천 모델 목록 + 로드 상태 |
| POST | `/models/load` | 모델 로드 (캐시 지원) |
| POST | `/models/switch/{id}` | 활성 모델 전환 |
| DELETE | `/models/unload/{id}` | 모델 언로드 |
| POST | `/chat` | 생성 (stream=true/false) |
| POST | `/garden` | P-Reinforce 저장 |
| GET | `/garden/tree` | 지식 트리 조회 |

---

## 자동 시작 설정 (선택)

```bash
# launchd plist로 Mac 부팅시 자동 시작
cat > ~/Library/LaunchAgents/com.ltcai.mlx.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.ltcai.mlx</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/path/to/LTCAI-ai-mlx/server/server.py</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict>
</plist>
EOF

launchctl load ~/Library/LaunchAgents/com.ltcai.mlx.plist
```
