# Lattice AI 9.9.8 — Autonomy Dial

> **Status: historical** — point-in-time release note.

릴리스일: 2026-07-27

## 한 줄 요약

에이전트 자율성 다이얼(`strict` / `trusted` / `bypass`)을 추가했습니다.
**환경설정 → 에이전트 자율성**에서 직접 바꿀 수 있습니다. 기능 브랜치 리뷰에서
발견한 네 가지 결함 — 스코프 없는 모드 해석, 고아 변경 제안, 설정 불가능한 실행
단위 오버라이드, 모드 변경 데드락 — 을 함께 수정했고, 게이트를 런타임에
몽키패치하던 계층을 제거했습니다.

## 왜 필요한가

지금까지 Lattice AI의 도구 게이트는 하나의 고정된 fail-closed 정책이었습니다.
읽기는 자동, 쓰기는 승인 또는 변경 제안. 안전하지만, 자기 워크스페이스 안에서
반복 작업을 시키는 사용자에게는 매 단계가 마찰이었습니다.

9.9.8은 그 정책을 버리지 않고 **다이얼**을 얹습니다. 기존 ToolRegistry와
Change Governor는 그대로 두고, "이 호출이 추가 승인 없이 실행되어도 되는가"만
모드에 따라 넓힙니다. **하드 차단(circuit breaker)은 모드와 무관하게 항상
동작합니다** — 어떤 모드도 하드 차단을 무력화하지 못합니다.

## 모드

| 모드 | 워크스페이스 쓰기 | 지식 읽기 | 실행 / 데스크톱 제어 | 기존 내용 변경 |
|------|------------------|-----------|---------------------|----------------|
| **strict** (기본) | 승인 / 제안 | 게이트 | 게이트 | 변경 제안으로 저장 |
| **trusted** | 자동 | 자동 | 게이트 | 자동 적용 + 감사 로그 |
| **bypass** | 자동 | 자동 | 자동 (워크스페이스 내) | 자동 적용 + 감사 로그 |

`bypass`로 바꿀 때는 `acknowledge_risk=true`가 필요하고, 그 확인은 감사
로그에 남습니다.

모드와 무관하게 항상 거부되는 것:

- `destructive` 위험도 도구
- `rm -rf /`, `rm -rf ~` 류의 셸 명령
- `/`, `~`, `/home`, `/Users` 같은 루트/홈 경로 대상 작업
- 차단된 경로 접두사
- 검토 가능한 제안으로 만들 수 없는 바이너리 덮어쓰기

## 설정 방법

**환경설정 → 에이전트 자율성** 패널에서 모드를 고르고 "이 설정으로 변경"을
누릅니다. 패널은 하드코딩된 모드 목록이 아니라 **서버가 내려주는 카탈로그를
그대로 렌더링**하므로, 서버에서 모드를 추가하거나 이름을 바꿔도 프론트엔드를
고칠 필요가 없습니다.

- 지금과 **다른** 모드를 골라야 적용 버튼이 활성화됩니다.
- 카탈로그에 `requires_ack`가 표시된 모드(바이패스)는 위험 확인란을 체크해야
  적용됩니다 — 서버가 강제하는 조건과 동일하므로, 거부될 요청을 UI가 먼저
  보내지 않습니다.
- 변경이 거부되면 서버가 준 메시지를 그대로 보여줍니다. 받지 못한 성공을
  성공이라고 표시하지 않습니다.

## API

```http
GET  /api/permission-mode
GET  /api/permission-mode/catalog
POST /api/permission-mode   {"mode": "trusted"}
POST /api/permission-mode   {"mode": "bypass", "acknowledge_risk": true}
```

스코프는 `workspace_id`(본문/쿼리) 또는 `X-Workspace-Id` 헤더. 워크스페이스
설정이 사용자 설정을 이기고, 둘 다 프로세스 기본값을 이깁니다. 프로세스
기본값은 `LATTICEAI_PERMISSION_MODE` 환경변수로 지정합니다.

## 이번 릴리스에서 고친 결함

기능 브랜치를 그대로 병합하지 않고 리뷰한 결과, 다이얼을 **무력하게** 하거나
**안전하지 않게** 만드는 결함 네 가지를 찾아 함께 고쳤습니다.

### 1. 저장된 모드가 실제 적용되지 않음 (기능 무효화)

집행 지점이 사용자/워크스페이스 스코프 **없이** 모드를 해석하고 있었습니다.
`POST /api/permission-mode`는 설정을 `users[email]`에 저장하는데, 집행은
스코프 없이 조회해 항상 전역 기본값(`strict`)으로 되돌아갔습니다. 즉 다이얼을
돌려도 아무 일도 일어나지 않았습니다.

이제 `enforce_policy`와 에이전트 도구 게이트가 호출자의 `user_email` /
`workspace_id`로 모드를 해석합니다. 바인딩된 리졸버는 스코프 kwargs를 받거나
(권장) 인자 없이도 동작합니다(`call_mode_source`).

