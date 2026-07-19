# Lattice AI v9.5.0 — Command Center

Released: 2026-07-20

9.5.0 puts the whole Brain one keystroke away. Until now, knowledge, past
conversations, automations, reviews, and health each lived on their own
screen. The Command Center condenses all of them into two instant surfaces:
a **Cmd+K command palette** that searches everything at once, and a
**Today's Briefing** that answers "what does my Brain see today, and what
should I do next?" in one glance.

## What it feels like

- Press **Cmd+K anywhere**. Type "계약서" and see in one list: the knowledge
  nodes about the contract, the past conversations where you discussed it,
  and the automation that digests that folder — plus instant page jumps.
  Arrow keys move, Enter goes, Esc closes.
- Open the Brain home and expand **오늘의 브리핑**: how many questions you
  asked, how many automations are on, how many items await review, and your
  Brain's health grade — with recently added knowledge and one-click next
  steps like *"검토 대기 5건 확인하기"*.
- Nothing here writes or calls a model. The Command Center is read-only,
  local, deterministic, and scoped to you and your workspace.

## Command Center API (`/api/command/*`)

- **Briefing** — `GET /api/command/briefing` aggregates six sections
  (knowledge, conversations, automations, review, health, suggestions), each
  degrading independently when its backend is unavailable, plus
  state-derived quick actions with stable ids (`review-pending`,
  `enable-drafts`, `install-suggestion`, `connect-knowledge`,
  `check-health`, `ask-brain`) targeting real app routes.
- **Universal search** — `GET /api/command/search?q=…` groups results across
  knowledge nodes (workspace-scoped keyword search), the user's own
  conversations (deduped per conversation, newest first), and installed
  automations with their enable state.

## Intuitive frontend surfaces

- **Command Palette** (`Cmd+K` / `Ctrl+K`): grouped results
  (지식 / 지난 대화 / 자동화 / 화면 이동), debounced live search, full
  keyboard navigation, and static page jumps that work even with an empty
  Brain. Replaces the old composer-focus shortcut with a real launcher.
- **Today's Briefing panel** on the Brain home: stat chips, recent
  knowledge, waiting suggestion count, and quick-action buttons that
  navigate straight to the right screen. Collapsible, loads only when
  opened, fully ko/en localized.

## Scope and safety

- History and graph reads are scoped to the requesting user and workspace;
  scoped reads exclude legacy-global rows.
- Both endpoints are read-only: no writes, no model calls, no external
  actions.
- Quick-action ids are deterministic so the surface is stable across
  refreshes.

## Verification

- New `tests/unit/test_command_center.py` (11 tests): briefing section
  independence, scoped history reads, quick-action derivation (including
  low-health and empty-state defaults), search grouping, conversation
  dedupe, workspace scoping, and no-backend degradation.
- New `CommandPalette.test.tsx` (3 component tests): Cmd+K open + search +
  navigate flow, page filtering without backend calls, Escape close, and
  briefing stats + quick-action navigation.
- Full sweep: **1097 unit**, **13 integration**, **17 frontend vitest**,
  **18 playwright visual** tests passing; lint, typecheck, docs, brain
  quality, and product-readiness gates green; live-boot smoke on both new
  endpoints.

## Compatibility

Purely additive: two new `/api/command/*` endpoints, one new palette
overlay, one new Brain home panel, new i18n keys. No existing endpoint
changed shape. The previous Cmd+K composer-focus behavior is superseded by
the palette, which includes a direct jump to the Brain conversation.
