# Lattice AI Release Guide

이 문서는 `npm`, `PyPI`, `VS Code`, `Cursor`, `Antigravity`, `Open VSX` 배포를
한 번에 처리하기 위한 체크리스트입니다.

> **현재 `.github/workflows/release.yml`은 태그 push에서 빌드와 검증만 수행합니다.**
> PyPI / npm / VS Code Marketplace / Open VSX 배포는 아래 수동 절차로만
> 진행합니다. 태그 생성은 패키지 스토어 publish를 자동으로 트리거하지 않습니다.

> **릴리스 증거물 보존 정책 (`output/release/`)**: 버전별 스크린샷/영상 증거는
> 최신 3개 버전만 보관합니다. `npm run release:evidence`가 캡처 후 자동으로
> `scripts/prune_release_evidence.mjs`를 실행해 오래된 버전을 정리하며
> (`LTCAI_RELEASE_EVIDENCE_KEEP`으로 조정), 과거 증거는 언제든 해당 태그를
> 체크아웃해 재생성할 수 있습니다.

## v12.0.0 — Open House (2026-08-18)

집을 정리해 손님을 들이는 릴리스. 가장 큰 두 크레이트를 도메인으로 나누고
(전부 `git mv`, 동작 변화 0), 처음 온 사람이 읽을 문서를 다시 썼으며,
11.9.0이 정직하게 적어 둔 갭 네 개를 닫았다. 문은 네이티브 **422
오퍼레이션 / 41 패밀리**(`POST /mcp` + 폴더 정리 라우트 가산), 워커는
**20 라우트**(`POST /worker/vector/query` 가산).

- **복잡도 관리(소유자 1순위)**: `lattice-agent` 43파일이
  `kernel`/`parse`/`content`/`tools`/`surface`/`prompts` 여섯 그룹으로,
  `lattice-platform` 31개 평면 모듈이 `workspaceos`/`toolsurface`/
  `governance`/`adminops`/`knowledge`/`modelops`/`shell` 일곱 도메인으로
  (100건 `git mv`). 두 크레이트 모두 크레이트 로컬 `ARCHITECTURE.md`를
  싣고, 각 그룹 `mod.rs`가 무엇이 속하고 무엇이 절대 들어가면 안 되는지와
  불변식을 적으며, 각 `src/lib.rs`가 호환 맵으로 끝나 기존 임포트 경로가
  전부 그대로 해석된다. `docs/DEVELOPMENT.md`는 기여자 온보딩(10분
  퀵스타트 + 어디에-무엇을 표 + 게이트 안내)으로 재작성,
  `docs/ROADMAP.md` 신설(우선순위 있는 갭 목록).
- **정직한 갭 4종 마감**: 복원이 스토어 세대(generation) 에폭으로
  인프로세스 즉시 반영(재시작 불필요) · `/setup/install`이 **서버가
  도출한 allowlist**의 항목에 한해 명시적 동의로 brew/pip/uv를 실행
  (기본은 여전히 수동) · `POST /mcp`가 OpenAPI 계약 안으로(단일 JSON-RPC
  봉투 오퍼레이션, 네이티브 마운트이므로 워커로 프록시되지 않음) · 포인터 도구가
  `pip install "ltcai[pointer]"`로 선언됨.
- **그래프 RAG 품질**: 한국어 2단계 조사 스트리핑 + 근거 게이트, 포함관계
  중복 제거, 방향 있는 타입드 엣지(`PART_OF`·`CONTRADICTS`를 실제로
  생산, 근거 분류), 섹션 트리(`Document ←PART_OF— Section —HAS_CHUNK→
  Chunk`, 트리플 555개 중 549개가 섹션 출처 보유), 임베딩 자동 감지
  (실모델 있으면 채택, 해시는 폴백이라고 표기, 벡터 정체성 `(model,dim)`
  필터 검증).
- **그래프 RAG 속도**: 무변경 재인덱스 33s → **0.26s**(낭비율 1.00 →
  0.00, 핑거프린트는 `ingestion_provenance`에 정착) · 첫 인덱싱 25.8s →
  7.2s · 드레인 ~66 → ~1,300 items/s(임베드를 트랜잭션 앞으로) · 백로그
  991건 40분 → 15.3초(적응형 스케줄러) · HNSW 증분 append + 검색 실사용
  (`LATTICEAI_VECTOR_INDEX=hnsw` → 워커 사이드카 후보 + Rust 정확 재스코어
  `hnsw+rescore`, 실패 시 사유를 실은 폴백; **기본은 여전히 brute**) ·
  vault-watch diff 스킵 · 삭제 파일 정리(`POST /api/ingestion/folder/prune`
  dry-run/confirm, 「삭제된 파일 정리 (N)」 카드, `delete_document_tree`로
  댕글링 0).
- **범용 소형모델 하네스(모델 불문, per-model 핵 금지)**: GUIDED 모드가
  JSON 요구 자체를 제거(번호 메뉴 → 인자 한 개씩 → 하네스가 액션 조립,
  꼬리는 모든 모드와 동일한 `perform_action`) · 측정 기반 프로브가
  standard/compact/guided를 고르고 모델 id + 크레이트 버전으로 캐시 ·
  미드런 하향 자기강등(위로는 절대 안 감) · 통합 카탈로그(native +
  `mcp.*` + `skill.*` 한 메뉴, mcp는 `POST /mcp`와 같은 거버넌스) ·
  시임 prefix 강제 + stop. Qwen **0.5B가 guided로 DONE**(3.9초, 실파일).
  모델 매트릭스 최종 수치는 릴리스 시점 `MATRIX_TABLE` 기준.
- **프론트**: ErrorBoundary 전면(라우트 + 헤비 패널, 「다시 시도」),
  Act/Brain 서브라우트 lazy 분할, 프리뷰 패턴 확장(권한 모드 diff
  프리뷰, 위험 기능 토글 ack), 번들 **104.2 / 150 KiB**.
- **고쳐진 기존 버그**: RAG 인용 지시가 에이전트 프롬프트로 새던 것,
  모델 로딩 이름 로스터 게이트(Qwen AWQ 로드 불가였음), 베낄 수 있는
  워크드 예제(`COPIED_EXAMPLE` fail-closed), `<|channel>` 한-파이프
  제어 프레임 누수.
- **정직한 고지**: 작은 모델의 *내용* 품질은 fail-closed 게이트가 잡는다
  (기계적 실행은 안정) · `api_key` 클라우드 경로는 여전히 모의 검증만
  (과금 0 정책) · DMG는 ad-hoc 서명 · 검색 기본은 brute(hnsw는 opt-in) ·
  watch는 자동 삭제하지 않음 · 크레이트 재구조는 이동이지 결합 해소가
  아님.

빌드 산출물은 `dist/ltcai-12.0.0-py3-none-any.whl`,
`dist/ltcai-12.0.0.tar.gz`, `ltcai-12.0.0.tgz`, `dist/ltcai-12.0.0.vsix`,
`src-tauri/target/release/bundle/dmg/Lattice AI_12.0.0_aarch64.dmg` 입니다.
와일드카드 업로드는 사용하지 않습니다.

상세: [RELEASE_NOTES_v12.0.0.md](RELEASE_NOTES_v12.0.0.md)

## v11.9.0 — Working Order (2026-08-17)

문서에만 있던 13개 Current 스텁을 실동작으로 올리고, 라이브 감사에서 깨진
N1–N9와 이전에 고장난 22항목을 다시 통과시킨 릴리스. 문은 그대로
(네이티브 420 / 41, 워커 **19 라우트** — `/worker/sysinfo`에
`capabilities`/`python_version`만 가산). 클라우드는 선택이고 기본은
로컬이며, OAuth CLI로 과금 없이 검증했다.

- **13개 스텁이 실제로 답한다**: `/models/recommendations`(네이티브
  RAM/AS 프로브 + 워커 카탈로그 + RAM-tier `top_pick`),
  `/setup/scan`+`auto`(실프로브), `/setup/install`(실설치 또는 수동
  안내 — brew/pip는 설계상 수동), computer-use 상태(워커 capabilities
  프로브), `/agent/eval`(결정적 스킬 평가, `requires_model` 정직),
  `/agents/api/run`(라이브 단일 에이전트 + 정직한 health), 자동화
  패턴/제안(conversation_messages 위 결정적 한국어 친화 질문 마이닝),
  워크플로 run(스텝 실행기 + 종료 상태)과 resume(승인 게이트 수정),
  리뷰 `run_now`(같은 실행기에 연결), `build`/`deploy_project`(거버넌스된
  스크립트를 실제로 실행), 백업 blob.
- **라이브 감사 N1–N9**: 에이전트 루프가 호스트에 묶이고 run 본문이
  실제 정책 표를 실음. 메모리 API가 빈 Brain에서 500을 내지 않음
  (`conversation_messages` 부트스트랩 + 방어적 리더). chat/memory/
  chronicle/command가 지식을 봄(null workspace = personal). brain
  health가 빈 100점을 주지 않음. 백업은 `VACUUM INTO` 스냅샷 + blob +
  정직한 매니페스트 + 원자적 복원. export가 edges/chunks를 실음. 폴더
  ingest가 신뢰된 소유자와 통합 승인 토큰(`LocalApprovals` →
  `/permissions/approve`)을 받음. 보이스 메모 텍스트가 저장됨.
- **하이브리드 클라우드가 배선됨**: 프로덕션에 ReviewSink + EgressAudit
  바인딩. 클라우드 답은 Review Center에 `kg_cloud_expansion` 제안으로
  올라가고, egress 감사는 형태/provider/model/reason만 기록(내용 없음).
  이중 자격증명 — `api_key`(OpenAI 호환, **모의 서버만** 검증, 실과금
  없음)와 `cli_oauth`(로컬 OAuth CLI: `agy` → gemini-3.7-flash, `grok`
  → grok-4.6). 해석 순서: `cloud_provider.json` → env → agy → grok →
  none. 에스컬레이션 `auto`(기본)/`manual`/`always`. 요청의
  `network_mode:"local_only"`가 항상 이김. 라이브 OAuth E2E는 API 과금
  0원.
- **MCP가 실서버**: `POST /mcp` streamable-HTTP JSON-RPC
  (`initialize` / `tools/list` / `tools/call`). 큐레이트된 안전 도구 +
  스키마가 파싱된 스킬 7개. 거버넌스 거절은 JSON-RPC 에러.
  `/mcp/call`이 실제로 디스패치. `/mcp`는 OpenAPI 계약 밖(설계).
- **2B(gemma-4-e2b)와 채팅 파일 생성**: compact 프로파일·파서 사다리·
  v10.8.0 salvage 복원. 「index.html 만들어줘」가 모델이 쓴 실제 파일을
  만듦(11.6.0 포트에서 빠졌던 v9.2.0 헤드라인). 에이전트 루프의
  *품질*은 정직하게 게이트 — 파일은 만들어도 요약이 critic에서 떨어질
  수 있음.
- **정직한 고지**: 2B 에이전트 품질, `api_key`는 모의만, 복원 후
  재시작, brew/pip 수동, `/mcp`는 OpenAPI 밖, DMG는 ad-hoc 서명.

빌드 산출물은 `dist/ltcai-11.9.0-py3-none-any.whl`,
`dist/ltcai-11.9.0.tar.gz`, `ltcai-11.9.0.tgz`, `dist/ltcai-11.9.0.vsix`,
`src-tauri/target/release/bundle/dmg/Lattice AI_11.9.0_aarch64.dmg` 입니다.
와일드카드 업로드는 사용하지 않습니다.

상세: [RELEASE_NOTES_v11.9.0.md](RELEASE_NOTES_v11.9.0.md)

## v11.8.0 — Travel Light (2026-08-16)

11.7.0이 백로그를 비운 다음에 남아 있던 것 — 호출자 없는 라우트, 하중을
받지 않는 게이트, 같은 것을 두 번 증명하는 골든, 진단을 통째로 덮고 있던
lint 억제 헤더 — 을 덜어낸 릴리스. 문은 그대로(네이티브 420 / 41),
바뀐 것은 워커 표면이 **28 → 19 라우트**라는 점.

- **게이트 다이어트**: `agent-smoke.yml` 삭제(모델 없는 러너에서 이중
  fail-open이라 초록불이 아무 것도 뜻하지 않았음), `ci.yml` 중복 스텝 제거
  (4레그 매트릭스는 유지 — OpenAPI/product-readiness/확장 테스트는 3.11+
  ubuntu 레그에서만, 커버리지 레그는 pytest 이중 실행 중단), `release.yml`은
  릴리스 스텝만, dependency-audit은 cron 전용, visual은 push+nightly,
  e2e-sidecar는 nightly 전용, 모든 워크플로에 `concurrency` +
  `timeout-minutes`. 로컬 lint 체인 13 → 10. 커버리지 게이트는 **라인 90**
  (분기 게이트 제거).
- **Rust 린트 재무장**: `lattice-{platform,retrieval,ingest,jobs}`의 약
  191개 파일 blanket `#![allow]` 헤더 제거, 드러난 **약 650건**의
  clippy/rustc 진단을 원인에서 수정. 워크스페이스 수준 허용 **0건 추가**,
  남긴 억제는 이유가 붙은 지역 `#[allow(clippy::too_many_arguments)]`
  8개뿐. 죽은 코드 삭제(`workspace_scope` 모듈, `WORKSPACE_OS_VERSION`
  상수, 호출자 0 항목 16개), `ROLE_CAPABILITIES`를 `lattice-auth` 단일
  출처로, `PhaseBudgets`에 토큰 상한 8192(MIN 128 / MAX 8192).
- **통합 테스트 파일 98 → 56** (lattice-platform 43 → 11) — 파일 하나가
  링크되는 바이너리 하나입니다. 테스트 함수는 삭제 없이 주제별로 합침.
- **골든 축소**: agent `decisions__trusted` / `decisions__bypass` 그리드
  (각 702행) 삭제 → 모든 판정 클래스를 덮는 이름 붙은 단위 테스트.
  `decisions__strict`와 `calls`는 702 → **171 대표행**(등가류당 한 행) +
  드리프트 가드. 모든 픽스처 계열이 `FROZEN.md`를 가짐(신규
  `chunking/FROZEN.md` 포함). retrieval/graph_write/agent_loop/http 골든은
  무변경.
- **중복 검증·죽은 코드 제거**: `core/agent_permission.py`, 죽은 보안
  헬퍼(`hash/verify_password`, `check_ip_rate_limit`,
  `configure_trusted_proxies`, `client_ip`, `bytes_match_extension`),
  `_kg_common/text.py`의 죽은 청커(+호출자 0 함수 9개), 렌더 시임의
  `_safe_filename` 이중 살균, 얼어붙은 픽스처의 생성기 2종,
  `scripts/{brain_quality_eval,agent_eval,check_python,bench_agent_smoke}.py`,
  `check_legacy_debt.mjs`(드리프트한 거울 — 파이썬 테스트가 권위).
  product-readiness 증거는 Rust 픽스처로 재지정, 판정은 **COMPLETE 10/10**.
