[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../ARCHITECTURE.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./ARCHITECTURE.md)

# Lattice AI 현재 아키텍처

> **상태: 정본(canonical)** — 현재 릴리스와 동기화되는 최신 아키텍처 정본 문서입니다.
> 과거 서브시스템 상세 이력은 [`docs/architecture.md`](../architecture.md)를 참고하세요.

현재 릴리스: **13.7.0 — Living Synthesis**.

Lattice AI는 단일 라이터 Rust 제품 호스트(`lattice-host`), 20개 라우트의 루프백 Python AI 연산 워커(`latticeai.worker_app`), 로컬 SQLite 지식 그래프, 모델 비종속 범용 적응형 에이전트 하네스(Universal Adaptive Agent Harness), React / Vite 8 / TypeScript 7 UI로 구성된 로컬 우선 Digital Brain 플랫폼입니다.

## 시스템 맵

```mermaid
flowchart TB
  user(["사용자"])

  subgraph surfaces["표면 계층 (Surfaces)"]
    direction LR
    ui["React / Vite 8 / TS 7.0.2 앱<br/>지연 라우트 · 라우트별 i18n · ko · en"]
    desktop["Tauri 2.11 데스크톱 셸<br/>호스트 토폴로지 기동"]
    editor["VS Code<br/>확장"]
    browser["브라우저<br/>확장"]
    desktop ~~~ ui ~~~ editor ~~~ browser
  end

  subgraph host["Lattice Host — 제품 서버 (Rust, 127.0.0.1)"]
    direction TB
    door["One Door: 네이티브 422 오퍼레이션 · 41 라우트 패밀리<br/>네이티브도 20개 허용 워커 라우트도 아니면 ⇒ 404"]
    subgraph crates["9개 워크스페이스 크레이트"]
      direction TB
      plat["lattice-platform (317 ops · 7 도메인)<br/>workspaceos · toolsurface · governance · adminops<br/>knowledge · modelops · shell"]
      retr["lattice-retrieval (65 ops)<br/>BM25F 사이드카 · NativeHnswIndex · 3-way RRF<br/>grounding · chronicle as_of · self_model"]
      ingestc["lattice-ingest (28 ops)<br/>청크 증분 동기화 · 디바운스 워치<br/>네이티브 Obsidian 위키링크 · Notion/Git/Mail 연동"]
      chatc["lattice-chat (8 ops)<br/>채팅 · 히스토리 · 네이티브 ingest_generated"]
      jobsc["lattice-jobs (4 ops)<br/>인덱스 · 적응형 드레인 스케줄러"]
      authc["lattice-auth<br/>비밀번호 로그인 · 세션 · CSRF"]
      agentc["lattice-agent (6 그룹)<br/>kernel · parse · content · tools · surface · prompts<br/>적응형 하네스 · DurableWorkflowStore"]
      corec["lattice-core<br/>Store (세대 에폭) · GraphWriter (단일 라이터)<br/>temporal · entity_resolution · embedding_contract"]
      hostc["lattice-host<br/>게이트웨이 · 슈퍼바이저 · 정적 UI"]
    end
    supervisor["슈퍼바이저<br/>uvicorn 워커 기동 · /health 게이트 · 백오프 재시작"]
    door --> crates
    door --> supervisor
  end

  subgraph gates["신뢰 경계 — Rust 강제"]
    direction LR
    trust["인증 · 동의 · 감사 · 마스킹"]
    mode["PermissionMode<br/>strict · trusted · bypass"]
    net["NetworkBoundary<br/>local_only · cloud_allowed"]
    breakers["서킷 브레이커<br/>모드 불변"]
    trust ~~~ mode ~~~ net ~~~ breakers
  end

  subgraph worker["Python AI 워커 — 연산 전용 (latticeai.worker_app · 20 라우트)"]
    direction LR
    llm["추론<br/>/agent/llm · /worker/llm/stream<br/>oMLX · MLX · Ollama · llama.cpp · LM Studio · vLLM"]
    compute["연산 심<br/>/worker/{embed,extract,parse,asr,<br/>render/docx|pdf|pptx|xlsx,vector/query}"]
    catalog["모델·엔진 카탈로그<br/>/models · /engines/* · /worker/sysinfo · /health"]
    llm ~~~ compute ~~~ catalog
  end

  subgraph data["Brain — 단일 라이터 SQLite"]
    direction LR
    kg["지식 그래프<br/>이중시간 노드 · edges_v2 · 출처(provenance)"]
    store["SQLite WAL<br/>세대 에폭 기반 무재시작 복원"]
    archive["포터블 아카이브<br/>.latticebrain (AEAD + X25519/Ed25519)"]
    kg ~~~ store ~~~ archive
  end

  cloud["클라우드 LLM 레인 — 옵트인, 기본 비활성<br/>api_key 실시간 프로브 · cli_oauth (agy / grok)<br/>ReviewSink + 형태 전용 EgressAudit"]

  user --> surfaces
  surfaces --> host
  door -- "허용된 20개 라우트만 전달" --> gates
  gates --> worker
  corec -- "단일 라이터 (Rust)" --> data
  crates -- "WAL 읽기" --> data
  chatc -- "최소 정제 슬라이스만 전달" --> cloud
  cloud -- "스트리밍 응답 + kg_cloud_expansion 제안" --> chatc
  net -. "local_only 시 완전 차단" .-> cloud
  breakers -. "민감/비공개 노드는 모든 모드에서 필터링" .-> cloud

  style cloud stroke-dasharray: 5 5
```

