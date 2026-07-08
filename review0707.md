# Lattice AI 전체 코드 리뷰 (2026-07-07)

리뷰어: Claude (backend/보안/아키텍처 관점)
대상 버전: 8.9.0 (main 브랜치, 커밋 `5eaed49`)
범위: Python 백엔드(`latticeai/`, `lattice_brain/`, `tools/`), 프런트엔드(`frontend/`), 설치/설정 스크립트, 레포 위생, 테스트/빌드.
검증: `ruff check` 클린, `pytest` 820 passed / 10 skipped. 주요 위험 파일은 라인 단위 정독.

---

## 적용 현황 (2026-07-07, 이 리뷰 직후 반영)

**구조 부채**
- §2.2 ✅ app_factory의 중복 로컬-승인 헬퍼 6개(`_local_approvals` 등)를 제거하고, 이를 참조하던 `test_security.py`를 실제 `PermissionGateway`로 이관. 이제 로컬 파일 승인은 `PermissionGateway` 단일 출처. 미사용이 된 `hashlib`/`secrets`/`time` import도 정리.
- §2.4 ✅ `LLMRouter`에 재진입 락(`threading.RLock`) 도입. `switch_model`/`unload_*`/`_enforce_local_model_limit`/`load_model`(동기 캐시 read·insert)/`_load_cloud_model`의 임계구역을 보호. 무거운 `run_in_executor` 로딩의 `await` 구간엔 락을 걸지 않아 동시 스위치/언로드가 블록되지 않음.
- §2.1 ✅ `app_factory._build`에서 코헤런트 블록을 런타임 시임으로 추출: VPC 설정 → `runtime/network_config_runtime.py`, history 질의/삭제 블록(scope 해석·`get_history`·grouping·clears) → `runtime/history_runtime.py`, **SSO 설정+OIDC discovery → `runtime/sso_config_runtime.py`**(구 `sso_runtime.py` 삭제). `app_factory.py` 1,509 → **1,274줄**, 미사용이 된 `json`/`re`/`secrets`/`hashlib`/`time` import도 정리. SSO 통합 시 `save_sso_config`가 discovery 캐시를 **실제로 무효화**하도록 캐시를 한 클로저로 공유 — 기존 no-op 잠재 버그 해소. `save_to_history`(쓰기 경로)는 redaction/audit/ingestion late-binding 결합이라 인라인 유지.
- §2.1 추가 ✅ 사용자 프로필/API 키 helper(`get_history_user`, `get_user_api_key`, `set_user_api_key`)를 `runtime/user_key_runtime.py`로 추출. keyring 우선순위, plaintext fallback 정책, keyring 저장 후 legacy plaintext 제거, 신규 사용자 identity 생성 경로를 `tests/unit/test_runtime_user_key.py`로 고정.
- §2.3 ✅ KG 그래프 계층을 관심사별로 분해(모두 mixin 조합/re-export 패턴, 외부 호출 표면 무영향, 820 테스트 green):
  - `_kg_common.py` **1,101 → 666줄**: 순수 상수 → `_kg_constants.py`, 파일/경로/해시/분류 유틸 21개 → `_kg_fsutil.py`. 남은 것은 텍스트/NLP extraction(concept·triple, LLM+규칙)뿐. 선형 의존(`_kg_constants` ← `_kg_fsutil` ← `_kg_common`), `_kg_common`의 computed `__all__`이 언더스코어 이름까지 downstream 전파.
  - `retrieval.py` **1,380 → 1,008줄**: 벡터 인덱스 메서드군(`_iter_vector_source_items`/`rebuild_vector_index`/`index_status`/`vector_search`) → `retrieval_vector.py`의 `KnowledgeGraphVectorMixin`.
  - `discovery.py` **1,455 → 507줄**: 로컬파일→그래프 인덱싱군(text 추출·node/index upsert·삭제·orphan cleanup·`index_local_folder`) → `discovery_index.py`의 `KnowledgeGraphLocalIndexMixin`. 남은 discovery는 스캔/미리보기/감사/소스관리.
  - 세 mixin 모두 `store.py`의 `KnowledgeGraphStore` bases에 조합되어 `self._vector_text_for_node` 등 형제 헬퍼가 MRO로 해결됨.
  - 추가 라운드(정적 데이터/순수 함수 분리, 동일 재노출 패턴):
    - `mcp_registry.py` **930 → 491줄**: 내장 MCP 서버 카탈로그(`MCP_REGISTRY`, 442줄) → `mcp_catalog.py`.
    - `retrieval.py` **1,008 → 810줄**: 문서생성용 멀티홉 검색군(`search_for_document_generation`/`multi_hop_context`) → `retrieval_docgen.py`의 `KnowledgeGraphDocGenMixin`(store.py 조합).
    - `chat.py` **1,080 → 930줄**: 순수 헬퍼/의도감지 함수 14개(language/intent 감지, 파일액션 파싱, recent-context 조립) → `chat_helpers.py`, `__all__`로 재노출 표면(테스트·app_factory) 보존.
    - `router.py` **918 → 825줄**: 클라우드 provider 카탈로그(`OPENAI_COMPATIBLE_PROVIDERS`/`PROVIDER_MODEL_CATALOG`/`MODEL_SOURCE_BY_FAMILY`) → `model_providers.py`, 교차모듈 소비처 위해 router에서 재노출.
  - 남긴 것: `workspace_os.py`(1,391)는 이미 `WorkspaceSnapshots`/`WorkspaceMemory`/`WorkspaceRuns`/`WorkspaceSkills` 협력 클래스로 위임하는 파사드라 추가 분할은 가치↓(mixin/컴포지션 혼용도 부적절). `model_runtime.py`(전역 상태 호환 계층), `telegram_bot.py`(스트리밍/통합), `discovery_index.py`(962줄이나 "로컬파일→그래프 인덱싱" 단일 관심사)는 분할 위험이 가치보다 커 현행 유지 권고.

