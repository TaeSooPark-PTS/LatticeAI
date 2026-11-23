[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../RELEASE_NOTES.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./RELEASE_NOTES.md)

# [v13.5.0 - Living Synthesis] (2026-11-24)

macOS(`omlx` Homebrew 및 무-Homebrew universal2 휠 폴백, `local_mlx`), Windows(`winget` Ollama / llama.cpp / LM Studio 및 `pip` 폴백), Linux(Ollama 설치 스크립트 및 `pip` / `vllm`) 전 환경 원클릭 **Qwen3.8 27B 4-bit** 설치·다운로드·실행과 실시간 가용 메모리 압박 자동 튜닝을 통합하고, 워크스페이스 타입체커를 **TypeScript 7.0.2 (`tsgo`)**로, `scripts/check_i18n_literals.mjs`를 `@babel/parser`로 전환했으며, `engine_adapters.py` 및 `model_loading.py` 모듈 리팩토링과 데드코드·불필요 테스트 정리를 완료했습니다.

상세 릴리스 노트: [docs/ko/releases/RELEASE_NOTES_v13.5.0.md](./releases/RELEASE_NOTES_v13.5.0.md).

# [v13.4.2 - Living Synthesis] (2026-11-24)

계층형 RAM/SSD KV 캐시를 지원하는 **oMLX (`jundot/omlx`)** 엔진을 통합하고, **`mlx-community/Qwen3.8-27B-4bit`** 모델을 32GB+ Apple Silicon Mac의 추천 및 다운로드 가능 카탈로그에 등록했습니다.

상세 릴리스 노트: [docs/ko/releases/RELEASE_NOTES_v13.4.2.md](./releases/RELEASE_NOTES_v13.4.2.md).

# [v13.4.1 - Living Synthesis] (2026-11-23)

프론트엔드(`typescript@7.0.2`, `vitest@5.1.1`, `jsdom@31`, `@testing-library/jest-dom@8`), Rust 워크스페이스(`axum@0.8`, `rusqlite@0.43`, `sha2@0.11`, `scrypt@0.12`, `fancy-regex@0.20`, `zip@9`, `reqwest@0.13`, `ed25519-dalek@3`, `notify@8`), Tauri 데스크톱(`tauri@2.11.6`, `plist@1.8.0`, `time@0.3.55`)의 메이저/브레이킹 의존성 업그레이드 호환성을 해결하고 Vitest 100% 커버리지 및 전체 워크스페이스 빌드를 복구했습니다.

상세 릴리스 노트: [docs/ko/releases/RELEASE_NOTES_v13.4.1.md](./releases/RELEASE_NOTES_v13.4.1.md).

# [v13.4.0 - Living Synthesis] (2026-11-20)

모델별 코드 분기 없는 범용 적응형 에이전트 하네스(`probe.rs` 3턴 마이크로 프로브, 관찰 우선 도구 순위화·한/영 도메인 분류, 결정론적 UI 바운딩 박스 중앙 좌표 해석 `ui_resolve.rs`, JSON 구문 복구·ChatML 프리필, 활성/폐기 증거 분리 `rag_grounding.rs`), 모듈화된 네이티브 웹 하네스·컴퓨터 유즈 브리지 정합성, 로컬 `Qwen2.5-0.5B-Instruct-Q4_K_M` 대상 25개 태스크 실측 통과율을 `28.0%`(Raw) / `24.0%`(Standard) → `84.0%`(`+56.0 pp` / 상대 `+200.0%`, 컴퓨터 유즈 `100.0%`, RAG 그라운딩 `100.0%`, 지연 시간 `-63.3%`)로 향상시킨 실증 벤치마크, 그리고 100% 한/영 양방향 GitHub 마크다운 대등성을 적용했습니다.

상세 릴리스 노트: [docs/ko/releases/RELEASE_NOTES_v13.4.0.md](./releases/RELEASE_NOTES_v13.4.0.md) · 벤치마크 보고서: [docs/ko/ADAPTIVE_HARNESS_BENCHMARK.md](./ADAPTIVE_HARNESS_BENCHMARK.md).

