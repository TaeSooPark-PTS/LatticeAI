<div align="center">
  <img src="docs/images/logo.svg" alt="Lattice AI" width="120" />

  # Lattice AI

  **A local-first AI workspace** — Plan → Execute → Review with multiple LLMs,
  on top of a durable Knowledge Graph.

  Apple Silicon (MLX) local model management · Personal & Organization workspaces ·
  Multi-agent workflows · SSO for teams
</div>

---

Lattice AI turns your files, documents, images, screenshots, conversations,
decisions, notes, and work history into linked knowledge. AI then works on top of
that Knowledge Graph to advise, analyze, generate documents, and automate work.

It is built around a simple product rule:

> Do not control the user in the name of protection. Explain clearly, disclose
> sources and risks, then let the user decide.

## What Lattice AI is

Lattice AI is four things working together, not an operating system and not a
simple chat front-end:

- **Local-first AI Workspace** — runs on your machine first; your data and graph
  stay local by default.
- **AI Pipeline Platform** — a Plan → Execute → Review loop that can route across
  multiple LLMs for each stage.
- **Knowledge Graph Platform** — multimodal inputs become entities,
  relationships, evidence, and artifacts that outlive any single model.
- **Workspace Platform** — Personal and Organization workspaces, role-based
  access, and SSO for teams.

## Highlights

- **Multimodal ingestion** — accepts PDFs, Word, spreadsheets, slide decks,
  images, screenshots, notes, code, web content, conversations, and work logs
  without asking you to pre-convert them.
- **Knowledge Graph core** — extracts entities, relationships, evidence,
  decisions, and artifacts so your work memory survives model changes.
- **Multi-agent workflow** — agents hand off work with structured context
  packets, review/retry loops, and replayable timelines.
- **Local model management** — MLX-VLM on Apple Silicon, with current-generation
  multimodal model recommendations based on your CPU, GPU, RAM, storage, and OS.
- **Source disclosure** — every recommended model shows its facts in plain
  language before use: maker country, maker company, run mode (local/cloud),
  internet requirement, and model name.
- **Basic / Advanced / Admin modes** — the same features everywhere; the modes
  differ only in how much is explained. Admin mode adds authority
  (user management, permissions, audit logs, org/security policy, model approval).

## v2.2.1 — Frontend & UX overhaul

v2.2.1 is a UX-focused release. No features were removed; the interface was
re-laid out and re-themed.

- **Responsive, mobile-first UI** — phone, tablet, laptop, desktop, ultrawide,
  and 4K. Content is re-laid out for each size, never hidden.
- **Light / Dark themes via design tokens** — a single source of truth in
  [`static/css/tokens.css`](static/css/tokens.css). `:root` holds light values,
  `[data-lt-theme="dark"]` holds dark values. The theme toggle, OS preference
  detection, and persistence live in
  [`static/scripts/ux.js`](static/scripts/ux.js).
- **Accessibility** — 44px touch targets, `:focus-visible` rings, a
  keyboard-safe chat composer (uses `visualViewport` insets), iOS no-zoom inputs,
  and reduced-motion support.
- **Knowledge Graph UX** — responsive canvas that re-fits on resize, zoom
  buttons, fullscreen, minimap, relationship filtering, a mobile graph↔card
  view, and a theme-aware palette.
- **Admin UX** — wide tables reflow to cards on mobile, with larger touch targets
  and full dark/light support.
- **File UX** — drag & drop and screenshot paste to attach. Model cards explain
  country, company, run mode, and internet use in plain language.

## Knowledge Graph flow

```text
files / documents / images / conversations / work history
  -> multimodal understanding
  -> entity extraction
  -> relationship extraction
  -> evidence storage
  -> Knowledge Graph update
  -> advice / analysis / document generation / automation
```

The graph preserves your work memory even when the model changes.

## Local model policy

Lattice AI recommends current-generation **multimodal** models for local use and
keeps text-only recommendation paths out of the product:

| Family | Default role | Example current recommendation |
| --- | --- | --- |
| Gemma 4 | Default Google multimodal family | `mlx-community/gemma-4-12b-it-4bit` |
| Gemma 4 large | Higher-quality local multimodal work | `mlx-community/gemma-4-31b-it-4bit` |
| Qwen3-VL | Smaller, balanced multimodal options | `mlx-community/Qwen3-VL-4B-Instruct-4bit` |
| Llama 4 | Meta multimodal option | `mlx-community/Llama-4-Scout-17B-16E-Instruct-4bit` |

Low-spec machines use smaller or quantized multimodal models rather than older
text-only models. See [MODEL_POLICY.md](MODEL_POLICY.md) for the full policy.

## Requirements

- Python **3.11+**
- macOS, Windows, or Linux for the workspace server
- Apple Silicon for local MLX-VLM model execution (optional; cloud models work
  without it)

## Quick start

Lattice AI is a Python + FastAPI app. The CLI entry point is `ltcai_cli.py`.

```bash
npm install        # installs the npm package and dev tooling
npm run dev        # runs: python3 ltcai_cli.py --reload
```

Then open:

```text
http://127.0.0.1:4825
```

Prefer Python directly?

```bash
python3 ltcai_cli.py
# host/port: --host 127.0.0.1 --port 4825
```

For local model execution on Apple Silicon, install the optional extra:

```bash
pip install "ltcai[local]"   # adds mlx-vlm
```

Useful commands:

```bash
npm test                # python3 -m pytest tests/ -v
npm run check:python    # py_compile across core modules
npm run build           # python3 -m build
```

## VS Code extension

A companion VS Code extension lives in [`vscode-extension/`](vscode-extension).

```bash
cd vscode-extension
npm install
npm run build
npm run package:vsix
```

## Build & packaging

Lattice AI ships as a Python package (`ltcai`), an npm package (`ltcai`), and a
VS Code extension.

```bash
python3 -m build        # Python wheel + sdist
npm pack                # npm tarball
```

Publishing is intentionally manual — no glob uploads. See [RELEASE.md](RELEASE.md)
for the exact, version-scoped publish steps for PyPI, npm, the VS Code
Marketplace, and Open VSX.

## Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) — system shape and runtime policy
- [docs/architecture.md](docs/architecture.md) — full architecture reference
- [PROJECT_PRINCIPLES.md](PROJECT_PRINCIPLES.md) — product principles
- [AI_PHILOSOPHY.md](AI_PHILOSOPHY.md) — how AI is used here
- [MODEL_POLICY.md](MODEL_POLICY.md) — local model recommendation policy
- [KNOWLEDGE_GRAPH.md](KNOWLEDGE_GRAPH.md) — graph model and behavior
- [docs/MULTI_AGENT_RUNTIME.md](docs/MULTI_AGENT_RUNTIME.md) — multi-agent runtime
- [docs/WORKFLOW_DESIGNER.md](docs/WORKFLOW_DESIGNER.md) — workflow designer
- [docs/REALTIME_COLLABORATION.md](docs/REALTIME_COLLABORATION.md) — realtime collaboration
- [docs/ENTERPRISE.md](docs/ENTERPRISE.md) — Organization workspaces & SSO (Okta / Microsoft Entra ID)
- [docs/security-model.md](docs/security-model.md) — security model
- [docs/PLUGIN_SDK.md](docs/PLUGIN_SDK.md) — plugin SDK
- [RELEASE_NOTES.md](RELEASE_NOTES.md) · [docs/CHANGELOG.md](docs/CHANGELOG.md)

## Release history

| Version | Theme |
| --- | --- |
| **2.2.1** | Frontend & UX overhaul — responsive, design-token theming, accessibility, graph/admin UX |
| 2.2.0 | Multimodal-first Knowledge Graph, source disclosure, Gemma-4 recommendation policy |
| 2.1.0 | Agent platform maturity |
| 2.0.0 | Agentic workspace platform |
| 1.7.0 | Graph and collaboration |
| 1.6.0 | Product experience deepening |

## License

MIT