**레포 위생**
- §4.1 ✅ `ltcai-0.3.1/`(구버전 사본, 3.1M) 제거.
- §4.2 ✅ `UNKNOWN.egg-info`, `.build-venv`(23M), 중복 `venv/`(1.0G, 활성 `.venv`는 보존), `build/`, `ltcai-8.9.0.tgz`(2.1M) 제거 — 약 1GB 회수, 모두 재생성 가능.
- §4.3 ✅ `git gc --prune=now`로 `.git` garbage object 9개 정리(garbage: 0).
- §4.4 ✅ `.env` 권한 `600`, `verification_report.json`은 `git rm --cached` + `.gitignore` 추가(런타임 산출물).
- §4.5 ⏸ (미적용) RELEASE_NOTES/리뷰 문서 디렉터리 재배치는 tracked 파일 대량 rename이라 참조 깨짐 위험이 있어 보류. 원하면 별도로 진행.

검증: 변경 후 `ruff` 클린, `pytest` 820 passed / 10 skipped 유지, import-safe(`import latticeai.app_factory` 부작용 없음) 회귀 없음.

---

## 0. 요약 (TL;DR)

8.9.0은 기반이 탄탄하다. import-safe 앱 팩토리, ToolRegistry 단일화, 세션·승인 토큰 hash-at-rest, KG v2 workspace 스코핑, durable conversation store, 820개 통과 테스트까지 — "데모"를 훨씬 넘어선 상태다. 지난 `docs/CODE_REVIEW_2026-07-06.md`에서 지적된 스코프/정책 항목 상당수가 실제로 반영됐다.

지금 남은 문제는 **새 기능 부재가 아니라 세 가지 구조적 부채**다:

