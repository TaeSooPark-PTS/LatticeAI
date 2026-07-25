# Lattice AI — Surface Parity Matrix

> **Status: reference**
> 도입: v9.9.4 (2026-07-25, 리뷰 Wave 1.4 "표면 패리티 체크리스트").
> 갱신: v9.9.5 — VS Code/Telegram 승인 흐름 갭 해소.
> 목적: 각 표면(서피스)이 Brain 계약의 어디까지를 제공하는지 **정직하게** 기록한다.
> "앱마다 다른 Lattice"를 방지하는 기준표이며, 기능을 과장하지 않는다.

## 표면 정의

| 표면 | 진입점 | 성격 |
| --- | --- | --- |
| Web `/app` | FastAPI sidecar가 서빙하는 SPA (`static/app`) | 1차 표면 — 모든 기능의 기준 |
| Desktop | Tauri 셸 (`src-tauri/`) | 동일 SPA를 감싸는 셸 — 웹과 구조적 동일 |
| VS Code | `vscode-extension/` (`ltcai.*` 커맨드) | 에디터 보조 표면 |
| Browser | `browser-extension/` (Send to Lattice) | 캡처 전용 표면 |
| Telegram | 봇 어댑터 | 대화 전용 표면 |

## 네 가지 루프 순간 × 표면

✅ 제공 · ◐ 부분 제공 · — 미제공(의도) · ✖ 갭(백로그)

| 루프 순간 | Web `/app` | Desktop | VS Code | Browser | Telegram |
| --- | --- | --- | --- | --- | --- |
| 첫 저장 (Capture) | ✅ DnD·폴더·웹 수집, 품질 경고 | ✅ (웹과 동일) | ◐ `sendToLattice` 선택 영역 전송 | ✅ 페이지 캡처 (단일 목적) | ◐ 메시지 수집 |
| 첫 회상 (Recall) | ✅ 하이브리드 검색 + grounding 배지 + 출처→청크 | ✅ (동일) | ◐ `askCurrentFile` — 배지 없음 ✖ | — | ◐ 답변만, 배지 없음 |
| 첫 산출물 (Artifact) | ✅ artifacts[] 카드·미리보기·Brain 기억 칩 | ✅ (동일) | ◐ `createFile` — 검증 파이프라인 경유, 카드 UI 없음 | — | ◐ 파일 전송만 |
| 첫 보호 (Approval) | ✅ 승인 카드 + TTL 카운트다운 + 재시작 생존 | ✅ (동일) | ✅ List/Approve/Reject 커맨드 + 토큰 캐시 | — | ✅ 인라인 Done/Cancel + run_id/token 재개 |

## 기능 상세 × 표면

| 기능 | Web `/app` | Desktop | VS Code | Browser | Telegram |
| --- | --- | --- | --- | --- | --- |
| 에이전트 스텝 타임라인 (v9.9.4) | ✅ 라이브 SSE | ✅ | — | — | — |
| 승인 재개 (`/agent/resume`) | ✅ | ✅ | ✅ `ltcai.approveAgent` / `rejectAgent` | — | ✅ callback → resume |
| 대기 중 승인 목록 (`GET /agent/approvals`, v9.9.4) | ✅ | ✅ | ✅ `ltcai.listApprovals` | — | — (봇 로컬 pending map) |
| Watch 상태 신호 (v9.9.4) | ✅ 홈 카드 | ✅ | — | — | — |
| Review Center (제안 승인/거절) | ✅ | ✅ | ✖ | — | — |
| 데모 코퍼스 First Value Loop | ✅ | ✅ | — | — | — |
| 모델 로드/추천 | ✅ | ✅ | ◐ `loadModel` | — | — |
| 코드 편집 보조 (edit/explain/refactor/tests) | — | — | ✅ | — | — |

## 규칙

1. **API 계약이 기준이다.** 모든 표면은 동일한 sidecar API를 소비한다. 표면별 전용 API를 만들지 않는다.
2. **◐/✖ 는 문서화된 상태로만 존재한다.** 새 기능을 Web에 추가할 때 이 표를 갱신하고, 다른 표면에 제공하지 않을 경우 "—(의도)" 인지 "✖(갭)" 인지 명시한다.
3. **✖ 갭은 릴리스 노트의 Honest Limitations 후보다.** 현재 갭: VS Code 회상 배지, VS Code Review Center.
4. 릴리스마다 이 표를 검토한다 (release checklist의 docs 단계).

## VS Code / Telegram 승인 메모 (v9.9.5)

- **토큰은 GET `/agent/approvals`에 다시 내려가지 않는다** (보안). Web은 일시 중지 응답의 토큰을 클라이언트에 보관하고, VS Code도 동일하게 pause 응답의 토큰을 확장 세션 캐시에 둔다. 캐시에 없으면 붙여넣기 또는 웹 UI로 유도한다.
- **Telegram**은 `waiting_approval`(레거시 `human_in_loop`)과 `awaiting_approval`(토큰) 둘 다 인라인 키보드로 처리하며, resume 시 run_id+token을 우선한다.
