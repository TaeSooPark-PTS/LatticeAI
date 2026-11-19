[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../releases/RELEASE_NOTES_v13.3.1.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./RELEASE_NOTES_v13.3.1.md)

# Lattice AI 13.3.1 — Living Synthesis

> **상태: historical** — v13.3.1 보존된 릴리스 노트입니다.

2026-11-19

**13.3.1 Living Synthesis**는 v13.3.0의 잔여 후속 아키텍처·UX 항목을 완료했습니다. 증분 문서 동기화를 락 프리 2단계 비동기 파이프라인(`plan_incremental_sync` + 비동기 `/worker/embed` + `commit_incremental_sync`)으로 분리해 시맨틱 임베딩 모델 환경에서도 감시 폴더 파일 수정 시 전체 파일을 재임베딩하지 않고 미변경 청크 벡터를 재사용하도록 했으며, `AnswerProof.tsx`에 주장 단위 검증 텔레메트리(`claim_verification`)를, `BrainIntelligencePanel.tsx`에 개체 통합 및 시간축 타임라인 요약(`entity_resolution`, `temporal_timeline`)을 노출했고(**100% Vitest 커버리지**), Python·npm·Cargo·Tauri·GitHub Actions 전반에 주간 GitHub Dependabot 자동 감시를 활성화했습니다.

## 주요 반영 사항

- **2단계 비동기 시맨틱 증분 청크 동기화 (`rust/lattice-ingest/src/{incremental_sync.rs,local_files_api/watch_deliver.rs,local_files_api/watch_bridge.rs}`):**
  - `incremental_sync`를 `plan_incremental_sync`(캐시된 청크 해시와 미캐시 청크 해시를 판별하는 블로킹 SQLite 읽기)와 `commit_incremental_sync`(원자적 `GraphWriter` 커밋 및 대체 청크 은퇴)로 분리하고 `sync_document_incremental`을 깔끔한 래퍼로 유지했습니다.
  - `local_files_api/watch_deliver.rs`(`try_incremental_sync_async`)를 추출해 로컬 해시 및 시맨틱 `EmbeddingIndexContract` **모두**에서 감시 파일 수정 시 `Handle::block_on` 교착 위험 없이 `plan_incremental_sync` → 비동기 `POST /worker/embed`(`post_idempotent_compute`) → `commit_incremental_sync` 순서로 실행되도록 했습니다.
  - `rust/lattice-ingest/tests/vault_watch_bridge.rs`에 시맨틱 임베더 계약 하에서 미변경 청크가 재사용됨을 검증하는 종단간 통합 테스트 `semantic_watched_edit_reembeds_only_changed_chunks_via_two_phase_sync`를 추가했습니다.
- **`AnswerProof.tsx` 주장 검증 UI (`frontend/src/features/brain/{AnswerProof.tsx,BrainConversation.tsx,brainData.ts,types.ts}`, `frontend/src/i18n/brain/evidence.ts`):**
  - `parseGrounding` 및 `MessageGrounding`에 `MessageClaimVerification`(`claimSupportRatio`, `citationPrecision`, `citationRecall`, `supersededCitationLeaks`, `numericMismatches`, `polarityMismatches`, `shouldAbstain`, `abstentionReason`)을 확장했습니다.
  - `AnswerProofCard` 내부에 주장 지지율·인용 정밀도 백분율과 수치 불일치·극성 불일치·대체 인용 누수·기권 권고 경고 배지를 표시하는 한/영(`ko`/`en`) `ClaimVerificationStrip`을 추가하고 `AnswerProof.test.tsx`와 `brainData.test.ts`에서 100% Vitest 커버리지를 달성했습니다.
- **`BrainIntelligencePanel.tsx` 개체 통합 및 시간축 타임라인 카드 (`frontend/src/features/brain/BrainIntelligencePanel.tsx`, `frontend/src/i18n/brain/care.ts`):**
  - 통합 결과 바로 아래에 `entity_resolution`(스캔된 개체 수, 병합 클러스터, 미리보기 후보 / 적용된 `SAME_AS` 엣지)과 `temporal_timeline`(활성·과거·미도달 사실 및 활성 시간축 상충 경고 배지)을 표시하는 한/영(`ko`/`en`) `ConsolidationEpistemicSummary`를 추가하고 `BrainIntelligencePanel.test.tsx`에서 100% Vitest 커버리지를 달성했습니다.
- **자동 공급망 모니터링 (`.github/dependabot.yml`):**
  - 주간 `Dependency Audit` 워크플로와 함께 `pip`(`/`), `npm`(`/` 및 `/vscode-extension`), `cargo`(`/rust` 및 `/src-tauri`), `github-actions`(`/` ) 대상 주간 Dependabot 업데이트 스케줄을 추가했습니다.

## 배포 아티팩트

- `dist/ltcai-13.3.1-py3-none-any.whl`
- `dist/ltcai-13.3.1.tar.gz`
- `ltcai-13.3.1.tgz`
- `dist/ltcai-13.3.1.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_13.3.1_aarch64.dmg`
