[![English](https://img.shields.io/badge/English-475569?style=flat-square)](../../releases/RELEASE_NOTES_v13.9.0.md) [![한국어](https://img.shields.io/badge/한국어-Active-2563eb?style=flat-square)](./RELEASE_NOTES_v13.9.0.md)

# Lattice AI v13.9.0 — Living Synthesis (2026-11-24)

## 주요 변경 사항

### 보안 및 무결성 강화 (PR #69 리뷰 항목 6건 해결)
1. **워크스페이스 범위 무결성 점검 및 복구 (`rust/lattice-core/src/integrity/{mod.rs,repair.rs}`):** `check_graph_integrity_scoped`와 `execute_integrity_repair`(`dry_run: true/false` 모두)를 인가된 워크스페이스 범위로 엄격히 제한하고, 전역·교차 워크스페이스 복구는 명시적 관리자 권한(`require_admin`)을 요구하도록 강화했습니다.
2. **단일 쓰기 풀 경유 강제 (`rust/lattice-core/src/db.rs`, `brain_api/extensions.rs`):** 별도 SQLite 쓰기 연결을 제거하고 모든 무결성 복구·엔티티 병합/롤백·지식 교정·임베딩 레지스트리 쓰기를 공유 `Store::write_conn` 단일 쓰기 파이프라인으로 통합했습니다.
3. **교차 워크스페이스 대상 ID 검증 (`entity_resolution_ext.rs`, `learning_loop.rs`, `brain_api/extensions.rs`):** 모든 노드 ID, 엣지 ID, 교정 ID, 엔티티 병합 레코드 ID가 인가된 워크스페이스 소속인지 검증하고 교차 워크스페이스 변경 요청을 HTTP `403`으로 차단했습니다.
4. **Windows 핸들 고정 샌드박스 I/O (`rust/lattice-agent/src/tools/safe_io.rs`):** 비-Unix 환경의 단일 리프 검사를 `CreateFileW`(`FILE_FLAG_OPEN_REPARSE_POINT`), `GetFileInformationByHandle`, `GetFinalPathNameByHandleW` 기반 경로 컴포넌트별 핸들 고정 순회로 교체했습니다.
5. **실제 모델 RAG 근거 검증 게이트 (`scripts/run_real_model_rag_eval.py`):** 생성된 주장과 인용을 검색된 근거 패시지와 대조해 미근거 주장 및 위조 인용에 페널티를 부여하는 `score_grounded_answer`를 추가했습니다.
6. **진단 엔드포인트 인증/포스처 게이트 (`rust/lattice-host/src/gateway/mod.rs`):** `GET /host/diagnostics`를 `require_open_posture` 보호 대상 `native` 라우터 내부로 이동했습니다.

### OpenAPI 스키마 통합, 인터랙티브 Brain UI 및 야간 RAG 평가 워크플로
- 16개 디지털 브레인 확장 엔드포인트를 `brain_api::MOUNTED`와 `rust/fixtures/openapi/memory_brain.json`에 통합하고 `frontend/openapi.json` 및 `frontend/src/api/openapi.ts`를 재생성했습니다.
- 5개 전용 React 브레인 거버넌스 패널(`IntegrityRepairPanel`, `EntityMergePanel`, `KnowledgeCorrectionsPanel`, `EmbeddingRegistryPanel`, `ClustersGapsPanel`)과 Vitest 테스트를 추가했습니다.
- `.github/workflows/nightly-rag-eval.yml` 야간 실제 모델 RAG 평가 워크플로를 추가했습니다.
