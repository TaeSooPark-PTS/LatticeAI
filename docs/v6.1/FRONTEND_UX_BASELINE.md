# v6.1.0 Frontend UX Baseline

## Scope

This file tracks the frontend/UX side of the v6.1.0 Product Hardening and
Digital Brain Completion work.

## Baseline Findings

- First-run is implemented in `frontend/src/components/ProductFlow.tsx` and is
  shown before the main Brain surface when `lattice.productFlow.complete` is not
  set.
- Brain Home is implemented in `frontend/src/App.tsx` as a living Brain state,
  conversation, memory layers, relationship depth, and care panel.
- Review Center lives under `Act` and is implemented in
  `frontend/src/features/review/`.
- The active README screenshots still point at `output/release/v6.0.0/`, so
  v6.1.0 evidence media must be regenerated near release sync.

## v6.1 UX Patch 1

First-run now lets users enter the Brain without downloading a model. This keeps
the local Brain profile and memory surface available even when model setup is
deferred or unavailable. The model recommendation remains primary, but model
installation is no longer the only path into the product.

Files:

- `frontend/src/components/ProductFlow.tsx`
- `frontend/src/i18n.ts`

Validation:

- `npm run typecheck:frontend`

## Remaining Frontend UX Work

- Tighten Brain Home empty states around first memory save, recall, and backup.
- Verify Review Center light/dark and mobile wrapping after backend/docs work.
- Regenerate v6.1.0 screenshots and GIFs after the full product flow stabilizes.