# [v13.3.1 - Living Synthesis] (2026-11-19)

시맨틱 임베딩 계약용 2단계 비동기 증분 청크 동기화(`plan_incremental_sync` + 비동기 `/worker/embed` + `commit_incremental_sync`, `watch_deliver.rs`), `AnswerProof.tsx` 주장 검증 텔레메트리(`claim_verification`) 및 `BrainIntelligencePanel.tsx` 개체 통합·시간축 요약 카드(한/영 i18n · Vitest 100%), 전 스택 주간 `.github/dependabot.yml` 자동 감시를 적용했습니다.

상세 릴리스 노트: [docs/ko/releases/RELEASE_NOTES_v13.3.1.md](./releases/RELEASE_NOTES_v13.3.1.md).

# [v13.3.0 - Living Synthesis] (2026-11-19)

v13.2.0 핵심 서브시스템(`post_idempotent_compute`, `verify_grounding_and_abstention`, `entity_resolution`·`temporal`, `DebouncedWatchEngine`·`sync_document_incremental`, `DurableWorkflowStore`)을 실제 HTTP·채팅·감시·워크플로 경로에 전면 연결하고, `worker_resilience.py` 동시성/취소/스트리밍 타임아웃 버그 및 `ingest.rs` 임시 파일 충돌·오버레이 오류 침묵을 수정했으며, Rust 비테스트 `.unwrap()` 0건 달성, 중복 `unsafe libc` 시간 블록 7개 단일화(`clock.rs`), 전 스택 800줄 초과 프로덕션 파일 0건 달성(Vitest 100% 유지)을 완료했습니다.

상세 릴리스 노트: [docs/releases/RELEASE_NOTES_v13.3.0.md](../releases/RELEASE_NOTES_v13.3.0.md).

# [v13.2.1 - Living Synthesis] (2026-11-19)

Rust 전 워크스페이스(`lattice-auth`, `lattice-core`, `lattice-ingest`, `lattice-retrieval`, `lattice-agent`, `lattice-platform`, `lattice-host`)와 Python(`model_capability_registry.py`, `worker_compute.py`), Frontend(`brainData.ts`) 대형 모듈 분할 리팩터링, 전 스택 미참조 데드 코드 및 구형 오비탈 Brain 레이어 제거(Vitest 100% 유지), Ubuntu `t64` 시각 회귀 CI 미러 페일오버 하드닝을 적용했습니다.

상세 릴리스 노트: [docs/releases/RELEASE_NOTES_v13.2.1.md](../releases/RELEASE_NOTES_v13.2.1.md).

# [v13.2.0 - Living Synthesis] (2026-11-19)

이중 시간축(Bi-temporal) 지식 그래프·시점 조회 및 비파괴 한/영 개체 통합(`temporal.rs`, `entity_resolution.rs`), 주장 단위 근거 검증·기권(`grounding.rs`) 및 검색/생성 분리형 모델 무관 RAG 평가(`rag_eval_grounded.rs`), OS 이벤트 디바운스 감시·청크 단위 증분 재임베딩·표/페이지/OCR 구조 보존 수집(`incremental_watch.rs`, `incremental_sync.rs`, `document_structure.rs`), 원자적 체크포인트·멱등 원장·재시작 승인 복구·롤백을 갖춘 내구성 DAG 에이전트 워크플로 엔진(`kernel/workflow.rs`), 워커 멱등 재시도·타임아웃 격리(`worker.rs`, `worker_resilience.py`)·클라우드 비밀 파일 차단(`boundary.rs`)·정직한 그래프 상태 프로브(`onedoor.rs`, `audit.rs`) 및 Section 16 종단간 제품 검증 스위트(`roadmap_section16_suite.rs`, 16.1–16.4)를 적용했습니다.

상세 릴리스 노트: [docs/releases/RELEASE_NOTES_v13.2.0.md](../releases/RELEASE_NOTES_v13.2.0.md).

# [v13.1.0 - Living Synthesis] (2026-11-18)

