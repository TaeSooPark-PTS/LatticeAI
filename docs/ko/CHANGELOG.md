[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../CHANGELOG.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./CHANGELOG.md)

# 변경 이력 (Changelog)

최상단 항목은 현재 릴리스 라인 또는 미배포 `main` 브랜치 작업 내역입니다.
이전 항목은 해당 릴리스 시점의 동작을 기록한 이력이며, 공개 변경 이력은 `v12.0.0` 이후 버전만 유지합니다.

## [13.5.0] - 2026-11-24 — Living Synthesis

### 추가됨
- **크로스플랫폼 원웨이 Qwen3.8 27B 4-bit 설치 및 가용 메모리 압박 자동 튜닝 (`latticeai/services/engine_adapters.py`, `latticeai/services/model_engines.py`, `latticeai/services/model_runtime/`, `rust/lattice-platform/src/modelops/`, `frontend/src/features/library/ModelsPanel.tsx`):**
  - Windows `winget`(`Ollama.Ollama`, `ggml.llamacpp`, `ElementLabs.LMStudio`) + `pip` `llama-cpp-python[server]` 폴백, Linux `curl` Ollama 설치 스크립트 + `pip` 폴백, macOS Apple Silicon `omlx` Homebrew 및 무-Homebrew GitHub Release universal2 휠 폴백(`~/.omlx/venv`)을 지원하는 `resolve_engine_installer()`를 추가했습니다.
  - 자동 크로스플랫폼 라우팅(`resolve_cross_platform_model_request`, `_recommended_with_engine_options`, Rust `probe.rs`, `scan.rs`, `probe_sysinfo.rs` Windows RAM 및 `winget` 감지), `unsloth/Qwen3.8-27B-GGUF` 공개 미러 폴백, `HF_XET_HIGH_PERFORMANCE=1` 고속 전송 기본 활성화를 적용했습니다.
  - macOS, Linux, Windows 실시간 가용 메모리 감지(`host_available_ram_gb`)를 통해 32GB 환경에서 동시 실행 앱 메모리 압박 시 `omlx_serve_args`가 자동으로 `--memory-guard safe`, `--hot-cache-max-size 1GB`, `--max-concurrent-requests 1`로 조정되도록 개선하고, GPU·양자화 KV 캐시(`q8_0`) 기반 `llamacpp_serve_args`를 추가했습니다.
  - `status.install_engine` 확인 토큰 자동 바인딩과 `ModelsPanel.tsx`의 `install_required` 동의 게이트를 연결해 원클릭 설치·다운로드·로드를 완성했습니다.

### 변경됨
- **TypeScript 7 (`tsgo`) 및 `@babel/parser` AST 전환 (`package.json`, `scripts/check_i18n_literals.mjs`, `scripts/run_openapi_typescript.mjs`):**
  - 루트 `typescript`를 `^5.9.3`에서 `^7.0.2`로 업그레이드하고, `scripts/check_i18n_literals.mjs`를 `@babel/parser` 기반으로 전환했으며, `openapi-typescript` 실행을 `scripts/run_openapi_typescript.mjs`로 격리했습니다.
- **모듈 리팩토링 및 데드코드·불필요 테스트 삭제 (`latticeai/services/{engine_adapters.py,model_engines.py,model_loading.py,model_catalog.py}`, `latticeai/core/policy.py`, `frontend/src/{routes.ts,lib/folderPicker.ts}`, `rust/Cargo.toml`):**
  - `model_engines.py`에서 `engine_adapters.py`(`EngineAdapter` 레지스트리)를 분리하고, `model_loading.py`의 동기/SSE 중복 분기를 단일 파이프라인으로 통합했습니다(`716 -> 492`줄).
  - 미사용 코드(`recommend_models_for_pc`, `capabilities_for_role`, `routeAliases`, `hasBrowserFolderPicker`, `rust/Cargo.toml`의 `walkdir`) 및 중복 테스트(`tests/unit/test_model_loading_deps.py`)를 삭제했습니다.

## [13.4.2] - 2026-11-24 — Living Synthesis

### 추가됨
- **oMLX 엔진 및 Qwen3.8 27B 4-bit 지원 (`latticeai/models/model_providers.py`, `latticeai/services/model_engines.py`, `latticeai/services/model_runtime/`, `latticeai/services/model_capability_catalog.py`):**
  - 계층형 RAM/SSD KV 캐시와 메모리 가드 자동 튜닝(`ensure_omlx_server`, `omlx_serve_args`), Hugging Face 캐시 `org--repo` 정규화, `/engines` 상태 API를 포함한 `omlx` 로컬 서버 엔진을 통합했습니다.
  - Apple M5 32GB 환경 실측 검증을 바탕으로 `mlx-community/Qwen3.8-27B-4bit`의 `recommended_ram_gb`를 `32.0`으로 조정하고, 추천 및 다운로드 카탈로그에 등록했습니다.

## [13.4.1] - 2026-11-23 — Living Synthesis

### 수정됨
- **메이저/브레이킹 의존성 업그레이드 호환성 해결 (`#58`, `#60`, `#61`):**
  - **프론트엔드 (`package.json`, `tsconfig.json`, `frontend/src/vite-env.d.ts`):** `overrides`를 통해 `openapi-typescript@7.13.0`과 `typescript@7.0.2` 피어 의존성 충돌을 해결하고, `tsconfig.json`에서 제거된 `baseUrl`을 정리하며, `vite/client` 타입 참조를 추가해 Vitest 5.1.1 100% 커버리지를 유지했습니다.
  - **Rust 워크스페이스 (`rust/`, `src-tauri/`):** `lattice-core`, `lattice-auth`, `lattice-agent`, `lattice-chat`, `lattice-ingest`, `lattice-retrieval`, `lattice-platform`을 `sha2` 0.11, `rusqlite` 0.43, `fancy-regex` 0.20, `axum` 0.8, `scrypt` 0.12, `zip` 9, `reqwest` 0.13, `tauri` 2.11.6에 맞게 업데이트했습니다.

## [13.4.0] - 2026-11-20 — Living Synthesis

