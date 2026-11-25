[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../CHANGELOG.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./CHANGELOG.md)

# 변경 이력 (Changelog)

최상단 항목은 현재 릴리스 라인 또는 미배포 `main` 브랜치 작업 내역입니다.
이전 항목은 해당 릴리스 시점의 동작을 기록한 이력이며, 공개 변경 이력은 `v13.0.0` 이후 버전만 유지합니다.

## [14.1.1] - 2026-11-25 — Living Synthesis

### 변경됨
- **저장소 전역 Python 포매팅 및 Blame 위생 (`.git-blame-ignore-revs`, `package.json`, `.github/workflows/ci.yml`):**
  - 전체 Python 파일 219개를 전용 포매팅 커밋(`55ba57c381a433066fd94434c3a6250c4a1beb59`)으로 `ruff format .` 정렬하고 `.git-blame-ignore-revs`에 기록했습니다.
  - `npm run lint:python`(CI 실행)에 `ruff check .`, `npm run typecheck:python`과 함께 `ruff format --check .`를 추가해 포맷 드리프트를 차단합니다.
- **전역 `C901` 복잡도 강제 (`pyproject.toml`, `latticeai/api/models.py`, `latticeai/api/worker_compute.py`, `tests/unit/test_worker_compute_extract.py`):**
  - `create_models_router`와 `create_worker_compute_router`를 응집도 높은 모듈 수준 하위 등록 함수와 헬퍼로 분해해 API/OpenAPI 변경 없이 McCabe `max-complexity = 20` 이하를 달성했습니다.
  - `[tool.ruff.lint.per-file-ignores]`에서 `"latticeai/api/*.py"` 및 `"latticeai/app_factory.py"`의 `"C901"` 예외를 전량 삭제(`C901` 예외 0건)하고 `/worker/extract` 테스트를 `tests/unit/test_worker_compute_extract.py`로 분리해 1,000줄 파일 상한을 준수했습니다.
- **서버 i18n 래칫 `0 unclaimed` 달성 (`scripts/check_server_i18n.mjs`):**
  - `worker_compute_render.py`와 `worker_resilience.py`를 감사 후 `NON_ROUTER_HELPERS`에 명시 등록하고, 향후 `latticeai/api/`에 미분류 `.py` 모듈이 추가되면 `exit 1`로 실패(fail-closed)하도록 래칫을 잠갔습니다.
- **컨테이너 소유권 및 권한 일관화 (`Dockerfile`):**
  - `Dockerfile` 43번 줄의 `lattice-host` 바이너리 복사에 `--chown=lattice:lattice --chmod=0755`를 명시해 런타임 스테이지 권한 정책을 통일했습니다.

## [14.1.0] - 2026-11-25 — Living Synthesis

### 수정됨
- **Rust ↔ Python UTC 타임스탬프 통합 (`rust/lattice-core/src/graph_write/clock.rs`, `lattice_brain/utils.py`, `lattice_brain/graph/_kg_fsutil.py`):**
  - Python의 `local_now()`, `now_iso()`, `_recency_score()`, `_safe_iso_from_stat_mtime()`을 Rust `SystemClock::now_iso()`와 동일한 오프셋 없는 UTC(`YYYY-MM-DDTHH:MM:SS`)로 통일해 비-UTC 환경의 9시간 편차를 제거했습니다.
  - `clock.rs`, `workspace/pyutil.rs`, `retrieval/routes/mod.rs`, `chat/turn.rs`, `command_center_api/store.rs`의 구형 로컬 시각 주석을 정리하고 `Asia/Seoul`, `America/New_York`, `UTC` 회귀 테스트(`tests/unit/test_timestamp_utc_consistency.py`)를 추가했습니다.
- **Tauri 프로덕션 포트 고정 정책 정렬 (`src-tauri/src/topology.rs`, `src-tauri/src/backend.rs`):**
  - `PortPolicy`(`PinnedProduction` / `AllowDynamicFallback`)를 도입해 프로덕션 빌드는 `tauri.conf.json` CSP 및 `capabilities/default.json`과 일치하는 `PINNED_PRODUCTION_PORT`(`4825`)로 엄격히 고정하고, 포트 충돌 시 조용한 `4826+` 폴백 대신 실행 가능한 `gateway_error` 안내와 함께 즉시 실패(fail-fast)하도록 수정했습니다.