지식 그래프 인터랙티브 노드 인스펙터·단일 조회 2-Hop 이웃 추론 합성(`neighborhood_synthesis.rs`, `GraphNodeInspector.tsx`, `MultiHopSynthesisView.tsx`)·상충 필터 및 원클릭 상충 기억 제안/해결(`ContradictionInspector.tsx`), 채팅 답변 원클릭 **Brain에 기억하기** 승격·인라인 변경 제안 diff 승인/거절(`InlineStagedProposals.tsx`)·실시간 도구 단계 표시 및 출처 모달 그래프 점프(`AnswerProof.tsx`), 임베딩 진행률 바·즉시 인덱싱 드레인(`POST /api/index/drain`)·`stale_embedder` 보존(`health.rs`), 모델 다운로드 바이트/파일 표시 및 협력적 취소(`InstallScreen.tsx`, `ModelsPanel.tsx`), `⌘K` 지식 노드 포커스·작업공간 즉시 쿼리 무효화 및 한/영 무누수 로컬라이제이션(Vitest 100%)을 적용했습니다.

상세 릴리스 노트: [docs/releases/RELEASE_NOTES_v13.1.0.md](../releases/RELEASE_NOTES_v13.1.0.md).

# [v13.0.0 - Sovereign Synthesis] (2026-11-18)

크로스 도메인 결합 제거(`lattice_core::redact`, `lattice_agent::seam::worker`, `modelops`→`http_reply`, `domain_boundaries.rs`)와 통합 Rust 서버 i18n(`lattice-auth`→`lattice_core::messages`, `check_server_i18n.mjs` 146개 카탈로그 검증), 다단계(2-Hop) 지식 그래프 추론 체인·매개 개념·상충 감지 합성(`reasoning_paths.rs`, `MultiHopSynthesisView.tsx`), 단일 트랜잭션 리뷰 스토어·폴더 재구성 원클릭 되돌리기(`reversible_reorganization.rs`, `ReviewCard.tsx`) 및 대화 문서 세션 영속화(`chat_document_sessions.json`), 프론트엔드 API 모듈 분할(`streaming.ts`, `transfers.ts`, Vitest 100%) 및 Tauri 백엔드 모듈화를 적용했습니다.

상세 릴리스 노트: [docs/releases/RELEASE_NOTES_v13.0.0.md](../releases/RELEASE_NOTES_v13.0.0.md).

# [v12.10.0 - Grounded Self] (2026-11-17)

결정론적 에이전트 트랜스크립트 근거 검증(`grounding.rs`)·답변 인용 증빙(`AnswerProof.tsx`)·브라우저 확장 언어 동기화, Brain 자기 모델 패널(`SelfModelPanel.tsx`, Vitest 100%) 및 지식 그래프 `PART_OF` 엣지(`edges`·`edges_v2`) 연쇄 삭제 보장, 협력적 모델 다운로드 취소(`499`)와 임베딩 모델 설치 직후 시맨틱 임베딩 자동 전환·배치 리인덱싱 드레인, `http_reply.rs` 도메인 간 결합 제거·Python 워커 라우터 6/6 전수 i18n 잠금·병렬 `cargo nextest`(`scripts/run_rust_tests.mjs`) 및 `release.yml`→`publish.yml` 자동 CD 체이닝을 적용했습니다.

상세 릴리스 노트: [docs/releases/RELEASE_NOTES_v12.10.0.md](../releases/RELEASE_NOTES_v12.10.0.md).

# [v12.9.0 - Semantic Guard] (2026-11-17)

시맨틱 쿼리 임베딩 계약 정렬(`query_embed.rs`)·순수 Rust `NativeHnswIndex`·워크스페이스/모델 스코프 Python HNSW 사이드카, `network_boundary_guard.rs` 및 `lattice-chat` 클라우드 경계 fail-closed 격리, 데스크톱(Tauri)·`LTCAI` CLI·호스트 슈퍼바이저·멀티스테이지 Docker 런타임 가드레일, 실시간 Workspace OS SSE(`/api/realtime/stream`, `useRealtimeStream.ts`) 및 `useAppBootstrap.ts` 분리, `.latticebrain` v2 아카이브 KDF 강화와 CI/Sidecar/Audit 게이트 보강을 적용했습니다.