- **워커 표면 28 → 19**: 호출자 0인 아홉 라우트를 end-to-end 삭제
  (`GET /api/embeddings/providers`, `POST /tools/read_document`,
  `GET /tools/pdf_pages`, `POST /worker/multimodal/describe`,
  `GET /api/ingestion/multimodal`, `POST /models/switch/{model_id}`,
  `DELETE /models/unload-all`, `POST /engines/pull-model`,
  `GET /api/capture/voice/status`). `latticeai/api/{tools,local_files,
  voice_capture}.py`와 `lattice_brain/ingestion/pipeline.py` 삭제,
  `pypdfium2` 의존성 제거, `rust/fixtures/worker_allowlist.json` 28 → 19,
  Rust KEEP 표·게이트웨이 allowlist 갱신 + 네거티브 테스트.
- **실제 버그 수정**: `SessionStore`가 미스 시 `sessions.json`을 다시 읽음
  (stat 가드 + 1초 throttle, 같은 락). v11.6.0부터 writer는 `lattice-auth`인데
  워커는 기동 시 한 번만 읽어, **워커가 뜬 뒤의 로그인이 워커에게 보이지
  않았음** — `REQUIRE_AUTH`에서는 파일에 있는 토큰에 401. 테스트 9개 추가.
- **Brain Chat Home 전면 재설계**(3패스): 컴포저가 히어로, LivingBrain 3배
  (1440에서 60px → 179px) + 금빛/옥빛 성장 링과 준비도에 묶인 '기억이 자라고
  있어요' 캡션, 풀캔버스 그리드(왼쪽 Brain / 가운데 컴포저·스타터 / 오른쪽
  제안), 바닥 연속성 바(지난 대화·현황·기억 지도·기능), 잉크+옥빛+금빛 토큰
  통일, `prefers-reduced-motion` 폴백. 죽은 컴포넌트 `FeedbackState.tsx` ·
  `DepthEmergence.tsx` 삭제.
- **정직한 고지**: 커버리지 강제 바닥이 100 → 90으로 내려감(실측은 100%),
  멀티모달 이미지/비디오 절반은 HTTP 문 없이 Brain Core에 남음, DMG는
  ad-hoc 서명(미서명), 삭제된 라우트의 메시지 카탈로그 키는 얼어붙은
  픽스처에 의도적으로 잔존, `tests/visual/mock_server`의 고아 mock 라우트
  하나는 증거 해시 결속 때문에 다음 캡처 사이클에 정리.
- 플로어: pytest **1,153** · vitest **1,761 / 100 파일** · cargo **1,733 /
  75 바이너리** · clippy `-D warnings` 깨끗. 순 diff 424 파일 ·
  +4,604 / −22,165.

빌드 산출물은 `dist/ltcai-11.8.0-py3-none-any.whl`,
`dist/ltcai-11.8.0.tar.gz`, `ltcai-11.8.0.tgz`, `dist/ltcai-11.8.0.vsix`,
`src-tauri/target/release/bundle/dmg/Lattice AI_11.8.0_aarch64.dmg` 입니다.
와일드카드 업로드는 사용하지 않습니다.

상세: [RELEASE_NOTES_v11.8.0.md](RELEASE_NOTES_v11.8.0.md)

## v11.7.0 — Clean Sweep (2026-08-15)

11.6.0이 공개로 남긴 백로그를 닫고, One Door가 몰랐던 회귀를 고치고,
표면을 먹빛/한지 elevation 언어로 다시 그린 릴리스. 문은 그대로 —
네이티브 420 / 41, 워커 28, allowlist에 `/worker/parse`와
`/worker/render/*` 유지.

- **§5.2 오라클 버그 3건을 고침**: command-search knowledge가
  `matches`를 읽고 결과를 반환. 스누즈가 offset-aware datetime을
  받고 잘못된 `until`은 422. 이중 거절은 409(형제 dismiss와 바이트
  동일). 옛 고장을 고정하던 픽스처는 의도적 발산으로 갱신.
- **§5.3 구멍 폐쇄**: 바이너리 업로드는 `/worker/parse`. 청크 벡터가
  네 ingest 문에. 사용자 훅이 네이티브 도구에서 발화(`HookSink`).
  `sanitize_write_content`가 루프와 `/tools/write_file`에. 리뷰 변이
  경로마다 `review_item_created/updated`. `workspace_os.json`은
  writer 하나(`WorkspaceOsStore` + 레지스트리 + 포트).
- **몰랐던 회귀**: Self-Model 쓰기 5경로가 은퇴 시임으로 404 —
  네이티브 복구, 녹화된 본문 9건 바이트 동일. `resolve_contradiction`이
  "적용됨"이라고 거짓말하던 것 포함. xlsx 보안보내기 502 →
  `/worker/render/xlsx`. chat `ingest_generated`는 11.5.2에서도
  스키마 400. vault-watch는 픽스처만 있고 폴러가 없었음 — 지금은
  네이티브 노트 ingest. 좌초 경로 정적 게이트(디코이 증명).
- **시한폭탄 해체**: chronicle `@today`, briefing freshness,
  insights/garden/proactive(08-21), health(09-28), quality(11-12).
  시계 시임 + falsifier + 4시간대 증명.
- **UI**: 유리 없음, elevation 사다리, 토큰 네이티브 cytoscape,
  번들 ~103 KiB. a11y/레이아웃 계약 유지.
- **남은 구멍은 공개**: `open_keys` pending-only, refiner 없음,
  `delete_node`의 `PART_OF` 잔여, owner 없는 리뷰 이벤트 침묵,
  KG-api 텍스트 전용 ingest, 리뷰 변이 = 스토어 사이클 2회,
  snooze 422는 영문 리터럴.

빌드 산출물은 `dist/ltcai-11.7.0-py3-none-any.whl`,
`dist/ltcai-11.7.0.tar.gz`, `ltcai-11.7.0.tgz`, `dist/ltcai-11.7.0.vsix`,
`src-tauri/target/release/bundle/dmg/Lattice AI_11.7.0_aarch64.dmg` 입니다.
와일드카드 업로드는 사용하지 않습니다.

상세: [RELEASE_NOTES_v11.7.0.md](RELEASE_NOTES_v11.7.0.md)

## v11.6.0 — One Door (2026-08-15)

제품 서버가 Rust 하나로 통합된 릴리스. Python은 웹 애플리케이션이 아니라
AI 워커입니다.

- **네이티브 420 오퍼레이션 / 41 라우트 패밀리**: `lattice-host`가 아홉
  크레이트의 라우터를 한 프로세스에 마운트하고 원래 경로 그대로
  응답합니다. `(method, path)` 중복은 라우터 생성 전에 이름 붙은 단언으로
  먼저 실패합니다.
- **Python 워커 28 라우트**: LLM·스트림, embed, extract, parse, render×4,
  ASR, multimodal-describe, models/engines, sysinfo, health. 커밋된
  allowlist(`rust/fixtures/worker_allowlist.json`) 밖의 경로는 프록시가
  아니라 `404 {"detail":"Not Found"}`이며, 드리프트 게이트가 그 목록을
  워커 프로필에서 다시 생성해 비교합니다.
- **모든 쓰기가 네이티브**: KG write 엔진이 `lattice-core`로. 32단계 행
  단위 패리티(매 단계 전 테이블 덤프, 허용 오차 0), `sqlite_master` 67
  객체 스키마 대조, 그래프 테이블 17개 소유권 WORKER → RUST_PLATFORM.
- **표면은 재생으로 증명**: 녹화된 HTTP 골든 **1,487 케이스**(12 픽스처
  파일)를 네이티브 라우트에 재생. 기존 골든 계열(retrieval/chunking/
  agent kernel/agent loop)은 그대로 초록.
- **삭제**: Python **298 파일 / 73,617줄**. 남은 127 파일은 문·분기
  **100.00%**(`fail_under=100`, 새 pragma 0).
- **제거된 표면**: Telegram 브리지, SSO OIDC 로그인/콜백 플로우(설정
  표면은 유지, 패스워드 로그인은 네이티브). 둘 다 워커 경계의 결과이며
  노트에 이유와 함께 적었습니다.
- **정직한 고지**: 그대로 이식한 오라클 버그 3건과 남은 구멍(업로드 추출
  UTF-8 전용, 공급 벡터 1차 노드 한정, 모델 로드 스트리밍은 FakeWorker +
  라이브 스모크로 증명, `/worker/render/pdf`의 새 `pdf` extra, 포인터
  도구의 워커 실행)은 전부 릴리스 노트에 열거되어 있습니다.

빌드 산출물은 `dist/ltcai-11.6.0-py3-none-any.whl`,
`dist/ltcai-11.6.0.tar.gz`, `ltcai-11.6.0.tgz`, `dist/ltcai-11.6.0.vsix`,
`src-tauri/target/release/bundle/dmg/Lattice AI_11.6.0_aarch64.dmg` 입니다.
와일드카드 업로드는 사용하지 않습니다.

상세: [RELEASE_NOTES_v11.6.0.md](RELEASE_NOTES_v11.6.0.md)

## v11.5.2 — Tight Ship (2026-08-12)

정착된 11.5.1 트리를 3중 감사하고 그 결과만 실행한 정리·정합성 릴리스.

- **삭제 약 1,100줄**: 이사 간 모듈 shim 6종, 배선된 적 없는 멀티모달
  스트리밍 시임, 호출자 0 심볼 약 27개(테스트 수술 동반), 소비자 없는
  `metadata_for` 인터페이스, npm tarball에 아직 실려 나가던 레거시
  Electron 셸, 죽은 측정 스크립트. `sys.modules[__name__]` shim 패턴은
  게이트가 재발을 막습니다. 커버리지 WP 테스트 183개는 **전수 하중
  검증**(10개가 79 분기 아크의 유일 소유자), 중복 기능 테스트 7개는
  회귀 가치로 의도적 보존.
- **통합**: 임베더 쌍을 골든에 못박힌 사본으로 단일화(조용한 벡터
  드리프트 차단), 워크스페이스 선택자 5표면이 정본 규칙으로 —
  **불일치 시 403**(의도된 동작 변경), sha256/SSE 프레임/데이터 디렉터리/
  모드 서비스/모듈 프로브 헬퍼 각 1곳, Rust byte-identical 사본 7건
  (`clock.rs` 통째 포함) 통합.
- **현관문(라이브 전/후 증거)**: 프록시 리다이렉트가 `Set-Cookie`·
  `Location`을 온전히 통과(초대 게이트 막다른 길, SSO 무인증, 딥링크
  12개 프래그먼트 분실 해소), 워커 오리진 절대 `Location` 재작성,
  `/rust/*`·`/host/status|jobs` **posture fail-closed**(이전엔 무인증
  그래프 서빙), `X-Forwarded-For/Proto/Host` 홉 통과(루프백·신뢰
  프록시만 존중), 수퍼바이저 CORS 오리진 주입, CSP `ws://` 제거,
  바인드 실패 시 Tauri 죽은 오리진 이동 제거.
- **감사가 드러낸 기능**: `POST /api/search/graph`(허용 목록에 있었으나
  도달 불가), `GET /api/ingestion/multimodal`(문서화됐으나 미배선),
  골든 신규 2계열 — `recent_chat`이 **실제 발산**을 잡음(Python
  `limit=0` 꼬리 슬라이스는 전부 보존, Rust는 빈 결과 → Python 기준으로
  Rust 수정) + `document_targets`/`agent_profiles` 97행. 골든 **251 파일**.
- 정직한 경계 명시: 네이티브 레인은 열린 posture·단일 로컬 소유자 표면,
  recent-chat 시임 소유권은 아직 `/chat`의 임시 prepend(마이너 항목),
  `workspace_scope_from_request` 자체는 남은 두 호출자에 대해 관대,
  프록시 홉은 장수 SSE 때문에 의도적으로 타임아웃 없음.
- 플로어: **7,022 + 1,761 테스트 · 100.00% · Rust 760 · OpenAPI 421 paths**.

빌드 산출물은 `dist/ltcai-11.5.2-py3-none-any.whl`,
`dist/ltcai-11.5.2.tar.gz`, `ltcai-11.5.2.tgz`, `dist/ltcai-11.5.2.vsix`,
`src-tauri/target/release/bundle/dmg/Lattice AI_11.5.2_aarch64.dmg` 입니다.
와일드카드 업로드는 사용하지 않습니다.

상세: [RELEASE_NOTES_v11.5.2.md](RELEASE_NOTES_v11.5.2.md)

## v11.5.1 — Rust Full Loop (2026-08-12)

11.5.0의 명시적 잔여 2건 완결 — 다이어그램의 모든 Rust 박스가 구현됨.

- **에이전트 루프 Rust 이식**: 상태기계·실행 규칙 전부·검증 fail-closed
  매핑·롤백 3-tier·승인 스토어(TTL/410) — 실제 Python 런타임 대본 재생
  **궤적 10종 byte-identical** + 헬퍼 그리드(판정 90 포함). 실워커
  라이브 스모크: 시임 4종 실통신, 무모델 턴 fail-closed 동일.
- **AI Worker 시임 3종**(additive, 문·분기 100%): `/agent/llm`·
  `/agent/tool`(서버측 불변 가드 유지+시임 게이트)·`/agent/change-proposal`.
- **문서 생성 컨텍스트 네이티브**: docgen 검색·multi-hop·컨텍스트 계약
  전체 — 골든 53 신규(총 247) 완전 일치, 계약 pytest 256.
- ARCHITECTURE.md System Map을 Rust 기반 현재 구조로 재작성.
- 플로어: **7,006 + 1,761 테스트 · 100.00% · Rust 739**.

빌드 산출물은 `dist/ltcai-11.5.1-py3-none-any.whl`,
`dist/ltcai-11.5.1.tar.gz`, `ltcai-11.5.1.tgz`, `dist/ltcai-11.5.1.vsix`,
`src-tauri/target/release/bundle/dmg/Lattice AI_11.5.1_aarch64.dmg` 입니다.
와일드카드 업로드는 사용하지 않습니다.

상세: [RELEASE_NOTES_v11.5.1.md](RELEASE_NOTES_v11.5.1.md)

## v11.5.0 — Rust Complete (2026-08-11)

Rust 로드맵 Phase 2·3·4 완결 — 검증된 조각만, Python은 AI Worker로.

- **Front-door 기본화(데스크톱)**: Tauri = 수퍼바이저+게이트웨이 토폴로지,
  포트 결박 CSRF는 env 주입으로 해소(실워커 라이브 증명 200/403),
  안전 밸브 3종, SSE `X-Accel-Buffering` 보강.
- **네이티브 확장**: 3채널 service-hybrid·graph 읽기 3종·히스토리
  전 읽기·Context Assembler — **패리티 191/191 완전 일치**(계약 199
  양방향); typed chunking 42케이스/332청크 + **뮤테이션 26/26** +
  폴링 워처(lattice-ingest, 쓰기는 워커 위임); 권한 커널 **판정
  2,452건 완전 일치** + 읽기 전용 명령 네이티브 실행(lattice-agent).
- **스케줄러가 갭을 닫음**: `POST /api/index/drain`(100% 커버) +
  lattice-jobs 60s 타이머·백오프·`/host/jobs` — "아무도 큐를 몰지
  않는다"는 한계 문구 삭제.
