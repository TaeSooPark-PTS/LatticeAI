# Lattice AI 전체 코드 리뷰 (2026-07-06)

## 8.9.0 반영 상태

이 리뷰에서 제안한 개선안은 8.9.0에서 가능한 범위까지 코드, 테스트,
문서, 릴리스 메타데이터에 반영했다. 단, 사용자가 명시적으로 제외한
Computer Use 직접 API 위험 항목(`/cu/*` policy/capability 적용)은 이번
릴리스 범위에서 제외했다.

8.9.0에서 완료한 주요 항목:

- `/history*`와 `/tools/clear_history`에 사용자/워크스페이스 스코프를 적용했다.
- Knowledge Graph 검색, 노드 조회, 관계 조회, traversal, chat context에 workspace scope를 적용했다.
- HTTP/MCP 직접 Tool API가 ToolRegistry 정책을 통과하도록 게이트를 추가했다.
- `SessionStore` TTL/refresh 값을 런타임에서 주입받도록 수정했다.
- AgentRuntime approval/rollback 의미를 실제 동작과 맞추고 회귀 테스트를 추가했다.
- local permission approval token을 hash-at-rest로 바꾸고 blocked prefix를 최종 강제했다.
- 모델 다운로드 허용값을 env 직접 조회가 아니라 runtime config 상태로 주입했다.
- AppRuntime legacy namespace adapter, KG JSON/runtime 분리, API client/CSS/i18n 검사 분리를 적용했다.
- README/RELEASE/docs/CHANGELOG/FEATURE_STATUS/SECURITY/VS Code 문서를 8.9.0으로 최신화했다.

## 1. 검토 개요

이 문서는 Lattice AI 저장소 전체를 대상으로 한 코드 리뷰 결과다. 검토 기준은 다음과 같다.

- Python 백엔드, Knowledge Graph, Agent Runtime, Tool Registry, 로컬 파일/권한, 인증/세션, 모델 런타임, 프런트엔드, 문서, 빌드/테스트 스크립트 확인
- 가상환경, 빌드 산출물, Tauri target, node_modules, 캐시 파일은 제외하고 1차 정적 메트릭 집계
- 주요 위험 파일은 라인 단위로 직접 확인
- 기존 아키텍처 문서, README, 릴리스 문서, 테스트 구조와 실제 구현의 일치 여부 확인

검토 결과, 프로젝트는 이미 단순 데모 수준을 넘어 로컬 우선 AI 워크스페이스로서 중요한 기반을 꽤 많이 갖추고 있다. 특히 import-safe 앱 팩토리, Tool Registry, 세션 토큰 해시 저장, Knowledge Graph v2 projection, React Query 기반 프런트엔드, OpenAPI 타입 생성, 릴리스 검증 스크립트는 좋은 방향이다.

다만 현재 가장 큰 부족점은 기능의 부재가 아니라 "보안/권한 정책이 모든 진입점에서 동일하게 적용되지 않는 것", "사용자/워크스페이스 스코프가 일부 API에서 빠지는 것", "런타임 조립과 전역 상태가 아직 커서 테스트와 운영 안정성이 약해지는 것"이다. 즉 다음 단계의 핵심은 새 기능 추가보다 경계 강화, 정책 단일화, 런타임 분해다.

## 2. 저장소 규모와 구조 메트릭

빌드 산출물과 가상환경을 제외한 주요 규모는 다음과 같다.

| 영역 | 파일 수 | 대략 라인 수 | 관찰 |
| --- | ---: | ---: | --- |
| Python | 311 | 66,599 | 백엔드, 런타임, KG, 도구, 테스트 기반이 큼 |
| TypeScript/TSX | 64 | 32,175 | OpenAPI 생성 타입과 프런트엔드가 큼 |
| CSS | 4 | 8,900 | `frontend/src/styles.css`가 대부분 |
| Markdown | 98 | 10,056 | 릴리스/아키텍처 문서가 많음 |
| JSON | 35 | 25,441 | `frontend/openapi.json` 포함 |

가장 큰 1차 소스 파일은 다음과 같다.

| 파일 | 라인 수 | 주요 문제 |
| --- | ---: | --- |
| `frontend/src/api/openapi.ts` | 18,185 | 생성 파일이므로 크기 자체는 수용 가능 |
| `frontend/openapi.json` | 15,639 | 생성 산출물, 릴리스 검증 대상 |
| `frontend/src/styles.css` | 8,592 | 수동 유지보수 비용이 큼 |
| `frontend/src/i18n.ts` | 1,853 | 번역 키/타입 관리가 커지고 있음 |
| `lattice_brain/graph/discovery.py` | 1,455 | KG discovery 책임 집중 |
| `latticeai/app_factory.py` | 1,425 | 조립 루트가 여전히 큼 |
| `latticeai/core/workspace_os.py` | 1,391 | Workspace OS 책임 집중 |
| `lattice_brain/graph/retrieval.py` | 1,341 | 조회, 스코프, 랭킹 책임 혼재 |
| `latticeai/services/model_runtime.py` | 1,146 | 전역 상태 호환 계층 유지 |
| `lattice_brain/graph/_kg_common.py` | 1,123 | 공통 상수, 유틸, LLM extraction, 전역 라우터 혼재 |
| `latticeai/api/chat.py` | 1,063 | 채팅, 히스토리, 문서 생성, trace 책임 혼재 |
| `latticeai/integrations/telegram_bot.py` | 1,009 | 예외 처리와 통합 로직 집중 |

