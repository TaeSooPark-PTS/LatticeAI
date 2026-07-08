# Lattice AI 전체 코드 리뷰 보고서

- **작성일**: 2026-07-08
- **방식**: 다중 에이전트 코드 리뷰 워크플로 (high effort) — 정확성 3각도 + 정리(cleanup) 파인더가 후보를 찾고, (file, line)마다 독립 검증 에이전트가 재확인
- **범위**: 전체 코드베이스 (working tree clean, 프로젝트 전체 대상)
- **결과**: 후보 34건 → **검증 통과 33건** (refuted 1건 제외)
- **검증 완료**: 40개 에이전트 전부 완료(에러 0). 1차 실행에서 세션 한도로 중단됐던 12개 검증을 재개해 모두 판정 확보. 항목 #22~#33이 이때 추가 확정됐다.

> 검증을 통과한 21건을 심각도 순으로 정리했다. 각 항목은 실제 코드 라인과 검증 에이전트가 확인한 근거를 포함한다. 압도적 다수가 **접근 제어 / 데이터 노출** 결함이며, 다른 곳에서는 강제하는 인증·승인 불변식(require_user/require_admin/승인 토큰)을 특정 표면에서 재수립하지 못하고 **fail-open** 하는 패턴이 반복된다.

---

## 요약 (한눈에)

| # | 심각도 | 파일:라인 | 문제 |
|---|--------|-----------|------|
| 1 | 🔴 Critical | `latticeai/integrations/telegram_bot.py:940` | 텔레그램 봇에 chat-id 허용목록 없음 — 아무나 명령·스크린샷·대화 미러링 수신 |
| 2 | 🔴 Critical | `latticeai/api/static_routes.py:68` | 초대 게이트가 정적 쿠키 `authorized=true` 만 믿음 — 게이트 무력화 |
| 3 | 🔴 Critical | `latticeai/core/tool_registry.py:150` | `local_read`/`local_list` 가 auto_approve — 승인 없이 임의 로컬 파일 읽기 |
| 4 | 🔴 Critical | `tools/commands.py:48` | `run_command` 가 `../` 탈출을 막지 않음 — 샌드박스 밖 파일 읽기 |
| 5 | 🔴 Critical | `latticeai/api/mcp.py:109` | `GET /mcp/tools` 만 인증 가드 누락 — 비인증 정보 노출 |
| 6 | 🔴 Critical | `lattice_brain/graph/retrieval.py:110` | 워크스페이스 스코핑이 예외 시 fail-open — 교차 워크스페이스 데이터 노출 |
| 7 | 🟠 High | `latticeai/api/chat.py:381` | 모델 미로딩 시 빈 파일 생성 후 "성공" 응답 |
| 8 | 🟠 High | `frontend/src/features/brain/BrainHome.tsx:87` | HTTP 오류에도 위임 "완료" 표시 — 사용자 목표 조용히 유실 |
| 9 | 🟠 High | `latticeai/services/run_executor.py:117` | 예외 시 run이 영구 'running' 상태로 멈춤 |
| 10 | 🟠 High | `latticeai/api/chat_helpers.py:55` | 네트워크 상태 요청 과탐지 — 무관한 질문에 IP 노출 |
| 11 | 🟠 High | `latticeai/api/chat_helpers.py:61` | URL 요청 과탐지 — "주소 알려줘"에 접속 URL 응답 |
| 12 | 🟠 High | `latticeai/api/chat.py:740` | 스트리밍 텍스트 이중 파싱 — 잘못된 출력 위험 |
| 13 | 🟡 Medium | `latticeai/integrations/telegram_bot.py:80` | 봇이 평문 세션 키 재생 — v4 해시 저장 이후 인증 항상 실패 |
| 14 | 🟡 Medium | `latticeai/integrations/telegram_bot.py:29` | `BASE_URL` 하드코딩(127.0.0.1:4825), env 오버라이드 없음 |
| 15 | 🟡 Medium | `latticeai/api/permissions.py:220` | 만료 정리 루프가 `key` 변수 리바인딩 — 승인 조회 실패 |
| 16 | 🟡 Medium | `latticeai/api/permissions.py:92` | `_notify_discord_permission_sync` 호출부 없음 — 죽은 알림 경로 |
| 17 | 🟡 Medium | `latticeai/api/chat.py:871` | fire-and-forget `create_task` — 참조 유실로 GC될 수 있음 (PLAUSIBLE) |
| 18 | 🟢 Low | `latticeai/runtime/audit_runtime.py:41` | 감사 로그가 매 append 마다 전체 JSON 재파싱·재작성 — O(n²) |
| 19 | 🟢 Low | `latticeai/app_factory.py:460` | 모든 기동마다 무조건 마이그레이션 재실행 |
| 20 | 🟢 Low | `latticeai/core/users.py:21` | `_atomic_write_json` 세 곳 중복 정의 |
| 21 | 🟢 Low | `latticeai/api/local_files.py:18` | 계층화된 패키지가 루트 모듈을 직접 import (레이어링 누수) |
| 22 | 🟠 High | `latticeai/api/chat.py:736` | 스트리밍 루프에 try/except 없음 — 중간 예외 시 히스토리·트레이스 유실, SSE 중단 |
| 23 | 🟡 Medium | `latticeai/api/chat.py:264` | async 핸들러가 동기 `save_to_history`/ingest 호출 — 이벤트 루프 블로킹 |
| 24 | 🟡 Medium | `latticeai/runtime/audit_runtime.py:31` | 감사 타임스탬프가 naive `datetime.now()` — LATTICE_TZ와 불일치, 'today' 필터 오작동 |
| 25 | 🟡 Medium | `lattice_brain/embeddings.py:14` | `LATTICEAI_VECTOR_DIM` 을 한쪽 복제본만 읽음 — 임베딩 차원 분기 |
| 26 | 🟡 Medium | `latticeai/app_factory.py:1177` | 런타임 네임스페이스가 denylist-only — 모든 non-underscore 로컬 노출 |
| 27 | 🟡 Medium | `latticeai/api/chat.py:298` | `chat()` 388줄 거대 핸들러 + `/clear` 분기만 `notify_chat_message` 누락 |
| 28 | 🟡 Medium | `frontend/src/i18n.ts:44` | 버전 문자열 "8.6" 하드코딩 (실제 8.9.0) — 낡은 버전 표시 |
| 29 | 🟢 Low | `latticeai/api/chat.py:263` | 매 요청마다 `print("🧪 …")` stdout — 로깅/리댁션 우회 |
| 30 | 🟢 Low | `setup_wizard.py` | `auto_setup.py` 의 GPU/CUDA/WSL 탐지 스택 통째 중복 |
| 31 | 🟢 Low | `static/css/tokens.css` | 서로 호환 안 되는 토큰 시스템 2개가 각자 "단일 출처" 주장 |
| 32 | 🟢 Low | `frontend/src/App.tsx:303` | 공유 헬퍼 중복(`navigateHash`/`clamp`/`isRecord`×5) |
| 33 | 🟢 Low | `latticeai/core/review_queue.py:62` | `_parse_iso`/`_sha256_file` 중복 정의 (PLAUSIBLE) |

