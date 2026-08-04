# [v10.8.0 - Within Reach] (2026-08-04)

이미 있었지만 손이 닿지 않던 것들 — 화면 밖으로 밀려난 버튼, 내가 고르지 않은
언어로 온 메시지, 작은 모델이 거의 다 만들었던 파일, 아무것도 안 바뀐 걸
확인하려고 전부 다시 읽던 인덱스.

See [RELEASE_NOTES_v10.8.0.md](RELEASE_NOTES_v10.8.0.md).

# [v10.7.0 - Plain Surface] (2026-08-04)

12개 화면 전면 재구성 — 대시보드 격자를 해체하고 각 화면을 사용자가 하러 온 일 중심으로 다시 배치했습니다. 기능은 하나도 제거하지 않았고, 그 사실을 경로·문구 키 대조와 픽셀 델타 게이트로 기계 검증합니다.

# [v10.6.3 - Loud Limits] (2026-08-04)

리뷰 2026-08 지적사항 대응. 검색 recall 리포트, 잡 영속화, CSRF 가드,
workspace 스코프 단일화, fail-closed 쓰기, 빌드 신선도 게이트.

See [RELEASE_NOTES_v10.6.3.md](RELEASE_NOTES_v10.6.3.md).

# [v10.6.2 - Ask First] (2026-08-03)

10.6.1은 다섯 화면을 다시 짰지만, **Brain 대화 홈은 절반만** 다시 짰습니다.
순서는 바뀌었는데 모양이 그대로였습니다 — 큰 Brain 그림과 가운데 정렬된 제목이
입력창 앞에서 화면 한가운데를 차지했고, 추천 질문 세 개는 **입력창과 그 입력창의
도구 줄 사이**에 끼어 있었습니다. 그래서 "직접 묻기"와 "고르기"가 같은 테두리를
쓰고 있었고, 입력창은 자기 도구와 상관없는 블록으로 갈라져 있었습니다.
10.6.2는 이 화면을 **두 장의 카드로 쪼갭니다.**

**스테이션 카드는 첫 동작만 담습니다.** 인사말은 세로 기둥에서 **가로 배너**가
되었습니다(그림 5.4rem → 3.2rem, 제목 위가 아니라 옆으로, 옅은 배경과 아래
실선). 그 아래가 입력창이고, 카드 바닥은 자료 추가와 혼자 해도 되는 일 한 줄
입니다. 입력창과 그 도구 사이에 이제 아무것도 없습니다. 추천 질문은 **아래의 별도
카드**(`.brain-secondary-deck`)로 내려갔습니다 — 먼저 할 일이 아니라 "생각나지
않으면 골라도 된다"는 제안이기 때문입니다. 스테이지 폭은 44rem → 50rem.

**이름은 역할이 있는 요소에 붙습니다.** 추천 질문 묶음은 `aria-label`을 평범한
`<div>`에 달고 있었습니다. 역할이 없는 요소에는 이름이 붙지 않으므로 브라우저가
그 라벨을 버렸고, 스크린리더에는 **이름 없는 덩어리**로 도착했습니다. 이제
라벨은 `<section>`에 있고(이름이 붙으면 `region`이 됩니다), 안쪽 div에는 없습니다.

**빈 상태도 설계했습니다.** 추천할 질문이 아직 없는 Brain — 첫날의 모든 Brain —
은 카드 대신 시작 질문 알약을 보여줍니다. 이 줄은 이 화면용으로 한 번도 그려진 적
이 없어서 대화 화면용 2.65rem 높이를 그대로 물려받고 있었고, 대신 서 있어야 할
카드보다 눈에 띄게 컸습니다. 이제 두 갈래가 같은 무게로 읽히고, 알약을 누르면
보내지 않고 입력창을 채웁니다.

**옮기면서 드러난 결함 셋 — 전부 우선순위 싸움이고, JSX만 봐서는 보이지 않습니다.**
`LivingBrain`은 후광을 **인라인 `box-shadow`** 로 씁니다. 인라인 스타일은 어떤
시트도 못 이기므로 `.brain-hero-organism .brain-aura { box-shadow: … }`는 처음
쓴 그대로 아무 일도 하지 않았고, 58px 그림 주위에 60px 후광이 남아 잘린 얼룩으로
찍혔습니다. 이제 흐림값이 `var(--aura-blur, 60px)`이고 배너만 14px로 줄입니다(다른
화면은 폴백으로 픽셀 그대로). 카드에 `overflow: hidden`을 주면 노트·웹 팝오버가
잘리고 카드가 스크롤 상자가 되어 포커스 때 인사말이 밀려 나갑니다 — 그래서
스테이션은 열어 두고 **배너가 자기 모서리만** `clip` 합니다. 마지막으로
`affordance.css`(가장 늦게 로드)의 감속 모드 취소 규칙이 옛 선택자를 가리키고
있어서, 정지를 요청한 사용자에게 카드가 계속 움직일 뻔했습니다.