### 추가됨
- **범용 적응형 에이전트 하네스 (`rust/lattice-agent/src/kernel/`):**
  - 모델별 하드코딩 분기 없이 3턴 런타임 마이크로 프로브(`probe.rs`)로 액션 계약 준수율·메뉴 선택·그라운딩 진실성을 실측(`agent_profile_probe.json` 캐시 및 런타임 강등 갱신)하도록 구현.
  - 관찰 우선 도구 순위화(`guided/ranking.rs`) 및 한/영 도메인 분류기(`guided/domain.rs`, `guided/args.rs`, `guided/suggestions.rs`)를 추가해 맹목적 실행보다 `computer_ui_inspect`, `web_search`, `web_fetch`, `read_file` 검사를 선행하도록 개선.
  - 명명된 UI 컨트롤의 바운딩 박스 중앙 좌표 `(x + w/2, y + h/2)`를 자동 산출하는 결정론적 해석(`execution/ui_resolve.rs`)과 JSON 구문 복구·ChatML 프리필(`execution/{repair.rs,mod.rs}`, `parse/action_salvage_text.rs`) 추가.
  - 폐기된(`is_superseded`) 노드를 배제하고 `[ACTIVE GROUNDED EVIDENCE]` 활성 증거만 요약·검증하는 분리 계층(`transcript/rag_grounding.rs`, `transcript/summaries.rs`) 및 폐쇄형 기권 검증 적용.
- **네이티브 웹 하네스 & 컴퓨터 유즈 브리지 정합성 (`rust/lattice-agent/src/tools/web/{mod.rs,html.rs,tests.rs}`, `latticeai/tools/{web.py,computer.py}`):**
  - Rust 웹 추출/HTML 파서 모듈화 및 Python 워커 웹·컴퓨터 유즈 스키마 패리티 완성(`tests/unit/test_web_and_cu_tools.py`, `rust/lattice-platform/tests/mcp_replay.rs`).
- **25개 태스크 실증 벤치마크 스위트 & 보고서 (`scripts/benchmark_adaptive_harness.py`, `rust/lattice-host/examples/harness_bench.rs`, `docs/ADAPTIVE_HARNESS_BENCHMARK.md`, `docs/ko/ADAPTIVE_HARNESS_BENCHMARK.md`):**
  - 로컬 `Qwen2.5-0.5B-Instruct-Q4_K_M` 대상 3군 비교 벤치마크에서 전체 통과율을 `28.0%`(Raw) / `24.0%`(비보정 Standard) → `84.0%`(`+56.0 pp`, 상대 `+200.0%` 향상, 컴퓨터 유즈 `100.0%`, RAG 그라운딩 `100.0%`, 도구 호출 `80.0%`, 웹 탐색 `80.0%`, 평균 지연 시간 `-63.3%`)로 실측·문서화(`tests/unit/test_adaptive_harness_benchmark.py`, `rust/lattice-agent/tests/adaptive_harness.rs`).
- **100% 영문 ↔ 한국어 양방향 문서 대등성 (`docs/ko/**`, `scripts/check_doc_languages.mjs`):**
  - GitHub에 노출되는 모든 마크다운 문서에 대해 순수 영문/순수 한국어 미러와 양방향 언어 전환 배지를 완성해 `npm run docs:check-languages` 100% 통과 달성.

## [13.3.1] - 2026-11-19 — Living Synthesis

### 추가됨
- `rust/lattice-ingest/src/incremental_sync.rs`를 2단계 `plan_incremental_sync` + `commit_incremental_sync`(`IncrementalSyncPlan`)로 분리하고 `rust/lattice-ingest/src/local_files_api/watch_deliver.rs`(`try_incremental_sync_async`)를 추출하여, 시맨틱 `EmbeddingIndexContract` 환경에서 감시 폴더 편집 시 변경된 청크만 비동기 `/worker/embed`를 호출하고 미변경 섹션 벡터를 재사용하도록 개선(`rust/lattice-ingest/tests/vault_watch_bridge.rs` 종단 간 검증).
- 주장 단위 근거 검증 텔레메트리(`MessageClaimVerification`, `parseGrounding` 파싱, `frontend/src/features/brain/AnswerProof.tsx` + `BrainConversation.tsx`의 이중 언어 `ClaimVerificationStrip`)를 추가하여 주장 지지율(%), 인용 정밀도(%), 수치·극성 불일치, 대체된(`superseded`) 출처 유출, 기권 권고를 100% Vitest 커버리지로 노출.
- `frontend/src/features/brain/BrainIntelligencePanel.tsx`에 이중 언어 `ConsolidationEpistemicSummary`를 추가하여 `entity_resolution`(스캔 개체, 병합 클러스터, 미리보기 후보 / 적용된 `SAME_AS` 엣지)과 `temporal_timeline`(활성·과거·미도달 사실 및 활성 시간 모순 배지)을 100% Vitest 커버리지로 표시.
- `pip`, `npm`(루트 + `vscode-extension`), `cargo`(`rust` + `src-tauri`), `github-actions` 주간 의존성 모니터링을 위한 `.github/dependabot.yml` 추가.

## [13.3.0] - 2026-11-19 — Living Synthesis

### 추가됨
- `WorkerSeamClient::post_idempotent_compute` 및 `post_idempotent_compute_with_policy`(`rust/lattice-core/src/worker.rs`)를 추가하고 `/worker/parse`, `/worker/extract`, `/worker/embed`, `/worker/render/*` 전반(`enrich.rs`, `garden_api/process.rs`, `knowledge_graph_api/ingest.rs`, `query_embed.rs`, `tools/meta.rs`, `security_dashboard/export.rs`, `lattice-chat/src/{intents,turn,worker}.rs`)에 결정적 SHA-256 `x-lattice-idempotency-key` 재시도를 연결.
- `lattice_retrieval::grounding::verify_grounding_and_abstention`을 `assess_answer_grounding`(`rust/lattice-chat/src/helpers.rs`)에 연결하여 기존 근거 판정과 함께 `claim_verification` 텔레메트리 방출.
- `lattice_core::entity_resolution`과 `lattice_core::temporal::query_temporal_timeline`을 `BrainIntelligenceService::consolidate`(`rust/lattice-retrieval/src/brain_api/consistency.rs`)에, 비차단 `DebouncedWatchEngine` + `sync_document_incremental`을 `rust/lattice-ingest/src/local_files_api/watch_bridge.rs`에, `DurableWorkflowStore` 원자적 체크포인트를 `rust/lattice-platform/src/governance/workflow_designer/executor.rs`에 연결.
- `rust/lattice-core/src/graph_write/clock.rs`에 안전한 로컬 시간 래퍼(`local_wall_parts`, `local_iso_for`, `naive_local_secs`, `local_offset_for_naive_wall`)를 추가하고 `lattice-platform` 및 `lattice-retrieval`의 중복 `unsafe libc` 블록 6개를 제거(`rust/lattice-retrieval/Cargo.toml`에서 미사용 `libc` 의존성 삭제).

