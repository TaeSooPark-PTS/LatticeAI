[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../releases/RELEASE_NOTES_v12.8.0.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./RELEASE_NOTES_v12.8.0.md)

# Lattice AI 12.8.0 — Trusted Brain

> **상태: historical** — 해당 시점의 보존된 릴리스 노트입니다.

2026-11-16

**12.8.0 Trusted Brain**은 Rust 호스트와 Python 워커 전반에 걸쳐 종단간 신뢰성, 보안 격리, 검색 정밀도, 브레인 이식성, 프론트엔드 유지보수성, 릴리스 자동화 업그레이드를 완료했습니다.

## 주요 반영 사항

- **통합 임베딩 계약, 세분화된 수집 상태 및 지식 라이프사이클:**
  - `rust/lattice-core/src/embedding_contract.rs`, `rust/lattice-core/src/graph_write/lifecycle.rs`, `latticeai/core/embedding_providers/`, `latticeai/worker_app.py`: 단일 표준 `EmbeddingIndexContract`(`provider_id`, `model_id`, `dim`, `normalize`, `dtype`, `version`)와 명시적 불일치 감지, 통합 `GraphWriter` 연쇄 삭제, 세분화된 `IngestStatus` 진행 보고, `KnowledgeLifecycleState`(`active`, `superseded`, `archived`, `deleted`) 출처 추적을 적용했습니다.
- **SQLite `LIMIT` 이전 검색 스코핑, 벡터 정렬 및 바운드 사이드카 캐싱:**
  - `rust/lattice-retrieval/src/bm25/`, `vector_alignment.rs`, `rag_eval.rs`: 작업공간 스코프와 활성 라이프사이클 필터를 SQL `LIMIT` 이전 단계로 밀어 넣고, `vector_search_aligned`가 활성 계약 대비 벡터 차원을 검증하며, 쿼리당 서브워드 재토큰화를 `SidecarCachePool`(`RwLock<Arc<Bm25SidecarIndex>>`, `DEFAULT_MAX_CACHE_BYTES` 상한) 내 캐시된 `Bm25SidecarIndex` 조회로 대체하고 검색 평가 게이트를 추가했습니다.
- **Fail-Closed 멀티유저·작업공간·테넌트 게이트웨이 격리:**
  - `rust/lattice-agent/src/tools/sandbox.rs`, `rust/lattice-host/src/gateway/{isolation.rs,agent_bind.rs}`, `rust/lattice-auth/src/enterprise.rs`, `rust/lattice-host/tests/agent_isolation_e2e.rs`: `IsolatedRootSpec`과 `resolve_agent_scope`를 통해 서버 측에서 사용자·테넌트·작업공간별 격리 `Workspace` 루트를 도출하고, `/agent`, `/tools/*`, `/mcp/*`, `/skills/*`, `/cu/*`, `/api/automation/*`, `/api/browser/*` 전반에서 크로스 작업공간/테넌트 스푸핑과 경로·심볼릭 링크 탈출을 보안 감사 이벤트와 함께 거부합니다.
- **암호화 `.latticebrain` 아카이브 이식성 및 정직한 PostgreSQL 상태 보고:**
  - `rust/lattice-platform/src/knowledge/portability/{archive.rs,crypto.rs,postgres.rs}`, `rust/lattice-platform/tests/portability_archive.rs`: 심볼릭 링크 및 경로 순회 차단을 포함한 AEAD 암호화 `.latticebrain` 생성/검사/검증/가져오기/복원 워크플로, 수신자 X25519 공유 내보내기/수락 흐름, 복원 전 자동 백업, PostgreSQL 마이그레이션·Docker 오케스트레이션의 정직한 `planned` 보고를 구현했습니다.
- **프론트엔드 기능 모듈화 및 저장소 정리:**
  - `Library.tsx`, `System.tsx`, `Capture.tsx`를 `frontend/src/features/` 하위의 집중 모듈로 리팩터링하면서도 108개 파일 100% Vitest 커버리지와 초기 JS 번들 예산을 유지했고, 오래된 `scratchpad/` 임시 산출물을 제거했습니다.
- **CI/CD 및 macOS Apple Silicon DMG 릴리스 자동화:**
  - `.github/workflows/ci.yml`에 PR 검색/사이드카 자동 게이트를 추가하고, `.github/workflows/release.yml`과 `.github/workflows/publish.yml`에 Apple Silicon Tauri DMG 빌드, 코드 서명/공증 훅, 검증, 통합 `SHA256SUMS` 게시 파이프라인을 구축했습니다.

## 배포 아티팩트

- `dist/ltcai-12.8.0-py3-none-any.whl`
- `dist/ltcai-12.8.0.tar.gz`
- `ltcai-12.8.0.tgz`
- `dist/ltcai-12.8.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_12.8.0_aarch64.dmg`