정적 패턴 관찰:

- `except Exception` 사용이 약 380회로 많다. 사용자 경험을 깨지 않기 위한 의도는 이해되지만, 권한/보안/데이터 무결성 경계에서는 더 구체적인 예외와 감사 로그가 필요하다.
- `TODO`는 많지 않지만 `tools/filesystem.py`에 집중되어 있다.
- `subprocess` 사용은 약 79회이며 모델 엔진 설치/실행 경로에 많다. 로컬 앱 특성상 필요하지만, 감사와 사용자 확인 경계가 더 중요해진다.
- `shell=True`는 제한적으로 보이며 대부분 정책 플래그 또는 Windows start 용도다. 큰 위험은 아니지만 installer 계열 실행은 별도 감사가 필요하다.
- CORS는 wildcard가 아니라 localhost/127.0.0.1 중심으로 구성되어 있어 방향이 좋다.

## 3. 총평

현재 상태를 한 문장으로 요약하면 "기능 표면은 넓고 기반 설계는 좋아졌지만, 로컬 파일/데스크톱/히스토리/KG처럼 민감한 영역의 정책 적용이 아직 진입점별로 들쭉날쭉하다"이다.

권장 우선순위:

1. 모든 사용자 데이터 조회 API에 사용자/워크스페이스 스코프 적용
2. 모든 도구 실행 진입점에 Tool Registry 정책을 강제
3. Computer Use API를 관리자 또는 별도 capability 승인 기반으로 격상
4. Agent approval/rollback의 실제 동작을 정책 의미와 맞춤
5. AppFactory와 모델/KG 전역 상태를 더 작은 런타임 컴포넌트로 분해
6. KG PostgreSQL 문서와 실제 구현의 불일치를 해소
7. 프런트엔드 API client, CSS, i18n 검사를 도메인 단위로 분리

## 4. 잘 되어 있는 점

### 4.1 import-safe 앱 구조

`latticeai/app_factory.py`는 과거 import 시점에 발생하던 무거운 초기화를 `create_app` / `build_runtime` 호출 시점으로 옮긴 구조다. 모듈 상단 주석도 이 의도를 명확히 설명한다. 이는 테스트, CLI, 문서 생성, OpenAPI 생성에서 매우 중요한 개선이다.

좋은 점:

- FastAPI app 생성과 런타임 조립이 명시적 진입점으로 이동
- `server.py`, `latticeai/server_app.py`가 lazy facade 역할을 하도록 정리됨
- runtime 하위 모듈로 config, security, persistence, hooks, web runtime을 분리하려는 방향이 보임

남은 문제:

- `_build()` 내부가 여전히 1,425라인 파일의 중심이며 `return dict(locals())`로 legacy namespace를 노출한다.
- 이 방식은 호환성에는 좋지만 실제 ownership, 타입 경계, 테스트 fixture 경계를 흐린다.

### 4.2 보안 기본기

긍정적인 부분:

- 세션 토큰은 평문 저장이 아니라 sha256 해시로 저장된다.
- `latticeai/core/security.py`에 암호 해시, 토큰 redaction, rate limit, trusted proxy 처리 등 보안 유틸이 모여 있다.
- `latticeai/runtime/web_runtime.py`의 CORS는 wildcard가 아니라 loopback과 명시 origin 중심이다.
- OIDC SSO는 PKCE와 nonce를 사용하며 fail-closed 검증 흐름이 있다.

### 4.3 Tool Registry 방향성

`latticeai/core/tool_registry.py`와 `latticeai/services/tool_dispatch.py`는 도구 권한, 위험도, auto approval, sandbox 정책을 중앙화하려는 좋은 구조다.

좋은 점:

- 도구별 manifest/diagnostics가 존재한다.
- admin 전용 도구와 user 도구를 나누려는 설계가 있다.
- agent 경로에서는 destructive/system sandbox에 대한 차단이 일부 동작한다.

핵심 부족점:

- 정책이 "존재"하지만 HTTP 직접 도구 API와 Computer Use 일부 경로에서는 강제되지 않는다.

### 4.4 Knowledge Graph 마이그레이션 안전성

좋은 점:

- `KnowledgeGraphStore`는 mixin 기반으로 write, projection, discovery, ingest, retrieval을 나누고 있다.
- v2 projection view를 두고 legacy table과 읽기 호환성을 유지하려는 구조가 있다.
- DB format version, projection version, forward compatibility guard가 있다.
- local-first 데이터 모델에 맞게 rollback/reprojection 중심 사고가 반영되어 있다.

### 4.5 프런트엔드 기반

좋은 점:

- React Query 기반 데이터 fetch가 보인다.
- 라우트가 lazy loading으로 나뉘어 있다.
- OpenAPI 타입 생성이 있어 API 계약을 타입으로 받을 수 있다.
- Zustand store가 간결하고 UI 상태 persistence가 단순하다.

## 5. P0/P1 핵심 이슈

### P0-1. 히스토리 조회 API가 사용자/워크스페이스 스코프를 적용하지 않음

근거:

- `latticeai/api/chat.py:791` `/history`는 `require_user(request)` 후 `get_history()` 전체를 반환한다.
- `latticeai/api/chat.py:797` `/history/conversations`도 전체 conversation grouping을 반환한다.
- `latticeai/api/chat.py:803` `/history/conversations/{conversation_id}`도 conversation_id만으로 메시지를 반환한다.
- `latticeai/api/chat.py:842` `/history/search`는 전체 `get_history()`에서 검색한다.
- 같은 파일의 채팅 생성 경로는 `effective_email`과 `history_user`를 사용하므로 저장 단계에는 사용자 메타데이터가 들어가고 있다.

위험:

- 인증이 켜진 조직/워크스페이스 모드에서 한 사용자가 다른 사용자의 대화 내용을 조회할 수 있다.
- 대화에는 로컬 파일 경로, 문서 내용, 모델 출력, 업무 정보, API 키 설정 힌트 등이 포함될 수 있다.
- local-first 제품이라도 "같은 머신의 여러 사용자/조직"을 지원하려면 가장 먼저 닫아야 할 경계다.

개선안:

1. 히스토리 읽기 함수에 `user_email`, `allowed_workspaces`, `conversation_id` 필터 인자를 추가한다.
2. `fetch_history`, `fetch_history_conversations`, `fetch_history_conversation`, `search_history`에서 `get_current_user` 또는 `require_user` 결과를 기준으로 필터링한다.
3. legacy-global 히스토리가 있다면 KG와 동일하게 호환 정책을 문서화한다. 예: no-auth local mode에서는 전체, auth mode에서는 owner 없는 legacy row를 관리자만 볼지 또는 마이그레이션할지 결정.
4. 삭제 API도 동일한 owner/scope 확인을 적용한다. 현재 `/history/conversations/{conversation_id}` 삭제는 `clear_conversation(conversation_id, ...)`로 전체 conversation_id를 지울 수 있다.

필수 테스트:

- 사용자 A가 만든 conversation을 사용자 B가 `/history`, `/history/conversations`, `/history/search`로 볼 수 없는지
- 사용자 B가 사용자 A의 conversation_id로 직접 조회/삭제할 때 404 또는 403이 나는지
- no-auth local mode에서는 기존 동작이 유지되는지
- admin 전용 감사/히스토리 조회는 명시적으로 admin route에서만 가능한지

### P0-2. Computer Use 직접 API가 민감한 데스크톱 제어를 일반 사용자 권한으로 실행함

근거:

- `latticeai/api/computer_use.py:150` `/cu/open_app`
- `latticeai/api/computer_use.py:155` `/cu/open_url`
- `latticeai/api/computer_use.py:160` `/cu/click`
- `latticeai/api/computer_use.py:165` `/cu/type`
- `latticeai/api/computer_use.py:170` `/cu/key`
- `latticeai/api/computer_use.py:175` `/cu/scroll`
- `latticeai/api/computer_use.py:180` `/cu/move`
- `latticeai/api/computer_use.py:185` `/cu/drag`

위 경로들은 모두 `require_user(request)`만 호출하고 `tool_response(...)`로 직접 실행한다. 반면 `/cu/status`, `/cu/screenshot`, `/cu/agent` 일부 shortcut 경로는 `_dispatch(...)`로 hook lifecycle을 통과한다.

위험:

- Computer Use는 로컬 데스크톱을 클릭/타이핑/앱 실행할 수 있으므로 파일 쓰기보다 더 민감할 수 있다.
- 같은 브라우저 세션을 가진 일반 사용자 또는 CSRF/로컬 웹 공격 표면이 이 API에 도달하면 의도하지 않은 키 입력, URL 오픈, 앱 실행이 가능하다.
- Tool Registry에서 system sandbox 또는 admin-only로 정의해도 이 직접 경로가 우회하면 정책 의미가 약해진다.

개선안:

1. 모든 `/cu/*` 직접 액션을 `_dispatch`로 통일한다.
2. `_dispatch` 내부에서 `check_tool_role` 또는 `ToolRegistry.policy_for`를 반드시 호출한다.
3. Computer Use에는 별도 capability를 둔다. 예: `desktop_control` permission, 짧은 TTL, 명시 opt-in, audit event.
4. click/type/key/drag는 admin 또는 로컬 단일 사용자 모드에서만 허용하는 기본값을 검토한다.
5. 좌표 기반 행동은 screenshot hash 또는 active app/window context를 audit에 남긴다.

필수 테스트:

- 일반 user가 `/cu/click` 호출 시 정책에 따라 403이 나는지
- admin 또는 승인된 capability가 있으면 성공하는지
- 모든 `/cu/*` 액션이 hook pre_tool/post_tool을 통과하는지
- policy block 시 실제 tool function이 호출되지 않는지

### P0-3. 직접 HTTP Tool API가 Tool Registry 정책을 완전히 강제하지 않음

근거:

- `latticeai/api/tools.py:256` `_tool_response`는 `dispatch_tool(HOOKS, ...)`만 호출한다.
- `latticeai/api/tools.py:282` 이후 `list_dir`, `read_file`, `write_file`, `edit_file`, `todo_write`, 문서 생성 도구 등이 `require_user` 후 `_tool_response`로 실행된다.
- `latticeai/services/tool_dispatch.py`의 role/policy 체크는 agent runtime 경로에서 더 잘 쓰이지만, 이 HTTP route 전체에 일관되게 적용되지 않는다.
- 일부 위험 도구(`run_command`, `deploy_project`)는 admin route로 보호되지만, 파일 쓰기/편집/문서 생성/desktop control도 정책적으로 민감하다.

