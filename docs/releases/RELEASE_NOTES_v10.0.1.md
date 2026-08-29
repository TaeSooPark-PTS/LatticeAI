# Lattice AI 10.0.1 — One Source of Truth

> **Status: historical** — point-in-time release note.

릴리스일: 2026-07-28

## 한 줄 요약

동작은 하나도 바뀌지 않았습니다. 에이전트 루프 모듈이 **루프만** 갖도록
정리하고, 그 과정에서 **조용히 깨질 수 있었던 결함 하나**를 찾아 고쳤습니다.

## 무엇을 옮겼나

`latticeai/core/agent.py`는 상태 기계와 상태 어휘와 순수 함수 440여 줄을
한 파일(82KB)에 함께 담고 있었습니다. 순수한 부분을 형제 모듈로 옮겼습니다.

```
latticeai/core/
  agent.py         1769 → 1326 lines   상태 기계만
                                       AgentRunContext · AgentDeps · SingleAgentRuntime
  agent_helpers.py      493 lines      순수 함수 (I/O 없음, 결정적)
  agent_state.py         41 lines      AgentState · AGENT_TERMINAL_STATES
```

옮긴 것: `extract_action` · `extract_action_details` · `normalize_plan` ·
`filter_learnings` · `compact_transcript` · `files_written` ·
`artifact_checklist` · `requirement_coverage` · 포맷터 2종 ·
`TranscriptBudget` · `PhaseBudgets`.

## 호출하는 쪽은 한 줄도 바뀌지 않았습니다

`latticeai.core.agent`에서 가져오던 이름은 **전부 그 자리에 그대로** 있고,
**같은 객체**입니다.

```python
from latticeai.core.agent import AgentState, normalize_plan, PhaseBudgets  # 그대로 동작

import latticeai.core.agent as m
from latticeai.core import agent_helpers as h
assert m.normalize_plan is h.normalize_plan   # 복사본이 아니라 동일 객체
```

영향 없이 유지된 곳: `api/chat_agent_http.py` · `api/chat_intents.py` ·
`api/computer_use.py` · `core/run_store.py` · `services/tool_dispatch.py` ·
`scripts/bench_agent_smoke.py` · `scripts/bench_models.py` · 테스트 8개 모듈.

이 계약을 이제 `__all__`이 **명시**합니다. 전에는 암묵적이었습니다.

## 이동 중에 찾은 결함

추출된 헬퍼는 트랜스크립트 단계를 이렇게 비교하고 있었습니다:

```python
if step.get("state") != "EXECUTING":     # 문자열 리터럴
```

원본은 `AgentState.EXECUTING.value`였습니다. 헬퍼가 문자열로 쓴 이유는
**순환 임포트** 때문입니다 — `AgentState`가 `agent.py`에 있고, `agent.py`가
헬퍼를 임포트하므로 헬퍼는 `AgentState`를 가져올 수 없었습니다.

**왜 위험한가:** enum 값의 이름을 바꾸면 이 비교는 조용히 아무것도 매치하지
않게 됩니다. `files_written`과 `artifact_checklist`가 빈 결과를 반환하고,
비평가(critic)는 "만들어진 파일 없음"을 근거로 판단하게 됩니다.
**테스트는 전부 통과합니다.** 어떤 테스트도 이 조합을 잡지 못합니다.

**고친 방법:** `AgentState`를 자기 모듈(`agent_state.py`)로 분리했습니다.
순환이 사라져 양쪽 모두 enum을 참조합니다.

```python
if step.get("state") != AgentState.EXECUTING.value:
```

## 지우기 전에 증명했습니다

원본을 삭제하기 전에, 추출된 18개 심볼 전부를 원본과 **AST 비교**했습니다
(주석·포맷·독스트링 차이는 무시하고 동작만 비교):

| 결과 | 개수 | 비고 |
| --- | --- | --- |
| 완전히 동일 | 16 | |
| 이름만 변경 | 2 | `_format_*` → `format_*`, 본문 동일 |
| 실제 차이 | 2 → 0 | 위의 `"EXECUTING"` 문자열, 고친 뒤 동일 |

## 화면

홈의 보조 컨트롤 줄을 시각적으로 한 단계 낮췄습니다 — 주요 동작 하나가 먼저
읽히도록 투명도·간격·구분선을 조정하고, 640px 아래에서는 손가락에 맞는 간격을
줍니다. 상단바 경계선도 약간 부드럽게 했습니다. **CSS만 바뀌었고** 선택자와
동작은 그대로입니다.

## 검증

| 게이트 | 결과 |
| --- | --- |
| pytest | 1747 passed / 11 skipped |
| `scripts/agent_eval.py` | 23/23 시나리오 (100%) |
| ruff (`latticeai/` `lattice_brain/` `scripts/` `tests/`) | clean |
| 프론트 단위 (Vitest) | 154/154 |
| 시각 테스트 (Playwright) | 18/18 |
| 번들 예산 | 150 KiB gzip 이내 |

## 업그레이드

할 일이 없습니다. 동작 변경 없음, API 변경 없음, 마이그레이션 없음.

10.0.0의 사용자 화면 작업(4구역 첫 화면, 한국어/영어 완전 지원, 무엇을 셌는지
말하는 숫자)은 그대로이며 [RELEASE_NOTES_v10.0.0.md](RELEASE_NOTES_v10.0.0.md)에
설명돼 있습니다.

## 알려진 한계

10.0.0과 동일합니다:

- 백엔드가 생성하는 일부 안내 문장(예: Postgres DSN 안내)은 서버 쪽에서 영어로
  만들어져 그대로 표시됩니다. 서버 i18n은 아직 범위 밖입니다.
- 사용자가 넣은 자료와 대화 내용은 번역하지 않습니다.
