[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../releases/RELEASE_NOTES_v13.8.0.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./RELEASE_NOTES_v13.8.0.md)

# Lattice AI v13.8.0 — Living Synthesis (디지털 브레인 플랫폼 고도화)

릴리스 일자: **2026-11-24**

## 주요 변경 사항

### P0 — 신뢰성, 진정성 있는 RAG 평가, 데이터 무결성, 엔티티 해석 및 보안 강화
- **실제 모델 vs 결정론적 픽스처 분리 RAG 평가 프레임워크 (`rust/lattice-retrieval/src/rag_eval_grounded.rs`, `rag_eval_dataset.rs`, `datasets/rag_eval_v1.json`, `scripts/run_real_model_rag_eval.py`):**
  - `FixtureDeterministic`, `RealModelLocal`, `RealModelCloud`, `RetrievalOnly`, `EndToEnd` 실행 모드와 `Executed` / `FixtureOnly` / `Skipped` / `Unavailable` 상태 표기를 명확히 분리하고 전체 실행 메타데이터(`dataset_version`, `prompt_version`, `embedding_model`, `provider`, `execution_location`)를 기록합니다.
  - 9개 필수 평가 카테고리를 포괄하는 버전 관리형 **320개 질의 RAG 평가 데이터셋**(`datasets/rag_eval_v1.json`, `rag-eval-v1.0.0`)을 추가했으며 워크스페이스 경계 누출 `0건`, 대체(superseded) 문서 배제율 `100%`를 검증했습니다.
- **지식 그래프 데이터 무결성 검사 및 안전 복구 (`rust/lattice-core/src/integrity.rs`, `rust/lattice-core/tests/integrity_fault_injection.rs`, `GET /api/brain/integrity`, `POST /api/brain/integrity/repair`):**
  - SQLite `integrity_check`/`foreign_key_check`, 댕글링 엣지(`edges`/`edges_v2`/`edge_occurrences`), 고아 청크·벡터·출처, 출처 누락, 교차 워크스페이스 참조 누출, 중복 자연키, 삭제된 로컬 파일 경로, 보조 인덱스(`nodes_v2`/`node_fts`/`vector_embeddings`) 드리프트 및 DAG 순환 참조를 통합 검사합니다.
  - 드라이런 영향 범위(blast-radius) 미리보기, 복구 전 자동 `VACUUM INTO` 백업 스냅샷, 안전 자동 복구 vs 사용자 승인 복구 분리 및 장애 주입(fault-injection) 테스트를 제공합니다.
- **엔티티 해석 및 안전한 중복 제거 (`rust/lattice-core/src/entity_resolution.rs`, `entity_resolution_ext.rs`, `/api/brain/entities/{candidates,merge,rollback}`):**
  - 타입 범위 정확 일치로 수정해 서로 다른 타입의 동음이의어(`Person:"Apple"` vs `Organization:"Apple"`)가 자동 병합되는 버그를 차단했습니다.
  - 퍼지 매칭, 다중 신호 점수화, `AutoMerge` / `ReviewCandidate` / `RejectedHomonym` 등급 분리, 영속 `entity_aliases`·`entity_merge_audit`·`entity_do_not_merge` 테이블, 교차 워크스페이스 병합 금지, 엣지 참조 마이그레이션 및 병합 단위 선택적 롤백(`rollback_entity_merge`)을 구현했습니다.
- **보안, TOCTOU/심볼릭 링크 샌드박스 및 클라우드 이그레스 PII/비밀 마스킹 (`rust/lattice-agent/src/tools/safe_io.rs`, `content/untrusted.rs`, `rust/lattice-core/src/redact.rs`, `rust/lattice-chat/src/pipeline/answers.rs`):**
  - OS 수준 디렉터리 FD 상대 경로 I/O(`openat` + `O_NOFOLLOW` + `O_CLOEXEC` + `fstat` 루트 `(st_dev, st_ino)` 검증)를 파일 쓰기/편집/스캐폴드/문서 생성/노트 저장 도구 전반에 적용하고 실제 심볼릭 링크·TOCTOU 스왑 테스트(`security_sandbox_toctou.rs`)를 추가했습니다.
  - 컴파일된 `OnceLock` PII+비밀 마스킹 엔진을 `lattice-core::redact`로 통합해 클라우드 발신 사용자 질문(`&question`)과 KG 컨텍스트(`compact_text`), `LoopTrace`, `/host/diagnostics` 모두에 적용했습니다.
  - 워크스페이스 범위 `DurableWorkflowStore::load_scoped`, `ingestion_jobs.workspace_id`, `fs.rs`/`watch.rs` 심볼릭 링크 탈출 차단, `local_files_api/local.rs` 민감 경로 읽기 차단, 단축 플래그 경로 검사(`rg -f/etc/passwd`), 한/영 비신뢰 콘텐츠 프롬프트 인젝션 프레이밍을 구현했습니다.