위험:

- Tool Registry에 정의한 `auto_approve`, `risk`, `sandbox`, blocked path 정책이 HTTP 직접 경로에서 빠질 수 있다.
- Agent 경로와 UI 직접 도구 경로의 보안 의미가 달라진다.
- 향후 새 도구 추가 시 "등록은 되었지만 직접 endpoint에서는 정책 우회"하는 회귀가 반복될 수 있다.

개선안:

1. `_tool_response` 시작 시 반드시 중앙 policy gate를 호출한다.
2. gate는 최소한 다음을 검사해야 한다.
   - 인증된 사용자 role
   - tool risk/destructive/sandbox/network/shell 속성
   - auto approval 여부
   - workspace path boundary
   - local absolute path approval token
3. `ToolRouterContext`에 `tool_governance` 또는 `ToolDispatchService` 자체를 주입한다.
4. 직접 HTTP 도구 endpoint와 agent tool execution이 같은 `execute_tool_with_policy(...)` 함수를 쓰게 만든다.
5. 정책 예외가 필요한 read-only 도구는 manifest에 명시한다.

필수 테스트:

- user role로 `write_file`이 정책상 blocked path에 쓰기를 시도하면 403
- `auto_approve=false` 도구는 승인 토큰 없이는 실행되지 않음
- HTTP direct, agent, workflow 세 진입점이 같은 policy 결과를 반환
- hook이 allow해도 registry gate가 block하면 실행되지 않음

### P1-1. 세션 TTL 설정 주입이 실제 SessionStore 만료에 반영되지 않음

근거:

- `latticeai/core/sessions.py:18`에 `SESSION_TTL = 60 * 60 * 24` 모듈 상수가 있다.
- `SessionStore._get_entry()`는 `latticeai/core/sessions.py:120`에서 이 모듈 상수로 만료를 판단한다.
- `latticeai/runtime/bootstrap.py`의 `build_session_runtime(ttl_seconds=...)`는 cookie max_age와 외부 노출 TTL을 구성하지만 `SessionStore`에는 ttl을 전달하지 못한다.

위험:

- 환경 설정이나 테스트에서 TTL을 바꿔도 서버 측 세션 저장소는 24시간으로 동작할 수 있다.
- 쿠키 만료와 서버 측 토큰 만료가 달라져 보안 정책이 일관되지 않는다.

개선안:

1. `SessionStore.__init__(ttl_seconds: int = SESSION_TTL, refresh_threshold_seconds: int = SESSION_REFRESH_THRESHOLD)`를 받도록 변경한다.
2. `build_session_runtime`에서 동일 TTL을 전달한다.
3. persist format은 그대로 유지해 backward compatibility를 보존한다.

필수 테스트:

- `SessionStore(ttl_seconds=1)` 생성 후 시간이 지나면 token이 만료되는지
- cookie max_age와 store TTL이 같은 설정에서 생성되는지
- 기존 `sessions.json` migration이 유지되는지

### P1-2. Agent approval이 실제 승인 대기 없이 auto-approved로 기록됨

근거:

- `latticeai/core/agent.py:210` `approve()`는 non-auto tools를 계산한다.
- `latticeai/core/agent.py:218` transcript에는 `requires_approval`과 `non_auto_approve_steps`가 남는다.
- 하지만 `latticeai/core/agent.py:222` decision은 항상 `"auto_approved"`이고 `ctx.state = EXECUTING`으로 넘어간다.
- `latticeai/core/agent.py:320` non-auto policy는 audit만 남기고 실행 자체는 계속된다.

위험:

- Tool Registry의 `auto_approve=false` 의미가 사용자에게 보이는 정책과 실제 실행 사이에서 어긋난다.
- 민감 도구는 승인 UI가 아직 없으면 block 또는 review queue로 보내야 한다.

개선안:

1. approval state를 실제로 terminal/pending 상태로 만들고 UI/API 승인 endpoint를 연결한다.
2. 승인 UI가 아직 없다면 `auto_approve=false` 도구는 기본 block으로 처리한다.
3. `requires_approval=true` 계획은 agent run response에 pending decision ID를 반환한다.
4. approval audit에는 requester, approver, tool, args redaction, TTL을 남긴다.

필수 테스트:

- `auto_approve=false` 도구가 승인 없이 실행되지 않는지
- 승인 후에는 같은 run id에서만 실행되는지
- 승인 TTL 만료 후 재실행이 막히는지

### P1-3. Agent rollback이 실제 파일 변경을 놓칠 수 있음

근거:

- `latticeai/core/agent.py:424` rollback은 transcript의 `result`를 읽는다.
- `latticeai/core/agent.py:425`에서 `result.get("success")`가 truthy인 경우만 rollback한다.
- 그러나 여러 도구는 `{path, bytes, ...}` 형태를 반환하고 `success` 키를 보장하지 않는다.

위험:

- 검증 실패 후 rollback 경로가 있다고 보이지만 실제 파일 변경은 복구되지 않을 수 있다.
- 사용자에게 "롤백을 시도했으나 복구할 파일이 없음"으로 보이면서 수정 파일은 남아 있는 상태가 될 수 있다.

개선안:

1. rollback 대상 판정은 policy의 `rollback == "git"`와 action/path 존재 여부를 기준으로 한다.
2. tool result schema를 표준화한다. 예: `ToolResult(success: bool, path?: str, changed?: bool, rollback_hint?: dict)`.
3. 파일 변경 전 snapshot 또는 git diff check를 남긴다.
4. rollback 실패는 audit에 error와 stderr를 남긴다.

필수 테스트:

- `write_file` 성공 결과에 `success`가 없어도 rollback 대상이 되는지
- git 미초기화 상태에서 명확한 실패 메시지가 나오는지
- 여러 파일 변경 중 일부 실패 시 partial rollback 결과가 정확한지

### P1-4. PostgreSQL/pgvector 지원 문서와 실제 KG 런타임 구현이 불일치함

근거:

- README와 기능 문서에서는 PostgreSQL/pgvector scale mode를 언급한다.
- `lattice_brain/storage/postgres.py`와 storage factory는 존재한다.
- 하지만 `lattice_brain/graph/store.py:39` 이후 capabilities를 확인한 뒤, `lattice_brain/graph/store.py:42`에서 `storage_caps.engine != "sqlite"`이면 `RuntimeError`를 발생시킨다.

위험:

- 운영자가 문서만 보고 PostgreSQL 모드를 기대하면 런타임에서 실패한다.
- "migration/scale tooling은 있지만 live KG store는 SQLite only"라는 실제 제약이 문서에 충분히 드러나지 않는다.

개선안:

1. 단기: 문서에 "현재 live KnowledgeGraphStore는 SQLite 런타임만 지원, Postgres는 migration/scale tooling 대상"이라고 명확히 쓴다.
2. 중기: `KnowledgeGraphStore`가 storage engine interface만으로 동작하도록 SQL dialect 차이를 분리한다.
3. 장기: pgvector 검색과 SQLite FTS/embedding 검색의 동등성 테스트를 만든다.

필수 테스트:

- Postgres URL 설정 시 현재 의도된 에러 메시지가 문서와 일치하는지
- migration tooling은 SQLite source에서 Postgres target으로 동작하는지
- live Postgres 지원을 켤 경우 SQLite/Postgres query contract가 같은지

### P1-5. Knowledge Graph read scoping이 API 전체에서 일관되지 않을 수 있음

근거:

- `lattice_brain/graph/retrieval.py:104` `filter_scoped_nodes`는 잘 만들어져 있다.
- `lattice_brain/graph/retrieval.py:122` `graph(..., allowed_workspaces=...)`는 스코프를 적용한다.
- 하지만 모든 read 메서드가 `allowed_workspaces` 인자를 받거나 route layer에서 필터링하는 구조는 아니다.
- search, context, neighbors, get_node, relationship/traverse 계열에서 누락 가능성이 있다.

위험:

- 그래프 화면은 스코프가 적용되지만 검색/컨텍스트/상세 조회에서는 다른 workspace node가 보일 수 있다.
- 히스토리 스코프 문제와 결합하면 조직 모드 데이터 격리 신뢰도가 낮아진다.

개선안:

1. KG read API 전체 목록을 만들고 `allowed_workspaces` 지원 여부를 표로 관리한다.
2. route layer에서 `allowed_workspaces_for(user)`를 빠뜨릴 수 없도록 `ScopedKnowledgeGraph` facade를 만든다.
3. legacy-global row 정책을 중앙 함수로 둔다.
4. read method 내부 SQL에서 가능한 경우 workspace predicate를 직접 적용해 후처리 필터 비용을 줄인다.

필수 테스트:

- workspace A/B node를 만들고 모든 read API에서 cross-workspace leak이 없는지
- legacy-global row가 의도한 정책대로 보이는지
- edge만 남거나 node만 필터링되어 그래프가 깨지지 않는지

## 6. P2 아키텍처 개선 이슈

### P2-1. AppFactory가 여전히 너무 큰 composition root임

근거:

- `latticeai/app_factory.py`는 1,425라인이다.
- `_build()` 내부에서 MLX 초기화, config, security, auth, KG, model router, hooks, platform, router registration이 모두 조립된다.
- `latticeai/app_factory.py:1383`에서 `return dict(locals())`로 전체 local namespace를 runtime attribute로 노출한다.

위험:

- 어느 객체가 public runtime API인지 불명확하다.
- 테스트가 내부 local name에 의존하기 쉽다.
- 새 런타임 컴포넌트를 추가할 때 함수 크기가 계속 커진다.

개선안:

1. `AppRuntime`을 명시 dataclass 또는 typed protocol로 전환한다.
2. legacy compatibility는 `LegacyRuntimeNamespace` adapter로 한정한다.
3. `_build()`는 다음 단계로 분해한다.
   - `build_core_runtime`
   - `build_model_runtime`
   - `build_graph_runtime`
   - `build_tool_runtime`
   - `build_platform_runtime`
   - `build_api_runtime`
4. 각 builder는 입력 config/context와 출력 dataclass를 명시한다.

권장 순서:

1. `return dict(locals())` 의존 테스트 목록 파악
2. public runtime attribute inventory 생성
3. typed `AppRuntimeParts` 추가
4. legacy facade는 `__getattr__`에서만 유지

### P2-2. 모델 런타임에 전역 mutable state와 직접 env 접근이 남아 있음

관찰:

- `latticeai/services/model_runtime.py`는 `ModelRuntimeState`를 두고 있지만 module-global compatibility sync를 유지한다.
- `latticeai/services/model_engines.py`와 일부 KG 모듈은 직접 `os.getenv(...)`를 읽는다.

위험:

- 테스트 간 상태 오염 가능성이 있다.
- 설정 변경이 runtime instance별로 분리되지 않는다.
- 데스크톱 앱과 서버 모드가 같은 process에서 다른 설정을 쓰기 어렵다.

개선안:

1. `Config`에 모델 엔진/다운로드/외부 접근 설정을 모두 수렴한다.
2. `ModelRuntimeState`를 모든 함수에 명시 주입하고 global sync는 legacy facade로만 남긴다.
3. process launching과 model catalog resolution을 별도 service로 분리한다.
4. installer 실행은 dry-run/audit/confirmation token을 포함한 command plan으로 만든다.

### P2-3. `_kg_common.py`가 공통 모듈 이상의 책임을 갖고 있음

근거:

- `lattice_brain/graph/_kg_common.py`는 1,123라인이다.
- 상수, path exclusion, JSON helper, LLM router global ref, extraction prompt, parsing utility가 섞여 있다.
- `lattice_brain/graph/_kg_common.py:51`의 `_llm_router_ref`와 `set_llm_router(...)`는 전역 상태다.

위험:

- KG extraction 테스트에서 router mock 주입이 어렵다.
- import 순서와 runtime 조립 순서에 민감해진다.
- graph module 간 순환 의존을 숨긴다.

개선안:

1. `kg/path_policy.py`: 확장자, 제외 디렉터리, size limit
2. `kg/json_utils.py`: `_safe_loads`, hashing, slug
3. `kg/extraction.py`: LLM extraction interface와 prompt
4. `kg/runtime.py`: router/extractor dependency injection
5. `set_llm_router`는 deprecated adapter로만 유지

### P2-4. Retrieval의 chunk count가 metadata LIKE에 의존함

근거:

- `lattice_brain/graph/retrieval.py:57` `type='Chunk' AND metadata_json LIKE ?`로 document chunk 수를 센다.

위험:

- node id가 다른 metadata 값에 우연히 포함되면 잘못 count될 수 있다.
- JSON 문자열 검색은 느리고 index 활용이 어렵다.
- v2 projection과 legacy table 사이 의미가 흔들릴 수 있다.

개선안:

1. chunk node에는 `source_node` 또는 `document_id`를 정규 컬럼으로 둔다.
2. legacy row는 migration/reprojection으로 보강한다.
3. count query는 indexed column 기반으로 바꾼다.

### P2-5. 로컬 파일 승인 저장소의 내구성과 정책 적용 위치가 약함

관찰:

- `PermissionGateway`는 approval/pending queue를 관리하지만 approval은 메모리에 있고 pending queue는 JSON 파일이다.
- queue 파일 write가 atomic replace/lock 중심인지 더 강화할 필요가 있다.
- local absolute path 승인은 direct local API에 적용되지만 Tool Registry blocked prefix와 같은 정책이 항상 같은 위치에서 강제되는 것은 아니다.

위험:

- 재시작 후 pending/approval 상태 해석이 일관되지 않을 수 있다.
- 승인 token이 외부 알림에 포함될 경우 민감 정보 노출 가능성이 있다.
- path allow/block 정책이 endpoint별로 달라질 수 있다.

개선안:

1. approval/pending 상태를 하나의 durable store로 통합한다.
2. atomic write + file lock을 적용한다.
3. 승인 token은 한 번만 보여주고 저장 시 hash한다.
4. blocked prefix와 local path policy는 `PermissionGateway`에서 최종 강제한다.

## 7. 프런트엔드 리뷰

### 7.1 API client가 단일 파일에 너무 많은 책임을 가짐

근거:

- `frontend/src/api/client.ts`는 672라인이며 OpenAPI client 생성, Tauri backend origin 탐색, fetch timeout, API wrapper, fallback shape, domain 함수가 섞여 있다.
- `frontend/src/api/client.ts:114`에서 `credentials: "same-origin"`을 사용한다.

위험:

- Tauri backend origin이 UI origin과 다르면 cookie/session 전달이 의도와 다를 수 있다.
- API가 늘수록 conflict와 테스트 비용이 커진다.

개선안:

1. `api/base.ts`: base URL, timeout, auth/credentials 정책
2. `api/chat.ts`, `api/brain.ts`, `api/admin.ts`, `api/tools.ts`, `api/workspaces.ts`로 domain client 분리
3. Tauri 환경에서는 cookie 인증을 쓸지 bearer/header 인증을 쓸지 명확히 검증
4. fallback shape는 테스트 fixture로 분리

필수 테스트:

- Tauri backend origin일 때 credentials가 실제로 전달되는지
- auth 실패/timeout/unavailable 응답 shape가 모든 domain client에서 동일한지

### 7.2 CSS가 단일 대형 파일로 커짐

근거:

- `frontend/src/styles.css`는 8,592라인이다.

