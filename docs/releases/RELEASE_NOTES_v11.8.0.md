# Lattice AI v11.8.0 — Travel Light (2026-08-16)

> **Status: historical** — point-in-time release note.

11.7.0은 백로그를 비웠습니다. 11.8.0은 그 다음에 남은 것 — 아무도 부르지
않는 라우트, 아무도 읽지 않는 게이트, 같은 것을 두 번 증명하는 골든,
전부를 덮어 놓은 lint 억제 헤더 — 을 덜어냈습니다. 문은 그대로입니다.
`lattice-host`가 **420 오퍼레이션 / 41 패밀리**를 서빙하고, Python은 AI
워커이며, 바뀐 것은 그 워커가 **28 라우트가 아니라 19 라우트**라는 점입니다.
지운 자리에는 매번 대체 증명을 남겼습니다.

## 한 줄 요약

| | |
|---|---|
| 테마 | **Travel Light** — 부르는 사람 없는 표면·중복 증명·억제 헤더를 덜어낸 릴리스 |
| 워커 표면 | 28 → **19 라우트**. 호출자 0인 아홉 개를 end-to-end로 삭제 |
| Rust | 약 191개 파일의 blanket `#![allow]` 제거, 진단 **약 650건**을 원인에서 수정 |
| 테스트 바이너리 | 통합 테스트 파일 98 → **56** (lattice-platform 43 → 11) |
| 골든 | agent 판정 그리드 702×2 삭제 + 702 → **171 대표행**, 대신 이름 붙은 단위 테스트 |
| CI | lint 13 → **10 게이트**, `agent-smoke.yml` 삭제, 커버리지 게이트는 **line 90** |
| 실제 버그 | `SessionStore`가 워커 기동 이후의 로그인을 못 보던 것 |
| UI | Brain Chat Home 전면 재설계 — 자라는 Brain, 히어로 컴포저 |
| 순 diff | 424 파일 · **+4,604 / −22,165** |

## 1. 게이트 다이어트 — 남긴 게이트는 전부 하중을 받습니다

게이트가 많다는 것과 안전하다는 것은 다른 말입니다. 두 번 도는 스텝,
실패해도 통과하는 잡, PR마다 15분씩 쓰면서 아무 것도 막지 못하는 워크플로를
정리했습니다.

- **`agent-smoke.yml` 삭제.** 호스티드 러너에는 MLX 모델이 없습니다. 이
  워크플로는 모델 부재를 이유로 fail-open하고, 그 결과를 다시 fail-open으로
  보고했습니다 — **이중 fail-open**은 초록불이 아니라 신호등이 꺼진
  것입니다. 되살리려면 실모델 러너가 필요하고, 그것은 결정이지 누락이
  아닙니다.
- **`ci.yml` 4레그 매트릭스는 유지, 중복 스텝은 제거.** OpenAPI 생성 ·
  product-readiness · 확장 테스트는 **3.11 + ubuntu 레그에서만** 돕니다.
  커버리지 레그는 `pytest tests/`를 두 번 돌리지 않습니다(같은 스위트를
  `--cov` 유무로 두 번 실행하던 것).
- **`release.yml`은 릴리스만.** 태그 push에서 CI의 lint/테스트를 다시 돌던
  스텝을 걷어냈습니다. 태그가 가리키는 커밋은 이미 CI를 통과한 커밋입니다.
- **트리거를 실제 필요에 맞춤**: `dependency-audit`은 cron 전용,
  `visual.yml`은 push + nightly(PR에서는 안 돎), `e2e-sidecar`는 nightly
  전용. 모든 워크플로에 `concurrency` 그룹과 `timeout-minutes`를 붙여
  매달린 잡이 러너를 붙잡지 못하게 했습니다.
- **로컬 lint 체인 13 → 10.** 사라진 셋은 스크립트 자체가 없어졌거나
  (`check_legacy_debt.mjs`), CI가 직접 부르거나, 다른 게이트가 이미 같은
  것을 보고 있던 것들입니다. 순서와 나머지 열 개는 그대로입니다.