### 변경됨
- `rust/lattice-platform/src/`에 남아 있던 비테스트 `.unwrap()` 호출 9개를 모두 제거(전체 Rust 워크스페이스 비테스트 `.unwrap()` `0`건 달성).
- 마지막으로 남아 있던 800줄 초과 프로덕션 소스 파일(`rust/lattice-chat/src/{cloud/provider.rs,intents.rs,helpers.rs}` → `provider_tests.rs`, `intents_tests.rs`, `helpers_tests.rs`; `lattice_brain/graph/_kg_common/extraction.py` → `extraction_triples.py`)을 분할하여 Rust·Python·프론트엔드 전반에서 800줄 초과 비테스트 파일 `0`건 달성.

### 수정됨
- `latticeai/api/worker_resilience.py`의 병합 대기자(coalesced-waiter) 오류 처리(`504`/`503` JSON), 응답 스트리밍 본문 타임아웃 커버리지, `BaseException`/`asyncio.CancelledError` 시 `_inflight` 해제를 수정하고 `tests/unit/test_worker_resilience.py`에 회귀 테스트 추가.
- `rust/lattice-ingest/src/local_files_api/ingest.rs`의 나노초 임시 파일 충돌 위험(`{pid}-{nanos}-{seq}`) 및 `persist_structure_and_multimodal_overlay` 오류 묵살(`structure_overlay_warning`)을 수정하고, `docs/releases/RELEASE_NOTES_v13.2.1.md`의 부정확한 파일명 정정.

## [13.2.1] - 2026-11-19 — Living Synthesis

### 변경됨
- `lattice-auth`, `lattice-core`, `lattice-ingest`, `lattice-retrieval`, `lattice-agent`, `lattice-platform`, `lattice-host`의 고밀도 Rust 모듈을 기능 단위로 세분화.
- `latticeai/services/model_capability_registry.py`(`model_capability_types.py`, `model_capability_catalog.py`), `latticeai/api/worker_compute.py`(`worker_compute_render.py`), `frontend/src/features/brain/brainData.ts`(`brainDataReaders.ts`, `brainDataReadiness.ts`)를 완전한 재내보내기 호환성을 유지하며 모듈화.
- Rust(`lattice-agent`, `lattice-platform`, `lattice-host`), Python(`latticeai/core/vector_index/sidecar.py`), 프론트엔드(`BrainGraphLayer`, `BrainMemoryLayer`, `BrainRelationshipLayer`, `graphLayout`, `ProductCommandCenter` 등)에서 미참조 헬퍼·빌더·폐기된 UI 레이어를 제거하면서 100% Vitest 커버리지 유지.

### 수정됨
- `.github/workflows/ci.yml` 시각 회귀 테스트 설정에서 Ubuntu `t64` Chromium 공유 라이브러리 조회를 일괄 처리하고 `15s` 미러 타임아웃 및 `mirrors.ubuntu.com` 장애 조치를 적용.

## [13.2.0] - 2026-11-19 — Living Synthesis

### 추가됨
- 이중 시간 지식 타임라인 및 충돌 진화 분류(`rust/lattice-core/src/temporal.rs`: `valid_from`..`valid_until`, `recorded_at`/`superseded_at`, `query_temporal_timeline`, `TemporalEvolution` / `ComplementaryRefinement` / `ActiveContradiction`)와 비파괴·롤백 가능한 정규 개체 통합(`rust/lattice-core/src/entity_resolution.rs`: `EntityAliasBook`, 한국어 조사 제거, `SAME_AS` 엣지, `rollback_entity_consolidation`) 추가.
- 주장 단위 근거 검증, 대체된(`superseded`) 출처 유출 차단, fail-closed 이중 언어 기권(`rust/lattice-retrieval/src/grounding.rs`) 및 `LocalEmbeddingModel`(`lattice-local-hash-v1`) 기반 검색/생성 분리 RAG 벤치마크·모델 교체 불변성 평가(`rust/lattice-retrieval/src/rag_eval_grounded.rs`) 추가.
- 이벤트 기반 디바운스 파일시스템 감시(`rust/lattice-ingest/src/incremental_watch.rs`), 청크 단위 증분 재임베딩 및 파일 이동 재배치(`rust/lattice-ingest/src/incremental_sync.rs`), 구조화된 표·페이지·슬라이드·시트·캡션 및 선택적 로컬 OCR/비전 수집(`rust/lattice-ingest/src/document_structure.rs`, `local_files_api/ingest.rs`) 추가.
- 내구성 체크포인트 DAG 에이전트 워크플로우 실행 엔진(`rust/lattice-agent/src/kernel/workflow.rs`) 추가: 비순환 검증, 읽기 병렬 / 쓰기 직렬 웨이브, SHA-256 멱등성 원장 재생, 단계 도중 크래시 복구, 재시작 간 상수 시간 `WaitingApproval` 일시 중지/재개, 쓰기 전 파일 롤백 스냅샷, 브레인 `USED_BY_AGENT` / `GENERATED_BY` 출처 내보내기 지원.
- 옵트인 멱등 워커 심 재시도(`rust/lattice-core/src/worker.rs`: `WorkerSeamClient::post_idempotent_json`, `WorkerRetryPolicy`), 무상태 Python 연산 타임아웃 격리·멱등 재생 병합(`latticeai/api/worker_resilience.py`, `worker_profile.py`), 섹션 16 종단 간 제품 검증 스위트(`rust/lattice-host/tests/roadmap_section16_suite.rs`, 16.1–16.4) 추가.

### 변경됨
- `rust/lattice-core/src/db.rs` 쓰기 소유권 문서를 네이티브 Rust `GraphWriter` 단일 쓰기 소유권과 무상태 Python 연산 구조에 맞게 갱신.

