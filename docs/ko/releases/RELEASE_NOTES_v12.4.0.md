[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../releases/RELEASE_NOTES_v12.4.0.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./RELEASE_NOTES_v12.4.0.md)

# Lattice AI 12.4.0 — Sharp Weight

> **상태: historical** — 해당 시점의 보존된 릴리스 노트입니다.

2026-11-11

12.3.0 Live Wire는 GGUF 양자화 태그, 동반 `--mmproj` 로딩, 소형 모델 도구/MCP 호출을 검증했습니다. 12.4.0 Sharp Weight는 검증된 로컬·클라우드 모델 카탈로그에 **Qwen 3.8 27B 4-bit** 및 **Gemma 4 26B A4B QAT / QAT-OptiQ 4-bit** 빌드를 추가하고, `Pillow` CVE 39건(`pillow>=13,<14`)과 루트·`vscode-extension` npm 락파일 취약점을 모두 해소했으며, `model_engines`의 HTTP 스킴 검증을 강화하고 Tailwind CSS v4 옥사이드 스캐너 범위를 `frontend/`로 제한했습니다.

진입점은 **422개 오퍼레이션 / 41개 패밀리**, Python 워커는 **20개 라우트**를 유지하며, 쓰기는 여전히 단일 `GraphWriter`를 통합니다.

## 주요 반영 사항

- **Qwen 3.8 27B 4-bit 로컬 및 클라우드 카탈로그 통합.** Hugging Face에서 `loadable_static` 검증을 통과한 `mlx-community/Qwen3.8-27B-4bit`(`16.1GB`, `qwen3_5`, safetensors 3샤드)를 대형 티어 추천 카탈로그에 추가하고, `local_mlx`, `ollama`, `vllm`, `lmstudio`, `llamacpp` 전반에 멀티엔진 GGUF/HF 라우트(`ggml-org/Qwen3.8-27B-GGUF:Q4_K_M`, `Qwen/Qwen3.8-27B`)를 연결했습니다. 또한 `qwen-3.8-27b-4bit` / `qwen3.8-27b` 별칭, `together`·`openrouter` 프로바이더 카탈로그(`Qwen/Qwen3.8-27B`, `qwen/qwen3.8-27b`), `lattice-platform`의 `CATALOG_HINTS`를 동기화했습니다.
- **Gemma 4 26B A4B QAT 및 QAT-OptiQ 4-bit 빌드 추가.** Google의 QAT(Quantization-Aware Trained) 릴리스 기반에 8-bit 공유 MLP·라우터 레이어를 적용한 `mlx-community/gemma-4-26B-A4B-it-qat-4bit`(`15.6GB`, `google/gemma-4-26B-A4B-it-qat-q4_0-gguf:Q4_0` 및 `google/gemma-4-26B-A4B-it-qat-q4_0-unquantized` 라우팅)와 Google QAT 기반의 민감도 유도 혼합 4-bit/8-bit `mlx-optiq` 빌드인 `mlx-community/gemma-4-26B-A4B-it-qat-OptiQ-4bit`(`21.9GB`)를 `mlx-community/gemma-4-26b-a4b-it-4bit`와 함께 추가하고, `gemma-4-26b-qat-4bit`·`gemma-4-26b-optiq-4bit` 별칭과 Rust `CATALOG_HINTS` 항목을 반영했습니다.
- **Python 및 npm 의존성 보안 감사 해소.** `pyproject.toml`과 `requirements.txt`에서 `pillow`를 `>=13,<14`로 업그레이드해 `pillow 12.3.0`의 CVE 39건을 닫고, `requirements.txt`에 `reportlab>=4,<5`와 `huggingface_hub>=1.0,<2`를 동기화했습니다. 또한 `package-lock.json`과 `vscode-extension/package-lock.json`(`vitest` / `@vitest/coverage-v8` `^4.1.12`, `glob` `^14.0.4`, `minimatch` `^11.0.7`)을 업데이트해 `pip-audit -r requirements.txt --strict` 및 `npm audit` 취약점 0건을 달성했습니다.
- **엔진 URL 스킴 하드닝, 관리자 보존 시계 주입 및 Tailwind CSS v4 스캐너 범위 제한.** `latticeai/services/model_engines.py`의 `_json_request`와 `get_openai_compatible_server_models`가 `urllib.request.urlopen` 호출 전에 `http(s)://` 외 스킴 URL을 거부합니다. `rust/lattice-platform/src/adminops/admin/handlers.rs`의 `admin_log_retention`은 원시 시스템 시간 `unix_now()` 대신 주입 가능한 `state.auth.clock().now()`를 사용합니다. `frontend/src/styles.css`는 `@import "tailwindcss" source("..");`를 지정해 동시 빌드 중 `@tailwindcss/oxide`가 `rust/target`을 순회하지 않고 `frontend/`만 스캔하도록 제한했습니다.

## 남은 과제 (Honest leftovers)

- 해시 임베딩은 무다운로드 기본값(`fallback`, 시맨틱 아님)으로 유지됩니다.
- macOS DMG는 빌드 머신에 Apple Developer ID가 없는 한 ad-hoc 서명됩니다.
- 벡터 검색 환경 기본값은 여전히 `brute`입니다(워커 사이드카 연결 시 512개 이상 벡터에서 자동 `hnsw+rescore`).

## 배포 아티팩트

- `dist/ltcai-12.4.0-py3-none-any.whl`
- `dist/ltcai-12.4.0.tar.gz`
- `ltcai-12.4.0.tgz`
- `dist/ltcai-12.4.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_12.4.0_aarch64.dmg`