- 플로어: **6,861 + 1,761 테스트 · 100.00% · Rust 534**. 잔여는
  §4c(루프 이식)만, 사유 명시.

빌드 산출물은 `dist/ltcai-11.5.0-py3-none-any.whl`,
`dist/ltcai-11.5.0.tar.gz`, `ltcai-11.5.0.tgz`, `dist/ltcai-11.5.0.vsix`,
`src-tauri/target/release/bundle/dmg/Lattice AI_11.5.0_aarch64.dmg` 입니다.
와일드카드 업로드는 사용하지 않습니다.

상세: [RELEASE_NOTES_v11.5.0.md](RELEASE_NOTES_v11.5.0.md)

## v11.4.0 — Rust Foundation (2026-08-11)

Rust 전환 Phase 1 — 전면 재작성 없이, 작동하고 증명된 조각부터.

- **`rust/` workspace 3크레이트**: lattice-core(같은 SQLite 읽기층 +
  임베더 bit-for-bit 포트), lattice-retrieval(하이브리드/키워드/벡터
  네이티브 — **패리티 75/75 완전 일치, 엡실론 0** + Python측 계약
  테스트 80개로 양방향 잠금), lattice-host(HTTP 헬스·백오프 자동
  재시작·우아한 종료·포트 통일 수퍼바이저 + 127.0.0.1 전용 게이트웨이:
  `/host/*`·네이티브 `/rust/search/*`·스트리밍 리버스 프록시, 옵트인).
- **데스크톱이 올라탐**: Tauri `main.rs` 451→149줄, 5개 IPC 커맨드 계약
  보존, 후보 해석 우선순위 버그 수정.
- **게이트**: CI rust 잡(fmt/clippy/test), `*.rs` 라인 상한 편입,
  버전 동기 타깃 18→25.
- 플로어 유지: **6,643 + 1,761 테스트 · 문·분기 100.00% · 프론트 4지표
  100% · Rust 194 테스트**. Python 서버는 전 표면을 그대로 서빙(무손상).

빌드 산출물은 `dist/ltcai-11.4.0-py3-none-any.whl`,
`dist/ltcai-11.4.0.tar.gz`, `ltcai-11.4.0.tgz`, `dist/ltcai-11.4.0.vsix`,
`src-tauri/target/release/bundle/dmg/Lattice AI_11.4.0_aarch64.dmg` 입니다.
와일드카드 업로드는 사용하지 않습니다.

상세: [RELEASE_NOTES_v11.4.0.md](RELEASE_NOTES_v11.4.0.md)

## v11.3.0 — Time Remembers (2026-08-11)

시간 감각을 화면으로 꺼내고, 코드베이스에서 큰 파일을 없앤 릴리스입니다.

- **기억의 연대기 (Brain Chronicle)** — 7번째 주 화면(`#/chronicle`):
  성장 곡선+시간 핸들(ARIA 슬라이더), 활동 히트맵, 그날의 이야기 카드,
  `store.as_of()` 기반 되감기. 11.1.0 temporal 컬럼의 첫 UI. 전부 읽기
  전용 — 스키마 변경 0, 쓰기 0, 모델 호출 0.
- **No Big Files** — 1,000줄 초과 1st-party 파일 28개 전부 분해
  (styles.css 10,956→48; Python 18개 모듈 → 동명 패키지). AST 동등성
  증명 + CSS 번들 byte-identical + i18n key맵 동일 증명으로 동작 변화 0.
  `check_max_file_lines.mjs` 게이트가 재발을 CI에서 차단(1,319파일 스캔).
- **게이트 강화** — 증거 결속이 mock 서버 전체 트리 지문화, 픽셀 게이트가
  신규 화면(베이스라인 없음)을 이해, i18n 고아 스캔이 파트 파일 인식.
- 플로어 유지: **6,560 + 1,761 테스트 · 40,488문·11,052분기 100.00% ·
  프론트 4지표 100%** · Playwright 40/40.

빌드 산출물은 `dist/ltcai-11.3.0-py3-none-any.whl`,
`dist/ltcai-11.3.0.tar.gz`, `ltcai-11.3.0.tgz`, `dist/ltcai-11.3.0.vsix`,
`src-tauri/target/release/bundle/dmg/Lattice AI_11.3.0_aarch64.dmg` 입니다.
와일드카드 업로드는 사용하지 않습니다.

상세: [RELEASE_NOTES_v11.3.0.md](RELEASE_NOTES_v11.3.0.md)

## v11.2.0 — All Systems On (2026-08-11)

모든 것이 실제로 작동하고, 오늘의 모델을 싣고, 스위치를 사용자에게 준
릴리스입니다.

- **모델 카탈로그 전수 최신화** — HF API 무부하 검증(가중치 0, 실로드 0):
  Hub에서 사라진 2종·gated·구세대 제거, 추천 10종 전부 2025–2026 세대
  (Gemma 4 · Qwen3.5/3.6 · gpt-oss-20b · 한국어 지원 LFM2.5), 기존 다운로드
  사용자는 인식 유지. 검증기는 이제 무부하 원칙을 위반할 수 없음.
- **기능 스위치보드** — 홈 dock "기능" 서랍에 opt-in 10종 라이브 토글
  (서버 카탈로그, 사용자>env>기본, 만진 스위치만 발화, 미설치는 정직 표기).
- **스코프 아웃 전면 해소** — Notion/Git/메일·캘린더 브릿지, X25519
  수신자 공개키 공유, ffmpeg 비디오 키프레임, 볼트 감시, 일괄 승인,
  Self-Model 루프 도달, 사진 의미 검색, kgv2_edges 근본 수정.
- **58행 증거 감사** — 51 작동, 죽은 기능·미배선·과장 문서 7건 수정
  (docs/FEATURE_AUDIT_v11.2.0.md 동봉).
- 플로어 유지: **6,490 테스트 · 39,054문·11,014분기 100.00%**.

빌드 산출물은 `dist/ltcai-11.2.0-py3-none-any.whl`,
`dist/ltcai-11.2.0.tar.gz`, `ltcai-11.2.0.tgz`, `dist/ltcai-11.2.0.vsix`,
`src-tauri/target/release/bundle/dmg/Lattice AI_11.2.0_aarch64.dmg` 입니다.
와일드카드 업로드는 사용하지 않습니다.

상세: [RELEASE_NOTES_v11.2.0.md](RELEASE_NOTES_v11.2.0.md)

## v11.1.0 — Product Intelligence (2026-08-10)

기초 위에 지능 레이어를 올린 기능 릴리스입니다 — 계획 문서
(docs/v11.1.0_PRODUCT_INTELLIGENCE_PLAN.md)의 5개 트랙 전부.

- **빠르다**: 플러그형 벡터 인덱스(HNSW 옵트인) — 하이브리드 p50
  10k에서 299ms → **10.1ms**, 50k에서도 43.9ms(recall 0.987). 영속 배경
  임베딩 큐, RRF 융합 옵션.
- **살아있다**: 모순 감지 → 리뷰 제안 → 승인 시 temporal 스탬프
  (`valid_from/to/superseded_by`, `as_of` 슬라이스), 25개 인제스트마다
  상위 개념·누락 엣지·proactive Brief 합성 제안 — 전부 제안-우선.
- **모든 것을 기억한다**: `allow_multimodal`(기본 꺼짐) 뒤에서 이미지·
  녹음이 1등 노드(Image/Audio) — 캡션 조작 스텁 삭제, 별도 이미지 벡터
  공간+late fusion, 승인 게이트를 우회하지 않는 인라인 썸네일.
- **나를 안다**: Self-Model 서브그래프(제안으로만 생성, 예산 규율 주입,
  투명 조회·삭제), 삭제가 구조적으로 불가능한 폴더 재구성 제안.
- **연결된다**: 승인 게이트 Obsidian vault 브릿지(위키링크→엣지, 멱등),
  서명·암호화 서브그래프 공유 프로토타입(수신=제안, 기본 꺼짐).
- 플로어 유지: **6,261 테스트, 37,590문·10,658분기 100.00%**, 3환경
  (macOS 3.14 · fresh 3.11 · linux 컨테이너) 검증.

빌드 산출물은 `dist/ltcai-11.1.0-py3-none-any.whl`,
`dist/ltcai-11.1.0.tar.gz`, `ltcai-11.1.0.tgz`, `dist/ltcai-11.1.0.vsix`,
`src-tauri/target/release/bundle/dmg/Lattice AI_11.1.0_aarch64.dmg` 입니다.
와일드카드 업로드는 사용하지 않습니다.

상세: [RELEASE_NOTES_v11.1.0.md](RELEASE_NOTES_v11.1.0.md)

## v11.0.1 — Both Branches (2026-08-10)

11.0.0이 기록만 하고 고치지 않은 결함 11건의 정산 릴리스입니다.

- **결함 11건 전부 수정** — Telegram 인증 헤더 유출·언로드 성공 조작,
  inspect_html 죽은 수집, 리뷰 id 초 충돌, vLLM 좀비 오판(→409),
  보안 대시보드 마스킹 갭·invalid JSON 내보내기(+상세 라우트 후속),
  임베딩 model_id 차원 동결, fast-path 모델 표기 불일치, 워크스페이스
  죽은 리터럴·미도달 404. 고정 테스트 반전 + 회귀 테스트
  (`test_fix_v1101_*`).
- **분기 커버리지 100% 게이트** — `branch = true`, 9,828아크 전부 실행,
  테스트 5,426 → 5,798개. `pragma: no branch`는 사유 명시 2줄뿐.
- **죽은 코드 제거** — `_wa()`, 스냅샷 정규화기, 404 arm, 증명된 죽은
  조건 3곳(전 유니코드 스캔 포함) 등 전부 참조-0 증명 후 삭제.
- 검증: 로컬 3연속 + fresh 3.11 venv + linux 3.14 컨테이너 모두 그린.

빌드 산출물은 `dist/ltcai-11.0.1-py3-none-any.whl`,
`dist/ltcai-11.0.1.tar.gz`, `ltcai-11.0.1.tgz`, `dist/ltcai-11.0.1.vsix`,
`src-tauri/target/release/bundle/dmg/Lattice AI_11.0.1_aarch64.dmg` 입니다.
와일드카드 업로드는 사용하지 않습니다.

상세: [RELEASE_NOTES_v11.0.1.md](RELEASE_NOTES_v11.0.1.md)

## v11.0.0 — Full Measure (2026-08-10)

기능이 아니라 바닥을 출하한 메이저 릴리스입니다. **Python 커버리지 72.80% →
100.00%, `fail_under = 100`이 CI 게이트**가 됐습니다. 화면은 의도적으로
한 픽셀도 바뀌지 않았습니다.

- **테스트 2,269 → 5,426개(+3,157, 신규 파일 145개).** 라우터 팩토리 + 주입
  페이크, tmp_path 위 실제 SQLite 스토어, 실제 문서 포맷 입력으로 작성.
- **플랫폼 잠금 분기도 ubuntu CI에서 실행** — MLX/Windows/watchdog/reportlab/
  psycopg 경로는 sys.modules 페이크·시임 패치로 구동.
- **`pragma: no cover`는 정확히 8줄**, 전부 도달 불가 사유를 주석으로 지님.
- **커버리지가 실제 결함을 드러냄** — Telegram 인증 헤더 오발송, 언로드
  성공 조작, 죽은 스타일시트 수집, 리뷰 아이템 id 초 단위 충돌, vLLM 좀비
  오판, 보안 대시보드 목록의 마스킹 갭 등. 동작 변경 없이 릴리스 노트에
  기록되고 현재 동작 그대로 테스트로 고정됨(수정은 다음 릴리스 후보).
- **부수효과 커버리지 제거** — build_phases 후반 4페이즈가 server-import
  부수효과 없이 전용 테스트로 커버됨.
- mypy 276/276 · 0 에러, ruff 클린, 프론트 100%(1,646 테스트) 유지,
  agent_eval 23/23.

빌드 산출물은 `dist/ltcai-11.0.0-py3-none-any.whl`,
`dist/ltcai-11.0.0.tar.gz`, `ltcai-11.0.0.tgz`, `dist/ltcai-11.0.0.vsix`,
`src-tauri/target/release/bundle/dmg/Lattice AI_11.0.0_aarch64.dmg` 입니다.
와일드카드 업로드는 사용하지 않습니다.

상세: [RELEASE_NOTES_v11.0.0.md](RELEASE_NOTES_v11.0.0.md)

## v10.10.0 — Quiet Station (2026-08-06)

Brain 대화 홈을 "고요한 스테이션"으로 다시 지은 릴리스입니다.

- **통계 배지 + 호버 팝오버** — 기억/주제 문장이 인사말 옆 칩이 되고, 요약
  그래프와 기억 지도 바로가기는 팝오버로 옮겨졌습니다(호버·클릭 경합은
  350ms 디바운스로 해소).
- **+ 하나로 접힌 캡처** — 문서/이미지/파일/폴더/노트/웹이 컴포저의
  `+ 추가` 뒤로. 업로드 중이거나 입력 폼이 열려 있으면 닫히지 않습니다.
- **모델 상태 필** — 상단 배너 대신 히어로 우측의 작은 필 + 유일한 액센트
  CTA. 전체 문장은 툴팁/aria-label로 유지.
- **독과 서랍** — 지난 대화·통계·기억 지도가 좌측 레일(모바일에선 가로
  줄)에서 포커스 트랩 서랍으로 열립니다. 포털 서랍 내부 그리드 붕괴(2px
  행)는 flex 컬럼 전환으로 해결.
- **보더리스 + 액센트 절제** — 그림자 층위, 액센트는 보내기/모델 CTA 두 곳.
- **프론트엔드 커버리지 100%** — statements/branches/functions/lines 전부,
  `all: true` 유지, vitest thresholds 100 + CI 커버리지 게이트.
- **히스토리 경계 9.0.0** — 8.x 노트 9개 제거, 4개 문서가 같은 경계 서술,
  단위 테스트로 고정.
- 비주얼 스펙 33개가 새 계약(+ 메뉴, 독 서랍, 배지 팝오버, 글로우 기하)을
  검증합니다.

빌드 산출물은 `dist/ltcai-10.10.0-py3-none-any.whl`,
`dist/ltcai-10.10.0.tar.gz`, `ltcai-10.10.0.tgz`, `dist/ltcai-10.10.0.vsix`,
`src-tauri/target/release/bundle/dmg/Lattice AI_10.10.0_aarch64.dmg` 입니다.
와일드카드 업로드는 사용하지 않습니다.

## v10.9.0 — Never Blocks (2026-08-05)

로컬 서버가 자기 이벤트 루프를 스스로 막던 문제를 잡은 릴리스입니다.

- **긴 작업이 이벤트 루프에서 내려왔습니다.** `ollama pull`, 엔진 설치,
  Hugging Face 가중치 다운로드, MCP `pip`/`npm` 설치, `/local/sysinfo`의
  `top`/`vm_stat`/`sysctl` 세 subprocess — 전부 `async def` 안에서 그대로
  실행되고 있었습니다. 다운로드 타임아웃이 900초이므로 최악의 경우 15분간
  서버 전체가 응답하지 못했습니다. `asyncio.to_thread`로 이관(스트리밍
  변형인 `prepare_and_load_model_stream`은 원래 올바르게 하고 있었습니다).
