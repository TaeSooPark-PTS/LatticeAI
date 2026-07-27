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

This repository keeps public release history from **8.0.0 through 9.9.8**.
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
