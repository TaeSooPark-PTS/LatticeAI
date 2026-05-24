<div align="center">
  <img src="https://raw.githubusercontent.com/TaeSooPark-PTS/LatticeAI/main/docs/images/logo.svg" alt="Lattice AI" width="280"/>
  <br/>
  <strong>One install. Your personal AI workspace.</strong>
  <br/>
  Local LLMs, cloud models, VS Code / Cursor, Telegram, MCP tools, files, admin controls, and a knowledge graph in one self-hosted stack.
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

## What is Lattice AI?

**Lattice AI** is a self-hosted AI server that unifies local and cloud LLMs into one practical workspace. Install once, then use the same AI from the web UI, VS Code / Cursor, Telegram, MCP clients, files, and your personal knowledge graph.

- 🖥️ **Web UI** — chat, file upload, admin dashboard, data graph
- 🧩 **VS Code / Cursor extension** — edit, explain, generate commands inline
- 📱 **Telegram bot** — access your AI from anywhere
- 🔌 **MCP server** — use Lattice tools inside Claude Desktop / Cursor
- 🔒 **Zero telemetry** — all data stays in `~/.ltcai/` on your machine
- ⚡ **30-second start** — `pip install ltcai` or `npm install -g ltcai`

---

## 📸 Product Preview

Real screens from the local web app:

<table>
<tr>
<td align="center" width="33%">
  <b>💬 Workspace Chat</b><br/>
  <img src="https://raw.githubusercontent.com/TaeSooPark-PTS/LatticeAI/main/docs/images/screenshot-chat.png" alt="Lattice AI workspace chat" width="100%"/>
  <sub>Web chat with local LLM, file upload, pipeline status</sub>
</td>
<td align="center" width="33%">
  <b>🛡️ Admin Dashboard</b><br/>
  <img src="https://raw.githubusercontent.com/TaeSooPark-PTS/LatticeAI/main/docs/images/screenshot-admin.png" alt="Lattice AI admin dashboard" width="100%"/>
  <sub>User management, audit log, security monitoring</sub>
</td>
<td align="center" width="33%">
  <b>🕸️ Knowledge Graph</b><br/>
  <img src="https://raw.githubusercontent.com/TaeSooPark-PTS/LatticeAI/main/docs/images/screenshot-graph.png" alt="Lattice AI knowledge graph" width="100%"/>
  <sub>Auto-built Graph RAG from chats &amp; documents</sub>
</td>
</tr>
</table>

What this gives users after install:

- A single local workspace for chat, files, models, runtime setup, and tool control
- A graph view that turns chats and documents into searchable knowledge
- Admin screens for users, model status, VPC settings, SSO, audit logs, and security monitoring

---

## ⚡ Quick Start (30 seconds)

**Python / PyPI**

```bash
pip install ltcai
pip install "ltcai[local]"
LTCAI doctor
LTCAI
# → http://localhost:4825
```

**Node / npm**

```bash
npm install -g ltcai
LTCAI doctor
LTCAI
```

**VS Code / Cursor**