1. **`app_factory._build`가 1,450줄 단일 함수** — 조립 루트가 여전히 거대하고 `dict(locals())`로 네임스페이스 전체를 export하는 레거시 호환 방식이라 테스트·추론이 어렵다.
2. **정책 게이트가 진입점별로 불균일** — `/tools/*`는 `enforce_tool_policy`를 통과하지만 `/cu/*` computer-use 직접 라우트는 정책 게이트를 우회한다(의도적 제외지만 문서/코드 경고가 약함).
3. **레포 위생** — `ltcai-0.3.1/`(구버전 전체 사본), `.env`(git-untracked지만 존재), 빌드 산출물, `venv/`+`.venv/`+`.build-venv/` 3중 가상환경, `.git` garbage objects 등 노이즈가 크다.

아래는 심각도 순으로 정리한 세부 항목이다.

---

## 1. 보안 / 정확성 (High)

### 1.1 `/cu/*` computer-use 라우트가 tool policy 게이트를 우회 — 🔴 High
`latticeai/api/computer_use.py`의 `/cu/click`, `/cu/type`, `/cu/key`, `/cu/open_url`, `/cu/drag` 등은 `require_user(request)`만 통과하면 `tool_response(computer_click, ...)`로 **직접** 실행된다. `/tools/*` 라우트가 거치는 `enforce_tool_policy(...)`(risk/destructive/auto_approve/admin 체크)를 타지 않는다.

즉 일반 로그인 사용자가 실제 마우스/키보드/앱 실행을 정책 승인 없이 구동할 수 있다. `tool_dispatch.enforce_policy`에 남긴 주석("Computer Use direct endpoints can remain unchanged when explicitly excluded")대로 **의도된 제외**지만, 위험도에 비해 방어가 약하다.

**개선:**
- 최소한 `/cu/*` 전체를 `require_admin` 또는 별도 명시적 opt-in 플래그(`LATTICEAI_ENABLE_COMPUTER_USE`) 뒤로 보낸다. 지금은 `require_user`만으로 열려 있다.
- `computer_*` 툴들도 `enforce_tool_policy(..., source="computer_use")`를 태워서 `/tools/*`와 동일한 게이트를 공유하게 한다(`_dispatch`는 hook lifecycle만 통과, 정책은 통과 안 함).
- 코드에 "정책 미적용 경로" 배너 주석 + `append_audit_event`로 모든 cu 액션을 감사 로그에 남긴다(현재 `/cu/agent`의 chrome 분기만 history 저장, 개별 액션은 감사 없음).

### 1.2 `local_write`/`local_read`/`local_list`의 경로 방어가 승인 게이트에만 의존 — 🟠 Medium-High
`tools/local_files.py`의 `local_write`는 `Path(path).expanduser().resolve()` 후 **아무 경로에나** 쓴다. 워크스페이스 샌드박스(`_resolve_path`)를 쓰지 않는다. 안전장치는 오직 상위 라우트(`create_local_files_router`)의 `permission_gateway.require_local_approval` + `LOCAL_WRITE_BLOCKED_PREFIXES`뿐이다.

`tools.execute_tool("local_write", ...)`를 라우트 밖(에이전트 런타임·훅·이후 리팩터)에서 호출하면 승인 게이트 없이 임의 파일 쓰기가 된다. 방어가 "호출 지점"에 흩어져 있어 깨지기 쉽다.

**개선:** blocked-prefix 검사와 승인 요구를 `local_write`/`local_read` **함수 자체**(또는 `ToolRegistry` governance)로 끌어내려, 어느 진입점에서 부르든 동일하게 강제한다. 라우트는 그 위에 승인 UX만 얹는다.

### 1.3 `md5` 사용 (quality.py) — 🟡 Low (보안 아님, 명확성)
`lattice_brain/quality.py:44,169`에서 `hashlib.md5(...)`로 콘텐츠 지문/중복 판정. 보안 용도는 아니지만(단순 dedup) 정적 스캐너·감사에서 계속 플래그된다.

**개선:** `hashlib.sha256(...)[:N]`으로 교체하거나 `hashlib.md5(..., usedforsecurity=False)`(Py3.9+)를 명시해 의도를 못박는다.