상세 릴리스 노트: [docs/releases/RELEASE_NOTES_v12.9.0.md](../releases/RELEASE_NOTES_v12.9.0.md).

# [v12.8.0 - Trusted Brain] (2026-11-16)

통합 `EmbeddingIndexContract`·지식 라이프사이클 상태(`KnowledgeLifecycleState`)·세분화된 `IngestStatus`, SQLite `LIMIT` 이전 워크스페이스 스코핑·벡터 차원 정렬(`vector_search_aligned`)·바운드 `SidecarCachePool`, 서버 측 `IsolatedRootSpec`/`resolve_agent_scope` 멀티유저·테넌트 fail-closed 격리, 암호화 `.latticebrain` 아카이브 및 정직한 PostgreSQL `planned` 보고, `frontend/src/features/` 모듈 분리, PR 검색 게이트·macOS DMG CI/CD 자동화를 적용했습니다.

상세 릴리스 노트: [docs/releases/RELEASE_NOTES_v12.8.0.md](../releases/RELEASE_NOTES_v12.8.0.md).

# [v12.7.0 - Fast Index] (2026-11-16)

사전 토큰화 BM25F 사이드카(`Bm25SidecarIndex`), SQLite `bm25_node_stats` 영속화, 역색인 포스팅 리스트·GraphRAG 인접 캐시로 쿼리 시 재토큰화 없이 O(1) 조회를 제공하고, 제품 중심 `README.md` 개편 및 `v12.0.0+` 릴리스 히스토리 정리를 적용했습니다. 문은 422 / 41, 워커는 20. 쓰기는 GraphWriter 하나.

상세 릴리스 노트: [docs/releases/RELEASE_NOTES_v12.7.0.md](../releases/RELEASE_NOTES_v12.7.0.md).

# [v12.6.0 - Hybrid Graph] (2026-11-15)