---

## 🔴 Critical — 접근 제어 / 데이터 노출

### 1. 텔레그램 봇에 chat-id 허용목록이 없음
- **위치**: `latticeai/integrations/telegram_bot.py:938-940`
- **문제**: `run_bot()` 업데이트 루프가 들어오는 모든 메시지를 무조건 처리하고 `register_chat_id(chat_id)` 로 자동 등록한다. 다른 모든 표면이 강제하는 `require_user`/`require_admin` 불변식이 봇에서는 전혀 재수립되지 않는다.
- **근거**: 파일·패키지 전체에 allowlist/allowed-chat 체크가 존재하지 않음(grep 무결과). 938–940행 `msg = update["message"]; chat_id = msg["chat"]["id"]; register_chat_id(chat_id)`.
- **영향(실패 시나리오)**: 낯선 사람이 봇 사용자명을 알아내 메시지 한 번만 보내면 (a) `/ss`(`/screenshot`)로 소유자 Mac 화면 캡처를 실시간 수신하고, (b) `[Web] …` 미러링 목록에 영구 구독되어 소유자가 웹 UI에 입력하는 모든 대화가 그 사람 텔레그램으로 방송된다. → 대화·화면 내용 조용한 유출.
- **권장 수정**: `TELEGRAM_ALLOWED_CHAT_IDS`(env) 화이트리스트를 두고 루프 진입 직후 검사, 미허용 chat-id는 즉시 무시. `register_chat_id` 는 소유자 인증 후에만.

### 2. 초대 게이트가 정적 쿠키를 신뢰
- **위치**: `latticeai/api/static_routes.py:62-74`
- **문제**: `authorized: Optional[str] = Cookie(None)` 를 평문으로 읽고, 68행 `if authorized == "true": return app_redirect(...)` 로 초대 코드 검증을 통과시킨다. 이 쿠키는 서버 세션에 묶이지 않은 정적·비서명 값이다.
- **근거**: 74행에서 쿠키를 정적 리터럴 `"true"` 로 설정. 서명/세션 바인딩 없음.
- **영향**: `INVITE_GATE_ENABLED` 공개 배포에서 초대 코드를 모르는 방문자가 devtools/curl로 `Cookie: authorized=true` 만 넣으면 게이트가 완전히 무력화된다. 오픈 등록과 결합하면 게이트가 배제하려던 임의 사용자가 유입.
- **권장 수정**: 서버측 세션에 초대 통과 상태를 저장하거나 HMAC 서명 쿠키(만료 포함)로 전환.

