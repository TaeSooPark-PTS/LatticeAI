# Lattice AI 12.4.0 — Sharp Weight

> **Status: historical** — point-in-time release note.

2026-11-11

12.3.0 Live Wire verified GGUF quantization tags, companion `--mmproj` loading,
and small-model tool/MCP calling. 12.4.0 Sharp Weight expands the verified
local and cloud model catalog with **Qwen 3.8 27B 4-bit** and **Gemma 4 26B A4B
QAT / QAT-OptiQ 4-bit** builds, closes all 39 `Pillow` CVEs (`pillow>=13,<14`)
and all npm lockfile vulnerabilities across root and `vscode-extension`,
hardens HTTP scheme validation in `model_engines`, and scopes Tailwind CSS v4's
oxide scanner to `frontend/`.

문은 **422 operations / 41 families**, 워커는 **20 routes**. 쓰기는 계속 `GraphWriter` 하나입니다.

## What landed

- **Qwen 3.8 27B 4-bit local & cloud catalog integration.** Added
  `mlx-community/Qwen3.8-27B-4bit` (`16.1GB`, `qwen3_5`, 3 safetensors shards,
  verified `loadable_static` on Hugging Face) to the large-tier recommendation
  catalog, wired multi-engine GGUF/HF routes (`ggml-org/Qwen3.8-27B-GGUF:Q4_K_M`
  and `Qwen/Qwen3.8-27B`) across `local_mlx`, `ollama`, `vllm`, `lmstudio`, and
  `llamacpp`, added `qwen-3.8-27b-4bit` / `qwen3.8-27b` aliases, added
  `Qwen/Qwen3.8-27B` / `qwen/qwen3.8-27b` to `together` and `openrouter`
  provider catalogs, and synced `CATALOG_HINTS` in `lattice-platform`.
- **Gemma 4 26B A4B QAT & QAT-OptiQ 4-bit builds.** Added
  `mlx-community/gemma-4-26B-A4B-it-qat-4bit` (`15.6GB`, built from Google's
  Quantization-Aware Trained release with 8-bit shared MLP and router layers,
  routed to `google/gemma-4-26B-A4B-it-qat-q4_0-gguf:Q4_0` and
  `google/gemma-4-26B-A4B-it-qat-q4_0-unquantized`) and
  `mlx-community/gemma-4-26B-A4B-it-qat-OptiQ-4bit` (`21.9GB`, sensitivity-guided
  mixed 4-bit/8-bit `mlx-optiq` build on Google's QAT base) alongside
  `mlx-community/gemma-4-26b-a4b-it-4bit`, with `gemma-4-26b-qat-4bit` and
  `gemma-4-26b-optiq-4bit` aliases and Rust `CATALOG_HINTS` entries.
- **Python & npm dependency security audit closure.** Upgraded `pillow` to
  `>=13,<14` in `pyproject.toml` and `requirements.txt` (closing 39 CVEs in
  `pillow 12.3.0`), synchronized `reportlab>=4,<5` and `huggingface_hub>=1.0,<2`
  in `requirements.txt`, and updated `package-lock.json` and
  `vscode-extension/package-lock.json` (`vitest` / `@vitest/coverage-v8` `^4.1.12`,
  `glob` `^14.0.4`, `minimatch` `^11.0.7`) so `pip-audit -r requirements.txt --strict`
  and `npm audit` report 0 vulnerabilities.
- **Engine URL scheme hardening, admin retention clock injection & Tailwind CSS v4 scanner scoping.**
  `_json_request` and `get_openai_compatible_server_models` in
  `latticeai/services/model_engines.py` now reject non-`http(s)://` URLs before
  calling `urllib.request.urlopen`. `admin_log_retention` in
  `rust/lattice-platform/src/adminops/admin/handlers.rs` now reads the
  injectable `state.auth.clock().now()` rather than raw wall-clock `unix_now()`.
  `frontend/src/styles.css` scopes `@import "tailwindcss" source("..");` so
  `@tailwindcss/oxide` scans only `frontend/` rather than walking `rust/target`
  during concurrent builds.

## Honest leftovers

- Hash embeddings remain the no-download default (`fallback`, not semantic).
- The macOS dmg is ad-hoc signed unless an Apple Developer ID is present on
  the build machine.
- Vector search env still defaults to `brute` (auto `hnsw+rescore` at 512+
  vectors when a worker sidecar is bound).

## Artifacts

- `dist/ltcai-12.4.0-py3-none-any.whl`
- `dist/ltcai-12.4.0.tar.gz`
- `ltcai-12.4.0.tgz`
- `dist/ltcai-12.4.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_12.4.0_aarch64.dmg`
