# Usability Heuristic Audit

> Status: audit snapshot 2026-07-21

A heuristic (expert) evaluation of the Lattice AI product surface against
Jakob Nielsen's 10 usability heuristics, focused on the five journeys called out
in external review: **device analysis**, **agent completion**, **change
approval**, **Brain hierarchy**, and **slow first run**.

**This is not a substitute for real-user testing.** Heuristic evaluation finds
plausible problems from an expert's read of the code and UI; it cannot measure
what real users actually do, misread, or abandon. Every recommendation below
should be validated with 5–8 real sessions before it is treated as fact.

Evidence is cited as `file:line` against the frontend source
(`frontend/src/**`, TypeScript/React) and Python services. Note the shipped
`static/app/assets/*.js` is a minified bundle and is not citable line-by-line;
citations point at the readable source it is built from.

## Journey 1 — Device analysis (onboarding)

Surface: `frontend/src/components/onboarding/AnalysisScreen.tsx`; backing logic
`latticeai/services/model_recommendation.py`.

- The screen renders **real detected facts**, not pure theater: it maps over a
  `analysis` prop and shows real RAM when available
  (`AnalysisScreen.tsx:18`, `:95` — `ramGb ? \`${Math.round(ramGb)} GB\``).
- RAM sizing and Apple-Silicon detection are honest, documented estimates:
  `estimated_ram_gb = size_gb * 1.25 + 2.5` (`model_recommendation.py:64`),
  `is_apple_silicon` (`:69`), `recommend_catalog` (`:160`).

Heuristics:
- **#1 Visibility of system status** — GOOD: hardware line items begin in a
  `flow.analysis.checking` state (`AnalysisScreen.tsx:63-67`) then resolve to
  detected values, so the scan reads as progress rather than a frozen screen.
- **#2 Match to the real world** — GOOD: plain-language labels (chip, RAM, GPU,
  support, models) via i18n keys rather than jargon.
- **#1 risk** — the staged "checking → detected" reveal is presentation timing,
  not the true probe duration; if detection is effectively instant, the delay is
  cosmetic. Recommend: keep the animation only as long as real work is pending,
  and label any assumed/estimated value (e.g. RAM heuristic) as an estimate so
  users don't read `~16 GB` as a precise measurement.

## Journey 2 — Agent completion

Surface: `latticeai/core/agent.py`.

- Terminal states are explicit: `DONE`, `FAILED`, `NEEDS_REVIEW`
  (`agent.py:50-56`), and a user-facing `final_message` is always set
  (`:307`, and the default `"작업을 완료했습니다."` at `:357`).

Heuristics:
- **#1 Visibility of system status** — GOOD: the loop distinguishes *completed*
  from *needs-review* from *failed*, so the UI can tell the user which of three
  very different outcomes occurred instead of a generic "done".
- **#9 Help users recognize/recover from errors** — GOOD structurally: parse
  slips are traced with a `recovered` flag (`agent.py:255`, `:439-441`), so the
  system can surface "I had to retry" honestly.
- **Recommendation** — ensure the frontend actually renders the *distinction*
  between `DONE` and `NEEDS_REVIEW` prominently (color + copy), not just the
  `final_message` string; a review-required outcome that looks like success is a
  **#1/#5** hazard (user thinks work shipped when it is only staged).

## Journey 3 — Change approval

Surface: `latticeai/services/change_proposals.py`, gate entry
`latticeai/core/agent.py:273` (`approve`).

- Mutations of existing content are **staged, not applied**: `review(...)`
  returns `{"decision": "proposed", ...}` with a proposal record
  (`change_proposals.py:124`, `:177`), and application is a separate explicit
  `approve_and_apply` (`:347`). Additive creates flow with less friction.

