[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../FEATURE_STATUS.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./FEATURE_STATUS.md)

# Lattice AI 기능 현황 (v14.3.0)

> **상태: 정본(Canonical)** — 현재 릴리스 기준의 실제 기능 상태와 한계를 기록합니다.

현재 릴리스: **14.3.0 — Temporal Synthesis**.

이 문서는 `v14.3.0` 기준 Lattice AI의 실제 제공 기능과 정직한 한계를 정리합니다.
공개 릴리스 이력은 [`RELEASE.md`](./RELEASE.md), [`RELEASE_NOTES.md`](./RELEASE_NOTES.md), [`CHANGELOG.md`](./CHANGELOG.md)에서 `v13.0.0` 이후 버전만 유지합니다.

---

## 제품 포지션

Lattice AI는 **로컬 우선(Local-first) 디지털 브레인**입니다. 영속적인 핵심 자산은 모델이 아니라 사용자의 브레인(대화, 문서, 의사결정, 기억, 출처, 지식 그래프 구조)이며, 로컬 또는 옵트인 클라우드 모델은 그 브레인 위에서 언제든 교체할 수 있는 런타임입니다.

- **첫 화면은 대시보드가 아니라 조용한 작업 공간입니다**: 살아 있는 Brain과 컴포저가 데스크톱(`1280×800`)과 모바일(`390×780`) 첫 뷰포트에 함께 자리하고, 자료 수집은 컴포저 안에 통합됩니다. 일상 목적지(대화 · 자료 · 기억)와 관리 목적지(작업 · AI 모델 · 설정)가 명확히 분리되어 있습니다.
- **단일 관문(One Door) Rust 제품 서버**: `lattice-host`가 9개 Rust 크레이트에 걸쳐 41개 패밀리, **422개 네이티브 오퍼레이션**을 직접 서빙하고, 단일 `GraphWriter` / `Store::write_conn` 풀로 모든 지식 그래프 쓰기를 소유하며, 21개 연산 전용 라우트의 루프백 Python AI 워커를 감독합니다.
- **정직한 근거와 제안 우선 거버넌스**: 하이브리드 검색은 문맥 품질과 주장 단위 근거 검증을 투명하게 노출합니다. 새 파일 생성은 최소 마찰로 실행되고, 기존 사용자 파일 수정·삭제, 자기모델 갱신, 모순 해결, 폴더 정리, 클라우드 지식 반영은 모두 해시 검증을 거치는 검토함(Review Center) 제안으로 스테이징됩니다.
- **명시적 자율성과 네트워크 경계**: `PermissionMode`(`strict` / `trusted` / `bypass`)와 `NetworkBoundaryMode`(`local_only` 기본 / `cloud_allowed` 옵트인)가 서로 독립적으로 동작하며, 파괴적 명령 서킷 브레이커와 민감 노드 필터는 어떤 모드에서도 해제되지 않습니다.

---

## 현재 기능 현황

