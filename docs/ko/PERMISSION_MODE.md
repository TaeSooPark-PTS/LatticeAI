[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../PERMISSION_MODE.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./PERMISSION_MODE.md)

# 권한 모드 (Permission Mode, v9.9.8)

최신 에이전트는 자율성 다이얼(autonomy dial)을 제공합니다. LatticeAI는 fail-closed 기본값을 버리지 않으면서 동일한 개념을 ToolRegistry와 Change Governor에 매핑합니다.

## 모드별 동작

| 모드 | 워크스페이스 쓰기 | 지식 읽기 | 실행 / 데스크톱 제어 | 기존 파일 변경 |
|------|------------------|-----------------|------------------------|-----------|
| **strict** (기본값) | 승인 / 제안 | 게이트 적용 | 게이트 적용 | Review Center 제안 |
| **trusted** | 자동 | 자동 | 게이트 적용 | 자동 적용 + 감사 로그 |
| **bypass** | 자동 | 자동 | 자동 (워크스페이스 범위) | 자동 적용 + 감사 로그 |

하드 서킷 브레이커는 어떤 모드에서도 항상 적용됩니다: 파괴적 위험, `rm -rf /` / `~`, 차단된 경로 접두사, 제안을 지원하지 않는 바이너리 덮어쓰기.

**trusted** / **bypass** 모드에서는 `ChangeGovernor.review`가 실행되기 *전에* 관리 대상 변경 경로를 결정합니다. 해당 호출은 부수 효과로 제안을 영속화하므로, 먼저 검토하고 나중에 판정을 버리면 변경이 적용되는 *동시에* 고아 제안이 Review Center에 남게 되기 때문입니다.

## API

```http
GET  /api/permission-mode
GET  /api/permission-mode/catalog
POST /api/permission-mode
{"mode": "trusted"}
{"mode": "bypass", "acknowledge_risk": true}
```

스코프: 선택적 `workspace_id` 쿼리/본문 또는 `X-Workspace-Id` 헤더.
워크스페이스별 설정이 사용자별 설정보다 우선하며, 둘 다 프로세스 기본값보다 우선합니다.

환경 변수 기본값: `LATTICEAI_PERMISSION_MODE=strict|trusted|bypass`.

## UI

`PermissionModePanel`은 **설정 → 혼자 해도 되는 일**(`SystemPage` 설정 탭, 10.5.0 이전 명칭은 "에이전트 자율성")에 렌더링됩니다. 홈 화면의 `BrainQuickControls`에서도 같은 다이얼에 접근할 수 있습니다.

이 패널은 하드코딩된 목록 대신 `/api/permission-mode/catalog`가 반환하는 모드를 그대로 렌더링하므로 서버에 새 모드를 추가해도 프론트엔드 수정 없이 표시됩니다. 10.5.0부터 앱이 알고 있는 모드의 *문구*는 서버에서 그대로 가져오지 않고 `frontend/src/lib/permissionCopy.ts`가 모드 ID를 쉬운 라벨과 설명으로 매핑합니다(`strict` → 먼저 물어보기, `trusted` → 웬만하면 알아서, `bypass` → 거의 다 알아서). 두 화면 모두 이 단일 모듈을 읽으므로 같은 다이얼이 서로 다른 이름으로 불리지 않습니다. 매핑에 없는 ID만 서버의 현지화 라벨로 폴백합니다.

적용 버튼은 아래 조건이 모두 충족되기 전까지 비활성화됩니다.

* 현재와 *다른* 모드가 선택되었고,
* 카탈로그 항목에 `requires_ack`가 설정된 모드라면 위험 인지 체크박스가 체크된 경우 — 서버가 강제하는 조건과 동일하므로 UI는 거부될 것이 뻔한 요청을 보내지 않습니다.

변경에 실패하면 서버가 반환한 메시지를 그대로 보여주며, 성공하지 않은 결과를 성공으로 표시하지 않습니다.

## 스코프 해석

스코프는 장식이 아니라 저장된 재정의가 실제로 효력을 발휘하게 하는 핵심 요소입니다. 모든 적용 지점은 *호출자의 신원*을 함께 전달해 다이얼을 해석합니다.