### 수정됨
- `rust/lattice-chat/src/boundary.rs`에서 외부 클라우드 문맥 페이로드의 비밀 파일 출처(`.env`, `.env.*`, `.pem`, `.key`, `.p12`, `.pfx`, `id_rsa*`, `id_ed25519*`, `credentials.json`; 단 `.example`/`.sample`/`.template` 제외)를 차단하고, `onedoor.rs` 및 `admin/{audit,handlers}.rs`에서 그래프 상태의 `empty`, `ok`, SQL 오류 `unavailable`을 명확히 구분.

## [13.1.0] - 2026-11-18 — Living Synthesis

### 추가됨
- 스코프 지정 지식 그래프 이웃 멀티홉 합성(`rust/lattice-retrieval/src/neighborhood_synthesis.rs`, `GET`/`POST /api/graph/node` `include_reasoning=true`), 대화형 `GraphNodeInspector`, `MultiHopSynthesisView.tsx`의 홉/모순 필터 필·신뢰도 바·클릭 가능한 노드 이동, `DigitalBrainExplorer.tsx`의 모순 전용 필터 및 `/` + `Escape` 단축키, `BrainIntelligencePanel.tsx`의 대화형 모순 제안/해결(`ContradictionInspector.tsx`) 추가.
- 원클릭 **브레인에 저장** 어시스턴트 인사이트 승격(`BrainConversation.tsx`), 인라인 변경 제안 diff 검사 및 승인/거절(`InlineStagedProposals.tsx`), 문맥형 `pendingChatPrompt` 전달(`BrainHome.tsx`), `AgentStepTimeline.tsx` 실시간 활성 도구 표시, `SourceChunkModal`(`AnswerProof.tsx`) 원클릭 지식 그래프 점프 및 원본 시각 증거 모달 미리보기 추가.
- `VectorFreshnessNotice`(`BrainSignals.tsx`)의 실시간 `임베딩 완료 / 전체` 진행률 시각화 및 원클릭 **지금 인덱싱**(`POST /api/index/drain`), `InstallScreen.tsx`·`ModelsPanel.tsx`(`useLibraryModels.ts`)의 읽기 쉬운 바이트/파일 지표 및 협조적 `AbortController` + `unloadModel` 다운로드 취소, `CommandPalette.tsx`의 `⌘K` 지식 그래프 노드 포커스 및 빠른 언어/그래프 액션, `WorkspaceProfileSwitcher.tsx`의 워크스페이스 전환 시 라이브 쿼리 무효화 추가.

### 수정됨
- `rust/lattice-retrieval/src/brain_api/health.rs`(`GET /api/brain/vector-freshness`)에서 `stale_embedder` 상태를 보존해 네이티브 Rust 서버에서도 임베더 교체 복구 안내가 종단 간 동작하도록 수정, `AnswerProof.tsx`의 인라인 원본 이미지 동의 버튼 클릭 이벤트 전파 차단, `InstallScreen.tsx` 폴백 오류 포맷 현지화(한국어 모드 영어 누출 0건, 100% Vitest 커버리지).

## [13.0.0] - 2026-11-18 — Sovereign Synthesis

### 추가됨
- 멀티홉 지식 그래프 추론 체인 및 이웃 모순/대체 합성(`rust/lattice-retrieval/src/bm25/reasoning_paths.rs`, `graph_rag.rs`, `fusion.rs`)과 대화형 2-Hop 합성 UI(`MultiHopSynthesisView.tsx`, `Brain.tsx`, `latticeApi.hybridSearchGraphRag`, 100% Vitest 커버리지) 추가.
- `rust/lattice-platform/src/governance/change_proposals.rs`에 자동 역방향 `folder_reorganization` 되돌리기 제안(`is_undo`, `undo_proposal_id`, `tests/reversible_reorganization.rs`)과 `ReviewCard.tsx`의 1-클릭 되돌리기 + 롤백 배지 UI 추가.
- `rust/lattice-chat/src/{documents.rs,state.rs}`에 내구성 대화 문서 세션 영속화(`chat_document_sessions.json`)와 `rust/lattice-platform/tests/domain_boundaries.rs`의 정적 크레이트/도메인 계층 경계 강제 추가.

### 변경됨
- `OnceLock` 컴파일 비밀 마스킹을 `rust/lattice-core/src/redact.rs`로 승격하고, `rust/lattice-agent/src/seam/worker.rs`를 추출해 `kernel/`과 `tools/`가 `surface/`를 임포트하지 않도록 분리했으며, `http_reply.rs`를 통해 `modelops` → `adminops` 결합을 해소하고 `lattice-chat`을 표준 `lattice_agent::{kernel,parse,content,tools,seam}` 경로로 이관.
- `review_items` 변경과 `timeline` 추가를 단일 원자적 `WorkspaceOsStore` 트랜잭션(`update_state_with_review_event`, `try_update_state_with_review_event`)으로 통합하고, `lattice-auth::messages`를 `lattice_core::messages`로 일원화(`check_server_i18n.mjs`가 146개 Rust 메시지 검증), `frontend/src/api/client.ts`(`streaming.ts`, `transfers.ts`)와 `src-tauri/src/backend.rs`(`backend_tests.rs`) 분할.

## [12.10.0] - 2026-11-17 — Grounded Self

### 추가됨
- 결정적 에이전트 트랜스크립트 근거 검증(`rust/lattice-agent/src/kernel/transcript/grounding.rs`, `agentloop/verification.rs`, `probe.rs`)을 추가해 모델별 분기 없이도 2B/8GB급 로컬 모델의 미읽기 파일 답변 및 환각 `path:line`·백틱 식별자 인용을 포착하고, `AnswerProof.tsx`에 대화형 인용 증거 배지 및 `browser-extension/popup.js`에 서버 언어 동기화 추가.
- 선호·습관·결정·성향·관계 및 인라인 `self_model_fact` 제안 승인/거절을 지원하는 Brain `SelfModelPanel`(100% Vitest 커버리지), `edges`·`edges_v2` 간 `PART_OF` 연쇄 삭제 검증(`graph_write_schema.rs`), 확장된 `cloud_lane.rs` / `rag_eval.rs` 테스트 스위트 추가.
- 협조적 `499` 모델 다운로드 취소 및 다운로드 후 시맨틱 임베딩 자동 활성화 + 적응형 일괄 재인덱싱 드레인 추가.