### 변경됨
- **Python 예외 처리 세분화 및 Ruff 규칙 강화 (`pyproject.toml`, `lattice_brain/`, `latticeai/`, `scripts/`, `tests/`):**
  - `lattice_brain/multimodal/images.py`(6곳), `latticeai/models/router/loading.py`(6곳) 및 저장소 전반의 포괄적 `except Exception` / `# noqa: BLE001`을 구체적 예외 튜플로 교체했습니다.
  - `[tool.ruff.lint] select`에 `"BLE"`를 전역 추가하고 `[tool.ruff.lint.mccabe] max-complexity`를 `30`에서 `20`으로 하향(초과 함수 12개 리팩터링)했으며, 프로덕션 `S608`을 유지한 채 `S603`, `S105`, `S108` 파일별 예외를 최소 범위로 축소했습니다.
- **Rust 인증 메시지, 비실패 Tauri 커맨드 및 독스트링 정리 (`rust/lattice-auth/`, `src-tauri/src/main.rs`, `lattice_brain/quiet.py`):**
  - `CSRF_DENIED_DETAIL`과 `CSRF_ERROR_CODE`를 `rust/lattice-auth/src/messages.rs`로 이동하고 `csrf.rs`의 사유 문자열을 구조화된 `CsrfReason` 열거형으로 교체했습니다(HTTP 403 JSON 계약 바이트 단위 유지).
  - 비실패 Tauri 커맨드 `backend_status`, `shutdown_backend`가 `Result<BackendStatus, String>` 대신 `tauri::AppHandle`을 통해 `BackendStatus`를 직접 반환하도록 정리하고 `lattice_brain/quiet.py` 독스트링을 최신화했습니다.
- **TypeScript 7 `noUncheckedIndexedAccess` 활성화 (`tsconfig.json`, `frontend/src/`):**
  - `"noUncheckedIndexedAccess": true`를 활성화해 68개 프론트엔드 소스·테스트 파일의 오류 457건을 해소하며 113개 테스트 파일 / 1,852개 테스트 Vitest **100% 커버리지**를 유지했습니다.
- **PR 시각 회귀 게이트 활성화 (`.github/workflows/ci.yml`):**
  - Playwright `visual` 스모크 잡이 `main` 푸시뿐 아니라 풀 리퀘스트에서도 실행되도록 확장했습니다.
- **v14.1.0 문서 최신화:**
  - 저장소 루트, `docs/`, 확장, 크레이트 맵 및 한국어 미러(`README.ko.md`, `docs/ko/`) 전체를 `v14.1.0` 기준으로 갱신하고 구형 v13 이전 서술 및 완료된 `v12.x` 로드맵 항목을 정리했습니다.

## [14.0.0] - 2026-11-25 — Living Synthesis

### 보안 및 안정성
- **인증 및 레이트 리미팅 (`rust/lattice-auth/src/{password.rs,ratelimit.rs,csrf.rs,routes.rs}`):**
  - `hash_password`의 패닉 `.expect()`를 `Result<String, PasswordError>`로 교체하고, 신규 비밀번호를 버전화 `$scrypt$n=14,r=8,p=1${salt}:{key}` 형식으로 해싱(레거시 `{salt}:{key}` 검증 호환 유지)하며 `stored_is_hashed` 검증을 강화했습니다.
  - 레이트 리미터 맵 상한(`MAX_ENTRIES = 10_000`), 만료 우선/최고령 축출, 뮤텍스 포이즈닝 복구, `check_ip`/`enforce` 간 통합 HTTP `429` + `Retry-After` 계약 및 영문 표준 CSRF 거부 메시지를 적용했습니다.
