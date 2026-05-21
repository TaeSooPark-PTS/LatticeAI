# Lattice AI — 아키텍처

## 전체 구조

```
┌─────────────────────────────────────────────────────────┐
│                    클라이언트 레이어                      │
│  웹 UI (chat.html)  │  VS Code 확장  │  Telegram 봇     │
└──────────────────────────┬──────────────────────────────┘
                           │ HTTP / SSE
┌──────────────────────────▼──────────────────────────────┐
│               server.py — FastAPI (port 4825)            │
│                                                          │
│  /chat  /agent  /models  /tools/*  /mcp/*  /garden      │
│  /account  /admin  /auth/sso  /knowledge-graph  /graph   │
└────┬──────────┬──────────┬──────────┬───────────────────┘
     │          │          │          │
     ▼          ▼          ▼          ▼
llm_router  tools.py  knowledge_  p_reinforce
  .py               graph.py      .py
     │
     ├── MLX (mlx_lm / mlx_vlm)   ← Apple Silicon 로컬
     ├── OpenAI SDK                ← openai / groq / together / openrouter
     └── Ollama / vLLM REST        ← 로컬 서버 연동
```

## 파일별 역할

| 파일 | 역할 |
|------|------|
| `server.py` | FastAPI 앱, 모든 HTTP 엔드포인트, 인증/세션/CORS/rate limit |
| `ltcai_cli.py` | CLI 엔트리포인트 (`LTCAI` 명령), `doctor` 서브커맨드, uvicorn 실행 |
| `llm_router.py` | 로컬(MLX/Ollama) ↔ 클라우드(OpenAI/Groq/…) 라우팅, 스트리밍 SSE |
| `tools.py` | 에이전트 도구 구현: read_file, edit_file, grep, run_command, todo_write/read, 스크린샷 등 |
| `knowledge_graph.py` | SQLite 지식 그래프 (노드/엣지/청크), Graph RAG 컨텍스트 주입 |
| `p_reinforce.py` | P-Reinforce 지식 정원 엔진, `~/.ltcai-brain/` 분류 저장 |
| `telegram_bot.py` | 로컬 AI Telegram 미러 봇 |
| `codex_telegram_bot.py` | 클라우드 Codex Telegram 봇 (GPT + GitHub 이슈) |
| `vscode-extension/` | TypeScript VS Code 확장 |
| `static/` | 웹 UI HTML (chat, account, admin, graph), PWA manifest/SW |
| `bin/ltcai.js` | npm CLI 엔트리포인트 (Python 환경 자동 부트스트랩) |

## 데이터 흐름

### 채팅 요청

```
브라우저 → POST /chat
  → server.py: 인증 확인, rate limit
  → llm_router.py: 모델 선택 (로컬/클라우드)
  → knowledge_graph.py: Graph RAG 컨텍스트 조회 + 주입
  → LLM 스트리밍 응답 (SSE)
  → knowledge_graph.py: 메시지/응답 인제스트
```

### 에이전트 요청

```
브라우저/VS Code → POST /agent
  → server.py: 인증 확인, rate limit (6/분)
  → llm_router.py: Discover→Plan→Implement→Verify 루프 (max 25스텝)
  → tools.py: read_file / edit_file / grep / run_command / todo_*
  → 각 스텝 결과 스트리밍
```

### 문서 업로드

```
브라우저 → POST /upload
  → server.py: magic-number 검증, rate limit (12/분)
  → tools.py: PDF/DOCX/XLSX/PPTX 파싱
  → knowledge_graph.py: Chunk/Page/Sheet/Slide 노드 인제스트
  → blob 저장: ~/.ltcai/knowledge_graph_blobs/
```

## 데이터 저장소

```
~/.ltcai/
├── users.json                   # 사용자 계정 (scrypt 해시)
├── sessions.json                # 세션 토큰 (24h TTL)
├── chat_history.json            # 채팅 히스토리
├── knowledge_graph.sqlite       # Graph RAG SQLite DB
├── knowledge_graph_blobs/       # 원본 업로드 파일
├── mcp_installs.json            # MCP 서버 설치 목록
└── todos.json                   # 에이전트 TODO 리스트

~/.ltcai-brain/
├── INDEX.md
├── 00_Raw/
├── 10_Wiki/
├── 20_Skills/
├── 30_Projects/
└── 40_Log/
```

## 인증 흐름

```
POST /login (username + password)
  → scrypt 검증
  → 세션 토큰 생성 (UUID, 24h TTL)
  → Set-Cookie: session=<token>; HttpOnly; SameSite=Lax

모든 민감 엔드포인트:
  → _require_auth(): 쿠키 검증 → User 반환 또는 401
```

SSO (OIDC):

```
GET /auth/sso/login → 리디렉션 (Entra ID / Okta)
GET /auth/sso/callback?code=... → 토큰 교환 → 세션 생성
```

## MCP 연동

`/mcp/tools` — 에이전트 도구 카탈로그를 MCP 형식으로 노출  
Claude Desktop / Cursor의 MCP 설정에 `http://localhost:4825/mcp` 추가 시 직접 도구 사용 가능.

자세한 내용: [mcp-tools.md](mcp-tools.md)