Heuristics:
- **#3 User control & freedom** — STRONG: proposal-first governance means a
  destructive/edit action is reversible-by-default (it hasn't happened yet).
  This is the best-defended journey of the five.
- **#5 Error prevention** — STRONG: the split between additive (`allow_additive`)
  and mutating (`proposed`) is exactly the guardrail #5 asks for.
- **#10 Help & documentation / #2 match** — RISK: the value only lands if the
  approval UI explains *what changes* and *why it was staged* in plain language.
  Recommend a per-proposal diff + one-line "why this needs your review" derived
  from the `classification`/`reason` provenance already stored
  (`change_proposals.py:237`), so approval is an informed decision, not a blind
  "OK".

## Journey 4 — Brain hierarchy

Surface: `frontend/src/features/brain/DepthEmergence.tsx`,
`.../BrainMemoryLayer.tsx`.

- The Brain reveals structure **progressively by depth**: nothing at depth 1,
  memory layer at depth ≥2, knowledge layer at depth ≥3, higher structure at
  depth ≥4 (`DepthEmergence.tsx:29`, `:33-39`); the memory layer caps visible
  nodes by depth (`BrainMemoryLayer.tsx:16`).

Heuristics:
- **#8 Aesthetic & minimalist design** — GOOD intent: progressive disclosure
  avoids dumping the full graph on a new user.
- **#6 Recognition rather than recall** — RISK: depth is an abstract control. A
  user may not know what "depth 3" means or that concepts appear only there.
  Recommend naming each tier (e.g. "Memories → Concepts → Connections") and
  showing the current tier label, so the hierarchy is recognizable, not a hidden
  numeric mode.
- **#7 Flexibility & efficiency** — RISK: capping visible nodes (6–8) aids
  focus but can hide relevant memory with no "show more" affordance; verify an
  expansion path exists for power users.

## Journey 5 — Slow first run

Surface: `frontend/src/components/onboarding/InstallScreen.tsx`,
`.../DownloadConsentPanel.tsx`; backing `latticeai/services/model_runtime.py`.

- First run is staged and streamed: `InstallStage` = idle→install→download→
  validate→load→done (`InstallScreen.tsx:12`), a live `percent` bar
  (`:28`, `:44`, `:119`) fed by `streamModelPrepare` (`:38`), plus an up-front
  time estimate line (`:86`, `expectedLine` at `:158` using
  `estimatedDownloadMinutes` / `estimatedFirstResponseSeconds`).
- Download size and consent are disclosed **before** the download begins
  (`DownloadConsentPanel.tsx:9`, `:18-24`).

Heuristics:
- **#1 Visibility of system status** — STRONG: staged progress + percent + ETA
  is close to the textbook remedy for a long wait; the slow first run is
  *communicated*, not silent.
- **#5 Error prevention / #3 control** — GOOD: size + consent before a large
  download respects the user's bandwidth and choice.
- **RISK** — the ETA is a static estimate from catalog metadata
  (`estimatedDownloadMinutes`), not a live-adjusted projection; on a slow link
  the bar can stall while the estimate reads optimistically. Recommend deriving
  a live ETA from observed `event.percent` throughput and clearly marking the
  initial number as an estimate. Also confirm a **failure** path exists on the
  `error` stage (`InstallScreen.tsx:12`) with a retry, not a dead end (#9).

## Cross-cutting findings

- **#4 Consistency & standards** — i18n has an explicit ko/en fallback discipline
  (e.g. `brain.sources.fallback` in both `frontend/src/i18n/brain.ts:643` and
  `:1301`). Keep en/ko key parity enforced (the repo already has an i18n literal
  check) so no screen silently falls back to Korean for an English user.
- **#10 Documentation** — onboarding leans on progressive UI over docs, which is
  good, but the abstract controls (Brain depth) would benefit from inline
  first-use hints.

## Priority recommendations (heuristic — validate with real users)

1. **Make `NEEDS_REVIEW` visually distinct from `DONE`** in agent completion
   (Journey 2) — highest risk of a false "it shipped" mental model.
2. **Show a diff + plain-language "why staged" on every change proposal**
   (Journey 3) so approval is informed.
3. **Name the Brain hierarchy tiers and show the current tier** (Journey 4) to
   convert an abstract depth control into recognizable structure.
4. **Live-adjust the first-run ETA and label estimates as estimates**
   (Journeys 1 & 5).
5. **Verify the install `error` stage offers a retry** (Journey 5, #9).

## Limitations (honest)

- Expert heuristic evaluation only; no real users, no task-success metrics, no
  time-on-task, no accessibility audit (screen-reader/keyboard) beyond noting
  `aria-hidden` usage.
- Frontend runtime behavior was inferred from source, not driven live in this
  pass; timing/animation claims should be confirmed in a running build.
- Severity is the evaluator's judgment, not a measured frequency × impact.