### 변경됨
- 크레이트 내부 `rust/lattice-platform/src/http_reply.rs`를 추출해 `knowledge/`와 `toolsurface/`가 `workspaceos::project_sessions`를 측면 참조하지 않도록 격리하고, `scripts/check_server_i18n.mjs`에서 6개 Python 워커 라우터 전체를 잠금(`0 not yet claimed`), 병렬 `cargo nextest` 러너(`scripts/run_rust_tests.mjs`) 추가, `.github/workflows/release.yml`에서 `publish.yml`(`workflow_call`) 자동 체이닝 구성.

## [12.9.0] - 2026-11-17 — Semantic Guard

### 추가됨
- `/api/search/hybrid`, `/memory/recall`, `/chat` 전반에 계약 정렬 시맨틱 쿼리 임베딩(`rust/lattice-retrieval/src/query_embed.rs`)과 순수 Rust `NativeHnswIndex`(`native_hnsw.rs`, `vector_hnsw.rs`), 워크스페이스/모델 스코프 Python HNSW 사이드카, `scripts/bench_retrieval_baseline.py` 추가.
- `local_only` / `cloud_allowed` 동의 및 민감도 규칙을 강제하는 서버 측 네트워크 경계·클라우드 슬라이스 가드(`network_boundary_guard.rs`, `boundary.rs`, `cloud/context.rs`, `agent_registry.rs`) 추가.
- 인증된 Workspace OS 실시간 SSE 스트리밍(`realtime.rs`, `useRealtimeStream.ts`) 및 `useAppBootstrap.ts` 추출(100% Vitest 커버리지).

### 변경됨
- Tauri 데스크톱 백엔드(`src-tauri/src/backend.rs`), `bin/ltcai.js` CLI 기동, `rust/lattice-host` 슈퍼바이저/게이트웨이 가드레일, 멀티스테이지 `Dockerfile`, `.latticebrain` v2 아카이브 KDF(`crypto.rs`, `kdf.rs`), CI / Sidecar E2E / 의존성 감사 워크플로우 강화 및 `AGENTS.md` 자율 태그·릴리스·CD 절차 반영.

## [12.8.0] - 2026-11-16 — Trusted Brain

### 추가됨
- 표준 `EmbeddingIndexContract`(`embedding_contract.rs`), 세분화된 `IngestStatus`, `KnowledgeLifecycleState`/`KnowledgeLifecycleMetadata`(`lifecycle.rs`), Rust 코어·Python 임베딩 워커 간 통합 `GraphWriter` 연쇄 삭제 추가.
- SQLite 워크스페이스/생명주기 사전 `LIMIT` 필터링, `vector_search_aligned`(`vector_alignment.rs`), 상한 `SidecarCachePool`(`DEFAULT_MAX_CACHE_BYTES`) 기반 서브워드 `Bm25SidecarIndex` 캐싱, `rag_eval.rs` 검색 게이트 추가.
- `/agent`, `/tools/*`, `/mcp/*`, `/skills/*`, `/cu/*`, `/api/automation/*`, `/api/browser/*` 전반에 서버 도출 fail-closed 격리(`IsolatedRootSpec`, `resolve_agent_scope`, `IsolationState`, `AgentBindState`) 및 엔터프라이즈 감사 커버리지 추가.
- 경로/심볼릭 링크 탈출 차단, X25519 공유 내보내기/수락 거버넌스, 복원 전 자동 백업을 갖춘 AEAD 암호화 `.latticebrain` 아카이브 포터빌리티(`archive.rs`, `crypto.rs`)와 정직한 `planned` PostgreSQL 보고(`postgres.rs`) 추가.
- `.github/workflows/ci.yml` 자동 PR 검색/사이드카 게이트 및 `.github/workflows/{release,publish}.yml` Apple Silicon Tauri DMG 빌드·코드서명/공증 훅·`SHA256SUMS` 발행 추가.

### 변경됨
- `Library.tsx`, `System.tsx`, `Capture.tsx`를 `frontend/src/features/` 아래 모듈 슬라이스로 리팩터링(108개 파일 100% Vitest 커버리지 유지)하고 구형 `scratchpad/` 파일 제거.

## [12.7.0] - 2026-11-16 — Fast Index

### 추가됨
- `rust/lattice-core/src/bm25_stats.rs`, `db/tables.rs`, `graph_write/`에 SQLite `bm25_node_stats` 사이드카 영속화를 추가하여, `GraphWriter`가 업서트·민감도 변경 시 사전 토큰화된 필드 길이(`dl_title`, `dl_tags`, `dl_body`, `dl_meta`)와 용어 빈도(`tf_json`)를 저장하고 삭제·큐레이션·교체 가져오기 시 정리하도록 구현(읽기 전용/동결 골든 DB에서는 미존재 테이블을 안전하게 건너뜀).
- 증분 `(node_id, updated_at, content_hash)` 동기화, O(1) 필드 길이·용어 빈도 조회, `expand_knowledge_graph_with_sidecar`, `score_and_expand_with_sidecar`를 지원하는 `Bm25SidecarIndex` 및 역색인/GraphRAG 인접 캐싱 추가.

### 변경됨
- `README.md`를 빠른 시작, 제품 투어, 문서 허브, 솔직한 경계, `v12.0.0+` 릴리스 이력 중심의 제품 우선 구성으로 개편.
- 로컬 및 GitHub Releases에서 v12 이전 릴리스 노트(`v9.*`–`v11.*`)와 사전 리뷰/계획 스냅샷을 정리하고 `scripts/check_current_release_docs.mjs` 및 `tests/unit/test_server_app_decomposition.py`에서 `v12.0.0+` 이력 하한선을 강제.

## [12.6.0] - 2026-11-15 — Hybrid Graph

