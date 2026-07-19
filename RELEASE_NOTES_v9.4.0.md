# Lattice AI v9.4.0 — Question-Driven Everyday Automation

Released: 2026-07-20

9.4.0 makes automating daily life effortless. Until now, automation in
Lattice AI meant browsing a catalog of generic starter recipes. Now the Brain
watches what you actually do — the questions you keep asking and the
knowledge folders you keep feeding — and proposes concrete automations with
**your own words as the evidence**.

## What it feels like

- You've asked "오늘 할 일 정리해줘" seven mornings in a row. The Act page now
  shows: *"이 질문을 7번 반복해서 물어보셨어요 — 매일 자동 초안으로
  만들어드릴까요?"* One click creates a scheduled-answer draft.
- You connected `~/Documents/계약서` to your Brain and 42 files are indexed.
  The Brain suggests a folder digest that drafts a summary whenever new
  knowledge arrives from that folder.
- Nothing runs by itself. Every accepted suggestion is a **disabled draft**:
  review-queue gated, local-only, no external actions, enabled only when you
  flip it on.

## Automation Intelligence (`/api/automation/*`)

- **Pattern mining** — `GET /api/automation/patterns` clusters your recurring
  question intents with a deterministic, local token-signature algorithm
  (Korean + English aware). No model call: the evidence is your literal past
  questions with counts and last-asked times.
- **Suggestions** — `GET /api/automation/suggestions` merges two sources:
  recurring questions (digest/status/follow-up intents map onto the matching
  starter recipe; any other repeated question becomes a parameterized
  "scheduled answer" workflow) and connected knowledge folders with indexed
  files (folder-digest automations triggered by new knowledge).
- **One-click install** — `POST /api/automation/install` builds the workflow
  through the same validated WorkspaceOS path as starter recipes, stamped
  with `suggestion_id` provenance. Installs are idempotent — double clicks
  and re-requests never duplicate workflows.
- **Overview** — `GET /api/automation/overview` powers the UI with one
  payload: suggestions, installed automations with enable state, and the
  consent contract.

## Intuitive automation surface

The Act page's recipes tab now opens with **"나를 위한 자동화 제안 /
Automation suggestions for you"**: evidence chips, cadence labels
("매일 자동 초안" / "새 지식이 들어올 때"), local-only badges, and a
one-click Create button. Installed suggestions show "초안 생성됨 — 검토 후
켜세요". Fully ko/en localized and visible in basic mode.

## Scope and safety

- History mining is scoped to the requesting user and workspace; scoped
  reads exclude legacy-global rows.
- Suggestion ids are deterministic (`sug-q-*`, `sug-src-*`) so the surface
  is stable across refreshes.
- Accepted suggestions never enable themselves; enabled runs still stage
  their output in the review queue before anything becomes durable memory.

## Verification

- New `tests/unit/test_automation_intelligence.py` (10 tests): clustering,
  intent→recipe mapping, scoped history reads, stable ids, installed
  marking, consent-first workflow definitions, overview shape, and
  no-backend degradation.
- Full sweep: **1086 unit**, **13 integration**, **14 frontend vitest**,
  **18 playwright visual** tests passing; lint, typecheck, docs, brain
  quality, and product-readiness gates green; live-boot smoke on all four
  new endpoints.

## Compatibility

Purely additive: four new `/api/automation/*` endpoints, one new Act panel,
new i18n keys. No existing endpoint changed shape.