**검증.** 단위 테스트가 세 표면(스테이션·덱·조용한 줄)이 **형제**로 이 순서인지,
스테이션 안에 추천 질문이 없는지, 덱이 이름 있는 `region`인지, 알약 갈래가
렌더되는지를 잡습니다. 브라우저 테스트 5개가 jsdom이 못 보는 것을 잡습니다:
팝오버가 잘리지 않고 스테이션을 스크롤시키지 않는지, 격자가 카드 안폭을 채우는지,
900·760·640·420px에서 칩으로 접히고도 사라지지 않는지(`responsive.css`에 이 클래스를
숨기는 규칙이 남아 있습니다), 감속 모드에서 transform과 transition이 모두 없어지고
색으로는 여전히 답하는지, 후광이 그림보다 작고 배너 안에 들어 있는지.

기능은 하나도 지우지 않았습니다. 백엔드 동작 변경 없음 —
`frontend/openapi.json`은 10.6.1 대비 `info.version`만 다릅니다.

See [RELEASE_NOTES_v10.6.2.md](RELEASE_NOTES_v10.6.2.md) and
[docs/CHANGELOG.md](docs/CHANGELOG.md).

---

# [v10.6.1 - First Things] (2026-08-03)

10.6.0은 주요 화면마다 1순위 패널 하나를 정했지만, 다섯 화면은 손대지 못한 채
남았습니다: 로그인, 추천 모델, Brain 대화 홈, 자동화 실행 목록, 리뷰 센터.
10.6.1은 그 다섯 개를 같은 규칙으로 다시 짭니다 — **여기 온 이유가 화면의 첫
번째 것이 되고, 나머지는 그 아래로 내려간다.** 로그인은 입력 폼 하나만 카드로
띄우고, 세 칸짜리 약속 바는 화면 맨 아래 얇은 띠로 내려갔으며, "비밀번호는 이
컴퓨터에 남습니다" 같은 안심 문구는 마지막 입력칸과 버튼 사이가 아니라 버튼
**뒤**로 옮겼습니다. 추천 모델 화면은 같은 모델을 CTA 버튼과 목록 첫 카드로 **두
번** 보여주고 있었습니다 — 이제 이름·이유·용량·소요 시간·버튼을 담은 히어로 카드
하나이고, 나머지 둘은 `다른 선택지` 아래 작은 카드입니다. Brain 홈은 입력창이
자기 테두리와 포커스 링을 갖고 맨 앞에 서고, 추천 질문은 툴팁에 숨어 있던 설명
줄이 보이는 카드 격자가 되었으며, 자료 추가와 자율성은 스테이션 바닥의 한 줄로
내려갔습니다. 실행 목록은 데이터 출처가 아니라 **급한 순서**로 쌓입니다: 승인함 →
설치된 자동화(마지막 실행 결과 포함) → 실행 기록. 리뷰 카드는 긴 diff가 승인·거절
버튼을 화면 밖으로 밀어내던 한 줄 구성에서 **왼쪽 근거 · 오른쪽 결정**의 두 칸이
되었습니다(근거가 없으면 한 칸으로 되돌아갑니다). 리뷰 항목은 제목이 heading인
`<article>`이 되었고, 상태·출처 필터에는 이름이 붙었습니다. 재배치 도중 원래 깨져
있던 것도 하나 드러났습니다: 프로젝트 CSS는 unlayered라 Tailwind 유틸리티를
이기는데, 시트가 값을 정하지 않은 속성(`p-6`)만은 유틸리티가 살아남아 Brain 홈에
없는 여백을 얹고 하단 선반을 모바일 내비 아래로 밀어 넣고 있었습니다. 기능은
하나도 지우지 않았습니다.

See [RELEASE_NOTES_v10.6.1.md](RELEASE_NOTES_v10.6.1.md) and
[docs/CHANGELOG.md](docs/CHANGELOG.md).

---

# [v10.6.0 - Promoted Panels] (2026-08-03)