- **커버리지 게이트는 `fail_under = 90`(라인).** 분기 게이트는
  내렸습니다. 측정값은 이번 릴리스에서도 여전히 100%지만, **강제되는
  바닥은 90**입니다 — 아래 §정직한 고지에 그대로 적었습니다.

## 2. Rust — 억제 헤더를 걷고 진단을 원인에서 고쳤습니다

`lattice-{platform,retrieval,ingest,jobs}` 네 크레이트의 맨 위에는 약
**191개 파일**에 걸쳐 blanket `#![allow(...)]` 헤더가 붙어 있었습니다.
헤더가 있는 동안 clippy는 그 파일에 대해 아무 말도 하지 않았고, 그래서
아무도 그 파일이 무슨 말을 듣고 있었는지 몰랐습니다.

- 헤더를 전부 제거하고, 드러난 **약 650건**의 clippy/rustc 진단을 **원인에서**
  고쳤습니다. 워크스페이스 수준 허용은 **0건 추가**했습니다.
- 남긴 억제는 **8개**뿐이고 전부 지역적입니다 —
  `#[allow(clippy::too_many_arguments)]`에 각각 이유가 붙어 있습니다.
  인자 수를 줄이려면 그 함수가 조립하는 의존성 묶음을 새 타입으로 만들어야
  하고, 그것은 이 릴리스의 범위가 아니라는 판단입니다.
- `cargo clippy --workspace --all-targets -- -D warnings`가 깨끗합니다.
- **죽은 코드 삭제**: `workspace_scope` 모듈, 쓰이지 않던
  `WORKSPACE_OS_VERSION` 상수, 그 밖에 호출자 0인 항목 16개.
- **`ROLE_CAPABILITIES` 단일 출처화.** 같은 표가 `lattice-auth`와
  `lattice-agent`에 각각 있었습니다. 두 사본이 갈라지면 권한 표가
  프로세스 안에서 자기 자신과 다른 답을 냅니다. `lattice-auth`가 소유하고
  `lattice-agent`가 의존합니다.
- **`PhaseBudgets`에 상한을 세웠습니다.** 이제 `MIN 128 / MAX 8192`
  토큰입니다. 하한만 있고 상한이 없으면, 설정 실수 한 번이 한 턴의
  컨텍스트를 모델 창 밖으로 밀어냅니다.

### 테스트 바이너리 98 → 56

크레이트마다 `tests/*.rs` 파일 하나가 곧 링크되는 바이너리 하나입니다.
`lattice-platform`은 그런 파일이 43개였고, 43번의 링크가 CI 시간의 대부분을
차지했습니다. 주제별로 합쳐 **11개**로 줄였습니다(전체 98 → 56). 테스트
**함수는 하나도 지우지 않았습니다** — 옮겼을 뿐이고, 총 개수는 아래
§게이트에 있습니다.

## 3. 골든 축소 — 702행 그리드를 이름 붙은 테스트로

`rust/fixtures/agent/golden/`에는 판정 그리드가 있었습니다.
`decisions__trusted`와 `decisions__bypass`는 각각 **702행**이었고, 세
파일은 같은 등가류를 서로 다른 모드로 반복하고 있었습니다. 702행이 붉게
변하면 사람이 읽는 것은 첫 줄 하나입니다.

- `decisions__trusted` · `decisions__bypass` 그리드를 **삭제**하고, 모든
  판정 클래스를 덮는 **이름 붙은 단위 테스트**로 대체했습니다. 실패
  메시지가 "702행 중 1행 불일치"가 아니라 어떤 규칙이 깨졌는지를 말합니다.
- `decisions__strict`와 `calls`는 **702 → 171 대표행**으로 줄였습니다 —
  등가류마다 한 행. 어떤 등가류도 행 없이 남지 않았습니다.
- **드리프트 가드**를 붙였습니다. 커널이 새 판정 클래스를 만들면 대표행
  세트가 그것을 덮지 않는다는 사실이 실패로 나옵니다. 축소가 조용한 커버리지
  손실이 되지 않게 하는 장치입니다.
