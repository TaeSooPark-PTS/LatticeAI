[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../releases/RELEASE_NOTES_v12.6.0.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./RELEASE_NOTES_v12.6.0.md)

# Lattice AI 12.6.0 — Hybrid Graph

> **상태: historical** — 해당 시점의 보존된 릴리스 노트입니다.

2026-11-15

12.5.0 Deep Vector가 네이티브 Google EmbeddingGemma 2 임베딩과 하드웨어 인식 추천을 도입했다면, **12.6.0 Hybrid Graph**는 검색 및 임베딩 스택을 종단간 업그레이드합니다. 진정한 **3-Way BM25F + 벡터 + 지식 그래프 RRF 하이브리드 인덱싱**, 그래프 인식 에이전트 스킬 및 MCP 검색 도구, **멀티스케일 서브워드 `FallbackTextEmbedder`**, **비인증 EmbeddingGemma 2 공개 미러 및 양자화 GGUF/MLX/ONNX 폴백**, **속도(`MB/s`)와 `ETA`를 포함한 실시간 SSE 다운로드 진행률 스트리밍**, **Pillow 13+ 레거시 정리**, **Ubuntu 24.04/26.04 `t64` 및 Chromium CI 복원력**, **모듈화 `scripts/bump_version/` CLI**를 제공합니다.

진입점은 **422개 오퍼레이션 / 41개 패밀리**, Python 워커는 **20개 라우트**를 유지하며, 쓰기는 여전히 단일 `GraphWriter`를 통합니다.

## 주요 반영 사항

- **3-Way BM25F + 벡터 + 지식 그래프 RRF 하이브리드 인덱싱 및 그래프 스킬.**
  - `rust/lattice-retrieval/src/bm25/`, `service_hybrid.rs`, `hybrid.rs`, `search_api/handlers.rs`: 보정된 필드 가중 Okapi **BM25F**(`k1 = 1.2`, `b = 0.75`, `body`/`metadata` 대비 `title`/`tags_headings` 가중치 상향, CJK 문자 바이그램, 한국어 조사 제거, 식별자 서브워드 분할), **1-Hop / 2-Hop 지식 그래프 구조 확장**(`RELATES_TO`, `MENTIONS`, `PART_OF`, `DEPENDS_ON`, `USES`, 공유 `Concept`/`Entity`/`Topic` 브리지 노드, `[[위키링크]]`, `#태그`, 홉 감쇠, 다중 시드 수렴), 그리고 BM25F·밀집/서브워드 벡터·지식 그래프 이웃 순위를 결합하는 **3-Way Reciprocal Rank Fusion(RRF)**을 구현했습니다. 골든 리플레이 동등성을 정확히 유지하면서 `/api/search/hybrid`의 0건 결과 자동 구제 폴백도 추가했습니다.
  - `rust/lattice-platform/src/toolsurface/tools/knowledge/`, `mcp/dispatch.rs`, `rust/lattice-agent/src/tools/catalog.rs`, `prompts/mod.rs`: `knowledge_search` / `obsidian_search`에 `mode`(`hybrid` | `bm25f` | `graph` | `graphrag` | `subgraph`), `hops`(`1..=2`), `include_graph`, 추출된 `subgraph` 페이로드를 추가하고 `knowledge_graph_search`, `knowledge_graph_context`, `GRAPH_SKILL_BRIEFS`를 연계했습니다.
