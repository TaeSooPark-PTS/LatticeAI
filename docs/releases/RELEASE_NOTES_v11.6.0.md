# Lattice AI v11.6.0 — One Door (2026-08-15)

> **Status: historical** — point-in-time release note.

문 하나만 남았습니다. 제품의 모든 경로는 이제 Rust `lattice-host`가
직접 응답하고, Python 패키지는 더 이상 웹 애플리케이션이 아니라
**AI 워커**(순수 연산)입니다. 설계:
[docs/v11.6.0_ONE_DOOR_PLAN.md](../v11.6.0_ONE_DOOR_PLAN.md)

## 한 줄 요약

| | |
|---|---|
| 제품 표면 | **네이티브 420 오퍼레이션 / 41 라우트 패밀리** (원래 경로 그대로) |
| Python 워커 | **28 라우트** — LLM·스트림, embed, extract, parse, render×4, ASR, multimodal-describe, models/engines, sysinfo, health |
| 크레이트 | **9개** (`lattice-{core,auth,agent,chat,ingest,jobs,platform,retrieval,host}`) |
| 쓰기 | **전부 Rust** — KG write 엔진이 단일 writer |
| 삭제 | Python **298 파일 / 73,617줄** |
| 남은 Python | **127 파일 / 약 20,900줄**, 문·분기 **100.00%** |

## 1. 문 하나 — 게이트웨이가 곧 제품 서버

`lattice-host`가 아홉 크레이트의 라우터를 한 프로세스에 마운트하고,
`mount_table()`이 그 합집합을 선언합니다. `(method, path)` 중복은 axum
생성자에서 나는 패닉이 아니라 **이름 붙은 단언**으로 먼저 실패합니다.

- 워커로 넘기는 것은 커밋된 allowlist에 있는 **28 라우트뿐**이고, 그
  파일(`rust/fixtures/worker_allowlist.json`)은 워커 프로필에서
  생성되어 드리프트 게이트가 지킵니다. 목록에 없으면 프록시가 아니라
  `404 {"detail":"Not Found"}` — FastAPI 자신의 본문 그대로입니다.
- 매칭 규칙은 메서드 **와** 경로 둘 다이고, `HEAD`는 `GET`을 따릅니다
  (200이냐 405냐는 워커가 정할 일이며, 이 홉이 지어내면 없는 권한을
  주장하는 것이 됩니다).
- 배선은 한 곳(`gateway/onedoor.rs`)에 모입니다 — `RuntimeConfig` 하나,
  `Store` 하나, `GraphWriter` 하나(그 `open`이 곧 스키마 부트스트랩이라
  어떤 라우트보다 먼저 실행됩니다), `AuthState` 하나, 워크스페이스
  멤버십 리졸버 하나. 스코핑은 더 이상 pass-through가 아닙니다.
- 백그라운드 잡 스케줄러가 `GraphWriter`를 받습니다. 이전에는 매 tick이
  워커에서 이미 사라진 `POST /api/index/drain`을 때리고 백오프만
  쌓았습니다 — 로그에는 보이고 제품에는 보이지 않던 고장입니다.

## 2. 모든 쓰기가 네이티브 — 그리고 바이트로 증명됩니다

지식 그래프 write 엔진(`lattice_core::graph_write`)이 ingest, curation,
provenance, taxonomy, 벡터 큐까지 통째로 옮겨왔습니다.

- **32단계 행 단위 패리티 배터리**: 실제 Python 스토어를 32단계로
  구동하며 매 단계마다 **모든 테이블을 덤프**하고, Rust 재생과
  비교합니다. 허용 오차 0.
- **스키마 대조**: `sqlite_master`의 **67개 객체 전부**(암묵 인덱스와
  FTS 그림자 테이블 포함)를 Python이 덤프한 마스터와 비교합니다.
- 그래프 테이블 **17개**의 소유자가 WORKER → RUST_PLATFORM으로
  넘어갔고, "그래프 DB의 어떤 테이블도 워커가 쓰지 않는다"가 이제
  주석이 아니라 테스트입니다.

## 3. 표면은 다시 설명한 것이 아니라 재생한 것입니다

Python 앱이 아직 그 경로들을 서빙하던 시절에 녹화한 **HTTP 골든
1,487 케이스**(커밋된 12개 픽스처 파일)를 네이티브 라우트에 재생하고,
상태줄과 본문을 대조합니다. 기존 골든 계열(retrieval 191, chunking,
agent kernel, agent loop 궤적 10종)은 **그대로**이며 여전히 초록입니다.

에이전트 루프의 변경 제안 파이프라인도 인프로세스가 되었습니다.
스테이징은 Review Center와 **같은 문서 핸들**로 들어가고
(`GovernanceState`), 승인·적용은 네이티브 `write_file`과 같은 샌드박스를
지나며, 리뷰어가 보는 unified diff는 CPython `difflib`를 그대로 이식한
`pydiff`가 만듭니다(`autojunk` 포함 — 없으면 200줄 넘는 파일에서 Python과
매번 다른 diff를 보여주게 됩니다).

