# 🧠 Connect AI MLX

**100% 로컬 AI 코딩 에이전트** — mlx-lm 기반, Apple Silicon 전용  
VS Code · Antigravity · Cursor 전부 지원 | P-Reinforce 지식 정원사 내장

---

## 아키텍처

```
connect-ai-mlx/
├── server/
│   ├── server.py        # FastAPI 브릿지 서버 (포트 4825)
│   ├── llm_router.py    # mlx-lm 멀티모델 핫스왑 코어
│   ├── p_reinforce.py   # 지식 정원사 (마크다운 위키 자동 정리)
│   └── requirements.txt
└── extension/
    ├── package.json
    ├── tsconfig.json
    └── src/
        ├── extension.ts          # 메인 진입점
        ├── client.ts             # HTTP 클라이언트 (streaming 포함)
        ├── commands/
        │   └── modelPicker.ts    # 모델 선택 QuickPick UI
        └── panels/
            └── ChatPanel.ts      # 채팅 Webview UI
```

---

## 빠른 시작

### 1. 서버 설치 & 실행

```bash
# 의존성 설치 (Python 3.11+ 권장)
cd server
pip install -r requirements.txt

# 서버 실행
python server.py
# → http://localhost:4825 에서 실행됨
```

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
cd extension
npm install
npm run compile

# VS Code / Cursor / Antigravity에서:
# 1. Extensions 패널 → "..." → "Install from VSIX" 또는
# 2. F5로 개발 모드 실행
```

---

## 지원 모델 (M5 32GB 기준)

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

지식은 `~/.connect-ai-brain/`에 자동 분류 저장:

```
~/.connect-ai-brain/
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
cat > ~/Library/LaunchAgents/com.connectai.mlx.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.connectai.mlx</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/path/to/connect-ai-mlx/server/server.py</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict>
</plist>
EOF

launchctl load ~/Library/LaunchAgents/com.connectai.mlx.plist
```