### 추가됨
- 3중 **BM25F + Vector + Knowledge Graph RRF 하이브리드 인덱싱 및 GraphRAG 검색**을 구현(보정된 Okapi BM25F `k1=1.2, b=0.75`, 한국어 조사 제거, CJK 문자 바이그램, 1-hop/2-hop 지식 그래프 구조 확장, `/api/search/hybrid` 무결과 자동 구제 폴백).
- 동결된 422 오퍼레이션 / 41 패밀리 계약을 유지하면서 그래프 인식 에이전트 스킬 및 MCP 도구(`knowledge_search`, `obsidian_search`, `knowledge_graph_search`, `knowledge_graph_context`, `GRAPH_SKILL_BRIEFS`)에 `mode`, `hops`, `include_graph`, `subgraph` 페이로드 지원 추가.
- 비인증 공개 미러 및 양자화 GGUF/MLX/ONNX 폴백(`embeddinggemma-2-4bit/8bit`, `embeddinggemma-2-GGUF`, `embeddinggemma-300m-ONNX`)과 `HF_ENDPOINT` 커스텀 미러 해석 추가.
- Python 다운로드 런타임, Rust `modelops`, 프론트엔드(`ProductFlow.tsx`, `InstallScreen.tsx`, `Library.tsx`, `ko`/`en` i18n) 전반에 실시간 SSE 다운로드 진행률 스트리밍 추가(100% Vitest 커버리지).
- `.github/workflows/*.yml` 전반에 `scripts/check_ubuntu_runner_compat.mjs` 및 Debian/Ubuntu `t64` 전환 패키지 폴백 체인 추가.
- `scripts/bump_version.py`를 모듈형 `scripts/bump_version/` CLI 패키지로 리팩터링(`all`, `openapi`, `cargo`, `markers`, `--check` 지원).

### 변경됨
- 오프라인 무다운로드 `FallbackTextEmbedder`(`768d`, `lattice-fallback-subword-v2:768`)를 다중 스케일 `3..5`-gram 서브워드, CJK n-gram, 한국어 조사 제거·자모 초성 쌍, 단어 바이그램, 2-probe 부호 있는 Blake2b 투영, L2 정규화로 업그레이드.
- `scripts/check_screenshot_pixel_delta.py`에서 Pillow 12 이하 `diff.getdata()` 폴백을 제거하고 Pillow 13+ `diff.get_flattened_data()`로 통일.

## [12.5.0] - 2026-11-11 — Deep Vector

### 추가됨
- Google EmbeddingGemma 2(`google/embeddinggemma-2`, `embeddinggemma-2-4bit/8bit`, `embeddinggemma-300m`, 768차원) 네이티브 Hugging Face 임베딩 제공자(평균 풀링, L2 정규화, 태스크 접두사, 폴백 보호) 추가.
- LLM 선택 전 메모리를 선예약(Gemma 2: 0.5~1.5GB, 300M: 0.3GB)하는 하드웨어 맞춤 PC 임베딩 추천(`recommend_embedding_for_host`) 추가.
- `ProductFlow.tsx`에 PC 맞춤 EmbeddingGemma 2 추천 카드(i18n 지원) 및 `Library.tsx`에 원클릭 준비/다운로드 노출.
- 모든 라우트·컨트롤·탭·버튼의 무오류 동작을 검증하는 포괄적 시각 E2E 스위트 `tests/visual/v3.all_buttons_inspection.spec.js` 추가.

### 변경됨
- `scripts/check_screenshot_pixel_delta.py`를 Pillow 14 지원 `diff.get_flattened_data()`로 이관.
- 모든 GitHub Actions 워크플로우(`.github/workflows/*.yml`)를 Node 24 네이티브 메이저 버전(`FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: "true"`)으로 업그레이드.
- `scripts/bump_version.py`에서 49개 대상의 OpenAPI 스펙 및 픽스처 버전 동기화 자동화.

## [12.4.0] - 2026-11-11 — Sharp Weight

### 추가됨
- `mlx-community/Qwen3.8-27B-4bit`(`16.1GB`, `qwen3_5`), `mlx-community/gemma-4-26B-A4B-it-qat-4bit`(`15.6GB`), `mlx-community/gemma-4-26B-A4B-it-qat-OptiQ-4bit`(`21.9GB`)을 로컬 모델 카탈로그, 멀티 엔진 GGUF/HF 라우트, 클라우드 카탈로그, Rust `CATALOG_HINTS`에 추가.

### 변경됨
- `pyproject.toml`과 `requirements.txt`의 `pillow` 제약을 `>=13,<14`로 상향(`pillow 12.3.0` CVE 39건 해소)하고 `reportlab>=4,<5`, `huggingface_hub>=1.0,<2` 동기화.
- 루트 및 `vscode-extension` npm 의존성과 락파일 갱신(`npm audit` 취약점 0건).

### 수정됨
- `latticeai/services/model_engines.py`의 `_json_request` 및 `get_openai_compatible_server_models`에서 비-`http(s)://` URL 스킴 거부.
- `rust/lattice-platform/src/adminops/admin/handlers.rs`의 `admin_log_retention`이 실제 시스템 시계 대신 `state.auth.clock().now()`를 읽도록 수정해 결정적 감사 픽스처 드리프트 제거.
- `frontend/src/styles.css`에 `@import "tailwindcss" source("..");`를 적용해 `@tailwindcss/oxide`가 `rust/target`을 스캔하지 않도록 범위 제한.

## [12.3.0] - 2026-10-19 — Live Wire

### 추가됨
- `POST /tools/inspect_html` 전체 HTML 구조 파서 추가(`title`, `links`, `scripts`, `stylesheets`, `images`, `forms`, `headings` `h1`–`h6`, `MAX_FILE_BYTES` 제한).
- `GET /tools/download`에 RFC 5987 `filename*=UTF-8''...` 헤더 및 UTF-8 퍼센트 디코딩 추가.
- `mlx-community/Qwen3.5-9B-MLX-4bit` 및 `mlx-community/Qwen3.6-35B-A3B-4bit`에 대한 검증된 GGUF 라우팅(`ollama`, `lmstudio`, `llamacpp`) 추가.
- `ensure_llamacpp_server`의 동반 `mmproj-*.gguf` 다운로드 및 `--mmproj` 플래그, `is_auxiliary_gguf` 필터링(`mmproj-`, `mtp-`, `dflash-`, `eagle3-`, `imatrix`), `model.safetensors.index.json` 샤드 완전성 검증(`_shards_complete`) 추가.
- `lattice-agent` 소형 모델 도구/MCP 호출 내성 강화: `KNOWN_ACTIONS` 확장, 문자열화된 `arguments` 복구(후행 쉼표·절단·Python dict), OpenAI `tool_calls` 배열 언래핑, `write_file`/`local_write` 펜스 HTML/코드 블록 구제, `perform_action`의 `adopt_named_args` 추가.

