[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../releases/RELEASE_NOTES_v12.3.0.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./RELEASE_NOTES_v12.3.0.md)

# Lattice AI 12.3.0 — Live Wire

> **상태: historical** — 해당 시점의 보존된 릴리스 노트입니다.

2026-10-19

12.2.1 True Count가 검색, 스킵 스탬프, 감시 정리, 소형 모델 요약을 정직하게 집계하도록 만들었다면, 12.3.0 Live Wire는 실제 Hugging Face 저장소와 클라우드 프로바이더 카탈로그를 전수 감사해 태그·라우트 불일치를 바로잡고, 실제 로드된 `mlx-community/gemma-4-e2b-it-4bit` 모델을 대상으로 소형 로컬 모델의 도구/MCP 호출, HTML 생성, 구조 검사(`POST /tools/inspect_html`), UTF-8 다운로드(`GET /tools/download`)를 종단간 검증했습니다.

진입점은 **422개 오퍼레이션 / 41개 패밀리**, Python 워커는 **20개 라우트**를 유지하며, 쓰기는 여전히 단일 `GraphWriter`를 통합니다.

## 주요 반영 사항

- **Hugging Face GGUF 양자화 태그 및 라우트 정합성 확보.** `ggml-org/gemma-4-*-GGUF` 저장소는 `Q4_K_M`이 아닌 `Q4_0` / `Q8_0` / `BF16`을 게시하며, `ggml-org/gpt-oss-20b-GGUF`는 `MXFP4`를 게시합니다. `_gguf_routes`가 저장소별 명시적 `ollama_tag`(Gemma 4는 `Q4_0`, GPT-OSS 20B는 `MXFP4`)를 받도록 수정하고, `Qwen3.5-9B-MLX-4bit`(`unsloth/Qwen3.5-9B-GGUF`)와 `Qwen3.6-35B-A3B-4bit`(`ggml-org/Qwen3.6-35B-A3B-GGUF`)를 `ollama`, `lmstudio`, `llamacpp` 전반의 검증된 GGUF 저장소에 연결했습니다.
- **클라우드 및 로컬 프로바이더 기본값 동기화.** `xai` 기본값을 `grok-4.6`(Rust 클라우드 레인과 일치), `together` 기본값을 `Qwen/Qwen3.6-27B`로 맞추고, `openrouter`에 `qwen/qwen3.6-35b-a3b`와 `x-ai/grok-4.6`을 등록했으며, `ollama` 기본값을 `hf.co/ggml-org/gemma-4-12B-it-GGUF:Q4_0`, `vllm` 기본값을 `google/gemma-4-12B-it`로 정렬했습니다. 존재하지 않던 `mlx-community/bge-m3`는 `LOCAL_MLX_MODELS`에서 제거했습니다(`OLLAMA_EMBEDDING_MODELS`에는 유지).
- **GGUF 보조 파일 필터링, 동반 `mmproj` 다운로드 및 샤드 검증.** `is_auxiliary_gguf`가 주 모델 가중치 선택 시 `mmproj-`, `mtp-`, `dflash-`, `eagle3-`, `imatrix` GGUF 파일을 제외하고, `ensure_llamacpp_server`가 동반 `mmproj-*.gguf` 파일을 내려받아 존재 시 `--mmproj` 인자를 전달하며, `hf_model_ready`가 `model.safetensors.index.json`에 나열된 모든 샤드를 검증합니다.
- **소형 모델 도구 호출 및 MCP 호출 하드닝.** `KNOWN_ACTIONS`에 `grep`, `inspect_html`, `knowledge_save`, `knowledge_search`, `preview_url`, `search_files`, `workspace_tree`를 추가했습니다. `object_from_args_value`는 후행 쉼표·절단·Python dict 리터럴이 포함된 문자열화 `arguments`를 복구하고, `normalize_call_keys`는 OpenAI `tool_calls` 배열을 언래핑하며, `salvage_labeled_action`은 `write_file`/`local_write`용 펜스 HTML/코드 블록을 추출하고, `perform_action`은 `adopt_named_args`(`html`/`code`/`source`/`data` → `content`)를 적용합니다.
- **HTML 구조 검사 및 RFC 5987 UTF-8 다운로드.** `POST /tools/inspect_html`이 `MAX_FILE_BYTES` 한도 내에서 `title`, `links`, `scripts`, `stylesheets`, `images`, `forms`, `headings`(`h1`–`h6`)를 파싱해 반환합니다. `GET /tools/download`는 퍼센트 인코딩된 UTF-8 경로를 올바르게 디코딩하고 `Content-Disposition`에 RFC 5987 `filename*=UTF-8''...` 헤더를 출력합니다.

## 남은 과제 (Honest leftovers)

- 해시 임베딩은 무다운로드 기본값(`fallback`, 시맨틱 아님)으로 유지됩니다.
- macOS DMG는 빌드 머신에 Apple Developer ID가 없는 한 ad-hoc 서명됩니다.
- 벡터 검색 환경 기본값은 여전히 `brute`입니다(워커 사이드카 연결 시 512개 이상 벡터에서 자동 `hnsw+rescore`).

## 배포 아티팩트

- `dist/ltcai-12.3.0-py3-none-any.whl`
- `dist/ltcai-12.3.0.tar.gz`
- `ltcai-12.3.0.tgz`
- `dist/ltcai-12.3.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_12.3.0_aarch64.dmg`
