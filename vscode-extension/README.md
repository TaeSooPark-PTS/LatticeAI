# Lattice AI — VS Code Extension

**AI Knowledge OS coding assistant** — Knowledge Graph · multimodal files · source-disclosed models · zero telemetry

[![VS Code Marketplace](https://img.shields.io/badge/VS%20Code%20Marketplace-Install-blue?logo=visualstudiocode)](https://marketplace.visualstudio.com/items?itemName=parktaesoo.ltcai)
[![Open VSX](https://img.shields.io/open-vsx/v/parktaesoo/ltcai?label=Open%20VSX)](https://open-vsx.org/extension/parktaesoo/ltcai)
[![License](https://img.shields.io/badge/license-MIT-green)](../LICENSE)

Connects to a running [Lattice AI](https://github.com/TaeSooPark-PTS/LatticeAI) server and brings Knowledge Graph workflows directly into VS Code, Cursor, and VSCodium.

---

## Current Release

**2.2.0 — Multimodal-First Knowledge OS Release.** The server now centers
multimodal file ingestion, source-disclosed model selection, Gemma-4-first local
recommendations, and Knowledge Graph workflows. The extension command surface
remains backward compatible; `Send To Lattice` continues to feed Workspace OS
and graph workflows.

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| **💬 Chat panel** | Work with multimodal models and graph context from the Lattice AI server |
| **✏️ Edit Selection** | Rewrite selected code with AI (`Cmd+Shift+E`) |
| **🔍 Explain Selection** | Get a plain-English explanation of selected code |
| **🧩 Refactor Selection** | Generate a reviewable diff for selected code |
| **🧪 Generate Tests** | Create focused tests from a selection or current file |
| **📤 Send To Lattice** | Send the current file/selection into Workspace OS workflows |
| **📄 Ask About Current File** | Ask with the active file attached as context |
| **⚡ Generate Command** | Describe a task → get a shell command |
| **🕸️ Graph RAG** | Chat history & docs auto-indexed as a knowledge graph |
| **🌱 Knowledge Garden** | Save snippets/notes to `~/.ltcai-brain/` |
| **🔌 MCP Tools** | Use any MCP server tool directly from the chat panel |
| **🔒 Zero telemetry** | All data stays local (`~/.ltcai/`) |

---

## 🚀 Quick Start

**1. Install the Lattice AI server** (one-time):

```bash
pip install ltcai
# With Apple Silicon local models:
pip install "ltcai[local]"
```

**2. Start the server:**

```bash
LTCAI
# → http://localhost:4825
```

**3. Open VS Code** → `Cmd+Shift+A` → Chat starts immediately.

> The extension auto-connects to `http://localhost:4825`. No configuration needed for local use.

---

## ⌨️ Commands & Shortcuts

| Command | Shortcut | Description |
|---------|----------|-------------|
| Lattice AI: Open Chat | `Cmd+Shift+A` | Open chat panel |
| Lattice AI: Edit Selection | `Cmd+Shift+E` | Rewrite selected code |
| Lattice AI: Load / Switch Model | `Cmd+Shift+M` | Pick a model to load |
| Lattice AI: Explain Selection | Right-click menu | Explain selected code |
| Lattice AI: Refactor Selection | Command palette | Refactor selected code with a diff preview |
| Lattice AI: Generate Tests | Command palette | Generate tests for the selection or current file |
| Lattice AI: Send To Lattice | Command palette | Record the file/selection in Workspace OS |
| Lattice AI: Ask About Current File | Command palette | Ask chat with current file context |
| Lattice AI: Generate Terminal Command | Right-click menu | Generate shell command |
| Lattice AI: Save to Knowledge Garden | Right-click menu | Save snippet/note |

---

## 🧠 Supported Models

**Runs on this computer (MLX-VLM + cross-platform multimodal paths):**
- `mlx-community/gemma-4-12b-it-4bit` — Gemma 4 default multimodal recommendation
- `mlx-community/gemma-4-31b-it-4bit` — Larger Gemma 4 multimodal model
- `mlx-community/Qwen3-VL-4B-Instruct-4bit` — Multimodal low-spec default
- `mlx-community/Qwen3-VL-8B-Instruct-4bit` — Multimodal balanced default
- `mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit` — Large multimodal model
- `mlx-community/Llama-4-Scout-17B-16E-Instruct-4bit` — Meta multimodal option
- LM Studio / vLLM / llama.cpp options on Windows and Linux
- Ollama remains selectable when the user prefers that path

**Internet-connected options:**
- OpenAI (GPT-5.5, GPT-5.4-mini, GPT-4o)
- OpenRouter (Claude Opus 4.7, Sonnet 4.6, Haiku 4.5, Qwen3-VL)
- Groq / Together AI
- Any OpenAI-compatible endpoint

---

## ⚙️ Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `ltcai.serverUrl` | `http://localhost:4825` | Lattice AI server URL |
| `ltcai.language` | `ko` | UI language (`ko` / `en`) |

For remote/tunnel server:
```json
{
  "ltcai.serverUrl": "https://your-server.trycloudflare.com"
}
```

---

## 🆚 vs. Other AI Extensions

| | Lattice AI | GitHub Copilot | Continue.dev | Cursor |
|---|:---:|:---:|:---:|:---:|
| Runs on this computer | ✅ | ❌ | ✅ | ✅ |
| Zero telemetry | ✅ | ❌ | ✅ | ❌ |
| Graph RAG | ✅ | ❌ | ❌ | ❌ |
| MCP tool support | ✅ | ❌ | ✅ | ✅ |
| Web UI included | ✅ | ❌ | ❌ | ❌ |
| Telegram / Discord bot | ✅ | ❌ | ❌ | ❌ |
| Free | ✅ | ❌ | ✅ | partial |

---

## 🔗 Links

- [Server GitHub](https://github.com/TaeSooPark-PTS/LatticeAI)
- [PyPI](https://pypi.org/project/ltcai/)
- [Changelog](https://github.com/TaeSooPark-PTS/LatticeAI/blob/main/docs/CHANGELOG.md)
- [Security Policy](https://github.com/TaeSooPark-PTS/LatticeAI/blob/main/SECURITY.md)
- [Report an Issue](https://github.com/TaeSooPark-PTS/LatticeAI/issues)

---

## 📄 License

MIT — [TaeSoo Park](https://github.com/TaeSooPark-PTS)
