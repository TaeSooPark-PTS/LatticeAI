# Lattice AI — VS Code Extension

**Local & cloud AI coding assistant** — Apple Silicon MLX · OpenAI · Groq · MCP · Graph RAG · zero telemetry

[![VS Code Marketplace](https://img.shields.io/badge/VS%20Code%20Marketplace-Install-blue?logo=visualstudiocode)](https://marketplace.visualstudio.com/items?itemName=parktaesoo.ltcai)
[![Open VSX](https://img.shields.io/open-vsx/v/parktaesoo/ltcai?label=Open%20VSX)](https://open-vsx.org/extension/parktaesoo/ltcai)
[![License](https://img.shields.io/badge/license-MIT-green)](../LICENSE)

Connects to a running [Lattice AI](https://github.com/TaeSooPark-PTS/LatticeAI) server and brings local/cloud AI directly into VS Code, Cursor, and VSCodium.

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| **💬 Chat panel** | Talk to local MLX models (Gemma, Qwen, DeepSeek) or cloud (GPT-4o, Claude, Groq) |
| **✏️ Edit Selection** | Rewrite selected code with AI (`Cmd+Shift+E`) |
| **🔍 Explain Selection** | Get a plain-English explanation of selected code |
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
| Lattice AI: Generate Terminal Command | Right-click menu | Generate shell command |
| Lattice AI: Save to Knowledge Garden | Right-click menu | Save snippet/note |

---

## 🧠 Supported Models

**Local (Apple Silicon only):**
- `mlx-community/gemma-4-26b-a4b-it-4bit` — Best all-round (32GB Mac)
- `mlx-community/Qwen2.5-Coder-32B-Instruct-4bit` — Best for coding
- `mlx-community/DeepSeek-R1-0528-4bit` — Best for reasoning
- Any MLX-compatible model from Hugging Face

**Cloud (any platform):**
- OpenAI (GPT-4o, GPT-4o-mini, o3, o4-mini)
- Groq (Llama 3.3, DeepSeek-R1, Gemma 2)
- OpenRouter / Together AI
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