### 변경됨
- `_gguf_routes`가 저장소별 `ollama_tag`(`gemma-4` → `Q4_0`, `gpt-oss-20b` → `MXFP4`)를 지원하도록 개선.
- 클라우드 및 로컬 제공자 기본값을 최신 모델(`grok-4.6`, `Qwen/Qwen3.6-27B`, `qwen/qwen3.6-35b-a3b`, `google/gemma-4-12B-it` 등)로 갱신.

### 수정됨
- `LOCAL_MLX_MODELS`에서 존재하지 않는 `mlx-community/bge-m3`를 제거(`OLLAMA_EMBEDDING_MODELS`의 `bge-m3`는 유지).
- `/tools/download`에서 비-ASCII 퍼센트 인코딩 경로의 Latin-1 바이트 손상 수정.

## [12.2.1] - 2026-08-29 — True Count

### 추가됨
- HNSW 사이드카 `COUNT`+델타 로딩: 웜 쿼리 시 `vector_embeddings` 전체를 덤프하지 않고 누락된 ID만 조회해 추가.
- 해시 기반 스킵 시 `GraphWriter`를 통해 기존 출처 레코드의 크기/mtime 재스탬핑.
- 감시 정리(Watch prune): 사라진 감시 파일을 그래프에서 자동 정리(`delete_document_tree`, `confirm=true`; 디스크는 절대 삭제하지 않음).
- `complete_a_summary`: 빈약한 "요약했습니다" 답변을 트랜스크립트에 이미 존재하는 `read_file` / `mcp.read_file` 결과로 보강.
- 컴팩트 카탈로그 CORE가 9행 상한 내에서도 `mcp.grep`, `mcp.list_dir`, `mcp.read_file`, `mcp.knowledge_search`를 유지하도록 조정.
- `api_key` 클라우드 `GET /models` 무과금 프로브 추가(연결 불가 키는 fail-closed).
- 첫 실행 및 라이브러리 안내에서 해시 검색을 해시로 명시하고 시맨틱 모델 전환 경로 제공.

### 변경됨
- 버전별 `RELEASE_NOTES_v*.md`를 `docs/releases/`로 정리.
- `README.md`를 사용자 우선(설치 → 첫 5분 → 릴리스) 흐름으로 재구성.

### 수정됨
- 로드된 HNSW 그래프가 신규 항목만으로 재구축되어 기존 레이블을 덮어쓰던 문제를 증분 append로 수정.

## [12.2.0] - 2026-08-29 — Small Voice

### 추가됨
- 함수 호출 봉투 파싱(`name`/`arguments`, 중첩 `function`, Qwen `<function=…><parameter=…>`, `Action: mcp.grep`) 추가.
- 컴팩트 실행 도구 목록을 `guided` 메뉴 크기(9)로 제한하고, 요청과 `when` 조건이 일치하는 스킬은 `skill.`을 명시하지 않아도 자동 참조.
- 따옴표로 감싼 검색어로 누락된 MCP/네이티브 `pattern`/`query` 자동 보완.
- 홈 컴포저에 폴더 검색, 읽기-요약, 기억, 스킬 검토 칩 추가 및 MCP/스킬 타임라인 행에 평문 접두사 표시.

### 변경됨
- 컴팩트 프롬프트가 2B 모델이 출력하는 함수 호출 봉투 형식을 명시하도록 조정.

### 수정됨
- 레이블 구제 파서가 점(`.`)이 포함된 `mcp.` / `skill.` 이름을 잡담으로 거부하던 문제 수정.

## [12.1.0] - 2026-08-29 — Fast Path

### 추가됨
- 폴더 수집과 볼트 감시가 최대 4개 파일을 병렬 처리(`INGEST_INFLIGHT`); 스탬프 스킵 파일은 풀에 진입하지 않음.
- 노트당 단일 `POST /worker/embed` 호출(문서 본문 + 모든 청크) 및 추출과 임베딩의 병렬 실행.
- 저장소에 512개 이상 벡터가 있고 워커 사이드카가 연결된 경우 기본 `brute` 검색도 `hnsw+rescore`를 우선 시도(실패 시 기존 `brute` 인덱스 블록 유지).

### 변경됨
- `rebuild_vector_index`가 쓰기 트랜잭션 밖에서 임베딩을 수행하도록 변경.
- `write_vectors_with`가 수집 시 전달된 벡터를 그대로 기록해 해시 모델의 중복 계산 방지.
- `ltcai[postgres]`를 빈 별칭으로 정리(라이브 Postgres 쓰기는 11.6.0에서 제거됨).

### 수정됨
- 발견 항목이 없을 때 주간 `pip-audit`이 마크다운 보고서 누락으로 실패하던 문제 수정.
- 삭제된 `tests/integration/test_postgres_migration_live.py`로 인해 주간 Postgres 통합 작업이 실패하던 문제 수정.

## [12.0.0] - 2026-08-18 — Open House

