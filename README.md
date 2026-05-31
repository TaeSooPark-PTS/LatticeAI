<div align="center">
  <img src="https://raw.githubusercontent.com/TaeSooPark-PTS/LatticeAI/main/docs/images/logo.svg" alt="Lattice AI" width="280"/>
  <br/>
  <strong>Lattice AI — Local-first Agentic Workspace Platform: plugins, visual workflows, multi-agent runs, and realtime activity over your graph, memory, skills, and timeline.</strong>
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

  <img src="docs/images/hero.gif" alt="Lattice AI — AI Workspace OS for local-first graph, memory, and agents" width="100%"/>
</div>

---

## What is Lattice AI?

Most AI tools answer one chat at a time. They do not remember your folders, your project history, your previous decisions, or how your files relate to each other.

**Lattice AI turns your local workspace into a Local-first Agentic Workspace Platform.**

It reads approved local folders, indexes chats and documents, builds a searchable knowledge graph, and connects the graph to snapshots, personal memory, agent runs, workflow history, skills, and an auditable timeline.

```text
Local files + chats + folders
          ↓
Automatic knowledge graph
          ↓
Graph-aware chat, snapshots, memory, agents, workflows, skills, and timeline
```

## Why Lattice AI?

- **Local-first by default** — models, data, and your knowledge graph stay on your machine (`~/.ltcai/`); cloud is strictly opt-in.
- **Memory that compounds** — every chat, file, and folder you approve becomes durable, searchable context instead of being forgotten.
- **A graph, not a pile of files** — people, projects, documents, decisions, and tasks are linked automatically and explored visually.
- **One workspace, everywhere** — the same local knowledge powers the web UI, VS Code / Cursor, Telegram, and MCP clients.
- **Built-in governance** — Personal and Organization workspaces, roles, an audit timeline, and sensitive-data monitoring for teams.

## Core Capabilities

| Capability | What it does |
|---|---|
| 🧠 Automatic knowledge graph | Turns chats, files, and folders into linked nodes and edges, curated automatically |
| 💬 Graph-aware chat & agents | Answers and multi-step agents grounded in your indexed local memory |
| 🖥️ Local model recommendation | Scans your hardware and rates each model **Recommended / Compatible / Not Recommended** |
| 🗂️ Workspaces & roles | Personal and Organization workspaces with owner / admin / member / viewer permissions |
| 🧩 Skills & MCP | Install skills and connect MCP tools from the in-product marketplace |
| 🔒 Admin & security | Audit timeline, permission approvals, sensitive-data detection, exportable reports |

<div align="center">
  <img src="docs/images/onboarding.png" alt="Onboarding flow: install, system scan, model recommendation, workspace, indexing, knowledge graph, first chat" width="100%"/>
</div>

---

## Quick Start

### Python / PyPI

```bash
pip install ltcai
LTCAI
# open http://localhost:4825
```

### Apple Silicon local models

```bash
pip install "ltcai[local]"
LTCAI
```

### Node / npm

```bash
npm install -g ltcai
LTCAI
```

### VS Code / Cursor