- **`libc` `unsafe` 없는 순수 Rust 시계 및 Fail-Closed 호스트 (`rust/lattice-core/src/graph_write/clock.rs`, `rust/lattice-host/src/`):**
  - `lattice-core`에서 모든 `unsafe` `libc` 시계 FFI 호출과 `libc` 의존성을 제거하고 순수 Rust UTC 달력 연산으로 교체했습니다.
  - 무리다이렉트 프록시 클라이언트 생성 실패 시 `GatewayState::with_client`가 fail-closed 하도록 변경하고 `std::env::set_var`를 `EnvOverlayProbe`로 대체했습니다.
- **논블로킹 Tokio I/O 및 상한 캐시 (`rust/lattice-ingest/`, `rust/lattice-chat/`, `rust/lattice-retrieval/`, `rust/lattice-jobs/`):**
  - `local_files_api/local.rs`의 블로킹 `std::fs` 호출과 `documents.rs`의 문서 세션 영속화를 `tokio::task::spawn_blocking`으로 오프로딩하고 디스크 쓰기 전에 뮤텍스를 해제합니다.
  - `cloud/budget.rs`(`4,096` 스코프)와 `native_hnsw.rs`(`32` 그래프)에 LRU 축출과 포이즈닝 복구를 적용하고, `lattice-jobs` 부분 드레인 보존·클레임 해제 로깅·`/host/jobs` 루프백/Bearer 가드를 추가했습니다.
- **Python 워커 및 패키징 (`pyproject.toml`, `lattice_brain/`, `latticeai/`):**
  - `local`/`all` extra의 `mlx-vlm`/`mlx-lm`에 `darwin`/`arm64` 플랫폼 마커를 추가하고, `hnswlib`/`pyautogui`를 `all`에 포함했으며, 빈 extra 제거·Changelog URL 교정·Ruff `S608`/`S105`/`S108`/`S603`/`S604`/`S607` 규칙 재활성화를 완료했습니다.
  - `ContextVar` 기반 `llm_router_scope` DI, 안전한 `LATTICEAI_VECTOR_DIM` 파싱 및 `embed_async`, 공용 `record_suppressed`, 보안 버킷 상한 및 잘못된 CIDR 경고, 부작용 없는 `todo_read`를 반영했습니다.
- **데스크톱 셸 및 확장 (`src-tauri/`, `vscode-extension/`, `browser-extension/`):**
  - Tauri 게이트웨이 `start`/`stop`/`restart` 비동기 직렬화, 루프백 외부 오리진 검증 및 안전 포트 증가, `spawn_blocking` 기반 `/usr/bin/osascript`, `'unsafe-inline'` 제거 `127.0.0.1:4825` 고정 CSP/권한을 적용했습니다.
  - VS Code 확장(Bearer 인증, TTL 단일 사용 승인 토큰 캐시, 스트림 타임아웃, 실시간 설정 리로드, 크로스플랫폼 VSIX 패키징)과 브라우저 확장(포트 `4825` 권한 축소, 라이브 DOM 가시성 추출, UTF-8 바이트 절단, 종단간 fetch 타임아웃)을 강화했습니다.

### 변경됨
- **엄격 TypeScript 7 프론트엔드 (`tsconfig.json`, `vitest.config.mts`, `package.json`, `frontend/src/`):**
  - `noImplicitReturns`, `noFallthroughCasesInSwitch`, `noUnusedLocals`, `noUnusedParameters`, `verbatimModuleSyntax`를 활성화하고 `AppRouteView.tsx` 분리 및 런타임 패키지의 `dependencies` 이동을 완료하며 113개 테스트 파일 Vitest 100% 커버리지를 유지했습니다.
- **CI/CD 및 다이제스트 고정 멀티스테이지 Docker (`.github/workflows/{ci,release,publish}.yml`, `Dockerfile`, `.dockerignore`):**
  - `rust-windows`(`windows-latest`), `tauri-check`(`macos-latest`), `docker-smoke` CI 잡과 최상위 `permissions: contents: read`, 핀 고정 `taiki-e/install-action`을 추가하고 모든 `Dockerfile` 베이스 이미지를 SHA256 다이제스트로 고정하며 프론트엔드 빌드 스테이지를 통합했습니다.
