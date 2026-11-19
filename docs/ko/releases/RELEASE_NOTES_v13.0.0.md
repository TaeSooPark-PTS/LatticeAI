[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../releases/RELEASE_NOTES_v13.0.0.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./RELEASE_NOTES_v13.0.0.md)

# Lattice AI 13.0.0 — Sovereign Synthesis

> **상태: historical** — 해당 시점의 보존된 릴리스 노트입니다.

2026-11-18

**13.0.0 Sovereign Synthesis**는 `lattice-core`, `lattice-auth`, `lattice-agent`, `lattice-platform`, `lattice-chat` 전반의 강제된 도메인 경계 분리, 구조화된 2-Hop 지식 그래프 추론 경로 및 상충 합성(`reasoning_paths.rs`, `MultiHopSynthesisView.tsx`), 단일 트랜잭션 리뷰 스토어 변경 및 원클릭 가역 폴더 재구성 되돌리기, 재시작을 넘어 유지되는 내구성 대화 문서 세션, 모듈화된 프론트엔드 API 슬라이스(`client.ts`, `streaming.ts`, `transfers.ts`), 통합 Rust 서버 i18n 카탈로그 검증을 제공하는 메이저 아키텍처·지능 업그레이드입니다.

## 주요 반영 사항

- **도메인 경계 분리 강제 및 아웃바운드 컴퓨트 심 (`rust/`):**
  - `OnceLock`으로 컴파일된 표준 비밀 마스킹(`redact_secret_text`, `redact_secrets`, `redact_structure`, `is_secret_key`)을 `lattice_core::redact`로 승격하여, `lattice-platform`과 `lattice-chat`에 중복되어 있던 정규식 엔진과 횡적 `knowledge -> workspaceos::workspace::redact` 참조를 제거했습니다.
  - `lattice_agent::surface`에서 `lattice_agent::seam::worker`를 분리하여 `kernel/`과 `tools/`가 컴퓨트 포트에 하향 의존하고 인바운드 HTTP 전송 계층(`surface/`)을 절대 import하지 않도록 했으며, `lattice-chat`을 표준 `lattice_agent::{kernel,parse,content,tools,seam}` 도메인 그룹으로 마이그레이션했습니다.
  - `lattice_platform::http_reply`(`json_from_ordered`, `message_error`, `now_iso_offset`, `workspace_from_headers`)를 확장해 `modelops/setup`이 `adminops`를 전혀 import하지 않도록 분리했으며, 이를 `rust/lattice-platform/tests/domain_boundaries.rs` 테스트로 영구 강제했습니다.
- **다단계(Multi-Hop) 지식 그래프 추론 경로 및 상충 합성 (`lattice-retrieval` & `Brain.tsx`):**
  - `rust/lattice-retrieval/src/bm25/reasoning_paths.rs`를 추가하고 `graph_rag.rs` + `fusion.rs`(`hybrid_bm25_vector_graph_search`)를 업그레이드해 1-Hop/2-Hop 추론 체인(`seed_id` → `bridge_id` → `target_id`) 추적, 매개 개념(`bridge_concepts`) 발굴, 이웃 상충/대체(`CONTRADICTS`, `CONFLICTS_WITH`, `SUPERSEDES`) 감지, 어휘 BM25F 시드가 존재할 때 노이즈성 벡터 단독 시드 승격 차단을 구현했습니다.
  - 브레인 하이브리드 검색(`Brain.tsx`)에 인터랙티브 **2-Hop 다단계 추론 합성** 모드(`MultiHopSynthesisView.tsx`, 한/영 i18n, 100% Vitest 커버리지)를 추가했습니다.
- **단일 트랜잭션 리뷰 스토어, 가역 재구성 되돌리기 및 내구성 채팅 세션:**
  - `workspace/store.rs`와 `governance/review_queue`에서 `review_items` 쓰기와 `timeline` 추가를 단일 원자적 `WorkspaceOsStore` 트랜잭션(`update_state_with_review_event`, `try_update_state_with_review_event`)으로 통합했습니다.
  - `governance/change_proposals.rs`(`tests/reversible_reorganization.rs`)에 자동 역방향 `folder_reorganization` 되돌리기 스테이징(`is_undo: true`, `undo_proposal_id`)을 추가하고 `ReviewCard.tsx`에 **원클릭 되돌리기** 액션과 롤백 배지를 연결했습니다.
  - `lattice_chat::documents::DocumentSessions::with_data_dir`에 바운드 원자적 디스크 영속화(`chat_document_sessions.json`)를 추가해 서버·데스크톱 재시작 후에도 멀티턴 문서 후속 질의가 유지되도록 했습니다.
- **통합 Rust 서버 i18n, 프론트엔드 클라이언트 분할 및 데스크톱 모듈화:**
  - `lattice_auth::messages`를 표준 `lattice_core::messages`로 통합(`WP-I3` 완료)하고 `scripts/check_server_i18n.mjs`가 Python 워커 라우터 6개와 함께 정렬된 Rust `(id, ko, en)` 카탈로그 146개 전수를 검증하도록 확장했습니다.
  - `frontend/src/api/client.ts`(995줄 → ~605줄)를 `streaming.ts`·`transfers.ts`로 모듈화하고 `src-tauri/src/backend.rs`(`backend_tests.rs`)를 800줄 미만으로 분할하면서도 **Vitest 구문·분기·함수·라인 100% 커버리지**를 유지했습니다.

## 배포 아티팩트

- `dist/ltcai-13.0.0-py3-none-any.whl`
- `dist/ltcai-13.0.0.tar.gz`
- `ltcai-13.0.0.tgz`
- `dist/ltcai-13.0.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_13.0.0_aarch64.dmg`