- **모든 픽스처 계열이 `FROZEN.md`를 갖습니다** — 새 `chunking/FROZEN.md`
  포함. 재생성할 수 없는 골든은 그 사실을 파일 옆에 적어 두는 것이
  규칙입니다.
- retrieval · graph_write · agent_loop · http 골든은 **손대지 않았습니다.**

## 4. 중복 검증과 죽은 코드 — 파이썬 쪽

같은 것을 두 곳에서 증명하면, 둘이 갈라지는 날 어느 쪽이 맞는지 아무도
모릅니다. 이번에는 갈라진 쪽을 지우고 권위를 하나로 남겼습니다.

- **`latticeai/core/agent_permission.py` 삭제** — 권한 판정은 커널의 것이고,
  이 모듈은 그 판정을 다시 흉내내고 있었습니다.
- **죽은 보안 헬퍼 삭제**: `hash_password` / `verify_password`(비밀번호
  로그인은 v11.6.0부터 `lattice-auth`의 것), `check_ip_rate_limit`,
  `configure_trusted_proxies`, `client_ip`, `bytes_match_extension`. 워커에는
  이들을 부르는 문이 남아 있지 않습니다.
- **`_kg_common/text.py`의 죽은 청커 삭제** (+ 호출자 0인 함수 9개). 청킹은
  `lattice-ingest`의 것이고, 게이트는
  `rust/lattice-ingest/tests/chunking_parity.rs`입니다.
- **렌더 시임의 `_safe_filename` 이중 살균 제거.** 파일명은 이미 한 번
  정규화된 뒤 이 경로에 도착합니다. 두 번째 통과는 안전을 더하지 않고
  "어느 쪽이 진짜 규칙인가"만 늘렸습니다.
- **삭제한 스크립트**: `scripts/generate_agent_parity_fixtures.py`,
  `scripts/generate_chunking_parity_fixtures.py`(픽스처가 얼었으므로 생성기는
  더 이상 재생성기가 아닙니다), `scripts/brain_quality_eval.py`,
  `scripts/agent_eval.py`, `scripts/check_python.py`,
  `scripts/bench_agent_smoke.py`, `scripts/check_legacy_debt.mjs`.
- **`check_legacy_debt.mjs`는 드리프트한 거울이었습니다.** 같은 규칙을
  파이썬 테스트와 mjs 스크립트가 각각 구현하고 있었고, 둘의 판정이 이미
  달랐습니다. **파이썬 테스트가 권위**이고, mjs 쪽을 지웠습니다.
- **`product_readiness` 증거를 Rust 픽스처로 재지정.** 몇 항목이 이제는
  존재하지 않는 파이썬 파일을 증거로 가리키고 있었습니다. 증거를 실제
  소유자(`rust/fixtures/...`)로 옮겼고 판정은 그대로 **COMPLETE 10/10**입니다.

## 5. 워커 표면 28 → 19

allowlist는 "이 경로는 프록시된다"는 약속입니다. 그 약속을 지키는 코드가
아무 데서도 불리지 않으면, 그것은 표면이 아니라 유지비입니다. 트리 전체에서
호출자를 찾지 못한 **아홉 개**를 라우트·구현·픽스처·게이트웨이까지
end-to-end로 지웠습니다.

| 삭제된 라우트 | 왜 |
|---|---|
| `GET /api/embeddings/providers` | 카탈로그를 읽는 화면이 없음 |
| `POST /tools/read_document` | 문서 파싱은 `POST /worker/parse`의 것 |
| `GET /tools/pdf_pages` | 같은 문 |
| `POST /worker/multimodal/describe` | 만들어지지 않은 네이티브 이미지 ingest용 시임 |
| `GET /api/ingestion/multimodal` | 능력 프로브 — 호출자 0 |
| `POST /models/switch/{model_id}` | 로드/언로드로 충분 |
| `DELETE /models/unload-all` | 호출자 0 |
| `POST /engines/pull-model` | 준비 경로는 `prepare-model` |
| `GET /api/capture/voice/status` | 상태 프로브 — 어떤 표면도 안 읽음 |