## Rust 워크스페이스 (9개 크레이트)

| 크레이트 | 책임 | 주요 모듈 |
| --- | --- | --- |
| `lattice-host` | 제품 컴포지션 루트, One Door 라우터, 정적 UI 서버, Python 워커 슈퍼바이저 | `gateway/onedoor.rs`, `gateway/product.rs` |
| `lattice-core` | 무재시작 복원 세대 에폭 `Store`, 단일 라이터 `GraphWriter`, 이중시간(`temporal.rs`), 엔티티 해소, `EmbeddingIndexContract`, 멱등 워커 클라이언트 | `graph_write/`, `temporal.rs`, `entity_resolution.rs`, `embedding_contract.rs`, `worker.rs` |
| `lattice-auth` | 비밀번호 인증, 세션 저장소, CSRF Origin 가드 | `session.rs`, `config.rs` |
| `lattice-agent` | 안전 커널, 범용 적응형 에이전트 하네스(`probe.rs` 3턴 마이크로 프로빙, `ui_resolve.rs`, `rag_grounding.rs`), fail-closed 검증기, `DurableWorkflowStore` | `kernel/`, `parse/`, `content/`, `tools/`, `surface/`, `prompts/` |
| `lattice-retrieval` | 사전 토큰화 BM25F 사이드카(`bm25_node_stats`), 순수 Rust `NativeHnswIndex`, 3-way BM25F+Vector+Graph RRF 하이브리드 검색, 주장 단위 근거 검증·기권, `as_of` 시점 조회, Self-Model | `bm25/`, `native_hnsw.rs`, `hybrid.rs`, `grounding.rs`, `chronicle_api/`, `self_model.rs` |
| `lattice-ingest` | 청크 단위 증분 동기화, `DebouncedWatchEngine`, PDF/표/OCR 구조 보존, `GraphWriter` 기반 네이티브 Obsidian(`[[wikilink]]` `LINKS_TO` 엣지) 및 Notion/Git/Mail 연동 브릿지 | `incremental_sync.rs`, `incremental_watch.rs`, `document_structure.rs`, `local_files_api/{folder.rs,interop.rs}` |
| `lattice-jobs` | 백그라운드 임베딩 및 벡터 인덱스 드레인 스케줄러 | `index.rs` |
| `lattice-platform` | 7개 도메인 라우트 패밀리: `workspaceos`, `toolsurface`(네이티브 `POST /mcp`, 도구, 플러그인, Computer Use), `governance`(검토함, 변경 제안, 훅, 워크플로), `knowledge`(포터빌리티, X25519/Ed25519 선택 서브그래프 공유, 네트워크 경계, 멀티모달, 음성), `modelops`, `adminops`, `shell` | `workspaceos/`, `toolsurface/`, `governance/`, `knowledge/`, `modelops/`, `adminops/`, `shell/` |
| `lattice-chat` | 근거 기반 채팅 오케스트레이션, 히스토리, 네이티브 `ingest_generated`, 옵트인 클라우드 에스컬레이션 | `chat.rs`, `history.rs` |

