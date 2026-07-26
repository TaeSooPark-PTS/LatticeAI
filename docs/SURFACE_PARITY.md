# Lattice AI — Surface Parity Matrix

> **Status: reference**
> 도입: v9.9.4 (2026-07-25, 리뷰 Wave 1.4 "표면 패리티 체크리스트").
> 갱신: v9.9.7 — 표에 남아 있던 ✖ 갭을 **전부** 해소. Browser 확장도 캡처 전용 경계를 넘어 회상/승인 가시성을 갖췄다.
> 목적: 각 표면(서피스)이 Brain 계약의 어디까지를 제공하는지 **정직하게** 기록한다.
> "앱마다 다른 Lattice"를 방지하는 기준표이며, 기능을 과장하지 않는다.

## 표면 정의

| 표면 | 진입점 | 성격 |
| --- | --- | --- |
| Web `/app` | FastAPI sidecar가 서빙하는 SPA (`static/app`) | 1차 표면 — 모든 기능의 기준 |
| Desktop | Tauri 셸 (`src-tauri/`) | 동일 SPA를 감싸는 셸 — 웹과 구조적 동일 |
| VS Code | `vscode-extension/` (`ltcai.*` 커맨드) | 에디터 보조 표면 |
| Browser | `browser-extension/` | 캡처 + 회상 + 승인 가시성 (v9.9.7) |
| Telegram | 봇 어댑터 | 대화 전용 표면 |

## 네 가지 루프 순간 × 표면

✅ 제공 · ◐ 부분 제공 · — 미제공(의도) · ✖ 갭(백로그)

| 루프 순간 | Web `/app` | Desktop | VS Code | Browser | Telegram |
| --- | --- | --- | --- | --- | --- |
| 첫 저장 (Capture) | ✅ DnD·폴더·웹 수집, 품질 경고 | ✅ (웹과 동일) | ◐ `sendToLattice` 선택 영역 전송 | ✅ 페이지 캡처 (단일 목적) | ◐ 메시지 수집 |
| 첫 회상 (Recall) | ✅ 하이브리드 검색 + grounding 배지 + 출처→청크 | ✅ (동일) | ✅ `askCurrentFile`/`askBrain` — 동일 `/chat` grounding 배지 | ✅ 팝업 질문 + 동일 grounding 배지 (v9.9.7) | ✅ 답변 + grounding 배지 (v9.9.7) |
| 첫 산출물 (Artifact) | ✅ artifacts[] 카드·미리보기·Brain 기억 칩 | ✅ (동일) | ◐ `createFile` + `runAgent`/`runAgentLive` 스텝·파일 요약, 카드 UI 없음 | — (의도: 브라우저에서 파일 쓰기 없음) | ◐ 파일 전송만 |
| 첫 보호 (Approval) | ✅ 승인 카드 + TTL 카운트다운 + 재시작 생존 | ✅ (동일) | ✅ List/Approve/Reject 커맨드 + 토큰 캐시 | ✅ 대기 건수 표시 (승인은 웹에서, v9.9.7) | ✅ 인라인 Done/Cancel + run_id/token 재개 |

## 기능 상세 × 표면

| 기능 | Web `/app` | Desktop | VS Code | Browser | Telegram |
| --- | --- | --- | --- | --- | --- |
| 에이전트 스텝 타임라인 (v9.9.4) | ✅ 라이브 SSE | ✅ | ✅ `runAgentLive` — `POST /agent` `stream:true`의 동일 `agent_step` 프레임 (v9.9.7) | — (의도) | — (의도) |
| 실행 결과 평문 설명 (v9.9.6) | ✅ 메시지 하단 노트 | ✅ | ✅ `runAgent` 출력 채널 | — | ✅ 답변 뒤 요약 메시지 |
| 근거 → 행동 원클릭 (v9.9.6) | ✅ 답변 근거 카드 | ✅ | ✅ `ltcai.evidenceActions` (v9.9.7) | — (의도) | — (의도) |
| 지식 정원 4화단 (v9.9.7) | ✅ 홈 패널 | ✅ | — (의도) | — (의도) | — (의도) |
| 폴더별 기억 상태 (v9.9.7) | ✅ Capture 카드 | ✅ | — (의도) | — (의도) | — (의도) |
| 음성 메모 캡처 (v9.9.7) | ✅ `POST /api/capture/voice` | ✅ | — (의도) | — (의도) | — (의도) |
| 승인 재개 (`/agent/resume`) | ✅ | ✅ | ✅ `ltcai.approveAgent` / `rejectAgent` | — | ✅ callback → resume |
| 대기 중 승인 목록 (`GET /agent/approvals`, v9.9.4) | ✅ | ✅ | ✅ `ltcai.listApprovals` | — | — (봇 로컬 pending map) |
| Watch 상태 신호 (v9.9.4) | ✅ 홈 카드 | ✅ | — | — | — |
| Review Center (제안 승인/거절) | ✅ | ✅ | ✅ `ltcai.reviewCenter` (409 충돌 정직 보고) | — (의도: 승인은 웹/에디터/봇에서) | ✅ `/review` + 인라인 승인/거절 (v9.9.7) |
| 데모 코퍼스 First Value Loop | ✅ | ✅ | — | — | — |
| 모델 로드/추천 | ✅ | ✅ | ◐ `loadModel` | — | — |
| 코드 편집 보조 (edit/explain/refactor/tests) | — | — | ✅ | — | — |