### 추가됨
- 크레이트 도메인 구조: `lattice-agent` 43개 파일을 6개 그룹(`kernel` / `parse` / `content` / `tools` / `surface` / `prompts`)으로, `lattice-platform` 31개 평면 모듈을 7개 도메인(`workspaceos` / `toolsurface` / `governance` / `adminops` / `knowledge` / `modelops` / `shell`, 100건 `git mv`)으로 재구조화. 순수 이동으로 동작 변화 0 — 골든 및 계약 테스트가 재구조 전후 동일한 결과를 반환.
- 크레이트 로컬 아키텍처 지도 2종(`rust/lattice-agent/ARCHITECTURE.md`, `rust/lattice-platform/ARCHITECTURE.md`) 추가. 각 그룹 `mod.rs`에 소속 규칙과 불변식을 명시하고, `src/lib.rs` 호환 맵으로 기존 임포트 경로 전체 유지.
- 우선순위(P1/P2/P3)·난이도·제안 경로를 담은 `docs/ROADMAP.md` 추가(이번 릴리스에서 해결된 항목은 닫힘으로 표기).
- `GUIDED` 프로파일: 번호 메뉴로 액션을 고르고 인자를 한 턴씩 받아 **하네스가 액션 구조체를 조립**하여 JSON 요구를 완전히 제거. 실행 꼬리는 모든 모드와 동일한 `perform_action`을 사용해 게이트 체인·루프 가드·쓰기 전 스냅샷·`sanitize_write_content`를 유지하며, 검증 역시 닫힌 `PASS`/`FAIL` + 한 줄 사유를 동일한 근거·커버리지 게이트로 심사.
- 측정 기반 프로파일 프로브(`kernel/probe.rs`): 모델 로드 시 고정 문항 2개를 루프 자신의 파서로 채점해 `standard` / `compact` / `guided`를 선택하고 모델 ID + 크레이트 버전으로 캐시(`LATTICEAI_AGENT_PROFILE` 최우선, `LATTICEAI_AGENT_PROBE=0`으로 비활성화 가능).
- 미드런 단방향 자가 강등(`demote_to_guided`): 측정된 다이얼, 아직 `guided` 아님, 실행 근거 없음의 3가지 조건에서만 하향 강등.
- 통합 도구 카탈로그(`tools/catalog.rs`): 네이티브 도구 + `mcp.<tool>` + `skill.<name>`을 하나의 번호 메뉴로 통합. 런이 거버넌스하는 이름은 더 엄격한 네이티브 경로로, 그 외는 `POST /mcp`와 동일한 거버넌스 검사를 통과.
- `POST /worker/vector/query` 추가 — HNSW 사이드카 후보 조회로 워커 allowlist를 19 → **20**개로 확장.
- `POST /api/ingestion/folder/prune` 추가 — 삭제된 파일 정리(`confirm` 없으면 드라이런, 있으면 `delete_document_tree`로 양방향 엣지 정리). SPA에 「삭제된 파일 정리 (N)」 버튼 제공.
- 섹션 트리(`Document ←PART_OF— Section ←PART_OF— Section`, `Section —HAS_CHUNK→ Chunk`) 추가(제목이 없는 문서는 인위적 섹션을 생성하지 않음).
- 별도 테이블 없이 `ingestion_provenance.metadata_json`에 파일별 수집 지문(경로 + 크기 + mtime + 내용 SHA-256) 저장.
- 스토어 세대(generation) 에폭 추가 — 데이터베이스 복원이 재시작 없이 인프로세스로 즉시 반영.
- 6개 `pyautogui` 포인터 도구의 공식 설치 경로인 `ltcai[pointer]` extra 선언.

### 변경됨
- `POST /mcp`를 단일 JSON-RPC 봉투 오퍼레이션으로 OpenAPI 제품 계약 **내부**에 편입(네이티브 마운트이므로 워커로 프록시되지 않음). 단일 관문은 네이티브 **422 오퍼레이션 / 41 패밀리**(11.9.0의 420 + `/mcp` + folder prune).
- `/setup/install`이 **서버가 도출한** allowlist에 포함된 항목에 한해 명시적 동의 후 실제 설치 수행(목록 밖 항목은 거부, 기본값은 수동 유지).
- 2단계 한국어 조사 제거 + 근거 게이트 + 포함관계 중복 제거 적용.
- 추출기가 `PART_OF` / `CONTRADICTS`를 실제로 생성하며, 방향·타입·분류된 근거와 함께 엣지 저장.
- 임베딩 자동 감지 — 다운로드된 실모델이 있으면 채택하고 해시는 폴백으로 명시. 벡터 정체성은 `(model, dim)`으로 필터링.
- 임베딩 생성을 쓰기 트랜잭션 **앞**으로 이동(드레인 ~66 → ~1,300 items/s)하고 적응형 스케줄러 적용(991건 백로그 40분 → 15.3초).
- 미변경 재인덱싱 33초 → **0.26초**(낭비율 1.00 → 0.00), 최초 인덱싱 25.8초 → 7.2초 단축; 볼트 감시도 미변경 노트 스킵.
- `LATTICEAI_VECTOR_INDEX=hnsw`를 실제 검색 경로로 전환 — 사이드카가 `k * 8` 후보(최대 200)를 반환하고 Rust가 `brute`와 동일한 코사인으로 정확 재스코어(`hnsw+rescore`). 사이드카 증분 append 지원, **기본값은 여전히 `brute`**, 실패 시 사유를 포함한 폴백.
- `Completion::prefix`로 완성의 첫 바이트 강제(`compact`는 `{"thoughts": "`). 토큰 단위 JSON 문법은 검토 후 채택하지 않음.
- 프론트엔드: 모든 라우트 및 헤비 패널에 `ErrorBoundary` 적용, Act/Brain 서브라우트 지연 분할, 권한 모드 diff 미리보기 및 위험 토글 확인 추가(번들 104.2 / 150 KiB).

### 수정됨
- RAG 인용 지시가 에이전트 프롬프트로 유출되어 도구를 호출해야 할 턴에 모델이 출처를 표기하던 문제 수정.
- 모델 로딩의 이름 로스터 게이트 때문에 Qwen AWQ 빌드가 로드되지 않던 문제 수정.
- `write_file` 본문이 내장 예제 상수와 일치할 경우 `COPIED_EXAMPLE`로 기록하고 쓰지 않도록 차단(fail-closed), `guided` 답변에서 되풀이된 지시문 줄 제거.
- 단일 파이프 `<|channel>` 제어 프레임이 출력 본문으로 새던 문제 수정.
- 복원 이후 오래된 풀 커넥션이 복원 전 바이트를 반환하던 문제 수정(세대 에폭으로 해소).

### 알려진 이슈
- 소형 모델의 *내용 품질*은 여전히 fail-closed 게이트가 검사합니다. 기계적 실행(파일 쓰기, `DONE` 도달)은 `guided`로 안정화되어 0.5B 모델도 3.9초 만에 실제 파일을 작성하지만, 요약의 질이 부족하면 검증기에서 `FAILED` / `NEEDS_REVIEW`로 종료될 수 있습니다.
- `api_key` 클라우드 경로는 모의 서버 기준 검증(무과금 정책)이며 라이브 검증은 `cli_oauth`로 수행되었습니다.
- 서명 시크릿이 없을 때 macOS DMG는 ad-hoc 서명(=미서명)으로 생성됩니다.
- 검색 기본값은 `brute`이며 `hnsw`는 옵트인입니다.
- 감시 모드는 파일을 자동 삭제하지 않으며 명시적 `prune` 경로로만 정리합니다.
- 크레이트 재구조화는 이동이며 완전한 결합 해소가 아닙니다(`lattice-platform`의 도메인 간 결합 4건은 각 `mod.rs`에 명시).
- `GraphWriter::delete_node`는 `PART_OF` 엣지를 남기므로 폴더 정리는 `delete_document_tree`를 사용합니다.