### 3. `local_read`/`local_list` 가 auto_approve
- **위치**: `latticeai/core/tool_registry.py:149-150` (`_r()` at 84-88, `auto_approve=True`)
- **문제**: 두 도구가 auto-approve read로 지정되어 에이전트 런타임·공유 정책 게이트가 승인 없이 실행한다. 반면 전용 HTTP 라우트(`/local/read`, `/local/list`)는 경로별 승인 토큰을 강제한다. 즉 도구 자체(`tools/local_files.py:34`, docstring "requires user approval via UI")는 아무 것도 강제하지 않는다.
- **영향**: 사용자의 질문이 플래너로 하여금 `~/.ssh/id_rsa` 나 `.env` 에 대한 `local_read` 를 emit하게 만들면, 모든 단계가 auto-approve라 plan이 'auto_approved'로 실행되어 개인키/API 시크릿(최대 2MB, 디스크 어디든)이 모델 컨텍스트로 읽혀 채팅에 표시된다. UI가 보장하는 승인 다이얼로그가 뜨지 않는다.
- **권장 수정**: `local_read`/`local_list` 를 `auto_approve=False` 로 바꾸고 에이전트 경로에서도 HTTP 라우트와 동일한 경로별 승인 토큰을 요구.

### 4. `run_command` 가 `../` 상대 탈출을 허용
- **위치**: `tools/commands.py:48-50`
- **문제**: `abs_args = [a for a in parts[1:] if a.startswith("/") and a not in ("/dev/null",)]` — 절대경로 인자만 거부한다. `../../../../Users/<user>/.ssh/id_rsa` 는 `.` 로 시작하므로 통과하며, 다른 어떤 체크도 인자 경로를 검증하지 않는다(`cwd` 만 `_resolve_*`로 confine).
- **문제 맥락**: `tools/__init__.py` 는 AGENT_ROOT가 모든 파일시스템 작업을 confine한다고 문서화하지만, allow-list 리더(cat, sed, head, tail, find, rg)가 이 샌드박스를 벗어난다.
- **영향**: 관리자(또는 사람이 승인한 plan 단계)가 `run_command('cat ../../../../…/.ssh/id_rsa')` 를 실행하면 워크스페이스 밖 파일 내용이 도구 출력/채팅으로 반환된다. 승인 결정의 전제였던 워크스페이스 격리 보장이 깨진다.
- **권장 수정**: 각 인자를 `_resolve_*` 로 정규화 후 AGENT_ROOT 하위인지 검사(`..` 포함 상대경로 및 심볼릭 링크 대상까지).

### 5. `GET /mcp/tools` 만 인증 가드 누락
- **위치**: `latticeai/api/mcp.py:109-110`
- **문제**: 핸들러 `async def mcp_tools():` 가 `request` 파라미터를 받지 않고 `require_user` 를 호출하지 않는다. 같은 라우터의 형제 라우트는 전부 호출(`/mcp/recommend`, `/mcp/installed`, `/mcp/custom` 등). 라우터 추출 과정에서 이 핸들러만 가드가 빠졌다.
- **영향**: 비인증 방문자가 `/mcp/tools` 요청 시 서버의 절대 워크스페이스 경로(`str(AGENT_ROOT)` — OS 사용자명/디렉터리 레이아웃 노출), 전체 도구 거버넌스 매트릭스, 설치된 MCP 통합 목록을 받는다. 다른 라우트는 모두 세션을 요구하는 정찰 정보다.
- **권장 수정**: 핸들러 시그니처에 `request: Request` 추가 후 `require_user(request)` 호출.

### 6. 워크스페이스 스코핑이 예외 시 fail-open
- **위치**: `lattice_brain/graph/retrieval.py:106-128`
- **문제**: `workspaces_of()` 가 `nodes_v2` SELECT의 모든 예외를 삼키고 `return {}`(110-111행). `filter_scoped_nodes` 는 스코프 맵에 없는 id를 legacy-global(모두에게 보임)로 취급(124-128행). 결과적으로 쿼리가 실패하면 스코핑이 조용히 비활성화된다(fail-open).
- **영향**: v2 프로젝션이 없거나 손상됐거나 SELECT가 어떤 이유로든 에러나는 DB에서, 워크스페이스 A 멤버가 `/knowledge-graph/search`·`/knowledge-graph/documents` 를 호출하면 워크스페이스 B의 문서·RAG 컨텍스트를 받는다. 아무에게도 에러가 표출되지 않는 교차 워크스페이스 데이터 노출.
- **권장 수정**: 예외를 삼키지 말고 fail-closed(빈 결과 반환 또는 에러 전파), 스코프 미상 id는 기본 비공개로.

---

## 🟠 High — 조용한 성공 / 오답 정확성 버그

### 7. 모델 미로딩 시 빈 파일을 만들고 "성공" 응답
- **위치**: `latticeai/api/chat.py:361, 381-387`
- **문제**: 콘텐츠 생성 분기가 `if content is None and router.current_model_id:` 로 가드되는데, 모델 미로딩 시 이 조건이 건너뛰어지고 381-382행 `if content is None: content = ""`, 384행 `execute_tool("write_file", …)` 로 빈 파일이 만들어진다. no-model 체크는 424행에서야 발생(이 분기 실행 후).
- **영향**: 모델을 안 띄운 사용자가 "report.md에 프로젝트 요약 만들어줘" 를 보내면, `is_file_action_request()` 는 true, `target_path` 는 해석되지만 `inline_file_action_content()` 가 None을 반환 → 빈 `report.md` 가 생성되고 "report.md 파일을 만들었습니다." 라고 응답. '모델을 로드하세요' 에러 대신 빈 파일 + 성공 메시지.
- **권장 수정**: no-model 체크를 파일 액션 분기 **이전**으로 옮기거나, `content == ""` 이면 쓰기 대신 에러 반환.

