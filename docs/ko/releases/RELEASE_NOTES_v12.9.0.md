[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../releases/RELEASE_NOTES_v12.9.0.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./RELEASE_NOTES_v12.9.0.md)

# Lattice AI 12.9.0 — Semantic Guard

> **상태: historical** — 해당 시점의 보존된 릴리스 노트입니다.

2026-11-17

**12.9.0 Semantic Guard**는 Phase 0–4의 검색 쿼리-계약 정렬, 순수 Rust 네이티브 HNSW 및 스코프 분리 Python HNSW 사이드카, fail-closed 네트워크 경계 및 클라우드 컨텍스트 격리, 데스크톱/CLI/슈퍼바이저/Docker 런타임 가드레일, 실시간 Workspace OS SSE 스트리밍, v2 `.latticebrain` 아카이브 KDF 강화를 완료했습니다.

## 주요 반영 사항

- **계약 정렬 시맨틱 쿼리 임베딩 및 네이티브 HNSW (`Phase 0–1`):**
  - `rust/lattice-retrieval/src/{query_embed.rs,native_hnsw.rs,vector_hnsw.rs,hybrid.rs,service_hybrid.rs,search_api/handlers.rs,memory_api/recall.rs}`, `rust/lattice-retrieval/tests/semantic_query_embed.rs`: `/api/search/hybrid`, `/memory/recall`, `/chat` 전반에서 쿼리 시점 벡터 임베딩을 활성 저장소의 `EmbeddingIndexContract`에 정렬하고, `scripts/bench_retrieval_baseline.py`와 함께 순수 Rust `NativeHnswIndex`를 도입했습니다.
  - `latticeai/core/vector_index/{hnsw.py,sidecar.py}`, `tests/unit/test_vector_index_sidecar.py`: Python HNSW 사이드카를 `(workspace_id, model_id, dim)` 단위로 스코프 분리하고 원자적 메타데이터 검증 및 계약 드리프트 보호를 적용했습니다.
- **Fail-Closed 네트워크 경계 및 클라우드 컨텍스트 격리 (`Phase 2`):**
  - `rust/lattice-platform/src/knowledge/{network_boundary.rs,network_boundary_guard.rs}`, `rust/lattice-chat/src/{boundary.rs,cloud/context.rs}`, `rust/lattice-platform/src/toolsurface/agent_registry.rs`: 채팅, 에이전트 레지스트리, 외부 전송 경로 전반에 서버 측 `local_only` / `cloud_allowed` 네트워크 경계 가드, 명시적 동의 확인, 민감도 필터링, 작업공간 스코프 클라우드 슬라이스 격리를 강제했습니다.
- **데스크톱, CLI, 호스트 슈퍼바이저 및 Docker 런타임 가드레일 (`Phase 3`):**
  - `src-tauri/src/backend.rs`, `bin/ltcai.js`, `rust/lattice-host/src/{main.rs,gateway/mod.rs,supervisor/command.rs}`, `Dockerfile`: Tauri 데스크톱 프로세스 수명주기 및 헬스 프로브, `LTCAI` CLI 호스트/워커 시작 해석, `lattice-host` 슈퍼바이저 및 게이트웨이 요청 가드레일, 멀티스테이지 컨테이너 빌드를 하드닝했습니다.
- **실시간 Workspace OS SSE 및 앱 부트스트랩 모듈화 (`Phase 4`):**
  - `rust/lattice-platform/src/workspaceos/realtime.rs`, `frontend/src/hooks/{useRealtimeStream.ts,useRealtimeStream.test.tsx,useAppBootstrap.ts}`, `frontend/src/App.tsx`, `frontend/src/components/ProductFlow.tsx`: 재연결 백오프와 100% Vitest 커버리지를 갖춘 인증 기반 실시간 Server-Sent Events(`/api/realtime/stream`)를 제공하고, `App.tsx`에서 `useAppBootstrap`을 분리했으며 첫 실행 온보딩 흐름을 다듬었습니다.
- **v2 `.latticebrain` 아카이브 KDF 강화 및 CI 게이트:**
  - `rust/lattice-platform/src/knowledge/portability/{crypto.rs,kdf.rs}`, `.github/workflows/{ci.yml,dependency-audit.yml,e2e-sidecar.yml}`: v1 복호화 하위 호환성을 유지하면서 `.latticebrain` v2 아카이브 키 유도 파라미터를 강화하고, CI·Sidecar E2E·Dependency Audit 게이트를 보강했습니다.

## 배포 아티팩트

- `dist/ltcai-12.9.0-py3-none-any.whl`
- `dist/ltcai-12.9.0.tar.gz`
- `ltcai-12.9.0.tgz`
- `dist/ltcai-12.9.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_12.9.0_aarch64.dmg`
