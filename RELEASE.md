# Lattice AI Release Guide

이 문서는 `npm`, `PyPI`, `VS Code`, `Cursor`, `Antigravity`, `Open VSX` 배포를
한 번에 처리하기 위한 체크리스트입니다.

> **`.github/workflows/release.yml`은 태그 push에서 빌드와 검증만 수행합니다.**
> 패키지 스토어 배포는 `.github/workflows/publish.yml` (CD)이 **GitHub
> Release를 published로 만들 때** 돌립니다. 태그 생성만으로는 npm / PyPI /
> VS Code Marketplace / Open VSX에 올라가지 않습니다. CD는 이미 올라간
> 버전을 `already-published`로 건너뛰므로 재실행해도 안전합니다.

> **릴리스 증거물 보존 정책 (`output/release/`)**: 버전별 스크린샷/영상 증거는
> 최신 3개 버전만 보관합니다. `npm run release:evidence`가 캡처 후 자동으로
> `scripts/prune_release_evidence.mjs`를 실행해 오래된 버전을 정리하며
> (`LTCAI_RELEASE_EVIDENCE_KEEP`으로 조정), 과거 증거는 언제든 해당 태그를
> 체크아웃해 재생성할 수 있습니다.

## v12.9.0 — Semantic Guard (2026-11-17)

**시맨틱 쿼리 임베딩 계약 정렬(`query_embed.rs`) 및 순수 Rust 네이티브 HNSW(`NativeHnswIndex`)·스코프 Python HNSW 사이드카**, **네트워크 경계·클라우드 컨텍스트 fail-closed 격리(`network_boundary_guard.rs`, `lattice-chat/src/boundary.rs`)**, **데스크톱(Tauri)·CLI·호스트 슈퍼바이저·멀티스테이지 Docker 가드레일**, **실시간 Workspace OS SSE(`/api/realtime/stream`) 및 `useAppBootstrap` 분리**, **`.latticebrain` v2 아카이브 KDF 강화 및 CI 게이트 확장**을 통합한 릴리스.

- **시맨틱 쿼리 정렬 & 네이티브/사이드카 HNSW:** `rust/lattice-retrieval/src/{query_embed.rs,native_hnsw.rs,vector_hnsw.rs}`와 `latticeai/core/vector_index/{hnsw.py,sidecar.py}`에서 `/api/search/hybrid`·`/memory/recall`·`/chat` 쿼리 임베딩을 활성 `EmbeddingIndexContract`에 정렬하고 순수 Rust HNSW 및 `(workspace_id, model_id, dim)` 스코프 사이드카 영속화를 제공한다.
- **네트워크 경계 격리 & 데스크톱/CLI/Docker 가드레일:** `network_boundary_guard.rs`와 `lattice-chat/src/{boundary.rs,cloud/context.rs}`가 `local_only`/`cloud_allowed` 동의 및 민감도 필터를 서버 측에서 강제하며, `src-tauri/src/backend.rs`·`bin/ltcai.js`·`rust/lattice-host`·`Dockerfile`의 프로세스 수명주기 및 컨테이너 가드레일을 보강했다.
- **실시간 SSE 스트림, v2 KDF & CI 게이트:** `workspaceos/realtime.rs`와 `useRealtimeStream.ts`(Vitest 100%)로 실시간 이벤트 스트리밍을 연결하고 `useAppBootstrap.ts`를 분리했으며, `portability/{crypto.rs,kdf.rs}` v2 KDF 강화(v1 복호화 하위 호환) 및 `.github/workflows/{ci,dependency-audit,e2e-sidecar}.yml` 게이트를 강화했다.

릴리스 노트는 [docs/releases/RELEASE_NOTES_v12.9.0.md](docs/releases/RELEASE_NOTES_v12.9.0.md).

빌드 산출물은 `dist/ltcai-12.9.0-py3-none-any.whl`,
`dist/ltcai-12.9.0.tar.gz`, `ltcai-12.9.0.tgz`, `dist/ltcai-12.9.0.vsix`,
`src-tauri/target/release/bundle/dmg/Lattice AI_12.9.0_aarch64.dmg` 입니다.

## v12.8.0 — Trusted Brain (2026-11-16)

**임베딩 계약·지식 라이프사이클 일원화**, **SQLite `LIMIT` 이전 워크스페이스 스코핑·벡터 차원 정렬·바운드 사이드카 캐시**, **사용자·워크스페이스·테넌트 fail-closed 게이트웨이 격리**, **암호화 `.latticebrain` 아카이브 및 정직한 PostgreSQL `planned` 보고**, **프론트엔드 기능 모듈 분리**, **PR 검색 게이트·macOS DMG CI/CD 자동화**를 통합한 릴리스.