### P1 — 시간 축 지식, 관계 지능, 하이브리드 검색, 섀도우 임베딩, 에이전트 신뢰성, 멀티에이전트 및 지식 학습 루프
- **시간 축 지식 및 관계 지능 (`rust/lattice-core/src/temporal.rs`, `graph_write/projection_v2.rs`, `graph_write/documents.rs`):** `valid_from`/`valid_to`/`observed_at`/`source_timestamp`/`updated_at`을 정렬하고 `nodes_v2` 동기화, `edges_v2` 근거/신뢰도/상태 기록 및 `GraphWriter::terminate_edge`, `GET /api/brain/entities/{id}/timeline`, `GET /api/brain/relationships/evolution`을 추가했습니다.
- **하이브리드 검색 (`rust/lattice-retrieval/src/bm25/`):** 허브 팬아웃·엣지 예산 제한, 근접 중복 제거, 결정론적 구문 재순위화(reranker), 통합 `score_explanation` 및 구조화된 `fallbacks` 리포트를 추가했습니다.
- **임베딩 모델 레지스트리 및 섀도우 인덱스 (`rust/lattice-core/src/embedding_registry.rs`, `latticeai/services/embedding_recommendation.py`):** Python↔Rust `embedding_index_contract` 스키마 불일치를 수정하고 영속 임베딩 레지스트리, 벡터 공간 호환성 가드, 재개 가능한 섀도우 재색인(`vector_embeddings_shadow`), 품질 게이트 기반 원자적 컷오버 및 즉시 롤백을 구현했습니다.
- **에이전트 런타임 신뢰성 및 멀티에이전트 협업 (`rust/lattice-agent/src/kernel/workflow.rs`, `multi_agent.rs`, `trace.rs`):** 재시도 가능/불가 오류 분류(`StepErrorKind`), `PartialSuccess`, `ImpactPreview`, 마스킹 트레이스 내보내기 및 최소 권한 5역할(`Supervisor`, `Planner`, `Researcher`, `Executor`, `Reviewer`)·격리 스크래치패드·공유 블랙보드·충돌 에스컬레이션·예산·사이클 방지를 갖춘 `MultiAgentSession`을 구현했습니다.
- **지식 학습 루프 (`rust/lattice-core/src/learning_loop.rs`, `/api/brain/corrections*`):** 엔티티 링크·사실·관계·인용 사용자 교정(`pending`/`confirmed`/`rejected`), 전후 스냅샷 감사 이력, 선택적 FTS5 재색인, 되돌리기 및 외부 모델 학습 업로드 영구 차단(`external_training_upload_allowed = 0`)을 구현했습니다.

### P2 & P3 — 자동 파일 인제스트, 모델 출처 UX, 데스크톱 워치독, 고급 KG 및 조직 거버넌스
- **자동 파일 인제스트 (`rust/lattice-ingest/src/local_files_api/{watch_bridge.rs,watch_deliver.rs}`):** OS 감시 `WatchBatch`를 직접 소비해 파일 이동(`moved_from_uri`) 증분 연동 및 감시 일시중지(`paused`)를 지원합니다.
- **모델 출처 계층 UX (`latticeai/models/model_providers.py`, `frontend/src/features/library/{libraryDomain.ts,ModelsPanel.tsx}`):** 12개 모델 패밀리의 국가·제조사·실행 위치 메타데이터를 확장하고 모델 라이브러리 UI에 `국가 → 제조사 → 로컬 PC / 인터넷 실행` 계층 배지를 표시합니다.
- **데스크톱 안정성 (`rust/lattice-host/src/supervisor/monitor.rs`, `gateway/routes.rs`):** 워커 시작 타임아웃 강제 종료, 연속 헬스 실패 워치독 자동 재시작 및 PII/비밀 마스킹 기반 `GET /host/diagnostics`를 추가했습니다.
- **고급 KG·멀티모달·조직 거버넌스:** `GET /api/brain/clusters`(라벨 전파 토픽 클러스터링), `GET /api/brain/gaps`(지식 공백 탐지), `Modality::Video` 계약, `prune_audit_events_before` 감사 보존 정책 실행 및 `orgs::remove_member` 오프보딩 세션 취소 기록을 추가했습니다.

## 정확한 버전별 배포 산출물
- `dist/ltcai-13.8.0-py3-none-any.whl`
- `dist/ltcai-13.8.0.tar.gz`
- `ltcai-13.8.0.tgz`
- `dist/ltcai-13.8.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_13.8.0_aarch64.dmg`
