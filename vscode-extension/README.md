[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./README.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](../docs/ko/VSCODE_EXTENSION.md)

# Lattice AI — VS Code Extension

**Extension for the local-first Digital Brain that keeps your knowledge durable across any AI model.**

[![VS Code Marketplace](https://img.shields.io/badge/VS%20Code%20Marketplace-Install-blue?logo=visualstudiocode)](https://marketplace.visualstudio.com/items?itemName=parktaesoo.ltcai)
[![Open VSX](https://img.shields.io/open-vsx/v/parktaesoo/ltcai?label=Open%20VSX)](https://open-vsx.org/extension/parktaesoo/ltcai)
[![License](https://img.shields.io/badge/license-MIT-green)](../LICENSE)

Lattice AI connects VS Code, Cursor, and VSCodium to your local-first Digital
Brain. Use it to send files into durable Brain context, work with Brain-backed
chat, run model workflows, and trigger coding actions from the editor.

## Current Release

**13.7.1 — Living Synthesis** introduces a zero-TS5 native TypeScript 7 (`tsgo`) OpenAPI declaration generator, domain-modularized Python runtime unit tests, cross-platform one-click Qwen3.8 27B 4-bit setup across macOS (`omlx`), Windows (`winget` + `pip`), and Linux, the model-agnostic **Universal Adaptive Agent Harness** (`probe.rs`, `ui_resolve.rs`, `rag_grounding.rs`), and **100% EN ↔ KO bilingual documentation parity** alongside 13.1.0–13.3.1 capabilities (two-phase async semantic incremental chunk sync, claim-verification & epistemic cards in `AnswerProof.tsx` / `BrainIntelligencePanel.tsx`, interactive Knowledge Graph neighborhood & 2-hop synthesis, contradiction inspector, Chat Save-to-Brain & inline proposals, live index drain, cooperative download cancel, Command Palette KG focus, and reactive workspace switching at 100% Vitest coverage). Full disclosure is in `docs/releases/RELEASE_NOTES_v13.7.1.md`.

**13.0.0 — Sovereign Synthesis** enforces cross-domain decoupling (`lattice_core::redact`, `lattice_agent::seam`, `domain_boundaries.rs`) and unified Rust server i18n (`lattice_core::messages`), adds 2-Hop Knowledge Graph reasoning chains, bridge concepts & contradiction synthesis (`reasoning_paths.rs`, `MultiHopSynthesisView.tsx`), introduces single-transaction review store mutations, one-click reversible folder reorganization undo (`ReviewCard.tsx`) and durable conversation document sessions across restarts, and splits `frontend/src/api/client.ts` and `src-tauri/src/backend.rs` at 100% Vitest coverage. Full disclosure is in `docs/releases/RELEASE_NOTES_v13.0.0.md`.

**The extension surface is unchanged apart from its version.** Sync, status, recall, staged proposals, `POST /agent` with `stream:true`, and `strict` / `trusted` / `bypass` travel over the wire as before.

**The extension surface is unchanged apart from its version.** Sync, status, recall, staged proposals, `POST /agent` with `stream:true`, and `strict` / `trusted` / `bypass` travel over the wire as before — the crate regrouping renamed files, not routes. Cloud escalation and MCP live on the host, not in the editor.

The extension connects your editor to the same local-first Digital Brain, explicit consent gates, replaceable model workflow (`MLX-VLM` / `llama.cpp` / `Ollama` / `LM Studio` / optional cloud), and proposal-first Review Center used by the desktop and web app.

## Quick Start

Install and start the Lattice AI workspace:

```bash
pip install ltcai
LTCAI
```

For Apple Silicon local model support:

```bash
pip install "ltcai[local]"
```

Then install the extension:

- [VS Code Marketplace: parktaesoo.ltcai](https://marketplace.visualstudio.com/items?itemName=parktaesoo.ltcai)
- [Open VSX: parktaesoo.ltcai](https://open-vsx.org/extension/parktaesoo/ltcai)

The extension auto-connects to `http://localhost:4825`.

## What It Adds

| Area | Description |
| --- | --- |
| Chat panel | Work with local or cloud models using workspace and graph context |
| Current file actions | Ask, edit, explain, refactor, and generate tests from the active file |
| Send To Lattice | Send the current file or selection into workspace workflows |
| Sync status | See whether the editor is connected, indexing, synced, or offline |
| Model workflows | Load, switch, and use model choices managed by Lattice AI |
| Knowledge Graph | Use graph-backed context from files, notes, screenshots, and conversations |
| Multi-agent workflow | Start planner/executor/reviewer style coding workflows |
| Local-first workspace | Keep personal work local while supporting organization workspace patterns |
| Native v4 app | Use `/app` as the primary product shell |

## Commands

| Command | Shortcut | Description |
| --- | --- | --- |
| Lattice AI: Open Chat | `Cmd+Shift+A` | Open the workspace chat panel |
| Lattice AI: Edit Selection | `Cmd+Shift+E` | Rewrite selected code |
| Lattice AI: Load Model | `Cmd+Shift+M` | Pick a local or cloud model |
| Lattice AI: Explain Selection | Right-click menu | Explain selected code |
| Lattice AI: Refactor Selection | Command palette | Refactor selected code |
| Lattice AI: Generate Tests | Command palette | Generate focused tests |
| Lattice AI: Send To Lattice | Command palette | Record file context in Lattice AI |
| Lattice AI: Ask About Current File | Command palette | Ask with current file context |
| Lattice AI: Show Sync Status | Command palette / status bar | Inspect the editor-to-app bridge state |
| Lattice AI: Generate Terminal Command | Right-click menu | Generate a shell command |
| Lattice AI: Save to Knowledge Garden | Right-click menu | Save a snippet or note |

## Model Workflow

Lattice AI supports local and cloud model choices:

- MLX-VLM on Apple Silicon for current multimodal local models, with MLX-LM
  retained as a text fallback only for standard Gemma 4 metadata.
- LM Studio, vLLM, llama.cpp, and Ollama-compatible local paths.
- OpenAI, OpenRouter, Groq, Together AI, and OpenAI-compatible endpoints.
- Model cards disclose maker country, maker company, run mode, internet usage,
  model name, HF verification status, download/load strategy, and hardware fit.

## Settings

| Setting | Default | Description |
| --- | --- | --- |
| `ltcai.serverUrl` | `http://localhost:4825` | Lattice AI workspace URL |
| `ltcai.autoLoadModel` | `false` | Load the default model on extension activation |
| `ltcai.defaultModel` | empty | Optional default model id |

Remote or tunnel server example:

```json
{
  "ltcai.serverUrl": "https://your-server.example"
}
```

## Links

- [Project README](https://github.com/TaeSooPark-PTS/LatticeAI)
- [PyPI](https://pypi.org/project/ltcai/)
- [npm](https://www.npmjs.com/package/ltcai)
- [GitHub Releases](https://github.com/TaeSooPark-PTS/LatticeAI/releases)
- [Changelog](https://github.com/TaeSooPark-PTS/LatticeAI/blob/main/docs/CHANGELOG.md)
- [Security Policy](https://github.com/TaeSooPark-PTS/LatticeAI/blob/main/SECURITY.md)

## License

MIT — [TaeSoo Park](https://github.com/TaeSooPark-PTS)