### 8. HTTP 오류에도 위임 "완료" 표시
- **위치**: `frontend/src/features/brain/BrainHome.tsx:87` (+ `frontend/src/api/base.ts`, `client.ts:384`)
- **문제**: `apiJson` 은 HTTP 오류에서 reject하지 않고 `{ ok: false, … }` 로 resolve한다. `latticeApi.runAgent` → `apiJson` 이므로 promise resolution을 성공으로 간주하는 `delegateMutation.onSuccess` 와 `handleProactiveAction` 의 `recordProactiveActivity(action, 'completed')` 가 409/403/400에서도 실행된다.
- **영향**: 에이전트 런타임 불가(409) 또는 정책 훅 차단(403) 상태에서 '에이전트에게 맡기기'를 누르면 백엔드는 run을 만들지 않는데도 UI는 'brain.delegate.done' 표시, proactive trail은 '완료' 로그, 'saved' 배지 렌더. 사용자 목표가 조용히 유실되면서 위임됐다고 보고.
- **권장 수정**: `runAgent` 결과의 `ok`/`status` 를 검사해 실패 시 onError 경로로 분기.

### 9. 예외 시 run이 영구 'running' 상태로 멈춤
- **위치**: `latticeai/services/run_executor.py:112-133`
- **문제**: `_run_agent` 는 `complete_reserved_run` 을 `try … finally: self._handles.pop(...)` 로만 감싸고 `except` 절이 없다. 반면 `_run_workflow` 는 `except Exception`(247행)에서 status="failed" + execution_failed 타임라인 이벤트를 남긴다. `complete_reserved_run` 은 내부적으로 `orchestrator.run` 만 가드하므로, run row 삭제 후 store 쓰기(`store.get_agent_run`/`update_agent_run`, 예: FileNotFoundError)가 detached asyncio 태스크로 예외를 던진다.
- **영향**: run의 durable row가 삭제되거나 워크스페이스 스코프가 실행 중 정리되면, 업데이트가 태스크 안에서 raise → 'Task exception was never retrieved' 로 삼켜지고 handle이 pop됨 → run이 Act 탭에서 결과·타임라인 실패 이벤트 없이 영구 'running'. 서버 재시작의 reconcile 패스까지 그대로. 사용자는 끝나지 않는 run을 보고 에러도 못 받음.
- **권장 수정**: `_run_workflow` 와 동일하게 `except Exception` 으로 status="failed" 기록 + 타임라인 실패 이벤트.

### 10. 네트워크 상태 요청 과탐지 → 무관한 질문에 IP 노출
- **위치**: `latticeai/api/chat_helpers.py:55-57`
- **문제**: `has_ip` 정규식 `(?<![a-z0-9])ip(?![a-z0-9])` 가 "ip가"의 'ip' 를 매칭(뒤 '가' 는 [a-z0-9] 밖). `asks_current` 가 흔한 단어 '뭐' 를 매칭.
- **영향**: "IP가 뭐야?" 같은 개념 질문에 `network_status()` 가 반환되어 실제 내부 IP·공인 IP·호스트명·인터페이스 목록을 노출. 물어보지도 않은 네트워크 정보 유출.
- **권장 수정**: 의도 탐지를 키워드 매칭 대신 더 좁은 패턴/명시적 트리거로. '뭐' 같은 범용어 제거.

### 11. URL 요청 과탐지 → "주소 알려줘"에 접속 URL 응답
- **위치**: `latticeai/api/chat_helpers.py:61-63`
- **문제**: `has_url` 이 "주소"/"링크"/"address" 를, `asks_current` 가 "알려"/"뭐" 등을 매칭. "우리 회사 주소 알려줘"(우편 주소)가 접속 URL 응답으로 오인된다.
- **영향**: 사용자가 물리 주소를 물었는데 현재 페이지/접속 URL을 답하는 오답.
- **권장 수정**: #10과 동일 근본 원인 — 의도 탐지 정교화.

### 12. 스트리밍 텍스트 이중 파싱
- **위치**: `latticeai/api/chat.py:740-742`
- **문제**: `elif isinstance(chunk, str) and "text='" in chunk:` → `clean_chunk = chunk.split("text='")[1]…`. 라우터(`latticeai/models/router.py:632`)는 이미 `.text` 를 추출한 평문 문자열을 yield하는데, 채팅 핸들러가 이를 다시 파싱하려 든다.
- **영향**: 정상 응답 텍스트에 `text='` 리터럴이 우연히 포함되면 출력이 잘못 잘린다. 라우터 표현이 바뀌면 조용히 깨지는 취약한 결합.
- **권장 수정**: 라우터가 이미 평문을 주므로 이 이중 파싱 분기를 제거.