위험:

- UI 변경 시 영향 범위 파악이 어렵다.
- dead style 제거가 어렵다.
- theme token과 component style이 섞이면 디자인 일관성이 떨어진다.

개선안:

1. `styles/tokens.css`
2. `styles/layout.css`
3. `styles/components/*.css`
4. `styles/features/brain.css`, `styles/features/admin.css`, `styles/features/settings.css`
5. visual regression 또는 Playwright screenshot baseline을 주요 화면에 추가

### 7.3 i18n 검사 범위가 너무 좁음

근거:

- `scripts/check_i18n_literals.mjs:6`의 roots는 `features/brain`, `features/admin`, `components/onboarding`만 검사한다.
- `frontend/src/pages/System.tsx` 등 page 레벨에는 hardcoded English label이 남아 있다.

위험:

- "i18n check 통과"가 실제 전체 UI 번역 품질을 보장하지 못한다.
- 새 페이지가 추가될수록 번역 누락이 누적된다.

개선안:

1. 검사 root를 `frontend/src` 전체로 확장한다.
2. 예외 목록을 명시 allowlist 파일로 관리한다.
3. translation key type을 생성해 존재하지 않는 키를 컴파일 단계에서 막는다.
4. Korean/English snapshot 테스트를 주요 페이지에 추가한다.

### 7.4 workspaceId clearing 버그

근거:

- `frontend/src/store/appStore.ts:78` `setWorkspaceId`는 값이 있을 때만 `localStorage.setItem(...)`을 호출한다.
- `workspaceId`가 `null`일 때 localStorage의 `lattice.workspace`를 제거하지 않는다.

위험:

- 사용자가 workspace를 해제해도 새로고침 후 이전 workspace가 되살아날 수 있다.
- 스코프 버그와 결합하면 사용자가 예상하지 않은 workspace context로 작업할 수 있다.

개선안:

```ts
setWorkspaceId: (workspaceId) => {
  try {
    if (workspaceId) localStorage.setItem("lattice.workspace", workspaceId);
    else localStorage.removeItem("lattice.workspace");
  } catch {}
  set({ workspaceId });
}
```

필수 테스트:

- workspace 선택 후 reload persistence
- workspace clear 후 reload 시 null 유지

## 8. 테스트와 검증 체계 리뷰

좋은 점:

- 테스트 소스 파일이 111개로 적지 않다.
- KG, runtime, config, security, tool registry, route 계열 테스트가 존재한다.
- `scripts/check_python.py`, `scripts/lint_frontend.mjs`, OpenAPI path count 검증 등 기본 자동화가 있다.

부족한 테스트:

| 영역 | 필요한 테스트 |
| --- | --- |
| 히스토리 스코프 | 사용자 A/B 격리, conversation 직접 조회/삭제 방지 |
| Tool policy | HTTP direct, agent, workflow가 같은 policy gate를 통과 |
| Computer Use | 일반 user 차단, admin/capability 승인 성공 |
| Session TTL | 설정 TTL과 store TTL 일치 |
| Agent rollback | `success` 키 없는 파일 변경 결과도 rollback |
| KG scoping | 모든 read API cross-workspace leak 방지 |
| PostgreSQL story | 현재 SQLite-only 계약 또는 Postgres live 지원 계약 고정 |
| i18n | `frontend/src` 전체 hardcoded literal 검사 |
| Desktop auth | Tauri origin에서 cookie/header 인증 동작 |

권장 CI gate:

1. `npm run check:python`
2. `npm run lint`
3. `npm run typecheck`
4. `npm run test:unit`
5. `npm run build`
6. `npm run docs:check-links`
7. 보안 회귀 전용 pytest group: `tests/security`, `tests/api/test_history_scope.py`, `tests/api/test_tool_policy_http.py`

## 9. 문서 리뷰

좋은 점:

- README와 RELEASE는 현재 버전 8.9.0을 중심으로 릴리스 산출물 경로를 꽤 엄격하게 관리한다.
- `ARCHITECTURE.md`는 현재 구조를 설명하는 데 유용하다.
- AGENTS 문서는 프로젝트의 선호 리팩터링 순서와 릴리스/문서 sync 규칙을 명확히 한다.

부족한 점:

- `docs/architecture.md`에는 과거 v3.6.0 중심 설명이 남아 있어 현재 `ARCHITECTURE.md`와 역할이 겹치거나 혼동될 수 있다.
- 기존 `review.md`는 2026-06-22 기준이며, 현재는 ToolRegistry와 AgentRuntime이 상당히 진행되어 일부 지적이 역사적 문맥이 되었다.
- PostgreSQL/pgvector scale mode 설명은 실제 live KG runtime 제약과 더 명확히 맞춰야 한다.

개선안:

1. `ARCHITECTURE.md`를 canonical architecture 문서로 지정한다.
2. `docs/architecture.md`는 historical 또는 detailed subsystem 문서로 이름/상단 안내를 바꾼다.
3. 기존 `review.md` 상단에 "historical review" 표시를 추가하고 이 문서를 최신 리뷰로 링크한다.
4. FEATURE_STATUS/README에 SQLite-only live KG 제약을 정확히 반영한다.

## 10. 보안/권한 개선 로드맵

### 10.1 1단계: 데이터 조회 격리

