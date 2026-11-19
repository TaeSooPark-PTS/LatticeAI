[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../releases/RELEASE_NOTES_v13.2.0.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./RELEASE_NOTES_v13.2.0.md)

# Lattice AI 13.2.0 — Living Synthesis

> **상태: historical** — 해당 시점의 보존된 릴리스 노트입니다.

2026-11-19

**13.2.0 Living Synthesis**는 `lattice-core`, `lattice-retrieval`, `lattice-ingest`, `lattice-agent`, `lattice-chat`, `lattice-host`, `lattice-platform` 및 무상태 Python 컴퓨트 워커 전반에 걸쳐 v13.2.0 로드맵의 신뢰성, 인식론적 근거 검증, 증분 수집, 내구성 에이전트 실행, 장애 격리 업그레이드를 완료했으며, 이를 **Section 16 제품 검증 스위트**(`rust/lattice-host/tests/roadmap_section16_suite.rs`)로 종단간 잠갔습니다.

## 주요 반영 사항

- **이중 시간축(Bi-Temporal) 지식 그래프 및 표준 개체 통합 (`temporal.rs`, `entity_resolution.rs`, `db.rs`):**
  - `rust/lattice-core/src/temporal.rs`: 현실 세계의 **유효 시간(valid time)**(`valid_from`..`valid_until`)과 브레인 **트랜잭션 시간(transaction time)**(`recorded_at`, `superseded_at`)을 모두 추적해 시점 조회 및 현재 상태 타임라인 쿼리(`query_temporal_timeline`, `stamp_temporal_fact`)를 지원하고, `TemporalEvolution`·`ComplementaryRefinement`(`REFINES`)를 `ActiveContradiction`과 구분합니다.
  - `rust/lattice-core/src/entity_resolution.rs`: 비파괴적이고 출처를 보존하는 표준 개체 통합(`EntityAliasBook`, 한국어 조사 제거, 다국어 별칭 클러스터, `SAME_AS` 엣지, 가역적 `rollback_entity_consolidation`)을 제공합니다.
  - `rust/lattice-core/src/db.rs`: 네이티브 Rust `GraphWriter` 단일 라이터 소유권과 무상태 Python 컴퓨트 워커 구조를 반영하도록 데이터베이스 쓰기 소유권 문서를 동기화했습니다.
- **주장 단위 근거 검증, 기권(Abstention) 및 분리형 RAG 벤치마크 (`grounding.rs`, `rag_eval_grounded.rs`):**
  - `rust/lattice-retrieval/src/grounding.rs`: 결정론적 주장 단위 근거 검증 및 인용 확인(`verify_grounding_and_abstention`)을 구현해 근거 없는 수치, 극성 반전, 대체된 인용 누수를 포착하고 활성 근거가 없거나 어긋나면 한/영 기권 메시지와 함께 fail-closed(`AbstentionReason`)로 처리합니다.
  - `rust/lattice-retrieval/src/rag_eval_grounded.rs`: 프로덕션 `LocalEmbeddingModel`(`lattice-local-hash-v1`) 위에서 검색(Retrieval)과 생성(Generation)을 분리 평가하는 RAG 벤치마크(`run_separated_rag_evaluation`)를 구축해 한국어/영어/혼합 질의의 `Recall@5`, `NDCG@10`, `MRR`, 대체 지식 배제율 `1.0`, 기권 정확도 `1.0`, 그리고 `gemma-4-local`·`qwen-local`·`cloud-frontier` 간 모델 교체 컨텍스트 불변성을 검증합니다.
- **이벤트 기반 증분 동기화 및 구조 보존 멀티모달 수집 (`incremental_watch.rs`, `incremental_sync.rs`, `document_structure.rs`):**
  - `rust/lattice-ingest/src/incremental_watch.rs`: 버스트 디바운스 병합, 바운드 배칭(`max_batch_size`), SHA-256 이름 변경/이동 상관 추적, 주기적 `WatchScanner` 스냅샷 조정 기능을 갖춘 OS 알림 기반 `DebouncedWatchEngine`(`notify::RecommendedWatcher`)을 추가했습니다.
  - `rust/lattice-ingest/src/incremental_sync.rs`: 미변경 `(text_hash, model_id, dimension)` 벡터를 재사용하고 이동된 파일은 이중 시간축 `SUPERSEDES` 계보로 재배치하며 삭제된 청크를 즉시 은퇴시키는 청크 단위 증분 재임베딩(`sync_document_incremental`)을 구현했습니다.
  - `rust/lattice-ingest/src/document_structure.rs` 및 `local_files_api/ingest.rs`: 파일 업로드 및 수집 시 제목 계층, 페이지/슬라이드/시트, 헤더 고정 `table_structured` 청크, 캡션, 선택적 로컬 `OcrVisionProvider` `ImageText` 노드를 보존합니다.