### 1.4 예외 삼킴 348곳 — 🟠 Medium (경계에서만 문제)
`except Exception` 348회. 대부분 "UX를 깨지 않기 위한" 로깅-후-계속이라 이해되지만, **인증/권한/데이터 무결성 경계**에서 광범위 삼킴은 실패를 은폐한다. 예: `app_factory.load_users`의 KG identity migration이 조용히 skip되면 신원 매핑이 어긋난 채 진행된다.

**개선:** 보안·정합성 경계 함수는 구체 예외로 좁히고, 삼킬 때도 `append_audit_event`로 흔적을 남긴다. 나머지 UX 경계는 그대로 두되 `logging.exception`(스택 포함)으로 격상.

### 1.5 `_stream_chat`의 텍스트 파싱 취약 (chat.py:890) — 🟡 Low
`chunk.split("text='")[1].split("', token=")[0]...`으로 스트리밍 청크에서 텍스트를 문자열 파싱한다. 모델 출력에 `text='`/`', token=`가 들어오면 깨진다. `except: pass`로 감싸 무증상 오작동이 된다.

**개선:** 라우터 스트림이 구조화된 객체(`chunk.text`)를 항상 내도록 상류에서 정규화하고, 문자열 스니핑 폴백은 제거한다.

---

## 2. 아키텍처 / 유지보수성 (High-value)

### 2.1 `app_factory._build`가 1,450줄 단일 함수 — 🔴 구조 부채 1순위
`_build(config)` 하나가 MLX init → config → 30+ 싱글턴 → 15+ 라우터 등록까지 전부 하고 마지막에 `return dict(locals())` 한다. 그리고 `AppRuntime`이 그 dict를 `__dict__.update`로 흡수해 레거시 `server_app.X` 속성을 복원한다.

문제:
- **테스트 격리 불가**: 일부만 조립해서 검증할 수 없다. 하나 건드리면 전체 `build_runtime()`.
- **암묵적 의존**: `locals()` export라 "누가 이 심볼을 쓰는지" 정적으로 안 보인다(`# noqa: F401 legacy attr` 주석이 곳곳에 필요한 이유).
- **순서 결합**: SSO cache가 `get_sso_settings` 정의 이후에만 build되는 식의 순서 의존이 함수 본문에 숨어 있다.

**개선(점진적):**
- 이미 `build_*_runtime` 시임들이 잘 뽑혀 있다. 남은 인라인 정의(`verify_and_migrate_password`, `load_users`, `save_to_history`, `_local_permission_response` 등)를 `latticeai/runtime/` 또는 서비스 계층으로 마저 옮긴다.
- `dict(locals())` export를 **명시적 dataclass**(`AppRuntimeNamespace`)로 교체해 export 표면을 타입으로 고정한다. 테스트가 참조하는 `_agent_risk` 등 legacy 심볼은 그 dataclass 필드로 승격.
- 목표: `_build`를 "시임 호출 + 라우터 등록"만 남은 200~300줄로.

### 2.2 `_local_approvals`/`_local_permission_response`/`_require_local_approval`가 app_factory에 죽은 채 중복 — 🟠 Medium
`app_factory.py:793-847`에 로컬 승인 로직이 통째로 정의돼 있는데, 실제 라우트는 `permissions.PermissionGateway`(별도·더 완성된 구현: 파일 큐, Discord 알림, hash-at-rest)를 쓴다. `grep` 결과 app_factory 쪽 `_local_approvals`는 **어디서도 참조되지 않는다**(정의만 있고 소비 없음).

**개선:** `app_factory.py`의 `_LOCAL_APPROVAL_TTL_SECONDS`, `_local_approvals`, `_normalize_local_path_for_approval`, `_content_fingerprint`, `_local_permission_response`, `_require_local_approval` 6개를 삭제한다. `PermissionGateway`가 단일 출처. (단, `dict(locals())` export 때문에 테스트가 이 이름을 잡고 있는지 먼저 `grep tests/` 확인 필요 — 없으면 바로 제거.)

