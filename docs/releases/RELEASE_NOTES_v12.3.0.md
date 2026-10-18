# Lattice AI 12.3.0 — Live Wire

> **Status: historical** — point-in-time release note.

2026-10-19

12.2.1 True Count가 검색·스킵·워치·작은 모델 요약을 정직하게 세었습니다.
12.3.0 Live Wire는 Hugging Face 실저장소와 클라우드 공급자 카탈로그를
전수 검증해 태그·경로 불일치를 바로잡고, 작은 로컬 모델의 툴콜/MCP 호출과
HTML 생성·구조 점검(`POST /tools/inspect_html`)·UTF-8 다운로드(`GET /tools/download`)를
실제 `mlx-community/gemma-4-e2b-it-4bit` 로드 상태에서 끝까지 검증했습니다.

문은 **422 operations / 41 families**, 워커는 **20 routes**. 쓰기는 계속 `GraphWriter` 하나입니다.

## What landed

- **Hugging Face GGUF quantization tag & route fidelity.** `ggml-org/gemma-4-*-GGUF`
  repositories publish `Q4_0` / `Q8_0` / `BF16` rather than `Q4_K_M`, and
  `ggml-org/gpt-oss-20b-GGUF` publishes `MXFP4`. `_gguf_routes` now takes an
  explicit `ollama_tag` per repository (`Q4_0` for Gemma 4, `MXFP4` for
  GPT-OSS 20B), and `Qwen3.5-9B-MLX-4bit` (`unsloth/Qwen3.5-9B-GGUF`) and
  `Qwen3.6-35B-A3B-4bit` (`ggml-org/Qwen3.6-35B-A3B-GGUF`) are wired to
  verified GGUF repositories across `ollama`, `lmstudio`, and `llamacpp`.
- **Cloud & local provider defaults synchronized.** `xai` defaults to
  `grok-4.6` (matching the Rust cloud lane), `together` defaults to
  `Qwen/Qwen3.6-27B`, `openrouter` lists `qwen/qwen3.6-35b-a3b` and
  `x-ai/grok-4.6`, `ollama` defaults to `hf.co/ggml-org/gemma-4-12B-it-GGUF:Q4_0`,
  `vllm` defaults to `google/gemma-4-12B-it`, and nonexistent
  `mlx-community/bge-m3` is removed from `LOCAL_MLX_MODELS` (kept in
  `OLLAMA_EMBEDDING_MODELS`).
- **GGUF auxiliary filtering, companion `mmproj`, and shard verification.**
  `is_auxiliary_gguf` filters out `mmproj-`, `mtp-`, `dflash-`, `eagle3-`, and
  `imatrix` GGUFs when selecting primary model weights; `ensure_llamacpp_server`
  downloads companion `mmproj-*.gguf` files and passes `--mmproj` when present;
  `hf_model_ready` verifies all shards listed in `model.safetensors.index.json`.
- **Small-model tool calling & MCP calling hardening.** `KNOWN_ACTIONS` now
  includes `grep`, `inspect_html`, `knowledge_save`, `knowledge_search`,
  `preview_url`, `search_files`, and `workspace_tree`. `object_from_args_value`
  recovers stringified `arguments` with trailing commas, truncation, or Python
  dict literals; `normalize_call_keys` unwraps OpenAI `tool_calls` arrays;
  `salvage_labeled_action` extracts fenced HTML/code blocks for `write_file` /
  `local_write`; `perform_action` applies `adopt_named_args` (`html`/`code`/`source`/`data` → `content`).
- **HTML structure inspection & RFC 5987 UTF-8 downloads.** `POST /tools/inspect_html`
  parses and returns `title`, `links`, `scripts`, `stylesheets`, `images`,
  `forms`, and `headings` (`h1`–`h6`) while enforcing `MAX_FILE_BYTES`.
  `GET /tools/download` decodes percent-encoded UTF-8 paths properly and emits
  RFC 5987 `filename*=UTF-8''...` in `Content-Disposition`.

## Honest leftovers

- Hash embeddings remain the no-download default (`fallback`, not semantic).
- The macOS dmg is ad-hoc signed unless an Apple Developer ID is present on
  the build machine.
- Vector search env still defaults to `brute` (auto `hnsw+rescore` at 512+
  vectors when a worker sidecar is bound).

## Artifacts

- `dist/ltcai-12.3.0-py3-none-any.whl`
- `dist/ltcai-12.3.0.tar.gz`
- `ltcai-12.3.0.tgz`
- `dist/ltcai-12.3.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_12.3.0_aarch64.dmg`