크레이트 내부 소유권 지도는 [`rust/lattice-agent/ARCHITECTURE.md`](../../rust/lattice-agent/ARCHITECTURE.md)와 [`rust/lattice-platform/ARCHITECTURE.md`](../../rust/lattice-platform/ARCHITECTURE.md)에 있습니다.

## 지식 수집 및 검색 파이프라인

```mermaid
flowchart LR
  src["파일 · 폴더 · Obsidian 볼트<br/>Notion · Git · Mail · 웹 · 멀티모달"]
  fp{"지문 검사<br/>크기 + 수정시각 + sha256"}
  skip["변경 없음 건너뜀"]
  sync["청크 단위 증분 동기화<br/>구조 + 표 + OCR + 위키링크"]
  gw["GraphWriter (단일 라이터)<br/>이중시간 노드 · edges_v2 · 출처"]
  idx["BM25F 사이드카 + NativeHnswIndex<br/>+ 비동기 /worker/embed 드레인"]
  rrf["3-way RRF 하이브리드 검색<br/>대체(superseded) 제외 · 근거 검증"]
  ans["근거 인용 답변<br/>또는 fail-closed 기권"]

  src --> fp
  fp -->|동일| skip
  fp -->|변경| sync --> gw --> idx --> rrf --> ans
```

- **단일 라이터 SQLite (`GraphWriter`):** 모든 쓰기는 `lattice-core::graph_write`로 직렬화됩니다. `.latticebrain` 아카이브 복원 시 스토어 세대 에폭이 증가해 기존 커넥션이 자동 교체되므로 호스트 재시작이 필요 없습니다.
- **네이티브 연동 브릿지:** `POST /api/ingestion/obsidian`은 `.md` 노트를 파싱하고 `[[wikilink]]` 참조에서 `LINKS_TO` 엣지를 `GraphWriter`로 직접 생성합니다. `POST /api/ingestion/interop`은 사용자 승인 토큰 하에 Notion 디렉터리/`.zip` 내보내기, 로컬 Git 커밋 로그, `.eml`/`.ics` 메일·캘린더 파일을 네이티브 Rust로 인제스트합니다.
- **근거 기반 검색:** 활성 주장과 대체(superseded) 주장을 쿼리 시점에 분리하며(`include_historical = false` 기본), `as_of` 시점 여행과 `grounding.rs` 주장 검증을 제공합니다.

## Python AI 워커 및 크로스플랫폼 모델 런타임

`latticeai.worker_app`(`create_worker_app`)은 **20개** 허용 라우트만 제공하는 루프백 전용 연산 워커이며 `worker_resilience.py`(`x-lattice-idempotency-key` 병합 및 단항 타임아웃 격리)로 보호됩니다.

- **크로스플랫폼 엔진 어댑터 (`latticeai/services/engine_adapters.py`):**
  - **macOS (Apple Silicon):** `omlx`(Homebrew 또는 `~/.omlx/venv` 무-Homebrew universal2 휠 폴백) 계층형 RAM/SSD KV 캐시 및 실시간 가용 메모리 압박 자동 튜닝(`host_available_ram_gb`, `omlx_serve_args`), `local_mlx`, `ollama`, `lmstudio`, `llamacpp`.
  - **Windows:** `winget` 무인 설치(`ollama`, `llamacpp`, `lmstudio`) 및 `pip install llama-cpp-python[server]` 자동 폴백.
  - **Linux:** 공식 Ollama 설치 스크립트 및 `pip` `llamacpp` / `vllm`, 양자화 KV GPU 서빙(`llamacpp_serve_args`).
- **모델 라우팅:** `Qwen3.8-27B-4bit`는 32GB+ Apple Silicon Mac에서 `mlx-community/Qwen3.8-27B-4bit`로, Windows/Linux에서는 공개 GGUF 경로(`ggml-org/Qwen3.8-27B-GGUF` / `unsloth/Qwen3.8-27B-GGUF`)로 자동 매핑되며 `HF_XET_HIGH_PERFORMANCE=1`이 기본 활성화됩니다.