- 파일로는 `latticeai/api/{tools,local_files,voice_capture}.py`와
  `lattice_brain/ingestion/pipeline.py`가 사라졌습니다.
- **`pypdfium2` 의존성이 빠졌습니다** — 그것을 쓰던 유일한 문이
  `/tools/pdf_pages`였습니다.
- `rust/fixtures/worker_allowlist.json`은 **28 → 19**. Rust 쪽 KEEP 표와
  게이트웨이 allowlist를 함께 고쳤고, **"이 아홉 개는 이제 전달되지
  않는다"는 네거티브 테스트**를 새로 붙였습니다. 삭제를 증명 없이 두면,
  다음 사람은 그것이 실수인지 결정인지 알 수 없습니다.

## 6. 자라는 Brain — 홈 화면 재설계

Brain Chat Home을 **세 번의 패스**로 다시 그렸습니다. 문제는 기능이 아니라
무게 배분이었습니다 — 정작 사용자가 할 일(묻기, 자료 넣기)이 작은 칸이었고,
Brain 그림은 장식만 한 60px 배지였습니다.

- **컴포저가 히어로입니다.** 대화 입력이 화면의 주인공이고, 스타터 pill이
  그 아래에 붙습니다.
- **Brain이 자랍니다.** LivingBrain이 3배 커졌습니다 — 1440 폭에서 60px →
  **179px**. 기억이 쌓이면 금빛·옥빛 **성장 링이 켜켜이 쌓이고**,
  준비도 상태에 묶인 **'기억이 자라고 있어요'** 캡션이 함께 뜹니다. 캡션은
  장식이 아니라 상태의 문장형입니다 — 준비도가 말하지 않는 것을 캡션이
  말하지 않습니다.
- **풀캔버스 그리드**: 왼쪽 Brain / 가운데 컴포저 + 스타터 / 오른쪽 제안
  패널, 그리고 바닥에 연속성 바 — **지난 대화 · 현황 · 기억 지도 · 기능**.
  이전에는 이 넷이 스크롤 아래 카드였습니다.
- **팔레트를 유기체 쪽으로 통일**: 잉크 + 옥빛 + 금빛 토큰. 11.7.0의
  elevation 언어를 벗어나지 않습니다.
- **`prefers-reduced-motion` 폴백**을 성장 애니메이션에 붙였습니다. 링은
  움직임 없이도 같은 정보를 전달합니다.
- **죽은 컴포넌트 삭제**: `FeedbackState.tsx`, `DepthEmergence.tsx`.

## 7. 실제 버그 하나 — `SessionStore`가 워커 기동 후의 로그인을 못 봤습니다

v11.6.0부터 `sessions.json`의 **writer는 `lattice-auth`**이고 워커는 읽기만
합니다. 그런데 워커는 그 파일을 `__init__`에서 **한 번만** 읽었습니다.
결과적으로 **워커가 뜬 뒤에 일어난 로그인은 워커에게 존재하지 않았습니다** —
`trusted_local_owner` 아래에서는 익명 소유자 경로가 먼저 답해 조용히
넘어갔고, `LATTICEAI_REQUIRE_AUTH=true`에서는 파일 안에 멀쩡히 있는 토큰에
대해 **401**이 나갔습니다.

이제 조회가 미스하면 포기하기 전에 파일을 다시 읽습니다. 그리고 그것이
토큰 추측 버스트를 디스크 읽기 버스트로 만들지 않도록 두 가지 가드를
같은 락 아래에서 겁니다 — 파일의 `mtime_ns`/`size`가 지난 로드 이후
그대로면 재읽기를 **건너뛰고**, 실제로 변하는 파일에 대해서는
`SESSION_RELOAD_MIN_INTERVAL`(1초)당 한 번의 파싱으로 **throttle**합니다.
동시 미스는 하나의 읽기로 합쳐집니다. 회귀 테스트 **9개**를 붙였습니다.

## 8. 정직한 고지 — 이번에 닫지 않은 것

