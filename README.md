<div align="center">
  <img src="docs/images/logo.svg" alt="Lattice AI" width="120" />

  # Lattice AI

  **Lattice AI v3 — Local-First AI Workspace Platform.**

  Work across Personal and Organization workspaces with Knowledge Graph,
  Vector Index, Hybrid Search, Native Chat, agents, files, models, and
  Basic / Advanced / Admin modes.
</div>

<div align="center">

[![PyPI version](https://img.shields.io/pypi/v/ltcai?label=PyPI)](https://pypi.org/project/ltcai/)
[![npm version](https://img.shields.io/npm/v/ltcai?label=npm)](https://www.npmjs.com/package/ltcai)
[![VS Code Marketplace](https://vsmarketplacebadges.dev/version-short/parktaesoo.ltcai.svg)](https://marketplace.visualstudio.com/items?itemName=parktaesoo.ltcai)
[![Open VSX](https://img.shields.io/open-vsx/v/parktaesoo/ltcai?label=Open%20VSX)](https://open-vsx.org/extension/parktaesoo/ltcai)
[![GitHub release](https://img.shields.io/github/v/release/TaeSooPark-PTS/LatticeAI?label=GitHub%20release)](https://github.com/TaeSooPark-PTS/LatticeAI/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![VS Code extension](https://img.shields.io/badge/VS%20Code-extension-blue?logo=visualstudiocode)](https://marketplace.visualstudio.com/items?itemName=parktaesoo.ltcai)

</div>

![Lattice AI demo](docs/images/lattice-ai-demo.gif)

## Install

Install the local workspace:

```bash
pip install ltcai
```

Add Apple Silicon local model support:

```bash
pip install "ltcai[local]"
```

Install the npm CLI:

```bash
npm install -g ltcai
```

Install the coding extension:

- [VS Code Marketplace: parktaesoo.ltcai](https://marketplace.visualstudio.com/items?itemName=parktaesoo.ltcai)
- [Open VSX: parktaesoo.ltcai](https://open-vsx.org/extension/parktaesoo/ltcai)
- [GitHub Releases](https://github.com/TaeSooPark-PTS/LatticeAI/releases)

## Quick Start

Start the workspace:

```bash
LTCAI
```

Then open:

```text
http://127.0.0.1:4825/app
```

Development checkout:

```bash
npm install
npm run dev
```

Useful validation commands:

```bash
npm run check:python
npm run test:unit
npm run build
```

## What Is Lattice AI?

Lattice AI v3 is a local-first AI workspace platform for people and teams who
want their files, models, graph context, retrieval, and agent workflows in one
place.

- **Primary app shell**: `/app` is the default product experience with Chat,
  Files, Hybrid Search, Knowledge Graph, Memory, Models, Settings, Advanced
  agent/workflow tooling, and Admin areas. Classic pages remain compatibility
  routes only; normal workflows stay in `/app`.
- **Local-first AI Workspace**: work starts on your machine, with local data and
  workspace state by default.
- **AI Pipeline Platform**: plan, execute, review, retry, and replay work across
  local models, cloud models, tools, files, and generated artifacts.
- **Knowledge Graph Platform**: documents, images, screenshots, notes,
  conversations, and decisions become linked entities, relationships, evidence,
  and reusable context.
- **Multi-Agent Workflow Platform**: agents hand off structured context, review
  work, retry with reasons, and keep timelines inspectable.
- **Personal / Organization Workspace**: move between personal work and team
  workspaces with role-aware views and Basic / Advanced / Admin modes.
- **Vector Index and Hybrid Search**: local vector rows are derived from the
  Knowledge Graph and fused with keyword and graph signals.
- **Local Model Management**: choose current multimodal local models with source
  disclosure, hardware-aware recommendations, and cloud fallback options.
- **Community-first workspaces**: Personal and Organization workspaces ship in
  the local product; enterprise SSO/SCIM/governance remain future extensions.

## Why Lattice AI?

Most AI tools split your work across a chat window, a model picker, loose files,
and disconnected automations. Lattice AI keeps those parts together:

- files and conversations become graph context;
- graph context feeds pipelines and coding actions;
- model cards disclose country, company, run mode, internet usage, and model
  identity;
- personal and organization workspaces keep team workflows separate from local
  work;
- multi-agent workflows leave behind replayable plans, reviews, retries, and
  outcomes.

## v3.3.1 Highlights

Lattice AI v3.3.1 rebuilds the visible `/app` product experience while
preserving the existing local-first runtime. The app now presents Chat, Files,
Search, Knowledge, Memory, Models, Settings, Advanced tooling, and Admin
workflows with clearer navigation and honest live/unavailable states.

- **Visual product rebuild** — compact rail navigation, quieter topbar,
  command-palette search, retrieval readiness footer, and denser controls.
- **Truthful Home dashboard** — backend, model, retrieval, memory, source, and
  trace readiness are derived from real endpoints instead of fabricated counts.
- **Basic / Advanced / Admin navigation** — Basic focuses on core workspace
  workflows; Advanced exposes agents, workflows, skills, hooks, and MCP; Admin
  keeps organization controls separate.
- **Files and Settings clarity** — manual upload is available immediately,
  folder watching is explicitly tied to the desktop local agent, and Settings
  shows backend, agent, model, telemetry, and embedding readiness.
- **Design system refresh** — cooler neutral light/dark tokens, tighter 8px
  radius discipline, compact cards/tables/stats/buttons, and regenerated
  hashed v3 assets.

The v3.2.0 platform remains the feature-complete foundation: multi-agent
collaboration, Agent Registry, Marketplace templates, Workflow Agents,
Autonomous Planning, Long-Term Memory, Skills, Hooks, Tool Registry, MCP
Manager, production embedding profiles, and hash-manifested `/app` assets.
Release audit: [docs/V3_2_AUDIT.md](docs/V3_2_AUDIT.md).

## Screenshots

### Workspace

![Workspace light theme](docs/images/workspace-light.png)

![Workspace dark theme](docs/images/workspace-dark.png)

### Knowledge Graph

![Knowledge Graph](docs/images/knowledge-graph.png)

### AI Pipeline

![AI Pipeline designer](docs/images/pipeline.png)

### Admin Dashboard

![Admin dashboard](docs/images/admin-dashboard.png)

### Mobile Responsive

![Mobile responsive layout](docs/images/mobile-responsive.png)

## Knowledge Graph Flow

```text
files / documents / images / screenshots / conversations / decisions
  -> multimodal understanding
  -> entity and relationship extraction
  -> evidence and artifact storage
  -> Knowledge Graph update
  -> AI pipeline context
  -> coding actions / analysis / documents / team workflows
```

The graph keeps useful workspace context available even when you change models.

## v3 Backend Retrieval

The v3 backend adds a local-first retrieval stack that combines the Knowledge
Graph, a SQLite vector index, and hybrid result fusion. It preserves existing
graph data while adding derived vector rows that can be rebuilt at any time.

Embedding status: production profiles are exposed through
`GET /api/embeddings/providers`, while `lattice-local-hash-v1` remains a
deterministic fallback for offline indexing and tests. It is never presented as
a production semantic embedding model.

Core API contracts:

- `POST /api/search/hybrid`
- `GET /api/search/keyword?q=...`
- `GET /api/search/vector?q=...`
- `GET /api/graph`
- `GET /api/graph/node?node_id=...`
- `GET /api/graph/relationship`
- `GET /api/index/status`
- `POST /api/index/rebuild`

See [docs/V3_BACKEND_ARCHITECTURE.md](docs/V3_BACKEND_ARCHITECTURE.md) for the
storage model, search model, migration behavior, and API response shape.

## Local Model Policy

Lattice AI recommends current-generation multimodal models for local use and
keeps local model choices explicit.

| Family | Default role | Example recommendation |
| --- | --- | --- |
| Gemma 4 | Default Google multimodal family | `mlx-community/gemma-4-12b-it-4bit` |
| Gemma 4 large | Higher-quality local multimodal work | `mlx-community/gemma-4-31b-it-4bit` |
| Qwen3-VL | Smaller, balanced multimodal options | `mlx-community/Qwen3-VL-4B-Instruct-4bit` |
| Llama 4 | Meta multimodal option | `mlx-community/Llama-4-Scout-17B-16E-Instruct-4bit` |

Every recommended model card shows maker country, maker company, run mode,
internet requirement, and model name. See [MODEL_POLICY.md](MODEL_POLICY.md).

## Architecture

```text
Personal / Organization Workspace
  -> files, chats, screenshots, model choices, workflow events
  -> Knowledge Graph
  -> AI Pipeline
  -> Multi-Agent Workflow
  -> coding actions, documents, analysis, team handoffs
```

Core areas:

- FastAPI local workspace app
- Knowledge Graph storage and graph APIs
- AI pipeline and workflow designer
- Multi-agent handoff, review, retry, and replay records
- Local model management and model recommendation catalog
- VS Code / Cursor / VSCodium extension surface
- Personal and organization workspace boundaries

## Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) — workspace, graph, pipeline, and model-management overview
- [docs/architecture.md](docs/architecture.md) — full architecture reference
- [PROJECT_PRINCIPLES.md](PROJECT_PRINCIPLES.md) — product principles
- [AI_PHILOSOPHY.md](AI_PHILOSOPHY.md) — how AI is used in the workspace
- [MODEL_POLICY.md](MODEL_POLICY.md) — local model recommendation policy
- [KNOWLEDGE_GRAPH.md](KNOWLEDGE_GRAPH.md) — graph model and behavior
- [docs/MULTI_AGENT_RUNTIME.md](docs/MULTI_AGENT_RUNTIME.md) — multi-agent workflow runtime
- [docs/WORKFLOW_DESIGNER.md](docs/WORKFLOW_DESIGNER.md) — AI pipeline designer
- [docs/REALTIME_COLLABORATION.md](docs/REALTIME_COLLABORATION.md) — realtime workspace events
- [docs/ENTERPRISE.md](docs/ENTERPRISE.md) — organization workspaces and SSO
- [docs/PLUGIN_SDK.md](docs/PLUGIN_SDK.md) — plugin SDK
- [RELEASE_NOTES.md](RELEASE_NOTES.md) and [docs/CHANGELOG.md](docs/CHANGELOG.md)

## Release history

| Version | Theme |
| --- | --- |
| **3.3.1** | Visual product rebuild — rebuilt `/app` shell, Basic/Advanced/Admin navigation, cooler token palette, compact component system, Home readiness dashboard, Files local-agent truthfulness, Settings runtime status, and v3.3.1 design notes |
| **3.3.0** | Product quality & honesty release — evidence-based feature audit (`FEATURE_STATUS.md`), single-source version truth, working manual document upload in Files, fixed document-generation streaming, truthful Home retrieval status, documented design system (`STYLE_SYSTEM.md`) |
| 3.2.0 | Feature-complete platform — multi-agent collaboration, agent registry, marketplace + templates, workflow agents, autonomous planning, long-term memory + manager, skills/hooks/tool registries, MCP manager, all operable from `/app` |
| 3.1.0 | Mainline platform completion — native `/app` workflows, Classic retired from normal paths, production embedding profiles, AgentRuntime/registries, hashed v3 assets |
| 3.0.1 | Release-blocker remediation — provider-backed embeddings (Hash/MLX/Ollama/OpenAI/Custom), unified AgentRuntime boundary, every v3 surface connected or clearly unavailable |
| 3.0.0 | v3 local-first AI workspace platform — `/app`, Native Chat, Knowledge Graph, Vector Index, Hybrid Search, workspace modes |
| 2.2.7 | Visual system stabilization — cohesive dark/light screens, crisp chat composer, dark graph canvas, Workspace OS polish |
| 2.2.6 | Token-native CSS foundation |
| 2.2.5 | Release hygiene hotfix — dark overlays, modal stack, cache-busting, favicon, and Telegram log masking |
| 2.2.4 | Chat dark-mode completion |
| 2.2.3 | Frontend stability and UX fixes |
| 2.2.2 | Frontend QA stabilization — mobile nav, admin actions, overflow fixes, and expanded visual tests |
| 2.2.1 | Frontend and UX overhaul for responsive workspace, themes, graph UX, admin reflow, and file attachment |
| 2.2.0 | Multimodal-first Knowledge Graph and local model source disclosure |
| 2.1.0 | Multi-agent workflow maturity |
| 2.0.0 | AI pipeline, workflow, and plugin platform foundation |
| 1.7.0 | Graph and collaboration |
| 1.6.0 | Product experience deepening |

## License

MIT