---

## 🟡 Medium — 설정 파손 / 로직 버그

### 13. 봇이 평문 세션 키 재생 — v4 해시 저장 이후 항상 실패
- **위치**: `latticeai/integrations/telegram_bot.py:75-80`
- **문제**: `_get_server_session()` 이 `sessions.json` 의 키를 세션 토큰으로 반환하고 `_server_client` 가 `cookies = {"session_token": token}` 로 전송. 그러나 `latticeai/core/sessions.py` 는 키를 해시로 저장(`_hash_token(token)`)하고 `SessionStore._get_entry` 는 쿠키를 재해시하므로, 재생된 해시가 어떤 세션과도 매칭되지 않는다(게다가 entry[0]도 이제 subject가 아님).
- **영향**: 봇의 서버 API 인증이 항상 실패 → 봇 기능(채팅/에이전트 릴레이 등)이 조용히 동작 불능.
- **권장 수정**: 봇용 서비스 토큰 발급 경로를 별도로 두거나, 세션 저장/조회와 동일한 해시 규약을 사용.

### 14. `BASE_URL` 하드코딩, env 오버라이드 없음
- **위치**: `latticeai/integrations/telegram_bot.py:29-39` (+ 372/378 직접 f-string)
- **문제**: `BASE_URL = "http://127.0.0.1:4825"` 리터럴에서 모든 봇 엔드포인트(CHAT/AGENT/STATUS/MODELS/GRAPH_STATS/UPLOAD_DOC/AGENT_RESUME URL)가 파생. env 오버라이드가 전혀 없다.
- **영향**: 서버 포트/호스트가 기본과 다르면 봇 전체가 파손. 배포 유연성 없음.
- **권장 수정**: `LATTICEAI_BASE_URL`(또는 기존 포트 설정)에서 읽도록.

### 15. 만료 정리 루프가 `key` 변수를 리바인딩
- **위치**: `latticeai/api/permissions.py:217-224`
- **문제**: `key = self.token_hash(token)` 후 lock 안에서 `expired = [key for key, value in … if …]` 리스트 컴프리헨션의 `key` 가 외부 `key` 를 덮어쓴다(파이썬 3에서 컴프리헨션 변수는 누수 안 되지만, 이어지는 `for key in expired: … pop(key)` 루프가 확실히 리바인딩). 이후 `record = self.local_approvals.get(key)` 가 요청자 토큰 해시가 아닌 마지막 만료 키로 조회.
- **영향**: 만료 항목 정리가 발생한 뒤, 유효 토큰으로 재시도한 사용자의 승인 조회가 잘못된 키로 이뤄져 승인이 거부된다.
- **권장 수정**: 루프 변수명을 `expired_key` 등으로 분리해 요청자 `key` 를 보존.

### 16. `_notify_discord_permission_sync` 호출부 없음
- **위치**: `latticeai/api/permissions.py:92`
- **문제**: 정의는 있으나 리포지토리 전체에 호출부가 없음(다른 매치는 `src-tauri/target/` 빌드 산출물뿐). 요청 경로 `local_permission_response`(169행)는 `self._perm_queue_write(...)`(184행)만 호출하고 notifier를 부르지 않는다.
- **영향**: 의도된 Discord 승인 동기화 알림이 절대 발송되지 않는 죽은 코드 경로. 기능 공백 또는 미완성 흔적.
- **권장 수정**: 실제로 필요하면 응답 경로에서 호출, 아니면 제거.

### 17. fire-and-forget `create_task` — 참조 유실 가능 (PLAUSIBLE)
- **위치**: `latticeai/api/chat.py:871`
- **문제**: `asyncio.create_task(_AGENT_RUNTIME.memory_update(ctx, req, current_user))` 의 반환 Task를 어디에도 저장하지 않는다(강한 참조·done 콜백 없음). 이벤트 루프는 약한 참조만 보유하므로, 완료 전 GC되면 메모리 업데이트가 조용히 취소될 수 있다(문서화된 asyncio 함정).
- **영향**: 부하 상황에서 메모리 업데이트가 간헐적으로 유실될 수 있음(재현 조건이 타이밍 의존이라 PLAUSIBLE).
- **권장 수정**: Task를 set에 보관하고 done 콜백에서 제거, 또는 예외 로깅 래퍼.

---

## 🟢 Low — 정리 / 효율 / 레이어링

### 18. 감사 로그가 매 append 마다 전체 재파싱·재작성 (O(n²))
- **위치**: `latticeai/runtime/audit_runtime.py:25, 41-50`
- **문제**: `with _audit_lock: events = _read_audit(); events.append(entry); tmp.write_text(json.dumps(events, …, indent=2)); tmp.replace(audit_file)`. 매 append가 전체 파일을 `json.loads` 로 재파싱하고 pretty-print로 통째 재작성.
- **영향**: 감사 이벤트가 쌓일수록 append 비용이 O(n)으로 증가 → 전체 O(n²), 파일 성장 시 지연·I/O 급증.
- **권장 수정**: JSONL append-only 포맷으로 전환하거나, 인메모리 버퍼 + 주기적 flush.