10.5.0이 각 화면의 **말**을 바꿨다면, 10.6.0은 각 화면의 **자리**를 바꿉니다. 주요
화면들은 그동안 동등한 탭 한 줄로 열렸습니다 — 처음 온 사람에게 선택지가 무엇인지
알기도 전에 고르라고 요구하는 배치입니다. 이제 모든 화면이 여기까지 오게 만든
질문에 답하는 패널 하나로 열립니다. 자료 화면은 파일·폴더·웹페이지를 모두 담은
**자료 추가하기 카드 하나**로 시작하고 진행 상황은 아래 조용한 2열로 내려갔습니다.
작업 화면은 빈 목표 작성기가 아니라 **검토함**으로 열리고, AI 모델 화면은 탭보다
먼저 지금 켜진 모델과 바꾸는 법을 답합니다. Brain 홈은 블록 다섯 개가 아니라
테두리 카드 하나이고, 연결 지도는 세 번째 탭이 아니라 버튼 하나 뒤의 하위
화면입니다. 설정의 탭 일곱 개는 이름 붙은 묶음 셋이 되었습니다. 매일 쓰는 곳
(대화·자료·기억)과 관리하는 곳(작업·AI 모델·설정)이 갈렸고, 관리 링크는 배열
하나에서 두 자리로 렌더링되며 단일 브레이크포인트가 어느 쪽을 보여줄지 정합니다.
그 과정에서 원래 깨져 있던 것들이 드러났습니다: `#/act/review`는 한 번도 검토함을
연 적이 없었고, 명령 팔레트는 목적지 목록의 사본을 따로 들고 있어 '작업'이 어떻게
도달했느냐에 따라 다른 화면을 뜻했습니다. 기능은 하나도 지우지 않았습니다.

See [RELEASE_NOTES_v10.6.0.md](RELEASE_NOTES_v10.6.0.md) and
[docs/CHANGELOG.md](docs/CHANGELOG.md).

---

# [v10.5.0 - Everyday Words] (2026-08-03)

10.4.0이 코드가 딛고 선 땅에 이름을 붙였다면, 10.5.0은 **읽는 사람이 딛고 선 땅**에
이름을 붙입니다. 처음 켠 사람이 만나는 모든 화면이, 그 사람이 쓸 법한 말로 지금
무슨 일이 일어나는지 설명합니다. 기능은 하나도 지우지 않았습니다 — 엔진의 어휘는
그 어휘를 원한 사람들이 있는 고급 모드 뒤로 옮겼습니다. 자율성 다이얼은
엄격/신뢰/바이패스 대신 **먼저 물어보기 / 웬만하면 알아서 / 거의 다 알아서**가
되었고, 홈과 설정이 같은 문구 모듈을 쓰기 때문에 같은 설정이 두 이름으로 불리지
않습니다. 숨겨져 있던 '진행 상황' 탭은 **내용 읽기 → 뜻 파악하기 → 기억에 연결하기**
세 단계를 보여줍니다. 실행 기록은 데이터베이스 id 대신 워크플로 이름으로 표시되고
`awaiting_approval`은 "내 승인 기다리는 중"으로 읽힙니다. 그리고 그동안 README에
실리던 스크린샷은 `advanced` 모드로 찍혀 있었습니다 — 이제 앱의 실제 기본값인
`basic`으로 찍습니다. 열 개 화면을 훑으며 빈 화면과 엔진 어휘를 모두 잡아내는
시각 테스트가 이 약속을 지킵니다.

See [RELEASE_NOTES_v10.5.0.md](RELEASE_NOTES_v10.5.0.md) and
[docs/CHANGELOG.md](docs/CHANGELOG.md).

---

# [v10.4.0 - Named Ground] (2026-08-02)

10.3.0은 측정되지 않은 것을 적어두고 끝냈습니다. 10.4.0은 그 목록을 비웠습니다.
mypy 백로그가 77개 모듈 1,407개 오류에서 **274개 모듈 0개**가 되었고, 그중 954개는
타입 문제가 아니라 읽기 어려움의 문제였습니다 — 계산된 `__all__` 하나와, 존재하지만
어디에도 적혀 있지 않던 11개 믹스인 계약. `app_factory._build`는 1,318줄에서
**26줄**이 되었고, 조립은 타입드 `RuntimeContext`를 공유하는 10개 단계로 나뉘었습니다.
그 과정에서 진짜 결함 네 개가 나왔는데, 그중 하나는 `python -m latticeai.server_app`이
한 번도 동작한 적이 없었다는 것입니다 — `main`이 옛 클로저의 지역 함수였고 export
허용목록에 없었습니다. 표면 패리티 표에 ◐ 는 이제 없습니다: VS Code가 폴더 수집,
보정/검증 플래그가 붙은 산출물 카드, 하드웨어 기반 모델 추천을 얻었고 Telegram도
같은 산출물 카드를 얻었습니다. 커버리지는 프론트 28.5→32.3%, Python 71.6→71.8%로
정직하게 일부만 올랐습니다.