### 2. Review Center에 고아 제안이 쌓임 (안전성)

`trusted` / `bypass`에서 게이트 래퍼가 `ChangeGovernor.review()`를 먼저
호출한 뒤 결과를 버리고 도구를 실행했습니다. 그런데 `review()`는 **변경 제안을
저장하는 부수효과**가 있습니다. 결과적으로 변경은 적용되고, 동시에 승인
대기 제안이 Review Center에 남았습니다.

이제 모드 판단이 governor 호출 **이전에** 이루어집니다. 제안을 만들지 않는
모드에서는 `review()`를 아예 호출하지 않습니다.

### 3. 실행 단위 모드 오버라이드가 설정 불가능 (죽은 코드)

`AgentRunContext`는 `__slots__`를 쓰는데 `permission_mode` 슬롯이 없었습니다.
문서화된 실행 단위 오버라이드 경로는 도달할 수 없는 죽은 코드였습니다.

이제 슬롯이 존재하고, `chat_agent_http`가 실행당 한 번 모드를 확정해
스탬프합니다. 덕분에 **사용자가 승인한 계획과 그 실행의 모든 도구 단계가
동일한 다이얼로 판정**됩니다. 중간에 설정이 바뀌어도 실행 중인 런은 흔들리지
않습니다. 승인 대기로 멈춘 런은 그 스탬프를 함께 저장하므로, 재개될 때도
승인 당시의 모드로 실행됩니다.

### 4. 모드 변경이 영구히 멈춤 (데드락)

`PermissionModeService.set_mode()`가 재진입 불가능한 `threading.Lock`을 잡은
채 같은 락을 잡는 `resolve()`를 호출했습니다. 모든 모드 변경 요청이 워커
스레드를 영구히 붙잡았습니다(단위 테스트도 함께 멈췄습니다).

락을 잡지 않는 `_resolve_from(data, ...)` 헬퍼를 분리해 해결했습니다.
`tests/unit/test_permission_mode_scope.py`에 타임아웃 기반 회귀 테스트를
추가했습니다.

## 구조 정리: 몽키패치 제거

이전 구현은 `agent_mode_patch.py`가 런타임 인스턴스에 `approval_requirements`
/ `_blocked_by_gates` / `_governor_review`를 **런타임에 덮어쓰는** 방식이었습니다.
`agent.py`를 건드리지 않는다는 장점이 있었지만, 패치 대상 메서드의 시그니처가
바뀌면 조용히 어긋날 수 있는 구조였습니다.

9.9.8에서는 세 게이트를 `SingleAgentRuntime`에 직접 구현하고
`agent_mode_patch.py`를 삭제했습니다. `permission_mode`는 동적 속성이 아니라
`AgentDeps`의 정식 필드입니다. 부수 효과로 차단 사유가 더 정확해졌습니다 —
하드 차단(파괴적 정책 / 서킷 브레이커)은 실제로 발동한 사유를 그대로
트랜스크립트에 남기고, 승인 경로로 뭉뚱그리지 않습니다.

## 정리 작업

- `is_circuit_breaker`의 아무 동작도 하지 않는 `if ... pass` 블록 제거
- `enforce_policy`의 중복된 `fail_closed` 409 분기 통합 — 바이너리 덮어쓰기는
  모드와 무관하게 차단이며, 분기할 이유가 없습니다
- 사용처가 사라진 `filter_governor_verdict` 제거
- 기능과 무관하게 삭제됐던 `tool_dispatch.py`의 docstring 5개 복원
- 서비스에 `rebind_data_dir` / `rebind_audit` 추가 — 라우터 마운트 전에 지연
  생성된 싱글턴이 폴백 경로(`~/.ltcai`)와 감사 로그 없음 상태로 고정되던 문제
  해결

## 테스트

- `tests/unit/test_permission_mode.py` — 모드 결정 테이블
- `tests/unit/test_permission_mode_service.py` — 영속화 및 bypass 확인 절차
- `tests/unit/test_permission_mode_scope.py` (신규) — 위 결함 1~4의 회귀 테스트.
  실제 `SingleAgentRuntime`을 구성해 검증하므로 게이트를 우회하지 않습니다
- `frontend/src/components/PermissionModePanel.test.tsx` (신규) — 카탈로그
  렌더링, 승인 확인 강제, 거부 처리
- 단위 테스트 1,744개 + 프론트엔드 테스트 150개 통과

## 호환성

기본값은 `strict`이며, 이는 9.9.7의 동작과 동일합니다. 모드를 바꾸지 않으면
관측 가능한 변화가 없습니다.

`latticeai.core.agent_mode_patch`는 삭제됐습니다. 이 모듈은 9.9.8에서만
존재했고 공개 API로 문서화된 적이 없습니다. `AgentDeps(permission_mode=...)`가
대체 경로입니다.