## 4. 포트가 찾아낸 결함 — 고친 것

Python 오라클 자체의 버그가 골든 대조에서 드러났고, 여기서 고쳤습니다.

- **KG write 4건**: `curate`의 문서 스캔이 `kgv2_*` 뷰를 읽는데
  `nodes`는 충돌 시 `type`을 갱신하지 않고 `nodes_v2`는 갱신합니다 —
  같은 노드가 한 테이블에서는 콘텐츠이고 다른 테이블에서는 아닙니다.
  `_store_pending_promotions`와 `set_node_sensitivity`는 `sort_keys`
  없이 직렬화해 CPython 딕셔너리 순서를 그대로 저장했고, `_json`은
  기본 구분자(`", "` / `": "`)를 써서 **모든 `edge:`/`event:` id가 그
  텍스트로부터 해시**되고 있었습니다.
- **리댁션 2건**: 모듈 문서가 주장하던 것과 달리
  `Authorization: Bearer <token>`은 리댁션되지 **않았고**(콜론 뒤 값이
  `Bearer` 6자라 8자 패턴에 못 미침), 텔레그램 토큰 정규식의 뒤쪽
  lookahead는 실제로 아무것도 거절하지 않고 있었습니다.
- **변경 제안 종류 화이트리스트**: `"reorganization"`과 비교하고 있었으나
  실제 상수는 `"folder_reorganization"`입니다. 즉 **모든 폴더 재구성
  제안은 승인될 수 없었습니다.** 어떤 픽스처도 그 종류를 다루지 않아
  아무도 잡지 못했습니다. 이제 이식되었고, `moves` 리포트가 핸들러가
  이미 읽고 있던 자리에 도착합니다.
- **직렬화 순서 함정**: `lattice-retrieval`이 `serde_json/preserve_order`를
  켜고 cargo가 빌드 전체에 피처를 통합하기 때문에, **제품이 포함된 모든
  빌드에서** `serde_json::Map`이 삽입 순서로 순회합니다. `sort_keys=True`를
  자처하던 덤프 헬퍼가 정렬되지 않은 `metadata_json`을 쓰고 있었고 —
  읽으면 같지만 **해시는 다릅니다**. 두 곳 모두 명시적으로 정렬합니다.

## 5. 정직한 고지 — 그대로 가져온 것, 없어진 것, 남은 구멍

### 5.1 제거된 표면

- **Telegram 브리지 — 제거.** 워커가 된 플랫폼 코드와 함께 삭제되었습니다
  (`latticeai.integrations.telegram_bot`은 이제 임포트 불가 목록에 있고,
  lifespan의 브리지 기동과 CLI의 텔레그램 알림도 함께 빠졌습니다).
  브리지할 제품 서버가 그 프로세스에 더 이상 없다는 것이 이유이며,
  이번 릴리스에서 독립적으로 판단된 기능 결정이 아닙니다.
- **SSO OIDC 로그인/콜백 플로우 — 제거.** 워커는 `/auth/*`를 하나도
  마운트하지 않고, `authlib`·`cryptography` 의존성이 그와 함께
  빠졌습니다. **설정 표면(구성 항목)은 남아 있고, 패스워드 로그인은
  네이티브로 동작합니다.** 이것도 워커 경계의 결과입니다.

### 5.2 알면서 그대로 이식한 오라클 버그

바꾸면 릴리스 중간에 표면이 달라지므로, **Python이 하던 그대로** 옮기고
여기에 적습니다.

- `/api/command/search`의 knowledge 그룹은 **항상 비어 있습니다**
  (검색 결과를 `results` 키로 읽는데 생산 측은 `matches`로 씁니다).
- 리뷰 스누즈에 offset-aware 시각이 들어오면 **500**입니다.
- 이미 거절된 제안을 다시 거절하면 **500**입니다.

### 5.3 남아 있는 구멍

- **업로드 추출 보강은 UTF-8 텍스트 전용입니다.** `/upload/document`가
  추출 시임을 호출하는 것은 본문이 UTF-8로 디코드될 때뿐이며, 그 외
  (바이너리·빈 본문)에는 시임이 없습니다. 이 경우에도 Document 노드는
  기록되고, **Concept 서브그래프가 조용히 틀린 것이 아니라 눈에 보이게
  없습니다.**
- **공급 벡터는 1차 노드만 덮습니다.** ingest 요청의 `embedding`은
  content/document/message **주 노드**에만 적용됩니다. 청크는
  `ChunkPiece.embedding`이 따로 문입니다. 그 밖의 노드는 기본 해시
  임베더 경로를 그대로 씁니다(그래서 패리티 골든이 바이트 동일하게
  유지됩니다).