| 적용 지점 | 전달되는 스코프 |
|-------------------|--------------|
| `ToolDispatchService.enforce_policy` | `current_user` + `workspace_id` |
| `SingleAgentRuntime` 도구 게이트 | `current_user` + `req.workspace_id` |
| 에이전트 계획 게이트 | 해당 실행에 각인된 모드 (아래 참고) |

`chat_agent_http`는 실행당 **한 번만** 모드를 해석해 `AgentRunContext.permission_mode`에 기록하므로, 실행 도중 저장된 환경설정이 바뀌더라도 사용자가 승인한 계획과 그 실행의 모든 도구 단계가 동일한 다이얼로 평가됩니다. 승인을 기다리며 일시 중지된 실행도 이 값을 함께 저장하므로 재개 시 승인 당시 모드를 유지합니다.

`ToolDispatchService.permission_mode`에 바인딩되는 리졸버는 `user_email`/`workspace_id` 키워드 인자를 받거나(권장) 인자 없이 호출될 수 있습니다(`call_mode_source` 참고). 스코프 없는 리졸버는 항상 프로세스 기본값만 반환하므로 사용자별·워크스페이스별 재정의가 조용히 무력화됩니다.

## 자동 연결 (Wiring)

수동으로 `app_factory`를 수정할 필요가 없습니다. 서버 시작 시:

1. `build_chat_agent_runtime_from_context`가 디스패치와 에이전트를 공유 모드 리졸버에 바인딩합니다.
2. `register_review_and_brain_tail_routers`가 `/api/permission-mode`를 마운트하고 `PermissionModeService`를 실제 `data_dir` 및 감사 싱크에 재바인딩합니다.

2단계는 "먼저 호출한 쪽이 고정"되는 방식이 아니라 *재바인딩*합니다. 그렇지 않으면 라우터 마운트 전에 일어난 도구 디스패치가 저장소를 감사 싱크 없는 기본 `~/.ltcai` 경로에 고정시켜 버릴 수 있습니다.

## 코드 맵

이 다이얼은 네이티브 Rust로 구현되어 있습니다. v11.6.0에서 에이전트 루프가 `lattice-agent`로, 라우트가 `lattice-platform`으로 이전되었고, v11.8.0에서 게이트 헬퍼 사본을 유지하던 마지막 Python 모듈(`latticeai/core/agent_permission.py`)이 삭제되었습니다.

| 모듈 | 역할 |
|--------|------|
| `rust/lattice-agent/src/mode.rs` | 순수 의사결정 테이블 (`latticeai.core.permission_mode` 이식) |
| `rust/lattice-agent/src/permission.rs` | 계획/도구 게이트 헬퍼 — 순서가 고정된 차단 사유 체인 |
| `rust/lattice-agent/src/breaker.rs` | 모드와 무관한 서킷 브레이커 |
| `rust/lattice-agent/src/agentloop/gates.rs` | 실행기 한 단계가 통과하는 게이트 순서 |
| `rust/lattice-agent/src/agentloop/planning.rs` | 계획 게이트 |
| `rust/lattice-platform/src/permission_mode.rs` | HTTP 라우트 및 영속화 (`permission_mode.json`, 원자적 교체) |
| `rust/lattice-auth/src/policy.rs` | 게이트가 참조하는 도구 정책 테이블 |
| `frontend/src/components/PermissionModePanel.tsx` | 설정 선택 UI |
| `frontend/src/lib/permissionCopy.ts` | 각 모드의 평이한 UI 문구 |
| `latticeai/core/permission_mode.py` | `POST /agent/tool`이 확인하는 워커 측 `is_circuit_breaker` — 디스패치 게이트뿐 아니라 실행 측에서도 모드 불변 거부가 유지됨 |

## AGENTS.md 참고 사항

**strict** 모드에서는 기존 규칙이 그대로 적용됩니다: 기존 사용자 콘텐츠를 직접 수정하지 않고 제안으로 스테이징합니다. **trusted** / **bypass** 모드에서는 사용자가 자율성을 명시적으로 높였으므로 변경이 감사 로그와 함께 자동 적용되지만, 서킷 브레이커는 여전히 파괴적 시스템 작업을 차단합니다.