| 영역 | 상태 | 설명 |
| --- | --- | --- |
| **단일 관문 Rust 제품 서버** | 현재 제공 | 9개 워크스페이스 크레이트(`lattice-host`, `lattice-core`, `lattice-auth`, `lattice-agent`, `lattice-retrieval`, `lattice-ingest`, `lattice-jobs`, `lattice-platform`, `lattice-chat`)가 41개 패밀리, **422개 네이티브 오퍼레이션**을 서빙합니다. Python AI 워커는 허용 목록(`rust/fixtures/worker_allowlist.json`)에 등록된 **21개 연산 라우트**만 처리하며, 그 외 비네이티브 경로는 `404`를 반환합니다. |
| **정본 크레이트 도메인 구조** | 현재 제공 | `lattice-agent`는 6개 그룹(`kernel`/`parse`/`content`/`tools`/`surface`/`prompts`), `lattice-platform`은 7개 도메인(`workspaceos`/`toolsurface`/`governance`/`adminops`/`knowledge`/`modelops`/`shell`)으로 구성되며, `13.7.2` 이후 모든 호출자가 정본 도메인 경로를 직접 사용하고 `domain_boundaries.rs`가 경계를 검증합니다. |
| **통합 UTC 시계 & 단일 라이터 SQLite** | 현재 제공 | 모든 지식 그래프 쓰기는 `lattice-core::graph_write::GraphWriter`와 `Store::write_conn`으로 직렬화됩니다. Rust(`clock.rs`, `libc` `unsafe` 0건)와 Python(`lattice_brain/utils.py`, `_kg_fsutil.py`)이 오프셋 없는 UTC 타임스탬프(`YYYY-MM-DDTHH:MM:SS`)로 통일되어 `Asia/Seoul`, `America/New_York`, `UTC` 환경에서 편차가 없습니다. `.latticebrain` 복원 시 스토어 세대 에폭이 증가해 재시작 없이 즉시 반영됩니다. |
| **Brain Home & 일상 표면** | 현재 제공 | 살아 있는 Brain 성장 링, 컴포저 중심 작업 흐름, 하단 연속성 바(지난 대화 · 현황 · 기억 지도 · 기능), 라우트별 `React.lazy` 분할(초기 JS gzip `<= 150 KiB`), 라우트별 i18n 네임스페이스(`ko` 기본 + `en`), 라우트/패널 `ErrorBoundary` 격리를 제공합니다. 엄격 TypeScript 7.0.2(`noUncheckedIndexedAccess`)와 Vitest 100% 커버리지(`113`개 파일, `1,852`개 테스트)를 유지합니다. |
| **근거 기반 대화 & 파일 생성** | 현재 제공 | 로드된 모델이 없을 때 가짜 출력을 만들지 않으며, 출처 근거, `ClaimVerificationStrip` 검증 스트립, 원클릭 **Brain에 저장**, 인라인 변경 제안 diff(`InlineStagedProposals`), 실시간 활성 도구 단계 표시, 거버넌스 기반 파일 생성(HTML, DOCX, PDF)을 지원합니다. 대화 문서 세션은 Tokio 런타임을 블로킹하지 않고 영속화됩니다. |
| **지식 그래프 & 2-Hop 이웃 합성** | 현재 제공 | 대화형 그래프 탐색기(`DigitalBrainExplorer`), `GraphNodeInspector`, 단일 조회 2-Hop 추론 체인 및 매개 개념(`neighborhood_synthesis.rs`, `MultiHopSynthesisView`), 모순 전용 필터링, `ContradictionInspector`, `/`·`Escape` 단축키, `⌘K` 노드 포커스 이동을 지원합니다. |
| **브레인 거버넌스 UI 패널** | 현재 제공 | 통합 `brain_api` OpenAPI 계약에 연결된 5개 지연 로드 React 패널(`IntegrityRepairPanel`, `EntityMergePanel`, `KnowledgeCorrectionsPanel`, `EmbeddingRegistryPanel`, `ClustersGapsPanel`)과 `SelfModelPanel`, `BrainIntelligencePanel`을 제공합니다. |
| **이중 시간(Bi-temporal) 지식 & 연대기** | 현재 제공 | 노드와 엣지가 `valid_from`/`valid_to`/`superseded_by` 및 `recorded_at`/`superseded_at` 이중 시간 정보를 유지합니다(`temporal.rs`). 사실 변화를 `TemporalEvolution`, `ComplementaryRefinement`, `ActiveContradiction`으로 구분하며, **연대기(`#/chronicle`)** 화면에서 SVG 성장 곡선, 활동 히트맵, 일일 기록, `as_of` 과거 시점 되감기를 조회합니다. |
| **엔티티 해소 & 선택적 롤백** | 현재 제공 | 타입 범위 정확 클러스터링(`(node_type, key)`)으로 교차 타입 동음이의어 오병합을 차단하며, 퍼지 다중 신호 점수, 한국어 조사 제거, 영속 `entity_aliases`/`entity_merge_audit`/`entity_do_not_merge` 테이블, 교차 워크스페이스 403 소유권 검증, 비파괴 `SAME_AS` 엣지 및 선택적 `rollback_entity_merge`를 지원합니다. |
| **지식 그래프 무결성 검사 & 안전 복구** | 현재 제공 | 워크스페이스 범위 손상·댕글링 엣지·고아 청크/벡터·교차 워크스페이스 누출·중복 자연키·인덱스 드리프트·DAG 순환 참조 검사(`lattice-core::integrity`), 드라이런 영향 미리보기, 복구 전 자동 `VACUUM INTO` 스냅샷 백업, 관리자 전용 전역 모드를 제공합니다. |
| **주장 단위 근거 검증 & 진정성 있는 RAG 평가** | 현재 제공 | `grounding.rs`가 답변 주장을 인용 청크와 대조해 지지율, 인용 정밀도, 수치·극성 불일치, 대체된(`superseded`) 출처 유출을 검증하고 근거 부족 시 기권을 권고합니다. `rag_eval_grounded.rs`와 320개 질의 데이터셋(`datasets/rag_eval_v1.json`)이 결정론적 픽스처와 실제 로컬/클라우드 모델 평가(`.github/workflows/nightly-rag-eval.yml`)를 엄격히 분리합니다. |
| **3중 하이브리드 검색 & GraphRAG** | 현재 제공 | 사전 토큰화 Okapi BM25F(`bm25_node_stats`, 2단계 한국어 조사 제거, CJK 바이그램, 허브 팬아웃 제한), 밀집/서브워드 벡터 검색, 1~2-Hop 지식 그래프 확장을 RRF로 결합하며 포함관계 중복 제거, 어휘 구문 재순위화, 통합 `score_explanation`, 명시적 `fallbacks`를 제공합니다. |
| **섹션 트리 & 구조 보존 수집** | 현재 제공 | `Document ←PART_OF— Section —HAS_CHUNK→ Chunk` 목차 트리와 표·페이지·슬라이드·시트·캡션 및 선택적 로컬 OCR/비전 오버레이를 보존합니다(`document_structure.rs`). |
| **폴더 수집 · 디바운스 감시 · 청크 증분 동기화** | 현재 제공 | `.latticeignore` 필터링, 파일 지문(크기+mtime+SHA-256) 미변경 스킵(재인덱싱 `33s → 0.26s`), `spawn_blocking` 파일 I/O, `DebouncedWatchEngine`(`moved_from_uri` 이동 연동·일시중지), 2단계 비동기 청크 증분 재임베딩(`incremental_sync.rs`, `watch_deliver.rs`)을 지원합니다. |
| **삭제된 파일 정리** | 현재 제공(동의 필요) | 증분 수집은 사라진 파일을 자동 삭제하지 않고 보고합니다. `POST /api/ingestion/folder/prune`은 드라이런을 먼저 제시하고 명시적 `confirm` 시 그래프 서브트리(`delete_document_tree` / `delete_node_subgraph`)만 정리합니다(디스크 원본 파일은 절대 삭제하지 않음). |
| **벡터 인덱스 백엔드 & 임베딩 레지스트리** | 현재 제공 | Rust ↔ Python 공통 `EmbeddingIndexContract`로 모델·리비전·차원·정규화 계약을 일치시킵니다. `brute`(기본 정확 스캔), `quantized`, 상한 LRU 캐시 `NativeHnswIndex` / 워커 `hnsw+rescore`(512개 이상 벡터 + 워커 연결 시 자동 시도)를 지원하며, `EmbeddingModelRegistry`로 재개 가능한 섀도 재색인·품질 게이트 컷오버·즉시 롤백을 제공합니다. |
| **백그라운드 임베딩 & 실시간 드레인 제어** | 현재 제공 | 내구성 `vector_jobs` 큐를 `lattice-jobs` 적응형 스케줄러가 처리합니다(부분 드레인 통계 보존 및 `/host/jobs` 루프백/Bearer 가드 포함). `VectorFreshnessNotice`에서 임베딩 진행률과 원클릭 **지금 인덱싱(`POST /api/index/drain`)**을 제어합니다. |
| **능동적 합성 · 학습 루프 · 자기모델** | 현재 제공 | 이벤트 기반 합성이 모순, 미명명 주제, 미연결 쌍, 오래된 기억을 `kg_change_digest` 제안으로 생성합니다. 로컬 전용 `knowledge_corrections` 학습 루프가 워크스페이스 소유권 검증 하에 교정 이력을 기록하며, `SelfModelPanel`과 `/api/memory/self-model*`로 소유자 선호·습관·결정·관계를 제안 우선으로 관리합니다. |
| **범용 적응형 에이전트 하네스 & 내구성 워크플로** | 현재 제공 | 모델별 분기 없는 `kernel/probe.rs` 3턴 마이크로 프로빙으로 `standard` / `compact` / `guided` 다이얼을 자동 선택하며, 하향 전용 자가 강등(`demote_to_guided`), `ui_resolve.rs`, `rag_grounding.rs`, fail-closed `NEEDS_REVIEW` 검증을 수행합니다. `DurableWorkflowStore`가 원자적 체크포인트, SHA-256 멱등 재생, `WaitingApproval` 재시작 복구, 파일 스냅샷 롤백, 최소 권한 5역할 `MultiAgentSession`을 제공합니다. |
| **변경 거버넌스 & 되돌리기 가능한 폴더 정리** | 현재 제공 | `MUTATING_TOOL_INVENTORY`가 모든 상태 변경 도구의 거버넌스를 보장합니다. 기존 파일 수정·삭제는 원본 해시 검증을 거치는 `change_proposal`로 스테이징됩니다(충돌 시 `409`). 폴더 정리는 `topics/<주제>/` 이동 계획만 제안(삭제 경로 없음)하며 승인 시 1-클릭 되돌리기(Undo) 제안을 자동 생성합니다(`reversible_reorganization.rs`). |
| **권한 모드 & TOCTOU 방지 샌드박스** | 현재 제공 | 사용자·워크스페이스별 `strict` / `trusted` / `bypass` 다이얼과 모드 불변 서킷 브레이커를 제공합니다. 사용자별 `AGENT_ROOT` 격리(`IsolatedRootSpec`)와 OS 수준 TOCTOU 방지 `safe_io.rs`(Unix `openat`/`O_NOFOLLOW` + Windows 핸들 고정 `CreateFileW` 경로 탐색)를 적용합니다. |
| **네트워크 경계 & 하이브리드 클라우드 채팅** | 현재 제공(옵트인) | 기본값은 `local_only`입니다. 옵트인 `cloud_allowed`에서는 상한 LRU 토큰 예산(`cloud/budget.rs`), 비밀 파일(`.env`, `.pem`, `.key`) 차단, PII/비밀 마스킹(`lattice-core::redact`), 형태 전용 `cloud_egress` 감사, `kg_cloud_expansion` 제안을 강제합니다. `api_key`(`GET /models` 무과금 프로브)와 `cli_oauth`(`agy`/`grok`)를 지원합니다. |
| **크로스플랫폼 로컬 모델 & 협조적 다운로드 취소** | 현재 제공 | `Origin Country → Vendor → Execution` 출처 카탈로그와 macOS(`omlx` 계층형 RAM/SSD KV 캐시, 플랫폼 가드 `local_mlx`, `ollama`, `lmstudio`, `llamacpp`), Windows(`winget` + `pip` `llama-cpp-python[server]` 폴백), Linux(`ollama`, `llamacpp`, `vllm`) 원클릭 설치를 지원합니다. `Qwen3.8-27B-4bit`, Gemma 4, EmbeddingGemma 2 다운로드 시 실시간 SSE 진행률과 협조적 취소(`AbortController` + `499`)를 제공합니다. |
| **도구 레지스트리 & 네이티브 MCP 서버** | 현재 제공 | `POST /mcp`가 OpenAPI 제품 계약에 포함된 네이티브 Streamable-HTTP JSON-RPC 서버(`initialize` / `tools/list` / `tools/call`)로 동작하며 에이전트 카탈로그(`mcp.<tool>`, `skill.<name>`)와 동일한 `check_governance` 게이트를 공유합니다. |
| **기능 토글 (Feature Toggles)** | 현재 제공 | 서버 렌더링 **기능** 서랍(`GET/POST /api/features`, 우선순위 `사용자 → 환경 변수 → 기본값`)에서 멀티모달, 비디오 수집, 볼트 감시, 브레인 네트워크, 자동 합성, 인라인 벡터 동기화, RRF 융합, 그래프 확장, 벡터 백엔드를 재시작 없이 제어합니다. |
| **네이티브 로컬 상호운용 브리지** | 현재 제공(옵트인) | 벤더 API 없이 승인 토큰 하에 Obsidian 볼트(`[[wikilink]]` `LINKS_TO` 엣지), Notion 내보내기(폴더/`.zip`), 로컬 Git 커밋 로그, `.eml`/`.ics` 파일을 네이티브 Rust `GraphWriter`로 직접 수집합니다. |
| **암호화 `.latticebrain` 아카이브 & Brain Network** | 현재 제공 | Rust 기반 RFC 7914 `scrypt` 암호문구 또는 `X25519` + `HKDF-SHA256` 수신자 공개키 암호화, 상수 시간 `SHA256-CTR` + `HMAC-SHA256` 검증, ZIP 경로 탈출 차단을 제공합니다. 옵트인 서브그래프 공유(`LATTICEAI_BRAIN_NETWORK=1`)는 Ed25519 서명과 검토함 제안 수신을 사용합니다. |
| **데스크톱 셸(Tauri 2.11) & 확장 프로그램** | 현재 제공 | 직렬화된 비동기 Tauri 게이트웨이 생명주기, 프로덕션 포트 `4825` 충돌 시 즉시 실패하는 `PortPolicy::PinnedProduction`, `'unsafe-inline'` 없는 엄격 CSP, 직접 `BackendStatus`를 반환하는 비실패 커맨드, `spawn_blocking` 폴더 선택기를 제공합니다. VS Code 확장(Bearer 인증, TTL 승인 캐시, 실시간 `agent_step` 타임라인, 크로스플랫폼 VSIX)과 브라우저 확장(포트 4825 권한, 라이브 DOM 가시성 추출, UTF-8 바이트 절단, 서버 언어 동기화)을 포함합니다. |
| **멀티모달(이미지 · 오디오 · 비디오)** | 옵트인(기본 꺼짐) | `allow_multimodal` 활성화 시 `Image`/`ImageText`, `Audio`, `Video`(`Modality::Video` + `ffmpeg` 키프레임 + `.srt`/`.vtt`)를 지원합니다. OCR(`pytesseract`), VLM 캡션, 비전 임베딩, 음성 전사기, `ffmpeg`는 번들되지 않으며 미설치 시 가짜 출력 없이 `unavailable`로 보고합니다. |
| **PostgreSQL 백엔드** | 계획됨(Planned) | 공식 로컬 라이브 저장소는 SQLite WAL입니다. PostgreSQL/pgvector는 드라이런 계획 및 수동 실행 CI 가드만 존재하는 계획 단계입니다. |
| **텔레그램 브리지 & SSO/OIDC 로그인 콜백** | 제거됨 (11.6.0) | 제품 서버가 Rust(`lattice-host`)로 전환되면서 함께 은퇴했습니다. 비밀번호 로그인, 세션, `$scrypt$` 해싱, 레이트 리미팅, CSRF, SSO 설정 표면은 `lattice-auth`에서 네이티브로 유지됩니다. |
| **시간 인식 검색 & 청킹** | 현재 제공 | 문맥 접두어를 갖춘 토큰 크기의 parent-child 청크, tree-sitter 코드 분할, 시간 의도 사전 필터, 최신성 감쇠, `SUPERSEDES` 처리, `sort_by=relevance\|newest`, `ltcai search`. |
| **재랭킹 & 채팅 근거 게이트** | 현재 제공(reranker는 `rag` extra 필요) | RRF 대체를 갖춘 `POST /worker/rerank` cross-encoder 재랭킹, 문서당 상한을 둔 MMR, Self-RAG 답변 보류. 실제 reranker·EmbeddingGemma 2 로딩은 빌드 환경에서 검증하지 못했습니다. |
| **3D 살아있는 Brain & 데스크톱 셸** | 현재 제공 | 검색 점등과 2D 대체를 갖춘 3D 그래프, 고정 뷰포트 셸, Brain Vital Pill, 도킹 근거 패널, `Cmd/Ctrl+K` 팔레트. |
| **릴리스 아티팩트 & 크로스플랫폼 CI/CD** | 현재 제공 | 정확 버전 Python wheel/sdist, npm tarball, VS Code VSIX, macOS Apple Silicon DMG(`Lattice AI_14.3.0_aarch64.dmg`), `SHA256SUMS`가 Ubuntu·Windows Rust CI, macOS Tauri CI, 다이제스트 고정 Docker 스모크 CI, PR + `main` Playwright 시각 스모크로 검증됩니다. |