- **임베딩 계약 & 지식 라이프사이클:** `rust/lattice-core/src/embedding_contract.rs`, `graph_write/lifecycle.rs`, `latticeai/core/embedding_providers/`, `latticeai/worker_app.py`에서 `EmbeddingIndexContract`, 세분화된 `IngestStatus`, 통합 `GraphWriter` 삭제, `KnowledgeLifecycleState`를 강제한다.
- **검색 스코핑·정렬·캐시 & 게이트웨이 격리:** `rust/lattice-retrieval`에서 `LIMIT` 전 SQL 필터링, `vector_search_aligned`, `SidecarCachePool` LRU 상한(`DEFAULT_MAX_CACHE_BYTES`)을 적용했고, `rust/lattice-agent`/`lattice-host`/`lattice-auth`에서 `IsolatedRootSpec`·`resolve_agent_scope`로 멀티유저·테넌트·워크스페이스 격리와 감사 로깅을 E2E 검증했다.
- **포터빌리티 아카이브, UI 모듈화 & macOS DMG 자동화:** `rust/lattice-platform/src/knowledge/portability/`에서 AEAD 암호화 `.latticebrain` 검증·복원 및 X25519 공유를 제공하고, `Library.tsx`·`System.tsx`·`Capture.tsx`를 `frontend/src/features/`로 분할(Vitest 100%)했으며, `.github/workflows/{ci,release,publish}.yml`에 검색 게이트와 Apple Silicon DMG 빌드·서명·SHA256SUMS 업로드를 추가했다.

릴리스 노트는 [docs/releases/RELEASE_NOTES_v12.8.0.md](docs/releases/RELEASE_NOTES_v12.8.0.md).

빌드 산출물은 `dist/ltcai-12.8.0-py3-none-any.whl`,
`dist/ltcai-12.8.0.tar.gz`, `ltcai-12.8.0.tgz`, `dist/ltcai-12.8.0.vsix`,
`src-tauri/target/release/bundle/dmg/Lattice AI_12.8.0_aarch64.dmg` 입니다.

## v12.7.0 — Fast Index (2026-11-16)

**Pre-Tokenized BM25F Sidecar (`Bm25SidecarIndex`)**, SQLite `bm25_node_stats` 영속화, **역색인 포스팅 리스트·GraphRAG 인접 캐시**, **제품 중심 `README.md` 개편 및 `v12.0.0+` 릴리스 히스토리 정리**를 포함한 릴리스. 문은 **422 오퍼레이션 / 41 패밀리**, 워커는 **20 라우트** (변동 없음). 쓰기는 계속 `GraphWriter` 하나다.

- **Pre-Tokenized BM25F Sidecar & `bm25_node_stats` SQLite 영속화.** `rust/lattice-core/src/bm25_stats.rs`, `db/tables.rs`, `graph_write/{mod,primitives,documents,curation,provenance}.rs`에 `bm25_node_stats` 스키마·`TableOwnership`을 등록하고 `GraphWriter::open`에서 `persist_bm25_sidecar`를 활성화해 노드 저장/민감도 갱신 시 필드 길이(`dl_title`, `dl_tags`, `dl_body`, `dl_meta`)와 토큰 빈도(`tf_json`)를 미리 영속화하며, 삭제·큐레이션·재임포트 시 자동 정리한다. `bm25_node_stats`가 없는 읽기 전용/동결 골든 DB는 안전하게 스킵해 호환성을 유지한다.
- **O(1) 사이드카 인덱스 & GraphRAG 인접 캐시.** `rust/lattice-retrieval/src/bm25/{sidecar,sidecar_cache,scoring,graph_rag,fusion,tokenizer,mod,tests}.rs` 및 `service_hybrid.rs`에서 `Bm25SidecarIndex`가 `(node_id, updated_at, content_hash)` 기준 증분 동기화, O(1) 필드 길이·용어 빈도 조회, 역색인 포스팅 후보 평가 및 `expand_knowledge_graph_with_sidecar` / `score_and_expand_with_sidecar`용 `title_to_doc_idxs`·`wikilink_to_doc_idxs`·`tag_to_doc_idxs`·`edge_adj` 캐시를 제공한다.
- **제품 중심 `README.md` 및 `v12.0.0+` 릴리스 정리.** `README.md`를 제품 소개·빠른 시작·8패널 Product Tour 중심으로 재구성하고, 로컬(`docs/releases/`, `docs/reviews/`, `RELEASE.md`, `RELEASE_NOTES.md`, `docs/CHANGELOG.md`, `FEATURE_STATUS.md`, `SECURITY.md`, `vscode-extension/README.md`)과 GitHub Releases에서 pre-v12 노트를 정리하며 `scripts/check_current_release_docs.mjs`에 `v12.0.0+` 하한을 적용했다.

