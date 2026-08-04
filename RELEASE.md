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

## v8.9.0 — Scoped Memory & Tool Policy Hardening (2026-07-06)

8.9.0 closes the actionable findings from `docs/reviews/CODE_REVIEW_2026-07-06.md`
except the explicitly excluded Computer Use direct API risk. The release
hardens authenticated history/KG scoping, direct Tool API policy gates,
AgentRuntime human-approval behavior, permission token storage, and frontend
maintainability seams. Installer/process execution now uses redacted command
plans, confirmation tokens, and local process audit events.

### Added
- Added user/workspace-scoped conversation history reads and deletes for chat
  and direct history tool routes.
- Added workspace scope enforcement inside Knowledge Graph retrieval/search,
  relationship search, traversal, and node reads.
- Added direct HTTP/MCP Tool API policy enforcement before hooks or handlers run.
- Added confirmation-token guarded installer/process command plans with redacted
  process audit events for setup and engine installation paths.
- Added regression coverage for TTL injection, scoped history, tool policy
  blocking, AgentRuntime explicit approval, permission token hashing, blocked
  local write prefixes, and model-download config injection.
- Added frontend API base split, CSS token/base split, and i18n literal
  allowlist budgets across `frontend/src`.

### Changed
- AgentRuntime now blocks non-auto-approved plans unless a real human approval
  path calls `approve(..., approved_by_human=True)`.
- Model download consent now flows through configured runtime state instead of
  reading environment variables directly in the gate.
- AppRuntime uses an explicit legacy namespace adapter for the historical
  module-level compatibility surface.
- README, release docs, readiness gates, package metadata, Tauri metadata, and
  VS Code extension metadata are synchronized to 8.9.0.
- Documentation clarifies that SQLite is the live local Brain store; Postgres
  remains optional scale/migration tooling rather than the default live KG
  implementation.

### Fixed
- Conversation store migrations now add scope columns before creating the
  workspace index, preserving upgrades from older DBs.
- Direct `write_file` and `edit_file` policy lookup now treats blocked system
  prefixes as destructive paths.
- Permission approval queues no longer persist raw approval tokens.
- Clearing the selected workspace now removes the persisted localStorage value.
- Tauri/backend API calls use credential inclusion for cross-origin localhost
  cookie/session behavior.

Expected artifacts (exact 8.9.0 names only):
- `dist/ltcai-8.9.0-py3-none-any.whl`
- `dist/ltcai-8.9.0.tar.gz`
- `dist/ltcai-8.9.0.vsix`
- `ltcai-8.9.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_8.9.0_aarch64.dmg`

## v8.8.0 — Brain Core Extraction & Recall Proof Hardening (2026-07-06)

8.8.0 packages the Brain Core extraction prep and recall-proof hardening work.
Internal-only Brain compatibility layers are physically removed, root-level
compatibility shims remain explicitly managed for external entrypoints, and the
Brain UI/backend path now gives users clearer conversation controls and better
evidence for why memories were recalled.

### Added
- Added Brain Core isolation coverage proving `lattice_brain` does not import
  the product `latticeai` package.
- Added retrieval quality gates for matched recall terms, confidence labels,
  and lexical evidence filtering.
- Added Brain conversation controls for starting, resuming, deleting, stopping,
  regenerating, and copying conversation output.

### Changed
- Removed internal-only flat Brain modules, the deprecated `latticeai.brain`
  namespace, and the `latticeai.services.agent_runtime` alias.
- Updated `legacy_shim_report()` to distinguish remaining shims from
  intentionally removed 8.8.0 layers.
- Hardened AgentRuntime role validation, legacy run contract reads, and
  persisted retry budgets.
- Updated package/runtime/static/Tauri metadata and current-release
  documentation to 8.8.0.

### Fixed
- File ingestion now rejects directory paths at the file-ingest boundary.
- Memory recall filters zero-evidence noise when higher-confidence lexical
  matches exist, and answer proof citations expose matched terms and confidence.

Expected artifacts (exact 8.8.0 names only):
- `dist/ltcai-8.8.0-py3-none-any.whl`
- `dist/ltcai-8.8.0.tar.gz`
- `dist/ltcai-8.8.0.vsix`
- `ltcai-8.8.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_8.8.0_aarch64.dmg`