### 2.3 거대 파일 4개 — 🟠 Medium
| 파일 | 라인 | 혼재된 책임 |
| --- | ---: | --- |
| `lattice_brain/graph/discovery.py` | 1,455 | KG discovery 전반 |
| `latticeai/core/workspace_os.py` | 1,391 | traces/snapshots/memories/agents/workflows 저장소 |
| `lattice_brain/graph/retrieval.py` | 1,380 | 조회 + 스코프 필터 + 랭킹 |
| `latticeai/services/model_runtime.py` | 1,166 | 전역 상태 호환 계층 |

`retrieval.py`는 특히 `filter_scoped_nodes`(스코프)와 `search`/`context_for_query`/`neighbors`(조회·랭킹)가 한 클래스에 섞여 있다.

**개선:** `workspace_os.py`를 리소스별(TracesRepo/SnapshotsRepo/MemoriesRepo/AgentsRepo)로 분해. `retrieval.py`는 스코프 필터를 mixin/전용 모듈로 분리해 랭킹 로직과 테스트를 독립시킨다. 급하진 않으나 신규 기능 추가 시마다 비용이 누적된다.

### 2.4 `LLMRouter` 동시성 미보호 — 🟠 Medium
`LLMRouter`(`models/router.py`)의 `_cache`/`_current`/`_last_used`는 **락 없이** 여러 async 요청이 공유한다. `switch_model`이 `self._current`를 갈아끼우는 도중 다른 요청이 `generate_as(current_model_id)`를 읽으면 레이스가 난다. `chat.py`에서 요청마다 `router.switch_model(req.model)`을 호출하므로 동시 채팅에서 서로의 모델을 바꿀 수 있다.

**개선:** 모델 스위칭/로드/언로드에 `asyncio.Lock`(또는 `threading.RLock`) 도입. 최소한 `generate_as(model_id=...)`처럼 **호출자가 모델을 명시**하는 경로를 표준화해 전역 `_current` 의존을 줄인다.

---

## 3. 코드 품질 / 세부 (Medium-Low)

### 3.1 `chat.py:507`·`chat.py:593` `is_file_action_request` 이중 호출
`/chat` 핸들러에서 `is_file_action_request(req.message)`를 두 번 평가하고, 첫 분기는 인라인 write, 두 번째 분기는 에이전트 위임으로 갈린다. 정규식 기반 의도 감지가 라우팅을 좌우하는데 로직이 길고(80줄+) 분기가 서로 겹친다.

**개선:** 의도 감지 → 라우팅 결정을 `_classify_chat_intent(req) -> Enum` 하나로 추출해 `/chat` 본문을 얇게. 테스트도 분류기 단위로 붙이기 쉬워진다.

### 3.2 `print()` 디버그 출력 81곳 (+ `🧪 /chat request` 류)
`chat.py:418`의 `print("🧪 /chat request: ...")`, `app_factory`의 `print("✅ MLX ...")` 등 런타임 `print`가 81곳. 프로덕션 로그에 이모지 디버그가 섞이고 로그 레벨 제어가 안 된다.

**개선:** `logging.getLogger("latticeai")`로 통일하고 레벨(`debug`/`info`)을 부여. 사용자용 부팅 배너(`main()`의 print)만 예외로 남긴다.

### 3.3 `save_vpc_config`/`load_vpc_config`가 `datetime.now()` (tz-naive)
`app_factory.py:554` `datetime.now().isoformat()`. 메모리(`stabilization_2026_05`)에 `LATTICE_TZ`로 시간대 통일했다고 돼 있는데 여기는 naive `now()`다. 감사·정렬에서 다른 tz-aware 타임스탬프와 섞이면 정렬이 어긋난다.

**개선:** 프로젝트 표준 tz-aware 헬퍼(`CONFIG.timezone` 기반)로 교체. `save_to_history`의 `datetime.now().isoformat()`(576행)도 동일.

### 3.4 `tools_pdf_pages`의 `except Exception → 500` 후 원문 노출
`api/tools.py:478` `raise HTTPException(500, f"PDF 렌더링 실패: {e}")` — 내부 예외 문자열을 그대로 클라이언트에 반환. 경로/파일시스템 세부가 샐 수 있다.

