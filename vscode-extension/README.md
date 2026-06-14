# Lattice AI — VS Code Extension

**Local-first AI workspace extension for Lattice AI v5 Living Brain, hybrid search, model workflows, and coding actions.**

[![VS Code Marketplace](https://img.shields.io/badge/VS%20Code%20Marketplace-Install-blue?logo=visualstudiocode)](https://marketplace.visualstudio.com/items?itemName=parktaesoo.ltcai)
[![Open VSX](https://img.shields.io/open-vsx/v/parktaesoo/ltcai?label=Open%20VSX)](https://open-vsx.org/extension/parktaesoo/ltcai)
[![License](https://img.shields.io/badge/license-MIT-green)](../LICENSE)

Lattice AI connects VS Code, Cursor, and VSCodium to a running local-first AI
workspace. Use it to send files into workspace context, work with Brain-backed
chat, run model workflows, and trigger coding actions from the editor.

## Current Release

**5.0.0 — Multilingual Brain Foundation Release.** The desktop workspace keeps
the v4 Brain Core extraction, AgentRuntime, ToolRegistry, graph, and Admin
Console foundations while adding Korean/English language choice across first-run
onboarding and the Brain surface. The extension connects to the same local
workspace APIs and remains compatible with the separated user/admin product
model.

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
| Lattice AI: Generate Terminal Command | Right-click menu | Generate a shell command |
| Lattice AI: Save to Knowledge Garden | Right-click menu | Save a snippet or note |

## Model Workflow

Lattice AI supports local and cloud model choices:

- MLX-VLM on Apple Silicon for current multimodal local models, with MLX-LM
  retained as a text fallback only for standard Gemma 4 metadata.
- LM Studio, vLLM, llama.cpp, and Ollama-compatible local paths.
- OpenAI, OpenRouter, Groq, Together AI, and OpenAI-compatible endpoints.
- Model cards disclose maker country, maker company, run mode, internet usage,
  and model name.

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