## v8.7.0 — Runtime State Hygiene & Release Evidence Refresh (2026-07-05)

8.7.0 packages the current main-branch hardening work into an exact release
line. Model runtime implementation paths now use the typed runtime-state object
as their source of truth, legacy module-global synchronization is explicitly
deprecated, and the checked-in release screenshots/GIF/WebM are refreshed from
the current app shell.

### Added
- Added unit coverage proving model-runtime internals read from
  `ModelRuntimeState` while the legacy globals remain a compatibility surface.
- Added 8.7.0 release evidence under `output/release/v8.7.0/`, including
  screenshots, walkthrough GIF/WebM, and the capture index.
- Added `RELEASE_NOTES_v8.7.0.md` and synchronized current-release docs.

### Changed
- Updated package/runtime/static/Tauri metadata to 8.7.0.
- Updated README release evidence links from the old 8.2.0 screenshots to the
  refreshed 8.7.0 captures.
- Updated current-release documentation and exact artifact examples to 8.7.0.

### Fixed
- Reduced internal reliance on bare module globals in
  `latticeai/services/model_runtime.py`; compatibility globals are still
  available for older callers.
- `sync_to_module_globals()` now emits `DeprecationWarning` so future code does
  not build new coupling to the legacy global state path.

Expected artifacts (exact 8.7.0 names only):
- `dist/ltcai-8.7.0-py3-none-any.whl`
- `dist/ltcai-8.7.0.tar.gz`
- `dist/ltcai-8.7.0.vsix`
- `ltcai-8.7.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_8.7.0_aarch64.dmg`

## v8.6.0 — Desktop Capture & Navigation Reliability (2026-07-05)

8.6.0 focuses on the user-facing capture path: folder selection now works from
the desktop app's localhost-hosted Tauri webview, Capture shows actionable
feedback when the picker is unavailable, and the new Brain shell navigation is
covered by updated visual smoke tests.

### Added
- Added Tauri capability coverage for `http://127.0.0.1:*` and
  `http://localhost:*`, preserving desktop IPC for the localhost app shell.
- Added a regression trust gate that verifies the Tauri capability keeps
  localhost desktop IPC enabled.
- Added `RELEASE_NOTES_v8.6.0.md` and synchronized current-release docs.

### Changed
- Updated Capture folder selection to detect both module and global Tauri
  bridges and to show a visible fallback message when the native picker cannot
  open.
- Updated Visual Smoke coverage for the Brain shell sidebar, advanced utility
  drawer, and admin-console entry flow.
- Synchronized package/runtime/static/Tauri metadata and release docs to 8.6.0.

### Fixed
- Fixed the folder-picker path for the Tauri production app after it navigates
  from bundled static content to the local FastAPI `/app` URL.
- Removed negative letter spacing from the updated frontend shell styling.

Expected artifacts (exact 8.6.0 names only):
- `dist/ltcai-8.6.0-py3-none-any.whl`
- `dist/ltcai-8.6.0.tar.gz`
- `dist/ltcai-8.6.0.vsix`
- `ltcai-8.6.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_8.6.0_aarch64.dmg`

## v8.5.0 — Tool Registry Readiness & Config DI (2026-07-01)

Full codebase scan for architectural debt, code smells, and improvement opportunities (per AGENTS.md priorities and rules). Targeted improvements implemented without breaking public behavior or legacy compatibility.

### Added
- Made ToolRegistry fully aligned: added governance + description for `vision_analyze`; diagnostics now report `ready: true` with no handler/governance mismatches.
- Propagated `tz_name` (from central `Config`) into `TriggerService` via `build_automation_runtime` / platform wiring — advances Config centralization and explicit DI for automation layer.

### Changed
- Updated TriggerService, automation_runtime, platform_runtime_wiring and call sites in app_factory to accept and forward `tz_name` for Config-driven construction (env fallback preserved for compatibility).
- Synchronized version metadata and all current-release documentation to 8.5.0.
- Performed full scan: identified large modules, legacy globals, registry drift, and DI gaps; addressed highest-leverage safe changes.

