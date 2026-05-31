# Lattice AI — VS Code Extension

**AI Workspace OS coding assistant** — Apple Silicon MLX · OpenAI · Groq · MCP · Graph RAG · zero telemetry

[![VS Code Marketplace](https://img.shields.io/badge/VS%20Code%20Marketplace-Install-blue?logo=visualstudiocode)](https://marketplace.visualstudio.com/items?itemName=parktaesoo.ltcai)
[![Open VSX](https://img.shields.io/open-vsx/v/parktaesoo/ltcai?label=Open%20VSX)](https://open-vsx.org/extension/parktaesoo/ltcai)
[![License](https://img.shields.io/badge/license-MIT-green)](../LICENSE)

Connects to a running [Lattice AI](https://github.com/TaeSooPark-PTS/LatticeAI) server and brings local/cloud AI directly into VS Code, Cursor, and VSCodium.

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| **💬 Chat panel** | Talk to local MLX models (Gemma, Llama, Phi, Mistral) or cloud (GPT-4o, Claude, Groq) |
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

**Local (Apple Silicon MLX + cross-platform local servers):**
- `mlx-community/Qwen3-VL-4B-Instruct-4bit` — Multimodal low-spec default
- `mlx-community/Qwen3-VL-8B-Instruct-4bit` — Multimodal balanced default
- `mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit` — Large multimodal model
- `mlx-community/gemma-4-26b-a4b-it-4bit` — Strong all-round multimodal model
- `mlx-community/Llama-3.1-8B-Instruct-4bit` — Balanced general model
- `mlx-community/Phi-4-mini-instruct-4bit` — Lightweight coding model
- `mlx-community/Mistral-7B-Instruct-v0.3-4bit` — Apache-licensed general model
- Ollama / LM Studio / vLLM / llama.cpp models on Windows and Linux

**Cloud (any platform):**
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
| Local model (offline) | ✅ | ❌ | ✅ | ✅ |
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