**개선:** 사용자에게는 일반 메시지, 상세는 `logging.exception`으로만. (다른 라우트도 동일 패턴 있는지 스윕 권장.)

### 3.5 `run_command` allow-list 견고하나 `python`/`node` 임의 실행 허용
`tools/commands.py`의 `ALLOWED_COMMANDS`에 `python`,`python3`,`node`,`npx`가 있다. 셸 연산자·절대경로·git remote는 잘 막지만, `python -c "..."`는 여전히 임의 코드 실행이다(워크스페이스 cwd 안이지만 네트워크·파일 접근은 프로세스 권한 전체).

이건 로컬-우선 에이전트의 **의도된 기능**에 가깝다. 다만 `/tools/run_command`가 `require_admin`이라는 점이 유일한 방어이므로, 그 경계를 문서에 명시하고 audit 로그를 남기는 게 좋다(현재 `run_command` 자체는 감사 이벤트 없음 — `_tool_response`가 hook lifecycle만 태움).

**개선:** exec 계열(`run_command`/`build_project`/`deploy_project`) 결과에 `append_audit_event("tool_exec", command=..., returncode=...)` 추가.

### 3.6 `frontend/src/i18n.ts` 1,853줄 수동 관리
키 패리티(ko/en)를 사람이 관리 중. 메모리(`ux_friendliness_2026_07`)에 "en 누락 시 한글 폴백" 규칙이 있는데, 이건 런타임 폴백이지 **누락 감지**는 아니다.

**개선:** CI에 ko/en 키 집합 diff 검사(스크립트 `scripts/`에 하나 추가). 누락 키를 빌드 실패로 만들면 폴백에 의존한 미번역이 쌓이지 않는다.

---

## 4. 레포 위생 / 릴리스 (Medium — 실사용·배포 신뢰도)

### 4.1 `ltcai-0.3.1/` 구버전 전체 사본이 레포에 상주 — 🟠
루트에 `ltcai-0.3.1/`(server.py, tools.py, telegram_bot.py … 27개 파일)가 **flat 구조 옛 버전 그대로** 들어 있다. 현재 코드(`latticeai/` 패키지)와 무관한 죽은 트리인데 grep·검색·리뷰 노이즈를 크게 만든다.

**개선:** 삭제하거나 별도 태그/브랜치로 보존. 최소한 `.gitignore` + 워킹트리에서 제거.

### 4.2 3중 가상환경 + 빌드 산출물 상주
`venv/`, `.venv/`, `.build-venv/` 세 개 + `dist/`, `build/`, `ltcai-8.9.0.tgz`(2.1MB), `ltcai.egg-info/`, `UNKNOWN.egg-info/`, `node_modules/`가 워킹트리에 있다. `UNKNOWN.egg-info`는 빌드 메타 오설정 흔적(패키지명 UNKNOWN).

**개선:** `.gitignore` 정비(대부분 이미 untracked로 보이나 물리 삭제로 트리 정리), `UNKNOWN.egg-info` 원인(빌드 시 name 누락) 추적. 가상환경은 하나로.

### 4.3 `.git` garbage objects + `size-pack` 이상
`git count-objects`가 `garbage found: .git/objects/../tmp_obj_*` 9개를 보고. 중단된 GC/네트워크 작업 잔재.

**개선:** `git gc --prune=now`로 정리(비파괴). 진행 전 백업 권장.

### 4.4 `.env`가 워킹트리에 존재 (702B)
git-tracked는 아니지만 실제 `.env`가 있다. `.env.example`(3.8KB)과 공존. 시크릿이 로컬에 평문으로 있는 것 자체는 정상이나, `Downloads/` 아래 프로젝트라 유출 표면이 넓다.

**개선:** `.env` 권한 `chmod 600` 확인, `.gitignore`에 `.env` 확실히 포함(현재 tracked 아님은 확인됨), 릴리스 tarball(`MANIFEST.in`)에 절대 포함 안 되는지 `scripts/validate_release_artifacts.py`에 assert 추가.