- **모델이 로드된 상태의 chat 스트리밍은 픽스처가 아니라 FakeWorker +
  라이브 스모크로 증명되었습니다.** 녹화 시점의 앱에는 로드된 모델이
  없었기 때문입니다. 테스트는 `/worker/llm/stream`이 1회,
  `/worker/embed`가 1회 이상, 은퇴한 record-turn 경로가 **0회**임을
  단언합니다.
- **`POST /worker/render/pdf`가 기본 설치에서 동작합니다** — `reportlab`이
  11.6.0부터 필수 의존성입니다. 이전에는 선언되지 않은 의존성을 지연
  임포트하다 **500**을 냈습니다. (`ltcai[pdf]` extra는 기존 설치 안내와의
  호환을 위해 빈 별칭으로 남습니다.)
- **pyautogui 포인터 도구는 워커에서 실행됩니다** — 결함이 아니라
  능력의 위치입니다. 여섯 개 포인터 도구(`computer_click/type/key/
  scroll/move/drag`)는 네이티브 도구 집합에서 제외되고 `POST /agent/tool`로
  내려갑니다. 워커 venv에 `pyautogui`를 직접 설치한 사용자는 계속
  동작하며, 설치하지 않았다면 이전과 똑같이 "사용할 수 없습니다"를
  받습니다.
- **네이티브 도구에 대해서는 아직 사용자 훅이 발화하지 않습니다.**
  훅 싱크가 배선되기 전까지의 상태이며, 이름을 붙여 결정으로 남깁니다.
- **`sanitize_write_content`는 네이티브 write 경로에 적용되지 않습니다.**
  Python에서도 루프만 이를 거쳤고 `/agent/tool`·`/tools/write_file`은
  거치지 않았습니다 — 아티팩트 파이프라인의 실제 구멍이며, 조용히
  재구현하지 않고 표시합니다.
- **`review_item_created` / `review_item_updated` 타임라인 이벤트는
  어느 리뷰 경로에서도 기록되지 않습니다.** 승인-적용 하나만 붙이면
  나머지 열 개 라우트와 불일치가 되므로 통째로 후속 과제입니다.
- **`workspace_os.json`에 두 writer**(`review_queue::GovernanceState`,
  `workspace::store::WorkspaceStore`)가 남아 있습니다 — 이번 릴리스가
  만든 것이 아니고, 같은 종류의 위험으로 기록해 둡니다.
- **아직 워커 원점으로 나가는 seam 호출 3건**이 있습니다(그래서 W3b가
  워커에서 지운 경로에 대해 404를 받습니다): `/clear` 명령의 그래프 감사
  이벤트 유실(정리 자체는 성공), 정원 노트가 vault에는 남고 Brain에는
  안 들어감, **브라우저 탭 캡처는 실패**합니다. 통합 테스트가 이
  누수 집합이 **정확히 이 셋**임을 단언하므로, 새 누수도 실패이고
  고치면 목록도 함께 줄어야 합니다.

## 6. 게이트

| 게이트 | 결과 |
|---|---|
| `pytest tests/ --cov` | **1,237 통과 · 13 스킵 · 문·분기 100.00%** (8,075문 / 2,158분기, `fail_under=100`, 새 pragma 0) |
| `ruff check .` / `mypy` | clean / **Success, 127 source files** |
| `compose_openapi.py` | **421 paths / 463 operations**, 바이트 동일 |
| `check_openapi_drift.mjs` | synchronized (`info.version` 11.6.0) |
| `check_server_i18n.mjs` | ok |
| 워커 allowlist 드리프트 | 픽스처 **28 라우트**, 워커가 실제로 서빙하는 집합과 일치 |
| Rust | `cargo fmt --all --check` clean · `clippy -D warnings` clean · `cargo test --workspace` **1,627 통과 · 0 실패** |
| 마운트 표 | `mounted_route_count()` **420** / `mount_table().len()` **41** (측정값) |

## 7. 산출물

- `dist/ltcai-11.6.0-py3-none-any.whl`
- `dist/ltcai-11.6.0.tar.gz`
- `ltcai-11.6.0.tgz`
- `dist/ltcai-11.6.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_11.6.0_aarch64.dmg`

와일드카드 업로드는 사용하지 않습니다.

## 8. 지원 정책 변경

공개 릴리스 히스토리와 보안 지원의 하한이 **11.0.0**으로 올라갔습니다.
11.6.0은 제품 서버를 다른 언어로 다시 만들었기 때문에, 10.x·9.x 설치본에
대한 수정은 이 프로젝트가 지킬 수 없는 약속이 됩니다. 이전 릴리스 노트는
`RELEASE_NOTES_v*.md`로 트리에 그대로 남습니다.
