# [v13.1.0 - Living Synthesis] (2026-11-18)

지식 그래프 인터랙티브 노드 인스펙터·단일 조회 2-Hop 이웃 추론 합성(`neighborhood_synthesis.rs`, `GraphNodeInspector.tsx`, `MultiHopSynthesisView.tsx`)·상충 필터 및 원클릭 상충 기억 제안/해결(`ContradictionInspector.tsx`), 채팅 답변 원클릭 **Brain에 기억하기** 승격·인라인 변경 제안 diff 승인/거절(`InlineStagedProposals.tsx`)·실시간 도구 단계 표시 및 출처 모달 그래프 점프(`AnswerProof.tsx`), 임베딩 진행률 바·즉시 인덱싱 드레인(`POST /api/index/drain`)·`stale_embedder` 보존(`health.rs`), 모델 다운로드 바이트/파일 표시 및 협력적 취소(`InstallScreen.tsx`, `ModelsPanel.tsx`), `⌘K` 지식 노드 포커스·작업공간 즉시 쿼리 무효화 및 한/영 무누수 로컬라이제이션(Vitest 100%)을 적용했습니다.

See [docs/releases/RELEASE_NOTES_v13.1.0.md](docs/releases/RELEASE_NOTES_v13.1.0.md).

# [v13.0.0 - Sovereign Synthesis] (2026-11-18)

크로스 도메인 결합 제거(`lattice_core::redact`, `lattice_agent::seam::worker`, `modelops`→`http_reply`, `domain_boundaries.rs`)와 통합 Rust 서버 i18n(`lattice-auth`→`lattice_core::messages`, `check_server_i18n.mjs` 146개 카탈로그 검증), 다단계(2-Hop) 지식 그래프 추론 체인·매개 개념·상충 감지 합성(`reasoning_paths.rs`, `MultiHopSynthesisView.tsx`), 단일 트랜잭션 리뷰 스토어·폴더 재구성 원클릭 되돌리기(`reversible_reorganization.rs`, `ReviewCard.tsx`) 및 대화 문서 세션 영속화(`chat_document_sessions.json`), 프론트엔드 API 모듈 분할(`streaming.ts`, `transfers.ts`, Vitest 100%) 및 Tauri 백엔드 모듈화를 적용했습니다.

See [docs/releases/RELEASE_NOTES_v13.0.0.md](docs/releases/RELEASE_NOTES_v13.0.0.md).

# [v12.10.0 - Grounded Self] (2026-11-17)

결정론적 에이전트 트랜스크립트 근거 검증(`grounding.rs`)·답변 인용 증빙(`AnswerProof.tsx`)·브라우저 확장 언어 동기화, Brain 자기 모델 패널(`SelfModelPanel.tsx`, Vitest 100%) 및 지식 그래프 `PART_OF` 엣지(`edges`·`edges_v2`) 연쇄 삭제 보장, 협력적 모델 다운로드 취소(`499`)와 임베딩 모델 설치 직후 시맨틱 임베딩 자동 전환·배치 리인덱싱 드레인, `http_reply.rs` 도메인 간 결합 제거·Python 워커 라우터 6/6 전수 i18n 잠금·병렬 `cargo nextest`(`scripts/run_rust_tests.mjs`) 및 `release.yml`→`publish.yml` 자동 CD 체이닝을 적용했습니다.

See [docs/releases/RELEASE_NOTES_v12.10.0.md](docs/releases/RELEASE_NOTES_v12.10.0.md).

# [v12.9.0 - Semantic Guard] (2026-11-17)

시맨틱 쿼리 임베딩 계약 정렬(`query_embed.rs`)·순수 Rust `NativeHnswIndex`·워크스페이스/모델 스코프 Python HNSW 사이드카, `network_boundary_guard.rs` 및 `lattice-chat` 클라우드 경계 fail-closed 격리, 데스크톱(Tauri)·`LTCAI` CLI·호스트 슈퍼바이저·멀티스테이지 Docker 런타임 가드레일, 실시간 Workspace OS SSE(`/api/realtime/stream`, `useRealtimeStream.ts`) 및 `useAppBootstrap.ts` 분리, `.latticebrain` v2 아카이브 KDF 강화와 CI/Sidecar/Audit 게이트 보강을 적용했습니다.

See [docs/releases/RELEASE_NOTES_v12.9.0.md](docs/releases/RELEASE_NOTES_v12.9.0.md).

# [v12.8.0 - Trusted Brain] (2026-11-16)

통합 `EmbeddingIndexContract`·지식 라이프사이클 상태(`KnowledgeLifecycleState`)·세분화된 `IngestStatus`, SQLite `LIMIT` 이전 워크스페이스 스코핑·벡터 차원 정렬(`vector_search_aligned`)·바운드 `SidecarCachePool`, 서버 측 `IsolatedRootSpec`/`resolve_agent_scope` 멀티유저·테넌트 fail-closed 격리, 암호화 `.latticebrain` 아카이브 및 정직한 PostgreSQL `planned` 보고, `frontend/src/features/` 모듈 분리, PR 검색 게이트·macOS DMG CI/CD 자동화를 적용했습니다.

