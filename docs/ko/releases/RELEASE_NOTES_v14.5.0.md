[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../releases/RELEASE_NOTES_v14.5.0.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./RELEASE_NOTES_v14.5.0.md)

# Lattice AI v14.5.0 — Living Automations (2026-11-27)

`v14.5.0`은 Lattice AI의 거버넌스, 검색, 이식성 기능을 실제로 동작하는 일상 워크플로로 끌어올립니다. 예약 및 브레인 이벤트 기반 자동화가 실제로 실행되어 검토 센터에 초안을 올리고, "최신" 검색은 신뢰 등급과 사용자 출처 피드백을 반영하며, 워크스페이스 브레인 전체를 깔끔한 Markdown/Obsidian 볼트로 내보낼 수 있습니다.

## 주요 변경 사항

### 1. 살아있는 자동화 (`lattice-platform`, `lattice-host`, `lattice-core`)
- **실제 트리거 엔진 (`workflow_designer/triggers/`)**: 활성화된 `interval` 및 `brain_event` 자동화를 `WorkspaceOsStore`에 영속화하고 다음 실행 시각을 계산하며, `lattice-host`의 백그라운드 스케줄러 루프에서 실제로 실행합니다.
- **수집 직후 `brain_event` 디스패치**: `GraphWriter`가 중복이 아닌 `ingest_content` / `ingest_file` 쓰기 완료 후 `IngestListener`를 호출해 같은 워크스페이스의 `brain_event` 자동화(예: **Follow-up Radar**)를 논블로킹으로 실행합니다.
- **초안 우선 안전성(Drafts-Before-Automation)**: 무인 실행은 항상 검토 큐에 `source: "trigger"` 초안만 생성하며, 수동 개입이 필요한 단계가 포함된 워크플로는 `requires_manual_run` 사유로 안전하게 건너뜁니다.
- **정직한 `GET /workflows/api/triggers`**: 워크스페이스별 실제 `armed` 목록(`kind`, `next_run_at`, `last_run_at`, `last_status`, `blocked`, `skip_reason`)을 반환합니다.

### 2. 신뢰 등급 기반 "최신" 랭킹 및 인용 피드백 (`lattice-core`, `lattice-retrieval`)
- **신뢰 등급 (`temporal/trust.rs`)**: 프론트매터(`graph_write/frontmatter.rs`), 사용자 신뢰 메타데이터, 수명주기 검증, 확정된 교정, 수집 출처를 바탕으로 노드를 `Verified` / `Normal` / `Draft` 등급으로 분류합니다(구형 SQLite DB 스키마 호환 포함).
- **인용 피드백 루프 (`correction_signals.rs`, `temporal/signals.rs`)**: `knowledge_corrections`의 활성 `wrong_source`(`0.25×`) 및 `useful`(`1.1×`) 판정을 검색 점수와 최신순 정렬에 반영합니다.
- **"좋은 출처가 아님" UI (`SourceFeedback.tsx`, `AnswerProof.tsx`)**: 답변 출처와 검색 결과에서 원클릭으로 출처 하향 및 되돌리기를 지원합니다.

### 3. Markdown / Obsidian 볼트 내보내기 (`lattice-platform`, `frontend/`)
- **신규 `POST /api/portability/vault` 엔드포인트**: 워크스페이스 지식을 YAML 프론트매터, `[[위키링크]]`, 구버전 `_history/` 분리, `_export.json` 매니페스트, 시크릿 마스킹, 경로 이탈 방지, 타 워크스페이스 및 공유 차단 항목 제외를 적용한 ZIP 볼트로 내보냅니다.
- **설정 UI (`VaultExportCard.tsx`)**: 시스템 설정에서 원클릭 볼트 다운로드를 제공하며 OpenAPI 프래그먼트와 TypeScript 타입(`431` 경로 / `474` 오퍼레이션 / `181` 스키마)을 동기화했습니다.

### 4. 가져오기 소스 UI, 대화 라이브러리 및 실행·검토 개선 (`frontend/`)
- **가져오기 소스 (`ImportPanel.tsx`, `api/imports.ts`)**: Obsidian, Notion, Git, Mail 통합 가져오기 UI를 캡처 화면에 추가했습니다.
- **대화 라이브러리 (`ConversationLibrary.tsx`, `conversationExport.ts`)**: 전체 대화 검색, 전체 목록 탐색, 안전한 파일명의 Markdown 내보내기를 지원합니다.
- **실행·검토·검색 개선**: 워크플로 실행 이력 및 리플레이 뷰어(`WorkflowRunHistory.tsx`), 내보내기 다운로드 및 중지 버튼 결함 수정, 검토 일괄 승인/기각 바(`BulkReviewBar.tsx`), 날짜 범위 및 구버전 포함 검색 필터(`SearchFilters.tsx`, `SearchResults.tsx`)를 추가했습니다.

## 배포 아티팩트

- `dist/ltcai-14.5.0-py3-none-any.whl`
- `dist/ltcai-14.5.0.tar.gz`
- `ltcai-14.5.0.tgz`
- `dist/ltcai-14.5.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_14.5.0_aarch64.dmg`
