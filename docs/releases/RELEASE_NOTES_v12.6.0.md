# Lattice AI 12.6.0 — Hybrid Graph

> **Status: canonical** — current release note.

2026-11-15

12.5.0 Deep Vector brought native Google EmbeddingGemma 2 embeddings and hardware-aware recommendations. **12.6.0 Hybrid Graph** upgrades the retrieval and embedding stack end-to-end: genuine **3-Way BM25F + Vector + Knowledge Graph RRF Hybrid Indexing**, graph-aware agent skills and MCP search tools, a **multi-scale subword `FallbackTextEmbedder`**, **unauthenticated EmbeddingGemma 2 public mirrors & quantized GGUF/MLX/ONNX fallbacks**, **real-time SSE download progress streaming with speed (`MB/s`) and `ETA`**, **Pillow 13+ cleanup**, **Ubuntu 24.04/26.04 `t64` & Chromium CI resilience**, and a **modular `scripts/bump_version/` CLI**.

문은 **422 operations / 41 families**, 워커는 **20 routes**. 쓰기는 계속 `GraphWriter` 하나입니다.

## What landed

- **3-Way BM25F + Vector + Knowledge Graph RRF Hybrid Indexing & Graph Skills.**
  - `rust/lattice-retrieval/src/bm25/`, `service_hybrid.rs`, `hybrid.rs`, and `search_api/handlers.rs`: Added calibrated field-weighted Okapi **BM25F** (`k1 = 1.2`, `b = 0.75`, boosted `title` / `tags_headings` over `body` / `metadata`, CJK character bigrams, Korean particle stripping, identifier subword splitting), **1-hop / 2-hop Knowledge Graph structural expansion** (`RELATES_TO`, `MENTIONS`, `PART_OF`, `DEPENDS_ON`, `USES`, shared `Concept`/`Entity`/`Topic` bridge nodes, `[[wikilinks]]`, `#tags`, hop decay, multi-seed convergence), and **3-way Reciprocal Rank Fusion (RRF)** combining BM25F, dense/subword vectors, and Knowledge Graph neighborhood ranks. Added automatic zero-hit rescue fallback on `/api/search/hybrid` while keeping golden replay parity exact.
  - `rust/lattice-platform/src/toolsurface/tools/knowledge/`, `mcp/dispatch.rs`, `rust/lattice-agent/src/tools/catalog.rs`, and `prompts/mod.rs`: Upgraded `knowledge_search` / `obsidian_search` with `mode` (`hybrid` | `bm25f` | `graph` | `graphrag` | `subgraph`), `hops` (`1..=2`), `include_graph`, and extracted `subgraph` payloads alongside `knowledge_graph_search`, `knowledge_graph_context`, and `GRAPH_SKILL_BRIEFS`.
- **Multi-Scale Subword `FallbackTextEmbedder` (#13.1).**
  - Upgraded the offline zero-download `FallbackTextEmbedder` (`768d`, `lattice-fallback-subword-v2:768`) in `latticeai/core/embedding_providers/text.py` and `rust/lattice-core/src/embeddings.rs` to use multi-scale character n-grams (`3..5-grams` with word boundaries), Korean particle stripping + syllable n-grams + Jamo lead pairs, CJK unigrams/bigrams/trigrams, word unigrams/bigrams, sublinear-TF/positional weighting, 2-probe signed Blake2b-64 projection, and L2 normalization.
- **Unauthenticated EmbeddingGemma 2 Public Mirrors & Quantized GGUF/MLX/ONNX Fallbacks (#14.2).**
  - `latticeai/services/embedding_recommendation.py`, `latticeai/services/model_runtime/download.py`, `latticeai/services/model_loading.py`, and `rust/lattice-platform/src/modelops/models_catalog/embedding.rs`: Automatically fall back to public unauthenticated repositories (`mlx-community/embeddinggemma-2-4bit`, `mlx-community/embeddinggemma-2-8bit`, `ggml-org/embeddinggemma-2-GGUF`, `onnx-community/embeddinggemma-300m-ONNX`) and honor custom `HF_ENDPOINT` mirrors whenever `HF_TOKEN` is unset or Hugging Face returns gated `401`/`403` responses.
- **Real-Time SSE Embedding Download Progress Streaming & UI Visualization (#15.2).**
  - `latticeai/services/model_runtime/download.py`, `latticeai/api/models.py`, and `rust/lattice-platform/src/modelops/setup/install.rs`: Emit byte-level `downloaded_bytes`, `total_bytes`, `percent`, `speed_bps`, `speed_mbps`, `eta_seconds`, `current_file`, `gated_fallback`, and `fallback_repo` over `text/event-stream` SSE and JSON snapshots (`GET /models`).
  - `frontend/src/components/ProductFlow.tsx`, `InstallScreen.tsx`, `recommendationModel.ts`, `frontend/src/pages/Library.tsx`, and `frontend/src/i18n/`: Render live `0% - 100%` progress bars, download speed (`MB/s`), `ETA`, and public mirror / quantized GGUF·MLX fallback notices in Korean and English with 100% Vitest coverage.
- **Pillow 13+ Legacy Fallback Removal (#13.2).**
  - `scripts/check_screenshot_pixel_delta.py`: Removed the legacy Pillow `<=12` `diff.getdata()` fallback and called Pillow 13+'s `diff.get_flattened_data()` directly.
- **Ubuntu 24.04 / 26.04 (`t64`) & Chromium CI Resilience (#14.1).**
  - Added `scripts/check_ubuntu_runner_compat.mjs` and updated `.github/workflows/` (`ci.yml`, `release.yml`, `publish.yml`, `visual.yml`, `e2e-sidecar.yml`) with Debian/Ubuntu `t64` transition package fallbacks (`libasound2t64 || libasound2`, `libatk1.0-0t64 || libatk1.0-0`, `libatk-bridge2.0-0t64 || libatk-bridge2.0-0`, `libcups2t64 || libcups2`, `libgtk-3-0t64 || libgtk-3-0`, `libwebkit2gtk-4.1-0 || webkit2gtk-4.1`) and Chromium shared-library diagnostics.
- **Modular `scripts/bump_version/` CLI Subcommands (#15.1).**
  - Refactored `scripts/bump_version.py` into `scripts/bump_version/{__init__,targets,openapi,cargo,markers,cli}.py` supporting `all` (or default `<version>`), `openapi`, `cargo`, and `markers` subcommands with `--check` mode while keeping `scripts/bump_version.py` backwards-compatible.

## Artifacts

- `dist/ltcai-12.6.0-py3-none-any.whl`
- `dist/ltcai-12.6.0.tar.gz`
- `ltcai-12.6.0.tgz`
- `dist/ltcai-12.6.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_12.6.0_aarch64.dmg`
