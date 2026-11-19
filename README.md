<div align="center">

# Lattice AI

**Your model is the voice you use today. Your Brain is the asset you keep.**<br/>
*모델은 갈아타도, 내 지식은 내 컴퓨터에 남는 로컬 우선 AI 브레인.*

[![PyPI Version](https://img.shields.io/pypi/v/ltcai?label=PyPI)](https://pypi.org/project/ltcai/)
[![npm Version](https://img.shields.io/npm/v/ltcai?label=npm)](https://www.npmjs.com/package/ltcai)
[![VS Code Marketplace Version](https://vsmarketplacebadges.dev/version-short/parktaesoo.ltcai.svg)](https://marketplace.visualstudio.com/items?itemName=parktaesoo.ltcai)
[![Open VSX Version](https://img.shields.io/open-vsx/v/parktaesoo/ltcai?label=Open%20VSX)](https://open-vsx.org/extension/parktaesoo/ltcai)
[![CI Status](https://github.com/TaeSooPark-PTS/LatticeAI/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/TaeSooPark-PTS/LatticeAI/actions/workflows/ci.yml)
[![CD Status](https://github.com/TaeSooPark-PTS/LatticeAI/actions/workflows/publish.yml/badge.svg)](https://github.com/TaeSooPark-PTS/LatticeAI/actions/workflows/publish.yml)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

<br/>

![v13.3.1 Living Brain walkthrough](output/release/v13.3.1/gifs/v13.3.1-living-brain-walkthrough.gif)

</div>

Lattice AI is a **local-first Digital Brain** that unifies your conversations, files, folders, notes, and web captures into one private Knowledge Graph you own. Local inference is the default; cloud models are strictly optional, and nothing leaves your machine without explicit consent.

대화·파일·폴더·노트·웹페이지가 모두 내 컴퓨터 안의 단일 지식 그래프로 축적됩니다. 기본값은 100% 로컬이며, 클라우드 연결은 사용자가 직접 선택한 경우에만 동작합니다.

---

## Why Lattice AI

- **Local-First Digital Brain & Hardware Auto-Fit** — All memories, provenance, and graph relationships live locally in SQLite and portable encrypted `.latticebrain` archives. Fresh installs work offline out-of-the-box with deterministic `lattice-local-hash-v1` lexical/hash embeddings and upgrade seamlessly to hardware-sized local **EmbeddingGemma 2** (`768d`) or configured semantic providers (`mlx-community`, `ggml-org` GGUF, `onnx-community`) with **live SSE download progress (`MB/s` & `ETA`)**.
- **Hybrid GraphRAG (BM25F + Vector + 2-Hop Knowledge Graph RRF)** — Combines field-weighted Okapi BM25F (with Korean particle stripping, CJK bigrams, and identifier subwords), dense/subword vector recall, and 1-hop/2-hop Knowledge Graph structural expansion (`RELATES_TO`, `MENTIONS`, `PART_OF`, `[[wikilinks]]`, `#tags`) via Reciprocal Rank Fusion — backed by graph-aware agent skills and MCP search tools.
- **Proposal-First Safe Autonomy & Review Center** — Additive creations run with minimal friction; edits or deletions of existing user files and cloud-extracted memories stage as hash-verified **Review Center** proposals with side-by-side diffs before applying. Even small 2B-class local models can safely act via guided prompts, and workflow/agent simulation without a loaded LLM is explicitly **deterministic** and **LLM-free** (it does not call a model).
- **One Brain Across Every Surface** — Use the **macOS Desktop App (`.dmg`)**, the **Web Studio (`/app`)**, the **VS Code / Cursor / Open VSX Extension**, the **Browser Extension**, or the **`LTCAI` CLI** against the same local Brain.

---

## Quick Start

### 1. Desktop App (macOS)

Download `Lattice AI_13.3.1_aarch64.dmg` from [GitHub Releases](https://github.com/TaeSooPark-PTS/LatticeAI/releases) and launch the app.

### 2. One-Command CLI & Web Studio

```bash
pip install ltcai          # or: npm install -g ltcai
LTCAI                      # opens the Web Studio at http://127.0.0.1:4825/app
```

- **Apple Silicon local models (MLX)**: `pip install "ltcai[local]"`
- **Pointer automation tools (optional)**: `pip install "ltcai[pointer]"`

### 3. Editor Extension (VS Code · Cursor · Antigravity)

Install [`parktaesoo.ltcai`](https://marketplace.visualstudio.com/items?itemName=parktaesoo.ltcai) from [VS Code Marketplace](https://marketplace.visualstudio.com/items?itemName=parktaesoo.ltcai) or [Open VSX](https://open-vsx.org/extension/parktaesoo/ltcai) to sync files, ask Brain-grounded questions, and review proposals directly inside your editor.

### First Five Minutes

1. **Wake your Brain** and confirm your local owner profile.
2. **Pick a recommended local model & embedder** sized for your hardware (with real-time SSE download progress).
3. **Capture a source** — drop a folder, document, note, or web page.
4. **Ask in your own words** and inspect the grounded citations, Knowledge Graph connections, and Review Center diffs.

---

## Product Tour

| Onboarding & Hardware Fit | Live Model Library & SSE Progress |
| --- | --- |
| ![Recommended Models](output/release/v13.3.1/screenshots/02-recommended-models.png) | ![Install & Load Progress](output/release/v13.3.1/screenshots/03-install-load-progress.png) |
| **Brain Chat Home** — grounded answers with source proof | **Memory Graph** — explore 2-hop concept & file links |
| ![Brain Chat Home](output/release/v13.3.1/screenshots/04-brain-chat-home.png) | ![Memory Graph](output/release/v13.3.1/screenshots/05-memory-graph.png) |
| **Capture & Knowledge Journey** — watch material become memory | **Review Center** — inspect diffs before any edit applies |
| ![Material to Memory](output/release/v13.3.1/screenshots/11-knowledge-journey.png) | ![Review Center](output/release/v13.3.1/screenshots/12-review-center.png) |
| **Brain Chronicle** — growth curve, heatmap & daily story | **Autonomy Dial & System** — plain-language permission controls |
| ![Brain Chronicle](output/release/v13.3.1/screenshots/13-chronicle.png) | ![Settings](output/release/v13.3.1/screenshots/08-system.png) |

Full visual index: [output/release/v13.3.1/SCREENSHOT_INDEX.md](output/release/v13.3.1/SCREENSHOT_INDEX.md) (login & library views: [01-login.png](output/release/v13.3.1/screenshots/01-login.png), [06-capture.png](output/release/v13.3.1/screenshots/06-capture.png), [07-model-library.png](output/release/v13.3.1/screenshots/07-model-library.png)).

---

## Documentation & Architecture Hub

| Area | Document | What You'll Find |
| --- | --- | --- |
| **Getting Started** | [docs/ONBOARDING.md](docs/ONBOARDING.md) | 5-minute product flow and first-run guarantees |
| **Product Philosophy** | [docs/WHY_LATTICE.md](docs/WHY_LATTICE.md) | Why the Brain outlives any individual AI model |
| **System Architecture** | [ARCHITECTURE.md](ARCHITECTURE.md) | `lattice-host` Rust server, single `GraphWriter`, and 20-route Python AI worker |
| **Feature Matrix** | [FEATURE_STATUS.md](FEATURE_STATUS.md) | Current shipped capabilities and honest system boundaries |
| **Operations & Self-Hosting** | [docs/OPERATIONS.md](docs/OPERATIONS.md) | Storage layout, `.latticebrain` backup/restore, and public mode checklist |
| **Enterprise & Open Core** | [docs/ENTERPRISE.md](docs/ENTERPRISE.md) | Open-core capability seam and [EDITION_STRATEGY.md](docs/EDITION_STRATEGY.md) |
| **Security & Privacy** | [SECURITY.md](SECURITY.md) · [PRIVACY.md](PRIVACY.md) | Threat model, loopback defaults, egress audit, and [TRUST_MODEL.md](docs/TRUST_MODEL.md) |
| **Contributor Guide** | [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Local build, Rust crate maps, quality gates, and [docs/ROADMAP.md](docs/ROADMAP.md) |
| **Releases & Changelog** | [RELEASE.md](RELEASE.md) · [docs/CHANGELOG.md](docs/CHANGELOG.md) | Per-release notes ([`docs/releases/`](docs/releases/)) and [RELEASE_NOTES.md](RELEASE_NOTES.md) |

---

## Current Release

The current release is **13.3.1 — Living Synthesis**:

- **Two-Phase Async Semantic Incremental Sync** — Split `incremental_sync` into `plan_incremental_sync` + async `/worker/embed` (`post_idempotent_compute`) + `commit_incremental_sync` (`watch_deliver.rs`), so editing a watched document under semantic embedding contracts only re-embeds changed chunks while reusing cached vectors for unchanged sections.
- **Claim Verification & Epistemic Consolidation UI (100% Vitest)** — Surfaced claim-level grounding telemetry (`claim_verification`: support %, precision %, numeric/polarity mismatches, superseded leaks, abstention) in `AnswerProof.tsx` and `entity_resolution` / `temporal_timeline` diagnostics in `BrainIntelligencePanel.tsx` with full `ko`/`en` localization.
- **Supply-Chain & Release Hardening** — Added weekly `.github/dependabot.yml` monitoring across Python, npm, Cargo, Tauri, and GitHub Actions while maintaining **0** non-test Rust `.unwrap()` calls and **0** hand-written production files over 800 lines.

<details>
<summary>Release notes & exact 13.3.1 distribution artifacts</summary>

Release notes: [docs/releases/RELEASE_NOTES_v13.3.1.md](docs/releases/RELEASE_NOTES_v13.3.1.md) · Guide: [RELEASE.md](RELEASE.md) · History: [docs/CHANGELOG.md](docs/CHANGELOG.md).

Expected artifacts for 13.3.1 must use exact filenames:

- `dist/ltcai-13.3.1-py3-none-any.whl`
- `dist/ltcai-13.3.1.tar.gz`
- `ltcai-13.3.1.tgz`
- `dist/ltcai-13.3.1.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_13.3.1_aarch64.dmg`

Do not use wildcard artifact uploads. Package registry publishing runs via `.github/workflows/publish.yml` (CD) when the GitHub Release is published.

</details>

---

## Honest Boundaries

- **Local by default**: SQLite is the live Brain store; cloud models (`cli_oauth` or `api_key` probed via `GET /models`), model downloads, Brain Network, and update checks require explicit user action.
- **Honest retrieval & simulation**: When no LLM is loaded, agent and workflow simulation is deterministic and LLM-free (it does not call a model) and is labeled as such. Exact brute-force vector search is the default (`LATTICEAI_VECTOR_INDEX=hnsw` opts into `hnsw+rescore` at 512+ vectors).
- **Safe deletion & signing**: Watch prune removes vanished files from the Knowledge Graph only after confirmation (never from disk). The macOS `.dmg` is ad-hoc signed, so first launch uses the standard Gatekeeper allow step. Remaining honest gaps are tracked in [docs/ROADMAP.md](docs/ROADMAP.md) and [FEATURE_STATUS.md](FEATURE_STATUS.md).

---

## Release History

Public release history in this repository covers `v12.0.0+` (the Rust-host + domain-grouped architecture). Full per-release notes live in [RELEASE_NOTES.md](RELEASE_NOTES.md) and [docs/releases/](docs/releases/).

| Version | Theme | Release Note |
| --- | --- | --- |
| 13.3.1 | Living Synthesis | [RELEASE_NOTES_v13.3.1.md](docs/releases/RELEASE_NOTES_v13.3.1.md) |
| 13.3.0 | Living Synthesis | [RELEASE_NOTES_v13.3.0.md](docs/releases/RELEASE_NOTES_v13.3.0.md) |
| 13.2.1 | Living Synthesis | [RELEASE_NOTES_v13.2.1.md](docs/releases/RELEASE_NOTES_v13.2.1.md) |
| 13.2.0 | Living Synthesis | [RELEASE_NOTES_v13.2.0.md](docs/releases/RELEASE_NOTES_v13.2.0.md) |
| 13.1.0 | Living Synthesis | [RELEASE_NOTES_v13.1.0.md](docs/releases/RELEASE_NOTES_v13.1.0.md) |
| 13.0.0 | Sovereign Synthesis | [RELEASE_NOTES_v13.0.0.md](docs/releases/RELEASE_NOTES_v13.0.0.md) |
| 12.10.0 | Grounded Self | [RELEASE_NOTES_v12.10.0.md](docs/releases/RELEASE_NOTES_v12.10.0.md) |
| 12.9.0 | Semantic Guard | [RELEASE_NOTES_v12.9.0.md](docs/releases/RELEASE_NOTES_v12.9.0.md) |
| 12.8.0 | Trusted Brain | [RELEASE_NOTES_v12.8.0.md](docs/releases/RELEASE_NOTES_v12.8.0.md) |
| 12.7.0 | Fast Index | [RELEASE_NOTES_v12.7.0.md](docs/releases/RELEASE_NOTES_v12.7.0.md) |
| 12.6.0 | Hybrid Graph | [RELEASE_NOTES_v12.6.0.md](docs/releases/RELEASE_NOTES_v12.6.0.md) |
| 12.5.0 | Deep Vector | [RELEASE_NOTES_v12.5.0.md](docs/releases/RELEASE_NOTES_v12.5.0.md) |
| 12.4.0 | Sharp Weight | [RELEASE_NOTES_v12.4.0.md](docs/releases/RELEASE_NOTES_v12.4.0.md) |
| 12.3.0 | Live Wire | [RELEASE_NOTES_v12.3.0.md](docs/releases/RELEASE_NOTES_v12.3.0.md) |
| 12.2.1 | True Count | [RELEASE_NOTES_v12.2.1.md](docs/releases/RELEASE_NOTES_v12.2.1.md) |
| 12.2.0 | Small Voice | [RELEASE_NOTES_v12.2.0.md](docs/releases/RELEASE_NOTES_v12.2.0.md) |
| 12.1.0 | Fast Path | [RELEASE_NOTES_v12.1.0.md](docs/releases/RELEASE_NOTES_v12.1.0.md) |
| 12.0.0 | Open House | [RELEASE_NOTES_v12.0.0.md](docs/releases/RELEASE_NOTES_v12.0.0.md) |