See [docs/releases/RELEASE_NOTES_v12.8.0.md](docs/releases/RELEASE_NOTES_v12.8.0.md).

# [v12.7.0 - Fast Index] (2026-11-16)

사전 토큰화 BM25F 사이드카(`Bm25SidecarIndex`), SQLite `bm25_node_stats` 영속화, 역색인 포스팅 리스트·GraphRAG 인접 캐시로 쿼리 시 재토큰화 없이 O(1) 조회를 제공하고, 제품 중심 `README.md` 개편 및 `v12.0.0+` 릴리스 히스토리 정리를 적용했습니다. 문은 422 / 41, 워커는 20. 쓰기는 GraphWriter 하나.

See [docs/releases/RELEASE_NOTES_v12.7.0.md](docs/releases/RELEASE_NOTES_v12.7.0.md).

# [v12.6.0 - Hybrid Graph] (2026-11-15)

3-way BM25F + Vector + Knowledge Graph RRF 하이브리드 인덱싱·검색, 그래프 인식 에이전트 스킬 및 MCP 도구, 멀티스케일 서브워드 `FallbackTextEmbedder`(#13.1), Pillow 13+ 레거시 제거(#13.2), Ubuntu 24.04/26.04 `t64`·Chromium CI 복원력(#14.1), 비인증 EmbeddingGemma 2 공개 미러 및 GGUF/MLX/ONNX fallback(#14.2), 모듈화 `scripts/bump_version/` CLI(#15.1), `ProductFlow.tsx`·`Library.tsx` 실시간 SSE 다운로드 속도·ETA 시각화(#15.2)를 적용했습니다. 문은 422 / 41, 워커는 20. 쓰기는 GraphWriter 하나.

See [docs/releases/RELEASE_NOTES_v12.6.0.md](docs/releases/RELEASE_NOTES_v12.6.0.md).

# [v12.5.0 - Deep Vector] (2026-11-11)

Google EmbeddingGemma 2(768차원) 네이티브 통합, 하드웨어(RAM/VRAM) 메모리 사전 예약 기반 PC 맞춤형 임베딩 모델 추천 시스템 구축, Pillow 13+ `get_flattened_data()` 전환, GitHub Actions Node 24 네이티브 메이저 버전 업그레이드, `scripts/bump_version.py`의 OpenAPI 스펙·파편 49개 타깃 자동 동기화를 적용했습니다. 문은 422 / 41, 워커는 20. 쓰기는 GraphWriter 하나.

See [docs/releases/RELEASE_NOTES_v12.5.0.md](docs/releases/RELEASE_NOTES_v12.5.0.md).

# [v12.4.0 - Sharp Weight] (2026-11-11)

Hugging Face 실저장소 검증을 거친 **Qwen 3.8 27B 4-bit**(`mlx-community/Qwen3.8-27B-4bit`)와 **Gemma 4 26B A4B QAT / QAT-OptiQ 4-bit**(`mlx-community/gemma-4-26B-A4B-it-qat-4bit`, `mlx-community/gemma-4-26B-A4B-it-qat-OptiQ-4bit`)를 추천·다운로드 카탈로그와 멀티엔진 라우팅에 추가하고, `pillow>=13,<14` 및 npm 의존성 보안 취약점 0건 달성, `model_engines` URL 스킴 검증, Tailwind CSS v4 소스 스캔 범위 제한을 적용했습니다. 문은 422 / 41, 워커는 20. 쓰기는 GraphWriter 하나.

See [docs/releases/RELEASE_NOTES_v12.4.0.md](docs/releases/RELEASE_NOTES_v12.4.0.md).

# [v12.3.0 - Live Wire] (2026-10-19)

Hugging Face 실저장소 양자화 태그(`Q4_0`·`MXFP4`)와 GGUF 라우팅, 보조 GGUF 필터링·`--mmproj` 동반 로드·샤드 검증, 작은 모델의 툴콜/MCP 인자 복구와 코드블록 HTML 추출, `POST /tools/inspect_html` 구조 분석과 RFC 5987 UTF-8 다운로드를 실모델(`mlx-community/gemma-4-e2b-it-4bit`) E2E로 검증했습니다. 문은 422 / 41, 워커는 20. 쓰기는 GraphWriter 하나.

See [docs/releases/RELEASE_NOTES_v12.3.0.md](docs/releases/RELEASE_NOTES_v12.3.0.md).

# [v12.2.1 - True Count] (2026-08-29)

검색·스킵·워치·작은 모델 답이 있는 것만 셉니다. 문은 422 / 41, 워커는
20. 쓰기는 GraphWriter 하나. 노트 파일은 `docs/releases/`로 모았습니다.

See [docs/releases/RELEASE_NOTES_v12.2.1.md](docs/releases/RELEASE_NOTES_v12.2.1.md).

# [v12.2.0 - Small Voice] (2026-08-29)

2B급 로컬 모델이 MCP·스킬·도구를 JSON 없이 고르고, 홈 작곡기가 그 일을
평범한 말로 제안합니다. 문은 422 / 41, 워커는 20. 12.1.0 Fast Path
인제스트 겹치기는 그대로입니다.

See [RELEASE_NOTES_v12.2.0.md](docs/releases/RELEASE_NOTES_v12.2.0.md).

# [v12.1.0 - Fast Path] (2026-08-29)

폴더 인제스트와 vault-watch가 파일을 겹쳐 돌리고, 노트당
`/worker/embed`는 한 방이며, rebuild는 쓰기 락 밖에서 해시하고, 512행
이상 Brain은 묶인 워커 시임이 있으면 `hnsw+rescore`를 먼저 시도합니다.
문은 422 / 41, 워커는 20. 주간 pip-audit와 Postgres CI가 깨끗한 런에서
빨개지던 것을 고쳤습니다.

See [RELEASE_NOTES_v12.1.0.md](docs/releases/RELEASE_NOTES_v12.1.0.md).

# [v12.0.0 - Open House] (2026-08-18)

가장 큰 두 크레이트를 도메인으로 나눴습니다 — `lattice-agent` 43파일이
`kernel`/`parse`/`content`/`tools`/`surface`/`prompts` 여섯 그룹으로,
`lattice-platform` 31개 평면 모듈이 일곱 도메인으로(100건 `git mv`,
동작 변화 0). 두 크레이트 모두 크레이트 로컬 `ARCHITECTURE.md`를 싣고,
`docs/DEVELOPMENT.md`는 기여자 온보딩으로 재작성됐으며
`docs/ROADMAP.md`가 새로 생겼습니다. 11.9.0이 적어 둔 갭 네 개를
닫았습니다: 복원이 스토어 에폭으로 즉시 반영(재시작 불필요),
`/setup/install`이 서버 도출 allowlist 항목에 한해 동의 기반 실행,
`POST /mcp`가 OpenAPI 계약 안으로(네이티브 마운트), 포인터 도구가
`ltcai[pointer]`로 선언. 그래프 RAG는 한국어 조사 인지·섹션 트리·
타입드 엣지 생산과 함께, 무변경 재인덱스 33s → 0.26s·드레인 ~66 →
~1,300 items/s·백로그 991건 15.3초를 얻었고 HNSW가 `hnsw+rescore`로
실제 검색에 쓰입니다(기본은 여전히 brute). GUIDED 모드는 작은 모델에게
JSON을 요구하지 않으며 Qwen 0.5B가 3.9초에 실파일로 DONE에 닿았습니다.
닫지 않은 것은 노트에 그대로 적었습니다.

See [docs/releases/RELEASE_NOTES_v12.0.0.md](docs/releases/RELEASE_NOTES_v12.0.0.md).

---

# Release Notes

This repository keeps public release history from **12.0.0 through 13.1.0** (the modern `v12.x`–`v13.x` Rust-host + domain-grouped architecture era). `SECURITY.md` supports the `13.x` and `12.x` eras, and this index follows the same boundary.

## Releases (12.0.0+)

- [v13.1.0 - Living Synthesis](docs/releases/RELEASE_NOTES_v13.1.0.md)
- [v13.0.0 - Sovereign Synthesis](docs/releases/RELEASE_NOTES_v13.0.0.md)
- [v12.10.0 - Grounded Self](docs/releases/RELEASE_NOTES_v12.10.0.md)
- [v12.9.0 - Semantic Guard](docs/releases/RELEASE_NOTES_v12.9.0.md)
- [v12.8.0 - Trusted Brain](docs/releases/RELEASE_NOTES_v12.8.0.md)
- [v12.7.0 - Fast Index](docs/releases/RELEASE_NOTES_v12.7.0.md)
- [v12.6.0 - Hybrid Graph](docs/releases/RELEASE_NOTES_v12.6.0.md)
- [v12.5.0 - Deep Vector](docs/releases/RELEASE_NOTES_v12.5.0.md)
- [v12.4.0 - Sharp Weight](docs/releases/RELEASE_NOTES_v12.4.0.md)
- [v12.3.0 - Live Wire](docs/releases/RELEASE_NOTES_v12.3.0.md)
- [v12.2.1 - True Count](docs/releases/RELEASE_NOTES_v12.2.1.md)
- [v12.2.0 - Small Voice](docs/releases/RELEASE_NOTES_v12.2.0.md)
- [v12.1.0 - Fast Path](docs/releases/RELEASE_NOTES_v12.1.0.md)
- [v12.0.0 - Open House](docs/releases/RELEASE_NOTES_v12.0.0.md)

## Canonical History

The canonical per-release history is maintained in:

- [RELEASE.md](RELEASE.md)
- [docs/CHANGELOG.md](docs/CHANGELOG.md)
