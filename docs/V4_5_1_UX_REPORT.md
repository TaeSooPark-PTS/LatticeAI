# v4.5.1 UX Report

## UX Principle

v4.5.1 designs for non-technical users who should understand what Lattice AI is,
what to do next, how to add knowledge, how to use the brain, how to install a
model, and how to automate work without reading the README.

## Primary Flow

The first-session journey is:

1. Make it yours.
2. Choose a space.
3. Meet your Mac.
4. Pick a brain.
5. Install locally.
6. Try a question.
7. Set the pace.
8. Explore memory.

This replaces implementation-facing setup labels with user outcomes while still
driving the same account, workspace, model recommendation, model setup, mode,
and graph routes.

## Screen Inventory Decision

- Brain became Home, emphasizing the shape of work rather than a graph tool.
- Capture became Add, emphasizing the act of feeding memory.
- Act became Automate, emphasizing supervised work instead of an agent console.
- System became Care, emphasizing trust, portability, and maintenance.
- Ask and Library survived because their user intent is direct and legible.

## Interaction Notes

- Command palette remains available with Cmd/Ctrl+K.
- Mode switching is visible as Calm, Deep, and Admin instead of Basic,
  Advanced, and Admin in the first viewport.
- Mobile navigation opens as a room chooser and preserves no-horizontal-overflow
  behavior at 390px width.

## Evidence

- Playwright visual coverage: `tests/visual/v3.spec.js`
- Screenshots: `output/audits/v4.5.1-reimagining/screenshots/`
