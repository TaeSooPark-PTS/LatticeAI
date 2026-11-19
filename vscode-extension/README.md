# Lattice AI — VS Code Extension

**Extension for the local-first Digital Brain that keeps your knowledge durable across any AI model.**

[![VS Code Marketplace](https://img.shields.io/badge/VS%20Code%20Marketplace-Install-blue?logo=visualstudiocode)](https://marketplace.visualstudio.com/items?itemName=parktaesoo.ltcai)
[![Open VSX](https://img.shields.io/open-vsx/v/parktaesoo/ltcai?label=Open%20VSX)](https://open-vsx.org/extension/parktaesoo/ltcai)
[![License](https://img.shields.io/badge/license-MIT-green)](../LICENSE)

Lattice AI connects VS Code, Cursor, and VSCodium to your local-first Digital
Brain. Use it to send files into durable Brain context, work with Brain-backed
chat, run model workflows, and trigger coding actions from the editor.

## Current Release

**13.3.1 — Living Synthesis** adds interactive Knowledge Graph neighborhood & 2-hop reasoning synthesis (`neighborhood_synthesis.rs`, `GraphNodeInspector.tsx`, `MultiHopSynthesisView.tsx`), conflict-only graph filtering and one-click contradiction staging/resolution (`ContradictionInspector.tsx`), Chat **Save to Brain** promotion & inline staged proposal approval (`InlineStagedProposals.tsx`), live embedding drain progress & **Index now** controls (`POST /api/index/drain`), cooperative download cancellation & byte metrics, Command Palette Knowledge Graph node focus, and reactive workspace switching at 100% Vitest coverage. Full disclosure is in `docs/releases/RELEASE_NOTES_v13.3.1.md`.

**13.0.0 — Sovereign Synthesis** enforces cross-domain decoupling (`lattice_core::redact`, `lattice_agent::seam`, `domain_boundaries.rs`) and unified Rust server i18n (`lattice_core::messages`), adds 2-Hop Knowledge Graph reasoning chains, bridge concepts & contradiction synthesis (`reasoning_paths.rs`, `MultiHopSynthesisView.tsx`), introduces single-transaction review store mutations, one-click reversible folder reorganization undo (`ReviewCard.tsx`) and durable conversation document sessions across restarts, and splits `frontend/src/api/client.ts` and `src-tauri/src/backend.rs` at 100% Vitest coverage. Full disclosure is in `docs/releases/RELEASE_NOTES_v13.0.0.md`.

**12.10.0 — Grounded Self** adds deterministic agent transcript grounding & interactive citation proof badges, a Brain Self-Model panel with inline proposal approval/dismissal, Knowledge Graph `PART_OF` edge cascade parity across `edges` and `edges_v2`, cooperative `499` model download cancellation with automatic post-download semantic embedding transition & reindex drain, crate-internal `http_reply.rs` transport decoupling, 100% Python worker server-i18n lock (`6/6` routers), parallel `cargo nextest` execution, and chained `release.yml` → `publish.yml` CD publishing. Full disclosure is in `docs/releases/RELEASE_NOTES_v12.10.0.md`.

**12.9.0 — Semantic Guard** aligns query-time semantic embeddings with the active `EmbeddingIndexContract` across search/recall/chat, adds pure-Rust `NativeHnswIndex` & workspace/model-scoped Python HNSW sidecars, enforces fail-closed `local_only`/`cloud_allowed` network boundary & cloud context guards, hardens Tauri desktop / `LTCAI` CLI / host supervisor / multi-stage Docker lifecycles, streams live Workspace OS SSE events (`/api/realtime/stream`), and upgrades v2 `.latticebrain` archive KDF & CI gates. Full disclosure is in `docs/releases/RELEASE_NOTES_v12.9.0.md`.

**12.8.0 — Trusted Brain** unifies the `EmbeddingIndexContract` & knowledge lifecycle tracking, pushes workspace/lifecycle filtering before SQL `LIMIT` with aligned vector search & bounded sidecar caches, enforces fail-closed multi-user/workspace/tenant isolation across gateway routes, delivers AEAD-encrypted `.latticebrain` archive portability with honest `planned` PostgreSQL status, modularizes `Library`/`System`/`Capture` UI slices, and automates Apple Silicon DMG CI/CD releases. Full disclosure is in `docs/releases/RELEASE_NOTES_v12.8.0.md`.

**12.7.0 — Fast Index** adds a Pre-Tokenized BM25F Sidecar (`Bm25SidecarIndex`), SQLite `bm25_node_stats` persistence, and an inverted posting-list & GraphRAG adjacency cache for O(1) lookup without query-time re-tokenization, alongside a product-first `README.md` and `v12.0.0+` release-history polish. Full disclosure is in `docs/releases/RELEASE_NOTES_v12.7.0.md`.

**12.6.0 — Hybrid Graph** adds 3-way BM25F + Vector + Knowledge Graph RRF hybrid indexing & graph-aware agent skills, multi-scale subword `FallbackTextEmbedder`, unauthenticated EmbeddingGemma 2 public mirrors & quantized GGUF/MLX/ONNX fallbacks, real-time SSE download progress streaming (`MB/s` & `ETA`) in Onboarding and Model Library, Pillow 13+ cleanup, Ubuntu 24.04/26.04 `t64` CI resilience, and the modular `scripts/bump_version/` CLI. Full disclosure is in `docs/releases/RELEASE_NOTES_v12.6.0.md`.

**12.5.0 — Deep Vector** adds native Google EmbeddingGemma 2 support, hardware-aware PC-tailored embedding recommendations, Pillow 13+ flattened data compatibility, Node 24 native CI/CD, and automated OpenAPI specification and fixture synchronization. Full disclosure is in `docs/releases/RELEASE_NOTES_v12.5.0.md`.

**12.4.0 — Sharp Weight** keeps the One Door topology (422 operations / 41 families, 20-route worker), adds verified `mlx-community/Qwen3.8-27B-4bit`, `mlx-community/gemma-4-26B-A4B-it-qat-4bit`, and `mlx-community/gemma-4-26B-A4B-it-qat-OptiQ-4bit` local/cloud routes, and closes all Python (`pillow>=13,<14`) and npm dependency security advisories. Full disclosure is in `docs/releases/RELEASE_NOTES_v12.4.0.md`.

**12.3.0 — Live Wire** keeps the One Door topology (422 operations / 41 families, 20-route worker) and verifies Hugging Face GGUF quantization tags (`Q4_0` for Gemma 4, `MXFP4` for GPT-OSS 20B), `Qwen3.5-9B` / `Qwen3.6-35B-A3B` GGUF routes, auxiliary GGUF filtering + companion `--mmproj` download, small-model tool/MCP argument recovery, and native HTML structure inspection (`/tools/inspect_html`) + RFC 5987 UTF-8 download (`/tools/download`). Full disclosure is in `docs/releases/RELEASE_NOTES_v12.3.0.md`.

**12.2.1 — True Count** keeps the One Door topology (422 operations / 41 families, 20-route worker) and makes search, skip, watch, and small-model summaries count what is actually there. Full disclosure is in `docs/releases/RELEASE_NOTES_v12.2.1.md`.

**The extension surface is unchanged apart from its version.** Sync, status, recall, staged proposals, `POST /agent` with `stream:true`, and `strict` / `trusted` / `bypass` travel over the wire as before.

**12.0.0 — Open House** grouped the two largest crates by purpose (`lattice-agent` six groups, `lattice-platform` seven domains, all `git mv`) and closed four named gaps: restore is live without a restart, `/setup/install` installs on per-item consent, `POST /mcp` is inside the OpenAPI contract, and pointer tools are `pip install "ltcai[pointer]"`. Full disclosure of that release is in `docs/releases/RELEASE_NOTES_v12.0.0.md`.

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