### Fixed
- ToolRegistry drift between handlers, governance, and descriptions (vision_analyze was implemented in tools/ but missing from central registry policy surface).
- Minor: timezone was only read via os.environ inside TriggerService instead of flowing from the Config seam.

Expected artifacts (exact 8.5.0 names only):
- `dist/ltcai-8.5.0-py3-none-any.whl`
- `dist/ltcai-8.5.0.tar.gz`
- `dist/ltcai-8.5.0.vsix`
- `ltcai-8.5.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_8.5.0_aarch64.dmg`

## v8.4.0 — Action-Aware Brain Chat (2026-07-01)

8.4.0 closes the gap between asking the Brain to create a file and seeing a
real artifact appear. Explicit create/write/save/edit file requests from the
Brain Chat route are now routed into the governed workspace file tool instead
of plain model generation, so the same composer can answer questions or perform
workspace file actions.

### Added
- Added a `/chat` file-action intent gate for explicit file creation, editing,
  saving, and artifact requests.
- Added regression coverage proving file creation requests from chat route into
  the workspace file tool and return `created_files`.

### Changed
- Kept normal Q&A on the direct chat generation path while routing only explicit
  side-effect file requests to the existing planner/executor/reviewer agent.
- Synchronized package/runtime/static/Tauri metadata and current-release docs to
  8.4.0.

### Fixed
- Literal file writes with user-provided content no longer require a model to be
  loaded before the workspace file tool runs.
- File target/content parsing no longer swallows surrounding prose into the path
  or treats descriptive words as literal file content.

Expected artifacts (exact 8.4.0 names only):
- dist/ltcai-8.4.0-py3-none-any.whl
- dist/ltcai-8.4.0.tar.gz
- dist/ltcai-8.4.0.vsix
- ltcai-8.4.0.tgz
- src-tauri/target/release/bundle/dmg/Lattice AI_8.4.0_aarch64.dmg

## v8.3.0 — Orchestrated Brain Readiness (2026-07-01)

8.3.0 turns the architecture and product polish backlog into measured release
work. Legacy shims are now inventoried, AgentRuntime/workflow boundaries are
more inspectable, graph ingestion is routed through the unified pipeline, and
the release docs name the onboarding and community/plugin growth paths.

### Added
- Added a managed legacy compatibility inventory with owners, replacements,
  reasons, removal phases, and readiness metrics.
- Added AgentRuntime lifecycle coverage for legacy run records plus explicit
  WorkflowEngine boundary/config inspection.
- Added graph ingestion tests for upload-to-pipeline behavior and
  workspace-safe duplicate content.
- Added 8.3.0 onboarding and community/plugin docs.

### Changed
- Routed `/knowledge-graph/ingest` through `IngestionPipeline` when the
  pipeline is wired, preserving provenance and hook lifecycle behavior.
- Kept duplicate text/web/note content isolated per workspace while retaining
  content-hash duplicate semantics.
- Converted stateful root shims for `mcp_registry.py` and `llm_router.py` into
  physical module aliases.
- Updated upload client handling so HTTP failures surface as honest UI errors.
- Synchronized package/runtime/static/Tauri metadata, readiness targets, and
  current-release docs to 8.3.0.

### Upgrade Notes
- Existing legacy-global text/web/note graph nodes are not rewritten in place.
  Re-ingesting the same content with a workspace id can create a separate
  workspace-scoped node; re-index existing sources after upgrading when you want
  provenance to converge on workspace scopes.

Expected artifacts (exact 8.3.0 names only):
- dist/ltcai-8.3.0-py3-none-any.whl
- dist/ltcai-8.3.0.tar.gz
- dist/ltcai-8.3.0.vsix
- ltcai-8.3.0.tgz
- src-tauri/target/release/bundle/dmg/Lattice AI_8.3.0_aarch64.dmg

## v8.2.0 — Brain Brief (2026-06-27)

8.2.0 adds an evidence-backed Brain Brief to the default Brain Home. Instead of
making the user infer readiness from scattered panels, the home screen now shows
what to notice, which real memory/graph signals support it, and the easiest next
action.

### Added
- Added `MemoryService.brain_brief()` and `/api/memory/brain-brief` so the Brain
  home briefing is generated from real workspace, conversation, graph, vector,
  and source-health data.
