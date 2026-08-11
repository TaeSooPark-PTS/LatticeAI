# Lattice AI v11.5.0 — Rust Complete (2026-08-11)

v11.4.0 Rust Foundation 로드맵의 **Phase 2 · 3 · 4를 한 릴리스로
완결**합니다. 원칙은 그대로입니다 — 작동하고 증명된 조각만 출하하고,
Python 서버는 목표 아키텍처의 **AI Worker**로 수렴하되 제거되지
않습니다. 설계·상세:
[docs/v11.5.0_RUST_COMPLETE_PLAN.md](docs/v11.5.0_RUST_COMPLETE_PLAN.md)

## Phase 2 — Front Door + 네이티브 읽기/검색/히스토리 + Ingestion

- **데스크톱 front-door 기본화**: Tauri가 수퍼바이저+게이트웨이
  토폴로지로 뜨고 웹뷰는 게이트웨이 origin으로 항해합니다. 차단기였던
  포트 결박 CSRF는 워커 env 주입
  (`LATTICEAI_CSRF_TRUSTED_ORIGINS`)으로 해소 — **실제 Python 워커
  상대 라이브 증명**(신뢰 origin 200, 타 포트/외부 origin 403).
  안전 밸브 3종(`LATTICEAI_DESKTOP_DIRECT=1`·BACKEND_ORIGIN·NO_BACKEND)
  유지. SSE 프록시는 `X-Accel-Buffering: no` 보강.
- **lattice-retrieval 확장**: 3채널 서비스 hybrid, graph_search,
  relationship_search, traverse(라운드 종속 BFS까지 재현), 히스토리
  전 읽기(스코프 SQL·그룹핑·"마지막 30그룹" 검색 그대로), Context
  Assembler 포트 — **신규 패리티 116 + 기존 75 = 191/191 완전 일치
  (엡실론 0)**, Python 계약 199 테스트가 양방향 잠금. `graph_context`
  중복·`int(depth or 1)` falsy 같은 Python 특유 동작도 재현·주석화.
- **lattice-ingest**: typed chunking 1:1 포트(4전략 · char 슬라이싱 ·
  PDF 페이지 산술 · 해시/청크 id 규약) — 골든 42케이스/332청크 완전
  일치 + **뮤테이션 테스트 26/26 적중**, mtime 폴링 워처(폴더 인제스트
  필터 체인 동일) 19테스트, 쓰기는 워커 인제스트 API 위임(단일 작성자).

## Phase 3 — Jobs · Scheduler · Context Builder

- **문서화된 갭을 닫음**: "배경 임베드 큐를 아무것도 몰지 않는다" →
  신규 additive 엔드포인트 `POST /api/index/drain`(문·분기 100%) +
  `GET /api/index/queue`, Rust `lattice-jobs` 스케줄러가 60초마다
  드레인(`LATTICEAI_JOBS_INTERVAL`, 5xx 백오프 60→600s), `/host/jobs`
  상태·수동 틱, 옵트인 잡 재개(`LATTICEAI_JOBS_AUTORESUME=1`).
  데스크톱 게이트웨이 토폴로지에서도 동작(`LATTICEAI_JOBS=0` 오프).
- Context Builder(채팅 어셈블러)는 네이티브 시임 위에서 골든 13케이스
  완전 일치(`/rust/context/assemble`).

## Phase 4 — Agent Runtime 안전 커널 (Tools · Sandbox · Permission)

- **권한 커널 결정표 패리티**: normalize_mode(별칭 전체)·
  effective_auto_approve·서킷 브레이커·should_stage_proposal·
  plan_requires_approval·block_reason_for_tool·classify_tool_call
  (인벤토리 26종) — **모드 판정 2,358건 + 명령 검증 59건 + shlex
  35건 완전 일치**, 실제 정책표(tool_registry 47종)를 데이터로 미러링.
- **명령 샌드박스 네이티브 실행**: 검증 통과한 읽기 전용 명령만
  (pwd/ls/find/cat/head/tail/wc/rg) 치환 env(HOME=AGENT_ROOT·고정
  PATH)·30s 타임아웃·12,000자 캡으로 직접 실행. 심링크 탈출 실제
  시도·거부, 타임아웃 실측. 변이는 절대 실행하지 않습니다.
- 게이트웨이 `/rust/agent/{preflight,exec,contract}`. **명시적 잔여**
  (계획 §4c): 에이전트 루프 오케스트레이션 자체의 이식은 결정 커널이
  잠긴 지금 후속의 기계적 작업으로 남습니다 — 이유는 계획 문서에.

## 게이트웨이 최종 맵

`/host/*`(상태·잡) · `/rust/search/{hybrid,keyword,vector,service-hybrid}`
· `/rust/graph/{search,relationships,traverse}` · `/rust/history*` ·
`/rust/context/assemble` · `/rust/ingest/{plan,chunk}` ·
`/rust/agent/{preflight,exec,contract}` · 나머지 전부 스트리밍 리버스
프록시. `/host`·`/rust` 네임스페이스는 절대 프록시로 새지 않습니다.

## 검증

- Python **6,861 테스트 · 문·분기 100.00%** · 프론트 **1,761 · 4지표
  100%** · **Rust 워크스페이스 534 테스트**(+ src-tauri 23).
- 패리티 골든 4계열 전부 양방향: 검색·히스토리·컨텍스트 191, 청킹
  332청크, 권한·명령 2,452판정, 임베딩 bit-for-bit — 음성 대조까지.
- fmt/clippy `-D warnings`/mypy/ruff 클린, fresh-resolve 3.11 재검증.

## 정직한 경계

Python 워커가 계속 소유: 문서 파서 매트릭스(pdf/docx/xlsx/pptx),
임베딩 생산, LLM 추론, 변이 도구 실행, 그래프 쓰기(단일 작성자).
에이전트 루프 오케스트레이션 이식은 §4c 사유로 다음 릴리스.

## 산출물

- `dist/ltcai-11.5.0-py3-none-any.whl`
- `dist/ltcai-11.5.0.tar.gz`
- `ltcai-11.5.0.tgz`
- `dist/ltcai-11.5.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_11.5.0_aarch64.dmg`

와일드카드 업로드는 사용하지 않습니다.
