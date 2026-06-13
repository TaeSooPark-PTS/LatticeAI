# Lattice AI v4.5.0 Product Surface Redesign Report

## Scope

This reset keeps the Lattice AI backend, Brain Core, storage, backup/restore, portability, workflow, model, and agent capabilities intact while replacing user-facing presentation and information hierarchy.

## Product Direction

The surface now presents Lattice AI as a premium local Digital Brain:

- primary navigation describes human goals instead of subsystems
- first-run setup has one obvious continuation action
- Basic mode hides raw implementation detail by default
- Advanced/Admin modes retain diagnostics and administrative depth
- model setup follows Environment Analysis -> Recommended Models -> Install -> Validate -> Load -> Ready
- graph exploration is framed as memory and source exploration, not a graph dump

## Restored Journey

Before:

- users landed in capability panels that exposed backend terminology
- model state mixed runtime names, model IDs, and recovery detail in Basic mode
- Ask exposed retrieval/graph trace panels as first-class UI
- Admin/security detail was visible from Basic mode

After:

- first run opens with a progress card, next action, and complete setup sequence
- model setup is guided and consent-based
- Ask shows Relevant memories and Sources in Basic mode
- Admin controls are gated behind Admin mode
- tool names and hook names are humanized in Basic mode

## Files Changed

- `frontend/src/App.tsx`
- `frontend/src/components/FirstRunGuide.tsx`
- `frontend/src/components/primitives.tsx`
- `frontend/src/pages/Act.tsx`
- `frontend/src/pages/Ask.tsx`
- `frontend/src/pages/Brain.tsx`
- `frontend/src/pages/Capture.tsx`
- `frontend/src/pages/Library.tsx`
- `frontend/src/pages/System.tsx`
- `frontend/src/routes.ts`
- `frontend/src/styles.css`
- `tests/visual/v3.spec.js`
- `static/app/`

## Evidence

- Before screenshots: `output/playwright/v4.5.0-product-surface-reset/screenshots/before/`
- After screenshots: `output/playwright/v4.5.0-product-surface-reset/screenshots/after/`
- Before GIF: `output/playwright/v4.5.0-product-surface-reset/gifs/before-product-walkthrough.gif`
- After GIF: `output/playwright/v4.5.0-product-surface-reset/gifs/after-product-walkthrough.gif`