목표:

- 히스토리, KG, 파일 인덱스, audit read API의 스코프 정책 통일

작업:

1. `allowed_workspaces_for(user)`를 route에서 직접 부르는 대신 scoped context 객체로 감싼다.
2. history 저장 row에 `user_email`, `workspace_id`, `organization_id`를 표준 필드로 보장한다.
3. 모든 read API에 `ScopeContext`를 전달한다.
4. legacy-global row 정책을 문서화하고 테스트한다.

### 10.2 2단계: Tool policy single gate

목표:

- agent, HTTP direct, workflow, computer-use가 같은 정책을 통과

작업:

1. `ToolExecutionGateway` 신설
2. 입력: `tool_name`, `args`, `user`, `source`, `approval_token`
3. 처리: role check -> path policy -> approval -> hook pre_tool -> execute -> hook post_tool -> audit
4. 기존 `_tool_response`, `_dispatch`, agent execution이 모두 이 gateway를 호출하도록 변경

### 10.3 3단계: 승인/감사 모델 강화

목표:

- 민감 도구 실행은 명시 승인 또는 admin/capability로만 가능

작업:

1. approval token hash 저장
2. approval TTL
3. approval scope: tool/action/path/content hash/user/run id
4. audit에는 content 전문 대신 hash와 redacted preview만 저장

## 11. 아키텍처 리팩터링 권장 순서

프로젝트 AGENTS의 선호 순서와 현재 코드 상태를 결합하면 다음 순서가 가장 안전하다.

1. AgentRuntime 정책 의미 보정
   - approval, rollback, tool execution gateway 통합
2. ToolRegistry separation 완성
   - 모든 도구 진입점에 registry gate 적용
3. Config centralization
   - 모델/KG/env 직접 접근 제거
4. Server decomposition
   - AppFactory `_build()`를 typed runtime builder로 분해
5. Knowledge Graph stabilization
   - read scoping 전면화, `_kg_common.py` 분해, Postgres 계약 정리
6. Documentation synchronization
   - architecture 문서 canonical화, old review historical 표시
7. UI feature enhancements
   - API client/CSS/i18n 구조 분리 후 기능 개선

## 12. 즉시 실행 가능한 작업 목록

### 보안 우선

- [x] `/history*` API 사용자/워크스페이스 스코프 적용
- [x] `/tools/*` 직접 API에 Tool Registry gate 적용
- [ ] `/cu/*` 직접 action에 policy/capability 적용 — 8.9.0 범위에서 사용자 요청으로 제외
- [x] local file permission gateway에 blocked prefix 최종 강제
- [x] approval token hash 저장 및 TTL 적용

### 안정성 우선

- [x] `SessionStore` TTL 주입 버그 수정
- [x] Agent rollback 판정 로직 수정
- [x] tool result schema 표준화 — rollback 경로에서 `success` 없는 dict 결과 허용
- [ ] installer/process 실행 audit 강화 — 별도 hardening 후속으로 유지

### 구조 개선

- [x] `AppRuntime` typed dataclass 도입
- [x] `_kg_common.py`를 path policy/json/extraction/runtime으로 분리 — 8.9.0에서는 JSON/runtime 1차 분리 완료
- [x] `model_runtime` global compatibility layer 축소
- [x] `frontend/src/api/client.ts` domain 분리
- [x] `frontend/src/styles.css` feature별 분리 — 8.9.0에서는 tokens/base 1차 분리 완료
- [x] `scripts/check_i18n_literals.mjs` 검사 범위 확대

### 문서

- [x] PostgreSQL/pgvector 현재 지원 범위를 README/FEATURE_STATUS에 정확히 반영
- [x] `docs/architecture.md`의 v3.6.0 문맥을 historical로 명확히 표시
- [x] 기존 `review.md` 상단에 최신 리뷰 링크 추가

## 13. 권장 첫 번째 PR

가장 먼저 만들 PR은 "보안 스코프 및 도구 정책 게이트"가 적합하다.

포함 범위:

1. History read/delete scoping
2. `ToolExecutionGateway` 최소 버전 도입
3. HTTP direct tools gate 적용
4. Computer Use direct actions gate 적용 — 8.9.0에서는 사용자 요청으로 제외
5. 테스트 추가

포함하지 않을 것:

- AppFactory 대규모 분해
- KG Postgres live 지원
- CSS/i18n 대규모 정리

이렇게 나누면 보안 효과가 크고, 변경 범위도 API/tool 경계로 비교적 명확하게 제한된다.

## 14. 결론

Lattice AI는 이미 "로컬 AI workspace"로서 핵심 재료가 충분하다. 지금 부족한 것은 기능 아이디어가 아니라 경계의 일관성이다. 사용자 데이터, 로컬 파일, 데스크톱 제어, agent tool execution은 모두 같은 정책 언어로 묶여야 한다.

가장 중요한 다음 리팩터링은 UI 개선이나 새 모델 기능보다 다음 세 가지다.

1. 사용자/워크스페이스 데이터 격리
2. Tool Registry 정책의 단일 실행 게이트화
3. Agent approval/rollback의 실제 의미 보정

이 세 가지를 먼저 닫으면 이후 AppFactory 분해, KG 안정화, 프런트엔드 모듈화는 훨씬 안전하게 진행할 수 있다.