릴리스 노트는 [docs/releases/RELEASE_NOTES_v12.7.0.md](docs/releases/RELEASE_NOTES_v12.7.0.md).

빌드 산출물은 `dist/ltcai-12.7.0-py3-none-any.whl`,
`dist/ltcai-12.7.0.tar.gz`, `ltcai-12.7.0.tgz`, `dist/ltcai-12.7.0.vsix`,
`src-tauri/target/release/bundle/dmg/Lattice AI_12.7.0_aarch64.dmg` 입니다.

## v12.6.0 — Hybrid Graph (2026-11-15)

**3-way BM25F + Vector + Knowledge Graph RRF 하이브리드 인덱싱·검색**, 그래프 인식 에이전트 스킬 및 MCP 검색 도구, **멀티스케일 서브워드 `FallbackTextEmbedder`**, **비인증 EmbeddingGemma 2 공개 미러 및 양자화(GGUF/MLX/ONNX) fallback**, **SSE 실시간 다운로드 속도(`MB/s`)·남은 시간(`ETA`) 스트리밍 UI**, **Pillow 13+ 레거시 제거**, **Ubuntu 24.04/26.04 `t64`·Chromium CI 복원력**, **모듈화 `scripts/bump_version/` CLI**를 통합한 릴리스. 문은 **422 오퍼레이션 / 41 패밀리**, 워커는 **20 라우트** (변동 없음). 쓰기는 계속 `GraphWriter` 하나다.