### 19. 모든 기동마다 무조건 마이그레이션 재실행
- **위치**: `latticeai/app_factory.py:460-475`
- **문제**: `load_users()` 가 호출마다 `migrate_knowledge_graph_identity(...)`(468행)와 `WORKSPACE_OS.migrate_workspace_identities(...)`(472행)를 게이트 없이 실행. 마이그레이션은 매번 DB/파일을 열어 스캔.
- **영향**: 기동 지연, 불필요한 I/O. 규모가 커지면 눈에 띄는 startup 비용.
- **권장 수정**: schema version/marker로 idempotent 게이트를 두어 이미 마이그레이션된 경우 건너뛰기.

### 20. `_atomic_write_json` 세 곳 중복 정의
- **위치**: `latticeai/core/users.py:21`, `latticeai/core/workspace_os_utils.py:46`, 그리고 `latticeai/core/…`(세 번째)
- **문제**: 거의 동일한 원자적 JSON 쓰기 헬퍼가 세 곳에 중복(`tmp.replace(path)` / `os.replace(tmp_path, path)`).
- **영향**: 유지보수 부담·드리프트 위험(한 곳만 고쳐지는 버그).
- **권장 수정**: 공용 유틸(`core/io_utils.py` 등)로 단일화.

### 21. 계층화된 패키지가 루트 모듈을 직접 import (레이어링 누수)
- **위치**: `latticeai/api/local_files.py:18` (외 `latticeai/runtime/hooks_runtime.py:24`, `latticeai/app_factory.py:204`)
- **문제**: 패키지화된 코드가 루트 레벨 모듈을 직접 import(`from local_knowledge_api import …`, `from setup_wizard import …`). 패키지 경계를 넘나드는 의존.
- **영향**: 모듈 이동/패키징 시 취약, import 순서/경로 의존성 증가.
- **권장 수정**: 루트 모듈을 적절한 하위 패키지로 이동하거나 명시적 재노출(re-export) 경유로 경계 정리.

---

## 추가 확정 항목 (재개 실행에서 검증 완료, #22~#33)

> 1차 실행에서 세션 한도로 중단됐던 12개 검증을 재개해 모두 CONFIRMED/PLAUSIBLE 판정을 받은 항목이다. 주로 `chat.py` 관련 정확성·효율 문제와 중복/드리프트성 정리 항목이다.

### 22. 🟠 스트리밍 루프에 try/except가 없어 중간 예외 시 히스토리·트레이스 유실
- **위치**: `latticeai/api/chat.py:726-763` (`_stream_chat`, `async for chunk in router.stream_generate(...)` @ 736)
- **문제**: 스트리밍 루프에 try/except가 없고 그대로 `StreamingResponse`(614-625)로 전달된다. `save_to_history`(749), `record_trace`(750-760), trace 프레임(762), `yield "data: [DONE]\n\n"`(763)이 **모두 루프 완료 후에만** 실행되므로, 스트림 중간에 예외가 나면 이들이 전부 건너뛰어지고 SSE 연결이 끊긴다. 프론트(`client.ts:247`)는 `done`으로 처리해 조용히 종료.
- **영향**: 모델이 생성 도중 실패하면 대화가 히스토리에 저장되지 않고(지식그래프 ingest 누락), 트레이스도 안 남고, 사용자는 잘린 응답만 본다. 부분 출력이 유실되고 원인 파악도 어렵다.
- **권장 수정**: 스트리밍 루프를 try/except/finally로 감싸 예외 시 오류 이벤트를 yield하고 finally에서 히스토리·트레이스·`[DONE]`을 보장.

### 23. 🟡 async 핸들러가 동기 I/O를 직접 호출해 이벤트 루프 블로킹
- **위치**: `latticeai/api/chat.py:264` (`async def chat`), `app_factory.py:485/531`, `lattice_brain/ingestion.py:181/344`
- **문제**: `chat()`(async)이 동기 함수 `save_to_history(...)`(282/287/345-346/544/573)를 await/executor 없이 직접 호출한다. `save_to_history` → `INGESTION_PIPELINE.ingest(...)` 는 완전 동기(SQLite 쓰기 포함, `ingestion.py:344`).
- **영향**: 각 채팅 요청이 SQLite 쓰기 + 임베딩 인덱싱 동안 이벤트 루프를 블로킹해, 동시 요청·스트리밍 응답의 지연/스톨을 유발한다.
- **권장 수정**: `await run_in_executor(...)` 로 오프로딩하거나 ingest를 백그라운드 큐로 분리.

