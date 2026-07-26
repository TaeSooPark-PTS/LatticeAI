# Lattice AI

**Your model is the voice you use today. Your Brain is the asset you keep.**

**모델은 갈아타도, 내 지식은 내 컴퓨터에 남는 로컬 우선 AI 브레인.**

[![PyPI Version](https://img.shields.io/pypi/v/ltcai?label=PyPI)](https://pypi.org/project/ltcai/)
[![npm Version](https://img.shields.io/npm/v/ltcai?label=npm)](https://www.npmjs.com/package/ltcai)
[![VS Code Marketplace Version](https://vsmarketplacebadges.dev/version-short/parktaesoo.ltcai.svg)](https://marketplace.visualstudio.com/items?itemName=parktaesoo.ltcai)
[![Open VSX Version](https://img.shields.io/open-vsx/v/parktaesoo/ltcai?label=Open%20VSX)](https://open-vsx.org/extension/parktaesoo/ltcai)
[![CI Status](https://github.com/TaeSooPark-PTS/LatticeAI/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/TaeSooPark-PTS/LatticeAI/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

![v9.9.6 Living Brain walkthrough](output/release/v9.9.6/gifs/v9.9.6-living-brain-walkthrough.gif)

Chat, files, folders, notes, and web pages all flow into one durable knowledge
graph on your computer. Any model — local MLX or cloud — can speak with that
memory. Nothing leaves your machine without explicit consent.

대화·파일·폴더·웹페이지가 전부 내 컴퓨터 안의 지식 그래프로 쌓이고, 어떤
모델이든 그 기억을 이어받아 대화합니다.

## What You Can Do

| | |
| --- | --- |
| **Chat with a Brain that remembers** — every conversation grows durable, source-linked memory ![Brain Chat](output/release/v9.9.6/screenshots/04-brain-chat-home.png) | **See how knowledge connects** — a real relationship graph, not a file list ![Memory Graph](output/release/v9.9.6/screenshots/05-memory-graph.png) |
| **Capture anything** — files, whole folders, notes, screenshots, web pages ![Capture](output/release/v9.9.6/screenshots/06-capture.png) | **Automate with review** — agent changes become proposals you approve first ![Review Center](output/release/v9.9.6/screenshots/12-review-center.png) |
| **Pick a model in one click** — recommended local models for your hardware ![Recommended Models](output/release/v9.9.6/screenshots/02-recommended-models.png) | **Stay in control** — audit, roles, retention in a separate admin surface ![Admin Console](output/release/v9.9.6/screenshots/10-admin-console.png) |

## Why Lattice AI

- **Own your memory** — knowledge lives in a local SQLite Brain you can back up,
  export, inspect, and restore (`.latticebrain` encrypted archive).
- **Model-independent** — switch between local MLX models and cloud models
  without rebuilding context from zero.
- **Honest by design** — the Brain tells you when retrieval context is limited,
  when captured pages extracted poorly, and when the vector index is catching up.
- **Safe automation** — automations are consent-first drafts; edits to existing
  content always pass through a reviewable proposal with a diff.

매번 AI에게 프로젝트 맥락을 다시 설명하고 있다면, 지식이 여러 서비스에 흩어져
있다면, 그 기억을 특정 회사가 아니라 내가 소유하고 싶다면 — Lattice AI가 그
브레인입니다.

## Quick Start

```bash
pip install ltcai        # or: npm install -g ltcai
LTCAI                    # then open http://127.0.0.1:4825/app
```

Apple Silicon local models: `pip install "ltcai[local]"`. Desktop app (Tauri)
ships as a dmg on each [GitHub Release](https://github.com/TaeSooPark-PTS/LatticeAI/releases).

First-run flow — wake the Brain, pick the owner, load a recommended model:

| | | |
| --- | --- | --- |
| ![Login](output/release/v9.9.6/screenshots/01-login.png) | ![Model install](output/release/v9.9.6/screenshots/03-install-load-progress.png) | ![Model library](output/release/v9.9.6/screenshots/07-model-library.png) |

Screenshot index and capture notes:
[output/release/v9.9.6/SCREENSHOT_INDEX.md](output/release/v9.9.6/SCREENSHOT_INDEX.md)

## Current Release

The current release is **9.9.6 — Same Brain Everywhere**:

- **The same Brain on every surface.** VS Code gains the grounding badge on
  recall, a Review Center for staged change proposals, and an agent run
  summary; Telegram repeats the same plain-language outcome. What each
  surface does and does not provide is recorded in
  [docs/SURFACE_PARITY.md](docs/SURFACE_PARITY.md).
- **Evidence becomes action.** The sources an answer actually used turn into
  one-click follow-ups — 요약, 체크리스트, 문서 파일, 한 페이지 — as
  evidence-scoped prompts that run through the normal chat path.
- **Weak models explained in plain language.** Every run returns an honest
  sentence about how it ended and how hard the model had to work, plus one
  concrete next step. It never upgrades a non-success into a success.
- **Sharper citations.** A new sentence-aware `prose` chunking strategy keeps
  Korean claims whole, and citations name where inside a document they came
  from (`Guide > Setup · p.4`).
- **One context contract.** Chat and document generation now share the same
  budget, the same `context_quality` signal, and the same assembly trace.
- **A graph that knows meaning from coincidence.** Relations carry their
  evidence class (`verb` vs `cooccurrence`); enumerations no longer
  manufacture relation chains.
- **Projects that continue.** `/api/projects` remembers the files a project
  produced, what is still open, and the last honest verification — and the
  next run reads it, including what the last failure said to do differently.
- **Loops closed.** Files a run just wrote are recallable immediately; a
  critic PASS that left a requested file unwritten is `NEEDS_REVIEW`, not
  `DONE`.
- **Funnel metrics become decisions.** Admin funnel metrics now return named
  alerts with the number that triggered them, silent below 10 samples.
- **Embedding-swap recovery.** A stale embedder index is named on screen with
  a one-click re-index instead of silently degrading recall.

Release notes: [RELEASE.md](RELEASE.md) · Full history: [docs/CHANGELOG.md](docs/CHANGELOG.md)

Expected artifacts for 9.9.6 release must use exact filenames:

- `dist/ltcai-9.9.6-py3-none-any.whl`
- `dist/ltcai-9.9.6.tar.gz`
- `ltcai-9.9.6.tgz`
- `dist/ltcai-9.9.6.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_9.9.6_aarch64.dmg`

Do not use wildcard artifact uploads. Package registry publishing remains owner-run.

## Architecture At A Glance

FastAPI on localhost is the source of truth; the React/Vite frontend and the
Tauri desktop shell sit on top; the independent `lattice_brain` package owns
the graph, memory, ingestion, and portability. Local-first by default — cloud
calls, downloads, Telegram, and update checks are opt-in.

See [ARCHITECTURE.md](ARCHITECTURE.md) for details and
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for the developer workflow
(`npm install && npm run dev`, validation via `npm run lint`,
`npm run test:unit`, `npm run test:visual`).

## Known Limitations

- External package registries are owner-published and can lag behind GitHub.
- PostgreSQL/pgvector is optional scale/migration tooling. SQLite remains the
  live local Brain store in 9.9.5.
- Docker, model downloads, cloud model calls, Telegram, Brain Network, and
  update checks require explicit user action.
- Conversation does not fabricate answers when no model is loaded. Agent and
  workflow simulation without a loaded LLM is deterministic and LLM-free (it
  does not call a model) — labeled as such, never presented as autonomous
  model success.

## Release History

| Version | Theme |
| --- | --- |
| 9.9.6 | Same Brain Everywhere |
| 9.9.5 | Closed Gaps |
| 9.9.4 | Durable Loops |
| 9.9.3 | Closed Loops |
| 9.9.2 | Artifact Trust |
| 9.9.1 | Clean Foundations |
| 9.9.0 | Fail-Closed Trust |
| 9.8.0 | Honest Knowledge Pipeline |
| 9.7.0 | Proactive Hybrid Brain |
| 9.6.0 | Trusted Agent Loop |
| 9.5.0 | Command Center |
| 9.4.0 | Question-Driven Everyday Automation |
| 9.3.0 | Proactive Brain Intelligence |
| 9.2.0 | Model-Agnostic File Generation |
| 9.1.0 | Code Review Completion & Fail-Closed Runtime |
| 9.0.0 | Code Review Closure & Runtime Cleanup |
| 8.9.0 | Scoped Memory & Tool Policy Hardening |
| 8.8.0 | Brain Core Extraction & Recall Proof Hardening |
| 8.7.0 | Runtime State Hygiene & Release Evidence Refresh |
| 8.6.0 | Desktop Capture & Navigation Reliability |
| 8.5.0 | Tool Registry Readiness & Config DI |
| 8.4.0 | Action-Aware Brain Chat |
| 8.3.0 | Orchestrated Brain Readiness |
| 8.2.0 | Brain Brief |
| 8.1.0 | Intuitive Brain Home |
| 8.0.0 | Runtime Architecture Contract |

Per-release details: [RELEASE_NOTES.md](RELEASE_NOTES.md)

## Documentation

- [docs/WHY_LATTICE.md](docs/WHY_LATTICE.md) — product philosophy
- [docs/TRUST_MODEL.md](docs/TRUST_MODEL.md) — local-first trust model
- [PRIVACY.md](PRIVACY.md) — privacy and external communication policy
- [FEATURE_STATUS.md](FEATURE_STATUS.md) — feature status and limitations
- [SECURITY.md](SECURITY.md) — security posture
- [RELEASE.md](RELEASE.md) — release guide and notes

## License

MIT. See [LICENSE](LICENSE).