- **3-Way BM25F + Vector + Knowledge Graph RRF 하이브리드 검색.** `rust/lattice-retrieval/src/bm25/`, `service_hybrid.rs`, `hybrid.rs`, `search_api/handlers.rs`에 필드 가중 오카피 BM25F(`k1=1.2, b=0.75`, 한국어 조사 제거·CJK 바이그램·식별자 서브워드), 1-hop/2-hop 지식 그래프 구조 확장(`RELATES_TO`, `MENTIONS`, `PART_OF`, 공유 개념 노드, `[[wikilinks]]`, `#tags`), 3-way RRF 융합 및 `/api/search/hybrid` zero-hit rescue fallback을 구현했다.
- **그래프 스킬 및 에이전트·MCP 그래프 도구.** `rust/lattice-platform/src/toolsurface/tools/knowledge/`와 `rust/lattice-agent`(`tools/catalog.rs`, `prompts/mod.rs`)에서 `knowledge_search`/`obsidian_search`의 `mode`(`hybrid`/`bm25f`/`graph`/`graphrag`/`subgraph`), `hops`, `include_graph` 서브그래프 추출과 `knowledge_graph_search`/`knowledge_graph_context`, `GRAPH_SKILL_BRIEFS`를 지원한다.
- **멀티스케일 서브워드 `FallbackTextEmbedder` (#13.1).** `latticeai/core/embedding_providers/text.py`와 `rust/lattice-core/src/embeddings.rs`의 오프라인 임베더(`768d`, `lattice-fallback-subword-v2:768`)를 `3..5-gram` 서브워드, 한국어 조사 제거 음절·초성 쌍, CJK n-gram, 2-probe 부호 있는 Blake2b 투영 및 L2 정규화로 고도화했다.
- **EmbeddingGemma 2 비인증 공개 미러 및 양자화 fallback (#14.2).** `HF_TOKEN` 미설정 또는 gated `401`/`403` 응답 시 `mlx-community/embeddinggemma-2-4bit`, `mlx-community/embeddinggemma-2-8bit`, `ggml-org/embeddinggemma-2-GGUF`, `onnx-community/embeddinggemma-300m-ONNX` 공개 미러로 자동 전환하며 `HF_ENDPOINT` 커스텀 미러를 지원한다.
- **실시간 SSE 다운로드 진행률 스트리밍 UI (#15.2).** 다운로드 엔진(`download.py`, `rust/lattice-platform/src/modelops`)이 `downloaded_bytes`, `total_bytes`, `percent`, `speed_bps`, `eta_seconds`, `gated_fallback`을 SSE(`text/event-stream`)로 전송하고 `ProductFlow.tsx`·`InstallScreen.tsx`·`Library.tsx`에 실시간 진행률·속도(`MB/s`)·ETA·미러 안내를 시각화했다(Vitest 100% 커버리지).
- **Pillow 13+ 정리(#13.2), Ubuntu 24.04/26.04 `t64` CI 복원력(#14.1) & `scripts/bump_version/` CLI(#15.1).** `scripts/check_screenshot_pixel_delta.py`에서 `diff.getdata()` 레거시를 제거하고, `.github/workflows/*.yml`과 `scripts/check_ubuntu_runner_compat.mjs`에 `t64` 패키지 fallback 및 Chromium 검증을 추가했으며, `scripts/bump_version/` 패키지(`all`, `openapi`, `cargo`, `markers` 서브커맨드)로 모듈화했다.

릴리스 노트는 [docs/releases/RELEASE_NOTES_v12.6.0.md](docs/releases/RELEASE_NOTES_v12.6.0.md).

빌드 산출물은 `dist/ltcai-12.6.0-py3-none-any.whl`,
`dist/ltcai-12.6.0.tar.gz`, `ltcai-12.6.0.tgz`, `dist/ltcai-12.6.0.vsix`,
`src-tauri/target/release/bundle/dmg/Lattice AI_12.6.0_aarch64.dmg` 입니다.

## v12.5.0 — Deep Vector (2026-11-11)

Hugging Face에서 제공되는 **Google EmbeddingGemma 2**(`google/embeddinggemma-2`, 768차원)를 네이티브 임베딩 제공자로 전면 통합하고, 사용자 호스트 하드웨어(RAM/VRAM)를 정밀 측정하여 임베딩 모델 메모리를 사전 예약한 후 최적 LLM을 선정하는 맞춤형 모델 추천 시스템을 도입한 릴리스. Pillow 13+ `get_flattened_data()` 전환, GitHub Actions CI/CD 워크플로우의 Node 24 네이티브 업그레이드, `scripts/bump_version.py`의 OpenAPI 스펙·파편 49개 타깃 자동 갱신을 완료했다. 문은 **422 오퍼레이션 / 41 패밀리**, 워커는 **20 라우트** (변동 없음). 쓰기는 계속 `GraphWriter` 하나다.

- **Google EmbeddingGemma 2 네이티브 통합.** `latticeai/core/embedding_providers/text.py`에 Hugging Face 임베딩 프로바이더(`google/embeddinggemma-2`, `embeddinggemma-2`, `embeddinggemma-2-4bit`, `embeddinggemma-2-8bit`, `embeddinggemma-300m`, 768차원)를 구현하고 Mean pooling, L2 정규화, 태스크 접두사를 지원하며, 오프라인 시 결정론적 해시 임베딩 fallback을 유지했다.
- **PC 하드웨어 맞춤형 임베딩 모델 추천 시스템.** `rust/lattice-platform/src/modelops/models_catalog/embedding.rs` 및 `latticeai/services/embedding_recommendation.py`로 분리 모듈화하여 8GB 이상 호스트는 EmbeddingGemma 2(0.5GB~1.5GB RAM 예약), 미만 호스트는 EmbeddingGemma 300M(0.3GB RAM 예약)을 추천하며 가용 메모리(`effective_ram_gb`)를 사전 차감하여 LLM을 추천하도록 했다.
- **온보딩 및 모델 라이브러리 임베딩 UI.** 온보딩 화면(`ProductFlow.tsx`)에 PC 맞춤 EmbeddingGemma 2 추천 카드를 추가(다국어 i18n 지원)하고, 모델 라이브러리(`Library.tsx`)에 추천 임베딩 모델 섹션 및 원클릭 준비 버튼을 배치했다.
- **Pillow 13+ 권장 API 전환 및 CI/CD 현대화.** `scripts/check_screenshot_pixel_delta.py`에서 Pillow 14 제거 예정인 `diff.getdata()`를 `diff.get_flattened_data()`로 교체하고, GitHub Actions 워크플로우 7종 전체에 Node 24 네이티브 메이저 버전과 `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: "true"`를 적용했다.
- **OpenAPI 명세 및 파편 버전 동기화 자동화.** `scripts/bump_version.py`가 `frontend/openapi.json`, `rust/fixtures/openapi/_envelope.json`, 13개 패밀리 파편, `_index.json`(SHA-256 재계산) 등 총 49개 타깃을 일괄 갱신하도록 확장했다.
- **전체 인터랙티브 UI 점검 완료.** `tests/visual/v3.all_buttons_inspection.spec.js` Playwright E2E 스위트로 전체 라우트와 모든 버튼을 클릭 검증하여 0 콘솔 에러, 0 서버 에러를 확인했다.

릴리스 노트는 [docs/releases/RELEASE_NOTES_v12.5.0.md](docs/releases/RELEASE_NOTES_v12.5.0.md).

빌드 산출물은 `dist/ltcai-12.5.0-py3-none-any.whl`,
`dist/ltcai-12.5.0.tar.gz`, `ltcai-12.5.0.tgz`, `dist/ltcai-12.5.0.vsix`,
`src-tauri/target/release/bundle/dmg/Lattice AI_12.5.0_aarch64.dmg` 입니다.

## v12.4.0 — Sharp Weight (2026-11-11)

Hugging Face에서 검증된 **Qwen 3.8 27B 4-bit**(`mlx-community/Qwen3.8-27B-4bit`)와 **Gemma 4 26B A4B QAT / QAT-OptiQ 4-bit**(`mlx-community/gemma-4-26B-A4B-it-qat-4bit`, `mlx-community/gemma-4-26B-A4B-it-qat-OptiQ-4bit`)를 모델 추천 및 다운로드 카탈로그에 추가하고, Python(`pillow>=13,<14`)과 npm 의존성 보안 취약점을 전수 해소한 릴리스. 문은 **422 오퍼레이션 / 41 패밀리**, 워커는 **20 라우트** (변동 없음). 쓰기는 계속 `GraphWriter` 하나다.

- **Qwen 3.8 27B 4-bit 추가 및 멀티엔진 라우팅.** `mlx-community/Qwen3.8-27B-4bit` (`16.1GB`, `qwen3_5`)를 대형 티어에 추가하고, `ggml-org/Qwen3.8-27B-GGUF:Q4_K_M` 및 `Qwen/Qwen3.8-27B` 경로를 5개 로컬 엔진(`local_mlx`·`ollama`·`vllm`·`lmstudio`·`llamacpp`)과 클라우드 공급자(`together`·`openrouter`), Rust `CATALOG_HINTS`에 연결했다.
- **Gemma 4 26B A4B QAT & QAT-OptiQ 4-bit 추가.** Google 공식 QAT 기반의 `mlx-community/gemma-4-26B-A4B-it-qat-4bit` (`15.6GB`, `google/gemma-4-26B-A4B-it-qat-q4_0-gguf:Q4_0` 라우팅)와 민감도 기반 혼합 4/8-bit `mlx-optiq` 빌드인 `mlx-community/gemma-4-26B-A4B-it-qat-OptiQ-4bit` (`21.9GB`)를 추가했다.
- **Python · npm 의존성 보안 감사 해소 및 런타임 보강.** `pillow>=13,<14` 상향으로 `Pillow 12.3.0`의 39개 CVE를 해결하고, 루트 및 `vscode-extension`의 npm audit 취약점을 0건으로 정리했으며, `model_engines`의 HTTP(S) 스킴 검증, `lattice-platform` `admin_log_retention`의 주입형 시계(`state.auth.clock()`) 연동, `frontend/src/styles.css`의 `@import "tailwindcss" source("..");` 스캔 범위 제한을 적용했다.

릴리스 노트는 [docs/releases/RELEASE_NOTES_v12.4.0.md](docs/releases/RELEASE_NOTES_v12.4.0.md).

빌드 산출물은 `dist/ltcai-12.4.0-py3-none-any.whl`,
`dist/ltcai-12.4.0.tar.gz`, `ltcai-12.4.0.tgz`, `dist/ltcai-12.4.0.vsix`,
`src-tauri/target/release/bundle/dmg/Lattice AI_12.4.0_aarch64.dmg` 입니다.

## v12.3.0 — Live Wire (2026-10-19)

Hugging Face 실저장소 양자화 태그·GGUF 라우팅·클라우드 공급자 기본값을 전수 정렬하고, 작은 로컬 모델의 툴콜/MCP 인자 복구와 HTML 생성·점검·다운로드를 실모델(`mlx-community/gemma-4-e2b-it-4bit`)로 끝까지 검증한 릴리스. 문은 **422 오퍼레이션 / 41 패밀리**, 워커는 **20 라우트** (변동 없음). 쓰기는 계속 `GraphWriter` 하나다.

- **Hugging Face GGUF 태그 및 라우팅 정합성.** `ggml-org/gemma-4-*-GGUF`는 `Q4_0`, `ggml-org/gpt-oss-20b-GGUF`는 `MXFP4` 태그를 가리키며, `Qwen3.5-9B-MLX-4bit` (`unsloth/Qwen3.5-9B-GGUF`)와 `Qwen3.6-35B-A3B-4bit` (`ggml-org/Qwen3.6-35B-A3B-GGUF`) GGUF 경로가 `ollama` / `lmstudio` / `llamacpp`에 연결됐다.
- **클라우드·로컬 공급자 기본값 동기화.** `xai` 기본값을 `grok-4.6`, `together`를 `Qwen/Qwen3.6-27B`, `openrouter`를 `qwen/qwen3.6-35b-a3b`·`x-ai/grok-4.6`으로 맞추고, 존재하지 않는 `mlx-community/bge-m3`를 로컬 MLX 목록에서 제거했다.
- **보조 GGUF 필터링 · `--mmproj` 동반 로드 · 샤드 검증.** `mmproj-`/`mtp-`/`dflash-`/`eagle3-`/`imatrix` GGUF를 주 모델 선택에서 제외하고, `llama-server` 기동 시 동반 `mmproj-*.gguf`를 다운로드해 `--mmproj`로 전달하며, `model.safetensors.index.json`의 샤드 완결성을 검증한다.
- **작은 모델 툴콜·MCP 호출 보강.** `KNOWN_ACTIONS` 확장, 문자열화된 `arguments` 복구, OpenAI `tool_calls` 배열 언래핑, 코드블록 HTML 추출(`fenced_content_block`), `perform_action` 내 `adopt_named_args`(`html`/`code`/`source`/`data` → `content`) 적용.
- **HTML 구조 점검 및 RFC 5987 UTF-8 다운로드.** `POST /tools/inspect_html`이 `title`·`links`·`scripts`·`stylesheets`·`images`·`forms`·`headings`를 추출하고, `GET /tools/download`가 UTF-8 경로 디코딩과 `filename*=UTF-8''...` 헤더를 제공한다.

릴리스 노트는 [docs/releases/RELEASE_NOTES_v12.3.0.md](docs/releases/RELEASE_NOTES_v12.3.0.md).

빌드 산출물은 `dist/ltcai-12.3.0-py3-none-any.whl`,
`dist/ltcai-12.3.0.tar.gz`, `ltcai-12.3.0.tgz`, `dist/ltcai-12.3.0.vsix`,
`src-tauri/target/release/bundle/dmg/Lattice AI_12.3.0_aarch64.dmg` 입니다.

## v12.2.1 — True Count (2026-08-29)

검색·스킵·워치·작은 모델 답이 *있는 것만* 세는 패치. 문은 **422
오퍼레이션 / 41 패밀리**, 워커는 **20 라우트** (변동 없음). 쓰기는
계속 `GraphWriter` 하나다.

- **HNSW 사이드카 COUNT+델타.** 따뜻한 쿼리는 벡터 테이블을 통째로
  읽지 않는다. 늘어난 행만 불러 붙인다.
- **SkipByHash가 스탬프를 다시 찍는다.** 같은 바이트의 `touch`가 다음
  스캔에서 스탬프 스킵이 된다.
- **워치에서 사라진 파일은 그래프에서 지운다.** 디스크는 건드리지
  않는다. 폴더 인제스트는 여전히 보고만 한다.
- **얇은 요약은 읽은 파일에서 채운다.** 근거가 없으면 검토 대기.
- **콤팩트 9행에 MCP 코어가 남는다.** `api_key`는 과금 없이
  `GET /models`로 살아 있는지 확인하고, 안 되면 닫는다.
- **해시 검색은 해시라고 말한다.** 첫 실행·라이브러리가 의미 모델
  설치를 가리킨다.

릴리스 노트는 [docs/releases/](docs/releases/)에 모아 두었다. 이
버전: [docs/releases/RELEASE_NOTES_v12.2.1.md](docs/releases/RELEASE_NOTES_v12.2.1.md).

빌드 산출물은 `dist/ltcai-12.2.1-py3-none-any.whl`,
`dist/ltcai-12.2.1.tar.gz`, `ltcai-12.2.1.tgz`, `dist/ltcai-12.2.1.vsix`,
`src-tauri/target/release/bundle/dmg/Lattice AI_12.2.1_aarch64.dmg` 입니다.

정직한 고지: 해시 임베딩은 여전히 다운로드 없는 기본값(이제 정직하게
표시), DMG는 Apple Developer ID가 없으면 ad-hoc, Marketplace는 게시
직후 몇 분 검증이 걸릴 수 있다.

## v12.2.0 — Small Voice (2026-08-29)

2B급 로컬 모델이 MCP·스킬·도구를 고를 수 있게, 그리고 그 일을 홈 화면에서
바로 고를 수 있게 만든 릴리스. 문은 **422 오퍼레이션 / 41 패밀리**,
워커는 **20 라우트** (변동 없음).

- **툴콜 봉투.** `name`/`arguments`, Qwen `<function=…>` XML,
  `Action: mcp.grep`이 같은 호출이 된다. 콤팩트 액션 목록은 9행.
- **스킬 when.** 요청이 스킬의 "use when" 문구와 맞으면 이름을 안 해도
  안내문을 먼저 읽는다.
- **요청이 이미 말한 인자.** 따옴표 친 검색어가 빠진 `pattern`을 채운다.
- **홈 작곡기.** 폴더 찾기 · 파일 읽고 요약 · 기억 찾기 · 스킬 검토
  칩. 설정에 같은 설명이 한 카드로 있다.

12.1.0 Fast Path 인제스트 겹치기는 그대로다. 자세한 목록은
[docs/releases/RELEASE_NOTES_v12.2.0.md](docs/releases/RELEASE_NOTES_v12.2.0.md).

빌드 산출물은 `dist/ltcai-12.2.0-py3-none-any.whl`,
`dist/ltcai-12.2.0.tar.gz`, `ltcai-12.2.0.tgz`, `dist/ltcai-12.2.0.vsix`,
`src-tauri/target/release/bundle/dmg/Lattice AI_12.2.0_aarch64.dmg` 입니다.

## v12.1.0 — Fast Path (2026-08-29)

디스크에서 회상까지 줄을 세우던 경로를 겹친 릴리스. 쓰기 엔진은 그대로
하나고, 느렸던 것은 그 앞의 시임과 그 안의 락이다. 문은 **422
오퍼레이션 / 41 패밀리**, 워커는 **20 라우트** (변동 없음).

- **폴더·watch 인제스트 파이프라인.** 최대 4개 파일이 parse / extract /
  embed를 겹친다. 스탬프 스킵은 풀에 들어가지 않는다. 쓰기는 여전히
  `GraphWriter` 하나다.
- **노트당 `/worker/embed` 한 방.** 문서 벡터와 청크 벡터가 한 `texts`
  바디. extract는 그 앞에 서지 않고 옆에 선다.
  `write_vectors_with`가 그 벡터를 파일해서 해시 모델이 같은 텍스트를
  다시 돌리지 않는다.
- **rebuild가 임베드를 쓰기 락 밖에서.** 12.0.0 drain과 같은 모양.
- **검색 auto-HNSW.** env 기본은 여전히 `brute`. 512행 이상이고 워커
  시임이 묶여 있으면 `hnsw+rescore`를 먼저 시도하고, 실패하면 원래
  brute 블록으로 정확 스캔한다. 골든은 바이트 동일.
- **CI.** 주간 pip-audit가 깨끗한 런에서 리포트 파일 부재로 실패하던
  것, 주간 Postgres 잡이 삭제된 테스트 파일을 찾다 실패하던 것을
  고쳤다. `ltcai[postgres]`는 빈 extra 별칭.

정직한 고지: 작은 모델 내용 품질, `api_key` 모의 검증, ad-hoc DMG,
watch 자동 삭제 없음, 실임베딩 기본 롤아웃은 아직. 자세한 목록은
[RELEASE_NOTES_v12.1.0.md](docs/releases/RELEASE_NOTES_v12.1.0.md).

빌드 산출물은 `dist/ltcai-12.1.0-py3-none-any.whl`,
`dist/ltcai-12.1.0.tar.gz`, `ltcai-12.1.0.tgz`, `dist/ltcai-12.1.0.vsix`,
`src-tauri/target/release/bundle/dmg/Lattice AI_12.1.0_aarch64.dmg` 입니다.
와일드카드 업로드는 사용하지 않습니다.

## v12.0.0 — Open House (2026-08-18)

집을 정리해 손님을 들이는 릴리스. 가장 큰 두 크레이트를 도메인으로 나누고
(전부 `git mv`, 동작 변화 0), 처음 온 사람이 읽을 문서를 다시 썼으며,
11.9.0이 정직하게 적어 둔 갭 네 개를 닫았다. 문은 네이티브 **422
오퍼레이션 / 41 패밀리**(`POST /mcp` + 폴더 정리 라우트 가산), 워커는
**20 라우트**(`POST /worker/vector/query` 가산).

- **복잡도 관리(소유자 1순위)**: `lattice-agent` 43파일이
  `kernel`/`parse`/`content`/`tools`/`surface`/`prompts` 여섯 그룹으로,
  `lattice-platform` 31개 평면 모듈이 `workspaceos`/`toolsurface`/
  `governance`/`adminops`/`knowledge`/`modelops`/`shell` 일곱 도메인으로
  (100건 `git mv`). 두 크레이트 모두 크레이트 로컬 `ARCHITECTURE.md`를
  싣고, 각 그룹 `mod.rs`가 무엇이 속하고 무엇이 절대 들어가면 안 되는지와
  불변식을 적으며, 각 `src/lib.rs`가 호환 맵으로 끝나 기존 임포트 경로가
  전부 그대로 해석된다. `docs/DEVELOPMENT.md`는 기여자 온보딩(10분
  퀵스타트 + 어디에-무엇을 표 + 게이트 안내)으로 재작성,
  `docs/ROADMAP.md` 신설(우선순위 있는 갭 목록).
- **정직한 갭 4종 마감**: 복원이 스토어 세대(generation) 에폭으로
  인프로세스 즉시 반영(재시작 불필요) · `/setup/install`이 **서버가
  도출한 allowlist**의 항목에 한해 명시적 동의로 brew/pip/uv를 실행
  (기본은 여전히 수동) · `POST /mcp`가 OpenAPI 계약 안으로(단일 JSON-RPC
  봉투 오퍼레이션, 네이티브 마운트이므로 워커로 프록시되지 않음) · 포인터 도구가
  `pip install "ltcai[pointer]"`로 선언됨.
- **그래프 RAG 품질**: 한국어 2단계 조사 스트리핑 + 근거 게이트, 포함관계
  중복 제거, 방향 있는 타입드 엣지(`PART_OF`·`CONTRADICTS`를 실제로
  생산, 근거 분류), 섹션 트리(`Document ←PART_OF— Section —HAS_CHUNK→
  Chunk`, 트리플 555개 중 549개가 섹션 출처 보유), 임베딩 자동 감지
  (실모델 있으면 채택, 해시는 폴백이라고 표기, 벡터 정체성 `(model,dim)`
  필터 검증).
- **그래프 RAG 속도**: 무변경 재인덱스 33s → **0.26s**(낭비율 1.00 →
  0.00, 핑거프린트는 `ingestion_provenance`에 정착) · 첫 인덱싱 25.8s →
  7.2s · 드레인 ~66 → ~1,300 items/s(임베드를 트랜잭션 앞으로) · 백로그
  991건 40분 → 15.3초(적응형 스케줄러) · HNSW 증분 append + 검색 실사용
  (`LATTICEAI_VECTOR_INDEX=hnsw` → 워커 사이드카 후보 + Rust 정확 재스코어
  `hnsw+rescore`, 실패 시 사유를 실은 폴백; **기본은 여전히 brute**) ·
  vault-watch diff 스킵 · 삭제 파일 정리(`POST /api/ingestion/folder/prune`
  dry-run/confirm, 「삭제된 파일 정리 (N)」 카드, `delete_document_tree`로
  댕글링 0).
- **범용 소형모델 하네스(모델 불문, per-model 핵 금지)**: GUIDED 모드가
  JSON 요구 자체를 제거(번호 메뉴 → 인자 한 개씩 → 하네스가 액션 조립,
  꼬리는 모든 모드와 동일한 `perform_action`) · 측정 기반 프로브가
  standard/compact/guided를 고르고 모델 id + 크레이트 버전으로 캐시 ·
  미드런 하향 자기강등(위로는 절대 안 감) · 통합 카탈로그(native +
  `mcp.*` + `skill.*` 한 메뉴, mcp는 `POST /mcp`와 같은 거버넌스) ·
  시임 prefix 강제 + stop. Qwen **0.5B가 guided로 DONE**(3.9초, 실파일).
  모델 매트릭스 최종 수치는 릴리스 시점 `MATRIX_TABLE` 기준.
- **프론트**: ErrorBoundary 전면(라우트 + 헤비 패널, 「다시 시도」),
  Act/Brain 서브라우트 lazy 분할, 프리뷰 패턴 확장(권한 모드 diff
  프리뷰, 위험 기능 토글 ack), 번들 **104.2 / 150 KiB**.
- **고쳐진 기존 버그**: RAG 인용 지시가 에이전트 프롬프트로 새던 것,
  모델 로딩 이름 로스터 게이트(Qwen AWQ 로드 불가였음), 베낄 수 있는
  워크드 예제(`COPIED_EXAMPLE` fail-closed), `<|channel>` 한-파이프
  제어 프레임 누수.
- **정직한 고지**: 작은 모델의 *내용* 품질은 fail-closed 게이트가 잡는다
  (기계적 실행은 안정) · `api_key` 클라우드 경로는 여전히 모의 검증만
  (과금 0 정책) · DMG는 ad-hoc 서명 · 검색 기본은 brute(hnsw는 opt-in) ·
  watch는 자동 삭제하지 않음 · 크레이트 재구조는 이동이지 결합 해소가
  아님.

빌드 산출물은 `dist/ltcai-12.0.0-py3-none-any.whl`,
`dist/ltcai-12.0.0.tar.gz`, `ltcai-12.0.0.tgz`, `dist/ltcai-12.0.0.vsix`,
`src-tauri/target/release/bundle/dmg/Lattice AI_12.0.0_aarch64.dmg` 입니다.
와일드카드 업로드는 사용하지 않습니다.

상세: [RELEASE_NOTES_v12.0.0.md](docs/releases/RELEASE_NOTES_v12.0.0.md)