- **멀티스케일 서브워드 `FallbackTextEmbedder` (#13.1).**
  - `latticeai/core/embedding_providers/text.py` 및 `rust/lattice-core/src/embeddings.rs`의 오프라인 무다운로드 `FallbackTextEmbedder`(`768d`, `lattice-fallback-subword-v2:768`)를 멀티스케일 문자 n-gram(단어 경계 포함 `3..5-gram`), 한국어 조사 제거 + 음절 n-gram + 자모 초성 쌍, CJK 유니그램/바이그램/트라이그램, 단어 유니그램/바이그램, 준선형 TF/위치 가중치, 2-프로브 부호화 Blake2b-64 투영, L2 정규화 방식으로 업그레이드했습니다.
- **비인증 EmbeddingGemma 2 공개 미러 및 양자화 GGUF/MLX/ONNX 폴백 (#14.2).**
  - `latticeai/services/embedding_recommendation.py`, `latticeai/services/model_runtime/download.py`, `latticeai/services/model_loading.py`, `rust/lattice-platform/src/modelops/models_catalog/embedding.rs`: `HF_TOKEN`이 미설정이거나 Hugging Face가 접근 제한 `401`/`403`을 반환하면 공개 비인증 저장소(`mlx-community/embeddinggemma-2-4bit`, `mlx-community/embeddinggemma-2-8bit`, `ggml-org/embeddinggemma-2-GGUF`, `onnx-community/embeddinggemma-300m-ONNX`)로 자동 폴백하며 사용자 지정 `HF_ENDPOINT` 미러도 준수합니다.
- **실시간 SSE 임베딩 다운로드 진행률 스트리밍 및 UI 시각화 (#15.2).**
  - `latticeai/services/model_runtime/download.py`, `latticeai/api/models.py`, `rust/lattice-platform/src/modelops/setup/install.rs`: 바이트 단위 `downloaded_bytes`, `total_bytes`, `percent`, `speed_bps`, `speed_mbps`, `eta_seconds`, `current_file`, `gated_fallback`, `fallback_repo`를 `text/event-stream` SSE와 JSON 스냅샷(`GET /models`)으로 스트리밍합니다.
  - `frontend/src/components/ProductFlow.tsx`, `InstallScreen.tsx`, `recommendationModel.ts`, `frontend/src/pages/Library.tsx`, `frontend/src/i18n/`: 실시간 `0% - 100%` 진행률 바, 다운로드 속도(`MB/s`), `ETA`, 공개 미러/양자화 GGUF·MLX 폴백 안내를 한국어·영어로 렌더링하며 100% Vitest 커버리지를 유지합니다.
- **Pillow 13+ 레거시 폴백 제거 (#13.2).**
  - `scripts/check_screenshot_pixel_delta.py`: 레거시 Pillow `<=12`용 `diff.getdata()` 폴백을 제거하고 Pillow 13+의 `diff.get_flattened_data()`를 직접 호출합니다.
- **Ubuntu 24.04 / 26.04(`t64`) 및 Chromium CI 복원력 (#14.1).**
  - `scripts/check_ubuntu_runner_compat.mjs`를 추가하고 `.github/workflows/`(`ci.yml`, `release.yml`, `publish.yml`, `visual.yml`, `e2e-sidecar.yml`)에 Debian/Ubuntu `t64` 전환 패키지 폴백(`libasound2t64 || libasound2`, `libatk1.0-0t64 || libatk1.0-0`, `libatk-bridge2.0-0t64 || libatk-bridge2.0-0`, `libcups2t64 || libcups2`, `libgtk-3-0t64 || libgtk-3-0`, `libwebkit2gtk-4.1-0 || webkit2gtk-4.1`)과 Chromium 공유 라이브러리 진단을 적용했습니다.
- **모듈화 `scripts/bump_version/` CLI 서브커맨드 (#15.1).**
  - `scripts/bump_version.py`를 `scripts/bump_version/{__init__,targets,openapi,cargo,markers,cli}.py`로 리팩터링해 기존 `scripts/bump_version.py` 하위 호환성을 유지하면서 `all`(또는 기본 `<version>`), `openapi`, `cargo`, `markers` 서브커맨드와 `--check` 모드를 지원합니다.

## 배포 아티팩트

- `dist/ltcai-12.6.0-py3-none-any.whl`
- `dist/ltcai-12.6.0.tar.gz`
- `ltcai-12.6.0.tgz`
- `dist/ltcai-12.6.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_12.6.0_aarch64.dmg`