- **14.0.0 아키텍처 문서 최신화 (`ARCHITECTURE.md`, `docs/architecture.md`, `docs/ko/`, `rust/lattice-{agent,platform}/ARCHITECTURE.md`):**
  - 모든 정본 아키텍처 문서와 한국어 미러를 `14.0.0` 기준으로 최신화하고 삭제된 구형 모듈·별칭 이력을 정리했습니다.

## [13.9.0] - 2026-11-24 — Living Synthesis

### 수정됨
- 워크스페이스 범위 무결성 검사·복구(`check_graph_integrity_scoped`, `execute_integrity_repair`) 및 관리자 전용 전역 모드, `Store::write_conn` 단일 라이터 풀 강제, 노드/엣지/교정/병합 대상의 교차 워크스페이스 403 차단, Windows 핸들 고정 TOCTOU 방지 `safe_io`, 실모델 RAG 평가 주장 근거 검증 게이트, `/host/diagnostics` 포스처 게이트를 적용했습니다.

### 추가됨
- 16개 디지털 브레인 확장 엔드포인트를 `brain_api::MOUNTED`와 `memory_brain.json`에 통합하고 5개 대화형 React 브레인 거버넌스 패널 및 `.github/workflows/nightly-rag-eval.yml`을 추가했습니다.

## [13.8.0] - 2026-11-24 — Living Synthesis

### 추가됨
- **P0.1 진정성 있는 RAG 평가 프레임워크 (`rust/lattice-retrieval/src/rag_eval_grounded.rs`, `rag_eval_dataset.rs`, `datasets/rag_eval_v1.json`, `scripts/generate_rag_eval_dataset.py`, `scripts/run_real_model_rag_eval.py`):**
  - 결정론적 픽스처(`FixtureDeterministic`)와 실제 로컬/클라우드 모델(`RealModelLocal`, `RealModelCloud`, `RetrievalOnly`, `EndToEnd`) 및 `Executed`/`FixtureOnly`/`Skipped`/`Unavailable` 상태 표기를 엄격히 분리하고, 9개 필수 카테고리를 포괄하는 320개 질의 버전 데이터셋(`rag-eval-v1.0.0`)을 추가했습니다.
- **P0.2 지식 그래프 데이터 무결성 검사 및 안전 복구 (`rust/lattice-core/src/integrity.rs`, `rust/lattice-core/tests/integrity_fault_injection.rs`, `GET /api/brain/integrity`, `POST /api/brain/integrity/repair`):**
  - SQLite 무결성/FK, 댕글링 엣지, 고아 청크·벡터·출처, 교차 워크스페이스 누출, 중복 자연키, 삭제된 파일 경로, 인덱스 드리프트, DAG 순환 참조를 통합 검사하고 드라이런 영향 범위 미리보기·자동 `VACUUM INTO` 백업·안전/승인 복구 분리 및 장애 주입 테스트를 구현했습니다.
- **P0.3 엔티티 해석 고도화 및 선택적 롤백 (`rust/lattice-core/src/entity_resolution.rs`, `entity_resolution_ext.rs`, `/api/brain/entities/{candidates,merge,rollback}`):**
  - 타입 범위 정확 일치로 교차 타입 동음이의어 오병합을 차단하고, 퍼지·다중 신호 점수, 영속 별칭/감사/병합금지 테이블, 교차 워크스페이스 병합 차단, 엣지 마이그레이션 및 `rollback_entity_merge`를 구현했습니다.
- **P0.4 TOCTOU 방지 FD 샌드박스, 클라우드 이그레스 PII/비밀 마스킹 및 프롬프트 인젝션 방어 (`rust/lattice-agent/src/tools/safe_io.rs`, `content/untrusted.rs`, `rust/lattice-agent/tests/security_sandbox_toctou.rs`, `rust/lattice-core/src/redact.rs`, `rust/lattice-chat/src/pipeline/answers.rs`):**
  - OS 수준 `openat` + `O_NOFOLLOW` + `fstat` 루트 검증 기반 파일 I/O, 컴파일된 `OnceLock` PII+비밀 마스킹(클라우드 발신 질문·컨텍스트·`LoopTrace`·`/host/diagnostics`), 워크스페이스 범위 `DurableWorkflowStore::load_scoped`, `ingestion_jobs.workspace_id`, 심볼릭 링크 탈출 차단, 민감 경로 읽기 차단 및 한/영 비신뢰 콘텐츠 프레이밍을 구현했습니다.
