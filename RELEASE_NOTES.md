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

This repository keeps public release history from **8.0.0 through 10.4.0**.
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