- **재발 방지 2중 게이트** — ruff `ASYNC210/220/221/222/230/251` 규칙 활성화
  (`ASYNC240`는 의도적 제외: 단일 stat 호출은 스레드 전환이 더 비쌉니다),
  그리고 `tests/unit/test_event_loop_not_blocked.py`가 핸들러 실행 중 티커
  코루틴을 돌려 루프가 실제로 살아 있었는지를 측정합니다.
- **보이지 않던 포커스 링 수정** — 10.8.0이 캡처 필 규칙의 transition에
  `border-color`를 넣어, 포커스가 닿는 순간에는 아직 idle 색이었습니다.
  같은 파일 아래쪽 공용 규칙의 주석이 정확히 이걸 금지하고 있었습니다.
- **답변 완료 후에도 "생각 중"이던 유기체 수정** — 회상 펄스가 걸어둔 900ms
  타이머가 스트림 종료 후에 발화하고 있었습니다.
- **서버 메시지 i18n 14개 라우터 추가 이관** — chat/chat_history/chat_intents/
  memory/knowledge_graph/local_files/portability/review_queue/project_sessions/
  network_boundary/models/tools/mcp/setup. `models.py`·`mcp.py`·`tools.py`는
  한 화면 안에서 한국어와 영어가 섞여 나오던 라우터였습니다.
  `scripts/check_server_i18n.mjs` 게이트 + `MIGRATED_ROUTERS` 확장 + 두 목록
  불일치를 잡는 테스트.
- **환영 화면이 접힌 부분 위에 들어옵니다** — 770px/747px → 747px/747px.
- **스트리밍 채팅 경로 테스트** — `frontend/src/test/fakeChatStream.ts` 가짜
  SSE 하네스 + 11개 시나리오. `useBrainChat` 12% → 약 70%.

빌드 산출물은 `dist/ltcai-10.9.0-py3-none-any.whl`, `dist/ltcai-10.9.0.tar.gz`,
`ltcai-10.9.0.tgz`, `dist/ltcai-10.9.0.vsix`,
`src-tauri/target/release/bundle/dmg/Lattice AI_10.9.0_aarch64.dmg` 입니다.
와일드카드 업로드는 사용하지 않습니다.

## v10.8.0 — Within Reach (2026-08-04)

이미 있었지만 손이 닿지 않던 것들을 손에 닿게 만든 릴리스입니다.

- **온보딩 3개 화면이 한 화면에 들어옵니다.** 모든 단계 위에 390px 히어로
  브레인이 그려지고 있어서 로그인 폼·추천 모델·설치 진행이 전부 접힌 부분
  아래에서 시작했습니다. `data-scale`로 환영 단계만 히어로, 이후는 104px
  마크. 환영 화면 자체도 1175px → 770px.
- **설치 화면이 번역 키 원문을 출력하던 문제 수정** — `t()`가 `defaultValue`를
  읽지 않아 `flow.install.stage.idle`이 사용자에게 그대로 보였습니다.
- **서버 메시지 i18n** — `latticeai/core/messages.py` 카탈로그 + 요청에서
  언어 결정(`X-Lattice-Language` → `Accept-Language`). auth/admin/browser
  라우터 이관. 브라우저 확장도 같은 문제(한 팝업 안 한/영 혼재)를 해결.
- **약한 로컬 모델 대응** — 파일 생성이 "가장 긴" 실패 응답 대신 "가장 파일에
  가까운" 응답을 복구에 넘깁니다. 동일 응답 반복 시 1회 한정 에스컬레이션.
  산문 파일 타입(.md/.txt)에 검증 추가. 멀티에이전트 JSON 파서도 균형 스캔 +
  trailing comma 복구(복구만, 창작 아님).
- **증분 인덱싱 비용 정상화** — 전체 코퍼스를 메모리에 올리고 항목마다 SELECT
  하던 것을 스트리밍 + 해시맵 1회 조회로. 변경 없으면 임베딩 0회(테스트로 단언).
- **프론트엔드 커버리지** 47.35% → 52.26%, 테스트 424 → 504.
- **workspace_os.py** 1128 → 945줄 (indexing/relationships/onboarding/
  computer-memory 매니저 분리).

빌드 산출물은 `dist/ltcai-10.8.0-py3-none-any.whl`, `dist/ltcai-10.8.0.tar.gz`,
`ltcai-10.8.0.tgz`, `dist/ltcai-10.8.0.vsix`,
`src-tauri/target/release/bundle/dmg/Lattice AI_10.8.0_aarch64.dmg` 입니다.
와일드카드 업로드는 사용하지 않습니다.

## v10.7.0 — Plain Surface (2026-08-04)

12개 화면 전면 재구성 — 대시보드 격자를 해체하고 각 화면을 사용자가 하러 온 일 중심으로 다시 배치했습니다. 해시 경로 38개는 하나도 사라지지 않았고(38 → 38, `frontend/src/routes.test.ts`가 착지 지점을 검증), 12개 화면이 문구만이 아니라 배치가 실제로 달라졌다는 것은 픽셀 델타 게이트가 검증합니다.

빌드 산출물은 `dist/ltcai-10.7.0-py3-none-any.whl`, `dist/ltcai-10.7.0.tar.gz`,
`ltcai-10.7.0.tgz`, `dist/ltcai-10.7.0.vsix`,
`src-tauri/target/release/bundle/dmg/Lattice AI_10.7.0_aarch64.dmg` 입니다.
와일드카드 업로드는 사용하지 않습니다.

## v10.6.3 — Loud Limits (2026-08-04)

리뷰 2026-08 지적사항 대응. 조용히 대충 하던 자리를 찾아, 하던 일은 그대로 하되
그렇게 하고 있다고 말하게 만든 릴리스.

- 검색: brute-force 벡터 recall의 조용한 절단 제거 — "M개 중 N개 스캔" 리포트
- 수집: 백그라운드 잡 큐 SQLite 영속화 — 재시작 후 남은 항목부터 재개
- 보안: CSRF Origin/Referer 가드 + workspace 스코프 단일화(불일치 시 403)
- 에이전트: 덮어쓰기 fail-closed, 모델 스트림 실패를 ModelStreamError로 구분
- 그래프: 백업 임포트 직후 벡터 인덱스 재정렬
- 빌드: 프론트 빌드 신선도 게이트
- 프론트: SSE 파서 분리, 스티키 스크롤 수정

상세: [RELEASE_NOTES_v10.6.3.md](RELEASE_NOTES_v10.6.3.md)

## v10.6.2 — Ask First (2026-08-03)

10.6.1 rebuilt five screens and only half-rebuilt one of them. The Brain home
got a new order — composer, then what to try, then the controls — but kept its
old shape: a 5.4rem organism and a centred headline running down the middle
before the composer, and the suggestion strip wedged between the composer and
its own toolbar. The thing to do and the alternatives to doing it wore the same
border, and the composer's controls were separated from the composer by an
unrelated block. 10.6.2 splits the screen.

**Two surfaces instead of one tall card.** `.brain-home-station` now holds the
first move and nothing else: a horizontal greeting banner (organism 5.4rem →
3.2rem, beside the title rather than above it, on a tinted strip with a
hairline foot), the composer in its focus-ring wrapper, and the toolbar — add
material on the left, the autonomy dial on the right — as the card's floor. The
three suggestions moved out to `.brain-secondary-deck`, a sibling card of the
station under the stage, and the quiet shelf row stays third. The stage widened
from 44rem to 50rem to carry the split without crowding, and `BrainConversation`
renders the three as siblings so `.brain-centered-home > *` keeps `flex: none`
on each.

**The name moved to the element that has a role.** The suggestion strip carried
`aria-label` on a plain `<div>` — an element with no role has nothing to name,
so the browser discarded it and the block reached a screen reader unnamed. The
label sits on the deck's `<section>` now, which a name promotes to a `region`,
and the strip inside it carries none.

**The empty branch got a design.** With no `suggested_questions`, the deck
renders `.brain-prompt-pills-row` instead of `.brain-prompt-grid`. That row had
been inheriting `conversation.css`'s 2.65rem `.brain-prompt-pill` floor, drawn
for pills in a live conversation, so the fallback stood half again as tall as
the cards it replaces. It is scoped and sized on this deck now — and scoped
rather than written bare, because `.brain-prompt-pill` is shared with two other
sheets.

**Three defects the move exposed, all specificity, none visible in JSX.**

- `LivingBrain` writes the aura's `box-shadow` inline from the Brain's depth, so
  a `.brain-hero-organism .brain-aura { box-shadow: … }` rule — written first,
  and read as correct — could never apply. The blur is now
  `var(--aura-blur, 60px)`; the banner sets `--aura-blur: 14px` and every other
  host keeps its exact previous rendering through the fallback.
- `overflow: hidden` on the station would have clipped the ingestion popover
  (which anchors to the toolbar and opens below it) *and* made the card a scroll
  container, so focusing the note field scrolled the greeting and half the
  composer out of view. The station stays `overflow: visible`; the banner uses
  `overflow: clip` and rounds its own top corners.
- `affordance.css` loads last and owns both the app-wide
  `button { white-space: nowrap }` opt-out list and the `prefers-reduced-motion`
  cancellation. Its `.brain-home-prompt-strip > button` entries matched nothing
  after the grid appeared: the hover-lift and reduced-motion selectors are
  re-pointed at `.brain-prompt-grid button`, while the `white-space` entry is
  deliberately *not* re-added — `home-simple.css` flips the cards to `nowrap`
  chips under 900px, and re-listing them here would tie and win that media rule.

**Guarded.** `BrainHome.test.tsx` asserts the three surfaces are direct siblings
of the stage in order, that the station contains neither the strip nor the deck,
that the deck is a named `region`, and that the pill branch renders. Five new
`tests/visual/v3.spec.js` tests cover what jsdom cannot: the popover opens
unclipped and does not scroll the station, the grid fills the deck's inner
width, the cards are `nowrap` chips at 900/760/640/420px and the strip stays
visible at each (a leftover `display: none` in `responsive.css` targets that
class), reduced motion removes both the transform and the transition while hover
still answers in colour, and the aura's blur is smaller than a third of the
organism with its lit box inside the banner.

**Compatibility.** No backend change; `frontend/openapi.json` differs from
10.6.1 only in `info.version`. No route or deep-link change. No feature removed.

## v10.6.1 — First Things (2026-08-03)

10.6.0 gave each main screen one leading panel and stopped short of five screens:
sign-in, the recommended model, the Brain home, the automation runs list, and the
review center. 10.6.1 rebuilds those five on the same rule — what you came for is
the first thing on the screen, everything else sits under it. Nothing was
deleted; the pieces moved.

**Onboarding leads with the one thing to do.** The login screen put a
three-card promise bar between the greeting and the form, so a first-time reader
met two sets of boxes before the one they type in. The form is now the only
raised surface, the promise bar is a quiet hairline strip at the foot of the
screen (`.ritual-promise.is-quiet`), and the two reassurances that used to sit
between the last field and the button read after it. Every input is bound to a
real `<label for>`, the form is named by the page heading, and a failure sets
`aria-invalid` + `aria-describedby` on the fields it applies to instead of
floating above them. The recommendation screen rendered its top pick twice — a
bare CTA above the list, then the same model as the first of three cards — with
nothing saying which one to press. It is one `.ritual-primary-hero-card` now,
holding rank, size, name, reason, time estimate and the button; the remaining
two models sit under a labelled `다른 선택지` grid as compact cards, and 뒤로 /
모델 없이 Brain 열기 split to opposite ends of a footer row instead of queuing up
with the hint that explains them.

**The Brain home leads with the box you type into.** The composer sits in its
own bordered wrapper with a focus ring; the three things to try moved directly
under it, from a centred row of one-line chips into a grid of cards whose second
line is visible instead of living in a `title` tooltip; and add-material plus the
autonomy dial dropped to the station floor as one toolbar. The runs tab stacks by
urgency instead of by data source: `승인함` — previously the last block, under two
tables of finished runs — is first and carries an attention treatment, installed
automations follow with their last run (mode, result, time, summary) inside the
card rather than as a line of prose, and the agent/workflow tables read as
history at the end. The review card was one column ending in a button row, so a
long diff pushed 승인 / 거절 below the fold; it is now a 7/5 split with evidence
on the left and an always-visible decision panel on the right, collapsing back to
one column when an item carries no evidence at all.

**Accessibility came with the structure, not after it.** Each review item is an
`<article>` named by its own `<h3>`, its decision block is an `<h4>` under that,
and the approve/reject cluster is a named `role="group"`; the status and source
filters have visible captions wired through `aria-labelledby` instead of being
two unlabelled tab strips. The Brain home's greeting is the page `<h1>`, and its
header/footer are scoped so they do not register as page landmarks.

**One layout bug the rebuild surfaced.** The project's own stylesheets are
unlayered and Tailwind's utilities live in `@layer utilities`, so a utility class
on a `.ritual-*` / `.brain-*` element loses to the sheet for every property the
sheet sets — and wins for every property it does not. Most of those utilities
were harmless dead code. `p-6` on the Brain home stage was not: the sheet sets no
padding there, so it stacked on children that already pad themselves and pushed
the quiet shelves under the fixed mobile nav, where the tap landed on the nav.
`frontend/src/styles/cssLayering.test.ts` now asserts the project ships no
`@layer`, which is the reason the sheet wins.

**Evidence.** `tests/visual/mock_server.cjs` serves `/api/proposals/counts` and
two installed automations with last-run detail, plus a `change_proposal` review
item carrying a real diff — without them the promoted approval block and the new
evidence column would have been captured empty. `output/release/v10.6.1/` was
re-captured on the rebuilt screens.

## v10.6.0 — Promoted Panels (2026-08-03)

10.5.0 changed the words on each screen. 10.6.0 changes where things sit. Every
main screen opened as a row of equal tabs, which asks a first-time user to choose
before they know what the choices are. Each screen now opens on the panel that
answers the question that brought the reader there, and everything else moves
below it. Nothing was deleted — every panel is still on its page.

**Each screen leads with one panel.** Capture's four page-level tabs became a
single `자료 추가하기` card that holds all three ways to add material — 파일
올리기 / 폴더 연결하기 / 웹페이지 저장하기 — as a choice inside one card rather
than as peers of the progress view; progress, connected folders and recent
documents drop to a quieter second row that is now always visible instead of
hidden behind a tab. Work opens on 검토함, the proposals waiting on the reader,
instead of an empty goal composer, and its heading follows the tab rather than
repeating one sentence everywhere. The model library answers "which model is
running, and can I switch it" in a card above the tab strip, offering only models
already downloaded and runnable on this machine. The Brain home's five stacked
blocks became one bordered station: greeting, composer, the add-material row and
the autonomy dial in a single card that lifts on `:focus-within`, with the Brain
artwork cut from 7rem to 5.4rem so it introduces the composer instead of
headlining the screen. The knowledge graph stopped being a third peer tab and
became a subview behind `연결 지도 열기`, with a labelled way back. Settings'
seven flat tabs became three named groups — 나와 작업공간 · 내 데이터 보관 ·
동작 방식과 연결.

