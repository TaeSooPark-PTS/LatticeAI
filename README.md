<div align="center">
  <img src="https://raw.githubusercontent.com/TaeSooPark-PTS/LatticeAI/main/docs/images/logo.svg" alt="Lattice AI" width="280"/>
  <br/>
  <strong>Private local AI workspace that turns your files, chats, and folders into a searchable knowledge graph.</strong>
  <br/><br/>

[![PyPI](https://img.shields.io/pypi/v/ltcai?label=PyPI&color=blue)](https://pypi.org/project/ltcai/)
[![PyPI Downloads](https://img.shields.io/pypi/dm/ltcai?label=PyPI%20downloads)](https://pypi.org/project/ltcai/)
[![npm](https://img.shields.io/npm/v/ltcai?label=npm)](https://www.npmjs.com/package/ltcai)
[![VS Code](https://vsmarketplacebadges.dev/version-short/parktaesoo.ltcai.svg)](https://marketplace.visualstudio.com/items?itemName=parktaesoo.ltcai)
[![Open VSX](https://img.shields.io/open-vsx/v/parktaesoo/ltcai?label=Open%20VSX)](https://open-vsx.org/extension/parktaesoo/ltcai)
[![CI](https://github.com/TaeSooPark-PTS/LatticeAI/actions/workflows/ci.yml/badge.svg)](https://github.com/TaeSooPark-PTS/LatticeAI/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green)](./LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue)](https://www.python.org/)

<br/>

<img src="https://raw.githubusercontent.com/TaeSooPark-PTS/LatticeAI/main/docs/images/lattice-ai-demo.gif" alt="Lattice AI demo showing chat, knowledge graph, and admin dashboard" width="100%"/>

</div>

---

## Why Lattice AI?

Most AI tools forget everything after each conversation. Your files sit in folders, your chats vanish, and nothing connects.

**Lattice AI remembers.** It reads your local files, indexes your conversations, and builds a knowledge graph that links people, projects, concepts, and documents — all on your machine, with zero data leaving your PC.

- **Your data stays local** — everything lives in `~/.ltcai/`, never sent to external servers
- **Your AI gets smarter over time** — every chat and file builds your personal knowledge graph
- **One install, works everywhere** — web UI, VS Code, Telegram, MCP clients, all connected to the same brain

---

## 3-Minute Workflow

```
1. Install             pip install ltcai && LTCAI
2. Detect hardware     Auto-detect CPU, GPU, RAM → recommend the best local model
3. Connect folders     Select local folders to index into your knowledge graph
4. Build knowledge     Files and chats auto-analyzed → nodes (people, concepts, files) + edges (mentions, contains, depends on)
5. Ask anything        "What did I discuss about the auth migration last week?" → Graph RAG retrieves context
6. Work from anywhere  Web UI · VS Code · Telegram · MCP — all connected to the same knowledge
```

---

## Product Preview

<table>
<tr>
<td align="center" width="33%">
  <b>Workspace Chat</b><br/>
  <img src="https://raw.githubusercontent.com/TaeSooPark-PTS/LatticeAI/main/docs/images/screenshot-chat.png" alt="Lattice AI workspace chat" width="100%"/>
  <sub>Chat with local/cloud LLM, upload files, pipeline controls</sub>
</td>
<td align="center" width="33%">
  <b>Knowledge Graph</b><br/>
  <img src="https://raw.githubusercontent.com/TaeSooPark-PTS/LatticeAI/main/docs/images/screenshot-graph.png" alt="Lattice AI knowledge graph" width="100%"/>
  <sub>Auto-built from chats and documents — nodes = nouns, edges = verbs</sub>
</td>
<td align="center" width="33%">
  <b>Admin Dashboard</b><br/>
  <img src="https://raw.githubusercontent.com/TaeSooPark-PTS/LatticeAI/main/docs/images/screenshot-admin.png" alt="Lattice AI admin dashboard" width="100%"/>
  <sub>User management, audit log, security monitoring</sub>
</td>
</tr>
</table>

---

## Quick Start

**Python / PyPI**

```bash
pip install ltcai
pip install "ltcai[local]"   # + Apple Silicon MLX models
LTCAI
# → http://localhost:4825
```

**Node / npm**

```bash
npm install -g ltcai
LTCAI
```

**VS Code / Cursor**

1. Install **Lattice AI** from [VS Code Marketplace](https://marketplace.visualstudio.com/items?itemName=parktaesoo.ltcai) or [Open VSX](https://open-vsx.org/extension/parktaesoo/ltcai)
2. Start the local server: `LTCAI`
3. `Cmd+Shift+A` to open the chat panel

**First run:** open `http://localhost:4825` → sign up → first account auto-becomes admin → pick a model → start chatting.

---

## How the Knowledge Graph Works

Lattice AI automatically analyzes your chats and files, extracting meaningful structure:

**Nodes (nouns)** — the things in your world:
| Type | Examples |
|------|----------|
| Document | PDF, PPTX, DOCX, code files, images |
| Person | You, mentioned colleagues |
| Concept | Technologies, frameworks, ideas |
| Chat | Conversation sessions |
| Task | Action items, TODOs |
| Decision | Choices made in discussions |

**Edges (verbs)** — how things relate:
`mentions` · `contains` · `resolves` · `depends on` · `explains` · `uses` · `replaces` · `supports` · `related to`

**Local folder indexing:**
1. Browse your drives and folders from the UI
2. Preview file counts, types, and sizes before indexing
3. Approve which folders to connect (sensitive files auto-excluded)
4. Files are parsed, chunked, and linked into the graph
5. Optional: enable file watcher for real-time updates

All data stays in a local SQLite database. Nothing leaves your machine.

---

## Comparison

Based on public product behavior as of 2026-05.

| | Lattice AI | Open WebUI | Continue.dev | GitHub Copilot |
|---|:---:|:---:|:---:|:---:|
| Local model (offline, Apple Silicon) | **Yes** | Yes | Yes | No |
| Cloud models (OpenAI, Groq...) | **Yes** | Yes | Yes | Yes |
| Knowledge graph (auto from files + chats) | **Yes** | No | No | No |
| Local folder indexing + file watcher | **Yes** | No | No | No |
| VS Code extension | **Yes** | No | Yes | Yes |
| Telegram bot | **Yes** | No | No | No |
| MCP registry (one-click install) | **Yes** | Partial | Yes | No |
| Admin + audit log | **Yes** | Yes | No | No |
| Zero telemetry, self-hosted | **Yes** | Yes | Yes | No |
| One-command public tunnel | **Yes** | No | No | No |
| Free | **Yes** | Yes | Yes | No |

---

## Supported Models

**Local (Apple Silicon MLX):**

| Model | Best for | Size | Min RAM |
|-------|----------|------|---------|
| Qwen3-VL 4B | Multimodal / low spec | ~2.7 GB | 8 GB |
| Qwen3-VL 8B | Multimodal / balanced | ~4.8 GB | 16 GB |
| Gemma 4 26B | Multimodal / large | ~15.6 GB | 32 GB |
| Qwen3-VL 30B A3B | Multimodal / top | ~18 GB | 48 GB |
| Phi 4 Mini | Coding (fast) | ~2.2 GB | 8 GB |
| Llama 3.1 8B | General | ~4.7 GB | 8 GB |
| Mistral 7B v0.3 | General / Apache | ~4.1 GB | 8 GB |

**Cross-platform (Ollama / LM Studio / vLLM / llama.cpp):**
Same models via Ollama pull, LM Studio download, or vLLM serve.

**Cloud (any platform):**
OpenAI GPT-5.5 · Claude Opus 4.7 / Sonnet 4.6 / Haiku 4.5 via OpenRouter · Groq · Together · xAI · any OpenAI-compatible endpoint

The setup wizard auto-detects your hardware and recommends the best model for your specs.

---

## Data Privacy

| | |
|---|---|
| **Storage** | All data in `~/.ltcai/` on your machine |
| **Telemetry** | None — no analytics, no tracking, no phoning home |
| **File access** | Approval-token gated — explicit consent per folder |
| **Cloud models** | When using cloud APIs, prompts are sent to the provider. Local models keep everything offline. |
| **Sensitive files** | `.env`, credentials, keys, certificates auto-excluded from indexing |
| **Delete** | Clear chat history, delete graph nodes, remove indexed folders at any time |

---

<details>
<summary><b>All Features</b></summary>

### Core Experience
| Feature | Description |
|---------|-------------|
| **Web UI** | Responsive chat, file upload, model picker, knowledge graph |
| **Auto Setup Wizard** | Detect hardware → recommend model → install dependencies → verify |
| **Graph RAG** | Chats and files auto-indexed into SQLite knowledge graph |
| **Local folder indexing** | Browse, audit, and index local folders with file watcher |

### Developer Tools
| Feature | Description |
|---------|-------------|
| **VS Code / Cursor** | Chat panel, Edit Selection, Explain, Generate command |
| **Multi-step agent** | File edit/create, grep, todo, terminal (25 steps, human-in-the-loop) |
| **Multi-LLM pipeline** | Plan → Execute → Review with different models |
| **MCP server** | Use Lattice tools in Claude Desktop / Cursor |
| **MCP registry** | One-click install from registry.modelcontextprotocol.io |
| **Skills marketplace** | 77 official skills (Anthropic + verified third-party) |
| **Plugin directory** | Browse 149 open-source plugins |

### Access & Communication
| Feature | Description |
|---------|-------------|
| **Telegram bot** | Chat, upload files, manage models from anywhere |
| **PWA** | Install on iPad / Android home screen |
| **Public tunnel** | `LTCAI --tunnel` — Cloudflare HTTPS, no account needed |

### Administration
| Feature | Description |
|---------|-------------|
| **User management** | Roles, permissions, disable/enable accounts |
| **SSO** | Entra ID / Okta OIDC |
| **Audit dashboard** | Per-user AI usage, sensitive data detection, event log, TXT/CSV/Excel export |
| **Security monitoring** | Rate limits, file access approvals, MCP install audit trail |

</details>

<details>
<summary><b>Security</b></summary>

| Property | Detail |
|----------|--------|
| Binding | Default `127.0.0.1:4825` — local only |
| Auth | Session required when network-exposed or public mode |
| Cookies | `HttpOnly + SameSite=Lax` — no localStorage token |
| Local file access | Approval-token gated (path + user + action scope) |
| Package install | Admin-only with audit trail (MCP, skills, pip, npm) |
| CORS | Localhost only by default; configure via `LATTICEAI_CORS_ALLOWED_ORIGINS` |
| File upload | Magic-number signature check (blocks extension spoofing) |
| Rate limits | `/chat` 30/min · `/agent` 6/min · `/upload` 12/min per user |
| Telemetry | None — all data in `~/.ltcai/` |

Report vulnerabilities: [SECURITY.md](SECURITY.md)

</details>

<details>
<summary><b>Setup & Configuration</b></summary>

### VS Code shortcuts

| Shortcut | Action |
|----------|--------|
| `Cmd+Shift+A` | Open chat |
| `Cmd+Shift+E` | Edit selected code |
| `Cmd+Shift+M` | Load / switch model |
| Right-click | Explain / Save to Knowledge Garden |

### Telegram bot

```bash
LATTICEAI_TELEGRAM_BOT_TOKEN=your-token LTCAI
```

### Public server (Docker / Render / Fly.io)

```bash
LATTICEAI_MODE=public \
LATTICEAI_PUBLIC_MODEL=openai:gpt-4o-mini \
OPENAI_API_KEY=sk-... \
LATTICEAI_INVITE_CODE=my-secret \
LTCAI
```

### Public tunnel (Cloudflare, no account)

```bash
LTCAI --tunnel
# → https://xxxx.trycloudflare.com
```

### Auto-start (Mac)

```bash
cat > ~/Library/LaunchAgents/com.ltcai.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.ltcai</string>
  <key>ProgramArguments</key><array><string>/usr/local/bin/LTCAI</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/ltcai.log</string>
  <key>StandardErrorPath</key><string>/tmp/ltcai.err</string>
</dict>
</plist>
EOF
launchctl load ~/Library/LaunchAgents/com.ltcai.plist
```

</details>

<details>
<summary><b>API Reference</b></summary>

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Server status & current model |
| GET | `/models` | Model list + load state |
| POST | `/models/load` | Load a model |
| POST | `/chat` | Chat (`stream=true/false`) |
| POST | `/agent` | Multi-step file agent |
| GET | `/knowledge-graph/stats` | Graph statistics |
| GET | `/knowledge-graph/search?q=` | Search the knowledge graph |
| GET | `/knowledge-graph/local/roots` | Discover local drives & folders |
| POST | `/knowledge-graph/local/audit` | Audit a folder before indexing |
| POST | `/knowledge-graph/local/index` | Index a folder into Graph RAG |
| GET | `/mcp/installed` | Installed MCP servers |
| POST | `/mcp/install` | Install MCP server (admin) |
| GET | `/skills/marketplace` | Skills marketplace |
| POST | `/skills/install` | Install a skill (admin) |
| GET | `/admin/audit` | Audit report |
| GET | `/permissions/pending` | Pending file-access approvals |

Full reference: [docs/mcp-tools.md](docs/mcp-tools.md)

</details>

<details>
<summary><b>Troubleshooting</b></summary>

| Symptom | Fix |
|---------|-----|
| Port 4825 in use | `lsof -i :4825` → `kill <PID>` or `LTCAI --port 4826` |
| `ModuleNotFoundError: mlx` | `pip install "ltcai[local]"` (Apple Silicon only) |
| Python < 3.11 | Upgrade Python: `python3 --version` |
| No API key warning | `OPENAI_API_KEY=sk-... LTCAI` or set in admin panel |
| Can't reach from iPad | `LATTICEAI_HOST=0.0.0.0 LTCAI` or use `--tunnel` |

</details>

---

## Platform Support

| Feature | macOS Apple Silicon | macOS Intel / Windows / Linux |
|---------|:---:|:---:|
| Web UI + cloud models | Yes | Yes |
| VS Code / Cursor extension | Yes | Yes |
| Telegram bot | Yes | Yes |
| MLX local models | Yes | -- |
| Ollama / LM Studio / vLLM | Yes | Yes |

---

## Distribution

| Channel | Link |
|---------|------|
| PyPI | [pypi.org/project/ltcai](https://pypi.org/project/ltcai/) |
| npm | [npmjs.com/package/ltcai](https://www.npmjs.com/package/ltcai) |
| VS Code Marketplace | [marketplace.visualstudio.com](https://marketplace.visualstudio.com/items?itemName=parktaesoo.ltcai) |
| Open VSX | [open-vsx.org](https://open-vsx.org/extension/parktaesoo/ltcai) |

Current version: **0.2.0** — [Changelog](docs/CHANGELOG.md)

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). All PRs welcome.

## License

MIT — [TaeSoo Park](https://github.com/TaeSooPark-PTS)

---

<details>
<summary>한국어 안내 (Korean)</summary>

## Lattice AI

**내 PC의 파일, 대화, 프로젝트를 기억하고 연결하는 로컬 AI 워크스페이스**

대부분의 AI 도구는 대화가 끝나면 모든 것을 잊습니다. Lattice AI는 다릅니다. 로컬 파일을 읽고, 대화를 기록하고, 사람·프로젝트·개념·문서를 연결하는 지식 그래프를 자동으로 만듭니다. 모든 데이터는 내 PC에만 저장됩니다.

### 3분 사용 흐름

```
1. 설치          pip install ltcai && LTCAI
2. 하드웨어 감지   CPU, GPU, RAM 자동 감지 → 최적 로컬 모델 추천
3. 폴더 연결      로컬 폴더를 선택하여 지식 그래프에 연결
4. 지식 구축      파일과 대화 자동 분석 → 점(사람, 개념, 파일) + 선(언급함, 포함함, 의존함)
5. 질문           "지난주 인증 마이그레이션 논의 내용은?" → Graph RAG가 컨텍스트 검색
6. 어디서든 작업   웹 UI · VS Code · Telegram · MCP — 같은 지식에 연결
```

### 설치

```bash
pip install ltcai                    # 클라우드 모델
pip install "ltcai[local]"           # + Apple Silicon MLX 로컬 모델
LTCAI                                # 서버 실행 → http://localhost:4825
LTCAI --tunnel                       # + Cloudflare 공개 URL 자동 발급
```

### 핵심 차별점

- **내 데이터가 AI의 기억이 된다** — 채팅과 파일이 자동으로 지식 그래프로 구조화
- **로컬 폴더를 연결하면 프로젝트 전체를 이해** — 파일 변경 시 실시간 업데이트
- **모든 데이터는 내 PC에** — `~/.ltcai/`에 저장, 텔레메트리 없음, 외부 전송 없음
- **설치 한 번으로 어디서든** — 웹 · VS Code · Telegram · MCP 클라이언트

### 추천 로컬 모델 (M-series Mac)

| 모델 | 용도 | 크기 | 최소 RAM |
|------|------|------|----------|
| Qwen3-VL 4B | 멀티모달 / 저사양 | ~2.7GB | 8GB |
| Qwen3-VL 8B | 멀티모달 / 균형 추천 | ~4.8GB | 16GB |
| Gemma 4 26B | 멀티모달 / 대형 | ~15.6GB | 32GB |
| Qwen3-VL 30B A3B | 멀티모달 / 최고급 | ~18GB | 48GB |

자세한 내용: [docs/CHANGELOG.md](docs/CHANGELOG.md) · [보안](SECURITY.md) · [기여](CONTRIBUTING.md)

</details>
