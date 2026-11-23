[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./RELEASE_NOTES_v13.5.0.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](../ko/releases/RELEASE_NOTES_v13.5.0.md)

# Lattice AI v13.5.0 — Living Synthesis (2026-11-24)

> **Status: canonical** — current release note (Cross-Platform One-Way Qwen3.8 27B Setup, TypeScript 7 Migration & Modular Refactoring).

Delivers one-click cross-platform engine installation and model setup for **Qwen3.8 27B 4-bit** across **macOS** (`omlx` Homebrew + zero-Homebrew universal2 wheel fallback, `local_mlx`), **Windows** (`winget` Ollama / llama.cpp / LM Studio + `pip` `llama-cpp-python[server]` fallback), and **Linux** (Ollama official install script + `pip` `llama-cpp-python[server]` / `vllm`), migrates the workspace typechecker to **TypeScript 7.0.2 (`tsgo`)** and `scripts/check_i18n_literals.mjs` to `@babel/parser`, and modularizes engine/loading services while removing dead code and obsolete tests.

## What Changed

- **Cross-Platform One-Way Engine Install & Qwen3.8 27B 4-bit Setup (`latticeai/services/engine_adapters.py`, `latticeai/services/model_engines.py`, `latticeai/services/model_runtime/`, `rust/lattice-platform/src/modelops/`, `frontend/src/features/library/ModelsPanel.tsx`):**
  - Added platform-aware `resolve_engine_installer()` supporting Windows `winget` (`Ollama.Ollama`, `ggml.llamacpp`, `ElementLabs.LMStudio`) + `pip` `llama-cpp-python[server]` fallback, Linux `curl` Ollama installer + `pip` fallbacks, and macOS Apple Silicon `omlx` installation via Homebrew or zero-Homebrew GitHub Release universal2 wheel fallback (`~/.omlx/venv`).
  - Added automatic cross-platform routing (`resolve_cross_platform_model_request`, `_recommended_with_engine_options`, Rust `probe.rs` & `scan.rs` & Windows RAM/`winget` probes in `probe_sysinfo.rs`) so Windows and Linux hosts automatically route `Qwen3.8-27B` onto `ollama:hf.co/ggml-org/Qwen3.8-27B-GGUF:Q4_K_M` or `llamacpp:ggml-org/Qwen3.8-27B-GGUF` (with `unsloth/Qwen3.8-27B-GGUF` mirror fallback and `HF_XET_HIGH_PERFORMANCE=1` high-speed transfers).
  - Added real-time available-RAM pressure detection (`host_available_ram_gb`) across macOS (`vm_stat`), Linux (`/proc/meminfo`), and Windows (`GlobalMemoryStatusEx`) so `omlx_serve_args` dynamically tightens `--memory-guard safe`, `--hot-cache-max-size 1GB`, and `--max-concurrent-requests 1` when concurrent apps constrain 32GB memory, alongside GPU/quantized-KV `llamacpp_serve_args`.
  - Wired automatic plan confirmation token binding in `status.install_engine` and `install_required` consent gating in `ModelsPanel.tsx` so consented one-click setup installs missing runtimes, downloads weights, starts the local server, and loads the model in one step.
- **TypeScript 7 (`tsgo`) & `@babel/parser` AST Migration (`package.json`, `scripts/check_i18n_literals.mjs`, `scripts/run_openapi_typescript.mjs`):**
  - Upgraded root `typescript` from `^5.9.3` to `^7.0.2`, migrated `scripts/check_i18n_literals.mjs` off the legacy TS 5 compiler AST API onto `@babel/parser`, and isolated `openapi-typescript`'s internal TS 5 factory usage behind `scripts/run_openapi_typescript.mjs`.
- **Modular Refactoring & Dead-Code / Obsolete-Test Cleanup (`latticeai/services/{engine_adapters.py,model_engines.py,model_loading.py,model_catalog.py}`, `latticeai/core/policy.py`, `frontend/src/{routes.ts,lib/folderPicker.ts}`, `rust/Cargo.toml`):**
  - Extracted `engine_adapters.py` (`EngineAdapter` registry) from `model_engines.py` and unified the duplicated blocking/SSE 7-way engine preparation tree in `model_loading.py` (`716 -> 492` lines).
  - Removed orphaned `recommend_models_for_pc` (`model_catalog.py`), unused `capabilities_for_role` (`policy.py`), unused `routeAliases` (`routes.ts`), unused `hasBrowserFolderPicker` (`folderPicker.ts`), unused workspace crate `walkdir` (`rust/Cargo.toml`), and redundant `tests/unit/test_model_loading_deps.py`.

## Exact Build Artifacts

- `dist/ltcai-13.5.0-py3-none-any.whl`
- `dist/ltcai-13.5.0.tar.gz`
- `ltcai-13.5.0.tgz`
- `dist/ltcai-13.5.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_13.5.0_aarch64.dmg`