---

## 알려진 한계 (정직한 경계)

- **검색 모델 미검증:** 14.2.0에서 실제 EmbeddingGemma 2와 `bge-reranker-v2-m3` 로딩은 실행해 보지 못했습니다. 실패하면 해시 임베딩과 RRF 순서로 대체합니다.
- **워크스페이스 내 문서의 사용자별 비공개 없음:** 다중 사용자 뷰어 필터는 대화 파생 노드에만 적용됩니다.
- **소형 모델의 어려운 과제 내용 품질**: 범용 적응형 에이전트 하네스가 기계적 실행 안정성을 크게 높였지만(`Qwen2.5-0.5B-Instruct-Q4_K_M` 기준 `28.0% → 84.0%`), 0.5B~2B 모델은 개방형 과제에서 유창하지만 불완전한 요약을 생성할 수 있으며 fail-closed 검증기가 이를 `NEEDS_REVIEW`로 격리합니다.
- **클라우드 `api_key` 라이브 과금 경계**: `api_key` 경로는 무과금 `GET /models` 프로브로 연결성을 검증하고 실패 시 fail-closed 합니다. CI에서는 과금 채팅 완성 호출을 실행하지 않으며 라이브 E2E는 무과금 `cli_oauth`로 검증합니다.
- **macOS DMG 코드 서명**: `.github/workflows/release.yml`은 `APPLE_CERTIFICATE` 시크릿 구성 시 Developer ID 서명 및 공증을 수행하고, 미구성 시 ad-hoc 서명(최초 실행 시 Gatekeeper 허용 필요)으로 폴백합니다. `npm run release:validate`는 파일명과 존재 여부만 검증합니다.
- **벡터 백엔드 환경 변수 기본값**: `LATTICEAI_VECTOR_INDEX` 기본값은 `brute`이며(512개 이상 벡터 + 워커 연결 시 `hnsw+rescore` 자동 시도), 기본 환경 변수 전환은 연속 수집 환경의 라이브 recall@10 검증을 전제로 합니다. `quantized` int8 백엔드는 10k 행 기준 메모리 절감 대비 약 2.2배 느려 기본 권장 대상이 아닙니다.
- **Hugging Face 런타임 커밋 고정 백로그**: `scripts/verify_hf_model_registry.py`는 가중치 다운로드 없이 Hub 메타데이터와 크기를 정적 검증하지만, `model_runtime/download.py`와 `embedding_providers/text.py`의 런타임 다운로드에는 기본 불변 `revision=` 고정이 아직 전 경로에 적용되지 않았습니다([`docs/SECURITY_AUDIT.md`](../SECURITY_AUDIT.md)에 Bandit `B615`로 추적 중).
- **비전·음성 모델 및 `ffmpeg` 미번들**: OCR, VLM 캡션, 공유 공간 비전 임베딩, 음성 전사기, `ffmpeg`는 선택적 외부 의존성이며 부재 시 가짜 값을 만들지 않고 `unavailable`로 보고합니다. 이미지·비디오 관찰 헬퍼는 별도 HTTP 라우트 없이 Brain Core 단위 테스트로 유지됩니다.
- **이미지 근거 썸네일**: 근거 카드는 명시적 `/local/serve` 승인 없이 디스크 원본 경로를 노출하지 않기 위해 96px 인라인 `data:image/` 썸네일(24 KB 상한)만 렌더링합니다.
- **로컬 내보내기 전용 상호운용 브리지**: Notion API, IMAP, macOS 캘린더/메일 실시간 연동은 의도적으로 범위 밖이며 사용자가 내보낸 로컬 파일만 수집합니다.
- **포인터 도구(`pyautogui`)**: 6개 포인터 도구는 네이티브 Rust OS 액추에이터가 아니라 Python 워커(`pip install "ltcai[pointer]"`)에서 실행됩니다.
- **자기모델 추출 범위**: 한/영 구문 테이블 기반 제안 우선 추출이며 LLM `refiner`는 미연결 상태이고 `open_keys`는 `pending` 제안만 필터링합니다.
- **커버리지 하한 비대칭**: 프론트엔드 Vitest는 구문·분기·함수·라인 100%(`all: true`)를 강제하고, Python CI는 라인 커버리지 90% 하한을 적용합니다.
- **로컬 OS 보안 경계**: 디스크 수준 데이터 보호는 OS 사용자 계정, 디스크 암호화, 외부 백업 정책에 의존합니다.

---

## Git에 유지되는 릴리스 이력 범위 (`v13.0.0+`)

이 저장소는 현대화된 Rust 호스트 및 도메인 그룹 아키텍처 세대(`v13.0.0`부터 `v14.2.0`까지)의 공개 릴리스 이력을 유지합니다:

- `14.2.0` · `14.1.2` · `14.1.1` · `14.1.0` · `14.0.0`
- `13.9.0` · `13.8.0` · `13.7.2` · `13.7.1` · `13.7.0` · `13.6.0` · `13.5.0` · `13.4.2` · `13.4.1` · `13.4.0` · `13.3.1` · `13.3.0` · `13.2.1` · `13.2.0` · `13.1.0` · `13.0.0`

상세 변경 이력은 [`CHANGELOG.md`](./CHANGELOG.md), 아키텍처는 [`ARCHITECTURE.md`](./ARCHITECTURE.md), 보안 지원 범위는 [`SECURITY.md`](./SECURITY.md)를 참고하세요.
