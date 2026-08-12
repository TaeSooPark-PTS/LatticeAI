# Lattice AI v11.5.2 — Tight Ship (2026-08-12)

정착된 11.5.1 트리를 세 갈래로 감사하고, 그 결과만 실행한 정리·정합성
릴리스입니다 — **Rust↔Python 중복 지도**(판정: 경계를 넘는 삭제는 0건.
모든 쌍둥이는 Python-direct 모드의 살아 있는 표면이거나 골든에 못박힌
사본이며, 진짜 중복은 Python 내부와 Rust 내부에 있었습니다), **아크 단위
죽은 코드·테스트 감사**, **라이브 front-door 패리티 감사**(192 엔드포인트
스윕 + 게이트웨이 대 직결 워커 대조). 설계:
[docs/v11.5.2_TIGHT_SHIP_PLAN.md](docs/v11.5.2_TIGHT_SHIP_PLAN.md)

## 삭제 — 약 1,100줄

- **이사 간 모듈 shim 6종**(`core/{graph_curator,hooks,multi_agent,
  workflow_engine}.py`, `services/{ingestion,kg_portability}.py`),
  **배선된 적 없는 멀티모달 스트리밍 시임**, **호출자 0인 심볼 약 27개**
  (자기 테스트만이 유일한 호출자 — "테스트를 입은 죽은 코드", 커버리지
  게이트에는 구조상 보이지 않음). 삭제마다 해당 단언만 골라내는 테스트
  수술을 동반해 문·분기 100%를 그대로 유지했습니다.
- 어떤 소비자도 쓰지 않던 `metadata_for` 인터페이스(벡터 인덱스 ABC
  선언 + 구현 4곳), 죽은 측정 스크립트 1종.
- **레거시 Electron 셸**: Tauri가 대체한 지 오래인데 npm tarball에는
  아직 실려 나가고 있었습니다. 릴리스 산출물·워크플로 어디에도 참조가
  없음을 확인한 뒤 제거.
- **재발 금지 가드**: `sys.modules[__name__]` shim 패턴은 `latticeai/`
  아래에 다시 나타날 수 없습니다(legacy-debt 게이트 확장).
- 커버리지 워크패키지 테스트 **183개는 전수 검증 결과 전부 하중을
  담당**합니다 — 그중 10개는 **79개 분기 아크의 유일한 소유자**입니다.
  커버리지상 중복인 기능 테스트 7개는 회귀 가치를 이유로 **의도적으로
  남겼습니다**.

## 통합 — 사본 하나로

- **임베더 쌍**: byte-identical 두 사본을 **골든에 못박힌 쪽**으로
  단일화. 실패 양식은 두 쓰기 경로 사이의 **조용한 벡터 드리프트**였습니다.
- **워크스페이스 선택자**: `chat`·`agent`·`upload`·`computer-use`·`admin`
  다섯 표면의 축자 재구현이 정본 규칙을 임포트합니다. **의도된 동작
  변경** — 사본들은 불일치 시 헤더를 조용히 우선했지만, 정본은 **403**
  으로 거절합니다.
- sha256 헬퍼·SSE 프레임 빌더(출력 byte-identical)·데이터 디렉터리
  기본값·모드 서비스 삼형제·모듈 임포트 가능성 프로브 — 각각 집이 하나가
  되었습니다.
- **Rust**: byte-identical 사본 7건 통합(통째로 복사돼 있던 `clock.rs`
  포함). `lattice-agent`의 독립 `pystr`는 의도된 경계이므로 유지.

## 현관문 — 라이브 전/후 증거와 함께

- **프록시 리다이렉트 통과**: 3xx가 `Set-Cookie`와 `Location`을 온전히
  달고 지나갑니다. 이전에는 초대 게이트가 **막다른 길**이었고, SSO
  로그인은 **조용히 인증되지 않았으며**, 딥링크 12개가 프래그먼트를
  잃었습니다. 워커 오리진을 가리키는 절대 `Location`은 게이트웨이
  오리진으로 재작성됩니다.
- **네이티브 레인 fail-closed**: `/rust/*`와 `/host/status|jobs`는 워커의
  posture를 그대로 따릅니다. 이전에는 워커가 인증을 요구하는 상태에서도
  **그래프 전체를 무인증으로 서빙**했습니다. 이제 posture가 닫힘이거나
  알 수 없으면 401.
- **프록시 신원**: `X-Forwarded-For/Proto/Host`가 홉을 건너가며, 루프백
  또는 신뢰 프록시 목록의 직접 피어에서 온 것만 존중됩니다.
  `--no-spawn` CSRF 거절과 내부 워커 포트를 적어 보내던 초대 링크·알림·
  SSO URL이 고쳐집니다.
- 수퍼바이저가 CSRF 오리진과 **나란히 CORS 오리진도 주입**합니다.
  존재하지 않는 WebSocket 엔드포인트를 가리키던 CSP의 `ws://` 항목 제거.
  게이트웨이 바인드 실패 시 Tauri 셸이 **죽은 오리진으로 이동하지
  않습니다**.

## 감사가 드러낸 기능

- `POST /api/search/graph` — 허용 목록에는 있었지만 **도달 경로가 없던**
  세 번째 채널의 라우트.
- `GET /api/ingestion/multimodal` — 문서에는 있었지만 배선되지 않았던
  상태 조회.
- **골든 신규 2계열**: 실제 `/chat`이 호출하는 `build_recent_chat_context`
  를 못박는 `recent_chat` 계열 — **실제 발산을 잡아냈습니다**: Python은
  `limit=0` 꼬리 슬라이스가 전부를 남기는데 Rust는 빈 결과를 돌려주고
  있었습니다. **Python이 기준**이고 Rust를 고쳤습니다. 여기에
  `document_targets`/`agent_profiles` 헬퍼 골든 97행. 골든 코퍼스
  **251 파일**.

## 정직한 경계

- 네이티브 레인은 **열린 posture·단일 로컬 소유자** 표면입니다(닫힘 또는
  알 수 없음 → 401).
- recent-chat 컨텍스트 시임의 소유권은 여전히 `/chat`의 임시 prepend에
  있고 어셈블러가 아닙니다 — 배선은 **마이너 릴리스 항목**입니다. 라이브
  프롬프트 형태가 바뀌는 변경을 이 릴리스에서 조용히 실어 보내지
  않습니다.
- `workspace_scope_from_request` 자체는 남은 두 호출자(knowledge_graph,
  workspace)에 대해 아직 관대합니다 — 후속 과제.
- 프록시 홉에는 **의도적으로 요청 타임아웃이 없습니다**. 일괄 타임아웃은
  장수 SSE를 죽입니다.

## 검증

Python **7,022 통과 · 11 스킵 · 문·분기 100.00%**(40,307문 · 10,970분기,
`fail_under=100`, pragma 예산은 11.5.1과 동일 — `no cover` 42 · `no branch` 2) · 프론트 **1,761 · 101 파일 ·
100%×4** · **Rust 760 테스트** · OpenAPI **421 paths** 재생성 ·
fresh-resolve 3.11 재검증.

## 산출물

- `dist/ltcai-11.5.2-py3-none-any.whl`
- `dist/ltcai-11.5.2.tar.gz`
- `ltcai-11.5.2.tgz`
- `dist/ltcai-11.5.2.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_11.5.2_aarch64.dmg`

와일드카드 업로드는 사용하지 않습니다.