3-way BM25F + Vector + Knowledge Graph RRF 하이브리드 인덱싱·검색, 그래프 인식 에이전트 스킬 및 MCP 도구, 멀티스케일 서브워드 `FallbackTextEmbedder`(#13.1), Pillow 13+ 레거시 제거(#13.2), Ubuntu 24.04/26.04 `t64`·Chromium CI 복원력(#14.1), 비인증 EmbeddingGemma 2 공개 미러 및 GGUF/MLX/ONNX fallback(#14.2), 모듈화 `scripts/bump_version/` CLI(#15.1), `ProductFlow.tsx`·`Library.tsx` 실시간 SSE 다운로드 속도·ETA 시각화(#15.2)를 적용했습니다. 문은 422 / 41, 워커는 20. 쓰기는 GraphWriter 하나.

상세 릴리스 노트: [docs/releases/RELEASE_NOTES_v12.6.0.md](../releases/RELEASE_NOTES_v12.6.0.md).

# [v12.5.0 - Deep Vector] (2026-11-11)

Google EmbeddingGemma 2(768차원) 네이티브 통합, 하드웨어(RAM/VRAM) 메모리 사전 예약 기반 PC 맞춤형 임베딩 모델 추천 시스템 구축, Pillow 13+ `get_flattened_data()` 전환, GitHub Actions Node 24 네이티브 메이저 버전 업그레이드, `scripts/bump_version.py`의 OpenAPI 스펙·파편 49개 타깃 자동 동기화를 적용했습니다. 문은 422 / 41, 워커는 20. 쓰기는 GraphWriter 하나.

상세 릴리스 노트: [docs/releases/RELEASE_NOTES_v12.5.0.md](../releases/RELEASE_NOTES_v12.5.0.md).

# [v12.4.0 - Sharp Weight] (2026-11-11)

Hugging Face 실저장소 검증을 거친 **Qwen 3.8 27B 4-bit**(`mlx-community/Qwen3.8-27B-4bit`)와 **Gemma 4 26B A4B QAT / QAT-OptiQ 4-bit**(`mlx-community/gemma-4-26B-A4B-it-qat-4bit`, `mlx-community/gemma-4-26B-A4B-it-qat-OptiQ-4bit`)를 추천·다운로드 카탈로그와 멀티엔진 라우팅에 추가하고, `pillow>=13,<14` 및 npm 의존성 보안 취약점 0건 달성, `model_engines` URL 스킴 검증, Tailwind CSS v4 소스 스캔 범위 제한을 적용했습니다. 문은 422 / 41, 워커는 20. 쓰기는 GraphWriter 하나.

상세 릴리스 노트: [docs/releases/RELEASE_NOTES_v12.4.0.md](../releases/RELEASE_NOTES_v12.4.0.md).

# [v12.3.0 - Live Wire] (2026-10-19)

Hugging Face 실저장소 양자화 태그(`Q4_0`·`MXFP4`)와 GGUF 라우팅, 보조 GGUF 필터링·`--mmproj` 동반 로드·샤드 검증, 작은 모델의 툴콜/MCP 인자 복구와 코드블록 HTML 추출, `POST /tools/inspect_html` 구조 분석과 RFC 5987 UTF-8 다운로드를 실모델(`mlx-community/gemma-4-e2b-it-4bit`) E2E로 검증했습니다. 문은 422 / 41, 워커는 20. 쓰기는 GraphWriter 하나.

상세 릴리스 노트: [docs/releases/RELEASE_NOTES_v12.3.0.md](../releases/RELEASE_NOTES_v12.3.0.md).

# [v12.2.1 - True Count] (2026-08-29)

검색·스킵·워치·작은 모델 답이 있는 것만 셉니다. 문은 422 / 41, 워커는 20. 쓰기는 GraphWriter 하나. 노트 파일은 `docs/releases/`로 모았습니다.

상세 릴리스 노트: [docs/releases/RELEASE_NOTES_v12.2.1.md](../releases/RELEASE_NOTES_v12.2.1.md).

# [v12.2.0 - Small Voice] (2026-08-29)

2B급 로컬 모델이 MCP·스킬·도구를 JSON 없이 고르고, 홈 작곡기가 그 일을 평범한 말로 제안합니다. 문은 422 / 41, 워커는 20. 12.1.0 Fast Path 인제스트 겹치기는 그대로입니다.

상세 릴리스 노트: [docs/releases/RELEASE_NOTES_v12.2.0.md](../releases/RELEASE_NOTES_v12.2.0.md).

# [v12.1.0 - Fast Path] (2026-08-29)

폴더 인제스트와 vault-watch가 파일을 겹쳐 돌리고, 노트당 `/worker/embed`는 한 방이며, rebuild는 쓰기 락 밖에서 해시하고, 512행 이상 Brain은 묶인 워커 시임이 있으면 `hnsw+rescore`를 먼저 시도합니다. 문은 422 / 41, 워커는 20. 주간 pip-audit와 Postgres CI가 깨끗한 런에서 빨개지던 것을 고쳤습니다.

상세 릴리스 노트: [docs/releases/RELEASE_NOTES_v12.1.0.md](../releases/RELEASE_NOTES_v12.1.0.md).

# [v12.0.0 - Open House] (2026-08-18)

가장 큰 두 크레이트를 도메인으로 나눴습니다 — `lattice-agent` 43파일이 `kernel`/`parse`/`content`/`tools`/`surface`/`prompts` 여섯 그룹으로, `lattice-platform` 31개 평면 모듈이 일곱 도메인으로(100건 `git mv`, 동작 변화 0). 두 크레이트 모두 크레이트 로컬 `ARCHITECTURE.md`를 싣고, `docs/DEVELOPMENT.md`는 기여자 온보딩으로 재작성됐으며 `docs/ROADMAP.md`가 새로 생겼습니다. 기존에 기록해 두었던 갭 네 개를 닫았습니다: 복원이 스토어 에폭으로 즉시 반영(재시작 불필요), `/setup/install`이 서버 도출 allowlist 항목에 한해 동의 기반 실행, `POST /mcp`가 OpenAPI 계약 안으로(네이티브 마운트), 포인터 도구가 `ltcai[pointer]`로 선언. 그래프 RAG는 한국어 조사 인지·섹션 트리·타입드 엣지 생산과 함께, 무변경 재인덱스 33s → 0.26s·드레인 ~66 → ~1,300 items/s·백로그 991건 15.3초를 얻었고 HNSW가 `hnsw+rescore`로 실제 검색에 쓰입니다(기본은 여전히 brute). GUIDED 모드는 작은 모델에게 JSON을 요구하지 않으며 Qwen 0.5B가 3.9초에 실파일로 DONE에 닿았습니다. 닫지 않은 것은 노트에 그대로 적었습니다.

상세 릴리스 노트: [docs/releases/RELEASE_NOTES_v12.0.0.md](../releases/RELEASE_NOTES_v12.0.0.md).

---

# 릴리스 노트 색인

이 저장소는 **12.0.0부터 13.5.0까지**의 공개 릴리스 이력을 유지합니다(최신 `v12.x`–`v13.x` Rust 호스트 + 도메인 그룹 아키텍처 세대). `SECURITY.md`가 `13.x` 및 `12.x` 세대를 지원하며, 이 색인 역시 동일한 경계를 따릅니다.

## 릴리스 목록 (12.0.0+)

- [v13.5.0 - Living Synthesis](./releases/RELEASE_NOTES_v13.5.0.md)
- [v13.4.2 - Living Synthesis](./releases/RELEASE_NOTES_v13.4.2.md)
- [v13.4.1 - Living Synthesis](./releases/RELEASE_NOTES_v13.4.1.md)
- [v13.4.0 - Living Synthesis](./releases/RELEASE_NOTES_v13.4.0.md)
- [v13.3.1 - Living Synthesis](./releases/RELEASE_NOTES_v13.3.1.md)
- [v13.3.0 - Living Synthesis](../releases/RELEASE_NOTES_v13.3.0.md)
- [v13.2.1 - Living Synthesis](../releases/RELEASE_NOTES_v13.2.1.md)
- [v13.2.0 - Living Synthesis](../releases/RELEASE_NOTES_v13.2.0.md)
- [v13.1.0 - Living Synthesis](../releases/RELEASE_NOTES_v13.1.0.md)
- [v13.0.0 - Sovereign Synthesis](../releases/RELEASE_NOTES_v13.0.0.md)
- [v12.10.0 - Grounded Self](../releases/RELEASE_NOTES_v12.10.0.md)
- [v12.9.0 - Semantic Guard](../releases/RELEASE_NOTES_v12.9.0.md)
- [v12.8.0 - Trusted Brain](../releases/RELEASE_NOTES_v12.8.0.md)
- [v12.7.0 - Fast Index](../releases/RELEASE_NOTES_v12.7.0.md)
- [v12.6.0 - Hybrid Graph](../releases/RELEASE_NOTES_v12.6.0.md)
- [v12.5.0 - Deep Vector](../releases/RELEASE_NOTES_v12.5.0.md)
- [v12.4.0 - Sharp Weight](../releases/RELEASE_NOTES_v12.4.0.md)
- [v12.3.0 - Live Wire](../releases/RELEASE_NOTES_v12.3.0.md)
- [v12.2.1 - True Count](../releases/RELEASE_NOTES_v12.2.1.md)
- [v12.2.0 - Small Voice](../releases/RELEASE_NOTES_v12.2.0.md)
- [v12.1.0 - Fast Path](../releases/RELEASE_NOTES_v12.1.0.md)
- [v12.0.0 - Open House](../releases/RELEASE_NOTES_v12.0.0.md)

## 정본 이력

버전별 정본 릴리스 이력은 아래 문서에서 관리됩니다.

- [RELEASE.md](./RELEASE.md)
- [docs/CHANGELOG.md](../CHANGELOG.md)
