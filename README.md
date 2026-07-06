# Lattice AI

**Lattice AI 8.9.0 is the local-first Digital Brain platform. This release hardens authenticated history and graph scoping, blocks unapproved direct Tool API execution paths, makes local permission approvals and installer/process execution safer to audit, and keeps package, desktop, extension, static app, and documentation metadata synchronized to the 8.9.0 line.**

**Lattice AI는 모델이 바뀌어도 내 지식과 맥락을 보존하는 로컬 우선 AI 브레인입니다.**

Your model is the voice you use today. Your Brain is the asset you keep.
Lattice AI preserves conversations, documents, decisions, project context,
relationships, and workflows on your computer by default. Cloud models, model
downloads, update checks, and other external communication happen only after
explicit consent.

It is not a ChatGPT clone, a model launcher, a graph database, or a note app.
It is the finished private AI memory layer wrapped in a Living Brain experience — with desktop source capture that uses native folder selection, ToolRegistry readiness, Config-driven DI for automation, and continued AgentRuntime/Tool wiring seams.

[![PyPI Version](https://img.shields.io/pypi/v/ltcai?label=PyPI)](https://pypi.org/project/ltcai/)
[![npm Version](https://img.shields.io/npm/v/ltcai?label=npm)](https://www.npmjs.com/package/ltcai)
[![VS Code Marketplace Version](https://vsmarketplacebadges.dev/version-short/parktaesoo.ltcai.svg)](https://marketplace.visualstudio.com/items?itemName=parktaesoo.ltcai)
[![Open VSX Version](https://img.shields.io/open-vsx/v/parktaesoo/ltcai?label=Open%20VSX)](https://open-vsx.org/extension/parktaesoo/ltcai)
[![CI Status](https://github.com/TaeSooPark-PTS/LatticeAI/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/TaeSooPark-PTS/LatticeAI/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

## Why You Need It

You need Lattice AI when:

- you ask different AI models about the same project and lose the context each time;
- your decisions are scattered across chats, notes, PDFs, folders, and tools;
- you want to switch models without rebuilding memory from zero;
- you want your AI Brain to stay on your computer by default;
- you want backup, restore, inspect, and export paths for your Brain.

이런 사람에게 필요합니다:

- 매번 AI를 바꿀 때마다 프로젝트 맥락을 다시 설명하는 사람
- 문서, 대화, 결정, 파일이 여기저기 흩어져 있는 사람
- 내 지식을 특정 AI 서비스 안에 묶어두고 싶지 않은 사람
- 로컬에 저장되는 개인 AI 브레인을 원하는 사람

## What You Can Do

- Chat with a Brain that remembers useful context instead of treating every
  session as disposable.
- Add documents, selected local folders, notes, screenshots, and web pages with
  source-aware memory.
- See recent memories, older memories, topics, relationships, and the full
  knowledge graph when you want deeper structure.
- Create consent-first Brain automation drafts for memory digests, project
  reviews, and follow-up suggestions before any schedule is enabled.
- Use a recommended local model without learning model internals first.
- Keep advanced controls, audit logs, roles, and retention in a separate Admin
  surface.
- Export or back up your Brain as an encrypted `.latticebrain` archive.

## One-Minute Flow

1. Launch the app and wake the Brain.
2. Create or open a local profile.
3. Let Lattice explain what this computer can run.
4. Start with the recommended model as the Brain's voice, or skip and choose later.
5. Talk to your Brain.
6. Use the memory rings to move from current context to the full knowledge graph.
7. Back up, inspect, export, or restore the Brain when you need ownership actions.

## Living Brain Flow

The screenshots below are the latest checked-in visual evidence captures. They
keep the first-run Brain flow, memory graph, source capture, model library,
system view, admin console, and review center visible as release gates while
8.9.0 focuses on scoped memory isolation, direct Tool API policy enforcement,
AgentRuntime human-approval semantics, confirmation-token guarded installer
execution, and frontend/runtime maintainability.

### 1. Wake Brain

The first screen makes the Brain the product. It explains the three-step path:
confirm owner, check the computer, choose the Brain voice.

### 2. Login

Choose the owner of the Brain. The profile is not a SaaS account by default; it
is the local identity for the knowledge you keep.

![Login](output/release/v8.7.0/screenshots/01-login.png)

### 3. Recommended Models

Start with a short list: safest recommendation, faster model, stronger model.
Advanced details stay available without overwhelming first-time users.

![Recommended Models](output/release/v8.7.0/screenshots/02-recommended-models.png)

### 4. Install And Load

Download and load only after consent. Lattice explains model size, local
execution, and network use before work starts.

![Install and Load](output/release/v8.7.0/screenshots/03-install-load-progress.png)

### 5. Brain Chat

Talk normally. Useful decisions and context become memory, then appear later as
topics, relationships, graph structure, and the concentric memory rings around
the Brain.

![Brain Brief Home](output/release/v8.7.0/screenshots/04-brain-chat-home.png)

### 6. Review Center

Automation results are staged for review before they become durable decisions.
Snooze, unsnooze, run now, approve, and dismiss actions stay explicit.

![Review Center](output/release/v8.7.0/screenshots/12-review-center.png)

## Brain Depths

The user travels inward from everyday memory to deeper structure:

| Level | User name | What the user gets |
| --- | --- | --- |
| Level 1 | Now memory | The living Brain presence and current conversation context |
| Level 2 | Older memory | Durable memories with source-aware recall |
| Level 3 | Topics | Recurring themes across chats and documents |
| Level 4 | Relationships | How decisions, people, files, and ideas connect |
| Level 5 | Full knowledge graph | Nodes, edges, search, and focused detail for advanced exploration |

Walkthrough:

![v8.7.0 Living Brain walkthrough](output/release/v8.7.0/gifs/v8.7.0-living-brain-walkthrough.gif)

Screenshot index and capture notes:
[output/release/v8.7.0/SCREENSHOT_INDEX.md](output/release/v8.7.0/SCREENSHOT_INDEX.md)

## Install

Run from Python:

```bash
pip install ltcai
LTCAI
```

Run from npm:

```bash
npm install -g ltcai
ltcai
```

Open the local app:

```text
http://127.0.0.1:4825/app
```

Apple Silicon local model extras:

```bash
pip install "ltcai[local]"
```

## Architecture At A Glance

- **Product category**: local-first Digital Brain.
- **Core capability**: private AI memory layer for conversations, documents,
  decisions, relationships, workflows, and project context.
- **UX metaphor**: Living Brain.
- **Desktop shell**: Tauri 2 starts a localhost sidecar.
- **Frontend**: React, TypeScript, Vite, TanStack Query, Zustand, Cytoscape.js,
  React Flow, and generated OpenAPI types.
- **Backend**: FastAPI on localhost is the UI source of truth.
- **Brain Core**: independent `lattice_brain` package for graph, memory,
  context, conversations, ingestion, runtime, workflow, storage, and portability.
- **Storage**: SQLite is the live local Brain store; PostgreSQL/pgvector tooling
  is optional scale-mode planning/migration support, not the default live graph
  backend.
- **Portability**: encrypted `.latticebrain` archives plus backup, restore,
  inspect, verify, import dry-run, and confirmed restore/import flows.
- **Trust boundary**: local-first by default; cloud calls, downloads, Telegram,
  Brain Network, Docker/Postgres setup, and update checks are opt-in.
- **Admin separation**: normal Brain use stays separate from users, audit logs,
  policies, security events, retention, and index rebuilds.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the current architecture.

## Local Development

```bash
npm install
npm run dev
```

Main validation set:

```bash
npm run check:python
npm run lint
npm run typecheck
npm run test:unit
npm run test:integration
npm run test:visual
npm run desktop:tauri:check
npm run docs:check-links
```

`npm run lint` includes the Python Ruff baseline, frontend TypeScript lint
gate, visual smoke syntax checks, and i18n literal checks.

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for developer workflow details.

## Current Release

The current release is **8.9.0 — Scoped Memory & Tool Policy Hardening**:

- Conversation history reads and deletes now scope by authenticated user and
  readable workspace, while preserving legacy-global rows only in no-auth or
  explicitly compatible flows.
- Knowledge Graph search, graph traversal, relationships, node reads, and chat
  context now apply workspace scope at the graph/service boundary.
- Direct HTTP/MCP Tool API calls enforce ToolRegistry policy before hooks or
  handlers run; destructive paths and non-auto-approved user writes are blocked.
- AgentRuntime no longer treats “requires approval” plans as implicitly
  approved, and rollback now handles tool results that omit `success`.
- Local permission approvals hash tokens at rest, use atomic queue writes, and
  reject write approvals for blocked system prefixes.
- Frontend API base handling, Tauri credentials, workspace clearing, CSS tokens,
  and i18n literal checks are split into safer maintainability seams.

Expected artifacts for 8.9.0 release must use exact filenames:

- `dist/ltcai-8.9.0-py3-none-any.whl`
- `dist/ltcai-8.9.0.tar.gz`
- `ltcai-8.9.0.tgz`
- `dist/ltcai-8.9.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_8.9.0_aarch64.dmg`

Do not use wildcard artifact uploads. Package registry publishing remains owner-run.

See [docs/ROADMAP_RECOMMENDATIONS.md](docs/ROADMAP_RECOMMENDATIONS.md) for the
strategic roadmap slices applied through 8.9.0 and the follow-up tracks.

## Known Limitations

- External package registries are owner-published and can lag behind GitHub.
- PostgreSQL/pgvector is optional scale/migration tooling. SQLite remains the
  live local Brain store in 8.9.0.
- Docker, model downloads, cloud model calls, Telegram, Brain Network, and update
  checks require explicit user action.
- Conversation does not fabricate answers when no model is loaded.
- Agent/workflow simulation without a loaded LLM is deterministic and does not
  call a model; it is labeled as LLM-free/model-free rather than presented as
  autonomous model success.

## Release History

| Version | Theme |
| --- | --- |
| 8.9.0 | Scoped Memory & Tool Policy Hardening: authenticated history/KG reads are workspace-scoped, direct Tool API paths enforce registry policy, local approvals hash tokens at rest, AgentRuntime approval semantics are explicit, and frontend/runtime seams are split |
| 8.8.0 | Brain Core Extraction & Recall Proof Hardening: internal-only Brain shim layers are removed, AgentRuntime run contracts/retry budgets are tighter, Brain Chat gains conversation controls, and citation recall exposes matched evidence |
| 8.7.0 | Runtime State Hygiene & Release Evidence Refresh: model-runtime internals prefer typed state over legacy globals, compatibility sync is deprecated, 8.7.0 visual evidence is refreshed, and all release metadata/docs are synchronized |
| 8.6.0 | Desktop Capture & Navigation Reliability: native folder selection works from the Tauri localhost app, picker failures surface in Capture, web saving remains one-action, and the Brain shell sidebar/admin flow is CI-covered |
| 8.5.0 | Tool Registry Readiness & Config DI: ToolRegistry drift removed, `tz_name` flows through central Config into automation runtimes, and current-release documentation is synchronized |
| 8.4.0 | Action-Aware Brain Chat: explicit file create/write/save/edit requests from Brain Chat route into the governed workspace file tool so files are actually created instead of returned as code-only answers |
| 8.3.0 | Orchestrated Brain Readiness: managed legacy shim inventory, stronger AgentRuntime/workflow boundaries, unified graph ingestion, workspace-safe duplicate content, first-run onboarding, and explicit community/plugin growth path |
| 8.2.0 | Brain Brief: evidence-backed home briefing, honest empty-state guidance, recall/graph/model-proof next actions, and continued model/workspace runtime extraction |
| 8.1.0 | Intuitive Brain Home: living Brain, recent memory, connected topic, next action, and composer are visible in one product-first screen with refreshed 8.1.0 evidence and artifacts |
| 8.0.0 | Runtime Architecture Contract: AgentRuntime, ToolRegistry, central Config, server decomposition, and KG hardening are captured as machine-checkable release boundaries with exact 8.0.0 artifacts |
| 7.9.0 | Agent Runtime Boundary Hardening: explicit `SingleAgentRuntime`, compatibility alias preservation, injected rollback port, and release/readiness docs aligned to the product AgentRuntime facade |
| 7.8.0 | Brain Chat Home UX Simplification: chat-first first viewport, visible workspace navigation, collapsed source/status utilities, hidden default depth controls, and removal of obsolete Brain UX components |
| 7.7.0 | Complete Product Polish: command-center Brain Home, repaired Review Center evidence, exact 7.7 docs/artifacts, product readiness gate, and stronger CI/release checks |
| 7.6.0 | Brain-Centered UX & Architecture Closure: Wake Brain first-run surface, concentric memory rings with direct depth controls, and machine-checkable closure of the two architecture/UX review files |
| 7.5.0 | Runtime Debt Burn-down & Release Risk Cleanup: API consumers get normalized contract views, retrieval quality uses a 250+ record corpus fixture, stale artifact risk is removed, npm audit is clean, and Tauri is updated past the old block warning |
| 7.4.0 | Runtime Contract Convergence & Corpus Retrieval: agent/workflow/audit/realtime records share the agent-run-contract/v1 family, and retrieval quality gates run against a real corpus-scale fixture |
| 7.3.0 | Runtime Contract & Retrieval Quality: shared agent-run contract across runtimes plus deterministic hybrid recall/ranking regression gates |
| 7.2.0 | Runtime Trust Baseline: agent run preview/readiness, simulation-mode guardrails, live ToolRegistry manifest/diagnostics, and tests for dispatch/governance/catalog drift |
| 7.1.0 | Brain Usability Completion: clearer first-run onboarding, ingestion progress/emergence, richer graph controls, inline answer proof, workspace/profile/admin discovery, empty/error/consent feedback, and VS Code sync status |
| 7.0.0 | Brain Productization Loop: first-screen ingestion for files/folders/notes/web, answer-level memory proof and source citations, model-continuity demo flow, five-minute first-run loop, and recall/KG quality eval in CI |

## Documentation

- [docs/WHY_LATTICE.md](docs/WHY_LATTICE.md) - product philosophy.
- [docs/TRUST_MODEL.md](docs/TRUST_MODEL.md) - local-first trust model.
- [PRIVACY.md](PRIVACY.md) - privacy and external communication policy.
- [ARCHITECTURE.md](ARCHITECTURE.md) - current technical architecture.
- [FEATURE_STATUS.md](FEATURE_STATUS.md) - current feature status and known limitations.
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) - developer workflow.
- [docs/LEGACY_COMPATIBILITY.md](docs/LEGACY_COMPATIBILITY.md) - root legacy shim map.
- [RELEASE.md](RELEASE.md) - release guide and current release notes.
- [docs/CHANGELOG.md](docs/CHANGELOG.md) - historical changes.
- [SECURITY.md](SECURITY.md) - security posture.

## License

MIT. See [LICENSE](LICENSE).