1. Install **Lattice AI** from [VS Code Marketplace](https://marketplace.visualstudio.com/items?itemName=parktaesoo.ltcai) or [Open VSX](https://open-vsx.org/extension/parktaesoo/ltcai)
2. Start the local server with `LTCAI`
3. Run `Lattice AI: Open Chat` (`Cmd+Shift+A`) in your editor

**First run:** open `http://localhost:4825` → sign up → first account auto-becomes admin → pick a model → start chatting.

**Public HTTPS tunnel (Cloudflare, no account needed):**

```bash
LTCAI --tunnel
# → https://xxxx.trycloudflare.com
```

---

## 🆚 Why Lattice AI?

Comparison is based on public product behavior as of 2026-05.

| | Lattice AI | Open WebUI | Continue.dev | GitHub Copilot |
|---|:---:|:---:|:---:|:---:|
| Local model (offline, Apple Silicon) | ✅ | ✅ | ✅ | ❌ |
| Cloud models (OpenAI, Groq…) | ✅ | ✅ | ✅ | ✅ |
| VS Code extension | ✅ | ❌ | ✅ | ✅ |
| Telegram bot | ✅ | ❌ | ❌ | ❌ |
| Graph RAG (auto knowledge graph) | ✅ | ❌ | ❌ | ❌ |
| MCP registry & install | ✅ | ❌ | ✅ | ❌ |
| Admin dashboard + audit log | ✅ | ✅ | ❌ | ❌ |
| Self-hosted, zero telemetry | ✅ | ✅ | ✅ | ❌ |
| One-command public tunnel | ✅ | ❌ | ❌ | ❌ |
| Free | ✅ | ✅ | ✅ | ❌ |

---

## 🧠 Supported Models

**Local — Apple Silicon only (MLX):**

| Model | Best for | Size |
|-------|----------|------|
| `mlx-community/gemma-4-26b-a4b-it-4bit` | General / coding | ~14 GB |
| `mlx-community/Qwen2.5-Coder-32B-Instruct-4bit` | Coding | ~18 GB |
| `mlx-community/DeepSeek-R1-0528-4bit` | Reasoning | ~38 GB |
| `mlx-community/Phi-4-4bit` | Coding (fast) | ~8 GB |

**Cloud (any platform):**
OpenAI · Groq · Together · OpenRouter · any OpenAI-compatible endpoint

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| **Web UI** | Responsive chat + admin panel + graph visualisation |
| **Auto Setup Wizard** | Detects → downloads → installs → verifies → repairs dependencies |
| **VS Code / Cursor** | Chat panel, Edit Selection, Explain, Generate command |
| **Telegram bot** | Local AI mirror + cloud Codex bot |
| **MCP server** | Use Lattice tools in Claude Desktop / Cursor |
| **MCP registry** | One-click install from registry.modelcontextprotocol.io |
| **Skills marketplace** | 77 official skills (Anthropic + Adobe · Airtable · Auth0 · Pydantic) |
| **Plugin directory** | Browse 149 open-source plugins |
| **Graph RAG** | Chat & docs auto-indexed as SQLite knowledge graph |
| **Multi-step agent** | File edit/create, grep, todo, terminal (25 steps) |
| **Multi-LLM pipeline** | Plan → Execute → Review with different models |
| **Human-in-the-loop** | Approve agent plan before execution |
| **Admin governance** | User status, role permissions, Okta / Entra ID SSO, security monitoring |
| **Audit dashboard** | Per-user AI usage, sensitive data detection, event log, UTF-8 TXT/CSV/Excel exports |
| **PWA** | Install on iPad / Android home screen |
| **SSO** | Entra ID / Okta OIDC |

---

## 🖥️ Platform Support

| Feature | macOS Apple Silicon | macOS Intel / Windows / Linux |
|---------|:---:|:---:|
| Web UI + cloud models | ✅ | ✅ |
| VS Code / Cursor extension | ✅ | ✅ |
| Telegram bot | ✅ | ✅ |
| MLX local models | ✅ | ❌ |
| Ollama / LM Studio / vLLM | ✅ | ✅ |

---

## 🛠️ Setup & Usage

### Install & run

```bash
# Verify everything is ready
LTCAI doctor

# Run with cloud API key
OPENAI_API_KEY=sk-... LTCAI

# Run with local MLX model (Apple Silicon)
LATTICEAI_MODE=local \
LATTICEAI_LOCAL_MODEL=mlx-community/gemma-4-26b-a4b-it-4bit \
LTCAI
```

### VS Code extension

1. VS Code → Extensions → search `ltcai` → Install
2. `Cmd+Shift+A` — open chat panel (auto-connects to `localhost:4825`)

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

### Public server (Render / Fly.io / Docker)

```bash
LATTICEAI_MODE=public \
LATTICEAI_PUBLIC_MODEL=openai:gpt-4o-mini \
OPENAI_API_KEY=sk-... \
LATTICEAI_INVITE_CODE=my-secret \
LTCAI
```

```bash
# Docker
docker build -t lattice-ai .
docker run --rm -p 4825:4825 \
  -e OPENAI_API_KEY="$OPENAI_API_KEY" \
  -e LATTICEAI_INVITE_CODE="my-secret" \
  -v "$PWD/.data:/data" \
  lattice-ai
```

---

## 🔒 Security

| Property | Detail |
|----------|--------|
| Binding | Default `127.0.0.1:4825` — local only |
| Auth | Session required when network-exposed or public mode |
| Cookies | `HttpOnly + SameSite=Lax` — no localStorage token |
| Local file access | Approval-token gated (path + user + action scope) |
| CORS | Localhost only by default; add origins via `LATTICEAI_CORS_ALLOWED_ORIGINS` |
| File upload | Magic-number signature check (blocks extension spoofing) |
| Rate limits | `/chat` 30/min · `/agent` 6/min · `/upload` 12/min per user |
| Telemetry | None — all data in `~/.ltcai/` |

Report vulnerabilities: [SECURITY.md](SECURITY.md)

---

## 🗂️ API Reference

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Server status & current model |
| GET | `/models` | Model list + load state |
| POST | `/models/load` | Load a model |
| POST | `/chat` | Chat (`stream=true/false`) |
| POST | `/agent` | Multi-step file agent |
| GET | `/mcp/installed` | Installed MCP servers |
| POST | `/mcp/install` | Install MCP server |
| GET | `/skills/marketplace` | Skills marketplace |
| POST | `/skills/install` | Install a skill |
| GET | `/plugins/directory` | Plugin directory |
| GET | `/admin/audit` | Admin audit report with per-user usage and recent events |
| GET | `/admin/sensitivity` | Security monitoring report for risky/compliant fields |
| GET/PATCH | `/admin/sso` | Okta / Entra ID OIDC configuration |
| GET | `/permissions/pending` | Pending file-access approvals (admin) |
| POST | `/permissions/approve/{token}` | Approve file access (admin) |

Full reference: [docs/mcp-tools.md](docs/mcp-tools.md)

---

## 🔧 Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Port 4825 in use | Previous process | `lsof -i :4825` → `kill <PID>` or `--port 4826` |
| `ModuleNotFoundError: mlx` | MLX not installed | `pip install "ltcai[local]"` (Apple Silicon only) |
| Python < 3.11 | Version mismatch | Upgrade: `python3 --version` |
| `LTCAI doctor` OPTIONAL | Optional dep missing | Safe to ignore if feature not needed |
| No API key warning | Cloud model not set | `OPENAI_API_KEY=sk-... LTCAI` or set in admin panel |
| Can't reach from iPad | Default bind 127.0.0.1 | `LATTICEAI_HOST=0.0.0.0 LTCAI` or use `--tunnel` |

---

## 🚀 Auto-start (Mac)

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

Or: `./start_ai.sh` (auto-restart + caffeinate)

---

## 📦 Distribution

| Channel | Link |
|---------|------|
| PyPI | [pypi.org/project/ltcai](https://pypi.org/project/ltcai/) |
| npm | [npmjs.com/package/ltcai](https://www.npmjs.com/package/ltcai) |
| VS Code Marketplace | [marketplace.visualstudio.com](https://marketplace.visualstudio.com/items?itemName=parktaesoo.ltcai) |
| Open VSX | [open-vsx.org](https://open-vsx.org/extension/parktaesoo/ltcai) |

Current version: **0.1.30** — [Changelog](docs/CHANGELOG.md)

---

## 🤝 Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). All PRs welcome.

## 📄 License

MIT — [TaeSoo Park](https://github.com/TaeSooPark-PTS)

---

<details>
<summary>한국어 안내 (Korean)</summary>

## 한국어 안내

**Lattice AI**는 로컬/클라우드 LLM을 웹 UI · VS Code 확장 · Telegram 봇 · MCP 도구로 하나의 서버에서 운영하는 개인 AI 워크스페이스입니다.

### 설치

```bash
pip install ltcai                    # 클라우드 모델
pip install "ltcai[local]"           # + Apple Silicon MLX 로컬 모델
LTCAI                                # 서버 실행 → http://localhost:4825
LTCAI --tunnel                       # + Cloudflare 공개 URL 자동 발급
```

### 주요 기능

- 웹 UI 채팅 + 어드민 대시보드 + Data Graph 시각화
- VS Code / Cursor 확장 (`Cmd+Shift+A`)
- Telegram 봇 연동
- MCP 레지스트리 & Skills 마켓플레이스
- Graph RAG — 채팅·문서를 SQLite 지식 그래프로 자동 구조화
- 멀티 LLM 파이프라인 (Plan → Execute → Review)
- Human-in-the-loop 에이전트 승인
- 감사 로그 & 데이터 거버넌스 대시보드, UTF-8 TXT/CSV/Excel 추출
- 사용자/권한/SSO/보안 모니터링이 분리된 관리자 화면
- 텔레메트리 없음 — 모든 데이터 로컬 저장

### 추천 로컬 모델 (M-series Mac)

| 모델 | 용도 | 크기 |
|------|------|------|
| `mlx-community/gemma-4-26b-a4b-it-4bit` | 범용 | ~14GB |
| `mlx-community/Qwen2.5-Coder-32B-Instruct-4bit` | 코딩 | ~18GB |
| `mlx-community/DeepSeek-R1-0528-4bit` | 추론 | ~38GB |

자세한 내용: [docs/CHANGELOG.md](docs/CHANGELOG.md) · [보안](SECURITY.md) · [기여](CONTRIBUTING.md)

</details>
