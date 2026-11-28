[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../releases/RELEASE_NOTES_v14.7.0.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./RELEASE_NOTES_v14.7.0.md)

# Lattice AI v14.7.0 — Private Scope, Verified Index (2026-11-28)

`v14.7.0`은 공유 워크스페이스 안의 소유자 전용 비공개 문서를 도입하고, `datasets/rag_eval_v1.json` 실측 검증 후 `LATTICEAI_VECTOR_INDEX` 기본값을 `hnsw`로 전환하며, Hugging Face 리비전 고정 및 SHA-256 무결성 검증, 소형 모델 다중 근거 합성 품질 향상, 선택적 자기모델 LLM 정제기, Brain Network 공유 UI를 제공합니다.

## 주요 변경

### 1. 워크스페이스 내 비공개 문서 지원
- `lattice-core`(`viewer_scope.rs`, `graph_write/`), `lattice-retrieval`(`bm25/scoring.rs`, `vector_alignment.rs`, `knowledge_graph_api/`), `lattice-ingest`(`/upload/document` 멀티파트 필드 및 `x-lattice-visibility` 헤더), `lattice-platform`(`share_bundle.rs`, `security_dashboard/`) 전반에 문서별 `visibility`(`workspace` 기본값 또는 `private`)를 적용했습니다.
- 비공개 문서와 노트는 소유자 범위 ID를 사용해 두 사용자가 동일 내용을 올려도 교차 중복 제거되지 않으며, 검색·벡터·그래프 탐색·문서 목록·내보내기·관리자 파일 조회에서 다른 사용자의 비공개 항목을 차단합니다.
- `BrainIngestionPanel`, `BrainIngestionDock`, `useBrainIngestion`, `uploadDocument`, `ingestNote`에 공개 범위 선택기와 API 전달을 추가했습니다.

### 2. `rag_eval_v1.json` 검증 기반 `LATTICEAI_VECTOR_INDEX=hnsw` 기본 전환
- `rust/lattice-retrieval/tests/hnsw_rag_eval_recall.rs`에서 40개 문서, 320개 질의, 5회 수집·삭제·개정(churn) 라운드로 정확 스캔 대비 `NativeHnswIndex` recall@10을 검증했습니다 (`baseline=1.0000`, 5라운드 `[0.9990625, 0.9984375, 0.9978125, 0.99625, 0.9965625]`, 모두 `0.90` 하한 초과).
- `lattice-retrieval`, `lattice-jobs`, `lattice-platform` 기능 메타데이터, `lattice-core` 한/영 설명의 기본값을 `hnsw`로 전환했습니다.

### 3. Hugging Face 리비전 고정 완료 및 SHA-256 무결성 검증
- `latticeai/core/hf_revisions.py`, `latticeai/models/router/local_models.py`, `latticeai/core/embedding_providers/{autodetect,text}.py`, `latticeai/services/model_runtime/download.py` 전반에 리비전 고정 및 SHA-256 검증을 완결했습니다.
- 검증된 Hub 커밋 SHA 고정, LFS SHA-256 검증 및 손상 파일 삭제, `PINNED_HF_SHA256` / `LATTICEAI_HF_SHA256`, `LATTICEAI_HF_REQUIRE_PINNED`, `LATTICEAI_HF_ALLOW_UNPINNED`, `LATTICEAI_HF_REQUIRE_SHA256` 제어를 지원합니다.

### 4. 소형 모델 합성 품질 개선 및 선택적 Self-Model LLM Refiner
- **소형 모델 합성 (`rust/lattice-agent/src/kernel/agentloop/synthesis.rs`):** `decomposed` 프로파일용 번호 매긴 `[S1]`..`[Sn]` 근거 스캐폴드를 구성하고, 근거 커버리지 검사·에코 지시문/단답 거부·`execution/mod.rs` 단일 보정 패스·`verification.rs` fail-closed `NEEDS_REVIEW` 라우팅을 모델별 분기 없이 적용했습니다.
- **선택적 Self-Model LLM 정제기 (`rust/lattice-retrieval/src/memory_api/self_model_refiner.rs`):** `LATTICEAI_SELF_MODEL_LLM_REFINER=1` 옵트인 시 결정론적 구문 추출을 보완해 비정형 사용자 프로필 사실을 제안하며, 모든 결과는 검토 센터 승인 후에만 반영됩니다.
- **회귀 테스트:** `rust/lattice-retrieval/src/brain_api/desk_tests.rs`를 추가해 `desk::open_keys_in`의 `snoozed` 중복 억제 동작을 고정했습니다.

### 5. Brain Network 공유 UI
- `frontend/src/api/brainNetwork.ts`와 `BrainNetworkPanel.tsx`를 `BrainGovernanceHub`에 추가해 `LATTICEAI_BRAIN_NETWORK=1` 상태, Ed25519 장치 지문·공개 키 복사, 피어 페어링·해제·푸시, 서명 서브그래프 내보내기 및 검토 센터 스테이징 가져오기를 제공합니다.

## 검증 결과
- `rag_eval_v1` HNSW recall@10: 기준선 `1.0000`, 5회 churn 라운드 `[0.9990625, 0.9984375, 0.9978125, 0.99625, 0.9965625]`.
- Rust 린트·테스트(`cargo fmt`, `clippy -D warnings`, `cargo-nextest`), Vitest 100% 커버리지(`134`개 파일, `2,055`개 테스트), Python pytest + 커버리지(`>= 90%`), TypeScript `typecheck`, OpenAPI 검증, 통합·CLI·확장 테스트, Playwright 시각 스모크 및 패키지 빌드를 모두 검증했습니다.

## 빌드 산출물
- `dist/ltcai-14.7.0-py3-none-any.whl`
- `dist/ltcai-14.7.0.tar.gz`
- `ltcai-14.7.0.tgz`
- `dist/ltcai-14.7.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_14.7.0_aarch64.dmg`