See [RELEASE_NOTES_v10.4.0.md](RELEASE_NOTES_v10.4.0.md) and
[docs/CHANGELOG.md](docs/CHANGELOG.md).

---

# [v10.3.0 - Measured Ground] (2026-07-29)

Three numbers this project reported about itself were wrong. Frontend coverage
read 54% because vitest only counts files a test already imports; with
`all: true` it is **28.5%**. Python coverage read 80% because the `omit`
pattern did not match the paths coverage records, so the suite counted itself;
it is **71.6%**. mypy covered 13 modules; it now covers **193 of 270** and
found three real defects doing so — a log line referencing a non-existent
attribute inside an error handler, an un-imported annotation, and a possible
`None` dereference. 208 frontend tests (up from 154) give every page its first
unit tests; 1,896 Python tests (up from 1,786) cover the Telegram allowlist,
`run_command` containment, the audit log, and model-load consent. The chat-turn
writer left `app_factory._build` and has 14 tests. What is still unmeasured is
listed in `docs/MYPY_BACKLOG.md`.

See [RELEASE_NOTES_v10.3.0.md](RELEASE_NOTES_v10.3.0.md) and
[docs/CHANGELOG.md](docs/CHANGELOG.md).

---

# [v10.2.0 - Load-Bearing Fixes] (2026-07-29)

Answers all twelve findings of a full 10.1.1 code review (71/100). A SQLite
connection leak across 70+ sites — `with sqlite3.connect(...)` commits but never
closes — is fixed, which also made test coverage measurable for the first time
(**71%**, now floored in CI). The cloud privacy guard was correct code that
could never fire: nothing could mark a memory sensitive and the blocked-type
list was empty; memories are now markable from the boundary panel, secret-bearing
paths are flagged at ingestion, and credential-shaped types are blocked. Outbound
knowledge is redacted and every send (and refusal) is audited. 112 silent
`except: pass` handlers now log. Plus: one duplicate retrieval removed, a
`mktemp` TOCTOU race fixed, truncating `zip` made loud, ruff widened to
B/S/I/SIM/RET/C901 with mypy on 13 modules, and CI gained macOS + Python 3.14.

See [RELEASE_NOTES_v10.2.0.md](RELEASE_NOTES_v10.2.0.md) and
[docs/CHANGELOG.md](docs/CHANGELOG.md).

---

# [v10.1.1 - Reachable Boundary] (2026-07-28)

10.1.0 built the hybrid path and shipped it with no way to reach it: the
network boundary dial existed only for whoever called `/api/network-boundary`
by hand, so every user stayed on the `local_only` default without being offered
the choice. This release adds the control — `환경설정 → 내 지식이 나가는 범위`,
beside the autonomy dial. It renders the server's own catalog, refuses to send
a cloud switch until the required acknowledgement is ticked, and previews the
**actual memories** a question would send (with token estimate and the guard's
verdict) before anything is sent — including while still on local-only, where
it says so. Write-back switches appear only once cloud is permitted. The
unmounted `static/app/network-boundary-panel.js` is removed. Defaults and
behaviour are otherwise unchanged.

See [RELEASE_NOTES_v10.1.1.md](RELEASE_NOTES_v10.1.1.md) and
[docs/CHANGELOG.md](docs/CHANGELOG.md).

---

# [v10.1.0 - Hybrid Brain] (2026-07-28)

A feature release adding a local-first hybrid path: the Knowledge Graph stays
on-device while cloud LLMs become an opt-in worker. The default network boundary
is `local_only` — cloud use requires an explicit acknowledgement, only minimal
related nodes leave the machine, and streamed answers expand the local Brain
with provenance under token guardrails and Review Queue gates. Adds the
`NetworkBoundaryMode` / `MinimalContext` contracts, a persisted dial, an
OpenAI-compatible streaming worker, the hybrid `/chat` branch, multimodal
streaming contracts, and a standalone network-boundary panel module — all
additive and covered by new `test_network_boundary` / `test_hybrid_phase2` /
`test_hybrid_phase3` suites. The dial is API-and-config only in this release:
the panel module is not mounted by any page and the React app has no control
for it, so anyone who does not call `/api/network-boundary` stays on the
`local_only` default.