## 규칙

1. **API 계약이 기준이다.** 모든 표면은 동일한 sidecar API를 소비한다. 표면별 전용 API를 만들지 않는다.
2. **◐/✖ 는 문서화된 상태로만 존재한다.** 새 기능을 Web에 추가할 때 이 표를 갱신하고, 다른 표면에 제공하지 않을 경우 "—(의도)" 인지 "✖(갭)" 인지 명시한다.
3. **✖ 갭은 릴리스 노트의 Honest Limitations 후보다.** v9.9.7 기준 표에 남은 ✖는 **없다**.
   - 남은 "—"는 전부 **설계 경계**이며, 각 항목에 이유를 함께 적는다. 예: 브라우저 확장은 파일을 쓰지 않으므로 산출물 생성이 없고, 승인 *결정*은 서명된 토큰이 필요한 흐름이라 대기 건수만 보여 주고 승인은 웹/에디터/봇에서 한다.
   - 경계를 새로 만들 때는 "왜 제공하지 않는가"를 이 문서에 남긴다. 이유 없는 "—"는 갭이지 경계가 아니다.
4. 릴리스마다 이 표를 검토한다 (release checklist의 docs 단계).

## VS Code / Telegram 승인 메모 (v9.9.5)

- **토큰은 GET `/agent/approvals`에 다시 내려가지 않는다** (보안). Web은 일시 중지 응답의 토큰을 클라이언트에 보관하고, VS Code도 동일하게 pause 응답의 토큰을 확장 세션 캐시에 둔다. 캐시에 없으면 붙여넣기 또는 웹 UI로 유도한다.
- **Telegram**은 `waiting_approval`(레거시 `human_in_loop`)과 `awaiting_approval`(토큰) 둘 다 인라인 키보드로 처리하며, resume 시 run_id+token을 우선한다.

## VS Code 회상·검토 메모 (v9.9.6)

- **회상 배지**: `askCurrentFile` / `askBrain`은 웹과 동일한 `POST /chat` 응답의 `grounding` 판정을 그대로 읽는다. 판정이 없으면 "근거 있음"으로 승격하지 않고 `unknown`으로 보고한다 (`vscode-extension/surface.ts::groundingBadge`).
- **Review Center**: `ltcai.reviewCenter`는 웹과 같은 `/api/proposals` 표면을 쓴다. 승인은 검토한 내용 그대로 적용하고, 409(스테이징 이후 파일 변경)는 "아무것도 쓰지 않았다"고 정직하게 알린다.
- **스텝 요약**: `ltcai.runAgent`는 실행 후 `steps`/`created_files`/`explanation`을 출력 채널에 요약한다. 라이브 SSE 타임라인은 아직 웹 전용이다 (◐).
- 파싱 계약은 `tests/vscode-extension.test.cjs`가 sidecar 페이로드 모양 그대로 검증한다.

## v9.9.7 — 남은 갭을 닫은 방법

- **VS Code 라이브 스텝**: `POST /agent`에 `stream: true`가 생겼다. 웹이 채팅
  경로로 받던 것과 **같은** `agent_step` 프레임을 그대로 내보내고, 마지막 프레임의
  터미널 페이로드는 JSON 응답과 동일하다 (`tests/unit/test_agent_stream_parity.py`가
  두 경로의 동등성을 고정한다).
- **VS Code 근거→행동**: 회상 응답의 `grounding.cited`를 확장이 기억했다가
  `/api/evidence/actions`로 보낸다. 파일을 만드는 액션은 에이전트로 흘려보내
  실제 산출물이 나오게 하고, 대화 액션은 채팅 패널로 보낸다.
- **Telegram 회상 배지 / Review Center**: 서버가 낸 판정을 그대로 렌더하고,
  `/review`가 `/api/proposals`를 그대로 소비한다. 409는 "아무것도 쓰지 않았다"로
  보고한다.
- **Browser 확장**: 캡처 전용 경계를 넘어 `/chat` 질문과 `agent/approvals` 대기
  건수를 보여 준다. **판정을 로컬에서 계산하지 않는다** — 판정이 없으면
  "근거 확인 불가"이며 절대 "근거 있음"으로 승격하지 않는다.