**Everyday and management destinations are no longer one list.** 대화 · 자료 ·
기억 stay in the primary nav; 작업 · AI 모델 · 설정 render as topbar links at
desktop widths and fold into the menu below that. Both copies are built from one
array, so they cannot drift, and `shell.css` decides which one shows from a
single 960px breakpoint, so they can never both appear or both vanish. The menu's
copy is now named 관리 and shares its accessible name with its topbar twin —
before this, the primary nav and the menu list were both called "화면 이동", so a
landmark list offered two indistinguishable navigations.

**Reorganizing the shell surfaced three things that were already broken.**
`#/act/review` had never opened the review inbox: the command palette and the
daily briefing had always emitted the `<screen>/<tab>` form, and nothing parsed
it, so those links fell silently to the Brain home. `parseHash` now resolves that
shape as its last step, after named aliases, so an alias always wins. The command
palette also read a second, private copy of the destination list, which meant
repointing 작업 in the shell left the palette's 작업 on the old target — one label
with two landing places depending on how you reached it. And the menu's focus
trap counted the hidden copy of the management links, putting its boundaries on
elements the browser refuses to focus and sometimes sending initial focus to a
`display:none` anchor, where `focus()` silently does nothing.

**Guarded, not asserted.** The visual sweep walks ten viewport widths from 390px
to 1440px and fails if a management link is visible in both places at once or in
neither, if the topbar overflows, if a navigation landmark is unnamed or shares
its name with another, or if the shell link and the command palette open
different screens for the same label. `tests/visual/mock_server.cjs` gained
`POST /models/load` and `GET /knowledge-graph/local/health` so the one-click
model switch and the folder-health card in Capture's second column actually
render during capture — a route the mock server does not serve is captured as a
placeholder and ships as a broken screenshot.

Backend behaviour is unchanged; `frontend/openapi.json` differs from 10.5.0 only
in `info.version`.

## v10.5.0 — Everyday Words (2026-08-03)

10.4.0 named the ground the code stands on. 10.5.0 renames the ground the
reader stands on. Nothing was deleted — every advanced panel is still one mode
switch away — but the words, the ordering, and the default a first-run user
lands in all changed.

**The autonomy dial is a sentence, not a mode name.** 엄격 / 신뢰 / 바이패스
became 먼저 물어보기 / 웬만하면 알아서 / 거의 다 알아서, each carrying one line
about what it will and will not do without asking. The home-screen dial and the
Settings panel had each been translating the server's catalog copy themselves;
both now read `frontend/src/lib/permissionCopy.ts`, so the same setting cannot
be named two ways. The lookup is by mode id with the server's own localized
label as fallback, which means a mode added server-side still renders — that
fallback has its own test, because a translation table is otherwise an
accidental allowlist.

**A file's path into memory is three named steps.** The 진행 상황 tab was hidden
from plain mode and held two raw API payloads. It now renders an `aria-label`ed
ordered list — 내용 읽기 → 뜻 파악하기 → 기억에 연결하기 — each step saying what
it does to your file and whether it is waiting, working, or done. 파싱 · 임베딩
· 인덱싱 no longer appear on it, and the freshness prompt that used to say
"임베딩 모델이 바뀌었어요 / 다시 인덱싱하기" now says "찾는 방식이 바뀌었어요 /
기억 다시 정리하기".

**Automations carry their own names.** A run is titled by its workflow name
(falling back to goal, then to "n번째 작업") instead of a database id, and its
state is translated per token — `awaiting_approval` reads as "내 승인 기다리는
중", an unrecognized token reads as "알 수 없음" rather than printing itself.
Advanced mode still shows the id beneath the name. Plain mode also gains a
"자동으로 실행되는 작업" summary — workflow name plus "새 자료가 들어오면" /
"정해진 시간에" — in place of the node canvas and JSON box, which stay for
advanced.

**Two smaller honesty fixes.** A model this machine cannot run says so in one
sentence instead of a registry line half-translated word by word (the detector
looks for long English prose with no Hangul rather than trusting the field).
The Brain stats panel answers "저장 위치 / 내 컴퓨터" and "가져가기 / 언제든
내보낼 수 있어요" where it used to print `schema_version` and a storage engine
name.

**The published evidence was itself wrong.** `capture_release_evidence.mjs` set
`lattice.mode = "advanced"` before capturing, so every README screenshot showed
payload panels, storage engines and hook logs that no first-run user is ever
shown. Capture now runs in the app's real default, `basic`. Two frames changed
as a result: the old `09-model-setup-status.png` had become a pixel-level
duplicate of the Brain home (the knowledge-flow strip only expands in advanced),
and is replaced by `09-automation-runs.png`; the new `11-knowledge-journey.png`
publishes the three-step journey. The walkthrough GIF was also being encoded
against ffmpeg's default palette, which rendered the app's ivory background as
dithered yellow and its greens as olive — the README's first image showed a
product that does not exist. It is now encoded from a palette generated off the
clip itself (1.5 MB → 2.8 MB, real colours).

**Guarded by a sweep, not by review.** `tests/visual/v3.spec.js` walks ten
plain-mode routes and fails if any renders empty, shows the service-unavailable
banner, or puts engine vocabulary (파싱, 임베딩, 인덱싱, 벡터, 스키마,
`awaiting_approval`, `retried_ok`, `schema_version`, `sqlite`, …) in front of a
reader who never asked for it. `#/runs` failed that assertion before this pass.

### Verification

| gate | result |
| --- | --- |
| vitest | 339 passed (35 files) · 32.91% |
| pytest (full, coverage) | 1,955 passed · 71.77% (70% floor enforced) |
| playwright (plain-mode sweep + journey) | 2 passed |
| ruff | All checks passed |
| mypy | 274 / 274 modules, 0 errors |
| release evidence capture | 12 screenshots + gif, captured in basic mode |
| docs:check-current | pass |

### Honest limitations

- The approval card under 작업 → 실행 still labels its raw payload fields
  `Action`, `Action Label`, `User Email`. It is visible in
  `09-automation-runs.png`, it is published rather than cropped out, and it is
  the first thing for the next pass.
- The plain-mode sweep checks a word list, not comprehension. It cannot catch a
  sentence that is jargon-free and still unclear.
- Backend behaviour is unchanged this release: no route, request, or response
  schema moved. `frontend/openapi.json` differs only in `info.version`.

## v10.4.0 — Named Ground (2026-08-02)

10.3.0 wrote down what was not measured. 10.4.0 emptied the list.

**The type backlog closed: 1,407 errors across 77 modules → 0 across 274.**
Two root causes accounted for 954 of them, and neither was a typing problem:

* `_kg_common.__all__` was *computed* (`[name for name in globals() if not
  name.startswith("__")]`). Correct at runtime, opaque to a checker — so twelve
  graph modules resolved not one name behind their star import and reported
  ~750 false `name-defined` errors. Freezing it to a literal fixed all of them;
  a test asserts the literal still equals what the expression would produce.
* The eleven graph mixins share a real contract (`_connect`, `_upsert_node`,
  `_v2_project_node`, …) that was written down nowhere, so 229 `attr-defined`
  errors were a checker correctly reporting that each mixin calls methods it
  does not have. `_kg_contract.py` declares those 23 members. It is
  typing-only: `_Core` is `object` at runtime, so the MRO is unchanged, and a
  test asserts that.

**The composition root is no longer one function.** `app_factory._build` went
from **1,318 lines to 26**, split into ten ordered phases sharing a typed
`RuntimeContext`. The single closure worked because closures resolve free
variables at call time — several sections legitimately depend on names bound
150 lines further down. The RuntimeContext preserves exactly that property
while naming the state, and the phase order is a contract with a test: which
phase produces which attribute, and that reading one early fails by name.

**Four real defects surfaced doing it:**

* `core/workspace_os.py` had four lines duplicated after a `return` — dead code
  that made `remove_member` look like it did its work twice.
* **`python -m latticeai.server_app` had never worked.** `main` was a local
  inside the old `_build` closure and was never on the export allowlist, so
  `get_shared_runtime().main()` always raised `AttributeError`. Every test uses
  `create_app()`, so nothing caught it.
* `models/router.py` shadowed a `dict` with a `str` in a loop, so the custom
  cloud-model branch built ids from the wrong variable.
* `lattice_brain/context.py` called an unconfigured retrieval seam unguarded,
  which died as a `TypeError` inside failure isolation without naming itself.

**The surface-parity matrix has no ◐ left.** All three remaining gaps were
rendering gaps, not contract gaps — the sidecar was already reporting the data
and the editor and bot were discarding it. VS Code gained folder capture
(`/api/ingestion/folder`, same approval dance as the web), artifact cards that
distinguish a deterministically repaired scaffold from clean model output, and
a hardware-derived model recommendation with its reasoning. Telegram gained the
same artifact card.

**Coverage moved honestly, not to target.** Frontend 28.5% → **32.3%**
(208 → 337 tests), Python 71.6% → **71.8%** (1,896 → 1,956). The new frontend
tests all went to modules with real logic and no test: routing, the app store's
behaviour when localStorage is disabled, API error translation, and the
knowledge-graph explorer. Frontend 70% is still roughly 2,200 statements away,
mostly large React page components, and this release does not claim otherwise.

### Verification

| gate | result |
| --- | --- |
| pytest (unit) | 1,953 passed |
| pytest (full, coverage) | 1,956 passed · 71.77% (70% floor enforced) |
| vitest | 337 passed · 32.31% |
| mypy | **274 / 274 modules, 0 errors** |
| ruff | All checks passed |
| VS Code extension contract | 19 passed |

### Honest limitations

- Frontend coverage is 32.3%, not 70%. The gap is the large page components.
- `router_registration`'s register functions still take 20–50 keyword
  arguments; that is the next structural target, not this release's.
- Release artifacts are built and validated by tag; package-registry publishing
  stays owner-run.

## v10.3.0 — Measured Ground (2026-07-29)

A release about knowing where things stand. Three numbers this project reported
about itself were wrong, and correcting them was more valuable than any feature
shipped alongside.

**Frontend coverage was never measured.** Vitest reports only files a test
already imports, so a module with no test at all left the denominator rather
than counting against it. The tool said 54%; with `all: true` the honest figure
is **28.5%**. 208 frontend tests now exist (up from 154), including the first
unit tests for every page — settings, memory, library, capture, automation —
via a `renderPage` harness that stubs the whole API surface so error states,
empty states and both languages are reachable without a browser.

**Python coverage was reported as 80%. It is 71.6%.** The `omit` pattern
`*/tests/*` does not match the repo-relative `tests/...` paths coverage
records, so the suite counted itself — and test files execute start to finish
by construction, which inflated the figure by nine points.

**mypy went from 13 modules to 193 of 270**, and found three real defects:

* `lattice_brain/runtime/hooks.py` logged `self._path`, which does not exist,
  *inside the handler for an unreadable registry* — so a recoverable read
  failure raised `AttributeError` instead of falling back to the default.
* `lattice_brain/graph/_kg_fsutil.py` annotated with `Iterable` without
  importing it; latent only because `from __future__ import annotations` defers
  evaluation.
* `lattice_brain/runtime/agent_runtime.py` could call `.get` on `None`.

**`save_to_history` left the composition root.** 66 lines deciding what a
message looks like after redaction, and what the audit log records about it,
were unreachable inside `app_factory._build`. They are now
`runtime/history_writer.py` with 14 tests asserting the order that is the
actual contract: redact before anything else sees the text, audit before the
store write, ingest through the pipeline rather than the store, and never let a
graph failure lose a message that was already saved.

Extracting it also surfaced a real hazard: a nested `def` resolves late-bound
names at call time, and a builder call does not. Two of the dependencies are
bound further down `_build`, so a naive extraction raised `UnboundLocalError`
at import. The closure stays a closure and delegates; the comment says why.

**New tests for boundaries that had none:** the Telegram allowlist (the only
surface reachable from the internet — empty, absent and malformed
configurations must all deny), `run_command`'s containment rules (allowlist,
shell operators, absolute paths, symlink escape, scrubbed environment,
timeout), the audit log and sensitivity report, and the model-load consent
gates. 1,896 Python tests, up from 1,786.

**What is not measured is written down.** `docs/MYPY_BACKLOG.md` lists the 77
modules still outside type checking with per-module error counts, smallest
first. ARCHITECTURE.md gains a Verification Surface diagram showing which
figures gate CI and which are only reported.

**Not done.** `app_factory._build` is still ~1,300 lines. Frontend coverage at
28.5% and Python at 71.6% are both below where they should be. See the release
notes for the honest self-assessment and what each remaining point costs.

## v10.2.0 — Load-Bearing Fixes (2026-07-29)

A full review of 10.1.1 scored the codebase 71/100. This release answers all
twelve findings. The theme is that the most expensive problems here were not
missing features — they were correct-looking code that could not do its job.

**The connection leak.** `with sqlite3.connect(...) as conn` commits or rolls
back and leaves the connection **open**; 70+ sites across the graph store, the
conversation store, the workspace state DB, the storage engines, portability
and migration relied on that idiom. CPython's refcounting freed them promptly
enough to hide it. Anything that keeps a frame alive — a profiler, a logged
traceback, a coverage tracer — delays collection until the process hits
`EMFILE`. `StorageEngine.session()` and the store helpers now close in
`finally`, and seven tests assert it stays that way.

**Coverage, for the first time.** Running `pytest --cov` used to fail ~400
tests, because the tracer holding frames *was* what exhausted descriptors. The
number was therefore unknown. It is **71%**, with a 70% floor wired into CI.

**A privacy guard that could not fire.** `is_node_blocked_for_cloud` was
correct and correctly wired, `HARD_BLOCK_NODE_TYPES` was an empty set, and
nothing in the entire product could set the metadata flags it looks for. The
promise "sensitive memories are never sent" was unfalsifiable. Now: the type
list names credential-shaped nodes; ingestion stamps never-leaves onto files
under `.ssh` / `.aws` / `.gnupg` / `.env` and friends (narrow by design — a
path can prove what a content heuristic only guesses); and the boundary panel
lets a user hold back any memory it lists, which is the one moment they are
looking at exactly what would go.

**Egress was neither redacted nor recorded.** `redact_secret_text` was applied
to logs, audit records and API previews — everywhere except the single path
where bytes actually leave. It now runs on the outbound payload. And every send
writes a `cloud_egress` audit entry *before* the provider is called, naming the
node ids, count, token estimate, provider and mode — shape, never content.
Refusals are recorded too.

**Silence became observability.** 112 handlers discarded their exception with
no trace. `quiet()` keeps the behaviour and logs the exception with its file,
function and line at DEBUG. `except: pass` is now a lint error.

**Also:** the cloud turn ran retrieval twice and discarded the first result;
vector similarity silently truncated to the shorter operand on a dimension
mismatch (now raises, because a plausible-but-meaningless score is worse than
an error); `tempfile.mktemp` — a TOCTOU race — replaced with `mkstemp` in the
Telegram bridge; three closures captured loop variables by reference; ruff
gained B/S/I/SIM/RET/C901 with every remaining finding either fixed or given a
written reason; mypy runs strict-ish over 13 trust-critical modules; CI gained
a macOS runner and Python 3.14; `workspace_os.py` shed 224 lines into two
cohesive modules.