- **내구성 체크포인트 DAG 에이전트 워크플로 엔진 (`kernel/workflow.rs`):**
  - `rust/lattice-agent/src/kernel/workflow.rs`: 임시 파일-원자적 rename 방식의 `DurableWorkflowStore`, 비순환 위상 검증(`WorkflowSpec::validate_dag`), 병렬 읽기 전용 vs. 직렬 변경 `ExecutionWave` 스케줄링, SHA-256 `idempotency_ledger` 리플레이, 단계 중간 크래시 복구, 재시작을 넘는 상수 시간 `WaitingApproval` 토큰 일시중지/재개, 변경 전 파일 롤백 스냅샷, 브레인 `USED_BY_AGENT` / `GENERATED_BY` 출처 내보내기를 구현했습니다.
- **워커 심 복원력, 클라우드 비밀 파일 가드 및 정직한 그래프 텔레메트리 (`worker.rs`, `worker_resilience.py`, `boundary.rs`, `onedoor.rs`, `audit.rs`):**
  - `rust/lattice-core/src/worker.rs` 및 `latticeai/api/worker_resilience.py`(`worker_profile.py`, `tests/unit/test_worker_resilience.py`): 바운드 `WorkerRetryPolicy`와 `x-lattice-idempotency-key`를 갖춘 opt-in `WorkerSeamClient::post_idempotent_json`과 Python `WorkerIdempotencyStore` 인플라이트 병합, 리플레이 캐싱, 단항 컴퓨트 타임아웃 격리(`LATTICEAI_WORKER_COMPUTE_TIMEOUT_S`)를 연동했습니다.
  - `rust/lattice-chat/src/boundary.rs`: 아웃바운드 클라우드 컨텍스트 페이로드에서 비밀 소스 파일(`.env`, `.env.*`, `.pem`, `.key`, `.p12`, `.pfx`, `id_rsa*`, `id_ed25519*`, `credentials.json` — 단 `.example`/`.sample`/`.template` 제외)을 차단합니다.
  - `rust/lattice-host/src/gateway/onedoor.rs` 및 `rust/lattice-platform/src/adminops/admin/{audit,handlers}.rs`: `probe_store_graph_stats`가 깨진 쿼리를 `0` 노드로 오보하는 대신 미초기화(`empty`), 정상(`ok`), SQL 실패(`unavailable`) 상태를 명시적으로 구분합니다.
- **Section 16 종단간 제품 검증 스위트 (`rust/lattice-host/tests/roadmap_section16_suite.rs`):**
  - **16.1** 분리형 RAG 정확도(`Recall@5 >= 0.99`, `MRR >= 0.90`), 대체 지식 배제율 `1.0`, 답변 불가 기권율 `1.0`, Gemma/Qwen/Cloud 모델 교체 불변성.
  - **16.2** 이중 시간축 `Jan → Mar → May` 시점·현재 상태 정책 변화 및 한/영 교차 언어(`Project Alpha`) 개체 통합.
  - **16.3** `local_only` 클라우드 경계 외부 전송 0건, `cloud_allowed`에서 민감/`local_only`/`.env.production` 배제, 검토 스테이징 기반 클라우드 KG 확장(`auto_commit == false`).
  - **16.4** Python 워커 도달 불가 시 장애 복구(빠른 실패 + 네이티브 SQLite 검색 `200`), 인덱싱 중간 크래시 복구(중복 0건, 누락 파일 0건), 완료 단계 재실행 없이 전체 재시작을 넘는 `WaitingApproval` 워크플로 재개.

## 배포 아티팩트

- `dist/ltcai-13.2.0-py3-none-any.whl`
- `dist/ltcai-13.2.0.tar.gz`
- `ltcai-13.2.0.tgz`
- `dist/ltcai-13.2.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_13.2.0_aarch64.dmg`