- **커버리지 강제 바닥이 100 → 90(라인)으로 내려갔고, 분기 게이트는
  없어졌습니다.** 이번 릴리스의 실측은 여전히 100%입니다. 그러나 게이트가
  지키는 것은 실측이 아니라 바닥이고, 바닥은 90입니다. 다음 커밋이 92%로
  들어와도 CI는 통과합니다. 이것은 트레이드오프이지 개선이 아닙니다.
- **`lattice_brain`의 멀티모달 이미지/비디오 절반에는 이제 HTTP 문이
  없습니다.** `POST /worker/multimodal/describe`가 유일한 문이었고, 그것이
  감싸던 네이티브 이미지 ingest는 만들어진 적이 없습니다. 관찰 함수들은
  Brain Core에 그대로 남아 단위 테스트를 받고 있으며, 모듈 헤더에 그 사실이
  적혀 있습니다. 필요한 날 시임은 몇 줄입니다.
- **DMG는 ad-hoc 서명(=미서명)입니다.** 이전 릴리스와 같습니다. 첫 실행에서
  Gatekeeper 우회 절차가 필요합니다.
- **삭제된 라우트의 메시지 카탈로그 키가 얼어붙은 패리티 픽스처에 남아
  있습니다.** 의도한 것입니다 — 픽스처는 **녹화 당시의 표면**을 고정하는
  기록이고, 지금 트리에 맞춰 다시 쓰면 그것이 증명하던 것을 잃습니다.
- **`tests/visual/mock_server`가 고아 mock 라우트 하나를 아직 서빙합니다.**
  현재 증거(스크린샷)가 해시로 그 서버에 묶여 있어서, 지금 지우면 증거
  결속이 깨집니다. 다음 캡처 사이클에서 함께 정리합니다.
- 11.7.0이 열어 둔 항목들은 그대로 열려 있습니다 — Self-Model `open_keys`는
  `pending`만, 추출 `refiner` 없음, `delete_node`는 `PART_OF`를 남김, owner가
  없는 프로세스에서 리뷰 이벤트는 침묵, `POST /knowledge-graph/ingest`는
  텍스트 전용, 리뷰 변이는 스토어 사이클 2회.
- Telegram 브리지와 SSO OIDC 로그인/콜백은 11.6.0에서 제거된 채로 남습니다.
  여섯 pyautogui 포인터 도구는 여전히 워커에서 실행되고, 재고 설치에서는
  "unavailable"입니다.

## 9. 게이트

- pytest 단위 **1,153 통과**. 실측 커버리지 100%, 강제 바닥은 라인 90.
- vitest **1,761 테스트 / 100 파일**, 문·분기·함수·라인 100%.
- cargo **1,733 테스트 / 75 바이너리**. 그중 통합 테스트 파일은 `rust/*/tests/*.rs`
  **56개**(이전 98개)이고, 나머지는 크레이트별 lib 유닛 테스트 타깃입니다.
- `cargo clippy --workspace --all-targets -- -D warnings` 깨끗,
  `cargo fmt --check` 통과.
- 워커 allowlist **19**, 네거티브 테스트로 삭제된 아홉 경로가 전달되지
  않음을 고정.
- product-readiness **COMPLETE 10/10**(증거는 Rust 픽스처).
- 순 diff: 424 파일 · **+4,604 / −22,165**.

## 10. 산출물

- `dist/ltcai-11.8.0-py3-none-any.whl`
- `dist/ltcai-11.8.0.tar.gz`
- `ltcai-11.8.0.tgz`
- `dist/ltcai-11.8.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_11.8.0_aarch64.dmg`

와일드카드 업로드는 사용하지 않습니다.

## 11. 지원 정책

공개 히스토리와 보안 지원의 하한은 **11.0.0**입니다. 11.8.0은 11.7.0과 같은
프로그램 — Rust 제품 서버 + Python AI 워커 — 위에 쌓입니다. 10.x·9.x는 다른
프로그램이며 `SECURITY.md`는 11.x만 지원합니다. 이전 노트는
`RELEASE_NOTES_v*.md`로 트리에 남습니다.