See [RELEASE_NOTES_v10.1.0.md](RELEASE_NOTES_v10.1.0.md) and
[docs/CHANGELOG.md](docs/CHANGELOG.md).

---

# [v10.0.1 - One Source of Truth] (2026-07-28)

A patch release with no behaviour change. `latticeai/core/agent.py` now holds
only the state machine (1769 → 1326 lines); its pure functions move to
`agent_helpers.py` and the `AgentState` vocabulary to `agent_state.py`. Every
name callers already imported from `latticeai.core.agent` still resolves there
as the same object, so nothing downstream changed. One latent defect was fixed
in the move: the helpers compared transcript steps against the literal string
`"EXECUTING"` rather than the enum, which would have silently broken artifact
reporting on any enum rename with no failing test. Home-screen spacing polish
is CSS only.

See [RELEASE_NOTES_v10.0.1.md](RELEASE_NOTES_v10.0.1.md) and
[docs/CHANGELOG.md](docs/CHANGELOG.md).

---

# [v10.0.0 - Plain Language] (2026-07-28)

Every screen was opened with a real local model loaded and every control
pressed. The home became four zones — Brain, composer, autonomy, capture — with
file / folder / note / web moved into the composer itself and the knowledge
graph behind the Brain. A 한국어 / English switch sits in the top bar and the
interface is fully translated both ways. Five defects that only appear in use
were fixed: a 311px header Brain, answers hidden behind the sticky composer, a
panel printing field names as values, clipped descriptions inside controls, and
a folder button that never opened a picker in a browser.

See [RELEASE_NOTES_v10.0.0.md](RELEASE_NOTES_v10.0.0.md) and
[docs/CHANGELOG.md](docs/CHANGELOG.md).

---

# [v9.9.9 - Lean Shell] (2026-07-27)

Cuts the first-paint JavaScript payload from 150.0 KiB to 99.3 KiB gzip (-34%)
by splitting the i18n table per lazy route: namespaces register on import, and
each route pulls only the copy it reads. The 9.9.8 budget bump is reverted — the
ceiling is back at its original 150 KiB with real headroom under it. A new
coverage check fails the build if a chunk reads a key it never imported, which
would otherwise render the raw key instead of translated text.

See [RELEASE_NOTES_v9.9.9.md](RELEASE_NOTES_v9.9.9.md) and
[docs/CHANGELOG.md](docs/CHANGELOG.md).

---

# [v9.9.8 - Autonomy Dial] (2026-07-27)

Adds a strict / trusted / bypass permission mode dial over the existing
ToolRegistry and Change Governor, settable from 환경설정 → 에이전트 자율성, and
fixes the defects that made an earlier draft of it either inert or unsafe:
scope-aware resolution so a stored per-user or per-workspace override actually
reaches enforcement, a run-scoped mode stamp that survives a paused approval,
no orphan proposals under trusted/bypass, and a deadlock in the preference
store that hung every mode change. The gates live in `SingleAgentRuntime`
itself — the monkey-patch layer is gone.

See [RELEASE_NOTES_v9.9.8.md](RELEASE_NOTES_v9.9.8.md) and
[docs/CHANGELOG.md](docs/CHANGELOG.md).

---

# [v9.9.7 - No Gaps Left] (2026-07-27)

Closes every `✖` the 9.9.6 parity matrix recorded, plus the documented design
boundaries: `/agent` SSE + live step timeline and evidence→action in VS Code,
grounding badge and Review Center in Telegram, recall and approval visibility
in the browser extension, a four-bed knowledge garden, a compact profile for
small local models with a direct-path fallback, per-folder memory state, two
pay-off-on-install skills, and voice memo capture with honest degradation.

See [RELEASE_NOTES_v9.9.7.md](RELEASE_NOTES_v9.9.7.md) and
[docs/CHANGELOG.md](docs/CHANGELOG.md).

---

# [v9.9.6 - Same Brain Everywhere] (2026-07-27)

