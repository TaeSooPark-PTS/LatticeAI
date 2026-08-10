# Lattice AI

**Your model is the voice you use today. Your Brain is the asset you keep.**

**모델은 갈아타도, 내 지식은 내 컴퓨터에 남는 로컬 우선 AI 브레인.**

[![PyPI Version](https://img.shields.io/pypi/v/ltcai?label=PyPI)](https://pypi.org/project/ltcai/)
[![npm Version](https://img.shields.io/npm/v/ltcai?label=npm)](https://www.npmjs.com/package/ltcai)
[![VS Code Marketplace Version](https://vsmarketplacebadges.dev/version-short/parktaesoo.ltcai.svg)](https://marketplace.visualstudio.com/items?itemName=parktaesoo.ltcai)
[![Open VSX Version](https://img.shields.io/open-vsx/v/parktaesoo/ltcai?label=Open%20VSX)](https://open-vsx.org/extension/parktaesoo/ltcai)
[![CI Status](https://github.com/TaeSooPark-PTS/LatticeAI/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/TaeSooPark-PTS/LatticeAI/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

![v11.2.0 Living Brain walkthrough](output/release/v11.2.0/gifs/v11.2.0-living-brain-walkthrough.gif)

Chat, files, folders, notes, and web pages all flow into one durable knowledge
graph on your computer. Any model — local MLX or cloud — can speak with that
memory. Nothing leaves your machine without explicit consent.

대화·파일·폴더·웹페이지가 전부 내 컴퓨터 안의 지식 그래프로 쌓이고, 어떤
모델이든 그 기억을 이어받아 대화합니다.

## What You Can Do

| | |
| --- | --- |
| **Chat with a Brain that remembers** — every conversation grows durable, source-linked memory ![Brain Chat](output/release/v11.2.0/screenshots/04-brain-chat-home.png) | **See how knowledge connects** — a real relationship graph, not a file list ![Memory Graph](output/release/v11.2.0/screenshots/05-memory-graph.png) |
| **Capture anything** — files, whole folders, notes, screenshots, web pages ![Capture](output/release/v11.2.0/screenshots/06-capture.png) | **Automate with review** — agent changes become proposals you approve first ![Review Center](output/release/v11.2.0/screenshots/12-review-center.png) |
| **Pick a model in one click** — recommended local models for your hardware ![Recommended Models](output/release/v11.2.0/screenshots/02-recommended-models.png) | **Stay in control** — audit, roles, retention in a separate admin surface ![Admin Console](output/release/v11.2.0/screenshots/10-admin-console.png) |
| **Watch a file become memory** — three named steps, not a pipeline diagram ![Material to memory](output/release/v11.2.0/screenshots/11-knowledge-journey.png) | **Say how much it may do alone** — one dial in plain words; dangerous actions stay blocked either way ![Settings](output/release/v11.2.0/screenshots/08-system.png) |

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
| ![Login](output/release/v11.2.0/screenshots/01-login.png) | ![Model install](output/release/v11.2.0/screenshots/03-install-load-progress.png) | ![Model library](output/release/v11.2.0/screenshots/07-model-library.png) |

Screenshot index and capture notes:
[output/release/v11.2.0/SCREENSHOT_INDEX.md](output/release/v11.2.0/SCREENSHOT_INDEX.md)

## Current Release

The current release is **11.2.0 — All Systems On**:

11.1.0 built the intelligence layer; 11.2.0 makes sure **every part of it
actually works, ships today's models, and puts the switches in your hand.**

- **The model catalog is current and verified.** Every recommended model
  was checked against the live Hugging Face API — no weights downloaded, no
  model loaded, nothing run on your machine. Two models that had vanished
  from the Hub (phi-3.5-vision, moondream2), four gated repos and three
  obsolete generations left the catalog; the ten recommendations are all
  2025–2026 builds (Gemma 4 line, Qwen3.5/3.6, gpt-oss-20b, LFM2.5 with
  Korean support), RAM-tiered, with exact-case ids, real byte sizes (one
  entry claimed 11.8 GB for a 61 GB repo) and a static-loadability verdict
  whose limits are stated, not hidden. Already-downloaded older models stay
  recognised so nobody's local weights become orphans.
- **Every switch is yours now.** A new 기능 drawer on the Brain home's dock
  lists every opt-in capability — multimodal memories, video, brain
  network sharing, vault watch, photo semantic search, RRF fusion, graph
  expansion, automatic synthesis, background indexing, and the vector
  backend choice — each with a plain-language line, its current source
  (default / env / you), and a live switch. Toggles apply at runtime
  through injected gate seams; an uninstalled backend says "install
  needed" instead of pretending.
- **The scope-outs are in.** Notion-export, Git-history and mail/calendar
  bridges (all through the single ingestion gate with approval and
  dry-run), recipient public-key encryption for subgraph shares (X25519
  sealed box; passphrase mode stays), video ingestion via guarded ffmpeg
  keyframes with subtitle companions, vault watch mode, bulk review
  approval, the Self-Model summary reaching the agent loop, and text→image
  semantic photo search.
- **A 58-row evidence audit** verified every documented feature against
  the running app: 51 worked as documented; the rest were fixed (a
  permanently blank briefing health section, cloud memory write-back that
  never reached the Review Center, an empty Brain scoring 100/excellent)
  or the docs were lowered to the truth. The audit ships as
  [docs/FEATURE_AUDIT_v11.2.0.md](docs/FEATURE_AUDIT_v11.2.0.md).

All of it lands with the floor intact: **6,490 tests, 100.00% of 39,054
statements and 11,014 branches**, verified on macOS 3.14, a fresh-resolve
python 3.11 environment, and a clean linux python:3.14 container.

Release notes: [RELEASE.md](RELEASE.md) · Full history: [docs/CHANGELOG.md](docs/CHANGELOG.md)

Expected artifacts for 11.2.0 release must use exact filenames:

- `dist/ltcai-11.2.0-py3-none-any.whl`
- `dist/ltcai-11.2.0.tar.gz`
- `ltcai-11.2.0.tgz`
- `dist/ltcai-11.2.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_11.2.0_aarch64.dmg`

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
  live local Brain store in 10.3.0.
- Docker, model downloads, cloud model calls, Telegram, Brain Network, and
  update checks require explicit user action.
- Conversation does not fabricate answers when no model is loaded. Agent and
  workflow simulation without a loaded LLM is deterministic and LLM-free (it
  does not call a model) — labeled as such, never presented as autonomous
  model success.
- Some backend-generated messages (for example the Postgres DSN notice) are
  produced server-side in English and are shown as-is; server-side i18n is not
  part of 10.3.0.

## Release History

| Version | Theme |
| --- | --- |
| 11.2.0 | All Systems On |
| 11.1.0 | Product Intelligence |
| 11.0.1 | Both Branches |
| 11.0.0 | Full Measure |
| 10.10.0 | Quiet Station |
| 10.9.0 | Never Blocks |
| 10.8.0 | Within Reach |
| 10.7.0 | Plain Surface |
| 10.6.4 | Loud Limits |
| 10.6.3 | Loud Limits |
| 10.6.2 | Ask First |
| 10.6.1 | First Things |
| 10.6.0 | Promoted Panels |
| 10.5.0 | Everyday Words |
| 10.4.0 | Named Ground |
| 10.3.0 | Measured Ground |
| 10.2.0 | Load-Bearing Fixes |
| 10.1.1 | Reachable Boundary |
| 10.1.0 | Hybrid Brain |
| 10.0.1 | One Source of Truth |
| 10.0.0 | Plain Language |
| 9.9.9 | Lean Shell |
| 9.9.8 | Autonomy Dial |
| 9.9.7 | No Gaps Left |
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