**Not done, and why.** `app_factory._build` is still one 1,343-line function.
Its sections share roughly 34 local bindings, so splitting it means threading
those through or introducing a runtime-context object — an architectural change
that deserves its own release rather than riding along with twelve others. The
C901 exemption names it so the decision is visible rather than implied.

## v10.1.1 — Reachable Boundary (2026-07-28)

10.1.0 shipped the hybrid path's contracts, API, `/chat` branch, policy store,
and token guards — everything except a way for a user to reach any of it. The
dial existed only for whoever called `/api/network-boundary` by hand, so in
practice every user stayed on the `local_only` default without ever being
offered the choice. This release is the missing control.

`NetworkBoundaryPanel` sits in **환경설정 → 내 지식이 나가는 범위**, beside the
autonomy dial, and follows the same rules: it renders the server's own catalog
rather than a hardcoded mode list, and it refuses to send a `cloud_allowed`
switch until the acknowledgement the server requires is ticked — the client
declines the request the server would decline.

It adds one thing the autonomy dial has no equivalent for. Type a question and
`/api/network-boundary/preview` returns the **actual node titles** that question
would send, the token estimate, and whether the token guard would refuse the
turn. The preview works in `local_only` as well, labelled as hypothetical, so a
user can look before deciding rather than after. "Only minimal related nodes
leave" is a promise; a list of which ones is evidence.

The write-back switches (`auto_commit`, `allow_multimodal`) render only once
the boundary permits cloud. A switch that cannot take effect reads as one that
did.

Also removed: `static/app/network-boundary-panel.js`, the unmounted standalone
module 10.1.0 shipped as this feature's "UI". It lived under `static/app/`,
which is Vite's build output directory — `npm run build:assets` wipes that
directory, so a hand-written file there could not survive a build. It was in
fact already deleted by the previous release's asset rebuild.

Behaviour is otherwise unchanged: `local_only` is still the default, the
sensitivity filter is still mode-invariant, and cloud-derived memory still
enters the Review Center as a proposal.

Verification: 12 component tests, 2 Playwright specs driving the real panel in
Chromium (the boundary tab, the acknowledgement gate, and the preview naming
real memories), and mock-server routes so the release screenshots show the
panel working rather than an unavailable state.

## v10.1.0 — Hybrid Brain (2026-07-28)

A feature release that adds a local-first hybrid path: the Knowledge Graph stays
on-device while cloud LLMs become an opt-in worker. The default network boundary
remains `local_only` — cloud use requires an explicit acknowledgement.

- **Network boundary** — `NetworkBoundaryMode` + `MinimalContext` contracts, a
  persisted dial, and runtime wiring so only minimal related nodes ever leave the
  machine.
- **Cloud streaming worker** — an OpenAI-compatible stream adapter with token
  budgets and a token guard; streamed answers expand the local Brain with
  provenance.
- **Hybrid chat path** — `/chat` branches through hybrid policy, context
  assembly, and cloud extraction, writing results back through the Review Queue.
- **Multimodal + UI** — multimodal streaming contracts and a network-boundary
  control panel in `/app`.

Additive and covered by `tests/unit/test_network_boundary.py`,
`test_hybrid_phase2.py`, and `test_hybrid_phase3.py`.

## v10.0.1 — One Source of Truth (2026-07-28)

A patch release with no behaviour change. The single-agent runtime module was
carrying its state machine, its state vocabulary, and ~440 lines of pure
functions in one 82KB file; the pure parts now live in sibling modules and
`latticeai/core/agent.py` holds only the loop (1769 → 1326 lines).

The re-export is the point. Every name callers already imported from
`latticeai.core.agent` — `AgentState`, `normalize_plan`, `extract_action`,
`PhaseBudgets`, `requirement_coverage`, and the rest — still resolves there and
is the same object, so the HTTP layer, `run_store`, `computer_use`, both bench
scripts and eight test modules are untouched. `__all__` now states that contract
rather than leaving it implied.

One real defect was fixed on the way in. The extracted helpers compared
transcript steps against the bare string `"EXECUTING"` rather than
`AgentState.EXECUTING.value`, because the enum lived in the module that imports
them — a circular import. Renaming an enum value would have silently stopped
matching, leaving `files_written` and `artifact_checklist` reporting nothing
while every test still passed. `AgentState` now has its own module
(`agent_state.py`) that both sides import.

All 18 extracted symbols were AST-compared against the originals before the
originals were deleted. Verification: pytest 1747 passed / 11 skipped,
`scripts/agent_eval.py` 23/23 (100%), ruff clean.

Also: home-screen spacing polish (CSS only) — the secondary control row is
visually demoted so one primary action reads first, with thumb-sized spacing
on small screens.

## v10.0.0 — Plain Language (2026-07-28)

10.0.0 is the release where the product stopped being read by its author. Every
screen was opened with a real model loaded (Gemma 4 26B on MLX) and every
control pressed; what broke or read as jargon was fixed.

**The first screen.** The home is four zones — the Brain, the composer, the
autonomy dial, and capture. File / folder / note / web moved into the composer's
own toolbar beside 문서 / 이미지, so "Brain에게 가르치기" is gone as a separate
panel. Nothing graph-shaped renders on the home: the knowledge graph opens by
clicking the Brain itself.

**Both languages, on every screen.** The top bar carries a 한국어 / English
switch. Memory tiers, agent roles, automation recipes, entity types, status
badges and payload field labels are all translated by their stable id, so the
server keeps one vocabulary and the reader sees theirs.

**Measured fixes, not impressions.** The conversation header Brain rendered at
311px because `size="trace"` had no size rule and inherited
`clamp(220px, 28vw, 320px)`; the sticky composer covered the end of every answer
because the stream had no bottom padding; `ValuePreview` printed a nested
object's field names where its value belonged; `pickFolder` called a
desktop-only API and so did nothing in a browser.

**Numbers that say what they counted.** "출처 반영률 12%" became "출처가 남은
기억 · 35 / 291개" with the sentence explaining it, and model coordinates became
model names.

## v9.9.9 — Lean Shell (2026-07-27)

9.9.9 fixes the cause behind the 9.9.8 bundle-budget bump. `frontend/src/i18n/*`
was one synchronous table, so every route's copy sat in the entry chunk by
construction and each new UI surface grew first paint. Namespaces now register
on import and each lazy route pulls only the copy it reads; the admin console
moved behind a lazy boundary too. Initial JS drops from 150.0 KiB to 99.3 KiB
gzip and the budget returns to its original 150 KiB. A new
`check_i18n_namespace_coverage` gate walks the real module graph and fails the
build if a chunk reads a key whose namespace it never imported — the one failure
mode of this split that is otherwise silent.

- 상세: [RELEASE_NOTES_v9.9.9.md](RELEASE_NOTES_v9.9.9.md)

## v9.9.8 — Autonomy Dial (2026-07-27)

9.9.8 gives the agent an explicit autonomy dial — `strict` (default),
`trusted`, `bypass` — layered over the existing ToolRegistry and Change
Governor rather than replacing them, and set from 환경설정 → 에이전트 자율성.
Circuit breakers stay mode-invariant. The release also fixes four defects found
reviewing the feature branch: an unscoped resolver that made stored per-user
overrides inert, orphan proposals left in the Review Center under
trusted/bypass, a `permission_mode` override that `__slots__` made unsettable,
and a lock re-entry deadlock that hung every `POST /api/permission-mode`. The
gates are implemented in `SingleAgentRuntime` directly rather than patched onto
it at construction time.

- 상세: [RELEASE_NOTES_v9.9.8.md](RELEASE_NOTES_v9.9.8.md)

## v9.9.7 — No Gaps Left (2026-07-27)

9.9.7 closes every `✖` the 9.9.6 parity matrix recorded plus the documented
design boundaries: `/agent` SSE with a live VS Code step timeline and
evidence→action in the editor, Telegram grounding badge and Review Center,
recall + approval visibility in the browser extension, a four-bed knowledge
garden, a compact agent profile for small local models with a direct-path
fallback, per-folder memory state, two pay-off-on-install skills, and voice
memo capture with honest degradation when no local transcriber exists.

- 상세: [RELEASE_NOTES_v9.9.7.md](RELEASE_NOTES_v9.9.7.md)

## v9.9.6 — Same Brain Everywhere (2026-07-27)

9.9.6 answers the 2026-07-27 full-stack review: VS Code/Telegram surface
parity (recall grounding badge, Review Center, agent run summary), evidence →
action one-click follow-ups, plain-language run outcomes with a concrete next
step, sentence-aware prose chunking plus document locators in citations, one
context contract shared by chat and document generation, evidence-classified
graph relations, persistent project sessions, three closed agent loops
(re-search, critic requirement coverage, failure learning), funnel alerts, and
embedding-swap recovery UX.

- 상세: [RELEASE_NOTES_v9.9.6.md](RELEASE_NOTES_v9.9.6.md)

## v9.9.5 — Closed Gaps (2026-07-26)

9.9.5 closes the seven residual gaps left by 9.9.4 Durable Loops: sidecar
Playwright nightly E2E, optional cross-encoder rerank, mid-run workspace
awareness (L5), rollback none|git|snapshot (L7), critic↔artifacts meta
checklist (L4), VS Code/Telegram approval flows (SURFACE_PARITY), and
unification of legacy human_in_loop onto the durable approval store (L1).
Also finishes the knowledge-graph retrieval_reads decomposition.

- 상세: [RELEASE_NOTES_v9.9.5.md](RELEASE_NOTES_v9.9.5.md)

## v9.9.4 — Durable Loops (2026-07-26)

9.9.4 ships every improvement wave from the 2026-07-25 full-stack review:
durable approval/run persistence that survives restarts (hashed tokens, 410
replan hints, `GET /agent/approvals`), a bounded executor context window, a
single RetrievalPolicy consumed by both fusion layers (rule-based query
rewrite + recency age-decay), manifest-aware plan rewriting plus React/Vite
and Python-package manifests, live `agent_step` SSE streaming with a step
timeline UI, type-aware chunking (markdown headings / code boundaries, PDF
page metadata) with byte-identical plain chunks, embedder fingerprinting with
honest `stale_embedder` signals, citation-instructed answers with a grounding
bench gate, periodic noise-curate suggestions, a review-before-promote mode
for graph promotions, five workflow harness scenarios, a funnel soft gate,
approval TTL countdown/replan UX, source→chunk drill-down, watch health
signals, and a weekly fail-open real-model agent smoke.

- 상세: [RELEASE_NOTES_v9.9.4.md](RELEASE_NOTES_v9.9.4.md)

## v9.9.3 — Closed Loops (2026-07-22)

9.9.3 ships the complete 22-item backlog left by the 2026-07-21 full-stack
review: multi-file project generation with bundle validation and zip download,
an interactive `awaiting_approval` flow (pause + token-gated resume instead of
FAILED), a 30-second First Value Loop over a built-in demo corpus, honest
answer grounding badges, query-class retrieval fusion behind a benchmark CI
gate, opt-in folder watch, capture-quality CTAs, graph noise curation,
automation dry-run/"run now" with surfaced last executions, inline file
previews, global drag-and-drop, 409 rebase UX, a11y/reduced-motion coverage,
per-phase token budgets, expanded filegen extensions with `ast.parse`
validation, and a deeper harness (23 agent_eval scenarios, golden sanitize
fixtures, multi-model filegen bench, deterministic knowledge-pipeline E2E,
funnel metrics).

- 상세: [RELEASE_NOTES_v9.9.3.md](RELEASE_NOTES_v9.9.3.md)

## v9.9.2 — Artifact Trust (2026-07-21)