### 4.5 루트 문서 과다 (98개 md)
`RELEASE_NOTES_v8.0.0` ~ `v9.0.0` 중심으로 정리됨. 9.0.0 문서 정리 전에는 15개 릴리스 노트 + `review.md`/`ux-brain-simplification-review.md`/`docs/CODE_REVIEW_2026-07-06.md` 등 리뷰 문서가 루트·docs에 흩어짐. `verification_report.json`, `chat_history.json`, `server.log`(25KB), `ai_server.log`(빈 파일)도 루트에.

**개선:** `RELEASE_NOTES_v*.md`는 `docs/releases/`로, 리뷰 문서는 `docs/reviews/`로 모은다. 로그·런타임 산출물(`server.log`, `chat_history.json`, `telegram_chats.json`, `verification_report.json`)은 데이터 디렉터리로 옮기고 `.gitignore`.

---

## 5. 잘 되어 있는 점 (유지할 것)

- **import-safe 앱 팩토리**: 모듈 import에 부작용 없음 — 테스트/툴링 친화적.
- **ToolRegistry 단일 출처**: `TOOL_HANDLERS` → `DEFAULT_TOOL_REGISTRY` → governance/catalog가 drift 안 나게 `registered_tools()`로 교차검증.
- **승인 토큰 hash-at-rest**: `PermissionGateway.token_hash`로 저장, 원문 토큰은 메모리·큐에 남기지 않음. 파일 큐도 atomic write(`.tmp` → `replace`).
- **KG/conversation workspace 스코핑**: `_scope_sql`, `filter_scoped_nodes`가 legacy-global 행 호환을 유지하면서 멤버십 스코프 적용.
- **durable conversation store**: 50-msg cap 제거, SQLite UNIQUE+`INSERT OR IGNORE`로 idempotent 레거시 import.
- **`_resolve_path` 샌드박스**: 워크스페이스 이탈(`AGENT_ROOT not in parents`) 차단이 일관됨.
- **installer 확인 토큰 플로우**: `auto_setup.apply_plan`이 `confirm=True` + 토큰 일치를 요구.
- **테스트 커버리지**: 820 passed. `ruff` 클린.

---

## 6. 우선순위 실행 목록

**지금(릴리스 신뢰도·보안):**
1. `/cu/*` computer-use를 `require_admin`/opt-in 플래그 뒤로 + 정책 게이트·감사 로그 적용 (§1.1)
2. `local_write`/`local_read` blocked-prefix·승인 강제를 함수 레벨로 하향 (§1.2)
3. exec/computer 툴에 `append_audit_event` 추가 (§3.5, §1.1)
4. `ltcai-0.3.1/` 및 빌드·로그 산출물 트리 정리, `git gc --prune` (§4.1–4.3)

**다음(구조 부채):**
5. `app_factory._build`의 잔여 인라인 로직을 runtime 시임으로 이전 + `dict(locals())` → 타입드 네임스페이스 (§2.1)
6. 죽은 `_local_approvals` 6개 심볼 삭제 (§2.2)
7. `LLMRouter`에 스위칭 락 도입 (§2.4)

**여유 될 때(품질):**
8. `_classify_chat_intent` 추출로 `/chat` 슬림화 (§3.1)
9. `print` → `logging` 통일 (§3.2)
10. tz-naive `datetime.now()` 일소 (§3.3)
11. `workspace_os.py`/`retrieval.py` 책임 분해 (§2.3)
12. i18n ko/en 키 패리티 CI 검사 (§3.6)

---

## 7. 확인 방법

- 보안 변경 후: `pytest tests -q` (현재 820 green 유지) + `/cu/click`을 non-admin 세션으로 호출해 403 확인.
- 레포 정리 후: `git status` 클린, 릴리스 tarball에 `.env`·산출물 미포함(`scripts/validate_release_artifacts.py`).
- 리팩터 후: `python -c "import latticeai.app_factory"`가 부작용 없이 즉시 반환하는지(import-safe 회귀 방지).