### 24. 🟡 감사 타임스탬프가 naive `datetime.now()` — 대시보드 'today' 필터 오작동
- **위치**: `latticeai/runtime/audit_runtime.py:31`
- **문제**: `timestamp: datetime.now().isoformat()` (naive, 시스템 로컬). 이 감사 싱크는 앱 전역용이며, `security_dashboard.py:297-298` 이 `today = timezones.today_str()`(LATTICE_TZ)와 `str(e.get('timestamp',''))[:10]` 를 비교한다.
- **영향**: UTC 서버 + `LATTICE_TZ=Asia/Seoul` 환경에서 UTC 날짜 접두사가 서울 'today'와 어긋나, 보안 대시보드의 오늘 감사 이벤트 집계가 틀린다. (메모리의 LATTICE_TZ 시간대 통일 원칙과 배치되는 잔여 버그.)
- **권장 수정**: 다른 시간 처리와 동일하게 LATTICE_TZ aware timestamp를 기록.

### 25. 🟡 `LATTICEAI_VECTOR_DIM` 을 한쪽 복제본만 읽어 임베딩 차원 분기
- **위치**: `lattice_brain/embeddings.py:14` vs `latticeai/core/local_embeddings.py:22-24`
- **문제**: `lattice_brain/embeddings.py` 는 import 시 `DEFAULT_EMBEDDING_DIM = int(os.getenv("LATTICEAI_VECTOR_DIM", "384"))` 로 env를 읽는데, `core/local_embeddings.py` 는 "7.6.0 config centralization" 주석과 함께 `DEFAULT_EMBEDDING_DIM = 384`(고정)이다. 나머지 모듈 본문은 두 파일이 그대로 복제.
- **영향**: `LATTICEAI_VECTOR_DIM=256` 설정 시 한 임베딩 모델은 256, 다른 하나는 384로 갈려 벡터 차원 불일치 → 검색/인덱싱 오류 또는 조용한 품질 저하.
- **권장 수정**: 두 복제본을 단일 모듈로 통합하고 설정을 한 곳에서만 읽도록.

### 26. 🟡 런타임 네임스페이스가 denylist-only — 모든 non-underscore 로컬 노출
- **위치**: `latticeai/app_factory.py:1177` (`build_runtime_namespace(locals(), ...)`), `namespace_runtime.py:139-148`
- **문제**: 함수 스코프 전체(`locals()`)를 넘기고, 네임스페이스 빌더가 denylist(`INTERNAL_RUNTIME_NAMES`/모듈 타입/underscore)만 걸러 나머지 non-underscore 로컬을 전부 `exported`로 노출한다. `runtime.__dict__.update(self.namespace)` + `server_app.py:23-33` 의 `getattr` 위임으로 광범위하게 재노출.
- **영향**: 의도치 않은 내부 변수가 런타임 표면으로 새어 나가고, 새 로컬을 추가할 때마다 무심코 공개될 위험(allowlist가 아니라 denylist라 기본이 "노출").
- **권장 수정**: allowlist 기반 export로 전환하거나 명시적 export 목록을 유지.

### 27. 🟡 `chat()` 388줄 거대 핸들러 + `/clear` 분기만 `notify_chat_message` 누락
- **위치**: `latticeai/api/chat.py:264-651` (특히 `/clear` 분기 298-341)
- **문제**: `chat()` 이 6개 인라인 의도 분기를 담은 ~388줄 핸들러로, 각 분기가 동일한 `save_to_history` + `notify_chat_message` + stream/JSONResponse 에필로그를 반복 복붙한다. 그런데 `/clear` 분기에는 `notify_chat_message` 호출이 아예 없다.
- **영향**: (버그) `/clear` 액션은 다른 분기와 달리 알림 미러링이 누락되어 동작이 비일관적이다. (유지보수) 반복 에필로그는 한 곳만 고쳐지는 드리프트를 부른다.
- **권장 수정**: 공통 에필로그를 헬퍼로 추출하고 `/clear` 포함 모든 분기가 동일 경로를 타도록.

### 28. 🟡 UI 버전 문자열 "8.6" 하드코딩 (실제 8.9.0)
- **위치**: `frontend/src/i18n.ts:44`(ko), `:1033`(en)
- **문제**: `brain.edition.tip` 이 ko/en 모두 "Lattice AI 8.6 …" 로 하드코딩. `package.json`·`pyproject.toml` 은 `8.9.0`. 이 tip은 `/health` 에서 보간되지 않는 정적 문자열이라, 8.9.0 빌드의 모든 사용자가 낡은 8.6 버전 표기를 본다.
- **영향**: 사용자 대면 버전 오표기. (프로젝트의 "버전 단일 출처" 원칙 위반.)
- **권장 수정**: 버전을 `/health` 등 단일 출처에서 주입하거나 빌드 시 치환.

### 29. 🟢 매 요청마다 `print("🧪 …")` stdout 디버그 출력
- **위치**: `latticeai/api/chat.py:263-271`
- **문제**: `@api_router.post("/chat")` 핸들러가 `require_user`/`enforce_rate_limit` 이후 `print(f"🧪 /chat request: …")` 를 무조건 실행. `logging` 이 아닌 `print` 라 로그 레벨 제어·리댁션 필터를 우회한다.
- **영향**: 운영 로그 오염 + 메시지 길이/이미지 여부 등이 무필터로 stdout에 남음(민감정보 리댁션 우회 소지).
- **권장 수정**: `logging.debug` 로 교체하거나 제거.