- **P1.1–P1.7 시간 축 지식·관계 지능·하이브리드 검색·섀도우 임베딩·멀티에이전트·지식 학습 루프:**
  - `nodes_v2`/`edges_v2` 시간 축·근거 동기화 및 `terminate_edge`, BM25F 허브 팬아웃 제한·근접 중복 제거·재순위화·점수 설명·폴백 추적, Python↔Rust `embedding_index_contract` 수정 및 섀도우 재색인·컷오버·즉시 롤백 레지스트리, `StepErrorKind`/`PartialSuccess`/`ImpactPreview`/마스킹 트레이스 내보내기, 최소 권한 5역할 `MultiAgentSession` 및 외부 학습 업로드 차단 `knowledge_corrections` 학습 루프를 구현했습니다.
- **P2 & P3 자동 인제스트·모델 출처 UX·데스크톱 워치독·고급 KG·조직 거버넌스:**
  - `WatchBatch` 직접 소비(`moved_from_uri` 이동 연동·일시중지), 12개 패밀리 모델 출처 계층 UI(`국가 → 제조사 → 실행 위치`), 워커 워치독 자동 재시작 및 마스킹 `/host/diagnostics`, 토픽 클러스터(`/api/brain/clusters`)·지식 공백(`/api/brain/gaps`), `Modality::Video`, 감사 보존 정책(`prune_audit_events_before`) 및 오프보딩 세션 취소 영수증을 추가했습니다.

## [13.7.2] - 2026-11-24 — Living Synthesis

### 변경됨
- **정본 도메인 모듈 경로 전면 이전 (`rust/lattice-agent`, `rust/lattice-platform`, `rust/lattice-host`, `rust/lattice-chat`, `rust/lattice-retrieval`):**
  - Rust 워크스페이스 전체 77개 이상의 소스·통합 테스트 파일이 정본 도메인 경로(`lattice_platform::<domain>::*`, `lattice_agent::<group>::*`)에서 직접 임포트하도록 이전했습니다.

### 제거됨
- **구형 크레이트 루트 호환성 재내보내기 맵 및 스캐폴드 스텁 (`rust/lattice-agent/src/lib.rs`, `rust/lattice-platform/src/lib.rs`, `rust/lattice-chat/src/lib.rs`):**
  - `lattice-agent`와 `lattice-platform`의 평탄한 크레이트 루트 `pub use` 호환성 맵을 제거해 엄격한 도메인 범위 공개 API 표면만 유지합니다.
  - 미사용 `lattice_chat::crate_ready()` 스캐폴드 스텁을 삭제했습니다.

## [13.7.1] - 2026-11-24 — Living Synthesis

### 제거됨
- **죽은 레거시 `forward()` 폴백 및 호환 스캐폴딩 (`rust/lattice-ingest`, `rust/lattice-platform`, `rust/lattice-retrieval`):**
  - `POST /api/ingestion/jobs/:job_id/resume`, `POST /api/ingestion/folder`, `POST /knowledge-graph/local/index`, `POST /api/browser/read-url`의 레거시 `forward()` 폴백을 제거하고 `GraphWriter` 미연결 시 일관된 HTTP `503`을 반환하도록 통일했습니다.
  - 미사용 `local_files_api::forward`, `browser_api::forward`, `local_files_api::require_seam`, `local_files_api::http::seam_error`, `search_api::require_seam`, `lattice_platform::crate_ready()`를 삭제했습니다.