- Added a Brain Brief panel to the centered Brain Home with a focus item,
  evidence counters, and direct actions for adding sources, asking, inspecting
  graph links, verifying model-independent proof, and managing backups.
- Added unit coverage for empty Brain guidance, recall-backed Brain Briefs, and
  the API endpoint.

### Changed
- Completed another runtime extraction pass by keeping model loading/server
  engine bodies in `model_loading.py` / `model_engines.py` behind compatibility
  delegations.
- Moved WorkspaceOS graph trace, run, skill, and snapshot comparison ownership
  into focused manager modules while preserving the store facade.
- Synchronized package/runtime/static/Tauri metadata, readiness targets, and
  current-release docs to 8.2.0.

Expected artifacts (exact 8.2.0 names only):
- dist/ltcai-8.2.0-py3-none-any.whl
- dist/ltcai-8.2.0.tar.gz
- dist/ltcai-8.2.0.vsix
- ltcai-8.2.0.tgz
- src-tauri/target/release/bundle/dmg/Lattice AI_8.2.0_aarch64.dmg

## v8.1.0 — Intuitive Brain Home (2026-06-27)

8.1.0 turns the default Brain surface from a dashboard-like status panel into a
product-first conversation entry. The living Brain stays directly above the
composer, while the first screen explains what the Brain remembers, what topic
is connected, and what the user should do next.

### Changed
- Added a focused `BrainFirstScreen` surface that combines LivingBrain, readiness
  status, recent memory, connected topic, and next-best action.
- Removed the dashboard-style four-metric growth strip from the default Brain
  entry and replaced it with narrative, action-oriented copy.
- Kept the primary action visible by moving talk/add-source/view-graph actions
  into the first screen and verifying their routes with Playwright.
- Tightened mobile and 320px layouts so the Brain and composer fit in the first
  viewport without horizontal overflow.
- Refreshed 8.1.0 screenshots, walkthrough GIF/WebM, static app assets, package
  metadata, Tauri metadata, readiness targets, and current-release docs.

Expected artifacts (exact 8.1.0 names only):
- dist/ltcai-8.1.0-py3-none-any.whl
- dist/ltcai-8.1.0.tar.gz
- dist/ltcai-8.1.0.vsix
- ltcai-8.1.0.tgz
- src-tauri/target/release/bundle/dmg/Lattice AI_8.1.0_aarch64.dmg

## v8.0.0 — Runtime Architecture Contract (2026-06-24)

8.0.0 makes the platform architecture release line explicit. AgentRuntime,
ToolRegistry, central Config, server decomposition, and Knowledge Graph
stabilization are now represented as machine-checkable contracts rather than
release-note claims.

### Changed
- Added `lattice-architecture-contract/v1` to `architecture_readiness()`,
  including the preferred refactoring order and concrete owners for runtime,
  registry, config, server, and KG boundaries.
- Added `tool-registry-contract/v1` to the live ToolRegistry manifest so
  dispatch, policy, and permission ownership are visible from one registry
  source of truth.
- Updated product readiness to target 8.0.0 and require the architecture
  contract, exact 8.0.0 artifacts, current docs, and release evidence.
- Made logical Knowledge Graph `replace` imports transactional, so malformed
  imports roll back without clearing the existing graph.
- Locked Knowledge Graph read-equivalence coverage for `list_documents`,
  `get_node`, `relationship_search`, and `traverse` across legacy and v2
  read paths.
- Preserved colliding legacy edge labels during logical import/backfill without
  regressing native write-door canonical edge dedupe.
- Synchronized Python, npm, VS Code extension, Tauri, static asset, marketplace,
  workspace, and multi-agent runtime versions to 8.0.0.
- Refreshed current-release documentation while setting 8.0.0 as the oldest retained
  release-history entry.

Expected artifacts (exact 8.0.0 names only):
- dist/ltcai-8.0.0-py3-none-any.whl
- dist/ltcai-8.0.0.tar.gz
- dist/ltcai-8.0.0.vsix
- ltcai-8.0.0.tgz
- src-tauri/target/release/bundle/dmg/Lattice AI_8.0.0_aarch64.dmg
