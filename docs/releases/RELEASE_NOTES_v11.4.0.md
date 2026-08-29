# Lattice AI v11.4.0 — Rust Foundation (2026-08-11)

> **Status: historical** — point-in-time release note.

Rust 전환의 첫 릴리스입니다. 목표 아키텍처(데스크톱·브라우저·VS Code →
React/TS → **Lattice Host(Rust)** → lattice-core / Agent Runtime / IPC →
Python AI Worker)는 [docs/v11.4.0_RUST_FOUNDATION_PLAN.md](../v11.4.0_RUST_FOUNDATION_PLAN.md)에
있고, 11.4.0은 그 **Phase 1**을 출하합니다 — 전면 재작성이 아니라,
**작동하고 증명된 Rust 조각**부터. 기존 Python 서버는 오늘도 모든 제품
표면을 그대로 서빙합니다(무손상).

## `rust/` cargo workspace — 3개 크레이트

### lattice-core — Brain 읽기 층 + 임베딩 산술
- Python과 **같은** `knowledge_graph.sqlite`를 읽기 전용(WAL,
  busy_timeout — 양보하는 쪽은 Rust)으로 연다.
- 해시 임베더(`lattice-local-hash-v1:384`)의 1:1 포트 — 토크나이저,
  blake2b-8 인덱스/부호, L2 정규화, `<f32` LE 인코딩까지 **bit-for-bit**
  골든 검증(8개 텍스트 × 384차원 `to_bits()` 비교).
- lane별 테이블 이원화(`kgv2_nodes` 뷰 / legacy `nodes`·`chunks` /
  `nodes_v2` 스코핑)를 현행 그대로 재현. FTS5 trigram은 가정이 아니라
  프로브 테스트로 증명.

### lattice-retrieval — 네이티브 하이브리드 검색, 패리티 증명 동반
- `hybrid_search`(2채널 alpha 융합) · `search`(키워드) ·
  `vector_search`(벡터)를 1/rank 어휘 점수, trigram FTS→LIKE 폴백→topic
  보충→(hits, type_boost, updated_at) 2-pass 재정렬, max-정규화 벡터,
  질의 분류(ko/en)·재작성·recency 감쇠, identity rerank, CPython
  round-half-even까지 그대로 포트.
- **패리티 75/75 완전 일치(엡실론 0)** — 25개 질의(4 클래스 ko/en,
  동점·청크·워크스페이스 경계·빈 질의 포함) × 3엔진의 JSON 응답을
  커밋된 Python 골든과 통째로 비교.
- **양방향 계약**: `tests/unit/test_rust_parity_contract.py`(80 테스트)가
  같은 픽스처·골든에 Python 엔진을 상시 재검증 — 어느 쪽이 변해도
  조용히 어긋날 수 없다.

### lattice-host — 수퍼바이저 + IPC/API 게이트웨이 (옵트인)
- Python 워커 수퍼바이저: HTTP `/health` 게이팅(기존 TCP 프로브 대체),
  크래시 지수 백오프 자동 재시작, SIGTERM→유예→SIGKILL 우아한 종료,
  포트 통일(4825 기준 빈 포트 탐색 — 기존 4825/8765/4899 불일치 해소),
  후보 해석 우선순위 버그(`sort()+dedup()`) 구조적 수정.
- 게이트웨이(axum, 127.0.0.1 전용): `/host/health`·`/host/status`,
  네이티브 `/rust/search/{hybrid,keyword,vector}`(GET/POST, 골든 대비
  완전 일치 검증), 그 외 전 경로 스트리밍 리버스 프록시(SSE 통과 증명).
  **front-door는 옵트인 바이너리** — 기존 진입 경로는 그대로다.
- 테스트 194개(수퍼바이저 생명주기·시그널·백오프·프록시·스트리밍) —
  테스트가 실제 데드락 2건을 잡아 수정했다.

## 데스크톱이 새 기초 위에 올라탐

Tauri 셸 `main.rs` 451줄 → **149줄** 씬 셸 + `lattice-host` 크레이트
소비. 5개 IPC 커맨드(`backend_origin`/`backend_status`/`restart_backend`/
`shutdown_backend`/`select_folder`)의 프론트 계약은 필드 단위로 보존
(추가 필드만 확장). 사용자 가시 변화 없이 수퍼바이저 품질만 오른다.

## 게이트/CI

- CI에 독립 `rust` 잡(ubuntu): `cargo fmt --check` + `clippy -D warnings`
  + `cargo test --workspace` — tauri 시스템 의존 없이.
- `check_max_file_lines.mjs`가 `*.rs`도 검사(전 파일 ≤500줄).
- `bump_version.py`/`test_version_consistency.py`에 rust workspace 버전
  편입(동기 타깃 18→25).

## 검증

- Python **6,643 테스트 · 문·분기 100.00%**(`fail_under=100`) — 플로어
  그대로. 프론트 **1,761 테스트 · 4지표 100%**. Rust **194 테스트** +
  패리티 75/75 + Python 계약 80.
- mypy 0 에러 · ruff 클린 · clippy `-D warnings` 클린 · 전체 lint 체인
  그린.

## Phase 1이 하지 않는 것 (정직한 경계)

3채널 서비스층 검색·KG 읽기 API의 네이티브화, Ingestion/Jobs/Scheduler,
Agent Runtime의 Rust화, 게이트웨이 기본 front-door 승격은 Phase 2~4로
명시 이월(계획 문서의 로드맵 표 참조). HNSW/CE rerank 등 비기본 경로는
워커 프록시로 그대로 동작한다.

## 산출물

- `dist/ltcai-11.4.0-py3-none-any.whl`
- `dist/ltcai-11.4.0.tar.gz`
- `ltcai-11.4.0.tgz`
- `dist/ltcai-11.4.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_11.4.0_aarch64.dmg`

와일드카드 업로드는 사용하지 않습니다.
