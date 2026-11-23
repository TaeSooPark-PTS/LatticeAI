[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./RELEASE_NOTES_v13.4.2.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](../ko/releases/RELEASE_NOTES_v13.4.2.md)

# Lattice AI v13.4.2 — Living Synthesis (2026-11-24)

> **Status: canonical** — current release note (oMLX Engine & Qwen3.8 27B 4-bit Support).

Adds first-class **oMLX (`jundot/omlx`)** engine integration with hardware-tuned tiered RAM/SSD KV caching and makes **`mlx-community/Qwen3.8-27B-4bit`** recommended and downloadable on 32GB+ Apple Silicon Macs.

## What Changed

- **oMLX Engine Integration (`latticeai/models/model_providers.py`, `latticeai/services/model_engines.py`, `latticeai/services/model_runtime/`, `rust/lattice-platform/src/modelops/models_catalog/mod.rs`):**
  - Registered `omlx` in `OPENAI_COMPATIBLE_PROVIDERS`, `ENGINE_INSTALLERS`, `LOCAL_ENGINES`, and `/engines` status reporting.
  - Added `ensure_omlx_server()` and `omlx_serve_args()` with hardware-tuned KV cache and memory-guard defaults (`--memory-guard balanced`, `--hot-cache-max-size 2GB` on 32GB / `4GB` on 48GB+, `--paged-ssd-cache-dir ~/.omlx/kv-cache`, `--paged-ssd-cache-max-size 32GB`, `--max-concurrent-requests 2`, `--initial-cache-blocks 128`).
  - Added `org--repo` <-> `org/repo` Hugging Face cache resolution so locally downloaded HF models are immediately discovered and served by oMLX.
- **Recommended & Downloadable Catalog (`latticeai/services/model_capability_catalog.py`, `latticeai/services/model_capability_registry.py`, `latticeai/services/model_catalog.py`):**
  - Lowered `mlx-community/Qwen3.8-27B-4bit` `recommended_ram_gb` from `48.0` to `32.0` after live Apple M5 32GB verification with oMLX prefix KV caching (`0.45s` warm TTFT, `69/74` cached prefix tokens).
  - Exposed `omlx` catalog entries (`omlx:mlx-community--Qwen3.8-27B-4bit` and Apple Silicon MLX models) in both recommended and downloadable model lists.

## Exact Build Artifacts

- `dist/ltcai-13.4.2-py3-none-any.whl`
- `dist/ltcai-13.4.2.tar.gz`
- `ltcai-13.4.2.tgz`
- `dist/ltcai-13.4.2.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_13.4.2_aarch64.dmg`
