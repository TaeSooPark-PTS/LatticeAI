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
- The active README screenshots now point at fresh `output/release/v6.1.0/`
  evidence generated from the built app.

## v6.1 UX Patch 1

First-run now lets users enter the Brain without downloading a model. This keeps
the local Brain profile and memory surface available even when model setup is
deferred or unavailable. The model recommendation remains primary, but model
installation is no longer the only path into the product.

Files:

- `frontend/src/components/ProductFlow.tsx`
- `frontend/src/i18n.ts`

## v6.1 UX Patch 2

Brain Home empty state now shows the first local Brain loop explicitly: save the
first memory, see it in Brain Home, then protect it with backup. This makes the
first-run promise visible at the exact moment the user enters an empty Brain.

Files:

- `frontend/src/App.tsx`
- `frontend/src/i18n.ts`
- `frontend/src/styles.css`

## v6.1 UX Patch 3

Review Center cards now explain that Run now previews/executes without approving
the item. This keeps the action semantics visible beside the controls instead of
only in high-level documentation.

Files:

- `frontend/src/features/review/ReviewCard.tsx`

Validation:

- `npm run typecheck:frontend`

## Remaining Frontend UX Work

- Tighten Brain Home empty states around first memory save, recall, and backup.
- Verify Review Center light/dark and mobile wrapping after backend/docs work.
- Keep v6.1.0 screenshots and GIFs current if additional UI copy changes land
  before final release sync.