9.9.2 unifies every file write behind the model-agnostic validation pipeline
(`sanitize_write_content` — the agent JSON loop now shares the direct chat
path's extract→validate→repair guarantee), makes the chat surface
artifact-first (`artifacts[]` contract, auto-suffix instead of silent
overwrite, optional Brain indexing of generated files), enforces the minimal
plan schema with a deterministic file-step fallback, filters trivial agent
learnings before they enter the Brain, and renders honesty in the UI:
"Auto-repaired" badges on file cards and an unmistakable warning strip for
`NEEDS_REVIEW`/`FAILED` runs. The FG-01..FG-08 scenario matrix from the
2026-07-21 review is pinned as a permanent unit-test harness.

- 상세: [RELEASE_NOTES_v9.9.2.md](RELEASE_NOTES_v9.9.2.md)

## v9.9.1 — Clean Foundations (2026-07-21)

9.9.1 removes the legacy root-shim layer (12 of 13 tracked shims deleted;
`server.py` alone remains for `uvicorn server:app`), adds a legacy debt gate
to `npm run lint`, and polishes the product surface: a "First 5 minutes"
guided card and the daily briefing on the empty Brain home, proactive Cmd+K
quick actions, a Review Center with human-language labels/framed diffs, and a
localized error pipeline so failures speak plain ko/en. The test suite is
scenario-named (37 files renamed), `output/release/` keeps only the newest
three evidence sets via an automated retention policy, and the current-release
docs gate now also verifies the ARCHITECTURE artifact map.

- 상세: [RELEASE_NOTES_v9.9.1.md](RELEASE_NOTES_v9.9.1.md)

## v9.9.0 — Fail-Closed Trust (2026-07-21)

9.9.0 makes the "trustworthy autonomy / honest knowledge" promises enforceable
by fixing two P0 trust defects, governing every mutating tool, and making
onboarding honest about unverifiable hardware — plus supply-chain, benchmark,
and doc-integrity groundwork.

### Change proposals can't overwrite your edits (P0)

- Proposals record `base_sha256` + `base_exists`; approval re-hashes the disk
  and rejects a changed/deleted/created target with a **409 conflict** instead
  of overwriting newer content. Writes are atomic (`os.replace`); duplicate or
  concurrent approvals apply exactly once (replay → 409).

### A confused verifier never reports success (P0)

- Unparseable critic output no longer fabricates PASS/DONE: one strict repair
  retry, then terminate as the new `NEEDS_REVIEW` state. `DONE` requires a
  valid PASS **and** execution evidence; the loose `next_state == DONE` success
  path is removed.

### Every mutating tool is governed (P1)

- `MUTATING_TOOL_INVENTORY` classifies all side-effecting tools; a CI gate
  fails closed on any ungoverned new mutator. Existing-content overwrites that
  can't be staged as a proposal (`create_docx/xlsx/pptx/pdf`, `local_write`)
  are blocked (409) at dispatch; new-file creation is unaffected.

### Honest onboarding (P1)

- Device analysis is `loading | ready | unavailable`; a failed probe shows the
  cause + retry + "continue without a model" instead of a fabricated
  `supported: true` card.

### Leaner, audited, honest (P2)

- Initial JS bundle −22% (180.3 → 141.6 KiB gzip) with a CI budget gate.
- `dependency-audit.yml` (pip-audit + npm audit + CycloneDX SBOM) and
  `postgres-integration.yml` (scheduled pgvector) workflows; all actions
  SHA-pinned. `docs/SECURITY_AUDIT.md`, `docs/BENCHMARKS.md` +
  `scripts/bench_models.py`, `docs/USABILITY_AUDIT.md`, and doc status/link
  classification. Eval separates correct-completion from safe-termination.

### Verification

- 1287 unit / 39 frontend / integration 3 passed (11 skipped, live PG on
  schedule) green; agent eval 20/20 @ 1.0; ruff, tsc, frontend lint, i18n,
  bundle budget, OpenAPI drift, current-release + doc-status gates pass;
  pip-audit + npm audit 0 vulns.

### Honest limitations

- External pentest and real user interviews were out of autonomous scope
  (substituted by a static security scan and heuristic usability audit);
  live PostgreSQL and per-model long benchmarks run via scheduled CI /
  harness rather than locally.

### Artifacts (exact filenames)

- `dist/ltcai-9.9.0-py3-none-any.whl`
- `dist/ltcai-9.9.0.tar.gz`
- `ltcai-9.9.0.tgz`
- `dist/ltcai-9.9.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_9.9.0_aarch64.dmg`

## v9.8.0 — Honest Knowledge Pipeline (2026-07-20)

9.8.0 makes the file→folder→web→graph→RAG→automation pipeline honest and
robust end to end: extraction-quality scoring on every ingest, resumable
background folder jobs with a jobs API and UI progress, chat answers that
disclose limited graph context, visible vector-index freshness, a 16-scenario
agent evaluation gate, and confidence-scored automation suggestions.

### Ingestion quality & robustness

- Every ingest result carries `extraction_quality` (score/level/reasons from
  pure heuristics; upstream confidence takes precedence) plus `warnings` on
  low-quality captures; the proactive quality gate runs observe-only and
  records `quality_gate` verdicts without changing behavior.
- Background jobs track progress (`total`/`processed`/`failed`/errors),
  survive per-item failures, and resume from remaining items.
- New endpoints: `GET /api/ingestion/jobs`, `GET /api/ingestion/jobs/{id}`,
  `POST /api/ingestion/jobs/{id}/resume`, `POST /api/ingestion/folder`
  (approval-gated local-disk access, `background: true` → `job_id`).

### Honest RAG & freshness signals

- Chat answers compute `context_quality` (mode/nodes/limited/reason) —
  top-level in non-stream responses and in the final SSE trailer; the UI
  shows a small note when graph context is limited.
- `GET /api/brain/vector-freshness` reports ready/pending/unavailable with
  pending counts and never raises; the Brain views show a pending-indexing
  chip refreshed after ingests.

### Evaluation & automation quality

- `scripts/agent_eval.py` grew 12 → 16 deterministic scenarios (ingestion
  chain, concept extraction, RAG-grounded answer with a gate-proving negative
  test, automation proposal-first) with grounding assertions against canned
  tool fixtures.
- Automation suggestions gain deterministic `confidence`,
  `confidence_factors` (including KG grounding), duplicate suppression,
  installed-recipe detection, and a low-quality floor; responses report a
  `quality` block.

### Product & docs

- README rebuilt media-first (hero GIF + screenshot grid, ~60% less prose).
- Frontend: freshness chip, low-extraction warnings, context-quality note in
  the assistant bubble, jobs progress panel with resume — all ko/en i18n.

### Verification

- 1263 unit / 27 frontend tests green; ruff, tsc, frontend lint, i18n
  parity/literal, OpenAPI drift, and current-release docs gates pass;
  agent eval 16/16 at success rate 1.0.

### Artifacts (exact filenames)

- `dist/ltcai-9.8.0-py3-none-any.whl`
- `dist/ltcai-9.8.0.tar.gz`
- `ltcai-9.8.0.tgz`
- `dist/ltcai-9.8.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_9.8.0_aarch64.dmg`

## v9.7.0 — Proactive Hybrid Brain (2026-07-20)

9.7.0 deepens the Brain along three tracks: unified hybrid retrieval that
keeps itself indexed, proactive graph-layer quality intelligence, and a
change-governance loop that is now closed end-to-end in the Review Center.

### Unified hybrid retrieval + self-syncing vector index

- `KnowledgeGraphStore.hybrid_search()` — one graph-layer entrypoint fusing
  lexical `search()` and `vector_search()`: scores normalized to [0,1],
  alpha-weighted fusion, chunk hits rolled up to parent nodes, per-source
  `scores`/`fusion` provenance on every match, workspace-scoped throughout.
  Falls back to `mode: "lexical_only"` (with detail) when the vector side is
  unavailable. `context_for_query(use_hybrid=True)` opts the context builder
  into it; the default path is byte-identical to 9.6.0.
- `index_node_incremental(node_id)` + automatic post-ingest sync in
  `IngestionPipeline` (`auto_vector_index=True`, env
  `LATTICEAI_AUTO_VECTOR_INDEX`): each successful non-duplicate ingest
  indexes just the new node's chunks; vector failures never fail the ingest —
  they downgrade `indexing_status` to `pending` so `rebuild_vector_index`
  backlog discovery picks them up.

### Folder & web ingestion

- `IngestionPipeline.ingest_folder(root, recursive=True, background=False)` —
  directory walk with `.latticeignore` (gitignore-like globs, `dir/` prunes,
  `#` comments), hard skip-list (`.git`, `node_modules`, `__pycache__`, venvs,
  build dirs), hidden-by-default, size/extension filters, capped error
  reporting, and optional scheduling on the background ingestion queue.
- `ingest_web_page(url, extracted_text)` — formalizes the web seam: fetching,
  cleaning, and layout parsing belong upstream (browser extension / tools);
  the graph layer receives extracted text and owns structuring + concepts.
  The module docstring now states this parsing-depth contract explicitly.

### Proactive Brain in the graph layer

- New `lattice_brain/graph/proactive.py` (`ProactiveBrain`): duplicate
  detection (content-hash exact + token-signature near-duplicates,
  sub-quadratic sampling), contradiction detection (negation and temporal,
  reusing `lattice_brain/quality.py`), a combined JSON-safe
  `quality_report()` (duplicates + contradictions + stale nodes + edge
  quality), and `consolidate_duplicates()` merge planning — proposal-first,
  plan-only until the store grows a safe merge primitive (auto-detected).
- New read endpoints `GET /api/brain/duplicates` and
  `GET /api/brain/quality-report`; `/api/brain/contradictions` and
  `/api/brain/consolidate` gain graph-layer results additively.
- `gate_ingest_candidate()` — a pure quality-gating seam (ingest /
  skip_duplicate / review) ready for ingestion-time adoption.

### Closed change-governance loop

- Review Center approval of a `change_proposal` item now delegates to
  `ChangeProposalService.approve_and_apply` — the staged content actually
  lands on disk through the single application path (409 on replay); before
  9.7.0 the review-queue approve only flipped the status.
- Proposals carry full provenance: tool, risk, change class, originating
  conversation id; reject accepts a reason (recorded); new counts endpoints
  (`GET /api/proposals/counts`, `GET /automation/reviews/counts`) badge the
  review inbox; a proposal detail endpoint serves diff + staged content.
- Frontend: `change_proposal` source filter, unified-diff preview, tier and
  deletion badges, reject-with-reason input, pending-count badge — ko/en
  i18n parity maintained.

### Agent-loop evaluation & runtime consistency

- `scripts/agent_eval.py` gate: 8 → 12 scenarios, adding file-generation
  happy path, file-generation failure recovery, a 3-step multi-step workflow
  chain with exact ordered tool-call assertions, and a governed-write
  proposal path pinning the approve()-excludes-governed-tools invariant.
- `SingleAgentRuntime.execute` (206 lines) decomposed into six focused
  helpers with zero behavior change; the multi-agent orchestrator now
  surfaces the real failure reason in `execution_failed` timeline events; new
  `test_runtime_consistency.py` pins contract-envelope, status-vocabulary,
  and fail-closed parity between the single- and multi-agent runtimes.

### Structure, performance & housekeeping

- All 10 root legacy modules (`knowledge_graph.py`, `kg_schema.py`,
  `llm_router.py`, `mcp_registry.py`, …) now emit `DeprecationWarning`
  naming their package replacement; the legacy-compatibility registry tracks
  all 13 shims.
- `scripts/profile_kg.py` — offline synthetic KG profiler (p50/p95 latency +
  tracemalloc peaks for ingest/search/context/traverse/vector phases);
  measured baseline recorded in `docs/PERFORMANCE.md`, which also names
  brute-force `vector_search()` as the first optimization candidate at scale.

### Verification

- 1201 unit / 13 integration / 19 frontend tests green; agent-loop-eval
  12/12; brain-quality-eval, readiness, docs, i18n-literal, openapi-drift,
  ruff, and frontend lint gates all pass.

## v9.6.0 — Trusted Agent Loop (2026-07-20)

9.6.0 engineers trust into autonomous work along four tracks: loop
observability, weak-model robustness with a real evaluation harness,
proposal-first change governance, and structural housekeeping.

### Agent loop observability (`loop` payload)

- `latticeai/core/agent_trace.py` — `LoopTrace` records typed events for
  every run: llm calls, parse errors (recovered or not), named format
  repairs, corrections, tool outcomes (ok / error / blocked / proposed),
  retries, approval and verdict decisions, rollback results.
- The agent API returns `loop` (trace summary) with both the
  waiting-approval and final responses.

### Weak-model robustness + evaluation harness

- `extract_action_details` adds python-literal repair (single quotes,
  True/False/None via `ast.literal_eval`) and reports every tolerance used
  by name; a second formatting slip escalates the correction with the exact
  valid tool list.
- `scripts/agent_eval.py` (new CI gate) drives the real SingleAgentRuntime
  through 8 deterministic scripted scenarios — happy path, weak-model format
  gauntlet, prose-slip recovery, correction escalation, destructive block,
  loop detection, critic retry, unrecoverable garbage — and fails the build
  unless all pass.

### Proposal-first change governance (`/api/proposals`)

- `latticeai/core/tool_governor.py` centrally classifies every governed
  call: read / additive / mutation / destructive.
- Additive creates (new files) now run with minimal friction in the agent
  loop; mutations and deletions of existing files are staged as review
  proposals (review-queue source `change_proposal`) with a unified diff,
  exact staged content, and a small/large tier. Approve applies exactly what
  was reviewed; reject discards; nothing touches disk while pending.
- The Brain home gains the "변경 제안 / Change proposals" panel with diff
  previews and one-click approve/reject; proposals also appear in the Act
  review center.

### Structure & process

- Ruff per-file lint ignores trimmed from 9 entries to 3 (all dead ignores
  removed; the one remaining legacy monolith is scoped to `E702` only).
- AGENTS.md carries a machine-checked current-release marker and agent-loop
  invariants, enforced by `scripts/check_current_release_docs.mjs`.

### Verification

- New tests: `test_agent_trace.py` (11), `test_agent_eval.py` (4),
  `test_change_proposals.py` (15), `PendingProposalsPanel.test.tsx` (2).
- Full sweep: 1127 unit / 13 integration / 19 frontend / 18 visual tests,
  agent-loop-eval + brain-quality + readiness + docs gates green, live-boot
  smoke on `/api/proposals`.

## v9.5.0 — Command Center (2026-07-20)

9.5.0 puts the whole Brain one keystroke away. A new read-only, deterministic
Command Center surface condenses every product area into two endpoints, and
the app gains a Cmd+K command palette plus a Today's Briefing panel.

### Command Center (`/api/command/*`)

- `GET /api/command/briefing` — one payload answering "what does my Brain see
  today?": recent knowledge from the scoped graph, conversation activity,
  automation enabled/draft counts, pending review items, a Brain-health
  snapshot, top automation suggestions, and state-derived quick actions with
  stable ids. Each section degrades independently when a backend is
  unavailable.
- `GET /api/command/search?q=…` — universal search grouping results across
  knowledge nodes (scoped keyword search), the user's own conversations
  (deduped per conversation, newest first), and installed automations. All
  reads are scoped to the requesting user and workspace.

### Command Palette + Today's Briefing (frontend)

- Cmd+K (or Ctrl+K) opens a command palette with grouped results
  (지식/지난 대화/자동화/화면 이동), keyboard navigation, and one-press page
  jumps; typing queries the universal search with debounce.
- The Brain home gains a collapsible "오늘의 브리핑 / Today's briefing" panel:
  stat chips (questions, automations on, awaiting review, Brain health),
  recently added knowledge, waiting automation suggestions, and one-click
  quick actions derived from actual product state.
- Fully ko/en localized; no model calls, no writes, no external actions.

### Verification

- New `tests/unit/test_command_center.py` (11 tests) and
  `CommandPalette.test.tsx` (3 component tests) cover section independence,
  scoped reads, quick-action derivation, search grouping/dedupe/scoping, and
  palette keyboard interaction.
- Full sweep: 1097 unit / 13 integration / 17 frontend / 18 visual tests,
  lint + typecheck + docs + readiness gates green, live-boot smoke on both
  new endpoints.

## v9.4.0 — Question-Driven Everyday Automation (2026-07-20)

9.4.0 makes automating daily life effortless. The Brain now watches what the
user actually does — the questions they keep asking and the knowledge folders
they keep feeding — and proposes concrete automations with the user's own
words as evidence.

### Automation Intelligence (`/api/automation/*`)

- `GET /api/automation/patterns` — deterministic local mining of recurring
  question intents from the user's chat history (token-signature clustering,
  Korean+English aware, no model call). Each pattern carries its literal
  example questions, count, and last-asked time.
- `GET /api/automation/suggestions` — recurring patterns become one-click
  suggestions: digest/status/follow-up intents map to the matching starter
  recipe, any other repeated question becomes a "scheduled answer"
  automation, and connected knowledge folders with indexed files become
  folder-digest suggestions triggered when new knowledge arrives.
- `POST /api/automation/install` — idempotent, consent-first install: each
  accepted suggestion is created as a disabled draft workflow (trigger →
  draft agent → review output) with review-queue gating, local-only /
  no-external-actions flags, and provenance metadata (`suggestion_id`), via
  the same validated WorkspaceOS workflow path as the starter recipes.
- `GET /api/automation/overview` — one payload for the automation surface:
  suggestions, installed automations with enable state, and consent
  contract.

### Intuitive automation surface

- The Act page's recipes tab now opens with "Automation suggestions for
  you": evidence chips ("you asked this 7 times", "a folder with 42 files in
  your Brain"), cadence labels, and a one-click Create button that produces
  a reviewable draft. Fully ko/en localized; visible in basic mode.

### Scope and safety

- History mining is scoped: `user_email` + workspace boundaries flow into
  the conversation-store query; legacy-global rows are excluded for scoped
  reads.
- Suggestion ids are deterministic, so re-requesting suggestions or double-
  clicking install never duplicates workflows.
- Nothing runs on its own: accepted suggestions stay disabled until the user
  explicitly enables them, and enabled runs still land in the review queue.

### Verification

- New `tests/unit/test_automation_intelligence.py` (10 tests): clustering,
  intent mapping, scoping, stable ids, install marking, consent-first
  definitions, overview, and no-backend degradation.
- Full sweep: 1086 unit, 13 integration, 14 frontend vitest, 18 playwright
  visual tests passing; lint/typecheck/docs/readiness gates green; live-boot
  smoke on all four new endpoints.

The exact 9.4.0 release artifacts are:

- `dist/ltcai-9.4.0-py3-none-any.whl`
- `dist/ltcai-9.4.0.tar.gz`
- `ltcai-9.4.0.tgz`
- `dist/ltcai-9.4.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_9.4.0_aarch64.dmg`

## v9.3.0 — Proactive Brain Intelligence (2026-07-20)

9.3.0 turns the Brain from a passive store into an active steward of its own
knowledge. The previously dormant `lattice_brain.quality` layer (dedupe,
merge, conflict/temporal-contradiction detection, edge quality) is wired into
router-facing capabilities, and the core recall path gains semantic evidence.

### Brain Intelligence (`/api/brain/*`)

- `GET /api/brain/health` — scored diagnosis across freshness (stale-node
  ratio), connectivity (orphan-node ratio), embedding coverage (vector-index
  scale), and consistency (duplicate/contradiction pressure), with an overall
  grade and recommended care actions. Every number is read from live stores;
  missing stores degrade the dimension to `unavailable`.
- `GET /api/brain/insights` — proactive digest: recent knowledge growth,
  trending node types, stale knowledge, disconnected (orphan) nodes, and
  suggested questions grounded in real node titles.
- `GET /api/brain/contradictions` — negation/preference conflicts and
  temporal contradictions across workspace memories, plus explicit
  CONTRADICTS edges from the graph, each with evidence snippets.
- `POST /api/brain/consolidate` — duplicate-memory and duplicate-edge
  detection. Dry-run by default; `apply=true` prunes only exact duplicate
  workspace memories through the audited MemoryService path and never mutates
  graph content.

### Hybrid recall

- `POST /api/memory/recall` blends vector similarity into the lexical
  ranking (`hybrid-evidence/v2` quality gate). Semantic hits surface
  knowledge phrased differently from the query; vector matches are
  workspace-scoped through `filter_scoped_nodes` before they can influence
  results; each row reports its `evidence_kinds` (lexical/semantic); and any
  vector-tier failure degrades recall honestly back to `lexical-evidence/v1`.

### Brain surface

- New "Brain intelligence check" panel beside Brain care: plain-language
  health grades, per-dimension scores, activity/attention chips,
  recommended care actions, and duplicate-cleanup preview/apply. Fully ko/en
  localized.

### Verification

- `tests/unit/test_brain_intelligence.py` (14 tests) covers health scoring,
  scoped graph reads, insights, contradiction pairs, consent-first
  consolidation, and hybrid recall (blend, merge, scoping, degradation).
- Full sweep on this release: 1076 unit, 13 integration, 14 frontend vitest,
  18 playwright visual tests passing; lint/typecheck/brain-quality-eval/
  product-readiness gates green; all four new endpoints exercised against a
  live-boot app.

The exact 9.3.0 release artifacts are:

- `dist/ltcai-9.3.0-py3-none-any.whl`
- `dist/ltcai-9.3.0.tar.gz`
- `ltcai-9.3.0.tgz`
- `dist/ltcai-9.3.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_9.3.0_aarch64.dmg`

## v9.2.0 — Model-Agnostic File Generation (2026-07-20)

9.2.0 makes "create a file" work reliably with any loaded LLM, including small
local models (gemma/qwen class) that previously produced broken HTML or chat
wrappers instead of file content.

### File generation pipeline

- New `latticeai/core/file_generation.py` module treats every model reply as
  untrusted content: extension-aware strict prompting (the prompt pins the
  exact first line, e.g. `<!DOCTYPE html>`), extraction of the real payload
  from Markdown fences, `<think>`/reasoning blocks, and conversational
  framing, per-type structural validation (complete HTML documents, parseable
  JSON, CSS rule blocks, fence-free code), one corrective retry that feeds the
  rejection reason back to the model, and a deterministic repair fallback
  (truncated HTML is closed, fragments are wrapped in a valid scaffold,
  invalid JSON is recovered or re-encoded) so the user always receives a
  structurally valid file.
- Chat file requests that name a type but no filename ("html 파일 만들어줘",
  "웹페이지 만들어줘") now resolve to an inferred target and run on the
  deterministic direct-write path instead of the model-driven agent JSON
  loop. File-generation temperature is clamped and the token budget raised so
  documents complete.
- The `/chat` direct-write response reports `generation` metadata (attempts,
  validation reasons, whether deterministic repair ran), and the confirmation
  message discloses when auto-repair produced the saved file.

### Agent loop hardening

- `extract_action` strips `<think>` blocks before locating the action JSON and
  tolerates trailing commas.
- The executor no longer aborts the run on the first malformed action reply:
  up to two corrective format reminders are fed back through the corrections
  channel before halting.
- The executor prompt pins exact `write_file` content rules (complete raw
  content, no fences, extension-valid documents).

### Tests

- `tests/unit/test_file_generation.py` covers extraction, validation, repair,
  filename inference, prompt anchoring, and the retry/repair orchestration
  (22 tests). Full unit suite: 1062 passing.

The exact 9.2.0 release artifacts are:

- `dist/ltcai-9.2.0-py3-none-any.whl`
- `dist/ltcai-9.2.0.tar.gz`
- `ltcai-9.2.0.tgz`
- `dist/ltcai-9.2.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_9.2.0_aarch64.dmg`

## v9.1.0 — Code Review Completion & Fail-Closed Runtime (2026-07-11)

9.1.0 completes every actionable item in
`docs/reviews/CODE_REVIEW_2026-07-11.md`. The release makes network, workspace,
invitation, and tool boundaries fail closed; replaces ambient runtime and model
state with typed ownership; decomposes the chat and frontend hotspots; makes
service failures visible and testable; and removes tracked release/review
clutter without rewriting historical release records.

### Security and access control

- Telegram messages and callback queries are denied unless their chat ID is in
  the required `LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS` allowlist. Allowed chats
  are registered only after authorization, and the bridge authenticates to the
  local server with the required `LATTICEAI_SERVER_SESSION_TOKEN`.
- Invitation authorization uses a signed, expiring server-bound value instead
  of trusting `authorized=true`; the built-in invitation code is removed and
  an enabled public invitation gate uses either an explicitly configured random
  code or a generated per-install secret persisted with private permissions.
  New SSO accounts must carry the same verified invite authorization, bound to
  the server-side one-time OIDC state, nonce, and PKCE transaction.
- Knowledge Graph scope lookup and unknown v2 nodes fail closed. Legacy-global
  reads require an explicit compatibility opt-in and have regression coverage
  for projection failures and cross-workspace isolation.
- Computer screenshot/status, knowledge and Obsidian tools, and chat network
  status now pass explicit capability, consent, user, workspace, or policy
  gates instead of relying on permissive auto-approval.
- Permission notifications disclose only token hints and can link to the
  optional `LATTICEAI_PERMISSION_UI_URL`; queue persistence is atomic and
  private. Non-loopback cookies are secure, reconnaissance endpoints redact or
  require authentication, and MCP paths are masked.

### Runtime and maintainability

- App assembly is expressed as typed config, security, Brain, model, and router
  stages. The legacy `server_app` surface remains an explicit compatibility
  allowlist and no longer depends on exporting `locals()`.
- Model selection and loading use injected typed state rather than dual-synced
  module globals, with API error translation kept at the HTTP boundary.
- Chat contracts, history, documents, and streaming live in focused modules;
  the route layer delegates to services instead of owning every chat concern,
  and agent/Computer Use records keep authenticated user/workspace ownership.
- Shallow runtime pass-through modules and repeated timestamp/status utilities
  are consolidated. Root setup and local-knowledge modules are compatibility
  shims over package-owned implementations.
- AgentRuntime naming is explicit, high-cost broad exception paths log or fail
  closed, and readiness gates check forbidden architectural patterns as well as
  symbol presence.

### Frontend reliability and repository hygiene

- Failed API results render unavailable/error states rather than healthy empty
  Brain data. Proof attachment, continuity checks, and action callbacks report
  success only after an `ok` response, with a core-service unavailable banner
  for critical queries.
- Brain logic is split into focused hooks, translations into namespaces, and
  experience styling into surface files. User-facing strings use i18n and the
  version is injected from package metadata.
- Vitest coverage now protects API empty shapes, proof parsing, conversation
  sessions, primitives, and i18n; visual coverage asserts that failed services
  do not become quiet-success UI.
- Obsolete local VSIX files are removed, ignored build/audit/workspace trees stay
  outside release archives, Electron is documented as an experimental
  compatibility shell, and review documents are archived under `docs/reviews/`.

The exact 9.1.0 release artifacts are:

- `dist/ltcai-9.1.0-py3-none-any.whl`
- `dist/ltcai-9.1.0.tar.gz`
- `dist/ltcai-9.1.0.vsix`
- `ltcai-9.1.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_9.1.0_aarch64.dmg`

The following product and isolation work is also included in 9.1.0:

- Reframed Brain Home around the product's actual knowledge lifecycle: chat,
  files, folders, notes, and web pages visibly enter the Living Brain, then
  resolve into a lightweight graph built only from real Knowledge Graph nodes
  and edges.
- Added truthful ingestion emergence evidence, native desktop folder selection,
  persistent conversation-to-knowledge traces, grounded next actions, and
  desktop/mobile layouts that keep the Brain—not a dashboard—the protagonist.
- Rebuilt the empty Brain home as a one-viewport organism: source capture,
  composer, real graph, life signal, and the primary memory-grounded action stay
  visible without page scrolling, while history and deeper proof open as
  overlays. Continuous breathing, heartbeat, sparks, and Brain-to-graph pulses
  now accelerate from real listening, recall, synthesis, and action state.
- Preserved access to every memory-grounded action in the compact command deck;
  the first action stays one click away and the complete reviewed set opens in a
  focused popover instead of extending the page.
- Brain automation recipes are now created as reviewable disabled drafts and
  require an explicit enable action. Triggered and review-queue runs execute a
  real agent pipeline grounded by scoped MemoryService recall, with researcher,
  planner, executor, and reviewer roles.
- Normalized ingestion hook names and provenance so chat, upload, local-folder,
  note, web, and legacy sources can drive workspace-scoped recipes without
  treating failed ingestion as knowledge.
- Local-folder hierarchy, file, chunk, concept, and semantic nodes now carry
  workspace-scoped identities. Automation events validate the same write scope,
  persist it through watcher restarts, and stay isolated by workspace and owner;
  legacy personal-folder nodes reproject in place without destructive ID rewrites.
- Enabling a reviewed recipe preserves the user's edited prompt, roles, name,
  and nodes; empty web captures remain visibly unsuccessful.
- Replaced the floating hamburger/drawer shell with visible desktop task
  navigation and a mobile bottom bar for Chat, Sources, Memory, and Work; model,
  settings, workspace, and admin utilities remain available in an accessible
  secondary menu.
- Rebuilt Brain Home around a large composer, contextual starters, recent
  conversations, visible source capture, the living knowledge flow, and grounded
  automation. Deeper memory rings and runtime proof still use progressive
  disclosure.
- Memory opens on search instead of the graph, basic Work opens on a single goal
  composer instead of runtime metrics, and basic Sources uses a one-column add
  flow with technical pipeline controls hidden.
- Added keyboard focus trapping/restoration for the secondary menu, semantic
  tab roles and arrow-key navigation, skip navigation, 44-pixel mobile targets,
  reduced-motion handling, and desktop/mobile visual regression coverage.
- Consolidated the new shell, conversation, and content grammar in a dedicated
  `experience.css` layer while keeping feature-specific legacy visualization
  styles compatible.

- Model generation now snapshots the requested model per request, so concurrent
  chat, streaming, and document jobs cannot switch each other's process-wide
  model state.
- Chat, upload, browser capture, graph ingestion, Brain Network, portability,
  MCP, realtime presence, shared registries, hooks, model lifecycle, and
  permission decisions now enforce authenticated identity, active workspace
  scope, or administrator ownership as appropriate.
- Knowledge Graph IDs for new workspace-scoped messages, documents, people,
  concepts, structured document children, and events include workspace identity;
  legacy unscoped IDs remain readable and are not destructively migrated.
- Web URL capture now rejects private/reserved DNS targets and rebinding,
  revalidates redirects, disables environment proxies, and enforces a streamed
  4 MiB response limit.
- Integration/OpenAPI generation runs in disposable state, committed OpenAPI
  artifacts are drift-gated, release archives reject personal bridge files, and
  the browser extension is aligned to version 9.1.0 and port 4825.
- The misleading client-only global egress toggle was removed. External actions
  continue to use their real feature-specific consent/configuration paths.
- MCP/plugin dispatch no longer bypasses local-file approval, and document RAG,
  answer traces, garden fallback, and realtime unscoped events fail closed at
  authenticated workspace boundaries.

## v9.0.0 — Code Review Closure & Runtime Cleanup (2026-07-08)

9.0.0 packages the July 8 code-review follow-up work and the remaining cleanup
risk reduction. The release keeps 8.9.0's scoped memory and ToolRegistry
hardening, then fixes functional reliability issues, consolidates duplicated
runtime/setup/frontend helpers, makes runtime audit append paths scale better,
and decomposes the main chat router epilogues so future chat behavior changes
have a smaller blast radius.

### Added
- Added regression coverage for no-model file generation, chat intent routing,
  permission-token cleanup, setup detection helpers, runtime audit JSONL appends,
  and shared chat fast-path epilogues.
- Added `latticeai.core.io_utils`, `latticeai.services.setup_detection`, and
  `lattice_brain.utils` as shared homes for duplicated JSON, timestamp, hash,
  and setup-probe helpers.

### Changed
- Runtime audit events now append to JSONL while preserving legacy JSON audit
  reads, avoiding full-file rewrites on every append.
- The legacy `server_app` runtime namespace now exports from an explicit
  allowlist instead of exposing every non-underscore local from app assembly.
- Chat fast paths now share history, notification, no-model, single-answer, and
  agent-payload epilogues instead of duplicating them in the main `/chat`
  handler.
- Setup wizard and zero-config setup share Windows GPU parsing, CUDA detection,
  WSL detection, and tool detection helpers.
- Static CSS and React SPA token ownership are documented as separate token
  sources with different consumption formats.
- README, release docs, readiness gates, package metadata, Tauri metadata, and
  VS Code extension metadata are synchronized to 9.0.0.

### Fixed
- File-generation requests now fail cleanly when no model is loaded instead of
  creating empty files and reporting success.
- Streaming chat/document generation now preserves terminal SSE events and
  history/trace persistence on mid-stream failures.
- Agent run executor exceptions now persist `failed` run status instead of
  leaving runs permanently `running`.
- Brain delegation now treats failed HTTP responses as failed UI activity.
- Local permission approval cleanup no longer corrupts the active token lookup
  when expired approvals are removed.
- Chat network-status and current-URL intent detection no longer overmatches
  generic IP/address questions.
- Telegram bot server URL configuration now honors environment overrides and
  avoids replaying hashed session keys as bearer cookies.
- Brain UI version copy, local embedding dimensions, and LATTICE_TZ-aware audit
  timestamps are aligned with the current runtime configuration.

Expected artifacts (exact 9.0.0 names only):
- `dist/ltcai-9.0.0-py3-none-any.whl`
- `dist/ltcai-9.0.0.tar.gz`
- `dist/ltcai-9.0.0.vsix`
- `ltcai-9.0.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_9.0.0_aarch64.dmg`
