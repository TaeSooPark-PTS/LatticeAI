# Lattice AI

**Your model is the voice you use today. Your Brain is the asset you keep.**

**모델은 갈아타도, 내 지식은 내 컴퓨터에 남는 로컬 우선 AI 브레인.**

[![PyPI Version](https://img.shields.io/pypi/v/ltcai?label=PyPI)](https://pypi.org/project/ltcai/)
[![npm Version](https://img.shields.io/npm/v/ltcai?label=npm)](https://www.npmjs.com/package/ltcai)
[![VS Code Marketplace Version](https://vsmarketplacebadges.dev/version-short/parktaesoo.ltcai.svg)](https://marketplace.visualstudio.com/items?itemName=parktaesoo.ltcai)
[![Open VSX Version](https://img.shields.io/open-vsx/v/parktaesoo/ltcai?label=Open%20VSX)](https://open-vsx.org/extension/parktaesoo/ltcai)
[![CI Status](https://github.com/TaeSooPark-PTS/LatticeAI/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/TaeSooPark-PTS/LatticeAI/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

![v11.5.2 Living Brain walkthrough](output/release/v11.5.2/gifs/v11.5.2-living-brain-walkthrough.gif)

Chat, files, folders, notes, and web pages all flow into one durable knowledge
graph on your computer. Any model — local MLX or cloud — can speak with that
memory. Nothing leaves your machine without explicit consent.

대화·파일·폴더·웹페이지가 전부 내 컴퓨터 안의 지식 그래프로 쌓이고, 어떤
모델이든 그 기억을 이어받아 대화합니다.

## What You Can Do

| | |
| --- | --- |
| **See your Brain's story in time** — a growth curve, an activity heatmap, and each day's story, rewindable to any past moment ![Brain Chronicle](output/release/v11.5.2/screenshots/13-chronicle.png) | **Chat with a Brain that remembers** — every conversation grows durable, source-linked memory ![Brain Chat](output/release/v11.5.2/screenshots/04-brain-chat-home.png) |
| **See how knowledge connects** — a real relationship graph, not a file list ![Memory Graph](output/release/v11.5.2/screenshots/05-memory-graph.png) | **Capture anything** — files, whole folders, notes, screenshots, web pages ![Capture](output/release/v11.5.2/screenshots/06-capture.png) |
| **Automate with review** — agent changes become proposals you approve first ![Review Center](output/release/v11.5.2/screenshots/12-review-center.png) | **Pick a model in one click** — recommended local models for your hardware ![Recommended Models](output/release/v11.5.2/screenshots/02-recommended-models.png) |
| **Watch a file become memory** — three named steps, not a pipeline diagram ![Material to memory](output/release/v11.5.2/screenshots/11-knowledge-journey.png) | **Say how much it may do alone** — one dial in plain words; dangerous actions stay blocked either way ![Settings](output/release/v11.5.2/screenshots/08-system.png) |

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
| ![Login](output/release/v11.5.2/screenshots/01-login.png) | ![Model install](output/release/v11.5.2/screenshots/03-install-load-progress.png) | ![Model library](output/release/v11.5.2/screenshots/07-model-library.png) |

Screenshot index and capture notes:
[output/release/v11.5.2/SCREENSHOT_INDEX.md](output/release/v11.5.2/SCREENSHOT_INDEX.md)

## Current Release

The current release is **11.5.2 — Tight Ship**:

A cleanup-and-correctness release built from three audits of the settled
11.5.1 tree — a Rust↔Python duplication map, an arc-level dead-code and
test audit, and a live front-door parity sweep across 192 endpoints
(gateway vs. direct worker). The duplication map's verdict was that the
boundary itself is sound: **zero cross-boundary deletions**, because every
twin is either the live Python-direct surface or a copy pinned by a parity
golden. The real duplication was inside each language, and that is what
this release removes.

- **About 1,100 lines gone.** Six moved-module shims, a multimodal
  streaming seam that was never wired, roughly 27 zero-caller symbols
  whose only callers were their own tests, an interface method
  (`metadata_for`) no consumer ever called, a dead measurement script,
  and the **legacy Electron shell** — superseded by Tauri, yet still
  shipping inside the npm tarball. Every deletion came with per-assertion
  test surgery so the 100% line **and** branch floor never moved, and a
  new guard forbids the `sys.modules[__name__]` shim pattern from coming
  back. The 183 coverage work-package test files were all verified
  load-bearing (ten of them are the sole owners of 79 branch arcs); seven
  coverage-redundant feature tests were deliberately kept for their
  regression value.
- **One home per thing.** The byte-identical embedder pair is
  single-sourced on the golden-pinned copy — silent vector drift between
  two write paths was the failure mode. The workspace selector's four
  verbatim re-derivations now use the canonical rule, an intended
  behaviour change: chat, agent, upload, computer-use and admin **403 on
  disagreement** where the copies silently preferred the header. sha256,
  SSE frame builders, the data-dir default, the mode services and the
  module-importability probe each collapsed to one implementation, and
  seven byte-identical Rust copies were consolidated (including a
  wholesale duplicate of `clock.rs`).
- **The front door, fixed with live before/after proof.** Proxied
  redirects now pass through with `Set-Cookie` and `Location` intact —
  the invite gate was a hard dead-end, SSO login silently never
  authenticated, and twelve deep links lost their fragment. Absolute
  `Location` values naming the internal worker are rewritten to the
  gateway origin. The native `/rust/*` and `/host/status|jobs` lanes are
  **posture-gated fail-closed**: they used to serve the whole graph
  unauthenticated while the worker required auth. `X-Forwarded-For/Proto/
  Host` cross the hop and are honoured only from a loopback or listed
  trusted-proxy peer, which fixes `--no-spawn` CSRF refusals and the
  invite links, notifications and SSO URLs that named the internal worker
  port. The supervisor injects CORS origins alongside the CSRF ones,
  stale `ws://` CSP entries are gone, and the Tauri shell no longer
  navigates to a dead origin when the gateway fails to bind.
- **What the audits surfaced.** `POST /api/search/graph` was allow-listed
  but unreachable until now; `GET /api/ingestion/multimodal` was
  documented but unwired. Two golden families were added: a `recent_chat`
  family pinning the `build_recent_chat_context` the live `/chat` path
  actually calls — it caught a real divergence, where Python's `limit=0`
  tail slice keeps everything and Rust returned empty (**Python is the
  reference; Rust was fixed**) — plus `document_targets` and
  `agent_profiles` helper goldens, 97 rows. The corpus stands at **251
  golden files**.
- **The boundaries, stated plainly.** The native lanes are an
  open-posture, single-local-owner surface (a closed or unknown posture
  answers 401). The recent-chat context seam is still owned by the ad-hoc
  prepends in `/chat` rather than the assembler — wiring it changes the
  shape of a live prompt, which is a minor-release item, not something to
  ship quietly here. `workspace_scope_from_request` itself remains
  lenient for its two remaining callers. And the proxy hop deliberately
  carries no request timeout: a blanket timeout would kill long-lived SSE.

The floor holds: **7,022 Python tests (11 skipped) at 100.00% statement
and branch coverage — 40,307 statements, 10,970 branches, `fail_under=100`
with the pragma budget untouched — 1,761 frontend tests across 101 files
at 100% on all four metrics, and 760 Rust workspace tests**, with the
OpenAPI schema regenerated at 421 paths and the whole suite re-verified in
a fresh-resolve Python 3.11 environment.

Release notes: [RELEASE.md](RELEASE.md) · Full history: [docs/CHANGELOG.md](docs/CHANGELOG.md)

Expected artifacts for 11.5.2 release must use exact filenames:

- `dist/ltcai-11.5.2-py3-none-any.whl`
- `dist/ltcai-11.5.2.tar.gz`
- `ltcai-11.5.2.tgz`
- `dist/ltcai-11.5.2.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_11.5.2_aarch64.dmg`

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
| 11.5.2 | Tight Ship |
| 11.5.1 | Rust Full Loop |
| 11.5.0 | Rust Complete |
| 11.4.0 | Rust Foundation |
| 11.3.0 | Time Remembers |
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
