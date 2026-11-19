[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../releases/RELEASE_NOTES_v13.2.1.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./RELEASE_NOTES_v13.2.1.md)

# Lattice AI 13.2.1 — Living Synthesis

> **상태: historical** — v13.2.1의 보존된 릴리스 노트입니다.

2026-11-19

**13.2.1 Living Synthesis**는 Rust 전체 워크스페이스 크레이트(`lattice-auth`, `lattice-core`, `lattice-ingest`, `lattice-retrieval`, `lattice-agent`, `lattice-platform`, `lattice-host`), Python 워커 및 모델 역량 레지스트리(`latticeai/`), TypeScript/React 스튜디오(`frontend/src/`) 전반에 걸친 저장소 전역 구조 리팩터링과 데드 코드 정리를 완료했으며, Ubuntu `t64` 시각 회귀 CI 미러 페일오버 하드닝을 함께 적용하면서도 모든 공개 HTTP·MCP·OpenAPI·SQLite 계약과 **100% Vitest 커버리지**를 그대로 보존했습니다.

## 주요 반영 사항

- **Rust 워크스페이스 모듈화 (`lattice-auth`, `lattice-core`, `lattice-ingest`, `lattice-retrieval`, `lattice-agent`, `lattice-platform`, `lattice-host`):**
  - `lattice-agent`: `kernel/agentloop/guided/args.rs`(`guided/suggestions.rs`), `kernel/proposals.rs`(`proposals_store.rs`, `proposals_tests.rs`), `parse/action.rs`(`action_salvage.rs`), `tools/catalog.rs`(`catalog_args.rs`, `catalog_tests.rs`), `kernel/agentloop/execution/tests.rs`(`tests_governance.rs`)를 분할했습니다.
  - `lattice-platform` & `lattice-host`: `toolsurface/agents/mod.rs`(`run_handlers.rs`), `modelops/models_catalog/probe.rs`(`probe_sysinfo.rs`, `probe_tests.rs`), `workspaceos/workspace/{handlers_more,handlers_core}.rs`(`handlers_orgs.rs`, `handlers_snapshots.rs`), `knowledge/portability/{share,crypto}.rs`(`share_bundle.rs`, `crypto_tests.rs`), `knowledge/network_boundary.rs`(`network_boundary_stores.rs`), `governance/workflow_designer/executor.rs`(`executor_steps.rs`, `executor_tests.rs`), `tests/mcp_replay.rs`(`tests/mcp_streamable.rs`), `lattice-host/src/gateway/mod.rs`(`gateway/tests.rs`), `lattice-host/tests/gateway_onedoor.rs`(`tests/gateway_source_contracts.rs`)를 분할했습니다.
  - `lattice-auth`, `lattice-core`, `lattice-ingest` & `lattice-retrieval`: `scim.rs`, `graph_write/{projection_v2,vectors_contract}.rs`, `document_structure_parsers.rs`, `local_files_api/{jobs_store,upload_parse}.rs`, `worker_tests.rs`, `bm25/sidecar_subword.rs`, `brain_api/proposal_candidates.rs`, `memory_api/{brief_actions,kg_vector_status,request_parse,self_model_write_tests}.rs`, `rag_eval_grounded_fixtures.rs`, `history_tests.rs`를 추출했습니다.
- **Python 모듈화 (`model_capability_registry.py`, `worker_compute.py`):**
  - `latticeai/services/model_capability_registry.py`를 `model_capability_types.py`, `model_capability_catalog.py`, 재내보내기용 `model_capability_registry.py`로 분할하고, `latticeai/api/worker_compute.py`에서 `latticeai/api/worker_compute_render.py`를 완전한 하위 호환성과 함께 분리했습니다.
- **전 스택 데드 코드 제거 및 프론트엔드 `brainData` 분할 (Vitest 100%):**
  - 미참조 Rust 헬퍼·빌더(`DecodeError::column_of`, `graph_skill_briefs`, `Workspace::shared_workspace`, `ModelsCatalogState::with_compat_profiles`, `SetupState::with_runner`, `PluginsState::with_plugins_dir`, `SupervisorConfig::behind_gateway`)와 미사용 Python 사이드카 델타 헬퍼(`store_vector_count`, `load_store_ids`, `_load_missing_items`)를 제거했습니다.
  - 은퇴한 오비탈 프론트엔드 Brain 레이어 컴포넌트 및 고아 export(`BrainGraphLayer`, `BrainMemoryLayer`, `BrainRelationshipLayer`, `graphLayout`, `ProductCommandCenter`, `GraphStatus`, `RetrievalStatus`, `MemoryStatus`, `canPickFolder`, `INGESTION_STAGE_ORDER`, `DEPTHS`, `MultiHopSynthesis`, `FlagKey`)를 정리하고 `frontend/src/features/brain/brainData.ts`를 `brainDataReaders.ts`와 `brainDataReadiness.ts`로 분할하면서 **구문·분기·함수·라인 100% 커버리지**를 유지했습니다.
- **시각 회귀 CI Ubuntu `t64` 미러 복원력 (`.github/workflows/ci.yml`):**
  - Ubuntu 24.04/26.04 `t64` Chromium 공유 라이브러리 해석을 단일 `apt-cache` 패스로 통합하고 `15s` HTTP 미러 타임아웃, `Acquire::Retries=3`, 자동 `mirrors.ubuntu.com` 폴백을 적용했습니다.

## 배포 아티팩트

- `dist/ltcai-13.2.1-py3-none-any.whl`
- `dist/ltcai-13.2.1.tar.gz`
- `ltcai-13.2.1.tgz`
- `dist/ltcai-13.2.1.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_13.2.1_aarch64.dmg`
