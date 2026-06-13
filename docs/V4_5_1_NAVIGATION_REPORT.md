# v4.5.1 Navigation Report

## New Navigation Model

The primary navigation is now:

- Home
- Ask
- Add
- Automate
- Library
- Care

This replaces the previous product taxonomy without removing compatibility
routes. Existing paths such as `#/knowledge-graph`, `#/hybrid-search`,
`#/files`, `#/agents`, `#/workspace-admin`, and admin aliases still route to
the same capability modules.

## Why This Model

- Home is the user-facing Digital Brain entry point.
- Ask is the clearest label for conversational work.
- Add explains knowledge ingestion without technical vocabulary.
- Automate explains agents/workflows as supervised outcomes.
- Library remains clear for models, skills, and connections.
- Care reframes account, backups, devices, and safety as maintenance.

## Implementation

- Route compatibility remains in `frontend/src/routes.ts`.
- The visible dock and mobile drawer are implemented in `frontend/src/App.tsx`.
- Visual tests now assert the new navigation labels and legacy route behavior.

## Evidence

- Tests: `tests/visual/v3.spec.js`
- Code: `frontend/src/routes.ts`, `frontend/src/App.tsx`
