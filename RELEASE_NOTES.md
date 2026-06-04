# Release Notes

## v2.2.3 - Frontend Stability & UX Fixes

Lattice AI v2.2.3 is a frontend stabilization release. It contains no new
features. It fixes real usability problems reported after v2.2.1, runs a full
UI/UX quality pass, and strengthens the automated visual tests. Every fix keeps
the existing design-token system and adds no `!important` or specificity-only
overrides.

### Fixes

- **Login inputs are readable in dark mode.** Email and password text was
  invisible in dark mode (light field background + theme text that flips to
  near-white = "white on white"). The login screen now has a proper dark theme
  (dark glass card, titlebar, fields, SSO buttons; light title/subtitle) and a
  Chrome/Safari autofill correction. Light mode is unchanged.
- **The recommendation result is clickable and scrollable again.** The model
  groups (Gemma 4, Qwen3-VL, Llama 4) and the action buttons were clipped and
  unreachable because the recommendation body had no working scroll region
  (a CSS selector that never matched its element). Long content now scrolls to
  the bottom, the accordions expand/collapse, and the action buttons are
  reachable.
- **The recommendation screen is readable in dark mode** (dark cards, light
  text), including the "Best for this PC" callout.
- **Button interactions are stable** across login, onboarding, the Knowledge
  Graph, and Admin — verified clickable with no overlay, `pointer-events`, or
  `z-index` blockers.

### Quality

- Light/Dark theme readability pass across login, onboarding, workspace, graph,
  and admin.
- Responsive checks from 375 px phones to 3440 px ultrawide.
- Accessibility: focus rings, keyboard operation, and Escape-to-close.
- Playwright visual suite expanded to 38 tests (login readability, recommendation
  scroll + accordions + reachable actions, dark-mode readability, and
  uncaught-page-error coverage).

### Expected Artifacts

```text
dist/ltcai-2.2.3-py3-none-any.whl
dist/ltcai-2.2.3.tar.gz
dist/ltcai-2.2.3.vsix
ltcai-2.2.3.tgz
```

## v2.2.2 - Frontend QA Stabilization Release

Lattice AI v2.2.2 is a stabilization release for the local-first AI workspace.
It contains no new features. It hardens the v2.2.x responsive UI, fixes
interaction defects found in a full frontend QA pass, strengthens the automated
visual test suite, and finalizes the README and release documentation. All
fixes preserve the existing design-token structure and add no `!important`.

### QA fixes

- **Mobile navigation reachable again** — the Knowledge Graph and Admin
  hamburger toggles were hidden on phones/tablets due to a CSS source-order
  bug; their drawers are now reachable across the mobile/tablet breakpoints.
- **Admin actions clickable** — a graph-only absolute `.toolbar` rule leaked
  onto Admin/Chat and floated a panel over the header, blocking the Refresh and
  Logout buttons. Scoped off the graph page.
- **No horizontal overflow on Workspace** — a visually-hidden toggle checkbox
  was stretching to viewport width; constrained to a 1px hit-box.

### QA coverage (automated)

- Light/dark theme parity (computed colors actually invert).
- Button clickability / hit-testing (no overlay or `pointer-events` blockers).
- No horizontal scroll across 375px phone → 3440px ultrawide (10 viewports).
- Mobile hamburger drawers open and close (graph + admin).
- Escape closes open drawers (keyboard a11y).
- Long surfaces scroll instead of clipping.

### Expected Artifacts

```text
dist/ltcai-2.2.2-py3-none-any.whl
dist/ltcai-2.2.2.tar.gz
dist/ltcai-2.2.2.vsix
ltcai-2.2.2.tgz
```

## v2.2.1 - Frontend / UX Overhaul Release

Lattice AI v2.2.1 is a frontend and UX release for the local-first AI
workspace. It makes knowledge graph, AI pipeline, model workflow, and
multi-agent coding surfaces easier to use across screen sizes while preserving
feature behavior.

### Highlights

- Mobile-first responsive UI across phone, tablet, laptop, desktop, ultrawide,
  and 4K. Content is re-laid out for smaller screens, never hidden.
- Light/dark mode with OS detection, a manual toggle, and persistence.
- Rebuilt design-token system as a single source of truth
  (`static/css/tokens.css`); no `!important`-based theming.
- Accessibility: 44px touch targets, `:focus-visible` rings, a keyboard-safe
  chat composer (visualViewport inset), iOS no-zoom inputs, and reduced-motion
  support.
- Knowledge Graph UX: responsive canvas that re-fits on resize, zoom buttons,
  fullscreen, minimap, relationship filter, mobile graph/card view, and a
  theme-aware palette.
- Admin UX: wide tables reflow to cards on mobile, with responsive layout,
  dark/light support, and larger touch targets.
- File UX: drag & drop and screenshot paste to attach files.
- Model cards show country, company, run mode, and internet usage in plain
  language.

### GitHub Release Copy

Local-first AI workspace for knowledge graphs, AI pipelines, and multi-agent
coding workflows.

Plan, execute, review, and remember work across local models, cloud models,
files, and team workflows.

This release refreshes the v2.2.1 workspace UI and marketplace-facing
positioning around:

- Local-first AI Workspace
- AI Pipeline Platform
- Knowledge Graph Platform
- Multi-Agent Workflow
- Personal / Organization Workspace
- Local Model Management
- SSO for teams

### Expected Artifacts

```text
dist/ltcai-2.2.1-py3-none-any.whl
dist/ltcai-2.2.1.tar.gz
dist/ltcai-2.2.1.vsix
ltcai-2.2.1.tgz
```

## v2.2.0 - Multimodal-First Knowledge OS Release

Lattice AI v2.2.0 reframes the product as an AI Knowledge Graph workspace. The release moves
model policy, documentation, UI copy, and recommendation logic toward a
multimodal-first Knowledge Graph architecture.

### Highlights

- README and architecture docs rewritten around AI Knowledge Graph workspace direction.
- New principle docs added for AI philosophy, model policy, and Knowledge Graph
  behavior.
- Local model catalogs now recommend current multimodal families only.
- Gemma 4 is the default recommendation family.
- Gemma 2, Gemma 3, Qwen2.5-VL, text-only fallback models, and MLX-LM
  recommendation paths are removed.
- Model entries now carry source disclosure metadata.
- Basic and advanced modes remain feature-equivalent; admin mode carries the
  actual authority boundary.
- Version metadata is aligned to `2.2.0`.

### Expected Artifacts

```text
dist/ltcai-2.2.0-py3-none-any.whl
dist/ltcai-2.2.0.tar.gz
dist/ltcai-2.2.0.vsix
ltcai-2.2.0.tgz
```