### 30. 🟢 `setup_wizard.py` 가 `auto_setup.py` 탐지 스택을 통째 중복
- **위치**: `setup_wizard.py` (`_parse_windows_video_controllers`:414, `_detect_gpu`:451, `_detect_cuda`:516, `_detect_wsl`:531, `_detect_tools`:544 등) vs `auto_setup.py`(156/194/296/287/278)
- **문제**: GPU/CUDA/WSL/툴 탐지 로직이 두 진입점에 병렬로 거의 동일하게 존재.
- **영향**: 두 구현이 갈라져 탐지 결과가 달라질 수 있음(AGENTS.md의 중복 비즈니스 로직 금지 위반).
- **권장 수정**: 탐지 스택을 공용 모듈로 단일화.

### 31. 🟢 호환 안 되는 토큰 시스템 2개가 각자 "단일 출처" 주장
- **위치**: `static/css/tokens.css`(v3.3.1, "단일 출처") vs `frontend/src/styles/tokens.css`(HSL triple 시스템)
- **문제**: 전자는 색·면·테두리·그림자·포커스의 단일 출처라 선언하지만, 후자는 `hsl(var(--token))` 로 소비되는 구조적으로 호환 안 되는 별도 토큰 시스템(ink/jade, shadcn식 이름)을 정의한다.
- **영향**: 두 소스가 공존해 어느 쪽이 실제 적용되는지 모호하고, 테마 변경 시 한쪽만 반영되는 드리프트.
- **권장 수정**: SPA(`/app`)와 정적 프론트의 토큰 출처를 명확히 분리·문서화하거나 하나로 수렴.

### 32. 🟢 프론트 공유 헬퍼 중복
- **위치**: `frontend/src/App.tsx:303`(`navigateHash`), `:319`(`clamp`), `isRecord`(AdminConsole.tsx:323/brainData.ts:320/primitives.tsx:98/System.tsx:444/graphExplorer.ts:64)
- **문제**: `navigation.ts:2` 의 공유 export와 동일한 `navigateHash`, `graphLayout.ts:35` 의 `clamp`, 5곳에 흩어진 `isRecord` 가 import 대신 복제.
- **영향**: 공유 헬퍼를 고쳐도(예: `navigateHash` 에 `history.replaceState` 추가) 복제본은 낡은 채 남음.
- **권장 수정**: 공유 모듈에서 import하도록 통일.

### 33. 🟢 `_parse_iso`/`_sha256_file` 중복 정의 (PLAUSIBLE)
- **위치**: `_parse_iso` @ `review_queue.py:62`, `workspace_os_utils.py:103`, `_kg_fsutil.py:31`; `_sha256_file` @ `archive.py:74`, `portability.py:47`
- **문제**: `_parse_iso`(byte-identical 3곳)와 `_sha256_file`(2곳)이 동일 복제. (후보의 "`_tokenize` 4곳" 주장은 과장 — 일부만 동일해 PLAUSIBLE.)
- **영향**: 유지보수 드리프트(예: `_parse_iso` 가 'Z' 접미사를 처리 못하는 점을 한 곳만 고칠 위험).
- **권장 수정**: 공용 유틸로 단일화.

---

## 참고 — 검증에서 기각(refuted)된 항목

- `tools/local_files.py:56` — `local_write` 의 blocked-prefix 체크가 형제 디렉터리를 과차단한다는 주장. 검증 결과 prefix들이 모두 슬래시 종료(`"/System/"` 등)라 `"/System-backups/…".startswith("/System/")` 는 False이고 `== "/System"` 도 아니므로 과차단이 발생하지 않음 → **기각**.

## 이번 실행에 대해

- **검증 완료**: 후보 34건에 대한 검증 에이전트가 모두 완료(재개 실행에서 40 에이전트, 에러 0)되어 **33건 CONFIRMED/PLAUSIBLE, 1건 REFUTED** 로 판정이 확정됐다. 1차 실행에서 세션 한도로 중단됐던 12개(#22~#33)를 재개해 마저 확인했다.
- 라인 번호는 검증 시점 코드 기준이며, 수정 과정에서 파일이 바뀌면 재확인이 필요하다.

## 권장 처리 순서

1. **즉시**: #1~#6 (접근 제어/데이터 노출). 특히 #1 텔레그램 봇과 #3/#4 로컬 파일 접근은 개인키·화면·대화 유출로 직결.
2. **다음**: #7~#12, #22 (조용한 성공/오답/스트림 유실). 사용자가 잘못된 결과를 신뢰하거나 데이터가 소실되는 정확성 버그.
3. **그 다음**: #13~#17, #23~#28 (설정·로직·효율). 봇 인증(#13)은 봇을 아예 무력화하므로 봇을 쓴다면 우선순위 상향. #24 감사 시간대, #25 임베딩 차원, #26 네임스페이스 노출은 조용히 잘못된 동작을 유발.
4. **여유 시**: #18~#21, #29~#33 (효율·정리·레이어링·중복 제거).
