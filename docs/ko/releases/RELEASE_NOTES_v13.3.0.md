[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../releases/RELEASE_NOTES_v13.3.0.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./RELEASE_NOTES_v13.3.0.md)

# Lattice AI 13.3.0 — Living Synthesis

> **상태: historical** — v13.3.0의 보존된 릴리스 노트입니다.

2026-11-19

**13.3.0 Living Synthesis**는 Rust 워크스페이스와 Python 워커 전반에 걸쳐 v13.2.0의 모든 핵심 서브시스템을 실제 프로덕션 경로에 전면 연결하고 하드닝했습니다. `worker_resilience.py`의 비동기 동시성 및 취소 버그를 수정하고, `post_idempotent_compute`, `verify_grounding_and_abstention`, `entity_resolution` + `temporal::query_temporal_timeline`, `DebouncedWatchEngine` + `sync_document_incremental`, `DurableWorkflowStore`를 라이브 HTTP/채팅/감시/워크플로 경로에 직접 연결했으며, Rust 비테스트 코드의 `.unwrap()`을 전량 제거하고 중복된 7개의 `unsafe { libc::localtime_r / gmtime_r / mktime }` 블록을 단일 안전 `lattice-core` 시계 래퍼로 통합했고, Rust·Python·Frontend 전반의 모든 수기 비테스트 프로덕션 소스 파일을 ≤800줄로 줄이면서도 **100% Vitest 커버리지**를 유지했습니다.

## 주요 반영 사항

- **핵심 v13.2.0 서브시스템의 라이브 프로덕션 연결:**
  - **멱등 컴퓨트 심 (`rust/lattice-core/src/worker.rs`, `lattice-ingest`, `lattice-retrieval`, `lattice-chat`, `lattice-platform`):** `(path, body)`당 결정론적 SHA-256 `x-lattice-idempotency-key`를 사용하는 `WorkerSeamClient::post_idempotent_compute` 및 `post_idempotent_compute_with_policy`를 추가해 `/worker/parse`, `/worker/extract`, `/worker/embed`, `/worker/render/*` 전반에 연결했습니다(`query_embed.rs`의 대화형 쿼리 시점 임베딩에는 즉각적인 어휘 폴백을 보존하기 위해 `max_retries: 0` 단일 시도 병합 적용).
  - **주장 단위 근거 텔레메트리 (`rust/lattice-chat/src/helpers.rs`):** `lattice_retrieval::grounding::verify_grounding_and_abstention`을 `assess_answer_grounding`에 직접 연결해 기존 근거 판정과 함께 구조화된 `claim_verification`(`claim_support_ratio`, `citation_precision`, `citation_recall`, `superseded_citation_leaks`, `numeric_mismatches`, `polarity_mismatches`, `should_abstain`, `abstention_reason`)을 첨부합니다.
  - **이중 시간축 및 개체 통합 (`rust/lattice-retrieval/src/brain_api/consistency.rs`):** `lattice_core::entity_resolution`(`resolve_entities_in_conn`, `apply_entity_consolidation`, `EntityAliasBook`)과 `lattice_core::temporal::query_temporal_timeline`을 `BrainIntelligenceService::consolidate`에 연결해 `entity_resolution`·`temporal_timeline` 페이로드를 반환하고 `apply=true` 시 가역적 `SAME_AS` 통합을 반영합니다.
  - **OS 이벤트 디바운스 감시 및 증분 청크 동기화 (`rust/lattice-ingest/src/local_files_api/watch_bridge.rs`):** `DebouncedWatchEngine`을 논블로킹 `spawn_blocking` 틱으로 백그라운드 폴러에 연결하고, 비동기 런타임 교착 없이 추적 파일 업데이트에 `sync_document_incremental`을 적용했습니다.
  - **내구성 워크플로 체크포인팅 (`rust/lattice-platform/src/governance/workflow_designer/executor.rs`):** `lattice_agent::kernel::workflow::DurableWorkflowStore`를 연결해 워크플로 실행을 `{data_dir}/durable_workflows/{run_id}.json`에 원자적으로 체크포인트합니다.
- **워커 복원력 동시성 및 수집 하드닝:**
  - **Python 컴퓨트 미들웨어 (`latticeai/api/worker_resilience.py`, `tests/unit/test_worker_resilience.py`):** 병합 대기자(`asyncio.wait_for(asyncio.shield(...))`)를 구조화된 `504`/`503` JSON 오류 처리로 감싸고, 응답 `body_iterator` 스트리밍을 단항 타임아웃 예산 안으로 편입했으며, `BaseException` / `asyncio.CancelledError` 발생 시에도 `_inflight[scope_key]`가 항상 해제되도록 보장했습니다.
  - **업로드 수집 안전성 (`rust/lattice-ingest/src/local_files_api/ingest.rs`):** 나노초 단독 임시 파일명을 충돌 없는 `{pid}-{nanos}-{seq}` 원자적 카운터로 교체하고, `persist_structure_and_multimodal_overlay` 실패를 조용히 버리지 않고 `structure_overlay_warning`으로 노출합니다.
- **비테스트 `.unwrap()` 0건, 단일 `libc` 시간 래퍼 및 ≤800줄 프로덕션 모듈화:**
  - `rust/lattice-platform/src/`(`admin/handlers.rs`, `automation.rs`, `portability/archive.rs`, `setup/demo.rs`, `agent_registry.rs`, `workspaceos/features.rs`)에 남아 있던 비테스트 `.unwrap()` 9건을 전량 제거하여 Rust 워크스페이스 비테스트 `.unwrap()` **0건**을 달성했습니다.
  - 중복된 7개의 `unsafe { libc::localtime_r / gmtime_r / mktime }` 블록을 `rust/lattice-core/src/graph_write/clock.rs`의 단일 안전 API(`local_wall_parts`, `local_iso_for`, `naive_local_secs`, `local_offset_for_naive_wall`)로 통합하고 `rust/lattice-retrieval/Cargo.toml`에서 미사용 `libc` 의존성을 제거했습니다.
  - 마지막으로 남아 있던 800줄 초과 비테스트 프로덕션 파일(`rust/lattice-chat/src/{cloud/provider.rs,intents.rs,helpers.rs}` → `provider_tests.rs`, `intents_tests.rs`, `helpers_tests.rs`; `lattice_brain/graph/_kg_common/extraction.py` → `extraction_triples.py`)을 분할해 Rust·Python·Frontend 전역에서 800줄 초과 수기 비테스트 프로덕션 파일 **0건**을 달성했으며, `docs/releases/RELEASE_NOTES_v13.2.1.md`의 부정확한 파일명도 바로잡았습니다.

## 배포 아티팩트

- `dist/ltcai-13.3.0-py3-none-any.whl`
- `dist/ltcai-13.3.0.tar.gz`
- `ltcai-13.3.0.tgz`
- `dist/ltcai-13.3.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_13.3.0_aarch64.dmg`
