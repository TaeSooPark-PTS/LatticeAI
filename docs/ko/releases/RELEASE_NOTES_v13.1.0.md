[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../releases/RELEASE_NOTES_v13.1.0.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./RELEASE_NOTES_v13.1.0.md)

# Lattice AI 13.1.0 — Living Synthesis

> **상태: historical** — 해당 시점의 보존된 릴리스 노트입니다.

2026-11-18

**13.1.0 Living Synthesis**는 지식 그래프 탐색과 인식론적 상충 해결, 실시간 채팅 + 에이전트 작업공간 실행력, 일상적인 수집·다운로드·검색 및 한/영(`ko`/`en`) 사용성 전반을 끌어올린 대규모 사용자 체감 업그레이드이며, 네이티브 Rust·Python 엔드포인트와 **100% Vitest 커버리지**로 종단간 뒷받침됩니다.

## 주요 반영 사항

- **인터랙티브 지식 그래프 인스펙터, 범위 한정 다단계 추론 합성 및 상충 해결 (`neighborhood_synthesis.rs`, `GraphNodeInspector.tsx`, `ContradictionInspector.tsx`):**
  - `rust/lattice-retrieval/src/neighborhood_synthesis.rs` 및 `search_api/handlers.rs`: `GET /api/graph/node`(`POST /api/graph/node`)가 `include_neighbors=true`와 함께 `include_reasoning=true`를 지원해, 단일 SQLite 조회만으로 범위 한정 이웃에서 결정론적 1-Hop/2-Hop `reasoning_paths`, 매개 `bridge_concepts`, `CONTRADICTS` / `SUPERSEDES` 상충 엣지를 계산합니다.
  - `frontend/src/pages/brain/{GraphNodeInspector.tsx,DigitalBrainExplorer.tsx,graphExplorer.ts}` 및 `frontend/src/features/brain/MultiHopSynthesisView.tsx`: 임의의 노드를 선택하면 실시간 신뢰도/출처, 클릭 가능한 1-Hop 이웃, 홉/상충 필터 필과 신뢰도 막대가 포함된 내장 2-Hop 합성, 원문 발췌 검사(`SourceChunkModal`), 문맥형 "브레인에 질문" 핸드오프, 툴바 상충 전용 필터(`Conflicts`), `/` + `Escape` 키보드 단축키를 제공합니다.
  - `frontend/src/features/brain/{ContradictionInspector.tsx,BrainIntelligencePanel.tsx}`: 브레인 인텔리전스 내에서 감지된 상충 쌍을 검사하고, 지식 그래프의 해당 노드로 즉시 점프하며, 검토 항목 스테이징(`POST /api/brain/contradictions/propose`) 및 원클릭 해결(`keep_old`, `replace`, `keep_both_temporal`, `POST /api/brain/contradictions/resolve`)을 수행합니다.
- **체감형 채팅 및 에이전트 작업공간 경험 (`BrainConversation.tsx`, `InlineStagedProposals.tsx`, `AnswerProof.tsx`, `AgentStepTimeline.tsx`):**
  - 어시스턴트 답변의 원클릭 **Brain에 기억하기(Save to Brain)** 버튼으로 합성된 인사이트를 `POST /api/ingest/note`를 통해 지식 그래프로 즉시 승격합니다.
  - 변경 제안을 스테이징한 에이전트 턴 바로 아래에 **인라인 스테이징 제안(Inline Staged Proposals)** 카드가 표시되어, 대화를 벗어나지 않고도 diff 확인과 원클릭 **승인 / 거절**을 처리할 수 있습니다.
  - `AgentStepTimeline.tsx`는 스트리밍 중 활성 도구 이름을 실시간으로, 완료 시 도구 실행 횟수를 표시하며, `SourceChunkModal`(`AnswerProof.tsx`)은 원클릭 **지식 그래프에서 검사** 이동과 동의 기반 원본 이미지 증거 미리보기를 추가했습니다.
- **실시간 임베딩 드레인, 협력적 다운로드 취소 및 한/영 일상 사용성 개선 (`health.rs`, `BrainSignals.tsx`, `InstallScreen.tsx`, `ModelsPanel.tsx`, `CommandPalette.tsx`, `WorkspaceProfileSwitcher.tsx`):**
  - `rust/lattice-retrieval/src/brain_api/health.rs`가 `/api/brain/vector-freshness`에서 `stale_embedder`를 보존하고, `VectorFreshnessNotice`(`BrainSignals.tsx`)가 `embedded / total` 진행률 바와 `POST /api/index/drain`에 연결된 원클릭 **지금 인덱싱** 버튼을 렌더링합니다.
  - `InstallScreen.tsx`, `ModelsPanel.tsx`, `useLibraryModels.ts`, `recommendationModel.ts`에 사람이 읽기 쉬운 바이트/파일 다운로드 지표(`MB / GB`, `current_file`), 협력적 다운로드 취소(`AbortController` + `DELETE /models/unload/{model_id}`), 영문 누출 0건의 완전한 한/영 오류 현지화를 적용했습니다.
  - `CommandPalette.tsx`(`⌘K`)가 선택된 지식 그래프 노드(`focusedGraphNodeId`)로 바로 포커스하고 빠른 언어 전환 및 그래프 탐색기 액션을 추가했으며, `WorkspaceProfileSwitcher.tsx`는 작업공간 전환 시 활성 쿼리를 즉시 무효화합니다.

## 배포 아티팩트

- `dist/ltcai-13.1.0-py3-none-any.whl`
- `dist/ltcai-13.1.0.tar.gz`
- `ltcai-13.1.0.tgz`
- `dist/ltcai-13.1.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_13.1.0_aarch64.dmg`