Answers the 2026-07-27 full-stack review: VS Code/Telegram surface parity
(grounding badge, Review Center, run summary), evidence→action one-click
follow-ups, plain-language run outcomes, sentence-aware prose chunking with
document locators in citations, one context contract for chat and docgen,
evidence-classified graph relations, persistent project sessions, three closed
agent loops, funnel alerts, and embedding-swap recovery UX.

See [RELEASE_NOTES_v9.9.6.md](RELEASE_NOTES_v9.9.6.md) and
[docs/CHANGELOG.md](docs/CHANGELOG.md).

---

# [v9.9.5 - Closed Gaps] (2026-07-26)

Closes the seven residual gaps from 9.9.4: live sidecar Playwright E2E,
optional cross-encoder rerank, mid-run workspace awareness, rollback
none|git|snapshot, critic artifact checklist, VS Code/Telegram approval
parity, and human_in_loop unification onto the durable approval store.

See [RELEASE_NOTES_v9.9.5.md](RELEASE_NOTES_v9.9.5.md) and
[docs/CHANGELOG.md](docs/CHANGELOG.md).

---

# Release Notes

This repository keeps public release history from **8.0.0 through 10.8.0**.
Earlier release notes and release evidence were removed from the Git tree so the
history stays focused on the current product era.

## Current Release

- [v9.9.7 - No Gaps Left](RELEASE_NOTES_v9.9.7.md)
- [v9.9.6 - Same Brain Everywhere](RELEASE_NOTES_v9.9.6.md)
- [v9.9.5 - Closed Gaps](RELEASE_NOTES_v9.9.5.md)
- [v9.9.4 - Durable Loops](RELEASE_NOTES_v9.9.4.md)
- [v9.9.3 - Closed Loops](RELEASE_NOTES_v9.9.3.md)

## Recent Release Notes

- [v9.9.2 - Artifact Trust](RELEASE_NOTES_v9.9.2.md)
- [v9.9.1 - Clean Foundations](RELEASE_NOTES_v9.9.1.md)
- [v9.9.0 - Fail-Closed Trust](RELEASE_NOTES_v9.9.0.md)
- [v9.8.0 - Honest Knowledge Pipeline](RELEASE_NOTES_v9.8.0.md)
- [v9.7.0 - Proactive Hybrid Brain](RELEASE_NOTES_v9.7.0.md)
- [v9.6.0 - Trusted Agent Loop](RELEASE_NOTES_v9.6.0.md)
- [v9.5.0 - Command Center](RELEASE_NOTES_v9.5.0.md)
- [v9.4.0 - Question-Driven Everyday Automation](RELEASE_NOTES_v9.4.0.md)
- [v9.3.0 - Proactive Brain Intelligence](RELEASE_NOTES_v9.3.0.md)
- [v9.2.0 - Model-Agnostic File Generation](RELEASE_NOTES_v9.2.0.md)
- [v9.1.0 - Code Review Completion & Fail-Closed Runtime](RELEASE_NOTES_v9.1.0.md)
- [v9.0.0 - Code Review Closure & Runtime Cleanup](RELEASE_NOTES_v9.0.0.md)
- [v8.9.0 - Scoped Memory & Tool Policy Hardening](RELEASE_NOTES_v8.9.0.md)
- [v8.8.0 - Brain Core Extraction & Recall Proof Hardening](RELEASE_NOTES_v8.8.0.md)
- [v8.7.0 - Runtime State Hygiene & Release Evidence Refresh](RELEASE_NOTES_v8.7.0.md)
- [v8.6.0 - Desktop Capture & Navigation Reliability](RELEASE_NOTES_v8.6.0.md)
- [v8.5.0 - Tool Registry Readiness & Config DI](RELEASE.md#v850--tool-registry-readiness--config-di-2026-07-01)
- [v8.4.0 - Action-Aware Brain Chat](RELEASE_NOTES_v8.4.0.md)

## Preserved Release Notes

- [v8.3.0 - Orchestrated Brain Readiness](RELEASE_NOTES_v8.3.0.md)
- [v8.2.0 - Brain Brief](RELEASE_NOTES_v8.2.0.md)
- [v8.1.0 - Intuitive Brain Home](RELEASE_NOTES_v8.1.0.md)
- [v8.0.0 - Runtime Architecture Contract](RELEASE_NOTES_v8.0.0.md)

## Canonical History

The canonical 8.0.0-9.4.0 history is maintained in:

- [RELEASE.md](RELEASE.md)
- [docs/CHANGELOG.md](docs/CHANGELOG.md)

The preserved individual note files only exist for release lines that had
standalone notes in the current product era.