- **`13.7.0` 이전 리뷰·계획·레거시 문서 (`docs/`, `docs/ko/`):**
  - `CODE_REVIEW_2026-11-20.md`, `reviews/CODEBASE_REVIEW_AND_IMPROVEMENT_ROADMAP.md`, 구형 `v11.x` 계획 문서, `LEGACY_COMPATIBILITY.md`(및 한국어 미러)를 삭제하고, 모든 벤치마크 문서(`ADAPTIVE_HARNESS_BENCHMARK.md`, `BENCHMARKS.md`, `PERFORMANCE.md`)는 그대로 보존했습니다.

### 추가됨
- **Git 연동 및 `GraphWriter` 미연결 503 통합 테스트 (`rust/lattice-ingest/tests/local_files_replay.rs`):**
  - 임시 `git init` 저장소 대상 `source: "git"` 통합 테스트(`dry_run` + 실인제스트)와 `unwired_graph_writer_returns_503_consistently` 503 계약 테스트를 추가했습니다.

## [13.7.0] - 2026-11-24 — Living Synthesis

### 추가됨
- **네이티브 Rust Obsidian 및 Interop 브릿지 (`rust/lattice-ingest/src/local_files_api/interop.rs`, `rust/lattice-ingest/src/local_files_api/ingest.rs`, `rust/lattice-ingest/tests/local_files_replay.rs`):**
  - 폐기된 Python 워커 포워딩을 제거하고 `GraphWriter` 기반 네이티브 Obsidian 볼트(`[[wikilink]]` `LINKS_TO` 엣지, `#tags`, `dry_run` 미리보기) 및 `notion`(디렉터리·`.zip`), `git`(커밋 로그), `mail`(`.eml`·`.ics`) 연동 브릿지를 구현했습니다.

### 변경됨
- **네이티브 ESM Vite 8 / Vitest 5 설정 (`vite.config.mts`, `vitest.config.mts`, `tsconfig.json`):**
  - `vite.config.ts`와 `vitest.config.ts`를 `fileURLToPath` + `readFileSync` 기반 `.mts`로 전환해 Vite 8 `configLoader: 'native'` 경고를 제거했습니다.
- **`v13.0.0+` 릴리스 원장 정리 및 `ARCHITECTURE.md` 최신화:**
  - `v13.0.0` 미만(`v12.x`) 릴리스 노트를 모두 정리하고 `scripts/check_current_release_docs.mjs`와 `tests/unit/test_server_app_decomposition.py`의 하한을 `v13.0.0+`로 갱신했습니다.
  - `ARCHITECTURE.md`와 `docs/ko/ARCHITECTURE.md`에서 과거 프리-v13 이력을 정리하고 `13.7.0` 현재 아키텍처 기준으로 최신화했습니다.

## [13.6.0] - 2026-11-24 — Living Synthesis

### 변경됨
- **TypeScript 5 완전 제거 및 네이티브 TypeScript 7 OpenAPI 타입 생성기 (`scripts/generate_openapi_types.mjs`, `scripts/check_openapi_drift.mjs`, `package.json`, `package-lock.json`):**
  - `openapi-typescript@7.13.0`, `openapi-ts5`(`npm:typescript@^5.9.3`) 별칭, `scripts/run_openapi_typescript.mjs` 래퍼 및 `package.json` `overrides` 설정을 제거하고 결정론적 무의존성 ESM OpenAPI 3.x 타입 생성기(`scripts/generate_openapi_types.mjs`)로 대체했습니다.
  - `typecheck:frontend`를 네이티브 `typescript@7.0.2`(`tsgo`) 기반 `npx tsc -p tsconfig.json --noEmit`으로 직접 실행하도록 복원해 `package-lock.json`에서 TypeScript 5 의존성을 완전히 제거했습니다.
- **Python 단위 테스트 도메인 모듈 분할 (`tests/unit/_runtime_test_fakes.py`, `tests/unit/test_*.py`):**
  - 5개의 대형 `tests/unit/test_cov_p1b_remaining*.py` 커버리지 묶음 파일(총 3,379줄)을 공용 `tests/unit/_runtime_test_fakes.py` 기반 9개의 도메인별 소형 단위 테스트 모듈(각 170~449줄)로 분할해 45개 전체 테스트와 `96%+` Python 라인 커버리지를 유지했습니다.

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