## 프론트엔드, 툴체인 및 검증 게이트

- **프론트엔드 (`frontend/src`):** React + Vite 8 + TypeScript 7.0.2 (`tsgo`), 네이티브 ESM 설정([`vite.config.mts`](../../vite.config.mts), [`vitest.config.mts`](../../vitest.config.mts)), 라우트별 지연 로딩 및 i18n 네임스페이스(`ko`, `en`), 모든 라우트·패널 `ErrorBoundary` 격리.
- **무-TS5 OpenAPI 코드 생성:** [`scripts/generate_openapi_types.mjs`](../../scripts/generate_openapi_types.mjs)가 TypeScript 5 의존성 없이 `frontend/openapi.json`(415 경로 / 457 오퍼레이션)에서 `frontend/src/api/openapi.ts`를 결정론적으로 생성합니다.
- **자동 검증 게이트:**
  - 프론트엔드: `npm run test:frontend:coverage`(111개 테스트 파일 4대 지표 100%), `tsc 7.0.2`, 번들 예산(`<= 150 KiB` gzip), i18n 리터럴·네임스페이스 검증.
  - Python: `npm run test:coverage`(`>= 90%` 하한, 현재 `96%+`), `ruff`, `mypy`, 도메인별 모듈화 단위 테스트(`tests/unit/`).
  - Rust & 데스크톱: `cargo fmt --check`, `cargo clippy --workspace --all-targets -- -D warnings`, 병렬 `cargo-nextest`, `src-tauri` `cargo check`.
  - 문서 & 비주얼: `docs:check-current`, `docs:check-links`, `docs:check-languages`(EN ↔ KO 100% 패리티), `check:max-file-lines`, Playwright 비주얼 스모크 및 릴리스 증거 바인딩.

## 로컬 우선 경계, 거버넌스 및 포터빌리티

- **제안 우선 거버넌스:** 신규 생성은 최소 마찰로 실행되며, 기존 사용자 파일 수정·삭제는 검토함(Review Center) 제안으로 스테이징되어 명시적 승인 후에만 반영됩니다.
- **네트워크 경계:** 기본값은 `local_only`입니다. 옵트인 `cloud_allowed`에서도 `sensitive` / `private` / `do_not_share` 노드는 양 모드 모두 차단되며, 턴/세션 토큰 예산, 형태 전용 송출 감사, `kg_cloud_expansion` 제안 스테이징이 적용됩니다.
- **포터빌리티 및 선택적 Brain Network:** 전체 백업은 AEAD 암호화 `.latticebrain` 아카이브(v2 KDF)를 사용합니다. 선택적 서브그래프 공유는 옵트인(`LATTICEAI_BRAIN_NETWORK=1`)으로 X25519 봉인 암호화와 Ed25519 출처 서명을 적용하며 수신 측에는 제안으로만 도착합니다.

## 릴리스 산출물 맵

13.7.0 정확 버전 산출물 이름:

- Python 휠: `dist/ltcai-13.7.0-py3-none-any.whl`
- Python sdist: `dist/ltcai-13.7.0.tar.gz`
- npm tarball: `ltcai-13.7.0.tgz`
- VS Code / Cursor / Antigravity 확장: `dist/ltcai-13.7.0.vsix`
- macOS Apple Silicon DMG: `src-tauri/target/release/bundle/dmg/Lattice AI_13.7.0_aarch64.dmg`

## 알려진 경계

- **PostgreSQL 백엔드:** 내보내기/상태 표면은 정직하게 `planned`로 표시되며 SQLite WAL이 정본 단일 라이터입니다.
- **실시간 메일박스/시스템 캘린더 연동:** 의도적으로 범위 밖이며 `mail` 연동 브릿지는 로컬 `.eml`/`.ics` 파일만 처리합니다.
- **남은 로드맵 항목:** [`docs/ko/ROADMAP.md`](./ROADMAP.md)와 [`docs/ko/FEATURE_STATUS.md`](./FEATURE_STATUS.md)에서 추적합니다.
