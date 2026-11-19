[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../releases/RELEASE_NOTES_v12.10.0.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./RELEASE_NOTES_v12.10.0.md)

# Lattice AI 12.10.0 — Grounded Self

> **상태: historical** — 해당 시점의 보존된 릴리스 노트입니다.

2026-11-17

**12.10.0 Grounded Self**는 결정론적 에이전트 트랜스크립트 근거 검증 및 인터랙티브 인용 증빙 배지, 검토 센터 제안 승인을 지원하는 전용 Brain 자기 모델(Self-Model) 패널, `edges`와 `edges_v2` 전반의 지식 그래프 `PART_OF` 연쇄 삭제 동등성, 협력적 모델 다운로드 취소(`499`)와 설치 완료 직후 시맨틱 임베딩 자동 전환·리인덱싱 드레인, 크레이트 내부 `http_reply.rs` 전송 계층 분리, Python 워커 서버 i18n 100% 잠금(`6/6` 라우터), 병렬 `cargo nextest` 테스트 실행, 그리고 `release.yml` → `publish.yml` 자동 CD 체이닝을 도입했습니다.

## 주요 반영 사항

- **결정론적 트랜스크립트 근거 검증 및 인용 증빙 (`rust/lattice-agent`, `AnswerProof.tsx`, `browser-extension`):**
  - `rust/lattice-agent/src/kernel/transcript/grounding.rs`, `transcript.rs`, `agentloop/verification.rs`, `probe.rs`: 모델별 분기 없이도 2B/8GB급 로컬 모델에서 읽지 않은 대상 파일에 대한 답변이나 환각 `path:line` 인용·백틱 식별자를 포착합니다.
  - `frontend/src/features/brain/AnswerProof.tsx`, `CitationVisual.test.tsx`, `browser-extension/popup.js`: 브레인 답변에 인터랙티브 인용/근거 증빙 상세 정보를 노출하고 브라우저 확장 팝업에서 서버 언어 설정을 동기화합니다.
- **Brain 자기 모델 패널 및 지식 그래프 `PART_OF` 연쇄 삭제 (`SelfModelPanel.tsx`, `lattice-retrieval`, `lattice-core`):**
  - `frontend/src/features/brain/{SelfModelPanel.tsx,SelfModelPanel.test.tsx}`, `frontend/src/api/{selfModel.ts,selfModel.test.ts}`, `frontend/src/i18n/brain/selfModel.ts`, `rust/lattice-retrieval/src/memory_api/self_model_write.rs`: 선호, 습관, 의사결정, 성향, 관계를 관리하고 대기 중인 `self_model_fact` 제안을 인라인으로 승인/기각할 수 있는 전용 Self-Model 카드를 100% Vitest 커버리지로 구현했습니다.
  - `rust/lattice-core/tests/graph_write_schema.rs`, `rust/lattice-retrieval/src/{brain_api/desk.rs,native_hnsw.rs,vector.rs,vector_alignment.rs}`, `rust/lattice-retrieval/tests/rag_eval.rs`, `rust/lattice-chat/tests/cloud_lane.rs`: 노드 삭제 시 `edges`(`from_node`/`to_node`)와 `edges_v2`(`source`/`target`) 모두에서 `PART_OF` 엣지 연쇄 삭제를 검증하고, 정렬된 벡터/HNSW 검색 및 RAG 평가와 클라우드 레인 경계 E2E 커버리지를 강화했습니다.
- **협력적 다운로드 취소 및 자동 시맨틱 임베딩 전환 (`latticeai/services`, `lattice-jobs`):**
  - `latticeai/services/model_runtime/download.py`, `model_loading.py`, `embedding_recommendation.py`, `rust/lattice-jobs/src/index_api/engine.rs`, `tests/unit/test_download_cancel_and_embedding_transition.py`: 협력적 `499` 다운로드 취소, 하드웨어 맞춤형 배치 크기 추천(`16`–`64`), 다운로드한 임베딩 모델 설치 완료 직후 자동 시맨틱 임베딩 활성화 및 리인덱싱 드레인을 추가했습니다.
- **플랫폼 전송 계층 분리, 서버 i18n 100% 잠금, 병렬 `cargo nextest` 및 체인 CD:**
  - `rust/lattice-platform/src/http_reply.rs`, `ARCHITECTURE.md`: 공용 JSON 응답, FastAPI 422 검증 엔벨로프, 현지화 메시지 상세, UTC ISO-8601 헬퍼를 `workspaceos::project_sessions`에서 추출해 `knowledge/`와 `toolsurface/`가 도메인 간 횡적 참조를 하지 않도록 분리했습니다.
  - `scripts/check_server_i18n.mjs`, `tests/unit/test_server_messages.py`: Python 워커 라우터 6개 전수 잠금(`0 not yet claimed`)을 달성했습니다.
  - `scripts/run_rust_tests.mjs`, `.github/workflows/{release.yml,publish.yml}`: 커넥터 토큰을 제거한 환경에서 병렬 `cargo nextest` 워크스페이스 스위트를 실행하고, `release.yml`이 GitHub Release를 빌드·게시한 직후 `publish.yml`(`workflow_call`)을 자동 체이닝합니다.

## 배포 아티팩트

- `dist/ltcai-12.10.0-py3-none-any.whl`
- `dist/ltcai-12.10.0.tar.gz`
- `ltcai-12.10.0.tgz`
- `dist/ltcai-12.10.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_12.10.0_aarch64.dmg`