1. Install **Lattice AI** from the [VS Code Marketplace](https://marketplace.visualstudio.com/items?itemName=parktaesoo.ltcai) or [Open VSX](https://open-vsx.org/extension/parktaesoo/ltcai)
2. Start the local server with `LTCAI`
3. Press `Cmd+Shift+A` to open the chat panel

**First run:** create an account -> the first account becomes admin -> open `/workspace` -> complete onboarding -> choose a model -> connect folders -> start asking questions.

---

## The 3-minute workflow

```text
1. Install
   pip install ltcai && LTCAI

2. Detect hardware
   CPU, GPU, RAM are detected and a suitable local model is recommended.

3. Connect folders
   Pick the local folders you want Lattice AI to index.

4. Build knowledge
   Files and chats become nodes and edges in a local knowledge graph.

5. Ask questions
   “What did I decide about the auth migration last week?”

6. Keep working
   Use the same local knowledge from the web UI, VS Code, Telegram, or MCP clients.
```

---

## Architecture

`server:app` stays a thin compatibility entrypoint; the FastAPI app is assembled in
`latticeai/server_app.py`, and the work lives in focused API routers, a service
layer, and core modules — so the app shell never grows monolithic again.

<div align="center">
  <img src="docs/images/architecture.png" alt="Lattice AI architecture — entrypoint, API routers, services, core, local engines and knowledge graph" width="100%"/>
</div>

See [docs/architecture.md](docs/architecture.md) for request and data-flow detail.

---

## Product Preview

<table>
<tr>
<td align="center" width="33%">
  <b>Workspace Chat</b><br/>
  <img src="https://raw.githubusercontent.com/TaeSooPark-PTS/LatticeAI/main/docs/images/screenshot-chat.png" alt="Lattice AI workspace chat" width="100%"/>
  <sub>Chat with local/cloud models, upload files, and control pipelines.</sub>
</td>
<td align="center" width="33%">
  <b>Knowledge Graph</b><br/>
  <img src="https://raw.githubusercontent.com/TaeSooPark-PTS/LatticeAI/main/docs/images/screenshot-graph.png" alt="Lattice AI knowledge graph" width="100%"/>
  <sub>Automatically built from chats, files, folders, and project context.</sub>
</td>
<td align="center" width="33%">
  <b>Admin Dashboard</b><br/>
  <img src="https://raw.githubusercontent.com/TaeSooPark-PTS/LatticeAI/main/docs/images/screenshot-admin.png" alt="Lattice AI admin dashboard" width="100%"/>
  <sub>User management, audit logs, permissions, and security monitoring.</sub>
</td>
</tr>
</table>

> Every image in this section is a **real screenshot** of the running app
> (Lattice AI v2.0.0), captured with a headless browser.

---

## Product Experience

### Onboard in minutes

A first run detects your OS, CPU, GPU, RAM, and disk, then recommends a local
model and rates every option **Recommended**, **Compatible**, or **Not
Recommended** for your machine — grouped by family (Gemma, Qwen, Llama, Phi,
DeepSeek, and more), with estimated RAM and a clear next step.

<div align="center">
  <img src="docs/images/onboarding.png" alt="Onboarding hardware scan: OS, CPU, GPU, RAM, disk, runtime" width="49%"/>
  <img src="docs/images/model-recommendation.png" alt="Local model recommendation with best-pick callout and per-family status" width="49%"/>
</div>

### Workspaces & organization

A **Current Workspace** card shows exactly where you are; switch instantly
between a **Personal** workspace and shared **Organization** workspaces. Org data
is scoped by `workspace_id`, and `owner / admin / member / viewer` roles map to a
transparent permission matrix with member management. A Workspace Health panel
summarizes indexed files, graph size, installed skills, memories, agent runs,
current model, last sync time, and status at a glance.

<div align="center">
  <img src="docs/images/workspace.png" alt="Current Workspace summary card with scoped counts" width="100%"/>
  <img src="docs/images/organization.png" alt="Organization workspace with members and roles" width="100%"/>
</div>

### Knowledge graph explorer

Your work becomes a typed knowledge graph automatically. The Entity Explorer
surfaces the most important entities and, on selection, their inbound/outbound
relationships, related entities, and a path back to you.

<div align="center">
  <img src="docs/images/graph.png" alt="Knowledge graph entity explorer with relationship detail" width="100%"/>
</div>

The Graph Canvas also supports node expand/collapse, focused subgraphs,
relationship highlighting, shortest-path visualization, and direct navigation
back into source conversations or files.

### Skills & editions

Browse and install skills from an in-product marketplace; an honest editions
panel shows that every Enterprise capability is an opt-in extension point,
disabled in the open-source Community build.

<div align="center">
  <img src="docs/images/skills.png" alt="Skill marketplace tabs: recommended, popular, installed, updates" width="49%"/>
  <img src="docs/images/enterprise.png" alt="Enterprise capability status panel — all disabled in Community" width="49%"/>
</div>

---

## Why it is different

| Problem | Lattice AI approach |
|---|---|
| AI forgets every conversation | Chats and files are indexed into persistent local memory |
| Files are scattered across folders | Approved folders become searchable graph context |
| Local model setup is confusing | Hardware detection recommends and loads a suitable model |
| Graph tools require manual node editing | Nodes and edges are created automatically from real work |
| Cloud AI may expose private data | Local models keep data on your machine; cloud is opt-in |
| Teams need visibility | Admin dashboard, audit logs, role controls, and sensitive-data monitoring |

---

## Core Features

### Local-first AI workspace

- Web UI running from a local server
- Local SQLite storage under `~/.ltcai/`
- Local folder indexing with explicit approval
- File upload, chat history, graph search, and document generation
- Optional cloud providers when you choose to use them

### Automatic knowledge graph

Lattice AI turns your work into structure automatically.

**Nodes** can represent:

| Node type | Examples |
|---|---|
| Document | PDF, DOCX, PPTX, XLSX, Markdown, code files |
| Concept | technologies, project names, ideas, architecture topics |
| Person | you, teammates, mentioned people |
| Chat | previous conversations and sessions |
| Task | TODOs, action items, follow-ups |
| Decision | choices made during discussions |

**Edges** describe relationships such as:

`mentions` · `contains` · `depends on` · `explains` · `uses` · `replaces` · `supports` · `related to`

The graph is curated automatically: noisy tokens, file extensions, generic words, and hard secrets are filtered before promotion.

### Model loading that users can trust

Lattice AI keeps model identity consistent across recommendation, download, load, backend router state, and frontend display.

- unified model resolution
- local model smoke test after load
- `ok` / `degraded` / `failed` compatibility status
- per-family compatibility profiles for GPT-OSS, Gemma, Qwen, Llama, Mistral, Phi, Deepseek, and more
- fast post-processing path during normal chat
- recovery path only when output looks broken

### Admin and security command center

For team or organization usage, Lattice AI includes admin-facing controls:

- user management and roles
- permission approvals for local file access
- audit event timeline
- sensitive chat/file detection
- risk overview by user
- raw data explorer with hard-secret redaction
- export to JSON, CSV, XLSX, TXT, or PDF

Hard secrets such as API keys, tokens, passwords, private keys, and common cloud credentials are redacted from security responses.

---

## Supported Models

### Local on Apple Silicon MLX

| Model | Best for | Approx. size | Suggested RAM |
|---|---|---:|---:|
| Qwen3-VL 4B | Multimodal / low spec | ~2.7 GB | 8 GB |
| Qwen3-VL 8B | Multimodal / balanced | ~4.8 GB | 16 GB |
| GPT-OSS 20B | Reasoning / open-weight | ~12.1 GB | 32 GB |
| Gemma 4 26B | Multimodal / large | ~15.6 GB | 32 GB |
| Gemma 4 31B | Multimodal / latest Gemma 4 | ~18.4 GB | 48 GB |
| Qwen3-VL 30B A3B | Multimodal / top local | ~18 GB | 48 GB |
| GPT-OSS 120B | Large reasoning model | ~62.3 GB | 128 GB |
| Phi 4 Mini | Fast coding/general chat | ~2.2 GB | 8 GB |
| Llama 3.1 8B | General chat | ~4.7 GB | 8 GB |
| Mistral 7B v0.3 | General / Apache | ~4.1 GB | 8 GB |

### Cross-platform engines

Lattice AI can also work with models served by:

- Ollama
- LM Studio
- llama.cpp
- vLLM
- OpenAI-compatible local or remote endpoints

### Cloud providers

Cloud models are optional. When enabled, prompts are sent to the selected provider.

Supported routes include OpenAI-compatible APIs, OpenRouter, Groq, Together, xAI, and other compatible endpoints.

---

## Privacy and data storage

| Area | Default behavior |
|---|---|
| Storage | Data is stored locally under `~/.ltcai/` |
| Default binding | `127.0.0.1:4825` local server |
| Telemetry | No built-in product telemetry by default |
| Folder access | Explicit approval per folder/action scope |
| Sensitive files | `.env`, credentials, keys, certificates, and similar files are auto-excluded |
| Cloud models | Off unless configured; cloud prompts go to the selected provider |
| Delete controls | Remove chats, graph nodes, indexed folders, and local data |

---

## Comparison

| Capability | Lattice AI | Open WebUI | Continue.dev | GitHub Copilot |
|---|:---:|:---:|:---:|:---:|
| Local model workflow | Yes | Yes | Yes | No |
| Local folder indexing | Yes | Limited | Workspace-focused | Limited |
| Automatic knowledge graph | Yes | No | No | No |
| Chat + file memory | Yes | Partial | Partial | Partial |
| VS Code / Cursor extension | Yes | No | Yes | Yes |
| Admin dashboard | Yes | Yes | No | No |
| Security audit exports | Yes | Limited | No | No |
| Optional cloud models | Yes | Yes | Yes | Yes |
| Local-first by default | Yes | Self-hosted | Local/dev focused | No |

---

## Current release

**2.0.0 — Agentic Workspace Platform.** Lattice AI graduates from a local-first
AI *workspace* to a local-first **Agentic Workspace Platform**: four new
subsystems land as one cohesive, fully integrated platform — all additive, with
every v1.x surface preserved.

- **Plugin SDK** — versioned, permissioned plugins (`plugin.json` manifest,
  allow-listed permissions, lifecycle, validation, and a safe execution
  boundary) that **extend** existing skills/tools/workflows rather than replace
  them. Ships two example plugins. See [docs/PLUGIN_SDK.md](docs/PLUGIN_SDK.md).
- **Workflow Designer** — node-based workflows (trigger · tool · skill · plugin ·
  agent · condition · output) with validation, execution, run history, and
  export/import. Pre-2.0 workflow history keeps working.
  See [docs/WORKFLOW_DESIGNER.md](docs/WORKFLOW_DESIGNER.md).
- **Multi-Agent Runtime 2.0** — Planner · Executor · Reviewer · Researcher ·
  Release roles with handoff, retry, and an observable timeline, connected to
  workspace, memory, graph, and workflow runs.
  See [docs/MULTI_AGENT_RUNTIME.md](docs/MULTI_AGENT_RUNTIME.md).
- **Realtime Collaboration** — presence + an activity feed over SSE, fed
  automatically from every workspace timeline event; single-user local mode and
  workspace isolation preserved. See [docs/REALTIME_COLLABORATION.md](docs/REALTIME_COLLABORATION.md).
- **Cross-system integration** — workflows run plugins/skills/agents, agent runs
  run plugins/workflows, and all activity surfaces in the realtime feed, the
  knowledge graph, and the workspace timeline. See [docs/V2_ARCHITECTURE.md](docs/V2_ARCHITECTURE.md).
- **Compatibility preserved** — API schemas, `server:app`,
  `latticeai.server_app.app`, CLI, MCP, model, workspace, chat, KG, existing
  skills/snapshots/memories/agent history, and the VS Code extension remain
  backward compatible. Changes are additive; no destructive migrations.

| Version | Theme |
|---|---|
| **2.0.0** | Agentic Workspace Platform (Plugin SDK, Workflow Designer, Multi-Agent Runtime, Realtime) |
| 1.7.0 | Graph & Collaboration Release |
| 1.6.0 | Product Experience Deepening (UX + real screenshots) |
| 1.5.0 | Unified Product Release (CI/VSIX recovery, model recommendation, Enterprise PoC) |
| 1.4.0 | Server App final decomposition |
| 1.1.0–1.3.0 | Organization workspaces, modularization, route safety net |

See the full [changelog](docs/CHANGELOG.md) and [RELEASE.md](RELEASE.md).

---

<details>
<summary><b>All Features</b></summary>

### Core experience

| Feature | Description |
|---|---|
| Web UI | Chat, file upload, model picker, graph view, admin pages |
| Auto setup wizard | Detect hardware, recommend model, install dependencies, verify load |
| Graph RAG | Retrieve context from indexed chats, files, and graph relationships |
| Local folder indexing | Browse, audit, approve, index, and optionally watch folders |
| Document generation | Use graph context to generate reports, summaries, and structured drafts |

### Developer tools

| Feature | Description |
|---|---|
| VS Code / Cursor | Chat panel, edit selection, explain code, generate code |
| Multi-step agent | File edit/create, grep, todo, and terminal workflow with human-in-the-loop |
| Multi-LLM pipeline | Plan, execute, and review with different models |
| MCP server | Expose Lattice tools to MCP-compatible clients |
| MCP registry | Install MCP servers from supported registries |
| Skills browser | Browse and install optional skills |
| Plugin browser | Browse compatible open-source plugins |

### Access and communication

| Feature | Description |
|---|---|
| Telegram bot | Chat, upload files, and manage models remotely |
| PWA | Install the web UI on mobile/tablet home screens |
| Public tunnel | `LTCAI --tunnel` for a temporary Cloudflare HTTPS URL |

### Administration

| Feature | Description |
|---|---|
| User management | Roles, permissions, account enable/disable |
| SSO | Entra ID / Okta OIDC configuration |
| Audit dashboard | AI usage, sensitive-data events, file access, exports |
| Security monitoring | Rate limits, approval logs, raw explorer, redaction |

</details>

<details>
<summary><b>Security</b></summary>

| Property | Detail |
|---|---|
| Binding | Default `127.0.0.1:4825` local only |
| Auth | Session required when network-exposed or public mode |
| Cookies | `HttpOnly + SameSite=Lax`; no localStorage token |
| Local file access | Approval-token gated by path, user, and action scope |
| Package install | Admin-only with audit trail for MCP, skills, pip, npm |
| CORS | Localhost only by default; configurable via `LATTICEAI_CORS_ALLOWED_ORIGINS` |
| File upload | Magic-number signature checks for extension spoofing defense |
| Rate limits | `/chat` 30/min · `/agent` 6/min · `/upload` 12/min per user |
| Telemetry | No built-in product telemetry by default |

Report vulnerabilities in [SECURITY.md](SECURITY.md).

</details>

<details>
<summary><b>Setup & Configuration</b></summary>

### VS Code shortcuts

| Shortcut | Action |
|---|---|
| `Cmd+Shift+A` | Open chat |
| `Cmd+Shift+E` | Edit selected code |
| `Cmd+Shift+M` | Load or switch model |
| Right-click | Explain / Save to Knowledge Garden |

### Telegram bot

```bash
LATTICEAI_TELEGRAM_BOT_TOKEN=your-token LTCAI
```

### Public server

```bash
LATTICEAI_MODE=public \
LATTICEAI_PUBLIC_MODEL=openai:gpt-4o-mini \
OPENAI_API_KEY=sk-... \
LATTICEAI_INVITE_CODE=my-secret \
LTCAI
```

### Public tunnel

```bash
LTCAI --tunnel
# → https://xxxx.trycloudflare.com
```

### Auto-start on macOS

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
|---|---|---|
| GET | `/health` | Server status and current model |
| GET | `/models` | Model list and load state |
| POST | `/models/load` | Load a model |
| POST | `/chat` | Chat with streaming or non-streaming output |
| POST | `/agent` | Multi-step file agent |
| GET | `/knowledge-graph/stats` | Graph statistics |
| GET | `/knowledge-graph/search?q=` | Search the knowledge graph |
| GET | `/knowledge-graph/local/roots` | Discover local drives and folders |
| POST | `/knowledge-graph/local/audit` | Audit a folder before indexing |
| POST | `/knowledge-graph/local/index` | Index a folder into Graph RAG |
| GET | `/mcp/installed` | Installed MCP servers |
| POST | `/mcp/install` | Install MCP server as admin |
| GET | `/skills/marketplace` | Skills marketplace |
| POST | `/skills/install` | Install a skill as admin |
| GET | `/admin/audit` | Audit report |
| GET | `/permissions/pending` | Pending file-access approvals |

Full reference: [docs/mcp-tools.md](docs/mcp-tools.md)

</details>

<details>
<summary><b>Troubleshooting</b></summary>

| Symptom | Fix |
|---|---|
| Port 4825 is already in use | `lsof -i :4825` then `kill <PID>`, or run `LTCAI --port 4826` |
| `ModuleNotFoundError: mlx` | Install local extras with `pip install "ltcai[local]"` on Apple Silicon |
| Python version is too old | Use Python 3.11 or newer |
| No API key warning | Set a provider key or use a local model |
| Cannot reach from iPad | Use `LATTICEAI_HOST=0.0.0.0 LTCAI` or `LTCAI --tunnel` |
| Model loads but chat looks broken | Check compatibility status; try another engine or model family |

</details>

---

## Platform Support

| Feature | macOS Apple Silicon | macOS Intel / Windows / Linux |
|---|:---:|:---:|
| Web UI + cloud models | Yes | Yes |
| VS Code / Cursor extension | Yes | Yes |
| Telegram bot | Yes | Yes |
| MLX local models | Yes | No |
| Ollama / LM Studio / vLLM / llama.cpp | Yes | Yes |

---

## Distribution

| Channel | Link |
|---|---|
| PyPI | [pypi.org/project/ltcai](https://pypi.org/project/ltcai/) |
| npm | [npmjs.com/package/ltcai](https://www.npmjs.com/package/ltcai) |
| VS Code Marketplace | [marketplace.visualstudio.com](https://marketplace.visualstudio.com/items?itemName=parktaesoo.ltcai) |
| Open VSX | [open-vsx.org](https://open-vsx.org/extension/parktaesoo/ltcai) |

---

## Documentation

| Doc | What's inside |
|---|---|
| [docs/architecture.md](docs/architecture.md) | App structure, request and data flow |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | Full version history |
| [RELEASE.md](RELEASE.md) | Release notes and the build/publish checklist |
| [SECURITY.md](SECURITY.md) | Security model and vulnerability reporting |
| [docs/ENTERPRISE.md](docs/ENTERPRISE.md) · [docs/EDITION_STRATEGY.md](docs/EDITION_STRATEGY.md) | Open-core boundary and edition strategy |
| [docs/kg-schema.md](docs/kg-schema.md) · [docs/mcp-tools.md](docs/mcp-tools.md) | Knowledge graph schema and MCP tool catalog |
| [docs/privacy.md](docs/privacy.md) · [docs/public-deploy.md](docs/public-deploy.md) · [docs/OPERATIONS.md](docs/OPERATIONS.md) | Privacy, public deployment, operations |

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Issues and pull requests are welcome.

## License

MIT — [TaeSoo Park](https://github.com/TaeSooPark-PTS)

---

<details>
<summary>한국어 안내 (Korean)</summary>

## Lattice AI

**내 PC의 파일, 대화, 폴더를 기억하고 연결하는 로컬 우선 AI 워크스페이스**

대부분의 AI 도구는 대화가 끝나면 맥락을 잊습니다. Lattice AI는 승인한 로컬 폴더와 대화를 인덱싱하고, 사람·프로젝트·개념·문서를 자동으로 지식 그래프로 연결합니다.

```text
로컬 파일 + 대화 + 폴더
        ↓
자동 지식 그래프
        ↓
그래프 기반 AI 검색, 채팅, 문서 생성, 관리자 감사
```

### 설치

```bash
pip install ltcai
LTCAI
# http://localhost:4825
```

Apple Silicon에서 로컬 모델까지 쓰려면:

```bash
pip install "ltcai[local]"
LTCAI
```

### 사용 흐름

```text
1. 설치한다.
2. CPU, GPU, RAM을 감지해서 적합한 로컬 모델을 추천받는다.
3. 연결할 로컬 폴더를 선택한다.
4. 파일과 대화가 자동으로 지식 그래프가 된다.
5. “지난주 인증 마이그레이션에서 결정한 게 뭐였지?”처럼 질문한다.
6. 같은 지식을 웹 UI, VS Code, Telegram, MCP에서 사용한다.
```

### 핵심 차별점

- **내 데이터가 AI의 기억이 됨** — 채팅과 파일을 자동으로 구조화
- **로컬 우선** — 기본 데이터는 `~/.ltcai/`에 저장
- **자동 그래프** — 사용자가 노드와 엣지를 직접 만들 필요 없음
- **모델 추천/로드 흐름** — 하드웨어 감지 후 적합한 모델 추천
- **선택형 클라우드** — 클라우드 모델은 사용자가 설정한 경우에만 사용
- **관리자/보안 기능** — 권한, 감사 로그, 민감정보 감지, export 지원

자세한 내용은 [docs/CHANGELOG.md](docs/CHANGELOG.md), [SECURITY.md](SECURITY.md), [CONTRIBUTING.md](CONTRIBUTING.md)를 참고하세요.

</details>
